"""Automatic documentation updater — keeps README, CHANGELOG, and ARCHITECTURE in sync.

Tracks code changes via git diff and updates only the sections marked with
HTML comment markers (e.g. <!-- FILE_TREE_START --> ... <!-- FILE_TREE_END -->).
Manual edits outside these markers are preserved.

If git is not available, the updater skips diff-based checks and only runs
on-demand updates without crashing.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Directories and files to ignore when building the file tree
IGNORE_PATTERNS = {
    "venv", "__pycache__", ".git", ".env", ".env.company",
    "node_modules", ".mypy_cache", ".pytest_cache", "converted",
    "config/projects_history.json",
}

# Mapping: trigger file glob → which doc sections to update
DEFAULT_CONFIG: dict[str, dict[str, Any]] = {
    "README.md": {
        "sections_to_update": ["file_tree", "dependencies", "version"],
        "triggers": ["requirements.txt", "src/*.py", "app.py"],
    },
    "CHANGELOG.md": {
        "sections_to_update": ["latest_version"],
        "triggers": ["*.py", "*.bat", "*.toml"],
    },
    "docs/ARCHITECTURE.md": {
        "sections_to_update": ["components", "dependencies"],
        "triggers": ["src/*.py"],
    },
}


class DocsUpdater:
    """Automatically updates project documentation when code changes."""

    def __init__(self, app_root: str) -> None:
        self.app_root = Path(app_root)
        self.docs_config = self._load_config()

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def _load_config(self) -> dict[str, dict[str, Any]]:
        """Load the auto-update configuration.

        Returns a dict mapping doc file paths to their tracked sections
        and trigger patterns.
        """
        config_path = self.app_root / "config" / "docs_update_config.json"
        if config_path.exists():
            try:
                return json.loads(config_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                logger.warning("Failed to read docs update config, using defaults")
        return DEFAULT_CONFIG

    # ------------------------------------------------------------------
    # Git helpers
    # ------------------------------------------------------------------

    def _git_available(self) -> bool:
        """Check whether git is accessible."""
        try:
            subprocess.run(
                ["git", "status"],
                cwd=self.app_root,
                capture_output=True,
                timeout=5,
            )
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False

    def _git_changed_files(self) -> list[str]:
        """Return files changed since the last commit (or all tracked files)."""
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD~1"],
                cwd=self.app_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return [f.strip() for f in result.stdout.splitlines() if f.strip()]

            # Fallback: list all tracked files (fresh repo with only 1 commit)
            result = subprocess.run(
                ["git", "ls-files"],
                cwd=self.app_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return [f.strip() for f in result.stdout.splitlines() if f.strip()]
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return []

    # ------------------------------------------------------------------
    # Check what needs updating
    # ------------------------------------------------------------------

    def _matches_trigger(self, changed_file: str, trigger_pattern: str) -> bool:
        """Check if a changed file matches a trigger glob pattern."""
        from fnmatch import fnmatch

        changed_file = changed_file.replace("\\", "/")
        return fnmatch(changed_file, trigger_pattern)

    def check_for_updates(self) -> list[dict[str, Any]]:
        """Check whether any documentation needs updating.

        Returns a list of dicts:
        [{"doc": "README.md", "reason": "requirements.txt changed", "sections": [...]}]
        """
        if not self._git_available():
            return []

        changed_files = self._git_changed_files()
        if not changed_files:
            return []

        updates: list[dict[str, Any]] = []

        for doc_path, cfg in self.docs_config.items():
            triggers = cfg.get("triggers", [])
            sections = cfg.get("sections_to_update", [])
            matched_triggers: list[str] = []

            for changed in changed_files:
                for trigger in triggers:
                    if self._matches_trigger(changed, trigger):
                        matched_triggers.append(changed)
                        break

            if matched_triggers:
                reason = ", ".join(matched_triggers[:3])
                if len(matched_triggers) > 3:
                    reason += f" (+{len(matched_triggers) - 3} more)"
                updates.append({
                    "doc": doc_path,
                    "reason": reason,
                    "sections": sections,
                })

        return updates

    # ------------------------------------------------------------------
    # README updaters
    # ------------------------------------------------------------------

    def update_readme_file_tree(self) -> bool:
        """Update the file tree section in README.md.

        Scans the current directory structure, ignoring venv/__pycache__/.git etc,
        and replaces content between <!-- FILE_TREE_START --> and <!-- FILE_TREE_END -->.
        """
        readme_path = self.app_root / "README.md"
        if not readme_path.exists():
            return False

        tree = self._build_file_tree(self.app_root, prefix="", max_depth=3)
        tree_block = "```\nschedule-generator/\n" + tree + "```"

        return self._replace_section(readme_path, "FILE_TREE", tree_block)

    def update_readme_dependencies(self) -> bool:
        """Update the dependencies table in README.md from requirements.txt."""
        readme_path = self.app_root / "README.md"
        req_path = self.app_root / "requirements.txt"
        if not readme_path.exists() or not req_path.exists():
            return False

        lines = req_path.read_text(encoding="utf-8").strip().splitlines()
        table_lines = [
            "| Пакет | Версия | Предназначение |",
            "|-------|--------|----------------|",
        ]
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            pkg, version = self._parse_requirement(line)
            purpose = PACKAGE_PURPOSES.get(pkg, "")
            table_lines.append(f"| {pkg} | {version} | {purpose} |")

        table_block = "\n".join(table_lines)
        return self._replace_section(readme_path, "DEPS", table_block)

    def update_readme_version(self) -> bool:
        """Update the version string in README.md from CHANGELOG.md."""
        readme_path = self.app_root / "README.md"
        changelog_path = self.app_root / "CHANGELOG.md"
        if not readme_path.exists() or not changelog_path.exists():
            return False

        version = self._extract_latest_version(changelog_path)
        if not version:
            return False

        content = readme_path.read_text(encoding="utf-8")

        # Update "**Версия:** X.Y.Z" line
        new_content = re.sub(
            r"\*\*Версия:\*\*\s*[\d.]+",
            f"**Версия:** {version}",
            content,
        )

        # Update "Текуща версия: **X.Y.Z**" line
        new_content = re.sub(
            r"Текуща версия:\s*\*\*[\d.]+\*\*",
            f"Текуща версия: **{version}**",
            new_content,
        )

        if new_content != content:
            readme_path.write_text(new_content, encoding="utf-8")
            return True
        return False

    # ------------------------------------------------------------------
    # CHANGELOG helpers
    # ------------------------------------------------------------------

    def suggest_changelog_entry(self, commit_messages: list[str]) -> str:
        """Suggest a changelog entry based on conventional commit messages.

        Groups by type: Добавено (feat), Променено (refactor/perf),
        Поправено (fix), Документация (docs).
        """
        groups: dict[str, list[str]] = {
            "Добавено": [],
            "Променено": [],
            "Поправено": [],
        }

        for msg in commit_messages:
            msg = msg.strip()
            if msg.startswith("feat:") or msg.startswith("feat("):
                desc = re.sub(r"^feat(\([^)]*\))?:\s*", "", msg)
                groups["Добавено"].append(f"- {desc}")
            elif msg.startswith("fix:") or msg.startswith("fix("):
                desc = re.sub(r"^fix(\([^)]*\))?:\s*", "", msg)
                groups["Поправено"].append(f"- {desc}")
            elif msg.startswith(("refactor:", "perf:", "style:")):
                desc = re.sub(r"^(refactor|perf|style)(\([^)]*\))?:\s*", "", msg)
                groups["Променено"].append(f"- {desc}")

        lines: list[str] = []
        for group_name, items in groups.items():
            if items:
                lines.append(f"\n### {group_name}")
                lines.extend(items)

        return "\n".join(lines) if lines else ""

    def update_changelog_latest_version(self) -> bool:
        """No-op: changelog entries are added manually or via suggest_changelog_entry."""
        return False

    # ------------------------------------------------------------------
    # ARCHITECTURE updaters
    # ------------------------------------------------------------------

    def update_architecture_components(self) -> bool:
        """Update the components list in ARCHITECTURE.md from actual .py files."""
        arch_path = self.app_root / "docs" / "ARCHITECTURE.md"
        src_dir = self.app_root / "src"
        if not arch_path.exists() or not src_dir.exists():
            return False

        # This is informational only — we don't auto-replace the components
        # section because it contains hand-written descriptions.
        # Instead, we log if new files appeared that aren't documented.
        documented = arch_path.read_text(encoding="utf-8")
        py_files = sorted(f.name for f in src_dir.glob("*.py") if f.name != "__init__.py")

        undocumented = [f for f in py_files if f.replace(".py", "") not in documented]
        if undocumented:
            logger.info("Undocumented modules in ARCHITECTURE.md: %s", undocumented)

        return False  # No auto-replacement for hand-written content

    def update_architecture_dependencies(self) -> bool:
        """Update the dependencies table in ARCHITECTURE.md from requirements.txt."""
        # The architecture deps table mirrors README — keep them in sync
        return False  # Manual for now; ARCHITECTURE deps are more detailed

    # ------------------------------------------------------------------
    # Run all
    # ------------------------------------------------------------------

    def run_all_updates(self) -> dict[str, Any]:
        """Execute all necessary documentation updates.

        Returns dict with "updates" list and "total" count.
        """
        updates_needed = self.check_for_updates()

        # If git is not available, still allow manual runs
        if not updates_needed:
            updates_needed = [
                {"doc": "README.md", "sections": ["file_tree", "dependencies", "version"]},
            ]

        results: list[dict[str, str]] = []

        for update in updates_needed:
            doc = update.get("doc", "")
            for section in update.get("sections", []):
                # Build method name: "README.md" + "file_tree" → "update_readme_file_tree"
                doc_key = doc.replace(".md", "").replace("docs/", "").lower()
                method_name = f"update_{doc_key}_{section}"
                method = getattr(self, method_name, None)
                if method and callable(method):
                    try:
                        updated = method()
                        status = "updated" if updated else "skipped"
                    except Exception as exc:
                        logger.exception("Failed to update %s/%s", doc, section)
                        status = f"error: {exc}"
                    results.append({"doc": doc, "section": section, "status": status})

        return {
            "updates": results,
            "total": sum(1 for r in results if r["status"] == "updated"),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_file_tree(self, root: Path, prefix: str = "", max_depth: int = 3, _depth: int = 0) -> str:
        """Build an ASCII file tree string, respecting ignore patterns."""
        if _depth >= max_depth:
            return ""

        entries: list[Path] = []
        try:
            entries = sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return ""

        lines: list[str] = []
        visible = [
            e for e in entries
            if e.name not in IGNORE_PATTERNS
            and not e.name.startswith(".")
            and str(e.relative_to(self.app_root)) not in IGNORE_PATTERNS
        ]

        for i, entry in enumerate(visible):
            connector = "└── " if i == len(visible) - 1 else "├── "
            extension = "    " if i == len(visible) - 1 else "│   "

            if entry.is_dir():
                lines.append(f"{prefix}{connector}{entry.name}/")
                subtree = self._build_file_tree(
                    entry, prefix + extension, max_depth, _depth + 1,
                )
                if subtree:
                    lines.append(subtree.rstrip("\n"))
            else:
                comment = FILE_COMMENTS.get(entry.name, "")
                suffix = f"  # {comment}" if comment else ""
                lines.append(f"{prefix}{connector}{entry.name}{suffix}")

        return "\n".join(lines) + "\n" if lines else ""

    def _replace_section(self, file_path: Path, marker_name: str, new_content: str) -> bool:
        """Replace content between <!-- MARKER_START --> and <!-- MARKER_END -->."""
        content = file_path.read_text(encoding="utf-8")
        start_marker = f"<!-- {marker_name}_START -->"
        end_marker = f"<!-- {marker_name}_END -->"

        start_idx = content.find(start_marker)
        end_idx = content.find(end_marker)

        if start_idx == -1 or end_idx == -1:
            logger.warning("Markers %s not found in %s", marker_name, file_path.name)
            return False

        before = content[: start_idx + len(start_marker)]
        after = content[end_idx:]
        new_full = before + "\n" + new_content + "\n" + after

        if new_full != content:
            file_path.write_text(new_full, encoding="utf-8")
            return True
        return False

    @staticmethod
    def _parse_requirement(line: str) -> tuple[str, str]:
        """Parse a pip requirement line into (package, version_spec)."""
        for sep in (">=", "==", "<=", "~=", "!=", ">", "<"):
            if sep in line:
                parts = line.split(sep, 1)
                return parts[0].strip(), sep + parts[1].strip()
        return line.strip(), ""

    @staticmethod
    def _extract_latest_version(changelog_path: Path) -> str:
        """Extract the latest version number from CHANGELOG.md."""
        content = changelog_path.read_text(encoding="utf-8")
        match = re.search(r"##\s*\[(\d+\.\d+\.\d+)\]", content)
        return match.group(1) if match else ""


# ---------------------------------------------------------------------------
# Static lookup tables
# ---------------------------------------------------------------------------

PACKAGE_PURPOSES: dict[str, str] = {
    "streamlit": "Уеб интерфейс",
    "anthropic": "Anthropic Claude API (контрольор)",
    "openai": "DeepSeek API (OpenAI-съвместим)",
    "plotly": "Интерактивен Gantt chart",
    "pandas": "Таблици и данни",
    "reportlab": "PDF генериране (A3 Gantt)",
    "python-dotenv": "Зареждане на .env конфигурация",
    "PyPDF2": "Четене на PDF файлове",
    "openpyxl": "Четене на Excel файлове",
    "watchdog": "Наблюдение на файлови промени",
    "PyMuPDF": "OCR на сканирани PDF-и",
    "python-docx": "Четене на Word документи",
}

FILE_COMMENTS: dict[str, str] = {
    "app.py": "Главно Streamlit приложение",
    "requirements.txt": "Python зависимости",
    "install.bat": "Инсталатор (Python + venv + пакети)",
    "start.bat": "Стартиране на приложението",
    "update.bat": "Обновяване (git pull + pip upgrade)",
    "CHANGELOG.md": "Списък на промените",
    "README.md": "Документация за потребители",
    "README_INSTALL.md": "Инструкции за инсталация",
    "ai_processor.py": "Оркестрация на AI pipeline",
    "ai_router.py": "Двоен AI маршрутизатор",
    "chat_handler.py": "Обработка на чат съобщения",
    "docs_updater.py": "Автоматично обновяване на документация",
    "export_pdf.py": "PDF експорт (A3 Gantt)",
    "export_xml.py": "MSPDI XML експорт (MS Project)",
    "file_manager.py": "Конвертиране на файлове",
    "gantt_chart.py": "Интерактивен Plotly Gantt",
    "knowledge_manager.py": "3-нивова база знания",
    "project_manager.py": "Управление на проекти",
    "schedule_builder.py": "Изграждане на график от AI отговор",
    "self_evolution.py": "Самоеволюция (3 нива)",
}
