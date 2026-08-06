"""Knowledge manager for the 3-tier knowledge system (Lessons, Methodologies, Skills).

Supports AI-verified lesson saving via AIRouter (Anthropic controller).
Includes cached knowledge loading and multi-level prompt building for DeepSeek.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.ai_router import AIRouter

logger = logging.getLogger(__name__)

# Колко знака уроци се събират в един промпт.  Под този таван влизат ВСИЧКИ
# уроци — при ~45 урока извличането е излишно усложнение.  Над него се
# подрежда по релевантност, за да не расте промптът безкрайно (P3).
_LESSONS_CHAR_BUDGET = 14000

# Дължина на префикса, по който се сравняват думи.  Българският е силно
# флектиран — „дезинфекция/дезинфекцията/дезинфекциите" трябва да съвпадат,
# без да влачим морфологичен анализатор.
_STEM_LEN = 5
_MIN_TOKEN_LEN = 3

# Думи без различаваща сила — изхвърлят се от заявката и от индекса.
_STOPWORDS = frozenset({
    "the", "and", "for", "with",
    "или", "като", "него", "нея", "тях", "този", "тази", "това", "тези",
    "който", "която", "което", "които", "при", "след", "преди", "върху",
    "между", "над", "под", "без", "със", "все", "още", "само", "също",
    "може", "трябва", "има", "няма", "бъде", "били", "беше", "став",
    "ако", "защото", "затова", "така", "тогава", "много", "малко",
    "проект", "проекта", "проекти", "дейност", "дейности",
})

_LESSON_NUM_RE = re.compile(r"#(\d+)")
_WORD_RE = re.compile(r"[\wА-Яа-яЁё]+", re.UNICODE)


def _parse_lesson_number(title: str) -> int:
    """Извлечи номера на урока от заглавието (0, ако липсва)."""
    match = _LESSON_NUM_RE.search(title)
    return int(match.group(1)) if match else 0


def _tokenize(text: str) -> list[str]:
    """Разбий текст на нормализирани основи за лексикално сравнение."""
    tokens = []
    for raw in _WORD_RE.findall(text.lower()):
        if len(raw) < _MIN_TOKEN_LEN or raw in _STOPWORDS:
            continue
        tokens.append(raw[:_STEM_LEN])
    return tokens


def rank_lessons(blocks: list[dict], query: str) -> list[tuple[float, dict]]:
    """Подреди уроци по лексикална близост до заявката (TF-IDF, насищащ TF).

    Умишлено БЕЗ embeddings: корпусът е десетки кратки текста на български,
    лексикалното съвпадение се справя, а резултатът остава детерминистичен,
    тестваем и без мрежова заявка при всяко генериране.

    Args:
        blocks: Уроци от `get_lesson_blocks()`.
        query: Свободен текст — тип проект, анализ, съдържание на документи.

    Returns:
        Списък от (резултат, урок), най-релевантните първи.  При празна
        заявка всички получават резултат 0.0 и редът се запазва.
    """
    if not blocks:
        return []

    query_stems = set(_tokenize(query))
    if not query_stems:
        return [(0.0, block) for block in blocks]

    doc_tokens = [_tokenize(block.get("text", "")) for block in blocks]

    # Документна честота за всяка основа.
    doc_freq: dict[str, int] = {}
    for tokens in doc_tokens:
        for stem in set(tokens):
            doc_freq[stem] = doc_freq.get(stem, 0) + 1

    total = len(blocks)
    scored: list[tuple[float, dict]] = []

    for block, tokens in zip(blocks, doc_tokens):
        counts: dict[str, int] = {}
        for stem in tokens:
            counts[stem] = counts.get(stem, 0) + 1

        score = 0.0
        for stem in query_stems:
            tf = counts.get(stem, 0)
            if not tf:
                continue
            idf = math.log(1 + total / doc_freq.get(stem, 1))
            # Насищане: десетото повторение не тежи колкото първото.
            score += idf * (tf / (tf + 1.5))
        scored.append((score, block))

    # Стабилно подреждане: при равен резултат печели по-новият урок.
    scored.sort(key=lambda pair: (-pair[0], -pair[1].get("number", 0)))
    return scored


def select_lessons(
    blocks: list[dict], query: str = "", char_budget: int = _LESSONS_CHAR_BUDGET
) -> list[dict]:
    """Избери уроците, които влизат в промпта, в рамките на бюджет знаци.

    Ако всички се събират — влизат всички (в реда от файла).  Ако не се
    събират, подрежда по релевантност и взима най-подходящите, после ги
    връща отново в реда от файла, за да е четим промптът.
    """
    if not blocks:
        return []

    total_chars = sum(len(b.get("text", "")) for b in blocks)
    if total_chars <= char_budget:
        return list(blocks)

    chosen: list[dict] = []
    used = 0
    for _score, block in rank_lessons(blocks, query):
        size = len(block.get("text", ""))
        if used + size > char_budget:
            continue
        chosen.append(block)
        used += size

    chosen.sort(key=lambda b: b.get("number", 0))
    return chosen


class KnowledgeManager:
    """Manages the 3-tier knowledge base: Lessons -> Methodologies -> Skills."""

    def __init__(self, knowledge_path: str) -> None:
        """Initialize the knowledge manager.

        Args:
            knowledge_path: Path to the knowledge/ directory.
        """
        self.knowledge_path = Path(knowledge_path)
        self.lessons_path = self.knowledge_path / "lessons"
        self.methodologies_path = self.knowledge_path / "methodologies"
        self.skills_path = self.knowledge_path / "skills"

        # Cache for knowledge content — avoids re-reading files every call
        self._knowledge_cache: dict[str, str] = {}
        self._cache_timestamps: dict[str, float] = {}

        # Path to productivities.json (sibling of knowledge/ dir)
        self._productivities_path = self.knowledge_path.parent / "config" / "productivities.json"

    # ------------------------------------------------------------------
    # Cached file reading
    # ------------------------------------------------------------------

    def _read_cached(self, filepath: Path) -> str:
        """Read file with timestamp-based caching.

        Returns cached content if file hasn't changed since last read.
        """
        key = str(filepath)
        if not filepath.exists():
            self._knowledge_cache.pop(key, None)
            self._cache_timestamps.pop(key, None)
            return ""

        current_mtime = filepath.stat().st_mtime
        cached_mtime = self._cache_timestamps.get(key, 0)

        if key in self._knowledge_cache and current_mtime == cached_mtime:
            return self._knowledge_cache[key]

        # File changed or not cached — read it
        try:
            content = filepath.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to read %s: %s", filepath, exc)
            return ""

        self._knowledge_cache[key] = content
        self._cache_timestamps[key] = current_mtime
        return content

    def invalidate_cache(self) -> None:
        """Force re-read of all cached files on next access."""
        self._knowledge_cache.clear()
        self._cache_timestamps.clear()

    # ------------------------------------------------------------------
    # Lessons
    # ------------------------------------------------------------------

    def get_lessons(self) -> list[str]:
        """Read all lesson TITLES from lessons_learned.md.

        Внимание: връща само заглавните редове — за броене и за списъци в UI.
        За промптове ползвай `get_lesson_blocks()`, което носи и тялото на
        урока (там са числата и причините).

        Returns:
            List of lesson title strings.
        """
        filepath = self.lessons_path / "lessons_learned.md"
        content = self._read_cached(filepath)
        if not content:
            return []

        lessons = []
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("**#"):
                lessons.append(stripped)
        return lessons

    def get_lesson_blocks(self) -> list[dict]:
        """Read lessons as FULL blocks — заглавие + тяло + раздел.

        ЗАЩО (P3 от REVISION_2026-07.md): досега в промпта влизаха само
        заглавията, и то последните 20 по ред във файла.  Тоест генераторът
        получаваше „#26 PowerShell -STA флаг" и „#27 pre-commit hook", а
        губеше #09–#17 — дезинфекция per section, теренни фактори, CI vs PE.
        Знанието за домейна отпадаше, а бележките за разработчика оставаха.

        Returns:
            Списък от {number, title, body, section, text}, подреден по
            реда във файла.  `text` е заглавие + тяло (за търсене).
        """
        filepath = self.lessons_path / "lessons_learned.md"
        content = self._read_cached(filepath)
        if not content:
            return []

        blocks: list[dict] = []
        section = ""
        current: dict | None = None

        for line in content.split("\n"):
            stripped = line.strip()

            if stripped.startswith("## "):
                heading = stripped[3:].strip()
                # „Формат"/„РАЗДЕЛ А: ..." — пази само реалните раздели.
                if heading.lower() != "формат":
                    section = heading
                continue

            if stripped.startswith("**#"):
                if current:
                    blocks.append(current)
                title = stripped.strip("*").strip()
                number = _parse_lesson_number(title)
                current = {
                    "number": number,
                    "title": title,
                    "body": "",
                    "section": section,
                    "lines": [],
                }
                continue

            if current is not None:
                if stripped == "---":
                    blocks.append(current)
                    current = None
                elif stripped:
                    current["lines"].append(stripped)

        if current:
            blocks.append(current)

        for block in blocks:
            block["body"] = "\n".join(block.pop("lines")).strip()
            block["text"] = f"{block['title']}\n{block['body']}".strip()

        return blocks

    def add_lesson(self, lesson: str) -> None:
        """Add a new lesson to the pending lessons file.

        Args:
            lesson: The lesson text to add.
        """
        filepath = self.lessons_path / "pending_lessons.md"
        if not filepath.exists():
            filepath.write_text(
                "# Нови уроци за преглед\n\n", encoding="utf-8"
            )

        content = filepath.read_text(encoding="utf-8")
        content += f"\n- {lesson}"
        filepath.write_text(content, encoding="utf-8")
        # Invalidate cache for this file
        self._knowledge_cache.pop(str(filepath), None)
        self._cache_timestamps.pop(str(filepath), None)

    def get_pending_lessons(self) -> list[str]:
        """Read pending lessons awaiting user approval.

        Returns:
            List of pending lesson strings.
        """
        filepath = self.lessons_path / "pending_lessons.md"
        content = self._read_cached(filepath)
        if not content:
            return []

        lessons = []
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("- ") and len(stripped) > 2:
                lessons.append(stripped[2:])
        return lessons

    def approve_lesson(self, lesson: str) -> None:
        """Move a lesson from pending to approved.

        Args:
            lesson: The lesson text to approve.
        """
        # Read current lessons to determine next number
        current_lessons = self.get_lessons()
        next_num = len(current_lessons) + 1

        # Add to lessons_learned.md
        learned_path = self.lessons_path / "lessons_learned.md"
        if learned_path.exists():
            content = learned_path.read_text(encoding="utf-8")
        else:
            content = "# Научени уроци\n"
        content += f"\n**#{next_num}**: {lesson}"
        learned_path.write_text(content, encoding="utf-8")

        # Remove from pending
        pending_path = self.lessons_path / "pending_lessons.md"
        if pending_path.exists():
            pending_content = pending_path.read_text(encoding="utf-8")
            pending_content = pending_content.replace(f"\n- {lesson}", "")
            pending_path.write_text(pending_content, encoding="utf-8")

        # Invalidate affected caches
        self._knowledge_cache.pop(str(learned_path), None)
        self._cache_timestamps.pop(str(learned_path), None)
        self._knowledge_cache.pop(str(pending_path), None)
        self._cache_timestamps.pop(str(pending_path), None)

    # ------------------------------------------------------------------
    # Methodology
    # ------------------------------------------------------------------

    # Типът, който АНАЛИЗЪТ връща, е на БЪЛГАРСКИ — точно както промптът го
    # иска ('разпределителна мрежа', 'довеждащ', 'единичен', 'инженеринг').
    # Файловете с методологии са с английски ключове.  ЖИВ ПРОГОН 2026-08-06:
    # реалният търг се класифицира като 'инженеринг' и в промпта влизаше
    # „Unknown project type: инженеринг" ВМЕСТО методологията — тихо, без
    # никакво съобщение.  Тук двата речника се срещат.
    _TYPE_ALIASES = {
        "разпределителна мрежа": "distribution",
        "разпределителна": "distribution",
        "distribution": "distribution",
        "довеждащ": "supply",
        "довеждащ водопровод": "supply",
        "supply": "supply",
        "supply_pipeline": "supply",
        "единичен": "single",
        "единичен участък": "single",
        "single": "single",
        "single_section": "single",
        "инженеринг": "engineering",
        "engineering": "engineering",
    }

    @classmethod
    def canonical_type(cls, project_type: str | None) -> str:
        """Каноничният ключ на типа проект, или „" ако е непознат."""
        key = str(project_type or "").strip().lower()
        return cls._TYPE_ALIASES.get(key, "")

    def get_methodology(self, project_type: str) -> str:
        """Get methodology content for a project type.

        Args:
            project_type: Каноничен ключ ('engineering', 'distribution',
                'supply', 'single') ИЛИ българското име от анализа
                ('инженеринг', 'разпределителна мрежа', 'довеждащ', 'единичен').

        Returns:
            Methodology content as string.
        """
        type_map = {
            "engineering": "engineering_projects.md",
            "distribution": "distribution_network.md",
            "supply": "supply_pipeline.md",
            "single": "single_section.md",
        }

        filename = type_map.get(self.canonical_type(project_type))
        if not filename:
            return f"Unknown project type: {project_type}"

        filepath = self.methodologies_path / filename
        content = self._read_cached(filepath)
        if not content:
            return f"Methodology for '{project_type}' not found."
        return content

    def update_methodology(self, project_type: str, content: str) -> None:
        """Update methodology file for a project type.

        Args:
            project_type: One of 'engineering', 'distribution', 'supply', 'single'.
            content: New methodology content.
        """
        type_map = {
            "engineering": "engineering_projects.md",
            "distribution": "distribution_network.md",
            "supply": "supply_pipeline.md",
            "single": "single_section.md",
        }

        filename = type_map.get(project_type)
        if not filename:
            return

        filepath = self.methodologies_path / filename
        filepath.write_text(content, encoding="utf-8")
        self._knowledge_cache.pop(str(filepath), None)
        self._cache_timestamps.pop(str(filepath), None)

    # ------------------------------------------------------------------
    # Skills
    # ------------------------------------------------------------------

    def get_skills(self) -> str:
        """Read the main SKILL.md file.

        Returns:
            Full SKILL.md content.
        """
        filepath = self.skills_path / "SKILL.md"
        return self._read_cached(filepath)

    # ------------------------------------------------------------------
    # Productivities
    # ------------------------------------------------------------------

    def get_productivities(self) -> str:
        """Read productivities.json as formatted text.

        Returns:
            Productivities JSON content as string.
        """
        content = self._read_cached(self._productivities_path)
        if not content:
            return ""
        return content

    # ------------------------------------------------------------------
    # Workflow rules
    # ------------------------------------------------------------------

    def get_workflow_rules(self) -> str:
        """Read workflow-rules.md from skill references.

        Returns:
            Workflow rules content.
        """
        filepath = self.skills_path / "references" / "workflow-rules.md"
        return self._read_cached(filepath)

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_knowledge_stats(self) -> dict:
        """Get statistics about the knowledge base.

        Returns:
            Dict with counts: lessons, pending, methodologies, skills_refs.
        """
        lessons_count = len(self.get_lessons())
        pending_count = len(self.get_pending_lessons())

        # Count methodology files (excluding README)
        methodology_count = 0
        if self.methodologies_path.exists():
            methodology_count = sum(
                1
                for f in self.methodologies_path.glob("*.md")
                if f.name != "README.md"
            )

        # Count skill reference files
        refs_path = self.skills_path / "references"
        refs_count = 0
        if refs_path.exists():
            refs_count = sum(1 for _ in refs_path.glob("*.md"))

        return {
            "lessons": lessons_count,
            "pending": pending_count,
            "methodologies": methodology_count,
            "skill_references": refs_count,
        }

    # ------------------------------------------------------------------
    # Multi-level system prompt builders
    # ------------------------------------------------------------------

    def build_system_prompt(
        self, project_type: str | None = None, query: str = ""
    ) -> str:
        """Build a FULL system prompt combining all knowledge tiers.

        Includes: SKILL.md + references + methodology + relevant lessons
        (пълни, не само заглавия) + productivities.  ~5000-8000 tokens.

        Args:
            project_type: Optional project type to include specific methodology.
            query: Свободен текст (анализ, съдържание на документи), по който
                се подбират най-релевантните уроци, ако всички не се събират.

        Returns:
            Combined system prompt string for AI.
        """
        return self.get_all_knowledge_for_prompt(
            project_type=project_type, level="full", query=query
        )

    def get_all_knowledge_for_prompt(
        self,
        project_type: str | None = None,
        level: str = "full",
        query: str = "",
    ) -> str:
        """Collect all knowledge into a single text for system prompt.

        Args:
            project_type: Optional project type for methodology inclusion.
            level: One of 'minimal', 'full', 'verification'.
                - minimal: Core rules + productivities (~1500-2000 tokens)
                - full: SKILL.md + methodology + relevant lessons + productivities
                  + workflow (~5000-8000 tokens)
                - verification: Everything including ALL lessons (~8000-12000 tokens)
            query: Текст за подбор на уроци по релевантност (level='full').

        Returns:
            Combined knowledge text.
        """
        if level == "minimal":
            return self._build_minimal_prompt()
        elif level == "verification":
            return self._build_verification_knowledge(project_type)
        else:
            return self._build_full_prompt(project_type, query=query)

    def _lessons_section(self, query: str) -> list[str]:
        """Изгради секцията с уроци за промпта (пълни блокове, подбрани)."""
        blocks = self.get_lesson_blocks()
        if not blocks:
            return []

        selected = select_lessons(blocks, query)
        parts = ["\n=== LESSONS LEARNED ==="]
        if len(selected) < len(blocks):
            parts.append(
                f"Total lessons: {len(blocks)} "
                f"(показани {len(selected)} най-релевантни)"
            )
        else:
            parts.append(f"Total lessons: {len(blocks)}")
        parts.extend(block["text"] for block in selected)
        return parts

    def _build_minimal_prompt(self) -> str:
        """Build minimal knowledge prompt for lightweight tasks (OCR, simple questions).

        Includes ONLY: core rules summary + productivities.
        ~1500-2000 tokens.
        """
        parts = [
            "=== CORE RULES ===",
            "You are an assistant for construction schedules (linear Gantt charts) "
            "for water and sewage (ViK) infrastructure projects in Bulgaria.",
            "Respond in Bulgarian. Follow the rules for generating linear schedules.",
            "",
            "Key rules:",
            "- Rule #0: Convert ALL documents to JSON BEFORE analysis",
            "- 7-day calendar, FS dependencies",
            "- Water supply BEFORE sewage; Sewage BOTTOM-UP",
            "- Disinfection: 2d (DN90-110 short), 4d (mixed/DN500), 6d (DN300 CI)",
            "- Testing: 2 days (strength + pressure drop)",
            "- Durations are computed deterministically by the system from "
            "config/productivities.json — do NOT calculate them yourself; "
            "supply length_m, dn, material and method instead",
            "- Rolling Wave: Water -> Sewage -> Roads with 10-12d LAG",
        ]

        # Add productivities
        prod = self.get_productivities()
        if prod:
            parts.append("\n=== PRODUCTIVITIES ===")
            parts.append(prod)

        return "\n".join(parts)

    def _build_full_prompt(
        self, project_type: str | None = None, query: str = ""
    ) -> str:
        """Build full knowledge prompt for generation and analysis tasks.

        Includes: SKILL.md + methodology + relevant lessons (пълни блокове)
        + productivities + workflow rules.  ~5000-8000 tokens.
        """
        parts = []

        # Tier 1: Skills (core rules)
        skills = self.get_skills()
        if skills:
            parts.append("=== SKILLS (Core Rules) ===")
            parts.append(skills)

        # Load skill references
        refs_path = self.skills_path / "references"
        if refs_path.exists():
            for ref_file in sorted(refs_path.glob("*.md")):
                ref_content = self._read_cached(ref_file)
                if ref_content:
                    parts.append(f"\n--- {ref_file.stem} ---")
                    parts.append(ref_content)

        # Tier 2: Methodology for specific project type
        #
        # Непознат тип НЕ влиза в промпта (одит на живия прогон 2026-08-06):
        # досега там отиваше низът „Unknown project type: X" и моделът четеше
        # СОБСТВЕНАТА си грешка като методология.  По-добре секцията да липсва,
        # а логът да каже, че методология не е приложена.
        if project_type:
            canonical = self.canonical_type(project_type)
            if canonical:
                parts.append(f"\n=== METHODOLOGY ({canonical}) ===")
                parts.append(self.get_methodology(canonical))
            else:
                logger.warning(
                    "Непознат тип проект '%s' — промптът остава БЕЗ методология.",
                    project_type,
                )

        # Tier 3: Lessons learned — пълни блокове, подбрани по релевантност
        parts.extend(self._lessons_section(f"{project_type or ''} {query}"))

        # Tier 4: Productivities
        prod = self.get_productivities()
        if prod:
            parts.append("\n=== PRODUCTIVITIES (config/productivities.json) ===")
            parts.append(prod)

        # Tier 5: Workflow rules
        workflow = self.get_workflow_rules()
        if workflow:
            parts.append("\n=== WORKFLOW RULES ===")
            parts.append(workflow)

        return "\n\n".join(parts)

    def _build_verification_knowledge(self, project_type: str | None = None) -> str:
        """Build comprehensive knowledge for verification tasks.

        Includes EVERYTHING: SKILL.md + workflow + ALL lessons.
        ~8000-12000 tokens. Suitable for Anthropic controller.
        """
        parts = []

        # Full skills
        skills = self.get_skills()
        if skills:
            parts.append("=== SKILLS (Core Rules) ===")
            parts.append(skills)

        # All references
        refs_path = self.skills_path / "references"
        if refs_path.exists():
            for ref_file in sorted(refs_path.glob("*.md")):
                ref_content = self._read_cached(ref_file)
                if ref_content:
                    parts.append(f"\n--- {ref_file.stem} ---")
                    parts.append(ref_content)

        # Methodology — непознат тип не влиза (виж `_build_full_prompt`).
        if project_type:
            canonical = self.canonical_type(project_type)
            if canonical:
                parts.append(f"\n=== METHODOLOGY ({canonical}) ===")
                parts.append(self.get_methodology(canonical))
            else:
                logger.warning(
                    "Непознат тип проект '%s' — проверката остава БЕЗ методология.",
                    project_type,
                )

        # ALL lessons — с телата им, не само заглавията
        blocks = self.get_lesson_blocks()
        if blocks:
            parts.append("\n=== ALL LESSONS LEARNED ===")
            parts.append(f"Total lessons: {len(blocks)}")
            for block in blocks:
                parts.append(block["text"])

        # Productivities
        prod = self.get_productivities()
        if prod:
            parts.append("\n=== PRODUCTIVITIES ===")
            parts.append(prod)

        # Workflow rules
        workflow = self.get_workflow_rules()
        if workflow:
            parts.append("\n=== WORKFLOW RULES ===")
            parts.append(workflow)

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # AI-verified lesson saving
    # ------------------------------------------------------------------

    def add_lesson_with_verification(
        self, lesson: str, router: AIRouter, context: str = ""
    ) -> dict:
        """Save a new lesson after verification by the controller (Anthropic).

        The controller checks that the lesson is clearly formulated and
        does not contradict existing lessons.

        Args:
            lesson: The new lesson text.
            router: AIRouter instance for AI verification.
            context: Context about when/why this lesson was learned.

        Returns:
            Dict with saved, file, formatted, feedback.
        """
        # Get existing lessons for context
        existing_lessons = self.get_lessons()
        existing_summary = "\n".join(existing_lessons[-20:]) if existing_lessons else ""

        # Verify via AI controller
        result = router.save_lesson(lesson, context, existing_summary)

        if result["approved"]:
            formatted = result["formatted_lesson"]

            # Add to approved lessons
            next_num = len(existing_lessons) + 1
            learned_path = self.lessons_path / "lessons_learned.md"

            if learned_path.exists():
                content = learned_path.read_text(encoding="utf-8")
            else:
                content = "# Научени уроци\n"

            content += f"\n**#{next_num}**: {formatted}"
            learned_path.write_text(content, encoding="utf-8")

            # Invalidate cache
            self._knowledge_cache.pop(str(learned_path), None)
            self._cache_timestamps.pop(str(learned_path), None)

            logger.info("Lesson #%d saved: %s", next_num, formatted[:80])

            return {
                "saved": True,
                "file": str(learned_path),
                "formatted": formatted,
                "feedback": result["reason"],
                "model": result.get("model", "unknown"),
            }

        # Not approved — save to pending with feedback
        pending_path = self.lessons_path / "pending_lessons.md"
        if not pending_path.exists():
            pending_path.write_text("# Нови уроци за преглед\n\n", encoding="utf-8")

        pending_content = pending_path.read_text(encoding="utf-8")
        pending_content += f"\n- {lesson} (REJECTED: {result['reason']})"
        pending_path.write_text(pending_content, encoding="utf-8")

        # Invalidate cache
        self._knowledge_cache.pop(str(pending_path), None)
        self._cache_timestamps.pop(str(pending_path), None)

        logger.info("Lesson rejected: %s — %s", lesson[:80], result["reason"])

        return {
            "saved": False,
            "file": str(pending_path),
            "formatted": result["formatted_lesson"],
            "feedback": result["reason"],
            "model": result.get("model", "unknown"),
        }
