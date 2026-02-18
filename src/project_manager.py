"""Project manager — tracks project history, state persistence, and resume.

Stores project metadata in config/projects_history.json for persistence across
sessions. Supports recent projects listing and automatic state restoration.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ProjectManager:
    """Manages projects — loading, history, last state, resume."""

    MAX_RECENT = 5

    def __init__(self, app_root: str) -> None:
        """Initialize the project manager.

        Args:
            app_root: Absolute path to the application root directory.
        """
        self.app_root = Path(app_root)
        self._db_path = self.app_root / "config" / "projects_history.json"
        self.projects: dict[str, Any] = self._load_projects_db()
        self.current_project: dict | None = None

    # ------------------------------------------------------------------
    # Database persistence
    # ------------------------------------------------------------------

    def _load_projects_db(self) -> dict:
        """Load the projects history from JSON file.

        Creates a fresh file if it doesn't exist or is corrupted.
        """
        if self._db_path.exists():
            try:
                data = json.loads(self._db_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "projects" in data:
                    return data
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load projects_history.json: %s", exc)

        # Create initial structure
        initial = {"version": "1.0", "projects": {}}
        self._save_projects_db(initial)
        return initial

    def _save_projects_db(self, data: dict | None = None) -> None:
        """Save the projects history to JSON file.

        Called after EVERY change — user may close the app at any time.
        """
        to_save = data if data is not None else self.projects
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db_path.write_text(
                json.dumps(to_save, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error("Failed to save projects_history.json: %s", exc)

    # ------------------------------------------------------------------
    # Project registration
    # ------------------------------------------------------------------

    def register_project(self, path: str, name: str | None = None) -> dict:
        """Register a new project or update an existing one.

        Args:
            path: Absolute path to the project directory.
            name: Optional display name (defaults to folder name).

        Returns:
            The project dict.
        """
        project_id = hashlib.md5(path.encode("utf-8")).hexdigest()[:12]
        now = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")

        projects_dict = self.projects.setdefault("projects", {})
        existing = projects_dict.get(project_id)

        if existing:
            # Update access time
            existing["last_accessed"] = now
            existing["stats"]["total_sessions"] = existing["stats"].get("total_sessions", 0) + 1
            self.projects["last_active_id"] = project_id
            self._save_projects_db()
            self.current_project = existing
            return existing

        # Count files in the directory
        files_total = 0
        p = Path(path)
        if p.exists() and p.is_dir():
            supported_ext = {".pdf", ".xlsx", ".xls", ".csv", ".json", ".txt", ".docx"}
            for f in p.iterdir():
                if f.is_file() and f.suffix.lower() in supported_ext:
                    files_total += 1

        project = {
            "id": project_id,
            "name": name or Path(path).name,
            "path": path,
            "created": now,
            "last_accessed": now,
            "status": "new",
            "progress": {
                "files_total": files_total,
                "files_converted": 0,
                "schedule_version": None,
                "last_schedule": None,
                "exports": [],
                "chat_summary": None,
                "chat_history": [],
            },
            "stats": {
                "total_ai_cost": 0.0,
                "total_sessions": 1,
                "total_messages": 0,
            },
        }

        projects_dict[project_id] = project
        self.projects["last_active_id"] = project_id
        self._save_projects_db()
        self.current_project = project
        return project

    # ------------------------------------------------------------------
    # Recent projects
    # ------------------------------------------------------------------

    def get_recent_projects(self, limit: int = 5) -> list[dict]:
        """Return the last N projects sorted by last_accessed (descending).

        Each project dict includes extra fields: exists, status_label, time_ago.

        Args:
            limit: Maximum number of projects to return.

        Returns:
            List of project dicts with UI-friendly fields.
        """
        projects_dict: dict = self.projects.get("projects", {})
        if not projects_dict:
            return []

        # Sort by last_accessed descending
        sorted_projects = sorted(
            projects_dict.values(),
            key=lambda p: p.get("last_accessed", ""),
            reverse=True,
        )

        result = []
        for proj in sorted_projects[:limit]:
            proj_copy = dict(proj)
            proj_copy["exists"] = Path(proj["path"]).is_dir()
            proj_copy["status_label"] = self.get_status_label(proj.get("status", "new"))
            proj_copy["time_ago"] = self.get_time_ago(proj.get("last_accessed", ""))
            result.append(proj_copy)

        return result

    # ------------------------------------------------------------------
    # Last active project (for auto-restore on session loss)
    # ------------------------------------------------------------------

    def get_last_active_project(self) -> dict | None:
        """Get the last active project for auto-restore after session loss."""
        last_id = self.projects.get("last_active_id")
        if not last_id:
            return None
        return self.projects.get("projects", {}).get(last_id)

    def clear_last_active(self) -> None:
        """Clear the last active project marker."""
        self.projects.pop("last_active_id", None)
        self._save_projects_db()

    # ------------------------------------------------------------------
    # Load / resume project
    # ------------------------------------------------------------------

    def load_project(self, project_id: str) -> dict | None:
        """Load a project by ID — returns the full state.

        Updates last_accessed and total_sessions.

        Args:
            project_id: The project ID hash.

        Returns:
            Project dict or None if not found.
        """
        projects_dict: dict = self.projects.get("projects", {})
        project = projects_dict.get(project_id)
        if not project:
            return None

        now = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        project["last_accessed"] = now
        project["stats"]["total_sessions"] = project["stats"].get("total_sessions", 0) + 1

        # Check if the folder still exists
        project["_exists"] = Path(project["path"]).is_dir()

        # Check if converted/ is up to date
        converted_dir = Path(project["path"]) / "converted"
        project["_has_converted"] = converted_dir.exists() and any(
            f.suffix == ".json" for f in converted_dir.iterdir()
        ) if converted_dir.exists() else False

        self._save_projects_db()
        self.current_project = project
        return project

    # ------------------------------------------------------------------
    # Save progress
    # ------------------------------------------------------------------

    def save_progress(self, project_id: str, updates: dict) -> None:
        """Save progress for a project. Persists immediately.

        Args:
            project_id: The project ID hash.
            updates: Dict of fields to update (status, files_converted,
                schedule_version, last_schedule, chat_summary, etc.).
        """
        projects_dict: dict = self.projects.get("projects", {})
        project = projects_dict.get(project_id)
        if not project:
            return

        now = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        project["last_accessed"] = now

        # Update top-level status
        if "status" in updates:
            project["status"] = updates["status"]

        # Update progress fields
        progress = project.setdefault("progress", {})
        for key in ("files_converted", "schedule_version", "last_schedule",
                     "chat_summary", "chat_history", "files_total"):
            if key in updates:
                progress[key] = updates[key]

        # Track exports
        if "export" in updates:
            progress.setdefault("exports", []).append(updates["export"])

        # Track AI costs
        if "ai_cost" in updates:
            project["stats"]["total_ai_cost"] = (
                project["stats"].get("total_ai_cost", 0.0) + updates["ai_cost"]
            )

        self._save_projects_db()

    # ------------------------------------------------------------------
    # Welcome message generation
    # ------------------------------------------------------------------

    def get_welcome_message(self, project: dict) -> str:
        """Generate a welcome message for a loaded project.

        Args:
            project: The project dict.

        Returns:
            Formatted Bulgarian welcome message.
        """
        name = project.get("name", "?")
        path = project.get("path", "?")
        status = project.get("status", "new")
        progress = project.get("progress", {})
        files_total = progress.get("files_total", 0)
        files_converted = progress.get("files_converted", 0)

        if status == "new":
            return (
                f"**{name}**\n"
                f"Папка: `{path}`\n"
                f"Намерени файлове: **{files_total}**\n\n"
                "Следваща стъпка: Конвертиране на файловете за анализ."
            )

        if status == "converting":
            return (
                f"**{name}**\n"
                f"Статус: Конвертиране — {files_converted}/{files_total} файла готови.\n\n"
                "Следваща стъпка: Завършете конвертирането и анализирайте документите."
            )

        if status == "analyzed":
            return (
                f"**{name}**\n"
                f"Статус: Документите са анализирани.\n"
                f"Файлове: {files_total} конвертирани.\n\n"
                "Следваща стъпка: Генериране на строителен график."
            )

        if status == "schedule_generated":
            schedule = progress.get("last_schedule")
            version = progress.get("schedule_version", "?")
            num_tasks = 0
            total_days = 0
            if schedule and isinstance(schedule, list):
                num_tasks = len(schedule)
                total_days = max(
                    (
                        t.get("end_day", t.get("start_day", 0) + t.get("duration", 0))
                        for t in schedule
                    ),
                    default=0,
                )
            elif schedule and isinstance(schedule, str):
                num_tasks = schedule.count('"id"')

            time_ago = self.get_time_ago(project.get("last_accessed", ""))

            return (
                f"**{name}**\n"
                f"Статус: Графикът е генериран"
                + (f" (версия {version})" if version and version != "?" else "")
                + f"\n"
                f"Дейности: {num_tasks} | Срок: {total_days} дни\n"
                f"Последна промяна: {time_ago}\n\n"
                "Можете да:\n"
                "- Прегледате графика в Gantt визуализацията\n"
                "- Направите промени (кажете какво искате)\n"
                "- Експортирате в PDF / XML\n"
                "- Генерирате нова версия"
            )

        if status == "exported":
            exports = progress.get("exports", [])
            last_export = exports[-1] if exports else {}

            return (
                f"**{name}**\n"
                f"Статус: Графикът е експортиран.\n"
                + (f"Последен експорт: {last_export.get('type', '?')} "
                   f"на {last_export.get('date', '?')}\n" if last_export else "")
                + "\n"
                "Можете да:\n"
                "- Направите корекции по графика\n"
                "- Експортирате отново (PDF / XML)\n"
                "- Заредите нов проект"
            )

        # Fallback
        return (
            f"**{name}**\n"
            f"Папка: `{path}`\n"
            f"Статус: {self.get_status_label(status)}"
        )

    # ------------------------------------------------------------------
    # Status labels (Bulgarian)
    # ------------------------------------------------------------------

    @staticmethod
    def get_status_label(status: str) -> str:
        """Translate status to Bulgarian with emoji."""
        labels = {
            "new": "Нов",
            "converting": "Конвертиране",
            "analyzed": "Анализиран",
            "schedule_generated": "График готов",
            "exported": "Експортиран",
        }
        return labels.get(status, status)

    @staticmethod
    def get_status_emoji(status: str) -> str:
        """Return emoji for project status."""
        emojis = {
            "new": "\U0001f195",       # NEW button
            "converting": "\U0001f504",  # arrows
            "analyzed": "\U0001f50d",    # magnifier
            "schedule_generated": "\U0001f4ca",  # chart
            "exported": "\u2705",        # check mark
        }
        return emojis.get(status, "\u2b55")

    # ------------------------------------------------------------------
    # Time formatting
    # ------------------------------------------------------------------

    @staticmethod
    def get_time_ago(iso_date: str) -> str:
        """Return human-readable time difference in Bulgarian.

        Args:
            iso_date: ISO 8601 datetime string.

        Returns:
            String like 'преди 2 часа', 'вчера', 'преди 3 дни'.
        """
        if not iso_date:
            return "неизвестно"

        try:
            # Handle both timezone-aware and naive datetimes
            dt_str = iso_date.replace("+00:00", "").replace("Z", "")
            dt = datetime.fromisoformat(dt_str)
            now = datetime.now(tz=timezone.utc).replace(tzinfo=None)
            diff = now - dt
        except (ValueError, TypeError):
            return "неизвестно"

        seconds = int(diff.total_seconds())
        if seconds < 0:
            return "току-що"
        if seconds < 60:
            return "току-що"
        if seconds < 3600:
            minutes = seconds // 60
            return f"преди {minutes} мин."
        if seconds < 86400:
            hours = seconds // 3600
            return f"преди {hours} {'час' if hours == 1 else 'часа'}"
        if seconds < 172800:
            return "вчера"
        if seconds < 604800:
            days = seconds // 86400
            return f"преди {days} дни"
        if seconds < 2592000:
            weeks = seconds // 604800
            return f"преди {weeks} {'седмица' if weeks == 1 else 'седмици'}"
        months = seconds // 2592000
        return f"преди {months} {'месец' if months == 1 else 'месеца'}"
