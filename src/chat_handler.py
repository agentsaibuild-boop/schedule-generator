"""Chat session handler — processes user messages via dual AI system.

Routes intents to appropriate actions: chat, generate, modify, export, lessons, evolve.
Uses AIProcessor (backed by AIRouter) for all AI operations.
Includes self-evolution support with 3-level change management (green/yellow/red).
Enforces strict JSON pipeline: converted files only for AI operations (Rule #0).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.ai_processor import AIProcessor
    from src.file_manager import FileManager
    from src.knowledge_manager import KnowledgeManager
    from src.project_manager import ProjectManager
    from src.schedule_builder import ScheduleBuilder
    from src.self_evolution import SelfEvolution

logger = logging.getLogger(__name__)

INTENT_KEYWORDS: dict[str, list[str]] = {
    "load_project": ["зареди", "папка", "проект", "път", "директория", "отвори"],
    "generate_schedule": [
        "генерирай", "график", "създай", "направи", "gantt",
        "линеен", "графика", "нов график",
    ],
    "ask_question": [
        "какво", "как", "защо", "кога", "колко", "обясни",
        "правило", "методика", "урок",
    ],
    "export": ["свали", "експорт", "pdf", "xml", "mspdi", "export", "изтегли"],
    "modify_schedule": [
        "промени", "корекция", "коригирай", "измени", "обнови", "добави",
        "премахни", "махни", "премести", "смени",
    ],
    "save_lesson": ["запиши урок", "научен урок", "запомни"],
    "evolve": [
        "добави функционалност", "промени приложението", "нова функция",
        "модифицирай", "обнови кода", "искам промяна", "добави модул",
        "нов тип проект", "нова възможност", "самоеволюция", "evolution",
    ],
}


class ChatHandler:
    """Manages the chat session: message processing, AI routing, intent detection."""

    def __init__(
        self,
        ai_processor: AIProcessor | None = None,
        file_manager: FileManager | None = None,
        knowledge_manager: KnowledgeManager | None = None,
        evolution: SelfEvolution | None = None,
        project_manager: ProjectManager | None = None,
        schedule_builder: ScheduleBuilder | None = None,
    ) -> None:
        """Initialize the chat handler.

        Args:
            ai_processor: AIProcessor instance for AI calls.
            file_manager: FileManager for project file access.
            knowledge_manager: KnowledgeManager for knowledge lookups.
            evolution: SelfEvolution instance for self-modification.
            project_manager: ProjectManager for project persistence and history.
            schedule_builder: ScheduleBuilder for local validation and adjustments.
        """
        self.ai = ai_processor
        self.files = file_manager
        self.knowledge = knowledge_manager
        self.evolution = evolution
        self.project_mgr = project_manager
        self.builder = schedule_builder
        self.history: list[dict[str, str]] = []
        self.current_schedule: dict | None = None
        self.correction_history: list[dict] = []

    def process_message(
        self,
        user_message: str,
        project_loaded: bool = False,
        conversion_done: bool = False,
        project_context: dict | None = None,
        pending_changes: dict | None = None,
        recent_projects: list[dict] | None = None,
    ) -> dict:
        """Process a user message and return a structured response.

        Args:
            user_message: The user's input text.
            project_loaded: Whether a project is loaded.
            conversion_done: Whether files are converted.
            project_context: Optional dict with current project info.
            pending_changes: Pending self-evolution changes awaiting confirmation.
            recent_projects: List of recent projects for number selection.

        Returns:
            Dict with response, schedule_updated, schedule_data,
            correction_info, intent, model_used, plus optional
            evolution_pending / evolution_applied / evolution_cleared /
            load_project_path / load_project_id.
        """
        # Check if there are pending evolution changes waiting for confirmation
        if pending_changes:
            return self._handle_confirm_change(user_message, pending_changes)

        # Check for recent project selection (numbers 1-5)
        stripped = user_message.strip()
        if (
            stripped.isdigit()
            and 1 <= int(stripped) <= 5
            and recent_projects
            and len(recent_projects) >= int(stripped)
        ):
            return self._handle_select_recent(int(stripped), recent_projects)

        self.history.append({"role": "user", "content": user_message})

        intent = self._detect_intent(user_message)

        try:
            result = self._handle_intent(
                user_message, intent, project_loaded, conversion_done, project_context
            )
        except Exception as exc:
            logger.exception("Error processing message")
            result = {
                "response": f"Възникна грешка: {exc}\n\nМоля, опитайте отново.",
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": intent,
                "model_used": "none",
            }

        self.history.append({"role": "assistant", "content": result["response"]})

        # Track message in project manager
        if self.project_mgr and self.project_mgr.current_project:
            pid = self.project_mgr.current_project.get("id")
            if pid:
                stats = self.project_mgr.projects.get("projects", {}).get(pid, {}).get("stats", {})
                stats["total_messages"] = stats.get("total_messages", 0) + 1
                self.project_mgr.save_progress(pid, {})

        return result

    def _handle_intent(
        self,
        message: str,
        intent: str,
        project_loaded: bool,
        conversion_done: bool,
        project_context: dict | None,
    ) -> dict:
        """Route to the appropriate handler based on intent."""

        if intent == "load_project":
            return self._handle_load_project(message)

        if intent == "generate_schedule":
            return self._handle_generate_schedule(
                message, project_loaded, conversion_done, project_context
            )

        if intent == "modify_schedule":
            return self._handle_modify_schedule(message)

        if intent == "export":
            return self._handle_export(message)

        if intent == "save_lesson":
            return self._handle_save_lesson(message)

        if intent == "evolve":
            return self._handle_evolve(message)

        if intent == "ask_question":
            return self._handle_question(message, project_context)

        # general — send to AI chat
        return self._handle_general(message, project_context)

    # ------------------------------------------------------------------
    # Intent handlers
    # ------------------------------------------------------------------

    def _handle_select_recent(self, number: int, recent_projects: list[dict]) -> dict:
        """Handle selection of a recent project by number.

        Args:
            number: 1-based index of the selected project.
            recent_projects: List of recent project dicts.

        Returns:
            Response dict with load_project_id for the app to handle.
        """
        idx = number - 1
        if idx >= len(recent_projects):
            return {
                "response": f"Няма проект с номер {number}.",
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "select_recent",
                "model_used": "none",
            }

        selected = recent_projects[idx]

        if not selected.get("exists", True):
            return {
                "response": (
                    f"Папката за проект **{selected.get('name', '?')}** не съществува:\n"
                    f"`{selected.get('path', '?')}`\n\n"
                    "Моля, заредете друг проект."
                ),
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "select_recent",
                "model_used": "none",
            }

        return {
            "response": f"Зареждам проект **{selected.get('name', '?')}**...",
            "schedule_updated": False,
            "schedule_data": None,
            "correction_info": None,
            "intent": "select_recent",
            "model_used": "none",
            "load_project_id": selected.get("id"),
            "load_project_path": selected.get("path"),
        }

    def _handle_load_project(self, message: str) -> dict:
        """Handle project loading intent.

        Tries to extract a path from the message for direct loading.
        """
        # Try to extract path from the message
        import re
        path_match = re.search(r'[A-Za-z]:\\[^\s"\']+|/[^\s"\']+', message)
        if path_match:
            path = path_match.group(0)
            return {
                "response": f"Зареждам проект от **{path}**...",
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "load_project",
                "model_used": "none",
                "load_project_path": path,
            }

        return {
            "response": (
                "Моля, въведете пътя до проектната папка.\n\n"
                "Примери:\n"
                "- `Зареди D:\\Projects\\Горица`\n"
                "- Или изберете папка от бутона 📂 в страничната лента."
            ),
            "schedule_updated": False,
            "schedule_data": None,
            "correction_info": None,
            "intent": "load_project",
            "model_used": "none",
        }

    def _handle_generate_schedule(
        self,
        message: str,
        project_loaded: bool,
        conversion_done: bool,
        project_context: dict | None,
    ) -> dict:
        """Handle schedule generation intent.

        Enforces strict JSON pipeline: only converted .json files are used.
        """
        if not project_loaded:
            return {
                "response": (
                    "⚠️ Първо заредете проект.\n\n"
                    "1. Изберете папката с тендерна документация\n"
                    "2. Натиснете **Зареди проект**\n"
                    "3. Конвертирайте файловете\n"
                    "4. След това кажете: **генерирай график**"
                ),
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "generate_schedule",
                "model_used": "none",
            }

        if not conversion_done:
            return {
                "response": (
                    "⚠️ Файловете не са конвертирани.\n\n"
                    "Натиснете **Конвертирай файлове** в страничната лента, "
                    "след което ще мога да анализирам документацията.\n\n"
                    "_Правило #0: Конвертиране ВИНАГИ преди анализ._"
                ),
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "generate_schedule",
                "model_used": "none",
            }

        if not self.ai or not self.ai.router:
            return {
                "response": "AI не е инициализиран. Проверете API ключовете в .env файла.",
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "generate_schedule",
                "model_used": "none",
            }

        # Get converted files info — ONLY .json files (Rule #0)
        converted_files = []
        if self.files and self.files.base_path:
            converted_files = self.files.get_converted_files()

        if not converted_files:
            return {
                "response": (
                    "⚠️ Няма конвертирани файлове за анализ.\n\n"
                    "Натиснете **Конвертирай файлове** в страничната лента."
                ),
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "generate_schedule",
                "model_used": "none",
            }

        # Validate all files are .json
        try:
            self.ai._validate_json_inputs(converted_files)
        except ValueError as exc:
            return {
                "response": f"⚠️ {exc}",
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "generate_schedule",
                "model_used": "none",
            }

        # Step 1: Analyze documents
        analysis = self.ai.analyze_documents(converted_files)

        if analysis.get("status") == "error":
            return {
                "response": f"Грешка при анализ: {analysis.get('message', 'неизвестна')}",
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "generate_schedule",
                "model_used": "none",
            }

        # Step 2: Generate schedule with verification
        project_type = ""
        if project_context:
            project_type = project_context.get("type", "")

        progress_messages: list[str] = []

        def _progress(msg: str) -> None:
            progress_messages.append(msg)

        gen_result = self.ai.generate_schedule(analysis, project_type, _progress)

        # Build response
        status = gen_result.get("status", "error")
        cycles = gen_result.get("cycles", 0)
        cost = gen_result.get("total_cost", 0.0)
        history = gen_result.get("history", [])

        response_parts = []

        # Progress log
        for msg in progress_messages:
            response_parts.append(f"- {msg}")

        if status == "approved":
            response_parts.append(
                f"\n**График одобрен!** ({cycles} {'цикъл' if cycles == 1 else 'цикъла'} проверка, ${cost:.4f})"
            )
        elif status == "needs_human_review":
            remaining = gen_result.get("remaining_issues", [])
            response_parts.append(
                f"\nСлед {cycles} опита за корекция, следните проблеми остават:"
            )
            for issue in remaining:
                response_parts.append(f"  - {issue}")
            response_parts.append("\nМоля, прегледайте и кажете как да продължа.")
        else:
            response_parts.append(f"\nГрешка: {gen_result.get('error', 'неизвестна')}")

        # Correction history summary
        if history:
            response_parts.append("\n**Корекционен цикъл:**")
            for h in history:
                c = h["cycle"]
                issues_count = len(h["issues"])
                issues_short = ", ".join(h["issues"][:3])
                response_parts.append(f"  Опит {c}: {issues_count} проблема ({issues_short})")

        self.current_schedule = gen_result.get("schedule")
        self.correction_history = history

        # Save schedule to project manager
        if self.project_mgr and self.project_mgr.current_project:
            pid = self.project_mgr.current_project.get("id")
            if pid:
                self.project_mgr.save_progress(pid, {
                    "status": "schedule_generated",
                    "last_schedule": self.current_schedule,
                })

        return {
            "response": "\n".join(response_parts),
            "schedule_updated": status in ("approved", "needs_human_review"),
            "schedule_data": self.current_schedule,
            "correction_info": {
                "status": status,
                "cycles": cycles,
                "cost": cost,
                "history": history,
            },
            "intent": "generate_schedule",
            "model_used": gen_result.get("gen_model", "unknown"),
        }

    def _handle_modify_schedule(self, message: str) -> dict:
        """Handle schedule modification intent.

        Sends the change to AI, re-verifies, then runs local diff validation
        to detect unintended changes, missing/new tasks, etc.
        """
        if not self.current_schedule:
            return {
                "response": "Няма генериран график за промяна. Първо генерирайте график.",
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "modify_schedule",
                "model_used": "none",
            }

        if not self.ai or not self.ai.router:
            return {
                "response": "AI не е инициализиран.",
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "modify_schedule",
                "model_used": "none",
            }

        # Snapshot the old schedule for diff comparison
        old_schedule: list[dict] = []
        if isinstance(self.current_schedule, list):
            old_schedule = self.current_schedule
        elif isinstance(self.current_schedule, dict):
            old_schedule = self.current_schedule.get("tasks", [])

        # Send modification request to AI
        schedule_str = (
            json.dumps(self.current_schedule, ensure_ascii=False)
            if isinstance(self.current_schedule, dict)
            else str(self.current_schedule)
        )

        messages = [{
            "role": "user",
            "content": (
                f"Текущ график:\n{schedule_str}\n\n"
                f"Промяна: {message}\n\n"
                "Приложи промяната и върни коригирания график в JSON."
            ),
        }]

        system_prompt = self.ai.build_system_prompt()
        result = self.ai.router.chat(messages, system_prompt)

        # Re-verify after modification
        rules = self.ai.build_verification_prompt()
        verification = self.ai.router.run_correction_cycle(
            result["content"], rules, max_cycles=2,
            knowledge_prompt=system_prompt,
        )

        new_schedule = verification.get("schedule")

        # --- Local diff validation ---
        validation_notes: list[str] = []
        if self.builder and old_schedule and new_schedule:
            new_tasks: list[dict] = []
            if isinstance(new_schedule, list):
                new_tasks = new_schedule
            elif isinstance(new_schedule, dict):
                new_tasks = new_schedule.get("tasks", [])

            if old_schedule and new_tasks:
                mod_result = self.builder.validate_modification(
                    old_schedule, new_tasks, message,
                )

                if mod_result.get("missing_tasks"):
                    ids = ", ".join(mod_result["missing_tasks"][:5])
                    validation_notes.append(
                        f"🔴 AI-ят е премахнал задачи: {ids}. Проверете внимателно."
                    )

                if mod_result.get("new_tasks"):
                    ids = ", ".join(mod_result["new_tasks"][:5])
                    validation_notes.append(
                        f"🔴 AI-ят е добавил нови задачи: {ids}. Проверете внимателно."
                    )

                if mod_result.get("unintended_changes"):
                    items = mod_result["unintended_changes"]
                    ids = ", ".join(c["id"] for c in items[:5])
                    validation_notes.append(
                        f"ℹ️ Освен поисканата промяна, бяха променени и: {ids}"
                    )
                    for item in items[:3]:
                        fields = ", ".join(item["changed_fields"][:4])
                        validation_notes.append(
                            f"   — {item['id']} ({item['name']}): {fields}"
                        )

                if not mod_result.get("valid") and not validation_notes:
                    validation_notes.append(
                        "⚠️ Внимание: AI-ят е направил непредвидени промени."
                    )

        self.current_schedule = new_schedule

        # Save updated schedule to project manager
        if self.project_mgr and self.project_mgr.current_project:
            pid = self.project_mgr.current_project.get("id")
            if pid:
                self.project_mgr.save_progress(pid, {
                    "status": "schedule_generated",
                    "last_schedule": self.current_schedule,
                })

        # Build response
        response_parts = [
            f"Промяната е приложена.",
            f"Модел: {result['model']}, Проверка: {verification['status']}",
        ]
        if validation_notes:
            response_parts.append("")
            response_parts.append("**Локална проверка:**")
            response_parts.extend(validation_notes)

        return {
            "response": "\n".join(response_parts),
            "schedule_updated": True,
            "schedule_data": self.current_schedule,
            "correction_info": {
                "status": verification["status"],
                "cycles": verification.get("cycles", 0),
            },
            "intent": "modify_schedule",
            "model_used": result["model"],
        }

    def _handle_export(self, message: str) -> dict:
        """Handle export intent — generate requested format and direct to tab."""
        if not self.current_schedule:
            return {
                "response": (
                    "Няма генериран график за експорт.\n\n"
                    "Използвайте таб **Експорт** вдясно след генериране на график."
                ),
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "export",
                "model_used": "none",
            }

        # Save export status to project manager
        if self.project_mgr and self.project_mgr.current_project:
            pid = self.project_mgr.current_project.get("id")
            if pid:
                self.project_mgr.save_progress(pid, {"status": "exported"})

        # Detect requested format
        msg_lower = message.lower()
        wants_pdf = any(w in msg_lower for w in ("pdf", "пдф", "печат"))
        wants_xml = any(w in msg_lower for w in ("xml", "mspdi", "project", "mpp"))

        # Generate export info message
        if wants_pdf or wants_xml:
            parts = ["\U0001f4e6 **Графикът е готов за експорт!**\n"]

            if wants_pdf:
                parts.append(
                    "\U0001f4c4 **PDF** — Отидете в таб **Експорт** и натиснете "
                    "**Генерирай PDF**, след което **Свали PDF**."
                )
            if wants_xml:
                parts.append(
                    "\U0001f4cb **XML** — Отидете в таб **Експорт** и натиснете "
                    "**Генерирай XML**, след което **Свали XML**.\n"
                    "\U0001f4a1 За .mpp: Отворете XML в MS Project \u2192 Save As \u2192 .mpp"
                )

            response = "\n\n".join(parts)
        else:
            response = (
                "\U0001f4e6 **Графикът е готов за експорт!**\n\n"
                "Налични формати в таб **Експорт**:\n"
                "- \U0001f4c4 **PDF** — A3 landscape Gantt диаграма за печат\n"
                "- \U0001f4cb **MSPDI XML** — за отваряне в MS Project\n"
                "- \U0001f527 **JSON** — суровите данни\n\n"
                "\U0001f4a1 За .mpp файл: отворете XML в MS Project \u2192 "
                "File \u2192 Save As \u2192 .mpp"
            )

        return {
            "response": response,
            "schedule_updated": False,
            "schedule_data": None,
            "correction_info": None,
            "intent": "export",
            "model_used": "none",
        }

    def _handle_save_lesson(self, message: str) -> dict:
        """Handle lesson saving intent."""
        if not self.ai or not self.ai.router:
            return {
                "response": "AI не е инициализиран — не може да се провери урокът.",
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "save_lesson",
                "model_used": "none",
            }

        # Extract lesson text (everything after trigger keywords)
        lesson_text = message
        for trigger in ("запиши урок", "научен урок", "запомни"):
            if trigger in message.lower():
                idx = message.lower().find(trigger)
                lesson_text = message[idx + len(trigger):].strip(" :-")
                break

        if not lesson_text or len(lesson_text) < 10:
            return {
                "response": (
                    "Моля, формулирайте урока по-подробно.\n"
                    "Пример: **запиши урок: DN90 PE се полага с 20% по-бързо от DN500**"
                ),
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "save_lesson",
                "model_used": "none",
            }

        # Get existing lessons for context
        existing = ""
        if self.knowledge:
            lessons = self.knowledge.get_lessons()
            existing = "\n".join(lessons[-10:]) if lessons else ""

        # Verify via controller
        result = self.ai.router.save_lesson(lesson_text, "user_request", existing)

        if result["approved"]:
            # Save the lesson
            if self.knowledge:
                self.knowledge.add_lesson(result["formatted_lesson"])

            return {
                "response": (
                    f"Урокът е проверен и записан.\n\n"
                    f"**Урок:** {result['formatted_lesson']}\n"
                    f"**Проверка:** {result['reason']}\n"
                    f"**Модел:** {result['model']}"
                ),
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "save_lesson",
                "model_used": result["model"],
            }

        return {
            "response": (
                f"Урокът НЕ е одобрен от контрольора.\n\n"
                f"**Причина:** {result['reason']}\n"
                f"**Предложение:** {result['formatted_lesson']}\n\n"
                "Можете да го преформулирате и опитате отново."
            ),
            "schedule_updated": False,
            "schedule_data": None,
            "correction_info": None,
            "intent": "save_lesson",
            "model_used": result["model"],
        }

    # ------------------------------------------------------------------
    # Self-evolution handlers
    # ------------------------------------------------------------------

    def _handle_evolve(self, message: str) -> dict:
        """Handle self-evolution intent: analyze, plan, generate changes."""
        if not self.evolution:
            return {
                "response": "Системата за самоеволюция не е инициализирана.",
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "evolve",
                "model_used": "none",
            }

        if not self.ai or not self.ai.router or not self.ai.router.anthropic_available:
            return {
                "response": (
                    "Anthropic API не е достъпен — самоеволюцията изисква Anthropic Claude.\n"
                    "Проверете ANTHROPIC_API_KEY в .env файла."
                ),
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "evolve",
                "model_used": "none",
            }

        progress: list[str] = []

        # Step 1: Analyze
        progress.append("Анализирам заявката... (Anthropic Sonnet 4.6)")
        plan = self.evolution.analyze_request(message)

        if plan.get("error"):
            return {
                "response": f"Грешка при анализ: {plan.get('description', 'неизвестна')}",
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "evolve",
                "model_used": "claude-sonnet-4-6",
            }

        level = plan.get("level", "red")

        # Step 2: Generate changes
        progress.append("Генерирам код... (Anthropic Sonnet 4.6)")
        changes = self.evolution.generate_changes(plan)

        if changes.get("error") and not changes.get("changes"):
            return {
                "response": f"Грешка при генериране: {changes.get('error', 'неизвестна')}",
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "evolve",
                "model_used": "claude-sonnet-4-6",
            }

        # Step 3: Preview
        preview = self.evolution.preview_changes(plan, changes)

        # Build response based on level
        progress_text = "\n".join(progress)

        if level == "green":
            # GREEN: Apply directly, no confirmation needed
            progress.append("Прилагам промени...")
            apply_result = self.evolution.apply_changes(changes)

            if apply_result["failed"] > 0:
                error_text = "\n".join(apply_result["errors"])
                return {
                    "response": (
                        f"{progress_text}\n\n"
                        f"Грешка при прилагане:\n{error_text}"
                    ),
                    "schedule_updated": False,
                    "schedule_data": None,
                    "correction_info": None,
                    "intent": "evolve",
                    "model_used": "claude-sonnet-4-6",
                }

            # Log the change
            self.evolution.log_change(message, plan, "", "applied")

            return {
                "response": (
                    f"{progress_text}\n\n"
                    f"{preview}\n\n"
                    f"Промените са приложени: {plan.get('description', '')}"
                ),
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "evolve",
                "model_used": "claude-sonnet-4-6",
            }

        elif level == "yellow":
            # YELLOW: Requires confirmation
            return {
                "response": (
                    f"{progress_text}\n\n"
                    f"{preview}\n\n"
                    "Тази промяна ще засегне конфигурацията.\n"
                    "Потвърждавате ли? Напишете **Да** за да продължа."
                ),
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "evolve",
                "model_used": "claude-sonnet-4-6",
                "evolution_pending": {
                    "level": level,
                    "plan": plan,
                    "changes": changes,
                    "request": message,
                },
            }

        else:
            # RED: Requires admin code
            admin_set = bool(self.evolution.admin_code)
            if not admin_set:
                return {
                    "response": (
                        f"{progress_text}\n\n"
                        f"{preview}\n\n"
                        "Тази промяна изисква админ код, но **ADMIN_CODE** не е зададен в .env.\n"
                        "Добавете `ADMIN_CODE=вашият-код` в `.env` файла и рестартирайте."
                    ),
                    "schedule_updated": False,
                    "schedule_data": None,
                    "correction_info": None,
                    "intent": "evolve",
                    "model_used": "claude-sonnet-4-6",
                }

            return {
                "response": (
                    f"{progress_text}\n\n"
                    f"{preview}\n\n"
                    "Тази промяна ще модифицира **кода** на приложението.\n\n"
                    "**ВНИМАНИЕ:** Промяната засяга ВСИЧКИ потребители.\n"
                    "Ще бъде създаден автоматичен backup преди промяната.\n\n"
                    "За да продължите, **въведете админ код:**"
                ),
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "evolve",
                "model_used": "claude-sonnet-4-6",
                "evolution_pending": {
                    "level": level,
                    "plan": plan,
                    "changes": changes,
                    "request": message,
                },
            }

    def _handle_confirm_change(self, user_message: str, pending: dict) -> dict:
        """Handle confirmation or admin code for pending evolution changes.

        Args:
            user_message: The user's confirmation message or admin code.
            pending: The pending changes dict from session state.

        Returns:
            Standard response dict with evolution status.
        """
        if not self.evolution:
            return {
                "response": "Системата за самоеволюция не е инициализирана.",
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "confirm_change",
                "model_used": "none",
                "evolution_cleared": True,
            }

        level = pending.get("level", "red")
        plan = pending.get("plan", {})
        changes = pending.get("changes", {})
        request = pending.get("request", "")
        stripped = user_message.strip()

        # Check for cancellation
        if stripped.lower() in ["не", "no", "отказ", "откажи", "cancel"]:
            return {
                "response": "Промяната е отказана.",
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "confirm_change",
                "model_used": "none",
                "evolution_cleared": True,
            }

        if level == "red":
            # Verify admin code
            if not self.evolution.verify_admin_code(stripped):
                return {
                    "response": "Невалиден админ код. Промяната е отказана.",
                    "schedule_updated": False,
                    "schedule_data": None,
                    "correction_info": None,
                    "intent": "confirm_change",
                    "model_used": "none",
                    "evolution_cleared": True,
                }
        else:
            # Yellow: check for confirmation word
            if stripped.lower() not in ["да", "yes", "потвърждавам", "ок", "ok"]:
                return {
                    "response": (
                        "Моля, потвърдете с **Да** или откажете с **Не**."
                    ),
                    "schedule_updated": False,
                    "schedule_data": None,
                    "correction_info": None,
                    "intent": "confirm_change",
                    "model_used": "none",
                    # Keep pending — don't clear
                }

        # Proceed with applying changes
        progress: list[str] = []

        # Backup (for red level)
        backup_hash = ""
        if level == "red":
            progress.append("Създавам backup...")
            backup = self.evolution.create_backup(plan.get("description", ""))
            if backup["success"]:
                backup_hash = backup["commit_hash"]
                progress.append(f"   Git commit: {backup_hash[:8]}")
            else:
                progress.append(f"   Backup неуспешен: {backup.get('error', '?')}")

        # Apply changes
        progress.append("Прилагам промени...")
        apply_result = self.evolution.apply_changes(changes)
        progress.append(f"   Приложени: {apply_result['applied']}, Грешки: {apply_result['failed']}")

        if apply_result["failed"] > 0:
            error_text = "\n".join(apply_result["errors"])
            # Auto-rollback for red level
            if level == "red" and backup_hash:
                progress.append("Тестовете не минаха! Автоматично връщам промените...")
                rollback_result = self.evolution.rollback(backup_hash)
                if rollback_result["success"]:
                    progress.append(f"Възстановен backup от: {backup_hash[:8]}")
                else:
                    progress.append(f"Rollback неуспешен: {rollback_result.get('error', '?')}")

            return {
                "response": (
                    "\n".join(progress) + "\n\n"
                    f"Грешки при прилагане:\n{error_text}\n\n"
                    "Моля, опишете какво искахте по-подробно и ще опитам отново."
                ),
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "confirm_change",
                "model_used": "claude-sonnet-4-6",
                "evolution_cleared": True,
            }

        # Run tests (for red level)
        if level == "red":
            progress.append("Тествам...")
            test_result = self.evolution.test_changes()
            progress.append(
                f"   {test_result['tests_passed']}/{test_result['tests_run']} теста минаха"
            )

            if not test_result["passed"]:
                error_text = "\n".join(test_result["errors"])
                progress.append("Тестовете не минаха! Автоматично връщам промените...")

                if backup_hash:
                    rollback_result = self.evolution.rollback(backup_hash)
                    if rollback_result["success"]:
                        progress.append(f"Възстановен backup от: {backup_hash[:8]}")
                    else:
                        progress.append(f"Rollback неуспешен: {rollback_result.get('error', '?')}")

                return {
                    "response": (
                        "\n".join(progress) + "\n\n"
                        f"Грешка: {error_text}\n\n"
                        "Моля, опишете какво искахте по-подробно и ще опитам отново."
                    ),
                    "schedule_updated": False,
                    "schedule_data": None,
                    "correction_info": None,
                    "intent": "confirm_change",
                    "model_used": "claude-sonnet-4-6",
                    "evolution_cleared": True,
                }

        # Commit changes
        description = plan.get("description", request[:50])
        commit_result = self.evolution.commit_changes(description)
        commit_hash = commit_result.get("commit_hash", "?")

        # Log
        self.evolution.log_change(request, plan, backup_hash, "applied")

        progress.append("Готово! Промените са приложени успешно.")
        progress.append(f"   Git commit: '{description}' ({commit_hash[:8]})")

        return {
            "response": "\n".join(progress),
            "schedule_updated": False,
            "schedule_data": None,
            "correction_info": None,
            "intent": "confirm_change",
            "model_used": "claude-sonnet-4-6",
            "evolution_cleared": True,
            "evolution_applied": True,
        }

    def _handle_question(
        self, message: str, project_context: dict | None
    ) -> dict:
        """Handle knowledge question via AI chat."""
        return self._handle_general(message, project_context)

    def _handle_general(
        self, message: str, project_context: dict | None
    ) -> dict:
        """Handle general messages via AI chat."""
        if not self.ai or not self.ai.router:
            # Offline mode — keyword-based responses
            return self._offline_response(message)

        # Build conversation for AI (last 10 messages for context)
        recent_history = self.history[-10:]

        result = self.ai.chat_response(recent_history, project_context)

        fallback_note = ""
        if result.get("fallback"):
            fallback_note = "\n\n_DeepSeek не отговаря. Отговорът е от Anthropic Claude._"

        return {
            "response": result["content"] + fallback_note,
            "schedule_updated": False,
            "schedule_data": None,
            "correction_info": None,
            "intent": "general",
            "model_used": result.get("model", "none"),
        }

    def _offline_response(self, message: str) -> dict:
        """Fallback response when no AI is available."""
        stats = {}
        if self.knowledge:
            stats = self.knowledge.get_knowledge_stats()

        return {
            "response": (
                "AI не е наличен в момента.\n\n"
                f"Базата знания съдържа: {stats.get('lessons', 0)} урока, "
                f"{stats.get('methodologies', 0)} методики.\n\n"
                "Проверете API ключовете в .env файла и рестартирайте."
            ),
            "schedule_updated": False,
            "schedule_data": None,
            "correction_info": None,
            "intent": "general",
            "model_used": "none",
        }

    # ------------------------------------------------------------------
    # Intent detection
    # ------------------------------------------------------------------

    def _detect_intent(self, message: str) -> str:
        """Detect the user's intent from keywords.

        Args:
            message: The user's message.

        Returns:
            Intent string.
        """
        message_lower = message.lower()

        best_intent = "general"
        best_score = 0

        for intent, keywords in INTENT_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in message_lower)
            if score > best_score:
                best_score = score
                best_intent = intent

        return best_intent

    # ------------------------------------------------------------------
    # Correction summary
    # ------------------------------------------------------------------

    def get_correction_summary(self) -> str:
        """Get a human-readable summary of the last correction cycle.

        Returns:
            Formatted string with cycle history.
        """
        if not self.correction_history:
            return "Няма история на корекции."

        lines = ["**Цикъл на проверка:**"]
        for h in self.correction_history:
            c = h["cycle"]
            issues = h["issues"]
            issues_str = ", ".join(issues[:3])
            if len(issues) > 3:
                issues_str += f" (+{len(issues) - 3} други)"
            lines.append(f"  Опит {c}: {len(issues)} проблема ({issues_str})")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # History management
    # ------------------------------------------------------------------

    def get_chat_history(self) -> list[dict[str, str]]:
        """Get the full chat history."""
        return self.history

    def clear_history(self) -> None:
        """Clear all chat history and schedule data."""
        self.history = []
        self.current_schedule = None
        self.correction_history = []

    def restore_history(self, messages: list[dict[str, str]]) -> None:
        """Restore chat history from saved data."""
        self.history = list(messages)
