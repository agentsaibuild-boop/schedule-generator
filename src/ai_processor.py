"""AI processor — orchestrates document analysis, schedule generation, and chat.

Uses AIRouter for all API calls (DeepSeek worker + Anthropic controller).
Enforces strict JSON pipeline: only converted .json files are accepted for analysis.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.ai_router import AIRouter
    from src.knowledge_manager import KnowledgeManager

logger = logging.getLogger(__name__)


class AIProcessor:
    """Orchestrates AI-powered schedule generation and document analysis."""

    def __init__(
        self,
        router: AIRouter | None = None,
        knowledge_manager: KnowledgeManager | None = None,
        api_key: str | None = None,
        skills_path: str = "",
    ) -> None:
        """Initialize the AI processor.

        Args:
            router: AIRouter instance for dual-AI calls.
            knowledge_manager: KnowledgeManager for building prompts.
            api_key: Legacy param (kept for backward compat during transition).
            skills_path: Legacy param.
        """
        self.router = router
        self.knowledge = knowledge_manager
        self._legacy_api_key = api_key or ""

    @property
    def is_configured(self) -> bool:
        """Check whether at least one AI model is available."""
        if self.router:
            return self.router.deepseek_available or self.router.anthropic_available
        return bool(self._legacy_api_key)

    # ------------------------------------------------------------------
    # System prompt builders
    # ------------------------------------------------------------------

    def build_system_prompt(self, project_type: str | None = None) -> str:
        """Build FULL system prompt for the worker (DeepSeek) from all knowledge tiers.

        Includes: SKILL.md + methodology + last 20 lessons + productivities + workflow.
        ~5000-8000 tokens.

        Args:
            project_type: Optional project type for specific methodology.

        Returns:
            Combined system prompt string.
        """
        if self.knowledge:
            return self.knowledge.get_all_knowledge_for_prompt(
                project_type=project_type, level="full"
            )

        return (
            "Ти си асистент за строителни графици за ВиК проекти в България. "
            "Отговаряй на български. Следвай правилата за генериране на линейни графици."
        )

    def build_minimal_prompt(self) -> str:
        """Build minimal system prompt for lightweight tasks (OCR, simple questions).

        Includes ONLY: core rules + productivities.
        ~1500-2000 tokens. Saves tokens for routine operations.

        Returns:
            Minimal system prompt string.
        """
        if self.knowledge:
            return self.knowledge.get_all_knowledge_for_prompt(level="minimal")

        return (
            "Ти си асистент за строителни графици за ВиК проекти в България. "
            "Отговаряй на български."
        )

    def build_verification_prompt(self) -> str:
        """Build strict verification rules for the controller (Anthropic).

        Returns:
            Verification rules string.
        """
        parts = ["Проверявай СТРИКТНО следните правила:\n"]

        if self.knowledge:
            # Include skills (core rules)
            skills = self.knowledge.get_skills()
            if skills:
                parts.append(skills)

            # Include verification checklist if available
            refs_path = self.knowledge.skills_path / "references"
            checklist_path = refs_path / "verification-checklist.md"
            if checklist_path.exists():
                parts.append(
                    "\n=== VERIFICATION CHECKLIST ===\n"
                    + checklist_path.read_text(encoding="utf-8")
                )

            # Include workflow rules
            workflow_path = refs_path / "workflow-rules.md"
            if workflow_path.exists():
                parts.append(
                    "\n=== WORKFLOW RULES ===\n"
                    + workflow_path.read_text(encoding="utf-8")
                )

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------

    def _validate_json_inputs(self, files: list[dict]) -> None:
        """Validate that all input files are converted .json files.

        Args:
            files: List of file info dicts from FileManager.

        Raises:
            ValueError: If any non-JSON files are detected.
        """
        non_json = [
            f.get("original", f.get("name", "unknown"))
            for f in files
            if f.get("converted") and not f["converted"].endswith(".json")
        ]
        if non_json:
            raise ValueError(
                f"Non-JSON files detected: {non_json}. "
                "Run file conversion first! (Rule #0)"
            )

    # ------------------------------------------------------------------
    # Document analysis
    # ------------------------------------------------------------------

    def analyze_documents(
        self, converted_files: list[dict], all_text: str = ""
    ) -> dict:
        """Analyze converted documents via the worker (DeepSeek).

        IMPORTANT: Only accepts converted .json files (Rule #0).

        Args:
            converted_files: List of file info dicts from FileManager.get_converted_files().
            all_text: Combined text content from all converted files (from FileManager.get_all_text()).

        Returns:
            Analysis dict with project_type, scope, quantities, etc.
        """
        if not self.router:
            return {
                "status": "error",
                "message": "AI Router not initialized.",
            }

        # Validate: only JSON files allowed
        self._validate_json_inputs(converted_files)

        # Build file index for reference
        file_summaries = []
        for f in converted_files:
            name = f.get("original", f.get("name", "unknown"))
            method = f.get("method", "")
            file_summaries.append(f"- {name} ({method})")
        files_index = "\n".join(file_summaries)

        # Use actual document content if available, fall back to index only
        if all_text.strip():
            # Truncate to ~120k chars to stay within token limits
            content_block = all_text[:120_000]
            if len(all_text) > 120_000:
                content_block += "\n\n[... съдържанието е съкратено ...]"
            doc_section = f"ФАЙЛОВЕ:\n{files_index}\n\nСЪДЪРЖАНИЕ:\n{content_block}"
        else:
            doc_section = f"ФАЙЛОВЕ (без съдържание — конвертирането не е успяло):\n{files_index}"

        system_prompt = self.build_system_prompt()
        messages = [{
            "role": "user",
            "content": (
                "Анализирай следните конвертирани документи от тендерна процедура за ВиК:\n\n"
                f"{doc_section}\n\n"
                "ВАЖНО: Документите в папката се допълват взаимно — информацията в един файл "
                "може да липсва или да е непълна в друг. "
                "Изгради консолидирана картина като кръстосаш ВСИЧКИ файлове:\n"
                "- Ако в единия файл има улица без метраж → търси метража в останалите\n"
                "- Ако в единия файл има метраж без улица → търси улицата в останалите\n"
                "- Ако данните липсват навсякъде → маркирай като 'неизвестно', НЕ измисляй\n"
                "- Ако данните си ПРОТИВОРЕЧАТ между файлове → НЕ избирай сам. "
                "Запиши в conflicts[] като: "
                "'[обект]: [стойност от файл А] vs [стойност от файл Б]'\n\n"
                "Определи:\n"
                "1. Тип проект — ЗАДЪЛЖИТЕЛНО избери ТОЧНО ЕДИН от следните типове:\n"
                "   - 'разпределителна мрежа' — мрежа с много клонове/участъци (улична мрежа)\n"
                "   - 'довеждащ' — един довеждащ водопровод/колектор (единична нишка)\n"
                "   - 'единичен' — единичен участък, 1-2 улици, без проектиране\n"
                "   - 'инженеринг' — ВКЛЮЧВА проектиране + строителство. Индикатори: "
                "'Технически проект', 'Геодезически проучвания', 'Авторски надзор' "
                "като ОТДЕЛНИ позиции в КСС, срок >500 дни\n"
                "   - 'mega' — >20km обща дължина или >500 участъка\n"
                "   - 'out_of_scope' — проектът НЕ може да се генерира автоматично. "
                "Задължително използвай 'out_of_scope' при: "
                "HDD/хоризонтално сондиране/microtunneling/pipe bursting технологии; "
                "аварийно-ремонтни дейности ('аварийна замяна', 'аварийен ремонт'); "
                "критично кратък срок (<20 работни дни за нестандартна работа); "
                "проектът е 'Демонтаж' или 'Рехабилитация' без ново строителство; "
                "Възложителят осигурява материалите (нестандартна доставка)\n"
                "2. Обхват — какви мрежи се строят (водопровод, канализация, пътни)\n"
                "3. Количества — DN, дължини на клонове/участъци (консолидирани от всички файлове)\n"
                "4. Срокове — ако са споменати\n"
                "5. Специфики — терен, материали, брой екипи\n"
                "6. locations — ИЗЧЕРПАТЕЛЕН списък на ВСИЧКИ имена на улици, квартали, "
                "местности, обекти и топоними, намерени буквално в документите. "
                "Включи само имена, които реално присъстват в текста. "
                "НЕ добавяй имена по предположение.\n"
                "7. conflicts — списък с противоречия между файлове, изискващи човешко решение\n\n"
                "ВАЖНО: Ако project_type е 'out_of_scope', обясни причината в полето 'specifics'.\n\n"
                "Отговори в JSON формат с полета: "
                "project_type, scope, quantities, deadlines, specifics, "
                "locations (list[str]), conflicts (list[str])."
            ),
        }]

        result = self.router.chat(messages, system_prompt)

        return {
            "status": "ok",
            "analysis": result["content"],
            "model": result["model"],
            "cost": result["cost"],
            "fallback": result.get("fallback", False),
        }

    # ------------------------------------------------------------------
    # Schedule generation with verification cycle
    # ------------------------------------------------------------------

    def generate_schedule(
        self,
        analysis: dict,
        project_type: str,
        progress_callback: Any | None = None,
        all_text: str = "",
        extra_locations: list[str] | None = None,
        sequence_constraints: dict | None = None,
    ) -> dict:
        """Generate a schedule via worker, then verify via controller.

        Args:
            analysis: Analysis dict from analyze_documents.
            project_type: Type of construction project.
            progress_callback: Optional callable(message: str) for progress.

        Returns:
            Dict with schedule, correction history, costs.
        """
        if not self.router:
            return {
                "status": "error",
                "message": "AI Router not initialized.",
            }

        # Step 1: Generate via DeepSeek
        if progress_callback:
            model_label = "DeepSeek" if self.router.deepseek_available else "Anthropic"
            progress_callback(f"Генерирам график... ({model_label})")

        system_prompt = self.build_system_prompt(project_type)
        analysis_text = (
            analysis.get("analysis", "")
            if isinstance(analysis.get("analysis"), str)
            else json.dumps(analysis, ensure_ascii=False)
        )

        # Extract locations whitelist from analysis
        locations: list[str] = []
        raw_analysis = analysis.get("analysis", "")
        if isinstance(raw_analysis, str):
            try:
                parsed_analysis = json.loads(raw_analysis)
                locations = parsed_analysis.get("locations", [])
            except (json.JSONDecodeError, AttributeError):
                pass
        elif isinstance(raw_analysis, dict):
            locations = raw_analysis.get("locations", [])

        # Merge situation-derived locations (ground-truth from site plans)
        if extra_locations:
            existing_lower = {loc.lower() for loc in locations}
            for loc in extra_locations:
                if loc.lower() not in existing_lower:
                    locations.append(loc)
                    existing_lower.add(loc.lower())
            logger.info("Locations after situation merge: %d total", len(locations))

        locations_section = ""
        if locations:
            loc_list = "\n".join(f"  - {loc}" for loc in locations)
            locations_section = (
                f"\n\nДОПУСТИМИ ИМЕНА НА МЕСТА (само тези са намерени в документите):\n"
                f"{loc_list}\n"
                "ПРАВИЛО: Използвай САМО горните имена в заглавията на задачите. "
                "Ако дадено място не е в списъка — НЕ го измисляй. "
                "Пиши 'Участък X' или 'Клон Y' вместо измислено название."
            )

        # Build sequence constraints section
        sequence_section = ""
        if sequence_constraints:
            default = sequence_constraints.get("default", "")
            default_label = (
                "Водопровод → Канализация" if default == "water_first"
                else "Канализация → Водопровод" if default == "sewer_first"
                else ""
            )
            lines = ["ЗАДЪЛЖИТЕЛНА ПОСЛЕДОВАТЕЛНОСТ (потвърдена от потребителя):"]
            if default_label:
                lines.append(f"  По подразбиране: {default_label}")
            for section, order in sequence_constraints.items():
                if section == "default":
                    continue
                order_label = (
                    "Водопровод → Канализация" if order == "water_first"
                    else "Канализация → Водопровод"
                )
                lines.append(f"  {section}: {order_label}")
            lines.append(
                "ПРАВИЛО: Спазвай горната последователност стриктно. "
                "НЕ я променяй дори ако смяташ, че друг ред е по-добър."
            )
            sequence_section = "\n\n" + "\n".join(lines)

        messages = [{
            "role": "user",
            "content": (
                f"Генерирай строителен линеен график за следния проект:\n\n"
                f"{analysis_text}"
                f"{locations_section}"
                f"{sequence_section}\n\n"
                # NOTE: project_type ТРЯБВА да идва от analyze_documents резултата,
                # не от project_context.get('type', ''). Ако е празен — AI използва анализа.
                f"Тип: {project_type or 'НЕИЗВЕСТЕН — определи от анализа по-горе'}\n\n"
                "КРИТИЧНО — Производителности по материал (не ги игнорирай!):\n"
                "- DN300 PE: 25-30 м/ден\n"
                "- DN300 CI (чугун, сив или ковък): 3-5 м/ден (10-12× по-бавно от PE!)\n"
                "- DN500 PE: 20-25 м/ден\n"
                "- Безизкопно (HDD/хоризонтално сондиране): 56 м/ден (отделен екип Сондаж)\n"
                "ПРЕДИ всяко изчисление: идентифицирай материала (PE/CI/AC/GRP) "
                "и избери ПРАВИЛНАТА производителност от productivities.json.\n\n"
                "ЗАДЪЛЖИТЕЛНО — Параметрични продължителности:\n"
                "  duration_days = ceil(length_m / effective_rate_m_per_day)\n"
                "  Минимум 5 работни дни за всяка дейност (мобилизация/логистика).\n"
                "ЗАБРАНЕНО: Да задаваш еднакви дни на всички клонове от един DN!\n"
                "Пример: Кл.16 (351м, DN90 PE) = 24д; Кл.22 (70м, DN90 PE) = 12д — РАЗЛИЧНИ!\n\n"
                "ЗАБРАНЕНО — Фантомни фази (НЕ ДОБАВЯЙ ако не са в КСС/модела):\n"
                "- 'Административна подготовка' (освен ако е изрично в КСС)\n"
                "- 'Въвеждане ВОБД'\n"
                "- 'Демобилизация'\n"
                "- 'Пускане в експлоатация' (освен ако е изрично в КСС)\n"
                "- 'Гаранционен срок' (освен ако е изрично в КСС)\n"
                "- Всяка друга фаза без конкретно покритие в предоставените документи\n\n"
                "ДЕЗИНФЕКЦИЯ — задължителна логика (дни зависят от DN и тип мрежа):\n"
                "- Разпределителна мрежа (много участъци): дезинфекция PER SECTION "
                "СЛЕД хидравлична проба на участъка\n"
                "- Довеждащ водопровод (1 нишка): обща дезинфекция СЛЕД всички секции\n"
                "- DN90-110 PE (до 500м/клон): 2 дни дезинфекция\n"
                "- Mixed DN, голяма мрежа: 4 дни дезинфекция\n"
                "- DN500 PE: 4 дни дезинфекция\n"
                "- DN300 CI, горски терен: 6 дни дезинфекция\n\n"
                "АБСОЛЮТНО ПРАВИЛО — КПС и Тласкател:\n"
                "ЗАДЪЛЖИТЕЛНО: КПС стартира САМО след завършване на Тласкател.\n"
                "Зависимост: [Тласкател] → [КПС] тип FS (Finish-to-Start), lag = 0.\n"
                "НИКОГА не планирай КПС паралелно с Тласкател или преди него!\n\n"
                "ПРАВИЛО — Настилки (асфалтиране, павета, тротоарна настилка):\n"
                "Настилките са ОТДЕЛНА бригада — не са дейност на основния изкопен екип.\n"
                "Зависимост: [Настилки] SS+30 от [Изкопни работи] — настилките стартират\n"
                "НЕ по-рано от 30 работни дни след началото на изкопаването (засипване + уплътняване).\n\n"
                "Отговори в JSON формат с:\n"
                "- tasks: масив от задачи с id, name, duration, start_day, "
                "dependencies, dn, length_m, team\n"
                "- total_duration: общ брой дни\n"
                "- teams: списък екипи\n"
                "- notes: допълнителни бележки"
            ),
        }]

        gen_result = self.router.chat(messages, system_prompt)

        if gen_result.get("error"):
            return {
                "status": "error",
                "message": gen_result["content"],
            }

        schedule_json = gen_result["content"]

        # Step 2: Verification cycle
        rules = self.build_verification_prompt()

        cycle_result = self.router.run_correction_cycle(
            schedule_json, rules, max_cycles=3, progress_callback=progress_callback,
            project_type=project_type,
        )

        gen_cost = gen_result.get("cost", 0.0)
        cycle_cost = cycle_result.get("total_cost", 0.0)

        # Step 3: Location hallucination check
        hallucination_warnings: list[str] = []
        if locations or all_text:
            schedule_tasks = cycle_result.get("schedule", [])
            if isinstance(schedule_tasks, list):
                hallucination_warnings = self._validate_task_locations(
                    schedule_tasks, locations, all_text
                )

        # Step 4: MS Project expert enrichment
        verified_schedule = cycle_result.get("schedule", {})
        msp_cost = 0.0
        if verified_schedule:
            if progress_callback:
                progress_callback("Обогатявам за MS Project... (Anthropic)")
            enriched, msp_cost = self.enrich_for_msproject(verified_schedule)
            if enriched:
                verified_schedule = enriched

        return {
            "status": cycle_result["status"],
            "schedule": verified_schedule,
            "cycles": cycle_result["cycles"],
            "total_cost": gen_cost + cycle_cost + msp_cost,
            "history": cycle_result.get("history", []),
            "remaining_issues": cycle_result.get("remaining_issues", []),
            "gen_model": gen_result["model"],
            "hallucination_warnings": hallucination_warnings,
        }

    # ------------------------------------------------------------------
    # Location hallucination validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_task_locations(
        tasks: list[dict],
        locations_whitelist: list[str],
        all_text: str,
    ) -> list[str]:
        """Check task names for location names not found in source documents.

        Strategy:
        - Extract capitalised Bulgarian/Latin tokens from each task name
          (likely street/place names, e.g. "Витоша", "Илиенци")
        - A token is suspicious if it appears in neither the whitelist nor
          the full document text
        - Returns list of human-readable warning strings

        Args:
            tasks: Generated schedule task list.
            locations_whitelist: Locations extracted by analyze_documents().
            all_text: Raw combined document text for broad substring search.

        Returns:
            List of warning strings (empty = no issues detected).
        """
        import re

        # Build a single searchable corpus: whitelist + full document text
        whitelist_lower = {loc.lower() for loc in locations_whitelist}
        corpus_lower = all_text.lower()

        # Regex: capitalised tokens of 4+ chars (avoids "DN", "РЕ", etc.)
        _PLACE_TOKEN = re.compile(r"\b[А-ЯA-ZЁ][а-яa-zёА-ЯA-Z]{3,}\b")

        # Common Bulgarian construction words to skip (not place names)
        _SKIP_WORDS = {
            "водопровод", "канализация", "участък", "клон", "фаза", "етап",
            "дейност", "монтаж", "полагане", "изкоп", "засипване", "уплътняване",
            "дезинфекция", "проба", "приемане", "проектиране", "надзор",
            "подготовка", "демонтаж", "рехабилитация", "реконструкция",
            "екип", "бригада", "доставка", "инсталация", "свързване",
            "разрешение", "съгласуване", "въвеждане", "експлоатация",
        }

        warnings: list[str] = []

        for task in tasks:
            name = task.get("name", "")
            if not name:
                continue

            tokens = _PLACE_TOKEN.findall(name)
            for token in tokens:
                if token.lower() in _SKIP_WORDS:
                    continue
                token_lower = token.lower()
                # Check whitelist and full document corpus
                in_whitelist = any(token_lower in loc.lower() for loc in locations_whitelist)
                in_corpus = token_lower in corpus_lower
                if not in_whitelist and not in_corpus:
                    task_id = task.get("id", "?")
                    warnings.append(
                        f"Задача {task_id} '{name}': "
                        f"'{token}' не е намерено в документите — възможна халюцинация."
                    )

        return warnings

    # ------------------------------------------------------------------
    # MS Project expert enrichment
    # ------------------------------------------------------------------

    def enrich_for_msproject(
        self, schedule: dict | str
    ) -> tuple[dict | None, float]:
        """Enrich a verified schedule with MS Project structure and metadata.

        This is the third AI pass in the pipeline — an MS Project expert that
        knows the output will be refined by a human in MS Project.  It adds:
          - WBS codes (hierarchical numbering: 1, 1.1, 1.2, 2, ...)
          - Dependency types per link (FS / SS / FF / SF) with lag_days
          - Milestone tasks at key phase transitions
          - Summary (parent) tasks grouping related activities
          - Constraint hints (ASAP / MFO / FNLT) where technically justified
          - Per-task notes explaining the scheduling logic for the human reviewer
          - risk_buffer_days: recommended float for critical tasks

        The method uses Anthropic (controller model) because it requires
        structured reasoning about MS Project conventions, not just text generation.

        Args:
            schedule: Verified schedule dict (or JSON string) from generate_schedule().

        Returns:
            Tuple of (enriched schedule dict | None, cost_usd).
            Returns (None, 0.0) on failure so the caller can fall back to the
            original verified schedule.
        """
        if not self.router:
            return None, 0.0

        if isinstance(schedule, str):
            try:
                schedule = json.loads(schedule)
            except json.JSONDecodeError:
                logger.warning("enrich_for_msproject: invalid JSON schedule string")
                return None, 0.0

        tasks = schedule.get("tasks", [])
        if not tasks:
            return None, 0.0

        system_prompt = (
            "Ти си сертифициран експерт по Microsoft Project (PMP + MCTS) с 15+ години опит "
            "в управлението на ВиК инфраструктурни проекти в България.\n\n"
            "КОНТЕКСТ: Получаваш верифициран строителен график (JSON), генериран от AI. "
            "Графикът ЩЕ БЪДЕ отворен в Microsoft Project от опитен ръководител на проект, "
            "който ще го доработи ръчно. Твоята задача е да го ОБОГАТИШ до ниво, "
            "което максимално улеснява човека в MS Project — не да промениш логиката, "
            "а да добавиш MS Project специфична структура и метаданни.\n\n"
            "КАКВО ДА ДОБАВИШ КЪМ ВСЯКА ЗАДАЧА:\n"
            "1. wbs: WBS код (напр. '1', '1.1', '2', '2.3') — йерархичен номер\n"
            "2. dependency_type: тип на всяка зависимост — 'FS', 'SS', 'FF' или 'SF'\n"
            "   По подразбиране е 'FS'. Използвай 'SS' когато задачите логично вървят паралелно "
            "   (напр. изкоп и монтаж на малко разстояние). Използвай 'FF' за финализиращи задачи.\n"
            "3. lag_days: закъснение след зависимостта в дни (0 = веднага, >0 = изчакай)\n"
            "   Примери: между изкоп и монтаж lag=0, между монтаж и засипване lag=1 (спиране на натиск)\n"
            "4. is_milestone: true само за задачи с duration=0 или за ключови контролни точки\n"
            "5. constraint_type: 'ASAP' (по подразбиране), 'MFO' (Must Finish On), "
            "   'FNLT' (Finish No Later Than) — само когато има реална техническа причина\n"
            "6. notes_msp: кратка бележка (1-2 изречения БГ) за ръководителя на проекта — "
            "   защо тази задача е така наредена, какво да внимава при ручна корекция\n"
            "7. risk_buffer_days: препоръчителен буфер в дни (0 ако няма риск, 3-10 за сложни)\n"
            "8. Ако има логически групи задачи — добави summary задача (is_summary: true, "
            "   duration = span от първата до последната подзадача, sub_task_ids: [...])\n\n"
            "ПРАВИЛА:\n"
            "- НЕ ПРОМЕНЯЙ start_day, duration, dependencies на съществуващите задачи\n"
            "- НЕ ДОБАВЯЙ нови работни задачи — само summary/milestone задачи\n"
            "- Milestone задачите имат duration=0 и се поставят в края на фаза\n"
            "- Summary задачите не се изпълняват — те само групират (is_summary=true)\n"
            "- WBS кодовете трябва да са консистентни с йерархията\n"
            "- lag_days могат да са отрицателни (lead time) — напр. -2 означава 'започни 2 дни преди края'\n\n"
            "ФОРМАТ НА ОТГОВОРА — САМО ДЕЛТА (не повтаряй оригиналните полета):\n"
            "{\n"
            '  "enrichments": [\n'
            '    {"id": 1, "wbs": "1", "dependency_type": "FS", "lag_days": 0,\n'
            '     "constraint_type": "ASAP", "notes_msp": "...", "risk_buffer_days": 0},\n'
            "    ...\n"
            "  ],\n"
            '  "milestones": [\n'
            '    {"id": "M1", "name": "Край фаза Водопровод", "start_day": 62, "wbs": "1.M"},\n'
            "    ...\n"
            "  ],\n"
            '  "summary_tasks": [\n'
            '    {"id": "S1", "name": "Водопроводна мрежа", "wbs": "1", "start_day": 0,\n'
            '     "duration": 62, "sub_task_ids": [1, 2, 3, 4, 5]},\n'
            "    ...\n"
            "  ],\n"
            '  "msp_notes": "Обща бележка за ръководителя..."\n'
            "}\n"
            "НЕ включвай никакъв текст извън JSON. "
            "НЕ повтаряй оригиналните полета (name, duration, start_day) в enrichments."
        )

        # Send slim task list (only fields needed for reasoning)
        slim_tasks = [
            {k: v for k, v in t.items()
             if k in ("id", "name", "duration", "start_day", "dependencies", "team")}
            for t in tasks
        ]
        slim_schedule = {
            "tasks": slim_tasks,
            "total_duration": schedule.get("total_duration"),
        }
        schedule_json = json.dumps(slim_schedule, ensure_ascii=False)
        user_msg = (
            "Обогати следния строителен график за MS Project. "
            "Върни САМО делтата (enrichments по id, milestones, summary_tasks, msp_notes):\n\n"
            f"{schedule_json}"
        )

        messages = [{"role": "user", "content": user_msg}]

        try:
            # Use Anthropic (controller) — delta format keeps output compact
            result = self.router.chat_anthropic_direct(
                messages, system_prompt, max_tokens=8192
            )
            raw = result.get("content", "")
            cost = result.get("cost", 0.0)

            # Strip markdown fences
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3].strip()

            enriched = json.loads(cleaned)

            # Merge enrichment deltas back into original tasks by id
            delta_by_id: dict = {}
            for e in enriched.get("enrichments", []):
                delta_by_id[e.get("id")] = e

            merged_tasks = []
            for orig in tasks:
                tid = orig.get("id")
                delta = delta_by_id.get(tid, {})
                merged = {**orig}
                for key in ("wbs", "dependency_type", "lag_days", "is_milestone",
                            "constraint_type", "notes_msp", "risk_buffer_days"):
                    if key in delta:
                        merged[key] = delta[key]
                merged_tasks.append(merged)

            result_schedule = {
                **schedule,
                "tasks": merged_tasks,
                "milestones": enriched.get("milestones", []),
                "summary_tasks": enriched.get("summary_tasks", []),
                "msp_notes": enriched.get("msp_notes", ""),
            }

            logger.info(
                "MS Project enrichment: %d tasks enriched, %d milestones, %d summary tasks",
                len(merged_tasks),
                len(result_schedule["milestones"]),
                len(result_schedule["summary_tasks"]),
            )
            return result_schedule, cost

        except Exception as exc:
            logger.warning("MS Project enrichment failed: %s", exc)
            return None, 0.0

    # ------------------------------------------------------------------
    # Chat response
    # ------------------------------------------------------------------

    def chat_response(
        self, messages: list[dict], project_context: dict | None = None
    ) -> dict:
        """Process a chat message via the worker.

        Args:
            messages: Chat history as list of dicts.
            project_context: Optional current project info.

        Returns:
            Dict with content, model, cost, fallback.
        """
        if not self.router:
            return {
                "content": "AI не е инициализиран. Проверете .env файла.",
                "model": "none",
                "cost": 0.0,
            }

        system_prompt = self.build_system_prompt()
        if project_context:
            ctx_str = json.dumps(project_context, ensure_ascii=False, default=str)
            system_prompt += f"\n\nТекущ проект: {ctx_str}"

        return self.router.chat(messages, system_prompt)

    # ------------------------------------------------------------------
    # Text reformatting (DeepSeek text task — cheap, no vision)
    # ------------------------------------------------------------------

    def reformat_text(self, raw_text: str, source_name: str = "") -> dict:
        """Reformat partial/messy PDF text via DeepSeek (text-only, no vision).

        Used when fitz extracts some text but it's poorly structured.
        Much cheaper than OCR — just a text cleanup task.

        Args:
            raw_text: Raw extracted text from fitz.
            source_name: Original filename for context.

        Returns:
            Dict with 'status' and 'text' keys.
        """
        if not self.router:
            return {"status": "error", "error": "AI Router not initialized."}

        if not raw_text or len(raw_text.strip()) < 20:
            return {"status": "error", "error": "Text too short to reformat."}

        result = self.router.reformat_text(raw_text, source_name)
        return result

    # ------------------------------------------------------------------
    # OCR (delegates to router, which handles fallback)
    # ------------------------------------------------------------------

    def ocr_pdf(self, filepath: str) -> dict:
        """OCR a scanned PDF using AI vision (DeepSeek, fallback Anthropic).

        Args:
            filepath: Absolute path to the PDF file.

        Returns:
            Dict with 'status' and 'data' keys matching conversion format.
        """
        try:
            import fitz  # PyMuPDF
        except ImportError:
            return {
                "status": "error",
                "error": "PyMuPDF (fitz) is required for OCR. Run: pip install PyMuPDF",
            }

        if not self.router:
            return {"status": "error", "error": "AI Router not initialized."}

        # Build minimal prompt for OCR context
        ocr_system_prompt = self.build_minimal_prompt()

        source_name = Path(filepath).name
        doc = fitz.open(filepath)
        pages_text: list[dict] = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            pix = page.get_pixmap(dpi=200)
            img_bytes = pix.tobytes("png")
            b64_image = base64.b64encode(img_bytes).decode("ascii")

            try:
                extracted = self.router.ocr_pdf_page(
                    b64_image, system_prompt=ocr_system_prompt
                )
            except Exception as exc:
                logger.warning(
                    "OCR error on page %d of %s: %s", page_num + 1, source_name, exc
                )
                if "rate" in str(exc).lower():
                    time.sleep(5)
                extracted = f"[OCR ERROR page {page_num + 1}: {exc}]"

            pages_text.append({"page": page_num + 1, "text": extracted})
            logger.info(
                "OCR page %d/%d of %s: %d chars",
                page_num + 1, len(doc), source_name, len(extracted),
            )

        doc.close()

        full_text = "\n\n".join(p["text"] for p in pages_text if p["text"])

        data = {
            "source_file": source_name,
            "type": "pdf",
            "extraction_method": "ocr_vision",
            "pages": len(pages_text),
            "content": pages_text,
            "full_text": full_text,
        }
        return {"status": "ok", "data": data}

    # ------------------------------------------------------------------
    # Situation / site-plan location extraction
    # ------------------------------------------------------------------

    def extract_situation_locations(self, filepath: str) -> list[str]:
        """Extract street/quarter/locality names from a situation (site-plan) PDF.

        Sends each page as a vision image with a focused prompt that asks for
        ONLY place names — not OCR of all text.  Returns de-duplicated list.

        Why vision even for vector PDFs: AutoCAD-generated PDFs may have
        extractable text but it comes out as fragmented coordinates/numbers.
        Vision correctly reads the human-readable labels on the drawing.

        Args:
            filepath: Absolute path to the PDF file (original, not converted).

        Returns:
            List of location strings found on the drawing.  Empty on failure.
        """
        try:
            import fitz  # PyMuPDF
        except ImportError:
            logger.warning("PyMuPDF not available — situation OCR skipped.")
            return []

        if not self.router:
            return []

        source_name = Path(filepath).name
        logger.info("Extracting locations from situation file: %s", source_name)

        # Read as bytes first — fitz.open(path) garbles Cyrillic paths on Windows
        try:
            with open(filepath, "rb") as _fh:
                _pdf_bytes = _fh.read()
        except OSError as exc:
            logger.error("Cannot read situation file %s: %s", source_name, exc)
            return []

        situation_prompt = (
            "Това е строителна ситуация (трасировъчен план) на ВиК проект в България.\n\n"
            "ЗАДАЧА: Извлечи САМО имената на улиците, булевардите, кварталите, жилищните "
            "комплекси, местностите и топонимите, видими на чертежа.\n\n"
            "КАК ДА ТЪРСИШ:\n"
            "- Имената на улиците са НАПИСАНИ ПО ОСТА на улицата — завъртян текст по "
            "посоката на улицата. Търси такъв завъртян/наклонен текст.\n"
            "- НА КРЪСТОПЪТИ има ДВЕ пресичащи се улици с перпендикулярни надписи — "
            "извлечи И ДВЕТЕ.\n"
            "- Стрелките 'посока на отвеждане' (→) могат да съдържат 'към ул. X' или "
            "'към ПСОВ X' — извлечи само топонима X, без 'към'.\n"
            "- Стандартни съкращения: ул., бул., кв., ж.к., м. — запази ги в резултата.\n\n"
            "НЕ ВКЛЮЧВАЙ: числа, координати, диаметри (DN, Ф), коти, дати, "
            "имена на фирми/проектанти, 'ПСОВ' самостоятелно без топоним.\n\n"
            "Отговори САМО с валиден JSON:\n"
            '{"locations": ["ул. Примерна", "бул. Витоша", "кв. Лозенец", "ж.к. Надежда"]}'
        )

        all_locations: list[str] = []
        doc = fitz.open(stream=_pdf_bytes, filetype="pdf")
        num_pages = len(doc)

        _MAX_BYTES = 4 * 1024 * 1024  # 4 MB — Anthropic hard limit is 5 MB

        for page_num in range(num_pages):
            page = doc[page_num]
            # Start at 100 dpi; if image is still too large drop to 72 then 50
            img_bytes = b""
            for dpi in (100, 72, 50):
                pix = page.get_pixmap(dpi=dpi)
                # JPEG is far smaller than PNG for large-format CAD drawings
                img_bytes = pix.tobytes("jpeg", jpg_quality=85)
                if len(img_bytes) <= _MAX_BYTES:
                    break
                logger.debug(
                    "Page %d at %d dpi → %d bytes, retrying at lower dpi",
                    page_num + 1, dpi, len(img_bytes),
                )
            if len(img_bytes) > _MAX_BYTES:
                logger.warning(
                    "Situation page %d still >4 MB after lowest dpi — skipping.", page_num + 1
                )
                continue
            b64_image = base64.b64encode(img_bytes).decode("ascii")

            try:
                raw = self.router.ocr_pdf_page(b64_image, system_prompt=situation_prompt, media_type="image/jpeg")
                # Strip markdown fences if present
                cleaned = raw.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
                    if cleaned.endswith("```"):
                        cleaned = cleaned[:-3].strip()
                parsed = json.loads(cleaned)
                page_locs = parsed.get("locations", [])
                if isinstance(page_locs, list):
                    all_locations.extend(str(loc) for loc in page_locs if loc)
            except Exception as exc:
                logger.warning(
                    "Situation location extraction failed on page %d of %s: %s",
                    page_num + 1, source_name, exc,
                )

        doc.close()

        # De-duplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for loc in all_locations:
            key = loc.strip().lower()
            if key and key not in seen:
                seen.add(key)
                unique.append(loc.strip())

        logger.info(
            "Situation '%s': extracted %d unique locations", source_name, len(unique)
        )
        return unique

    # ------------------------------------------------------------------
    # Legacy compatibility methods
    # ------------------------------------------------------------------

    def process_documents(self, files: list[dict], project_type: str) -> dict:
        """Legacy method — delegates to analyze_documents."""
        return self.analyze_documents(files)

    def generate_schedule_legacy(self, analysis: dict, config: dict) -> dict:
        """Legacy method — delegates to generate_schedule."""
        return self.generate_schedule(analysis, config.get("project_type", ""))

    def chat(self, messages: list[dict], system_prompt: str) -> str:
        """Legacy chat method — returns just the content string."""
        result = self.chat_response(messages)
        return result.get("content", "")

    def ask_clarification(self, context: str, question: str) -> str:
        """Legacy method for clarification questions."""
        return f"Уточняващ въпрос: {question}"
