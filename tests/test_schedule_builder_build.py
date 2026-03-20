"""Unit tests for ScheduleBuilder.build_from_ai_response.

FAILURE означава: ScheduleBuilder.build_from_ai_response е счупен —
конвертирането на AI отговор в schedule tasks не работи.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.schedule_builder import ScheduleBuilder


@pytest.fixture()
def builder() -> ScheduleBuilder:
    return ScheduleBuilder()


def _minimal_task(tid: str = "T1", name: str = "Задача", start: int = 0, dur: int = 5) -> dict:
    return {"id": tid, "name": name, "start_day": start, "duration": dur}


# ---------------------------------------------------------------------------
# Falsy / missing input → demo schedule
# ---------------------------------------------------------------------------

class TestFallbackToDemo:
    def test_none_returns_demo(self, builder):
        result = builder.build_from_ai_response(None)
        assert isinstance(result, list)
        assert len(result) > 0

    def test_empty_dict_returns_demo(self, builder):
        result = builder.build_from_ai_response({})
        assert isinstance(result, list)
        assert len(result) > 0

    def test_missing_tasks_key_returns_demo(self, builder):
        result = builder.build_from_ai_response({"total_duration": 100})
        assert isinstance(result, list)
        assert len(result) > 0

    def test_tasks_empty_list_returns_demo(self, builder):
        result = builder.build_from_ai_response({"tasks": []})
        assert isinstance(result, list)
        assert len(result) > 0

    def test_tasks_none_value_returns_demo(self, builder):
        result = builder.build_from_ai_response({"tasks": None})
        assert isinstance(result, list)
        assert len(result) > 0

    def test_demo_schedule_has_required_keys(self, builder):
        """Demo schedule tasks must have name, start_day, duration at minimum."""
        result = builder.build_from_ai_response({})
        for task in result:
            assert "name" in task
            assert "start_day" in task
            assert "duration" in task


# ---------------------------------------------------------------------------
# Valid tasks → returned as-is
# ---------------------------------------------------------------------------

class TestValidTasksReturned:
    def test_single_task_returned(self, builder):
        tasks = [_minimal_task()]
        result = builder.build_from_ai_response({"tasks": tasks})
        assert result == tasks

    def test_multiple_tasks_returned_in_order(self, builder):
        tasks = [_minimal_task("T1", "Задача 1"), _minimal_task("T2", "Задача 2", start=5)]
        result = builder.build_from_ai_response({"tasks": tasks})
        assert result == tasks
        assert result[0]["id"] == "T1"
        assert result[1]["id"] == "T2"

    def test_extra_fields_preserved(self, builder):
        tasks = [{"id": "A1", "name": "Изкоп", "start_day": 0, "duration": 10,
                  "dn": "DN90", "length_m": 350, "team": "Бригада 1"}]
        result = builder.build_from_ai_response({"tasks": tasks})
        assert result[0]["dn"] == "DN90"
        assert result[0]["length_m"] == 350
        assert result[0]["team"] == "Бригада 1"

    def test_dependencies_preserved(self, builder):
        tasks = [
            _minimal_task("A", "Изкоп", 0, 10),
            {**_minimal_task("B", "Полагане", 10, 5), "dependencies": ["A"]},
        ]
        result = builder.build_from_ai_response({"tasks": tasks})
        assert result[1]["dependencies"] == ["A"]

    def test_additional_top_level_keys_ignored(self, builder):
        tasks = [_minimal_task()]
        ai_data = {"tasks": tasks, "total_duration": 42, "teams": ["Б1"], "notes": "бележки"}
        result = builder.build_from_ai_response(ai_data)
        assert result == tasks

    def test_returns_same_object_not_copy(self, builder):
        """build_from_ai_response returns the tasks list directly (no deep copy)."""
        tasks = [_minimal_task()]
        result = builder.build_from_ai_response({"tasks": tasks})
        assert result is tasks

    def test_large_schedule_returned_intact(self, builder):
        tasks = [_minimal_task(f"T{i}", f"Задача {i}", start=i * 5, dur=5) for i in range(50)]
        result = builder.build_from_ai_response({"tasks": tasks})
        assert len(result) == 50

    def test_tasks_with_subtasks_preserved(self, builder):
        tasks = [
            {"id": "P1", "name": "Участък 1", "start_day": 0, "duration": 20,
             "subtasks": [_minimal_task("P1.1", "Изкоп", 0, 10)]},
        ]
        result = builder.build_from_ai_response({"tasks": tasks})
        assert "subtasks" in result[0]
        assert len(result[0]["subtasks"]) == 1


# ---------------------------------------------------------------------------
# Edge cases — unusual but valid inputs
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_tasks_with_zero_start_day(self, builder):
        tasks = [_minimal_task("T1", "Подготовка", start=0, dur=1)]
        result = builder.build_from_ai_response({"tasks": tasks})
        assert result[0]["start_day"] == 0

    def test_tasks_key_with_truthy_single_item(self, builder):
        tasks = [_minimal_task()]
        result = builder.build_from_ai_response({"tasks": tasks})
        assert len(result) == 1

    def test_ai_data_with_only_tasks_key(self, builder):
        tasks = [_minimal_task("X", "Само задача")]
        result = builder.build_from_ai_response({"tasks": tasks})
        assert result[0]["name"] == "Само задача"

    def test_false_ai_data_int_zero_returns_demo(self, builder):
        """Falsy non-dict values (e.g. 0) should return demo schedule."""
        result = builder.build_from_ai_response(0)
        assert isinstance(result, list)
        assert len(result) > 0

    def test_false_ai_data_empty_string_returns_demo(self, builder):
        result = builder.build_from_ai_response("")
        assert isinstance(result, list)
        assert len(result) > 0
