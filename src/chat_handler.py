"""Chat session handler — processes user messages via dual AI system.

Routes intents to appropriate actions: chat, generate, modify, export, lessons, evolve.
Uses AIProcessor (backed by AIRouter) for all AI operations.
Includes self-evolution support with 3-level change management (green/yellow/red).
Enforces strict JSON pipeline: converted files only for AI operations (Rule #0).
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.ai_processor import AIProcessor
    from src.file_manager import FileManager
    from src.knowledge_manager import KnowledgeManager
    from src.project_manager import ProjectManager
    from src.schedule_builder import ScheduleBuilder
    from src.self_evolution import SelfEvolution

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# AI Intent Detection — prompt template
# ---------------------------------------------------------------------------
INTENT_DETECTION_PROMPT = """\
Ти си рутер на команди. Потребителят пише на свободен български.
Твоята задача е да разбереш какво иска и да върнеш САМО валиден JSON.

Налични команди (intent):
- load_project    : зареди/отвори/смени/затвори проект
- generate_schedule : генерирай/създай строителен график (Gantt)
- modify_schedule : промени/коригирай вече генериран график
- export          : свали/експортирай в PDF/XML/JSON
- ask_question    : въпрос за проект, правила, обобщение, статус
- save_lesson     : запиши научен урок
- evolve          : промени самото приложение (нова функция, модул)
- chat            : общ разговор, поздрав, нещо извън горните

{state_context}

