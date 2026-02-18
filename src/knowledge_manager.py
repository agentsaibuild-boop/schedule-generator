"""Knowledge manager for the 3-tier knowledge system (Lessons, Methodologies, Skills)."""

import os
from pathlib import Path


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

    def get_lessons(self) -> list[str]:
        """Read all learned lessons from lessons_learned.md.

        Returns:
            List of lesson strings.
        """
        filepath = self.lessons_path / "lessons_learned.md"
        if not filepath.exists():
            return []

        content = filepath.read_text(encoding="utf-8")
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

    def get_pending_lessons(self) -> list[str]:
        """Read pending lessons awaiting user approval.

        Returns:
            List of pending lesson strings.
        """
        filepath = self.lessons_path / "pending_lessons.md"
        if not filepath.exists():
            return []

        content = filepath.read_text(encoding="utf-8")
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
            return f"Непознат тип проект: {project_type}"

        filepath = self.methodologies_path / filename
        if not filepath.exists():
            return f"Методиката за '{project_type}' не е намерена."

        return filepath.read_text(encoding="utf-8")

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

    def get_skills(self) -> str:
        """Read the main SKILL.md file.

        Returns:
            Full SKILL.md content.
        """
        filepath = self.skills_path / "SKILL.md"
        if not filepath.exists():
            return ""
        return filepath.read_text(encoding="utf-8")

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

    def build_system_prompt(self, project_type: str | None = None) -> str:
        """Build a system prompt combining all knowledge tiers.

        Args:
            project_type: Optional project type to include specific methodology.

        Returns:
            Combined system prompt string for AI.
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
                ref_content = ref_file.read_text(encoding="utf-8")
                parts.append(f"\n--- {ref_file.stem} ---")
                parts.append(ref_content)

        # Tier 2: Methodology for specific project type
        if project_type:
            methodology = self.get_methodology(project_type)
            parts.append(f"\n=== METHODOLOGY ({project_type}) ===")
            parts.append(methodology)

        # Tier 3: Lessons learned (summarized)
        lessons = self.get_lessons()
        if lessons:
            parts.append("\n=== LESSONS LEARNED ===")
            parts.append(f"Total lessons: {len(lessons)}")
            # Include last 20 lessons for context
            recent = lessons[-20:]
            for lesson in recent:
                parts.append(lesson)

        return "\n\n".join(parts)
