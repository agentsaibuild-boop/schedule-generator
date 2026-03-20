"""Unit tests for gantt_chart.py — pure helper functions.

Covers: get_type_label, day_to_date, _filter_tasks, _is_subtask,
get_schedule_stats, generate_demo_schedule structure.

FAILURE означава: src/gantt_chart.py :: helper функции са счупени —
Gantt визуализацията, статистиките или демо данните са дефектни.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.gantt_chart import (
    _filter_tasks,
    _is_subtask,
    day_to_date,
    generate_demo_schedule,
    get_schedule_stats,
    get_type_label,
)


# ---------------------------------------------------------------------------
# get_type_label
# ---------------------------------------------------------------------------


def test_get_type_label_known_code():
    assert get_type_label("design") == "Проектиране"


def test_get_type_label_water_pipe():
    assert get_type_label("water_pipe") == "Водоснабдяване"


def test_get_type_label_sewer():
    assert get_type_label("sewer") == "Канализация"


def test_get_type_label_kps():
    assert get_type_label("kps") == "КПС"


def test_get_type_label_road():
    assert get_type_label("road") == "Пътни работи"


def test_get_type_label_all_known_types_return_cyrillic():
    """Every TYPE_LABELS entry must translate to a non-empty Bulgarian string."""
    from src.constants import TYPE_LABELS
    for code, label in TYPE_LABELS.items():
        result = get_type_label(code)
        assert result == label
        assert result  # not empty


def test_get_type_label_unknown_returns_code():
    """Unknown type code → falls back to the raw code string."""
    assert get_type_label("unknown_xyz") == "unknown_xyz"


def test_get_type_label_empty_string():
    assert get_type_label("") == ""


# ---------------------------------------------------------------------------
# day_to_date
# ---------------------------------------------------------------------------


def test_day_to_date_day_one():
    """Day 1 should equal the project start date."""
    assert day_to_date(1, "2026-06-01") == "01.06.2026"


def test_day_to_date_day_two():
    assert day_to_date(2, "2026-06-01") == "02.06.2026"


def test_day_to_date_crosses_month():
    """Day 31 from June 1 → July 1."""
    assert day_to_date(31, "2026-06-01") == "01.07.2026"


def test_day_to_date_crosses_year():
    """Day 1 from Dec 31 → Dec 31 itself."""
    assert day_to_date(1, "2026-12-31") == "31.12.2026"


def test_day_to_date_day_366():
    """Day 366 from Jan 1 2026 → Jan 1 2027."""
    assert day_to_date(366, "2026-01-01") == "01.01.2027"


def test_day_to_date_format():
    """Result must be in DD.MM.YYYY format."""
    result = day_to_date(5, "2026-03-20")
    parts = result.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


# ---------------------------------------------------------------------------
# _filter_tasks
# ---------------------------------------------------------------------------

_SAMPLE_TASKS = [
    {"id": "T1", "team": "Екип А", "phase": "water", "type": "water_pipe"},
    {"id": "T2", "team": "Екип А", "phase": "road", "type": "road"},
    {"id": "T3", "team": "Екип Б", "phase": "water", "type": "water_pipe"},
    {"id": "T4", "team": "Екип Б", "phase": "design", "type": "design"},
]


def test_filter_tasks_no_filter():
    result = _filter_tasks(_SAMPLE_TASKS, None, None, None)
    assert len(result) == 4


def test_filter_tasks_by_team():
    result = _filter_tasks(_SAMPLE_TASKS, "Екип А", None, None)
    assert len(result) == 2
    assert all(t["team"] == "Екип А" for t in result)


def test_filter_tasks_by_phase():
    result = _filter_tasks(_SAMPLE_TASKS, None, "water", None)
    assert len(result) == 2


def test_filter_tasks_by_type():
    result = _filter_tasks(_SAMPLE_TASKS, None, None, "road")
    assert len(result) == 1
    assert result[0]["id"] == "T2"


def test_filter_tasks_combined_team_and_phase():
    result = _filter_tasks(_SAMPLE_TASKS, "Екип А", "water", None)
    assert len(result) == 1
    assert result[0]["id"] == "T1"


def test_filter_tasks_no_match():
    result = _filter_tasks(_SAMPLE_TASKS, "Несъществуващ екип", None, None)
    assert result == []


def test_filter_tasks_empty_input():
    result = _filter_tasks([], "Екип А", None, None)
    assert result == []


# ---------------------------------------------------------------------------
# _is_subtask
# ---------------------------------------------------------------------------


def test_is_subtask_true():
    parent = {"id": "P1", "name": "Parent"}
    child = {"id": "C1", "parent_id": "P1"}
    assert _is_subtask(child, [parent, child]) is True


def test_is_subtask_false_no_parent_id():
    task = {"id": "T1"}
    assert _is_subtask(task, [task]) is False


def test_is_subtask_false_parent_not_in_list():
    """parent_id set but matching parent is not in the schedule list."""
    task = {"id": "C1", "parent_id": "MISSING"}
    assert _is_subtask(task, [task]) is False


def test_is_subtask_parent_id_none():
    task = {"id": "T1", "parent_id": None}
    assert _is_subtask(task, [task]) is False


# ---------------------------------------------------------------------------
# get_schedule_stats
# ---------------------------------------------------------------------------


def test_get_schedule_stats_empty():
    stats = get_schedule_stats([])
    assert stats["total_tasks"] == 0
    assert stats["critical_count"] == 0
    assert stats["total_days"] == 0
    assert stats["teams"] == []
    assert stats["type_breakdown"] == {}


def test_get_schedule_stats_total_tasks():
    tasks = [
        {"id": "T1", "start_day": 1, "end_day": 10, "duration": 10,
         "type": "water_pipe", "team": "Водопровод"},
        {"id": "T2", "start_day": 1, "end_day": 20, "duration": 20,
         "type": "sewer", "team": "Канализация"},
    ]
    stats = get_schedule_stats(tasks)
    assert stats["total_tasks"] == 2


def test_get_schedule_stats_critical_count():
    tasks = [
        {"id": "T1", "start_day": 1, "end_day": 5, "duration": 5,
         "type": "design", "is_critical": True},
        {"id": "T2", "start_day": 1, "end_day": 5, "duration": 5,
         "type": "road", "is_critical": False},
        {"id": "T3", "start_day": 1, "end_day": 5, "duration": 5,
         "type": "water_pipe"},  # no is_critical key
    ]
    stats = get_schedule_stats(tasks)
    assert stats["critical_count"] == 1


def test_get_schedule_stats_total_days_uses_end_day():
    tasks = [
        {"id": "T1", "start_day": 1, "end_day": 100, "duration": 100, "type": "water_pipe"},
        {"id": "T2", "start_day": 50, "end_day": 150, "duration": 100, "type": "sewer"},
    ]
    stats = get_schedule_stats(tasks)
    assert stats["total_days"] == 150


def test_get_schedule_stats_total_days_fallback_start_plus_duration():
    """When end_day is missing, falls back to start_day + duration."""
    tasks = [
        {"id": "T1", "start_day": 10, "duration": 20, "type": "water_pipe"},
    ]
    stats = get_schedule_stats(tasks)
    assert stats["total_days"] == 30  # 10 + 20


def test_get_schedule_stats_teams_sorted():
    tasks = [
        {"id": "T1", "type": "water_pipe", "team": "Екип Б"},
        {"id": "T2", "type": "sewer", "team": "Екип А"},
        {"id": "T3", "type": "road", "team": "Екип Б"},
    ]
    stats = get_schedule_stats(tasks)
    assert stats["teams"] == ["Екип А", "Екип Б"]


def test_get_schedule_stats_type_breakdown():
    tasks = [
        {"id": "T1", "type": "water_pipe", "duration": 10},
        {"id": "T2", "type": "water_pipe", "duration": 20},
        {"id": "T3", "type": "sewer", "duration": 15},
    ]
    stats = get_schedule_stats(tasks)
    assert stats["type_breakdown"]["water_pipe"]["count"] == 2
    assert stats["type_breakdown"]["water_pipe"]["days"] == 30
    assert stats["type_breakdown"]["sewer"]["count"] == 1
    assert stats["type_breakdown"]["sewer"]["days"] == 15


def test_get_schedule_stats_no_team_key_excluded():
    """Tasks without 'team' key must not pollute the teams list."""
    tasks = [
        {"id": "T1", "type": "design", "duration": 5},  # no team
        {"id": "T2", "type": "water_pipe", "team": "Водопровод", "duration": 10},
    ]
    stats = get_schedule_stats(tasks)
    assert stats["teams"] == ["Водопровод"]


# ---------------------------------------------------------------------------
# generate_demo_schedule
# ---------------------------------------------------------------------------


def test_generate_demo_schedule_returns_list():
    demo = generate_demo_schedule()
    assert isinstance(demo, list)


def test_generate_demo_schedule_has_tasks():
    demo = generate_demo_schedule()
    assert len(demo) >= 10  # at minimum several phases


def test_generate_demo_schedule_all_tasks_have_required_keys():
    required = {"id", "name", "type", "start_day", "duration"}
    demo = generate_demo_schedule()
    for task in demo:
        missing = required - task.keys()
        assert not missing, f"Task {task.get('id')} missing keys: {missing}"


def test_generate_demo_schedule_all_ids_unique():
    demo = generate_demo_schedule()
    ids = [t["id"] for t in demo]
    assert len(ids) == len(set(ids)), "Duplicate task IDs in demo schedule"


def test_generate_demo_schedule_start_day_positive():
    demo = generate_demo_schedule()
    for task in demo:
        assert task["start_day"] >= 1, f"Task {task['id']} has start_day < 1"


def test_generate_demo_schedule_duration_non_negative():
    """Duration must be >= 0 (0 is valid for milestones like protocol events)."""
    demo = generate_demo_schedule()
    for task in demo:
        assert task["duration"] >= 0, f"Task {task['id']} has negative duration"


def test_generate_demo_schedule_types_are_known():
    """All top-level tasks must use a type from constants.TYPE_LABELS."""
    from src.constants import TYPE_LABELS
    valid_types = set(TYPE_LABELS.keys())
    demo = generate_demo_schedule()
    for task in demo:
        tp = task.get("type")
        assert tp in valid_types, f"Task {task['id']} has unknown type '{tp}'"


def test_generate_demo_schedule_has_critical_path():
    """At least one task must be on the critical path."""
    demo = generate_demo_schedule()
    criticals = [t for t in demo if t.get("is_critical")]
    assert len(criticals) > 0, "Demo schedule has no critical tasks"


def test_generate_demo_schedule_get_schedule_stats_compatible():
    """generate_demo_schedule output must be parseable by get_schedule_stats."""
    demo = generate_demo_schedule()
    stats = get_schedule_stats(demo)
    assert stats["total_tasks"] == len(demo)
    assert stats["total_days"] > 0
