"""Schedule builder — constructs schedule data from AI responses."""

from __future__ import annotations

from datetime import datetime, timedelta


class ScheduleBuilder:
    """Builds and validates schedule data structures."""

    def build_from_ai_response(self, ai_data: dict) -> list[dict]:
        """Build a schedule task list from an AI response.

        Args:
            ai_data: Dict with schedule data from AI processor.

        Returns:
            List of task dicts with name, start, end, duration, etc.
        """
        # Placeholder — returns demo data
        return self._get_demo_schedule()

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

        for i, task in enumerate(schedule):
            if not task.get("name"):
                errors.append(f"Задача #{i + 1} няма име.")
            if task.get("start_day", 0) < 0:
                errors.append(f"Задача '{task.get('name')}' има отрицателен начален ден.")
            if task.get("duration", 0) <= 0:
                warnings.append(f"Задача '{task.get('name')}' има нулева продължителност.")

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
        # Placeholder
        return schedule

    @staticmethod
    def _get_demo_schedule() -> list[dict]:
        """Generate demo schedule data for display.

        Returns:
            List of demo task dicts.
        """
        base = datetime(2025, 1, 1)
        return [
            {
                "name": "Проектиране",
                "category": "Проектиране",
                "start": base,
                "end": base + timedelta(days=179),
                "start_day": 1,
                "duration": 180,
                "dn": "-",
                "length_m": "-",
                "team": "Проектант",
            },
            {
                "name": "Мобилизация",
                "category": "Строителство",
                "start": base + timedelta(days=180),
                "end": base + timedelta(days=189),
                "start_day": 181,
                "duration": 10,
                "dn": "-",
                "length_m": "-",
                "team": "Всички",
            },
            {
                "name": "Водопровод Кл.1",
                "category": "Водопровод",
                "start": base + timedelta(days=190),
                "end": base + timedelta(days=229),
                "start_day": 191,
                "duration": 40,
                "dn": "DN110",
                "length_m": "520",
                "team": "Екип 1",
            },
            {
                "name": "Канализация Гл.Кл",
                "category": "Канализация",
                "start": base + timedelta(days=200),
                "end": base + timedelta(days=259),
                "start_day": 201,
                "duration": 60,
                "dn": "DN315",
                "length_m": "740",
                "team": "Екип 2",
            },
            {
                "name": "Пътни работи",
                "category": "Пътни работи",
                "start": base + timedelta(days=219),
                "end": base + timedelta(days=279),
                "start_day": 220,
                "duration": 61,
                "dn": "-",
                "length_m": "-",
                "team": "Пътна бригада",
            },
            {
                "name": "Авторски надзор",
                "category": "Авт. надзор",
                "start": base + timedelta(days=180),
                "end": base + timedelta(days=279),
                "start_day": 181,
                "duration": 100,
                "dn": "-",
                "length_m": "-",
                "team": "Проектант",
            },
        ]
