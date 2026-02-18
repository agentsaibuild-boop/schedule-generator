"""Knowledge manager for the 3-tier knowledge system (Lessons, Methodologies, Skills).

Supports AI-verified lesson saving via AIRouter (Anthropic controller).
Includes cached knowledge loading and multi-level prompt building for DeepSeek.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.ai_router import AIRouter

logger = logging.getLogger(__name__)


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
        """Read all learned lessons from lessons_learned.md.

        Returns:
            List of lesson strings.
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
        content = learned_path.read_text(encoding="utf-8")
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

    def get_methodology(self, project_type: str) -> str:
        """Get methodology content for a project type.

        Args:
            project_type: One of 'engineering', 'distribution', 'supply', 'single'.

        Returns:
            Methodology content as string.
        """
        type_map = {
            "engineering": "engineering_projects.md",
            "distribution": "distribution_network.md",
            "supply": "supply_pipeline.md",
            "single": "single_section.md",
        }

        filename = type_map.get(project_type)
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

    def build_system_prompt(self, project_type: str | None = None) -> str:
        """Build a FULL system prompt combining all knowledge tiers.

        Includes: SKILL.md + references + methodology + last 20 lessons + productivities.
        ~5000-8000 tokens. Use for schedule generation and document analysis.

        Args:
            project_type: Optional project type to include specific methodology.

        Returns:
            Combined system prompt string for AI.
        """
        return self.get_all_knowledge_for_prompt(
            project_type=project_type, level="full"
        )

    def get_all_knowledge_for_prompt(
        self,
        project_type: str | None = None,
        level: str = "full",
    ) -> str:
        """Collect all knowledge into a single text for system prompt.

        Args:
            project_type: Optional project type for methodology inclusion.
            level: One of 'minimal', 'full', 'verification'.
                - minimal: Core rules + productivities (~1500-2000 tokens)
                - full: SKILL.md + methodology + 20 lessons + productivities + workflow (~5000-8000 tokens)
                - verification: Everything including ALL lessons (~8000-12000 tokens)

        Returns:
            Combined knowledge text.
        """
        if level == "minimal":
            return self._build_minimal_prompt()
        elif level == "verification":
            return self._build_verification_knowledge(project_type)
        else:
            return self._build_full_prompt(project_type)

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
            "- days = ceil(length_m / productivity_rate)",
            "- Rolling Wave: Water -> Sewage -> Roads with 10-12d LAG",
        ]

        # Add productivities
        prod = self.get_productivities()
        if prod:
            parts.append("\n=== PRODUCTIVITIES ===")
            parts.append(prod)

        return "\n".join(parts)

    def _build_full_prompt(self, project_type: str | None = None) -> str:
        """Build full knowledge prompt for generation and analysis tasks.

        Includes: SKILL.md + methodology + last 20 lessons + productivities + workflow rules.
        ~5000-8000 tokens.
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
        if project_type:
            methodology = self.get_methodology(project_type)
            parts.append(f"\n=== METHODOLOGY ({project_type}) ===")
            parts.append(methodology)

        # Tier 3: Lessons learned (last 20)
        lessons = self.get_lessons()
        if lessons:
            parts.append("\n=== LESSONS LEARNED ===")
            parts.append(f"Total lessons: {len(lessons)}")
            recent = lessons[-20:]
            for lesson in recent:
                parts.append(lesson)

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

        # Methodology
        if project_type:
            methodology = self.get_methodology(project_type)
            parts.append(f"\n=== METHODOLOGY ({project_type}) ===")
            parts.append(methodology)

        # ALL lessons (not just last 20)
        lessons = self.get_lessons()
        if lessons:
            parts.append("\n=== ALL LESSONS LEARNED ===")
            parts.append(f"Total lessons: {len(lessons)}")
            for lesson in lessons:
                parts.append(lesson)

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
