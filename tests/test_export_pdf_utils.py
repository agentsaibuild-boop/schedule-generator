"""Unit tests for export_pdf.py — pure utility functions.

Covers: _day_to_x, _format_task_name, _flatten_schedule,
_calculate_pages, _generate_months.

FAILURE означава: src/export_pdf.py :: utility функции са счупени —
PDF Gantt позиционирането, форматирането на задачи, пагинацията
или оста на времето са дефектни.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.export_pdf import (
    _calculate_pages,
    _day_to_x,
    _flatten_schedule,
    _format_task_name,
    _generate_months,
)


# ---------------------------------------------------------------------------
# _day_to_x
# ---------------------------------------------------------------------------


class TestDayToX:
    def test_day_one_maps_to_gantt_left(self):
        """Day 1 starts at gantt_left (zero offset)."""
        result = _day_to_x(1, total_days=100, gantt_left=50.0, gantt_width=500.0)
        assert result == 50.0

    def test_proportional_midpoint(self):
        """Day 51 of 100 maps to 50% across gantt width."""
        result = _day_to_x(51, total_days=100, gantt_left=0.0, gantt_width=100.0)
        assert abs(result - 50.0) < 0.01

    def test_last_day_near_right_edge(self):
        """Last day starts at (total-1)/total, not at the right edge."""
        result = _day_to_x(100, total_days=100, gantt_left=0.0, gantt_width=100.0)
        assert abs(result - 99.0) < 0.01

    def test_zero_total_days_returns_gantt_left(self):
        """Guard: total_days=0 must not divide by zero."""
        result = _day_to_x(5, total_days=0, gantt_left=42.0, gantt_width=300.0)
        assert result == 42.0

    def test_gantt_left_offset_is_applied(self):
        """gantt_left offset shifts the result correctly."""
        result = _day_to_x(1, total_days=10, gantt_left=100.0, gantt_width=200.0)
        assert result == 100.0  # day 1 → always gantt_left

    def test_negative_total_days_returns_gantt_left(self):
        """Negative total_days treated same as zero (guard path)."""
        result = _day_to_x(3, total_days=-5, gantt_left=10.0, gantt_width=100.0)
        assert result == 10.0


# ---------------------------------------------------------------------------
# _format_task_name
# ---------------------------------------------------------------------------


class TestFormatTaskName:
    def test_short_name_unchanged(self):
        task = {"name": "Изкопни работи"}
        assert _format_task_name(task) == "Изкопни работи"

    def test_name_exactly_40_chars_unchanged(self):
        name = "А" * 40
        task = {"name": name}
        assert _format_task_name(task) == name

    def test_name_over_40_chars_truncated_with_ellipsis(self):
        name = "А" * 50
        task = {"name": name}
        result = _format_task_name(task)
        assert result.endswith("...")
        assert len(result) == 40

    def test_phase_long_name_not_truncated(self):
        """Phase rows (is_phase=True) keep the full name."""
        name = "Б" * 50
        task = {"name": name}
        result = _format_task_name(task, is_phase=True)
        assert result == name

    def test_missing_name_returns_empty_string(self):
        result = _format_task_name({})
        assert result == ""

    def test_name_41_chars_is_truncated(self):
        name = "В" * 41
        task = {"name": name}
        result = _format_task_name(task)
        assert len(result) == 40
        assert result.endswith("...")


# ---------------------------------------------------------------------------
# _flatten_schedule
# ---------------------------------------------------------------------------


class TestFlattenSchedule:
    def test_empty_input_returns_empty(self):
        assert _flatten_schedule([]) == []

    def test_flat_task_no_sub_activities(self):
        schedule = [{"id": "1", "name": "Задача А"}]
        result = _flatten_schedule(schedule)
        assert len(result) == 1
        assert result[0]["_is_phase"] is False
        assert result[0]["_is_sub"] is False
        assert result[0]["_indent"] == 0

    def test_phase_with_sub_activities_marked_as_phase(self):
        schedule = [{
            "id": "P1",
            "name": "Фаза 1",
            "sub_activities": [
                {"id": "T1", "name": "Подзадача 1"},
            ],
        }]
        result = _flatten_schedule(schedule)
        assert result[0]["_is_phase"] is True
        assert result[0]["_is_sub"] is False

    def test_sub_activities_marked_as_sub(self):
        schedule = [{
            "id": "P1",
            "name": "Фаза 1",
            "sub_activities": [
                {"id": "T1", "name": "Подзадача 1"},
                {"id": "T2", "name": "Подзадача 2"},
            ],
        }]
        result = _flatten_schedule(schedule)
        assert len(result) == 3  # phase + 2 subs
        assert result[1]["_is_sub"] is True
        assert result[1]["_indent"] == 1
        assert result[2]["_is_sub"] is True

    def test_multiple_phases_flatten_in_order(self):
        schedule = [
            {"id": "P1", "name": "Фаза 1", "sub_activities": [{"id": "T1", "name": "T1"}]},
            {"id": "P2", "name": "Фаза 2", "sub_activities": [{"id": "T2", "name": "T2"}]},
        ]
        result = _flatten_schedule(schedule)
        assert len(result) == 4
        assert result[0]["id"] == "P1"
        assert result[1]["id"] == "T1"
        assert result[2]["id"] == "P2"
        assert result[3]["id"] == "T2"

    def test_original_task_fields_preserved(self):
        schedule = [{
            "id": "P1",
            "name": "Фаза",
            "duration": 30,
            "team": "Бригада А",
            "sub_activities": [],
        }]
        result = _flatten_schedule(schedule)
        assert result[0]["duration"] == 30
        assert result[0]["team"] == "Бригада А"

    def test_empty_sub_activities_not_phase(self):
        """Task with empty sub_activities list should NOT be marked as phase."""
        schedule = [{"id": "T1", "name": "Задача", "sub_activities": []}]
        result = _flatten_schedule(schedule)
        assert result[0]["_is_phase"] is False


# ---------------------------------------------------------------------------
# _calculate_pages
# ---------------------------------------------------------------------------


class TestCalculatePages:
    def test_zero_tasks_returns_one_page(self):
        assert _calculate_pages(0, rows_per_page=20) == 1

    def test_negative_tasks_returns_one_page(self):
        assert _calculate_pages(-5, rows_per_page=20) == 1

    def test_exact_fit_one_page(self):
        assert _calculate_pages(20, rows_per_page=20) == 1

    def test_one_extra_task_needs_second_page(self):
        assert _calculate_pages(21, rows_per_page=20) == 2

    def test_two_full_pages(self):
        assert _calculate_pages(40, rows_per_page=20) == 2

    def test_large_schedule(self):
        # 105 tasks, 20 per page → ceil(105/20) = 6
        assert _calculate_pages(105, rows_per_page=20) == 6

    def test_single_task_one_page(self):
        assert _calculate_pages(1, rows_per_page=20) == 1


# ---------------------------------------------------------------------------
# _generate_months
# ---------------------------------------------------------------------------


class TestGenerateMonths:
    def test_single_month_returns_one_entry(self):
        start = datetime(2026, 6, 1)
        end = datetime(2026, 6, 30)
        months = _generate_months(start, end)
        assert len(months) == 1
        assert months[0]["label"] == "Юни 2026"

    def test_first_month_starts_at_day_one(self):
        start = datetime(2026, 6, 1)
        end = datetime(2026, 8, 31)
        months = _generate_months(start, end)
        assert months[0]["start_day"] == 1

    def test_month_labels_in_bulgarian(self):
        start = datetime(2026, 1, 1)
        end = datetime(2026, 12, 31)
        months = _generate_months(start, end)
        labels = [m["label"] for m in months]
        assert "Яну 2026" in labels
        assert "Дек 2026" in labels
        assert len(months) == 12

    def test_year_boundary_december_to_january(self):
        """Month generation must not crash at year boundary."""
        start = datetime(2026, 11, 1)
        end = datetime(2027, 2, 28)
        months = _generate_months(start, end)
        assert len(months) == 4
        labels = [m["label"] for m in months]
        assert "Дек 2026" in labels
        assert "Яну 2027" in labels

    def test_short_labels_are_sequential(self):
        """М1, М2, М3 short labels are correctly numbered."""
        start = datetime(2026, 6, 1)
        end = datetime(2026, 8, 31)
        months = _generate_months(start, end)
        assert months[0]["short_label"] == "М1"
        assert months[1]["short_label"] == "М2"
        assert months[2]["short_label"] == "М3"

    def test_mid_month_start_first_month_starts_at_one(self):
        """When project starts mid-month, first month still has start_day=1."""
        start = datetime(2026, 6, 15)
        end = datetime(2026, 8, 15)
        months = _generate_months(start, end)
        assert months[0]["start_day"] == 1

    def test_month_end_days_are_increasing(self):
        """end_day of each month must be greater than start_day of same month."""
        start = datetime(2026, 3, 1)
        end = datetime(2026, 6, 30)
        months = _generate_months(start, end)
        for m in months:
            assert m["end_day"] > m["start_day"], (
                f"Month {m['label']}: end_day {m['end_day']} <= start_day {m['start_day']}"
            )

    def test_consecutive_months_are_contiguous(self):
        """end_day of month N equals start_day of month N+1 - 1."""
        start = datetime(2026, 4, 1)
        end = datetime(2026, 7, 31)
        months = _generate_months(start, end)
        for i in range(len(months) - 1):
            assert months[i]["end_day"] + 1 == months[i + 1]["start_day"], (
                f"Gap between {months[i]['label']} end ({months[i]['end_day']}) "
                f"and {months[i+1]['label']} start ({months[i+1]['start_day']})"
            )
