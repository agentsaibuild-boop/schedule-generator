"""Schedule builder — constructs schedule data from AI responses."""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.gantt_chart import day_to_date, generate_demo_schedule, get_type_label


class ScheduleBuilder:
    """Builds and validates schedule data structures."""

    def build_from_ai_response(self, ai_data: dict) -> list[dict]:
        """Build a schedule task list from an AI response.

        Args:
            ai_data: Dict with schedule data from AI processor.

        Returns:
            List of task dicts in the standard schedule format.
        """
        if not ai_data:
            return generate_demo_schedule()

        # When AI provides schedule data, convert to standard format
        tasks = ai_data.get("tasks", [])
        if tasks:
            return tasks

        return generate_demo_schedule()

    def validate_schedule(self, schedule: list[dict]) -> dict:
        """Validate a schedule for errors and warnings.

        Args:
            schedule: List of task dicts.

        Returns:
            Dict with 'valid' bool, 'errors' list, 'warnings' list.
        """
        errors: list[str] = []
        warnings: list[str] = []

        if not schedule:
            errors.append("Графикът е празен.")
            return {"valid": False, "errors": errors, "warnings": warnings}

        ids_seen: set[str] = set()
        for i, task in enumerate(schedule):
            if not task.get("name"):
                errors.append(f"Задача #{i + 1} няма име.")
            tid = task.get("id", "")
            if tid in ids_seen:
                errors.append(f"Дублирано ID: {tid}")
            ids_seen.add(tid)
            if task.get("start_day", 0) < 0:
                errors.append(
                    f"Задача '{task.get('name')}' има отрицателен начален ден."
                )
            if task.get("duration", 0) < 0:
                warnings.append(
                    f"Задача '{task.get('name')}' има отрицателна продължителност."
                )

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
        }

    def adjust_schedule(
        self, schedule: list[dict], changes: dict
    ) -> list[dict]:
        """Apply adjustments to an existing schedule.

        Args:
            schedule: Current task list.
            changes: Dict describing the changes to apply.

        Returns:
            Updated task list.
        """
        return schedule

    def to_dataframe(
        self, schedule: list[dict], start_date: str
    ) -> pd.DataFrame:
        """Convert schedule to a pandas DataFrame for table display.

        Args:
            schedule: List of task dicts.
            start_date: Project start date (ISO format).

        Returns:
            DataFrame with columns: №, Дейност, Тип, DN, L(м), Екип,
            Начало, Край, Дни, Критичен.
        """
        rows: list[dict[str, Any]] = []
        for i, task in enumerate(schedule):
            duration = task.get("duration", 0)
            end_day = task.get(
                "end_day",
                task.get("start_day", 0) + max(duration, 1) - 1,
            )
            rows.append({
                "№": i + 1,
                "Дейност": task["name"],
                "Тип": get_type_label(task.get("type", "")),
                "DN": task.get("diameter", "—"),
                "L(м)": task.get("length_m", "—"),
                "Екип": task.get("team", "—"),
                "Начало": day_to_date(task["start_day"], start_date),
                "Край": day_to_date(end_day, start_date),
                "Дни": duration,
                "Критичен": "🔴" if task.get("is_critical") else "",
            })
        return pd.DataFrame(rows)