Отговори САМО с JSON (без ``` , без коментари):
{{"intent": "...", "params": {{...}}}}

За load_project добави:  "params": {{"action": "open"|"close", "query": "..."}}
  query = САМО ключовата дума за името (без "моля", "зареди", "проект" и т.н.)
За generate_schedule:    "params": {{"instructions": "..."}}
За modify_schedule:      "params": {{"change": "..."}}
За export:               "params": {{"format": "pdf"|"xml"|"json"}}
За ask_question/chat:    "params": {{"topic": "..."}}\
"""

# ---------------------------------------------------------------------------
# Fallback keyword matching (used when AI is unavailable)
# ---------------------------------------------------------------------------
INTENT_KEYWORDS: dict[str, list[str]] = {
    "load_project": ["зареди", "отвори", "затвори", "закрий"],
    "generate_schedule": [
        "генерирай", "график", "създай", "направи", "gantt",
        "линеен", "графика", "нов график",
    ],
    "ask_question": [
        "какво", "какви", "как", "защо", "кога", "колко", "обясни",
        "правило", "методика", "урок", "обобщение", "покажи",
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

LOAD_PROJECT_PHRASES: list[str] = [
    "зареди проект", "отвори проект", "смени проект", "затвори проект",
    "закрий проект", "зареди папка", "отвори папка",
]


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
        progress_callback: Any | None = None,
        pending_sequence: dict | None = None,
        pending_conflicts: list[str] | None = None,
        pending_conflicts_analysis: dict | None = None,
    ) -> dict:
        """Process a user message and return a structured response.

        Args:
            user_message: The user's input text.
            project_loaded: Whether a project is loaded.
            conversion_done: Whether files are converted.
            project_context: Optional dict with current project info.
            pending_changes: Pending self-evolution changes awaiting confirmation.
            recent_projects: List of recent projects for number selection.
            progress_callback: Optional callable(pct: float, text: str) for
                progress updates (0.0–1.0).
            pending_sequence: Pending sequence questionnaire state.

        Returns:
            Dict with response, schedule_updated, schedule_data,
            correction_info, intent, model_used, plus optional
            evolution_pending / evolution_applied / evolution_cleared /
            load_project_path / load_project_id / pending_sequence.
        """
        self._progress = progress_callback or (lambda pct, txt: None)
        # Check if there are pending evolution changes waiting for confirmation
        if pending_changes:
            return self._handle_confirm_change(user_message, pending_changes)

        # Check if we are mid-sequence questionnaire
        if pending_sequence:
            return self._handle_sequence_answer(user_message, pending_sequence)

        # Check if user is resolving cross-document conflicts
        if pending_conflicts and pending_conflicts_analysis:
            return self._handle_conflict_resolution(
                user_message, pending_conflicts, pending_conflicts_analysis
            )

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

        # --- AI-powered intent detection ---
        self._progress(0.05, "Разпознаване на заявката...")
        ai_result = self._detect_intent_ai(
            user_message, project_loaded, conversion_done,
            project_context, recent_projects,
        )
        intent = ai_result.get("intent", "chat")
        intent_params = ai_result.get("params", {})

        # If AI detected load_project with a clean query, use it directly
        if intent == "load_project" and intent_params.get("query"):
            load_result = self._handle_load_project_smart(
                user_message, intent_params, recent_projects,
            )
            if load_result:
                self._progress(1.0, "Готово!")
                self.history.append({"role": "assistant", "content": load_result["response"]})
                return load_result

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

        self._progress(1.0, "Готово!")
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

        Tries to extract a path from the message, or match a project by name.
        Also handles close/switch project commands.
        """
        base = {
            "schedule_updated": False,
            "schedule_data": None,
            "correction_info": None,
            "intent": "load_project",
            "model_used": "none",
        }

        msg_lower = message.lower()

        # 0) Handle close/switch project
        if any(w in msg_lower for w in ("затвори", "закрий")):
            return {**base, "response":
                    "За да затворите текущия проект, натиснете "
                    "**Смени проект** в страничната лента.",
                    "close_project": True}

        # 1) Try full file path
        path_match = re.search(r'[A-Za-z]:\\[^\s"\']+|/[^\s"\']+', message)
        if path_match:
            path = path_match.group(0)
            return {**base, "response": f"Зареждам проект от **{path}**...",
                    "load_project_path": path}

        # 2) Try to find project by name in recent projects
        if self.project_mgr:
            recent = self.project_mgr.get_recent_projects(10)
            if recent:
                # Strip known command words to isolate the project name
                stripped_msg = msg_lower
                for word in ("зареди", "отвори", "проект", "папка", "път",
                             "директория", "смени", "на"):
                    stripped_msg = stripped_msg.replace(word, "")
                query = stripped_msg.strip()

                if query:
                    # Exact name match first, then substring
                    for proj in recent:
                        name = proj.get("name", "").lower()
                        if name == query:
                            if not proj.get("exists", True):
                                return {**base, "response":
                                        f"Папката за **{proj['name']}** не съществува."}
                            return {**base,
                                    "response": f"Зареждам проект **{proj['name']}**...",
                                    "load_project_path": proj["path"]}

                    for proj in recent:
                        name = proj.get("name", "").lower()
                        if query in name or name in query:
                            if not proj.get("exists", True):
                                return {**base, "response":
                                        f"Папката за **{proj['name']}** не съществува."}
                            return {**base,
                                    "response": f"Зареждам проект **{proj['name']}**...",
                                    "load_project_path": proj["path"]}

                # No match found — show available projects with numbers
                names = ", ".join(
                    f"**{i+1}. {p['name']}**" for i, p in enumerate(recent[:5])
                    if p.get("exists", True)
                )
                if query:
                    msg = f"Не намерих проект '{query}'."
                else:
                    msg = "Кой проект да заредя?"
                return {**base, "response":
                        f"{msg}\n\nНалични проекти: {names}\n\n"
                        "Изберете с номер (напр. **1**) или въведете пълен път до папката."}

        return {**base, "response": (
            "Моля, въведете пътя до проектната папка.\n\n"
            "Може да:\n"
            "- Изберете от скорошните проекти в страничната лента\n"
            "- Въведете пълен път (напр. `D:\\Проекти\\Име на проект`)\n"
            "- Натиснете бутона 📂 за избор на папка"
        )}

    @staticmethod
    def _extract_project_type(analysis: dict, project_context: dict | None = None) -> str:
        """Extract project_type from AI analysis result with fallback to manual selection.

        Args:
            analysis: Result dict from analyze_documents(); its "analysis" value
                      may be a raw JSON string or an already-parsed dict.
            project_context: Optional dict with a "type" key (manual selection).

        Returns:
            project_type string, or "" if not determinable.
        """
        project_type = ""
        raw = analysis.get("analysis", "")
        if isinstance(raw, str):
            try:
                project_type = json.loads(raw).get("project_type", "")
            except Exception:
                pass
        elif isinstance(raw, dict):
            project_type = raw.get("project_type", "")

        if not project_type and project_context:
            project_type = project_context.get("type", "")

        return project_type

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
        self._progress(0.10, "Анализ на документите...")
        all_text = self.files.get_all_text() if self.files else ""
        analysis = self.ai.analyze_documents(converted_files, all_text=all_text)

        if analysis.get("status") == "error":
            return {
                "response": f"Грешка при анализ: {analysis.get('message', 'неизвестна')}",
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "generate_schedule",
                "model_used": "none",
            }

        # Step 1a: Surface conflicts found during cross-document analysis
        conflicts: list[str] = []
        raw_analysis = analysis.get("analysis", "")
        if isinstance(raw_analysis, str):
            try:
                parsed_a = json.loads(raw_analysis)
                conflicts = parsed_a.get("conflicts", [])
            except Exception:
                pass
        elif isinstance(raw_analysis, dict):
            conflicts = raw_analysis.get("conflicts", [])

        if conflicts:
            conflict_lines = "\n".join(f"  - {c}" for c in conflicts)
            return {
                "response": (
                    f"⚠️ **Открити противоречия между файловете — необходимо е вашето решение "
                    f"преди генерирането:**\n{conflict_lines}\n\n"
                    "Моля, уточнете кои стойности са верни. "
                    "След вашия отговор ще продължа с генерирането."
                ),
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "generate_schedule",
                "model_used": analysis.get("model", "none"),
                "pending_conflicts": conflicts,
                "pending_analysis": analysis,
            }

        # Step 1b: Sequence questionnaire — ask before generating
        seq_state = self._start_sequence_questionnaire(analysis)
        if seq_state:
            return {
                "response": seq_state["question"],
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "generate_schedule",
                "model_used": analysis.get("model", "none"),
                "pending_sequence": seq_state,
            }

        # Step 1c: Extract locations from situation / site-plan files (ground-truth toponyms)
        situation_locations: list[str] = []
        if self.files and self.ai:
            classification = self.files.classify_files(ai_processor=self.ai)
            situation_paths = classification.get("situation_paths", [])
            if situation_paths:
                self._progress(0.20, f"Четене на ситуация ({len(situation_paths)} файл/а)...")
                for sit_path in situation_paths:
                    locs = self.ai.extract_situation_locations(sit_path)
                    situation_locations.extend(locs)
                if situation_locations:
                    logger.info(
                        "Situation extraction added %d locations", len(situation_locations)
                    )

        # Step 2: Generate schedule with verification
        self._progress(0.25, "Генериране на график...")

        project_type = self._extract_project_type(analysis, project_context)

        progress_messages: list[str] = []

        # Progress steps: generate=25%, verify cycles up to 90%
        _cycle_pcts = [0.45, 0.60, 0.75, 0.85, 0.90, 0.92]
        _cycle_idx = [0]

        def _progress(msg: str) -> None:
            progress_messages.append(msg)
            pct = _cycle_pcts[min(_cycle_idx[0], len(_cycle_pcts) - 1)]
            _cycle_idx[0] += 1
            self._progress(pct, msg)

        gen_result = self.ai.generate_schedule(
            analysis, project_type, _progress,
            all_text=all_text,
            extra_locations=situation_locations or None,
            sequence_constraints=project_context.get("sequence_constraints") if project_context else None,
        )

        # Build response
        status = gen_result.get("status", "error")
        cycles = gen_result.get("cycles", 0)
        cost = gen_result.get("total_cost", 0.0)
        history = gen_result.get("history", [])

        response_parts = []

        # Show situation location extraction result
        if situation_locations:
            loc_preview = ", ".join(situation_locations[:5])
            if len(situation_locations) > 5:
                loc_preview += f" и още {len(situation_locations) - 5}"
            response_parts.append(
                f"📍 **Прочетена ситуация:** {len(situation_locations)} топонима "
                f"({loc_preview})"
            )

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

        # Hallucination warnings
        hallucination_warnings = gen_result.get("hallucination_warnings", [])
        if hallucination_warnings:
            response_parts.append(
                f"\n⚠️ **Открити {len(hallucination_warnings)} потенциални халюцинации в имена:**"
            )
            for w in hallucination_warnings[:10]:
                response_parts.append(f"  - {w}")
            if len(hallucination_warnings) > 10:
                response_parts.append(f"  ... и още {len(hallucination_warnings) - 10}")
            response_parts.append(
                "\nМоля, проверете тези имена спрямо оригиналната документация преди употреба."
            )

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

        self._progress(0.15, "Изпращане на промяната към AI...")
        system_prompt = self.ai.build_system_prompt()
        result = self.ai.router.chat(messages, system_prompt)

        # Re-verify after modification
        self._progress(0.50, "Проверка на промените...")
        rules = self.ai.build_verification_prompt()

        def _mod_progress(msg: str) -> None:
            self._progress(0.70, msg)

        verification = self.ai.router.run_correction_cycle(
            result["content"], rules, max_cycles=2,
            knowledge_prompt=system_prompt,
            progress_callback=_mod_progress,
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

    # ------------------------------------------------------------------
    # Conflict resolution
    # ------------------------------------------------------------------

    def _handle_conflict_resolution(
        self,
        user_message: str,
        conflicts: list[str],
        analysis: dict,
    ) -> dict:
        """Handle user's resolution of cross-document conflicts.

        The user provides clarification (e.g. "use file A values").
        We patch the analysis with their answer and continue generation.
        """
        _base = {
            "schedule_updated": False,
            "schedule_data": None,
            "correction_info": None,
            "intent": "generate_schedule",
            "model_used": "none",
        }

        if not self.ai or not self.ai.router:
            return {**_base, "response": "AI не е инициализиран."}

        # Ask AI to patch the analysis based on user clarification
        self._progress(0.10, "Прилагане на вашите уточнения...")

        conflict_text = "\n".join(f"- {c}" for c in conflicts)
        raw_analysis = analysis.get("analysis", "")

        patch_messages = [{
            "role": "user",
            "content": (
                f"Имаше противоречия между документите:\n{conflict_text}\n\n"
                f"Потребителят отговори: \"{user_message}\"\n\n"
                f"Текущ анализ:\n{raw_analysis}\n\n"
                "Актуализирай анализа като приложиш решенията на потребителя. "
                "Върни САМО коригирания JSON анализ (без обяснения)."
            ),
        }]
        system_prompt = self.ai.build_system_prompt()
        patch_result = self.ai.router.chat(patch_messages, system_prompt)

        if patch_result.get("error"):
            return {**_base,
                "response": f"Грешка при прилагане на корекциите: {patch_result['content']}"}

        # Build patched analysis
        patched_analysis = {**analysis, "analysis": patch_result["content"]}

        # Check if questionnaire is needed for patched analysis
        seq_state = self._start_sequence_questionnaire(patched_analysis)
        if seq_state:
            return {
                **_base,
                "response": (
                    "Уточненията са приложени.\n\n" + seq_state["question"]
                ),
                "model_used": patch_result.get("model", "none"),
                "pending_sequence": seq_state,
            }

        # Proceed directly to generation
        self._progress(0.20, "Генериране на график...")
        result = self._continue_generation(patched_analysis, {})
        result["response"] = "Уточненията са приложени.\n\n" + result.get("response", "")
        return result

    # ------------------------------------------------------------------
    # Sequence questionnaire
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_sections_from_analysis(analysis: dict) -> list[str]:
        """Extract section/branch names from analysis quantities.

        Returns list of section names found in the analysis, e.g.
        ["Клон 1", "Клон 2", "ул. Витоша", ...].
        Empty list if no sections found.
        """
        raw = analysis.get("analysis", "")
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except Exception:
                return []
        elif isinstance(raw, dict):
            parsed = raw
        else:
            return []

        quantities = parsed.get("quantities", {})
        sections: list[str] = []

        # quantities may be a dict {section_name: {...}} or a list
        if isinstance(quantities, dict):
            sections = [k for k in quantities.keys() if k and k != "total"]
        elif isinstance(quantities, list):
            for item in quantities:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("section") or item.get("branch")
                    if name:
                        sections.append(str(name))

        return sections

    def _start_sequence_questionnaire(self, analysis: dict) -> dict | None:
        """Start the sequence questionnaire if the project has both water and sewer.

        Returns a pending_sequence state dict with the first question,
        or None if the questionnaire is not needed (e.g. water-only project).
        """
        raw = analysis.get("analysis", "")
        parsed: dict = {}
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except Exception:
                pass
        elif isinstance(raw, dict):
            parsed = raw

        # Check scope + project_type + quantities keys for network presence
        scope = str(parsed.get("scope", "")).lower()
        project_type_str = str(parsed.get("project_type", "")).lower()
        quantities_str = str(parsed.get("quantities", "")).lower()
        combined = f"{scope} {project_type_str} {quantities_str}"

        _WATER_KEYWORDS = [
            "водопровод", "вода", "water",
            "водоснабдяване", "питейна", "водопроводна", "тласкател",
        ]
        _SEWER_KEYWORDS = [
            "канализация", "канал", "sewer",
            "отводняване", "канализационна", "фекална", "дъждовна",
        ]
        # "вк мрежа" / "в/к" означава комбинирана В+К мрежа — задейства и двете
        _COMBINED_KEYWORDS = ["вк мрежа", "в/к мрежа"]
        has_combined = any(w in combined for w in _COMBINED_KEYWORDS)
        has_water = has_combined or any(w in combined for w in _WATER_KEYWORDS)
        has_sewer = has_combined or any(w in combined for w in _SEWER_KEYWORDS)

        # Only ask if BOTH networks are present
        if not (has_water and has_sewer):
            return None

        sections = self._extract_sections_from_analysis(analysis)

        return {
            "step": "q1",
            "analysis": analysis,
            "sections": sections,
            "constraints": {},  # will be filled as user answers
            "question": (
                "Преди да генерирам графика, имам един въпрос:\n\n"
                "**Коя мрежа се изпълнява първа?**\n"
                "  В — Водопровод първо\n"
                "  К — Канализация първо"
            ),
        }

    def _handle_sequence_answer(self, user_message: str, state: dict) -> dict:
        """Handle user answers during the sequence questionnaire.

        Returns either the next question (with pending_sequence)
        or triggers schedule generation (without pending_sequence).
        """
        step = state.get("step")
        msg = user_message.strip().upper()

        _base = {
            "schedule_updated": False,
            "schedule_data": None,
            "correction_info": None,
            "intent": "generate_schedule",
            "model_used": "none",
        }

        # ── Q1: water or sewer first? ───────────────────────────────────
        if step == "q1":
            if msg.startswith("В") or "ВОДОПРОВОД" in msg or "ВОДА" in msg:
                choice = "water_first"
                choice_label = "Водопровод → Канализация"
            elif msg.startswith("К") or "КАНАЛ" in msg:
                choice = "sewer_first"
                choice_label = "Канализация → Водопровод"
            else:
                return {**_base, "response": (
                    "Моля, отговори с **В** (Водопровод първо) или **К** (Канализация първо)."
                ), "pending_sequence": state}

            sections = state.get("sections", [])
            new_state = {**state, "step": "q2", "constraints": {"default": choice}}

            if not sections:
                # No named sections — apply to whole project and generate
                return self._generate_with_sequence(new_state)

            sections_list = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(sections))
            return {**_base,
                "response": (
                    f"Разбрах: **{choice_label}** за целия проект.\n\n"
                    f"Важи ли това за **всички участъци**?\n"
                    f"  **ДА** — генерирай\n"
                    f"  **НЕ** — ще посоча изключенията\n\n"
                    f"Намерени участъци:\n{sections_list}"
                ),
                "pending_sequence": new_state,
            }

        # ── Q2: same for all sections? ──────────────────────────────────
        if step == "q2":
            if "ДА" in msg or msg in ("Д", "YES", "Y", "DA"):
                return self._generate_with_sequence(state)

            if "НЕ" in msg or msg in ("Н", "NO", "N", "NE"):
                sections = state.get("sections", [])
                sections_list = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(sections))
                default_label = (
                    "Водопровод → Канализация"
                    if state["constraints"]["default"] == "water_first"
                    else "Канализация → Водопровод"
                )
                opposite_label = (
                    "Канализация → Водопровод"
                    if state["constraints"]["default"] == "water_first"
                    else "Водопровод → Канализация"
                )
                return {**_base,
                    "response": (
                        f"Кои участъци имат обратна последователност "
                        f"(**{opposite_label}**)?\n"
                        f"Напиши номерата, разделени със запетая "
                        f"(напр. **1, 3**):\n\n{sections_list}"
                    ),
                    "pending_sequence": {**state, "step": "q2_exceptions"},
                }

            return {**_base, "response": (
                "Моля, отговори с **ДА** или **НЕ**."
            ), "pending_sequence": state}

        # ── Q2 exceptions: which sections are different? ────────────────
        if step == "q2_exceptions":
            sections = state.get("sections", [])
            default = state["constraints"]["default"]
            opposite = "sewer_first" if default == "water_first" else "water_first"

            # Parse numbers from user input
            nums = [int(n) - 1 for n in re.findall(r"\d+", msg)
                    if 1 <= int(n) <= len(sections)]

            if not nums:
                return {**_base, "response": (
                    "Не разпознах номера. Моля, напиши номерата на участъците "
                    "(напр. **1, 3**)."
                ), "pending_sequence": state}

            exception_names = [sections[i] for i in nums]
            constraints = {**state["constraints"]}
            for name in exception_names:
                constraints[name] = opposite

            exc_label = ", ".join(exception_names)
            return self._generate_with_sequence({**state, "constraints": constraints,
                                                 "_exc_label": exc_label})

        # Unknown step — clear and restart
        return {**_base, "response": "Нещо се обърка. Напиши **генерирай график** отново."}

    def _generate_with_sequence(self, state: dict) -> dict:
        """Trigger schedule generation with collected sequence constraints."""
        analysis = state["analysis"]
        constraints = state["constraints"]

        # Build human-readable summary
        default = constraints.get("default", "water_first")
        default_label = "Водопровод → Канализация" if default == "water_first" else "Канализация → Водопровод"
        summary = f"Последователност: **{default_label}**"
        exc_label = state.get("_exc_label")
        if exc_label:
            opposite_label = "Канализация → Водопровод" if default == "water_first" else "Водопровод → Канализация"
            summary += f"\nИзключения ({opposite_label}): {exc_label}"

        # Re-enter generation flow
        result = self._continue_generation(analysis, constraints)
        result["response"] = summary + "\n\n" + result.get("response", "")
        result.pop("pending_sequence", None)
        return result

    def _continue_generation(self, analysis: dict, sequence_constraints: dict) -> dict:
        """Run the generation steps after questionnaire is complete."""
        all_text = self.files.get_all_text() if self.files else ""

        # Situation locations
        situation_locations: list[str] = []
        if self.files and self.ai:
            classification = self.files.classify_files(ai_processor=self.ai)
            for sit_path in classification.get("situation_paths", []):
                situation_locations.extend(self.ai.extract_situation_locations(sit_path))

        project_type = self._extract_project_type(analysis)

        progress_messages: list[str] = []
        _cycle_pcts = [0.45, 0.60, 0.75, 0.85, 0.90, 0.92]
        _cycle_idx = [0]

        def _progress(msg: str) -> None:
            progress_messages.append(msg)
            pct = _cycle_pcts[min(_cycle_idx[0], len(_cycle_pcts) - 1)]
            _cycle_idx[0] += 1
            self._progress(pct, msg)

        gen_result = self.ai.generate_schedule(
            analysis, project_type, _progress,
            all_text=all_text,
            extra_locations=situation_locations or None,
            sequence_constraints=sequence_constraints,
        )

        status = gen_result.get("status", "error")
        cycles = gen_result.get("cycles", 0)
        cost = gen_result.get("total_cost", 0.0)
        history = gen_result.get("history", [])
        response_parts = []

        for msg in progress_messages:
            response_parts.append(f"- {msg}")

        if status == "approved":
            response_parts.append(
                f"\n**График одобрен!** ({cycles} {'цикъл' if cycles == 1 else 'цикъла'} проверка, ${cost:.4f})"
            )
        elif status == "needs_human_review":
            remaining = gen_result.get("remaining_issues", [])
            response_parts.append(f"\nСлед {cycles} опита остават проблеми:")
            for issue in remaining:
                response_parts.append(f"  - {issue}")
        else:
            response_parts.append(f"\nГрешка: {gen_result.get('error', 'неизвестна')}")

        hallucination_warnings = gen_result.get("hallucination_warnings", [])
        if hallucination_warnings:
            response_parts.append(
                f"\n⚠️ **{len(hallucination_warnings)} потенциални халюцинации в имена:**"
            )
            for w in hallucination_warnings[:10]:
                response_parts.append(f"  - {w}")

        self.current_schedule = gen_result.get("schedule")
        self.correction_history = history

        # Save to project manager (same as _handle_generate_schedule)
        if self.project_mgr and self.project_mgr.current_project:
            pid = self.project_mgr.current_project.get("id")
            if pid:
                self.project_mgr.save_progress(pid, {
                    "status": "schedule_generated",
                    "last_schedule": self.current_schedule,
                })

        return {
            "response": "\n".join(response_parts),
            "schedule_updated": bool(self.current_schedule),
            "schedule_data": self.current_schedule,
            "correction_info": {
                "status": status,
                "cycles": cycles,
                "cost": cost,
                "history": history,
            },
            "intent": "generate_schedule",
            "model_used": gen_result.get("gen_model", "none"),
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
        self._progress(0.20, "Изпращане към AI...")
        recent_history = self.history[-10:]

        result = self.ai.chat_response(recent_history, project_context)
        self._progress(0.90, "Получаване на отговор...")

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
    # AI-powered intent detection
    # ------------------------------------------------------------------

    def _detect_intent_ai(
        self,
        message: str,
        project_loaded: bool,
        conversion_done: bool,
        project_context: dict | None,
        recent_projects: list[dict] | None,
    ) -> dict:
        """Detect intent via DeepSeek AI — understands natural Bulgarian.

        Sends a cheap, fast call to DeepSeek that translates free-form user
        input into a structured {intent, params} JSON. Falls back to keyword
        matching if AI is unavailable.

        Returns:
            Dict with 'intent' and 'params' keys.
        """
        # Build state context for the AI
        state_parts: list[str] = []
        if project_loaded:
            proj_name = ""
            if project_context:
                from pathlib import Path
                proj_name = Path(project_context.get("path", "")).name
            state_parts.append(f"Текущ проект: '{proj_name}' (зареден)")
            if conversion_done:
                state_parts.append("Файлове: конвертирани, готови за анализ")
            else:
                state_parts.append("Файлове: НЕ са конвертирани")
            if self.current_schedule:
                state_parts.append("График: генериран")
            else:
                state_parts.append("График: няма")
        else:
            state_parts.append("Няма зареден проект.")

        if recent_projects:
            names = [f"  {i+1}. {p.get('name', '?')}" for i, p in enumerate(recent_projects[:5])]
            state_parts.append("Налични проекти:\n" + "\n".join(names))

        state_context = "\n".join(state_parts)

        # Try AI detection
        if self.ai and self.ai.router and (
            self.ai.router.deepseek_available or self.ai.router.anthropic_available
        ):
            try:
                prompt = INTENT_DETECTION_PROMPT.format(state_context=state_context)
                messages = [{"role": "user", "content": message}]
                result = self.ai.router.chat(messages, prompt)
                parsed = self.ai.router._parse_json_response(result.get("content", "{}"))
                intent = parsed.get("intent", "chat")
                # Normalize: 'chat' → 'general' for handler compatibility
                if intent == "chat":
                    intent = "general"
                logger.info("AI intent: %s, params: %s", intent, parsed.get("params"))
                return {"intent": intent, "params": parsed.get("params", {})}
            except Exception as exc:
                logger.warning("AI intent detection failed, using keywords: %s", exc)

        # Fallback to keyword matching
        return {"intent": self._detect_intent_keywords(message), "params": {}}

    def _detect_intent_keywords(self, message: str) -> str:
        """Fallback keyword-based intent detection (no AI needed)."""
        message_lower = message.lower()

        for phrase in LOAD_PROJECT_PHRASES:
            if phrase in message_lower:
                return "load_project"

        if re.search(r'[A-Za-z]:\\[^\s"\']+|/[^\s"\']+', message):
            return "load_project"

        best_intent = "general"
        best_score = 0
        for intent, keywords in INTENT_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in message_lower)
            if score > best_score:
                best_score = score
                best_intent = intent

        return best_intent

    # ------------------------------------------------------------------
    # Smart project loading (uses AI-extracted params)
    # ------------------------------------------------------------------

    def _handle_load_project_smart(
        self,
        message: str,
        params: dict,
        recent_projects: list[dict] | None,
    ) -> dict | None:
        """Load a project using the AI-extracted query parameter.

        The AI has already cleaned 'зареди проект Плевен моля' → query='плевен'.
        We just need to match against recent projects.

        Returns:
            Response dict, or None to fall through to _handle_load_project.
        """
        base = {
            "schedule_updated": False,
            "schedule_data": None,
            "correction_info": None,
            "intent": "load_project",
            "model_used": "none",
        }

        action = params.get("action", "open")
        query = (params.get("query") or "").strip().lower()

        # Close project
        if action == "close":
            return {**base,
                    "response": "За да затворите текущия проект, натиснете "
                    "**Смени проект** в страничната лента.",
                    "close_project": True}

        if not query:
            return None  # Fall through to original handler

        # Check for file path in the query
        path_match = re.search(r'[A-Za-z]:\\[^\s"\']+|/[^\s"\']+', message)
        if path_match:
            path = path_match.group(0)
            return {**base, "response": f"Зареждам проект от **{path}**...",
                    "load_project_path": path}

        # Match against recent projects
        if not recent_projects and self.project_mgr:
            recent_projects = self.project_mgr.get_recent_projects(10)

        if not recent_projects:
            return None  # Fall through

        # Exact match
        for proj in recent_projects:
            name = proj.get("name", "").lower()
            if name == query:
                if not proj.get("exists", True):
                    return {**base, "response":
                            f"Папката за **{proj['name']}** не съществува."}
                return {**base,
                        "response": f"Зареждам проект **{proj['name']}**...",
                        "load_project_path": proj["path"]}

        # Word-level fuzzy match: any query word in project name
        query_words = [w for w in query.split() if len(w) > 1]
        best_match = None
        best_score = 0
        for proj in recent_projects:
            name = proj.get("name", "").lower()
            score = sum(1 for w in query_words if w in name)
            if score > best_score:
                best_score = score
                best_match = proj

        if best_match and best_score > 0:
            if not best_match.get("exists", True):
                return {**base, "response":
                        f"Папката за **{best_match['name']}** не съществува."}
            return {**base,
                    "response": f"Зареждам проект **{best_match['name']}**...",
                    "load_project_path": best_match["path"]}

        # No match — show list
        names = ", ".join(
            f"**{i+1}. {p['name']}**" for i, p in enumerate(recent_projects[:5])
            if p.get("exists", True)
        )
        return {**base, "response":
                f"Не намерих проект '{query}'.\n\n"
                f"Налични проекти: {names}\n\n"
                "Изберете с номер (напр. **1**) или въведете пълен път."}

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
