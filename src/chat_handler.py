"""Chat session handler — processes user messages via dual AI system.

Routes intents to appropriate actions: chat, generate, modify, export, lessons, evolve.
Uses AIProcessor (backed by AIRouter) for all AI operations.
Includes self-evolution support with 3-level change management (green/yellow/red).
Enforces strict JSON pipeline: converted files only for AI operations (Rule #0).
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import TYPE_CHECKING, Any

from src.ai_router import MODEL_CONTROLLER
from src.deadline import DeadlineExceeded, attempt_timeout, run_with_deadline
from src.prompt_safety import format_injection_warnings
from src.self_evolution import DISABLED_MESSAGE as EVOLUTION_DISABLED_MESSAGE
from src.self_evolution import is_enabled as evolution_enabled

if TYPE_CHECKING:
    from src.ai_processor import AIProcessor
    from src.file_manager import FileManager
    from src.knowledge_manager import KnowledgeManager
    from src.project_manager import ProjectManager
    from src.schedule_builder import ScheduleBuilder
    from src.self_evolution import SelfEvolution

logger = logging.getLogger(__name__)


def _срок_с_думи(секунди: int) -> str:
    """„10 мин" / „45 сек" — за съобщението към човека пред екрана."""
    return f"{секунди // 60} мин" if секунди >= 60 else f"{секунди} сек"

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
        "линеен", "нов график",
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


def _отпечатък(път: str) -> str:
    """Съдържанието на файла като ключ — за да не се чете два пъти.

    Тръжният пакет дава един и същи чертеж под две имена; сравнението по име
    не го хваща, а по съдържание — да.
    """
    import hashlib

    try:
        with open(път, "rb") as fh:
            return hashlib.md5(fh.read()).hexdigest()
    except OSError:
        return ""


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
        self.current_project_type: str = ""

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

        # Забележка: admin кодът НЕ минава оттук.  При `pending_changes`
        # функцията връща по-горе (`_handle_confirm_change`) и до този ред не
        # се стига, тоест `self.history` никога не вижда кода.  Маската за
        # UI слоя е в app.py.  (Тук имаше проверка, която беше мъртъв код —
        # посочено при одит 2026-07-23.)
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
    def _parsed_analysis(analysis: dict) -> dict:
        """Анализът като dict — независимо как моделът е форматирал отговора.

        ЖИВ ПРОГОН 2026-08-06 (Sonnet през OpenRouter): анализът се върна
        ограден с ```json … ``` и всяко място тук ползваше гол `json.loads`,
        който гърми на оградата.  Провалът беше ТИХ и събаряше наведнъж:
        project_type (→ празен, тоест и защитата „out_of_scope" мъртва),
        conflicts (моделът НАМЕРИ противоречие и то не стигна до човека),
        specifics, списъка с участъци и въпросника за последователността
        (проект с водопровод И канализация не питаше нищо).

        `parse_json_strict` маха оградата и, ако трябва, изкопава обекта от
        заобикалящ текст.  При невъзможност — празен dict, както досега.
        """
        raw = analysis.get("analysis", "")
        if isinstance(raw, dict):
            return raw
        if not isinstance(raw, str) or not raw.strip():
            return {}
        from src.json_contract import parse_json_strict
        parsed = parse_json_strict(raw)
        if parsed.data is None:
            logger.warning("Анализът не е валиден JSON: %s", parsed.error)
            return {}
        if parsed.recovered:
            logger.info("Анализът беше ограден с текст — JSON-ът е изкопан.")
        return parsed.data

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
        project_type = ChatHandler._parsed_analysis(analysis).get("project_type", "")

        if not project_type and project_context:
            project_type = project_context.get("type", "")

        return project_type

    def _required_files(self) -> list[str]:
        """Имената на задължителните документи (КСС) — за приоритет в промпта.

        При грешка връща празен списък: приоритизирането е оптимизация, не
        бива да чупи генерирането.
        """
        if not self.files:
            return []
        try:
            classification = self.files.classify_files(ai_processor=self.ai)
            return list(classification.get("required") or [])
        except Exception as exc:
            logger.debug("Не мога да определя приоритетни документи: %s", exc)
            return []

    def _try_package_generation(
        self, analysis, boq_index, *, num_teams: int, locations, progress,
        segments=None, tender=None,
    ):
        """Пакетният път (2026-08-07) — устройството на човешкия еталон.

        Моделът описва ОБЕКТА (кои участъци съществуват и коя част от кой ред
        на КСС им се пада), а технологичните вериги, зависимостите, WBS-ът,
        датите и критичният път идват от детерминистичния код.  Това е
        разликата между „списък дейности" и линеен график.

        Изисква индекс на КСС — без редове няма какво да се разпределя.  При
        какъвто и да е неуспех връща None и извикващият пада към досегашния
        път: нов път не бива да е причина за нула изход.  `PACKAGE_GENERATION=0`
        го изключва напълно.
        """
        if not boq_index or os.getenv("PACKAGE_GENERATION", "1") == "0":
            return None

        # ОПИТВА, ДОКАТО НЕ ИЗЛЕЗЕ ИЗНОСИМ ГРАФИК.
        #
        # Серията от 14.08.2026: 21 от 40 прогона са чисти, тоест един опит е
        # хвърляне на монета.  `tools/build_audit_package.py` отдавна опитва до
        # 10 пъти и НАДЕЖДНО вади чист график — а приложението, което ползва
        # потребителят, опитваше веднъж.  Оттам и усещането, че инструментът
        # работи, а продуктът не: разликата не беше в генерацията, а в това кой
        # повтаря опита — ние или човекът пред екрана.
        #
        # Повтаря се САМО неизносим резултат.  График, който е готов, се връща
        # какъвто е — това не е търсене на по-хубав изход, а довършване на
        # прекъснат опит.
        опити = max(int(os.getenv("GENERATION_ATTEMPTS", "4")), 1)
        срок = attempt_timeout()
        последен = None
        обратна_връзка = ""      # какво се провали в ПРЕДИШНИЯ опит
        for опит in range(1, опити + 1):
            ако_повторен = f" (опит {опит} от {опити})" if опит > 1 else ""
            progress(f"Генерирам по физически участъци (пакети){ако_повторен}...")
            try:
                # ТВЪРД СРОК НА ЕДИН ОПИТ (жив прогон 14.08.2026): опитът виси с
                # часове при увиснал доставчик и заключва интерфейса.  Таванът на
                # едно HTTP извикване не е таван на опита — един опит е десетки
                # извиквания, а стрийминг, който капе по токен, не гърми никога.
                result = run_with_deadline(
                    lambda напредък, _вр=обратна_връзка: self.ai.generate_schedule_packaged(
                        analysis, boq_index,
                        num_teams=max(int(num_teams or 1), 1),
                        locations=locations, segments=segments,
                        progress_callback=напредък, feedback=_вр,
                        tender=tender),
                    срок, progress=progress, name=f"пакетна генерация, опит {опит}")
            except DeadlineExceeded:
                logger.warning("Пакетната генерация, опит %d: изтече срокът от "
                               "%d сек", опит, срок)
                края = "прекъсвам и повтарям." if опит < опити else "прекъсвам."
                progress(f"Опит {опит}: няма отговор {_срок_с_думи(срок)} — {края}")
                continue
            except Exception as exc:                   # noqa: BLE001
                logger.warning("Пакетната генерация, опит %d: %s", опит, exc,
                               exc_info=True)
                continue

            if result.get("status") == "error":
                logger.warning("Пакетната генерация, опит %d: %s", опит,
                               result.get("message"))
                continue

            последен = result
            # Липсващ флаг НЕ значи неуспех: съдим по статуса, иначе готов
            # график се преповтаря четири пъти и се плаща четири пъти.
            износим = result.get("exportable",
                                 result.get("status") in ("ok", "approved"))
            if износим:
                if опит > 1:
                    progress(f"Готов график от {опит}-и опит.")
                return result
            причина = self._why_not_exportable(result)
            logger.info("Опит %d не даде износим график (%s) — повтарям",
                        опит, причина)
            # СЛЕДВАЩИЯТ ОПИТ НАУЧАВА ОТ ТОЗИ (жив прогон 14.08.2026): четири
            # слепи опита дадоха 36, 28, 11 и 33 участъка за един и същ обект.
            # Всеки започваше от нулата и наново избираше едрината на
            # разделянето, вместо да поправи каквото не е достигнало.
            обратна_връзка = self._feedback_for_next_attempt(result, опит)
            if опит < опити:
                # Причината е на екрана, не само в лога: иначе човекът гледа
                # „генерирам..." по няколко минути и не знае какво не достига.
                progress(f"Опит {опит}: {причина} — повтарям.")

        # Нито един опит не е износим: връща се последният, за да види човекът
        # КАКВО пречи, вместо да получи нищо.
        return последен

    @staticmethod
    def _why_not_exportable(result: dict) -> str:
        """Едно изречение защо графикът не е готов — за човека пред екрана."""
        причини: list[str] = []
        цитати = (result.get("citation_report") or {}).get("uncovered") or []
        if цитати:
            причини.append(f"{len(цитати)} непокрити реда от КСС")
        запазване = result.get("conservation") or {}
        if запазване and not запазване.get("ok"):
            превишени = len(запазване.get("over") or [])
            недостиг = len(запазване.get("short") or [])
            липсващи = len(запазване.get("missing") or [])
            ако = [f"{n} {дума}" for n, дума in
                   ((превишени, "превишени"), (недостиг, "недостигащи"),
                    (липсващи, "неразпределени")) if n]
            причини.append("количества: " + ", ".join(ако) if ако
                           else "количествата не се връзват с КСС")
        блокери = result.get("export_blockers") or result.get("blockers") or []
        if блокери and not причини:
            причини.append(str(блокери[0])[:80])
        return "; ".join(причини) or str(result.get("status") or "не мина проверката")

    @staticmethod
    def _feedback_for_next_attempt(result: dict, опит: int) -> str:
        """Какво да знае СЛЕДВАЩИЯТ опит за провала на този — с редовете.

        Разликата с `_why_not_exportable` е адресатът: онова е изречение за
        човека, това е указание за модела.  Затова тук има РЕФЕРЕНЦИИ (кой ред
        е останал непокрит, кой е разпределен двойно) — те са проверими и
        насочват към поправка, вместо към ново хвърляне на зара.
        """
        редове: list[str] = []
        участъци = len(result.get("packages") or [])
        if участъци:
            редове.append(f"Опит {опит} раздели обекта на {участъци} участъка.")

        диагноза = result.get("partition_diagnosis") or {}
        редове.extend(f"Разделянето: {s}" for s in (диагноза.get("signals") or []))

        непокрити = (result.get("citation_report") or {}).get("uncovered") or []
        if непокрити:
            редове.append(
                f"{len(непокрити)} реда от КСС останаха БЕЗ покриваща дейност — "
                f"напр. {', '.join(map(str, непокрити[:5]))}. Тези количества "
                "трябва да попаднат в участък, чиято работа ги изпълнява.")

        запазване = result.get("conservation") or {}
        if запазване and not запазване.get("ok"):
            for ключ, дума in (("missing", "неразпределени"),
                               ("over", "разпределени в повече от нужното"),
                               ("short", "разпределени под количеството в КСС")):
                редове_кл = запазване.get(ключ) or []
                if редове_кл:
                    примери = list(редове_кл)[:5]
                    редове.append(
                        f"{len(редове_кл)} реда {дума} — напр. "
                        f"{', '.join(map(str, примери))}.")
        return "\n".join(редове)

    def _boq_index(self) -> list:
        """Индексът с количествени редове — за цитиране в промпта.

        При грешка връща празен списък: цитирането е подобрение, не бива да
        чупи генерирането.
        """
        if not self.files or not self.files.base_path:
            return []
        try:
            from src.provenance import build_quantity_index
            return build_quantity_index(self.files.base_path)
        except Exception as exc:
            logger.debug("Не мога да индексирам количествата: %s", exc)
            return []

    @staticmethod
    def _with_drawing_counts(boq: list, nodes: list, notes: list[str]) -> list:
        """Долива преброените от чертежа точкови позиции към количествата.

        ЧЕРТЕЖЪТ ДОПЪЛВА, ТАБЛИЦАТА НАДДЕЛЯВА.  Шахтите, оттоците и сградните
        отклонения влизат само там, където таблиците мълчат — договорното
        количество е онова, което възложителят е купил, а не онова, което сме
        преброили.  Какво е добавено и какво е премълчано се КАЗВА: иначе
        „преброено, но неизползвано" и „непреброено" изглеждат еднакво отвън.
        """
        if not nodes:
            return boq
        try:
            from src.situation_reader import (merge_node_rows,
                                              nodes_as_quantity_rows)
            редове, бележки = merge_node_rows(boq, nodes_as_quantity_rows(nodes))
            notes.extend(бележки)
            return редове
        except Exception as exc:      # noqa: BLE001
            logger.warning("Точковите позиции не се сляха с количествата: %s",
                           exc)
            return boq

    def _verify_quantities(self, gen_result: dict) -> dict:
        """Свери количествата в графика срещу редовете в КСС (BACKLOG т.3).

        Отговаря на въпроса „откъде е това число".  Каквото не може да се
        свери, се маркира като несверено — по-полезно от фалшива увереност.
        """
        if not self.files or not self.files.base_path:
            return {}
        try:
            from src.ai_processor import AIProcessor
            from src.provenance import (
                annotate_schedule, build_quantity_index, verify_citations,
            )

            tasks = AIProcessor._tasks_from(gen_result.get("schedule"))
            if not tasks:
                return {}
            index = build_quantity_index(self.files.base_path)
            if not index:
                return {"no_index": True}

            # Етап 2: ако моделът е цитирал редове, проверяваме ЦИТАТИТЕ.
            # Ако не е — падаме към сверяване по сходство (етап 1).
            if any(task.get("source_ref") for task in tasks):
                return verify_citations(tasks, index)
            return annotate_schedule(tasks, index)
        except Exception as exc:
            logger.warning("Сверяването на количествата се провали: %s", exc)
            return {}

    @staticmethod
    def _format_quantity_provenance(report: dict) -> list[str]:
        """Покажи колко от количествата са сверени срещу документ."""
        if not report:
            return []
        if report.get("no_index"):
            return [
                "\n📄 **Произход на количествата: непроверен** — няма таблични "
                "документи (Excel/CSV) за сверяване. От свободен текст не може "
                "да се посочи ред и клетка."
            ]

        total = report.get("total", 0)
        if not total:
            return []

        verified = report.get("verified", 0)
        human = report.get("human", 0)
        lines = [
            f"\n📄 **Количества, сверени срещу КСС:** {verified} от {total}"
        ]
        if human:
            lines.append(f"  ({human} ръчно въведени от човек — не се сверяват)")

        # Етап 2: моделът цитира ред, кодът проверява цитата.  Разликата
        # между „няма цитат" и „цитатът не съвпада" е съществена —
        # несъвпадащият изглежда като доказателство, а не е.
        if "problems" in report:
            mismatch = report.get("mismatch", 0)
            unknown = report.get("unknown_ref", 0)
            uncited = report.get("uncited", 0)

            if mismatch:
                lines.append(
                    f"\n🛑 **{mismatch} цитата НЕ съвпадат с посочения ред** — "
                    "числото изглежда подкрепено с документ, но не е:"
                )
                for item in [p for p in report["problems"]
                             if p["status"] == "mismatch"][:5]:
                    lines.append(
                        f"  - {item['id']} {str(item['name'])[:34]}: "
                        f"графикът казва {item['quantity']}, "
                        f"ред {item['ref']} казва {item['actual']}"
                    )
            if unknown:
                lines.append(
                    f"\n⚠️ **{unknown} цитата сочат несъществуващ ред** "
                    "(измислен източник):"
                )
                for item in [p for p in report["problems"]
                             if p["status"] == "unknown_ref"][:3]:
                    lines.append(f"  - {item['id']}: '{item['ref']}'")
            if uncited:
                lines.append(
                    f"\n📌 {uncited} количества без посочен източник — идват от "
                    "AI, не от документ."
                )
            return lines

        unverified = report.get("details") or []
        if unverified:
            lines.append(
                f"  {len(unverified)} НЕ съвпадат с нито един ред в документите:"
            )
            for item in unverified[:5]:
                closest = item.get("closest")
                hint = f" (най-близко: {closest[:40]})" if closest else ""
                lines.append(
                    f"  - {item.get('id')} {str(item.get('name'))[:36]} "
                    f"= {item.get('quantity')}{hint}"
                )
            if len(unverified) > 5:
                lines.append(f"  ... и още {len(unverified) - 5}")
            lines.append("  Тези числа идват от AI, не от документ. Проверете ги.")
        return lines

    @staticmethod
    def _format_truncation_warning(analysis: dict) -> list[str]:
        """Кажи ясно, ако документи НЕ са стигнали до AI-я.

        BACKLOG т.2: отрязването беше тихо — добавяше се само
        „[... съдържанието е съкратено ...]", без да се знае кое е отпаднало.
        """
        truncation = analysis.get("truncation") or {}
        if not truncation.get("truncated"):
            return []

        dropped = truncation.get("dropped_documents") or []
        lines = [
            f"\n⚠️ **Не всички документи стигнаха до анализа** "
            f"({truncation.get('chars', 0):,} от {truncation.get('total_chars', 0):,} знака):"
        ]
        for name in dropped[:6]:
            lines.append(f"  - {name}")
        if len(dropped) > 6:
            lines.append(f"  ... и още {len(dropped) - 6}")
        lines.append(
            "  Задължителните документи (КСС) влизат първи. Ако нещо важно е "
            "отпаднало, извадете ненужните файлове от папката."
        )
        return lines

    @staticmethod
    def _format_generation_repairs(gen_result: dict) -> list[str]:
        """Покажи какво КОДЪТ е поправил след генерацията.

        И двата ремонта (допокриване на непокрити КСС редове, разделяне на
        застъпени бригади) променят резултата на модела.  Тиха промяна е
        недопустима: инженерът трябва да види, че задача е преместена във
        времето, за да прецени дали така е приемливо на терен.
        """
        lines: list[str] = []

        # КАКВО е разделянето на обекта, а не само колко участъка са станали:
        # „36 участъка" и „11 участъка" изглеждат еднакво на екрана, а второто
        # е групиране по диаметър (жив прогон 14.08.2026).
        разделяне = gen_result.get("partition_diagnosis") or {}
        if разделяне.get("packages"):
            от_чертежа = разделяне.get("drawn_segments") or 0
            потвърдено = (f", {от_чертежа} отсечки от чертежа" if от_чертежа
                          else ", без отсечки от чертеж")
            if разделяне.get("ok"):
                lines.append(
                    f"\n🗺️ **Разделяне на обекта:** {разделяне['packages']} "
                    f"участъка ({разделяне.get('linear_packages', 0)} мрежови"
                    f"{потвърдено})."
                )
            else:
                причини = "; ".join(разделяне.get("signals") or [])
                lines.append(
                    f"\n🗺️ **Разделянето не е по трасета:** "
                    f"{разделяне['packages']} участъка{потвърдено} — {причини}. "
                    "Графикът е верен по количества, но едрината му не отговаря "
                    "на обекта."
                )

        rounds = gen_result.get("repair_rounds") or 0
        if rounds:
            lines.append(
                f"\n🔁 **Допокриване:** {rounds} допълнителн(и) опит(а) за "
                f"позиции от КСС, останали без своя дейност в първия проход."
            )

        unproductive = [p for p in (gen_result.get("parts") or [])
                        if p.get("unproductive")]
        if unproductive:
            lines.append(
                f"\n🚫 **{len(unproductive)} допълнителни опита не доказаха нито "
                f"един ред** — задачите им НЕ са добавени, за да не се брои една "
                f"и съща работа два пъти. Липсващите позиции остават блокер."
            )

        nets = (gen_result.get("network_links") or {}).get("added") or []
        if nets:
            lines.append(
                f"\n🌊 **Ред на мрежите:** {len(nets)} връзки — вода → канал → "
                f"пътни (Правило #74/#75). Частите вече не тръгват всички в ден 1."
            )

        repair = gen_result.get("spatial_repair") or {}
        added = repair.get("added_links") or []
        unresolved = repair.get("unresolved") or []
        if added:
            lines.append(
                f"\n🔀 **Пространствен ремонт:** {len(added)} застъпвания на "
                f"бригади са разделени във времето (добавена е връзка "
                f"край→начало). Проверете дали така е приемливо:"
            )
            for link in added[:5]:
                lines.append(
                    f"  - {link['predecessor']} → {link['successor']} по "
                    f"'{link.get('alignment', '?')}' "
                    f"({link.get('overlap_m', 0):.0f}м застъпване)"
                )
            if len(added) > 5:
                lines.append(f"  ... и още {len(added) - 5}")
        if unresolved:
            lines.append(
                f"\n⚠️ **{len(unresolved)} застъпвания НЕ можаха да се разделят "
                f"автоматично** (връзката би затворила цикъл или е между "
                f"обобщаваща задача и подзадача) — остават като грешка."
            )
        return lines

    @staticmethod
    def _format_validation_report(gen_result: dict) -> list[str]:
        """Покажи резултата от детерминистичната валидация на графика.

        Тиха валидация е равносилна на липсваща: ако графикът има кръгова
        зависимост или задача, започваща преди края на предшественика си,
        потребителят трябва да го види ПРЕДИ да го изпрати на възложителя.
        """
        validation = gen_result.get("validation") or {}
        if not validation:
            return []

        if not validation.get("checked"):
            return [
                "\n⚠️ **Детерминистичната проверка не можа да се изпълни** — "
                "не бяха намерени задачи в графика."
            ]

        errors = validation.get("errors", [])
        warnings = validation.get("warnings", [])

        spatial = validation.get("spatial") or {}
        covered = spatial.get("covered", 0)
        total = spatial.get("total", 0)

        # Пространственото покритие се казва ВИНАГИ — иначе не се разбира дали
        # проверката за сблъсък на бригади изобщо е имала данни да работи.
        if covered:
            alignments = spatial.get("alignments") or []
            spatial_note = (
                f"\n📍 **Пространствена проверка:** {covered} от {total} задачи "
                f"с пикетаж"
                + (f" по {len(alignments)} оси" if alignments else "")
            )
        else:
            spatial_note = (
                f"\n📍 **Пространствена проверка: пропусната** — нито една задача "
                "няма пикетаж. Сблъсък на бригади и дължина на открит изкоп "
                "НЕ са проверени."
            )

        if not errors and not warnings:
            return [
                f"\n✅ **Детерминистична проверка: чиста** "
                f"({validation.get('task_count', 0)} задачи — зависимости, "
                f"дати, продължителности, екипи)",
                spatial_note,
            ]

        lines: list[str] = []
        if errors:
            lines.append(
                f"\n🛑 **Графикът НЕ минава детерминистичната проверка — "
                f"{len(errors)} грешки:**"
            )
            for err in errors[:6]:
                lines.append(f"  - {err}")
            if len(errors) > 6:
                lines.append(f"  ... и още {len(errors) - 6}")
            lines.append(
                "  **Не изпращайте този график на възложителя, преди да се "
                "отстранят.** Грешките са в логиката (зависимости, дати), "
                "не в оформлението."
            )

        if warnings:
            lines.append(f"\n⚠️ **{len(warnings)} предупреждения:**")
            for warn in warnings[:4]:
                lines.append(f"  - {warn}")
            if len(warnings) > 4:
                lines.append(f"  ... и още {len(warnings) - 4}")

        lines.append(spatial_note)
        return lines

    @staticmethod
    def _format_duration_report(gen_result: dict) -> list[str]:
        """Направи видимо какво е преизчислил детерминистичният калкулатор.

        Без този отчет замяната на продължителностите е тиха — потребителят
        вижда различни числа от тези, които AI-ят е обявил, без обяснение.

        Args:
            gen_result: Резултатът от AIProcessor.generate_schedule.

        Returns:
            Списък редове за чат отговора (празен, ако стъпката е пропусната).
        """
        report = gen_result.get("duration_report") or {}
        if not report.get("applied"):
            return []

        summary = report.get("summary", {})
        recomputed = summary.get("recomputed", 0)
        skipped = summary.get("skipped", 0)
        unresolved = summary.get("unresolved", 0)
        old_total = summary.get("old_total_duration", 0)
        new_total = summary.get("new_total_duration", 0)

        # Одит v6, точка 6: досега `if not recomputed: return []` излизаше
        # ПРЕДИ блока за недоказани продължителности.  Затова при финалния
        # отчет (нищо преизчислено, но 2 недоказани) човекът не виждаше
        # нищо.  Сега празно се връща само ако няма НИТО преизчислени, НИТО
        # недоказани.
        if not recomputed and not unresolved:
            return []

        lines: list[str] = []
        if recomputed:
            lines.append(
                f"\n📐 **Продължителности, преизчислени от productivities.json:** "
                f"{recomputed} задачи (пропуснати {skipped} — няма норма)"
            )
            if old_total != new_total:
                lines.append(
                    f"  Обща продължителност: {old_total}д → **{new_total}д** "
                    f"(AI-ят беше сметнал {old_total}д)"
                )
            for change in report.get("changes", [])[:8]:
                lines.append(
                    f"  - {change['id']} {change['name'][:40]}: "
                    f"{change['old']}д → {change['new']}д ({change['reason']})"
                )
            extra = len(report.get("changes", [])) - 8
            if extra > 0:
                lines.append(f"  ... и още {extra}")
            for warning in report.get("warnings", []):
                lines.append(f"  ⚠️ {warning}")

        lines.extend(ChatHandler._format_unresolved_durations(report))
        return lines

    # Човешки текст за машинните кодове от duration_calculator.
    _CODE_LABELS = {
        "MISSING_MATERIAL": "материалът не е указан (PE/CI/PVC)",
        "MISSING_DN": "липсва диаметър DN",
        "MISSING_LENGTH": "липсва дължина в метри",
        "NO_PRODUCTIVITY_RULE": "няма норма за този DN и материал",
        "COUNT_NO_RATE": "бройки без известна норма",
        "NOT_PARAMETRIC": "не е тръбна дейност (изкоп, извозване, настилки)",
    }

    @staticmethod
    def _format_unresolved_durations(report: dict) -> list[str]:
        """Кажи ясно кои продължителности НЕ са доказани.

        Одит 2026-07-23: изчислената и предположената стойност бяха в едно
        поле и потребителят нямаше как да разбере кои числа в графика му са
        сметнати по норма и кои са предположение на езиков модел.
        """
        summary = report.get("summary", {})
        unresolved = summary.get("unresolved", 0)
        if not unresolved:
            return []

        by_code = summary.get("by_code", {})
        lines = [
            f"\n📌 **{unresolved} задачи с НЕДОКАЗАНА продължителност** "
            "— стойността идва от AI, не от нормите:"
        ]
        for code, count in sorted(by_code.items(), key=lambda kv: -kv[1]):
            if code == "NOT_PARAMETRIC":
                continue  # изкоп/настилки нямат норми — очаквано, не е дефект
            label = ChatHandler._CODE_LABELS.get(code, code)
            lines.append(f"  - {count}× {label}")

        not_parametric = by_code.get("NOT_PARAMETRIC", 0)
        if not_parametric:
            lines.append(
                f"  ({not_parametric} дейности без норма в конфига — "
                "изкоп, извозване, настилки — това е очаквано)"
            )
        lines.append(
            "  Провери тези стойности спрямо КСС, преди да ползваш графика."
        )
        return lines

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
        # BACKLOG т.2: задължителните документи (КСС) излизат ПЪРВИ, за да не
        # отпаднат при отрязване само защото са по-назад по азбучен ред.
        all_text = self.files.get_all_text(priority=self._required_files()) if self.files else ""
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
        conflicts: list[str] = self._parsed_analysis(analysis).get("conflicts", []) or []

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
        seq_state = self._start_sequence_questionnaire(analysis, project_context)
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
        situation_locations, situation_segments, situation_nodes = \
            self._read_situation_files()

        # Step 2: Generate schedule with verification
        self._progress(0.25, "Генериране на график...")

        project_type = self._extract_project_type(analysis, project_context)

        # C2 fix: refuse to generate when classifier returns out_of_scope
        if project_type == "out_of_scope":
            specifics = self._parsed_analysis(analysis).get("specifics", "")
            reason = f"\n\n**Причина:** {specifics}" if specifics else ""
            return {
                "response": (
                    "⛔ **Проектът е извън обхвата на генератора.**"
                    f"{reason}\n\n"
                    "Системата поддържа: водоснабдяване, канализация, КПС, "
                    "довеждащ водопровод и инженеринг проекти за ВиК инфраструктура. "
                    "HDD/хоризонтален сондаж СЕ поддържа. При microtunneling, "
                    "pipe bursting или нестандартни проекти моля използвайте "
                    "ръчно въвеждане."
                ),
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "generate_schedule",
                "model_used": analysis.get("model", "none"),
            }

        progress_messages: list[str] = []

        # Progress steps: generate=25%, verify cycles up to 90%
        _cycle_pcts = [0.45, 0.60, 0.75, 0.85, 0.90, 0.92]
        _cycle_idx = [0]

        def _progress(msg: str) -> None:
            progress_messages.append(msg)
            pct = _cycle_pcts[min(_cycle_idx[0], len(_cycle_pcts) - 1)]
            _cycle_idx[0] += 1
            self._progress(pct, msg)

        # STAGING (проба 2026-07-31): голям проект (КСС с няколко части —
        # водопровод/канализация/пътна) не се събира в едно AI извикване дори
        # на 8192 токена → отрязан JSON.  Ако количествата обхващат ≥2 листа,
        # генерираме по части и сливаме.  Иначе — обикновеният път.
        _boq = self._with_drawing_counts(self._boq_index(), situation_nodes,
                                         progress_messages)
        # Броим (документ, лист) — одит v13 #6: два файла с лист „КСС" не бива
        # да се считат за една част (иначе staging не се задейства и голям
        # проект пак се отрязва).
        _sheets = {(getattr(getattr(r, "source", None), "document", ""),
                    getattr(getattr(r, "source", None), "sheet", ""))
                   for r in _boq if getattr(r, "quantity", None) is not None}
        # 2026-08: прагът беше „≥2 листа".  Реалният търг е ЕДИН лист с 28
        # позиции — минаваше по правия път, където няма нито допокриване на
        # непокрити редове, нито пространствен ремонт → 6 от 28 позиции и
        # застъпени екипи.  Сега всеки график С КОЛИЧЕСТВА минава през staging;
        # при един лист това е една част, плюс двата ремонта.
        _packaged = self._try_package_generation(
            analysis, _boq, num_teams=1,
            locations=situation_locations or None,
            segments=situation_segments or None, progress=_progress)

        if _packaged is not None:
            gen_result = _packaged
        else:
            # Резервните пътища минават през СЪЩИЯ твърд срок: увисналият
            # доставчик не пита по кой път е тръгнала генерацията.
            _срок = attempt_timeout()
            _последователности = (project_context.get("sequence_constraints")
                                  if project_context else None)
            if [s for s in _sheets if s[1]]:
                _progress(f"Генерирам по части (staging) — {len(_sheets)} част(и) от КСС...")

                def _работа(напредък, *, _seq=_последователности):
                    return self.ai.generate_schedule_staged(
                        analysis, project_type, напредък,
                        all_text=all_text, boq_index=_boq,
                        extra_locations=situation_locations or None,
                        sequence_constraints=_seq,
                    )
            else:
                def _работа(напредък, *, _seq=_последователности):
                    return self.ai.generate_schedule(
                        analysis, project_type, напредък,
                        all_text=all_text,
                        extra_locations=situation_locations or None,
                        sequence_constraints=_seq,
                        boq_index=_boq,
                    )

            try:
                gen_result = run_with_deadline(
                    _работа, _срок, progress=_progress, name="генерация")
            except DeadlineExceeded:
                logger.warning("Генерацията изтече срока от %d сек", _срок)
                _обяснение = (
                    f"генерацията не завърши за {_срок_с_думи(_срок)} и беше "
                    "прекъсната — доставчикът не отговаря. Опитайте отново.")
                gen_result = {"status": "error", "error": _обяснение,
                              "message": _обяснение}

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

        if status == "invalid":
            # GATE (одит 2026-07-23): кодът отхвърли графика.  Никакво
            # „одобрен" — AI статусът вече не е авторитетен.
            response_parts.append(
                "\n🛑 **Графикът е ОТХВЪРЛЕН от проверката.** "
                f"(AI го беше отбелязал като '{gen_result.get('ai_status', '?')}', "
                f"${cost:.4f})\n"
                "Не се записва като текущ график и не може да се експортира."
            )
        elif status == "approved":
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

        response_parts.extend(self._format_truncation_warning(analysis))
        response_parts.extend(
            self._format_quantity_provenance(self._verify_quantities(gen_result))
        )
        response_parts.extend(self._format_generation_repairs(gen_result))
        response_parts.extend(self._format_duration_report(gen_result))
        response_parts.extend(self._format_validation_report(gen_result))
        response_parts.extend(
            format_injection_warnings(gen_result.get("injection_findings") or [])
        )

        # GATE (одит 2026-07-23; разширен v6, точка 1): само график с ПРИЕТ
        # статус става текущ и се записва.  Досега условието беше
        # `status != "invalid"` — blacklist, който пускаше `error`/`stopped`
        # (сринат или спрян контрольор) да станат работен график.  Сега е
        # allowlist: approved / needs_human_review.  Всичко друго се пази само
        # като неуспешна ревизия — за диагностика, не за употреба.
        from src.ai_processor import AIProcessor
        _valid = status in AIProcessor.ACCEPTED_STATUSES
        if _valid:
            self.current_schedule = gen_result.get("schedule")
        else:
            self.rejected_schedule = gen_result.get("schedule")
        self.correction_history = history
        self.current_project_type = project_type

        # Save schedule to project manager
        if _valid and self.project_mgr and self.project_mgr.current_project:
            pid = self.project_mgr.current_project.get("id")
            if pid:
                self.project_mgr.save_progress(pid, {
                    "status": "schedule_generated",
                    "last_schedule": self.current_schedule,
                })

        return {
            "response": "\n".join(response_parts),
            "schedule_updated": _valid and status in ("approved", "needs_human_review"),
            "schedule_data": self.current_schedule,
            "validation": gen_result.get("validation"),
            "export": {"exportable": gen_result.get("exportable"),
                       "export_blockers": gen_result.get("export_blockers"),
                       "export_policy": gen_result.get("export_policy")},
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
            result.get("content", ""), rules, max_cycles=2,
            knowledge_prompt=system_prompt,
            progress_callback=_mod_progress,
            project_type=self.current_project_type,
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

        # ------------------------------------------------------------------
        # СЪЩИТЕ ЗАЩИТИ КАТО ПРИ ГЕНЕРИРАНЕ (одит 2026-07-23, точка 4).
        #
        # Дотук този път правеше САМО структурен diff (`validate_modification`),
        # показваше предупреждения и записваше графика ВИНАГИ.  Тоест дори
        # генериращият pipeline да е поправен, една команда „намали срока с
        # 20 дни" можеше да върне график с duration=-5, самозависимост или
        # кръгова зависимост — и той ставаше текущият.
        #
        # Затова тук: преизчисляване → пълна валидация → gate.
        # ------------------------------------------------------------------
        from src.ai_processor import AIProcessor

        modified_tasks = AIProcessor._tasks_from(new_schedule)
        before_tasks = AIProcessor._tasks_from(self.current_schedule)

        # INPUT-LOCK И ЗА МОДИФИКАЦИЯТА (одит v8, точка 1).
        #
        # Досега заключването живееше само в correction cycle-а при генериране.
        # Тук AI връщаше цял график и можеше да промени екипи/пикетаж/входове/
        # зависимости на задачи, които човекът НЕ е поискал — прилагаха се и
        # оставаха strict-exportable.  Сега непоисканите задачи минават през
        # същото заключване; поисканите (посочените в съобщението) са свободни.
        mod_lock = AIProcessor.enforce_modification_lock(
            modified_tasks, before_tasks, message)
        # TRUST BOUNDARY (одит v12): изтрий AI-подадени provenance статуси ПРЕДИ
        # човешкото маркиране — иначе AI може да си сложи фалшив human_override и
        # да заобиколи проверката срещу КСС.  Легитимният произход се задава
        # само от mark_human_overrides/verify_citations (сървърни операции).
        try:
            from src.provenance import strip_ai_provenance
            strip_ai_provenance(modified_tasks)
        except Exception as exc:
            logger.debug("strip_ai_provenance (модификация) се провали: %s", exc)
        if isinstance(new_schedule, dict):
            new_schedule = {**new_schedule, "tasks": modified_tasks}
        else:
            new_schedule = modified_tasks

        duration_report: dict = {}
        if modified_tasks and self.builder:
            recomputed = self.builder.recompute_durations(modified_tasks)
            modified_tasks = recomputed["schedule"]
            duration_report = {
                "applied": True,
                "changes": recomputed["changes"],
                "skipped": recomputed["skipped"],
                "warnings": recomputed["warnings"],
                "summary": recomputed["summary"],
            }
            if isinstance(new_schedule, dict):
                new_schedule = {**new_schedule, "tasks": modified_tasks}
            else:
                new_schedule = modified_tasks

        # BACKLOG т.3 етап 3: количествата, които тази ръчна промяна е сменила,
        # вече идват от ЧОВЕК, не от AI или документ.  Произходът го отразява.
        try:
            from src.provenance import mark_human_overrides
            mark_human_overrides(before_tasks, modified_tasks, message)
        except Exception as exc:
            logger.debug("Маркирането на ръчни промени се провали: %s", exc)

        validation = AIProcessor._validate_final_schedule(new_schedule)

        # GATE на модификацията (одит v5 т.6 + v6 т.1): прилага се само при
        # валиден график И приет статус.  Досега `_valid` беше само
        # validation.valid — сринат/спрян контрольор (status error/stopped)
        # пак прилагаше промяната.  Сега error/stopped я отхвърлят.
        # Одит v7 т.4 + v8 т.4/6: произходът на количествата влиза в gate-а,
        # с `checked` за fail-closed при strict (липсващ индекс/грешка).
        # Смята се ПРЕДИ статуса — за да може непокритие да СВАЛИ статуса, а не
        # само да е blocker (одит v19: provisional игнорираше blockers).
        _citation_report: dict = {"checked": False, "reason": "no_boq_index"}
        _cov_problem = False
        try:
            from src.provenance import verify_citations, analyze_boq_coverage
            _idx = self._boq_index()
            if _idx:
                _citation_report = {**verify_citations(modified_tasks, _idx),
                                    "checked": True}
                # Одит v18 P0: coverage gate ЛИПСВАШЕ в модификацията — премахване
                # на задача оставяше КСС ред непокрит, а strict пак пускаше.  Сега
                # и тук се проверяват непокрити/дублирани/двусмислени позиции.
                _cov = analyze_boq_coverage(modified_tasks, _idx)
                _citation_report["uncovered"] = _cov["uncovered"]
                _citation_report["over_covered"] = sorted(_cov["over_covered"])
                _citation_report["ambiguous"] = _cov.get("ambiguous", [])
                _citation_report["uncited_production"] = _cov.get("uncited_production", [])
                _cov_problem = bool(_cov["uncovered"] or _cov["over_covered"]
                                    or _cov.get("ambiguous")
                                    or _cov.get("uncited_production"))
        except Exception as exc:
            logger.warning("verify_citations при модификация се провали: %s", exc)
            _citation_report = {"checked": False, "reason": "exception"}
        # Одит v22 P0: „не можах да проверя" = „не е доказано" — БЕЗ изключения.
        # Всеки непроверен произход (липсващ КСС индекс ИЛИ exception при самата
        # проверка) сваля статуса до needs_human_review, за да НЕ е експортируем при
        # НИКОЯ policy (strict/provisional/lenient).  (v21 пропускаше липсващия индекс
        # като „degraded режим" — одитът правилно го отхвърли: модификация без
        # проверим произход е недоказана, точка.)
        if not _citation_report.get("checked"):
            _cov_problem = True

        _ctrl_status = verification.get("status", "approved")
        # Одит v8, точка 1: непоискана AI промяна на защитени полета/структура
        # → needs_human_review (не се експортира без човек, макар графикът да е
        # приложен с върнатите оригинални стойности).
        if mod_lock["unrequested_change"] and _ctrl_status == "approved":
            _ctrl_status = "needs_human_review"
        # Одит v19 P0: непокрита/дублирана/двусмислена BOQ позиция след промяна
        # СВАЛЯ статуса до needs_human_review — така графикът НЕ е експортируем при
        # НИКОЯ policy (provisional игнорира само blockers, не и статуса), както
        # твърди документацията („ambiguous е fail-closed навсякъде").
        if _cov_problem and _ctrl_status == "approved":
            _ctrl_status = "needs_human_review"
        _valid = bool(validation.get("valid")) and _ctrl_status in AIProcessor.ACCEPTED_STATUSES
        _mod_status = _ctrl_status if _valid else (
            "invalid" if not validation.get("valid") else _ctrl_status
        )
        export_decision = AIProcessor._export_decision(
            _mod_status, validation, {}, duration_report, _citation_report
        )

        if _valid:
            self.current_schedule = new_schedule
        else:
            self.rejected_schedule = new_schedule
            logger.error(
                "Модификацията е ОТХВЪРЛЕНА: %s",
                "; ".join(validation.get("errors", [])[:3]),
            )

        # Save updated schedule to project manager
        if _valid and self.project_mgr and self.project_mgr.current_project:
            pid = self.project_mgr.current_project.get("id")
            if pid:
                self.project_mgr.save_progress(pid, {
                    "status": "schedule_generated",
                    "last_schedule": self.current_schedule,
                })

        # Build response
        if _valid:
            # Одит v23: показвай АВТОРИТЕТНИЯ статус (_mod_status), не AI-контрольора —
            # иначе „Проверка: approved" подвежда, докато кодът е needs_human_review.
            if _mod_status == "needs_human_review":
                response_parts = [
                    "Промяната е приложена като РАБОТНА версия, но графикът НЕ е "
                    "готов за експорт — маркиран е за човешки преглед.",
                    f"Модел: {result.get('model', '?')}, Статус: needs_human_review",
                ]
            else:
                response_parts = [
                    "Промяната е приложена.",
                    f"Модел: {result.get('model', '?')}, Статус: approved",
                ]
            # Одит v23: обясни ЗАЩО чака преглед — липсващ произход или недоказано
            # покритие (иначе UI показваше „чиста проверка" без причина).
            if not _citation_report.get("checked"):
                _reason = _citation_report.get("reason")
                _txt = ("липсва КСС индекс — покритието на количествата не може да "
                        "се докаже" if _reason == "no_boq_index"
                        else "грешка при проверката на произхода"
                        if _reason == "exception" else "произходът не е проверен")
                response_parts.append(
                    f"\n⚠️ Произходът на количествата НЕ е проверен ({_txt}); "
                    "затова графикът чака човешки преглед и няма да се експортира.")
            elif _cov_problem:
                _u = len(_citation_report.get("uncovered") or [])
                _a = len(_citation_report.get("ambiguous") or [])
                _o = len(_citation_report.get("over_covered") or [])
                response_parts.append(
                    f"\n⚠️ Недоказано покритие след промяната: непокрити={_u}, "
                    f"неопределими={_a}, дублирани={_o} — човешки преглед, без експорт.")
            # Одит v8, точка 1: ако AI е пипнал непоискани задачи, казваме го
            # ясно и обясняваме, че защитените полета са върнати и че графикът
            # чака човешки преглед (не се експортира без него).
            if mod_lock["unrequested_change"]:
                touched = sorted(set(
                    [r["id"] for r in mod_lock["reverted"]]
                    + mod_lock["dependency_changes"]
                    + mod_lock["added"] + mod_lock["removed"]
                ))
                response_parts.append(
                    "\n⚠️ Освен поисканото, AI промени и НЕПОИСКАНИ задачи "
                    f"({', '.join(map(str, touched))}). Защитените полета "
                    "(екип, пикетаж, входове, зависимости) са върнати към "
                    "оригинала; графикът е маркиран за човешки преглед и няма "
                    "да се експортира без него."
                )
        else:
            response_parts = [
                "🛑 **Промяната е ОТХВЪРЛЕНА** — резултатът не минава "
                "детерминистичната проверка.",
                "Предишният график остава непроменен.",
            ]
        if validation_notes:
            response_parts.append("")
            response_parts.append("**Локална проверка:**")
            response_parts.extend(validation_notes)

        response_parts.extend(self._format_duration_report({"duration_report": duration_report}))
        response_parts.extend(self._format_validation_report({"validation": validation}))

        return {
            "response": "\n".join(response_parts),
            "schedule_updated": _valid,
            "schedule_data": self.current_schedule,
            "validation": validation,
            "export": {"exportable": export_decision["exportable"],
                       "export_blockers": export_decision["blockers"],
                       "export_policy": export_decision["policy"]},
            "duration_report": duration_report,
            # Одит v23: произходът се връща на UI/API — вкл. `reason` (напр.
            # no_boq_index), за да не изглежда „чиста проверка" без обяснение.
            "citation_report": _citation_report,
            "correction_info": {
                "status": _mod_status,
                "cycles": verification.get("cycles", 0),
                # Одит v23: пълният отчет и ВЪТРЕ в correction_info (не само top-level).
                "citation_report": _citation_report,
                "provenance_checked": bool(_citation_report.get("checked")),
                "provenance_reason": _citation_report.get("reason"),
            },
            "intent": "modify_schedule",
            "model_used": result.get("model", "unknown"),
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
        # Бариера ПРЕДИ всичко останало — включително преди четенето на
        # файлове в analyze_request/generate_changes, което се случваше без
        # никаква проверка и беше път за изнасяне на съдържание към AI.
        if not evolution_enabled():
            return {
                "response": EVOLUTION_DISABLED_MESSAGE,
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "evolve",
                "model_used": "none",
            }

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
        progress.append("Анализирам заявката... (Anthropic Opus 4.8)")
        plan = self.evolution.analyze_request(message)

        if plan.get("error"):
            return {
                "response": f"Грешка при анализ: {plan.get('description', 'неизвестна')}",
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "evolve",
                "model_used": MODEL_CONTROLLER,
            }

        level = plan.get("level", "red")

        # Step 2: Generate changes
        progress.append("Генерирам код... (Anthropic Opus 4.8)")
        changes = self.evolution.generate_changes(plan)

        if changes.get("error") and not changes.get("changes"):
            return {
                "response": f"Грешка при генериране: {changes.get('error', 'неизвестна')}",
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "evolve",
                "model_used": MODEL_CONTROLLER,
            }

        # Step 3: Preview
        preview = self.evolution.preview_changes(plan, changes)

        # Build response based on level
        progress_text = "\n".join(progress)

        if level == "green":
            # GREEN: Apply directly, no confirmation needed.
            # Бекъп СЕ ПРАВИ и тук — досега само red получаваше такъв, тоест
            # автоматично приложена промяна в knowledge/ нямаше път назад (P6).
            progress.append("Създавам backup...")
            backup = self.evolution.create_backup(plan.get("description", ""))
            if backup["success"]:
                progress.append(f"   Git commit: {backup['commit_hash'][:8]}")
            else:
                progress.append(f"   Backup неуспешен: {backup.get('error', '?')}")

            progress.append("Прилагам промени...")
            apply_result = self.evolution.apply_changes(changes, declared_level=level)

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
                    "model_used": MODEL_CONTROLLER,
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
                "model_used": MODEL_CONTROLLER,
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
                "model_used": MODEL_CONTROLLER,
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
                    "model_used": MODEL_CONTROLLER,
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
                "model_used": MODEL_CONTROLLER,
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
        parsed = ChatHandler._parsed_analysis(analysis)
        if not parsed:
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

    def _start_sequence_questionnaire(
        self, analysis: dict, project_context: dict | None = None
    ) -> dict | None:
        """Start the sequence questionnaire if the project has both water and sewer.

        Returns a pending_sequence state dict with the first question,
        or None if the questionnaire is not needed (e.g. water-only project).
        """
        parsed: dict = self._parsed_analysis(analysis)

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
        # "вк мрежа" / "в/к мрежа" / "в и к" / "вик" означава комбинирана В+К мрежа — задейства и двете
        _COMBINED_KEYWORDS = ["вк мрежа", "в/к мрежа", "вик мрежа", "вик инфраструктура", "в и к мрежа", "в и к инфраструктура", "вик", "в&к"]
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
            "project_context": project_context,
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

            # ОТГОВОРЪТ ВЛИЗА В `tender` (19.08.2026).  Дотогава той стигаше
            # само до промпта на модела, тоест на детерминистичния път — който
            # е по подразбиране — нямаше НИКАКЪВ ефект върху графика.  Виж
            # `tender_parameters.for_this_run`.
            tender = {**(state.get("tender") or {}),
                      "network_order": "В" if choice == "water_first" else "К"}
            return {**_base,
                "response": (
                    f"Разбрах: **{choice_label}**.\n\n"
                    "**Как се полага водопроводът?**\n"
                    "  **И** — на открит изкоп\n"
                    "  **С** — със сондаж (безизкопно)\n\n"
                    "Това мени срока чувствително: в еталонния график целият "
                    "водопровод е сондиран за 36 екипо-дни, а с открит изкоп "
                    "същата работа е седем пъти повече."
                ),
                "pending_sequence": {**state, "step": "q_laying",
                                     "tender": tender,
                                     "constraints": {"default": choice}},
            }

        # ── Q1б: открит изкоп или сондаж? ───────────────────────────────
        if step == "q_laying":
            if msg.startswith("С") or "СОНДА" in msg or "БЕЗИЗКОП" in msg:
                метод, метод_етикет = "hdd", "сондаж (безизкопно)"
            elif msg.startswith("И") or "ИЗКОП" in msg or "ОТКРИТ" in msg:
                метод, метод_етикет = "open", "открит изкоп"
            else:
                return {**_base, "response": (
                    "Моля, отговори с **И** (открит изкоп) или **С** (сондаж)."
                ), "pending_sequence": state}

            tender = {**(state.get("tender") or {}), "laying_method": метод}
            new_state = {**state, "tender": tender,
                         "_laying_label": метод_етикет}
            return self._ask_contract_days(new_state, метод_етикет)

        # ── Q1в: колко дни дава договорът за строителството? ────────────
        #
        # ТУК, А НЕ ПО-НАДОЛУ.  Когато обектът няма именувани участъци — както
        # един довеждащ водопровод — потокът прескачаше и въпроса за екипите, и
        # всичко след него.  А срокът е най-силният лост, който имаме: с него
        # Илиянци пада от 885 на 761 дни, а Харманли от 346 на 314, защото
        # екипите се ИЗЧИСЛЯВАТ от него вместо да се приемат.
        if step == "q_deadline":
            дни = 0
            ако_числа = re.findall(r"\d+", msg)
            if ако_числа:
                дни = max(0, int(ако_числа[0]))
            elif not any(з in msg for з in ("НЯМА", "НЕ ЗНАМ", "-", "ПРОПУСНИ")):
                return {**_base, "response": (
                    "Моля, напиши **число** — колко календарни дни дава "
                    "договорът за строителството (напр. **660**).  Ако още не "
                    "е известен, напиши **няма**."
                ), "pending_sequence": state}

            tender = {**(state.get("tender") or {})}
            if дни:
                tender["contract_days"] = дни
            state = {**state, "step": "q2", "tender": tender,
                     "_deadline_label": (f"{дни} дни" if дни
                                         else "не е обявен")}
            return self._after_deadline(state)

        # ── Q2: same for all sections? ──────────────────────────────────
        if step == "q2":
            if "ДА" in msg or msg in ("Д", "YES", "Y", "DA"):
                return self._ask_parallel_teams(state)

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
            return self._ask_parallel_teams({**state, "constraints": constraints,
                                             "_exc_label": exc_label})

        # ── Q3: how many teams? ──────────────────────────────────────────
        if step == "q3_teams":
            nums = re.findall(r"\d+", msg)
            if nums:
                num_teams = max(1, int(nums[0]))
                # ОБЯВЕНИЯТ БРОЙ НАДДЕЛЯВА НАД СМЕТКАТА (19.08.2026).  Когато
                # процедурата дава срок, екипите се ИЗЧИСЛЯВАТ от него
                # (`crew_sizing`) и отговорът тук се губеше.  Изпълнителят
                # обаче знае с какво разполага — числото му е по-силно, а
                # сметката остава видима, за да се види разликата.
                tender = {**(state.get("tender") or {}),
                          "declared_teams": num_teams}
                state = {**state, "tender": tender}
                if num_teams == 1:
                    return self._generate_with_sequence({**state, "num_teams": 1, "parallel": False})
                return self._ask_parallel_question({**state, "num_teams": num_teams})
            return {**_base, "response": (
                "Моля, напиши **число** — колко екипи ще работят (напр. **2**)."
            ), "pending_sequence": state}

        # ── Q4: parallel or sequential? ─────────────────────────────────
        if step == "q4_parallel":
            if "ДА" in msg or msg in ("Д", "YES", "Y", "DA"):
                return self._generate_with_sequence({**state, "parallel": True})
            if "НЕ" in msg or msg in ("Н", "NO", "N", "NE"):
                return self._generate_with_sequence({**state, "parallel": False})
            return {**_base, "response": (
                "Моля, отговори с **ДА** (паралелно) или **НЕ** (последователно)."
            ), "pending_sequence": state}

        # Unknown step — clear and restart
        return {**_base, "response": "Нещо се обърка. Напиши **генерирай график** отново."}

    def _ask_contract_days(self, state: dict, метод_етикет: str) -> dict:
        """Питай за договорния срок — той решава колко екипа трябват.

        Срокът стои в обявлението и в проекта на договор, НЕ в количествената
        сметка.  Измерено 24.08.2026: нито техническата спецификация, нито
        сметката на реалния търг носят ред с продължителност, тоест дотук
        оразмеряването на екипите не се задействаше никога.
        """
        _base = {
            "schedule_updated": False,
            "schedule_data": None,
            "correction_info": None,
            "intent": "generate_schedule",
            "model_used": "none",
        }
        return {**_base,
            "response": (
                f"Разбрах: **{метод_етикет}**.\n\n"
                "**Колко календарни дни дава договорът за СТРОИТЕЛСТВОТО?**\n\n"
                "Пише го в обявлението, не в количествата.  От него се "
                "изчислява колко екипа трябват, за да се събере работата в "
                "срока — иначе срокът излиза какъвто се получи.\n\n"
                "  напиши **число** (напр. **660**)\n"
                "  **няма** — ако още не е известен"
            ),
            "pending_sequence": {**state, "step": "q_deadline"},
        }

    def _after_deadline(self, state: dict) -> dict:
        """Или въпросът за участъците, или направо генериране."""
        _base = {
            "schedule_updated": False,
            "schedule_data": None,
            "correction_info": None,
            "intent": "generate_schedule",
            "model_used": "none",
        }
        sections = state.get("sections", [])
        if not sections:
            # Няма именувани участъци — важи за целия обект.
            return self._generate_with_sequence(state)

        default = state.get("constraints", {}).get("default", "water_first")
        choice_label = ("Водопровод → Канализация" if default == "water_first"
                        else "Канализация → Водопровод")
        sections_list = "\n".join(f"  {i+1}. {s}"
                                  for i, s in enumerate(sections))
        срок = state.get("_deadline_label", "")
        return {**_base,
            "response": (
                (f"Договорен срок: **{срок}**.\n\n" if срок else "")
                + f"Последователността **{choice_label}** важи ли за "
                  f"**всички участъци**?\n"
                  f"  **ДА** — генерирай\n"
                  f"  **НЕ** — ще посоча изключенията\n\n"
                  f"Намерени участъци:\n{sections_list}"
            ),
            "pending_sequence": state,
        }

    def _ask_parallel_teams(self, state: dict) -> dict:
        """Ask Q3: how many teams."""
        _base = {
            "schedule_updated": False,
            "schedule_data": None,
            "correction_info": None,
            "intent": "generate_schedule",
            "model_used": "none",
        }
        return {**_base,
            "response": (
                "**Колко екипи ще работят?**\n\n"
                "  **1** — един екип\n"
                "  **2** — два екипа\n"
                "  **3** или повече — посочи броя"
            ),
            "pending_sequence": {**state, "step": "q3_teams"},
        }

    def _ask_parallel_question(self, state: dict) -> dict:
        """Ask Q4: parallel or sequential."""
        _base = {
            "schedule_updated": False,
            "schedule_data": None,
            "correction_info": None,
            "intent": "generate_schedule",
            "model_used": "none",
        }
        num_teams = state.get("num_teams", 2)
        return {**_base,
            "response": (
                f"**{num_teams} екипа — паралелно ли?**\n\n"
                "  **ДА** — екипите работят едновременно (съкращава срока)\n"
                "  **НЕ** — екипите работят последователно"
            ),
            "pending_sequence": {**state, "step": "q4_parallel"},
        }

    def _generate_with_sequence(self, state: dict) -> dict:
        """Trigger schedule generation with collected sequence constraints."""
        analysis = state.get("analysis")
        constraints = state.get("constraints", {})
        if not analysis:
            return {
                "response": "Грешка: липсват данни за анализ. Моля, опитайте отново.",
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "generate_schedule",
                "model_used": "none",
            }
        project_context = state.get("project_context")

        # Build human-readable summary
        default = constraints.get("default", "water_first")
        default_label = "Водопровод → Канализация" if default == "water_first" else "Канализация → Водопровод"
        summary = f"Последователност: **{default_label}**"
        exc_label = state.get("_exc_label")
        if exc_label:
            opposite_label = "Канализация → Водопровод" if default == "water_first" else "Водопровод → Канализация"
            summary += f"\nИзключения ({opposite_label}): {exc_label}"
        laying_label = state.get("_laying_label")
        if laying_label:
            summary += f"\nПолагане на водопровода: **{laying_label}**"
        deadline_label = state.get("_deadline_label")
        if deadline_label:
            summary += f"\nДоговорен срок за строителството: **{deadline_label}**"
        num_teams = state.get("num_teams", 1)
        parallel = state.get("parallel", num_teams > 1)
        if num_teams > 1:
            summary += f"\nЕкипи: **{num_teams}**, {'паралелно' if parallel else 'последователно'}"

        # Re-enter generation flow
        result = self._continue_generation(analysis, constraints, project_context,
                                           num_teams=num_teams if parallel else 1,
                                           tender=state.get("tender"))
        result["response"] = summary + "\n\n" + result.get("response", "")
        result.pop("pending_sequence", None)
        return result

    def _read_situation_files(self) -> tuple[list[str], list[dict], list]:
        """Чертежите, прочетени ВЕДНЪЖ — места, отсечки и точкови позиции.

        ЗАЩО Е ОБЩ МЕТОД (24.08.2026).  Четенето стоеше вътре в `handle_generate`,
        а той връща управлението на въпросника още преди да стигне дотук: при
        реален проект генерацията минава през `_continue_generation`, който
        четеше САМО местата.  Тоест прочетените отсечки и преброените шахти не
        стигаха до пътя, по който върви истинската работа — виждаха се само в
        `tools/offline_dry_run.py`.

        Returns:
            (места, отсечки, точкови позиции) — всяко празно, ако няма чертежи.
        """
        situation_locations: list[str] = []
        situation_segments: list[dict] = []
        #: Точковите позиции от чертежа — шахти, оттоци, сградни отклонения.
        #: Спецификацията рядко ги изброява, а чертежът ги показва всичките.
        situation_nodes: list = []
        if not (self.files and self.ai):
            return situation_locations, situation_segments, situation_nodes

        classification = self.files.classify_files(ai_processor=self.ai)
        situation_paths = classification.get("situation_paths", [])
        if not situation_paths:
            return situation_locations, situation_segments, situation_nodes

        self._progress(0.20,
                       f"Четене на ситуация ({len(situation_paths)} файл/а)...")
        # ЕДНА МРЕЖА, МНОГО ЛИСТА.  Тръжният пакет дава един и същи
        # чертеж по няколко пъти: `5_СИТ_ИНВЕСТИЦИИ_R.pdf` и
        # `Prilojenie_…_SEWERAGE_R.pdf` са БАЙТ ПО БАЙТ еднакви, а
        # водопроводът идва в три варианта — „нормална работа", „при
        # пожар" и инвестиционното приложение.  Без отсяване
        # канализацията се удвоява, а водопроводът се утроява.
        видени_файлове: set[str] = set()
        видени_отсечки: set[tuple] = set()
        for sit_path in situation_paths:
            отпечатък = _отпечатък(sit_path)
            if отпечатък and отпечатък in видени_файлове:
                logger.info("Ситуация %s е същият файл като вече "
                            "прочетен — пропуска се.", sit_path)
                continue
            if отпечатък:
                видени_файлове.add(отпечатък)
            locs = self.ai.extract_situation_locations(sit_path)
            situation_locations.extend(locs)
            # ТОЧКОВИТЕ ПОЗИЦИИ СЕ БРОЯТ ОТ ЧЕРТЕЖА (изпълнителят,
            # 24.08.2026).  Спецификацията дава метри тръба и мълчи за
            # шахтите, оттоците и сградните отклонения; чертежът ги
            # изписва всичките.  Каквото таблиците дават, си остава
            # тяхно — сливането по-долу отсява.
            if os.getenv("SITUATION_NODES", "1") != "0":
                try:
                    from src.situation_reader import read_sewer_nodes
                    situation_nodes.extend(read_sewer_nodes(sit_path))
                except Exception as exc:      # noqa: BLE001
                    logger.warning("Точковите позиции от %s не се "
                                   "прочетоха: %s", sit_path, exc)
            # Отсечките между възли (РШ/ОТ) са в ЧЕРТЕЖА, не в КСС —
            # без тях пакетите не могат да се кръстят като в еталона.
            # Пропуск тук не спира генерацията (виж generate_packages).
            if os.getenv("SITUATION_SEGMENTS", "1") != "0":
                # ПЪРВО ЧЕТЕНЕ, ЧАК ПОСЛЕ ПИТАНЕ.  Тръжните ситуации са
                # векторни: етикетите „Кл.48 / DN 700 / L=618.74м" се
                # четат с координати, а легендата казва кои линии са на
                # ТАЗИ процедура.  Детерминистичният четец не струва
                # токени, повтаря се и обяснява всяко отпадане.  Vision
                # остава само за чертежи, които той не разбира.
                # Кой чертеж е кой не се гадае по името: пробват се и
                # двата прочита и се взима този, който е разбрал нещо.
                # Канализационният иска ЛЕГЕНДА с цветни пера;
                # водопроводният — етикети „PEHD DN…".  Чертеж, който
                # не е нито едното, връща празно и минава към vision.
                прочетени = []
                try:
                    from src.situation_reader import (
                        read_sewer_situation, read_water_situation)
                    from src.tender_parameters import sub_project
                    подобект = sub_project()
                    for чети in (
                        read_sewer_situation,
                        lambda п: read_water_situation(п, area=подобект),
                    ):
                        прочетени = [dict(о._asdict())
                                     for о in чети(sit_path) if о.in_scope]
                        if прочетени:
                            break
                except Exception as exc:      # noqa: BLE001
                    logger.warning("Четенето на ситуация се провали: %s", exc)
                # Същата отсечка от друг лист е същата работа.
                нови = []
                for о in прочетени:
                    ключ = (о.get("network"), str(о.get("branch")),
                            о.get("dn"),
                            round(float(о.get("length_m") or 0), 2))
                    if ключ in видени_отсечки:
                        continue
                    видени_отсечки.add(ключ)
                    нови.append(о)
                if прочетени:
                    situation_segments.extend(нови)
                    logger.info("Ситуация %s: %d участъка ПРОЧЕТЕНИ "
                                "(без модел), %d нови",
                                sit_path, len(прочетени), len(нови))
                    continue
                try:
                    situation_segments.extend(
                        self.ai.extract_situation_segments(sit_path))
                except Exception as exc:      # noqa: BLE001
                    logger.warning("Отсечки от ситуация се провалиха: %s", exc)
        if situation_locations or situation_segments:
            logger.info(
                "Ситуация: %d места, %d отсечки",
                len(situation_locations), len(situation_segments))

        return situation_locations, situation_segments, situation_nodes

    def _continue_generation(
        self, analysis: dict, sequence_constraints: dict, project_context: dict | None = None,
        num_teams: int = 1, tender: dict | None = None,
    ) -> dict:
        """Run the generation steps after questionnaire is complete.

        `tender` носи отговорите на въпросника — ред на мрежите, метод на
        полагане, обявени екипи — до самата генерация.  Виж
        `tender_parameters.for_this_run`.
        """
        all_text = self.files.get_all_text() if self.files else ""

        # ЧЕРТЕЖИТЕ СЕ ЧЕТАТ И ТУК (24.08.2026).  Това е пътят, по който върви
        # реалният проект — след въпросника.  Дотук той вадеше само МЕСТАТА, а
        # отсечките и точковите позиции оставаха в другия клон на кода: тоест
        # прочетеното от чертежа не стигаше до графика, който човекът получава.
        situation_locations, situation_segments, situation_nodes = \
            self._read_situation_files()

        project_type = self._extract_project_type(analysis, project_context)

        progress_messages: list[str] = []
        _cycle_pcts = [0.45, 0.60, 0.75, 0.85, 0.90, 0.92]
        _cycle_idx = [0]

        def _progress(msg: str) -> None:
            progress_messages.append(msg)
            pct = _cycle_pcts[min(_cycle_idx[0], len(_cycle_pcts) - 1)]
            _cycle_idx[0] += 1
            self._progress(pct, msg)

        # STAGING и в потока след въпросника (проба 2026-07-31): това е пътят
        # за реален проект (след въпроса за екипи).  Ако КСС има ≥2 части,
        # генерираме по части — иначе голям проект пак се отрязва.
        _boq = self._with_drawing_counts(self._boq_index(), situation_nodes,
                                         progress_messages)
        # Броим (документ, лист) — одит v13 #6: два файла с лист „КСС" не бива
        # да се считат за една част (иначе staging не се задейства и голям
        # проект пак се отрязва).
        _sheets = {(getattr(getattr(r, "source", None), "document", ""),
                    getattr(getattr(r, "source", None), "sheet", ""))
                   for r in _boq if getattr(r, "quantity", None) is not None}
        _packaged = self._try_package_generation(
            analysis, _boq, num_teams=num_teams,
            locations=situation_locations or None,
            segments=situation_segments or None, progress=_progress,
            tender=tender)

        if _packaged is not None:
            gen_result = _packaged
        elif [s for s in _sheets if s[1]]:
            _progress(f"Генерирам по части (staging) — {len(_sheets)} част(и) от КСС...")
            gen_result = self.ai.generate_schedule_staged(
                analysis, project_type, _progress,
                all_text=all_text, boq_index=_boq, num_teams=num_teams,
                extra_locations=situation_locations or None,
                sequence_constraints=sequence_constraints,
            )
        else:
            gen_result = self.ai.generate_schedule(
                analysis, project_type, _progress,
                all_text=all_text,
                extra_locations=situation_locations or None,
                sequence_constraints=sequence_constraints,
                boq_index=_boq,
                num_teams=num_teams,
            )

        status = gen_result.get("status", "error")
        cycles = gen_result.get("cycles", 0)
        cost = gen_result.get("total_cost", 0.0)
        history = gen_result.get("history", [])
        response_parts = []

        for msg in progress_messages:
            response_parts.append(f"- {msg}")

        if status == "invalid":
            # GATE (одит 2026-07-23): кодът отхвърли графика.  Никакво
            # „одобрен" — AI статусът вече не е авторитетен.
            response_parts.append(
                "\n🛑 **Графикът е ОТХВЪРЛЕН от проверката.** "
                f"(AI го беше отбелязал като '{gen_result.get('ai_status', '?')}', "
                f"${cost:.4f})\n"
                "Не се записва като текущ график и не може да се експортира."
            )
        elif status == "approved":
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

        response_parts.extend(
            self._format_quantity_provenance(self._verify_quantities(gen_result))
        )
        response_parts.extend(self._format_generation_repairs(gen_result))
        response_parts.extend(self._format_duration_report(gen_result))
        response_parts.extend(self._format_validation_report(gen_result))
        response_parts.extend(
            format_injection_warnings(gen_result.get("injection_findings") or [])
        )

        # GATE (одит 2026-07-23; разширен v6, точка 1): allowlist на статуса —
        # error/stopped НЕ стават текущ график.  Виж бележката в
        # `_handle_generate_schedule`.
        from src.ai_processor import AIProcessor
        _valid = status in AIProcessor.ACCEPTED_STATUSES
        if _valid:
            self.current_schedule = gen_result.get("schedule")
        else:
            self.rejected_schedule = gen_result.get("schedule")
        self.correction_history = history
        self.current_project_type = project_type

        # Save to project manager (same as _handle_generate_schedule)
        if _valid and self.project_mgr and self.project_mgr.current_project:
            pid = self.project_mgr.current_project.get("id")
            if pid:
                self.project_mgr.save_progress(pid, {
                    "status": "schedule_generated",
                    "last_schedule": self.current_schedule,
                })

        return {
            "response": "\n".join(response_parts),
            "schedule_updated": _valid and bool(self.current_schedule),
            "schedule_data": self.current_schedule,
            "validation": gen_result.get("validation"),
            "export": {"exportable": gen_result.get("exportable"),
                       "export_blockers": gen_result.get("export_blockers"),
                       "export_policy": gen_result.get("export_policy")},
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
        # Одит 2026-07-23: изключването пазеше входа (_handle_evolve), но НЕ и
        # този път.  Останал `pending_changes` обект от преди изключването
        # продължаваше към backup и прилагане.  Тук се затваря и той.
        if not evolution_enabled():
            return {
                "response": EVOLUTION_DISABLED_MESSAGE,
                "schedule_updated": False,
                "schedule_data": None,
                "correction_info": None,
                "intent": "confirm_change",
                "model_used": "none",
                "evolution_cleared": True,
            }

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

        # Backup — ЗА ВСЯКО ниво, не само за red (P6): без бекъп няма rollback.
        backup_hash = ""
        progress.append("Създавам backup...")
        backup = self.evolution.create_backup(plan.get("description", ""))
        if backup["success"]:
            backup_hash = backup["commit_hash"]
            progress.append(f"   Git commit: {backup_hash[:8]}")
        else:
            progress.append(f"   Backup неуспешен: {backup.get('error', '?')}")

        # Apply changes
        progress.append("Прилагам промени...")
        apply_result = self.evolution.apply_changes(changes, declared_level=level)
        progress.append(f"   Приложени: {apply_result['applied']}, Грешки: {apply_result['failed']}")

        if apply_result["failed"] > 0:
            error_text = "\n".join(apply_result["errors"])
            # Auto-rollback — вече има бекъп на всяко ниво
            if backup_hash:
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
                "model_used": MODEL_CONTROLLER,
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
                    "model_used": MODEL_CONTROLLER,
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
            "model_used": MODEL_CONTROLLER,
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

        # Require "/" to be at a word boundary (start or after whitespace) to
        # avoid false matches on fractions/units like "5м/ден" or "В/К".
        if re.search(r'[A-Za-z]:\\[^\s"\']+|(?:^|\s)/[^\s"\']{2,}', message):
            return "load_project"

        best_intent = "general"
        best_score = 0
        for intent, keywords in INTENT_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in message_lower)
            # Use >= so that later (more specific) intents win ties over earlier
            # (more generic) ones — e.g. "добави функционалност" → evolve, not
            # modify_schedule; "запиши урок" → save_lesson, not ask_question.
            if score >= best_score and score > 0:
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

        The AI has already cleaned 'зареди проект Тестоград моля' → query='Тестоград'.
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
        self.current_project_type = ""

    def restore_history(self, messages: list[dict[str, str]]) -> None:
        """Restore chat history from saved data."""
        self.history = list(messages)
