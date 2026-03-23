"""Unit tests for ScheduleBuilder.validate_schedule.

Covers all error and warning conditions:
  ERRORS: empty schedule, missing name, duplicate ID, negative start_day,
          end_day mismatch, missing dependency, circular dependency,
          FS violation, sub-activity out of bounds, zero total duration.
  WARNINGS: duration >365, missing dependency for non-first task,
            pipe/sewer without diameter, team overlap >2, gap >30 days,
            parent_id pointing to non-existent task.

FAILURE означава: src/schedule_builder.py :: validate_schedule е счупена —
валидаторът ще пропуска грешни графики или ще дава фалшиви предупреждения.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.schedule_builder import ScheduleBuilder

builder = ScheduleBuilder()
_v = builder.validate_schedule


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _task(
    id_: str,
    name: str,
    start: int = 1,
    duration: int = 5,
    deps: list[str] | None = None,
    end: int | None = None,
    parent_id: str | None = None,
    team: str | None = None,
    type_: str | None = None,
    diameter: str | None = None,
    sub_activities: list[dict] | None = None,
) -> dict:
    t: dict = {
        "id": id_,
        "name": name,
        "start_day": start,
        "duration": duration,
        "dependencies": deps or [],
    }
    if end is not None:
        t["end_day"] = end
    if parent_id is not None:
        t["parent_id"] = parent_id
    if team is not None:
        t["team"] = team
    if type_ is not None:
        t["type"] = type_
    if diameter is not None:
        t["diameter"] = diameter
    if sub_activities is not None:
        t["sub_activities"] = sub_activities
    return t


def _valid_single() -> list[dict]:
    """Minimal valid one-task schedule."""
    return [_task("T1", "Изкопни работи", start=1, duration=10, end=10)]


# ===========================================================================
# ERRORS
# ===========================================================================

def test_empty_schedule_is_invalid():
    result = _v([])
    assert result["valid"] is False
    assert any("празен" in e for e in result["errors"])


def test_valid_single_task_passes():
    result = _v(_valid_single())
    assert result["valid"] is True
    assert result["errors"] == []


def test_missing_name_produces_error():
    task = _task("T1", "", start=1, duration=5)
    result = _v([task])
    assert result["valid"] is False
    assert any("няма име" in e for e in result["errors"])


def test_duplicate_id_produces_error():
    t1 = _task("T1", "Задача А", start=1, duration=5, end=5)
    t2 = _task("T1", "Задача Б", start=10, duration=5, end=14)
    result = _v([t1, t2])
    assert result["valid"] is False
    assert any("Дублирано ID" in e for e in result["errors"])


def test_negative_start_day_produces_error():
    task = _task("T1", "Задача", start=-1, duration=5)
    result = _v([task])
    assert result["valid"] is False
    assert any("отрицателен начален ден" in e for e in result["errors"])


def test_end_day_mismatch_produces_error():
    # start=1 + duration=10 - 1 = 10, but we set end=15
    task = _task("T1", "Задача", start=1, duration=10, end=15)
    result = _v([task])
    assert result["valid"] is False
    assert any("end_day" in e and "≠" in e for e in result["errors"])


def test_end_day_consistent_passes():
    # start=1, duration=10 → end=10
    task = _task("T1", "Задача", start=1, duration=10, end=10)
    result = _v([task])
    assert result["valid"] is True


def test_dependency_on_nonexistent_id_produces_error():
    task = _task("T1", "Задача", start=1, duration=5, deps=["MISSING"])
    result = _v([task])
    assert result["valid"] is False
    assert any("несъществуващо ID" in e for e in result["errors"])


def test_circular_dependency_produces_error():
    t1 = _task("T1", "Задача А", start=1, duration=5, deps=["T2"])
    t2 = _task("T2", "Задача Б", start=10, duration=5, deps=["T1"])
    result = _v([t1, t2])
    assert result["valid"] is False
    assert any("Кръгова зависимост" in e for e in result["errors"])


def test_fs_violation_produces_error():
    # T1: start=1, duration=10 → ends day 10
    # T2: start=5 (before T1 ends) → FS violation
    t1 = _task("T1", "Предшественик", start=1, duration=10, end=10)
    t2 = _task("T2", "Наследник", start=5, duration=5, end=9, deps=["T1"])
    result = _v([t1, t2])
    assert result["valid"] is False
    assert any("започва ден" in e for e in result["errors"])


def test_fs_valid_when_successor_starts_after_predecessor():
    t1 = _task("T1", "Предшественик", start=1, duration=10, end=10)
    t2 = _task("T2", "Наследник", start=11, duration=5, end=15, deps=["T1"])
    result = _v([t1, t2])
    assert result["valid"] is True


def test_sub_activity_out_of_parent_bounds_produces_error():
    sub = {
        "id": "S1",
        "name": "Поддейност",
        "start_day": 1,
        "duration": 5,
        "end_day": 5,
    }
    # Parent: start=10, duration=20 → end=29
    # Sub starts at day 1 → before parent start
    parent = _task("T1", "Родител", start=10, duration=20, end=29, sub_activities=[sub])
    result = _v([parent])
    assert result["valid"] is False
    assert any("излиза извън обхвата" in e for e in result["errors"])


def test_sub_activity_within_bounds_passes():
    sub = {
        "id": "S1",
        "name": "Поддейност",
        "start_day": 12,
        "duration": 3,
        "end_day": 14,
    }
    parent = _task("T1", "Родител", start=10, duration=20, end=29, sub_activities=[sub])
    result = _v([parent])
    assert result["valid"] is True


# ===========================================================================
# WARNINGS
# ===========================================================================

def test_duration_over_365_produces_warning():
    task = _task("T1", "Дълга задача", start=1, duration=400)
    result = _v([task])
    assert any("365" in w for w in result["warnings"])


def test_duration_exactly_365_no_warning():
    task = _task("T1", "Гранична задача", start=1, duration=365)
    result = _v([task])
    assert not any("365" in w for w in result["warnings"])


def test_orphan_task_with_late_start_produces_warning():
    # No dependencies, no parent, starts on day 10 — suspicious
    task = _task("T1", "Сираче", start=10, duration=5)
    result = _v([task])
    assert any("няма предшественици" in w for w in result["warnings"])


def test_task_starting_on_day1_no_orphan_warning():
    task = _task("T1", "Начало", start=1, duration=5)
    result = _v([task])
    assert not any("няма предшественици" in w for w in result["warnings"])


def test_water_pipe_without_diameter_produces_warning():
    task = _task("T1", "Водопровод ул. Х", start=1, duration=10, type_="water_pipe")
    result = _v([task])
    assert any("diameter" in w.lower() or "DN" in w for w in result["warnings"])


def test_water_pipe_with_diameter_no_warning():
    task = _task(
        "T1", "Водопровод ул. Х", start=1, duration=10,
        type_="water_pipe", diameter="DN90",
    )
    result = _v([task])
    assert not any("diameter" in w.lower() or "DN" in w for w in result["warnings"])


def test_sewer_without_diameter_produces_warning():
    task = _task("T1", "Канализация", start=1, duration=10, type_="sewer")
    result = _v([task])
    assert any("diameter" in w.lower() or "DN" in w for w in result["warnings"])


def test_team_overlap_over_2_tasks_produces_warning():
    # Team "Бригада 1" assigned to 3 overlapping tasks
    t1 = _task("T1", "Задача А", start=1, duration=20, end=20, team="Бригада 1")
    t2 = _task("T2", "Задача Б", start=5, duration=20, end=24, team="Бригада 1")
    t3 = _task("T3", "Задача В", start=10, duration=20, end=29, team="Бригада 1")
    result = _v([t1, t2, t3])
    assert any("Бригада 1" in w for w in result["warnings"])


def test_gap_over_30_days_produces_warning():
    t1 = _task("T1", "Предшественик", start=1, duration=5, end=5)
    # gap = 50 - 5 - 1 = 44 days
    t2 = _task("T2", "Наследник", start=50, duration=5, end=54, deps=["T1"])
    result = _v([t1, t2])
    assert any("празнина" in w for w in result["warnings"])


def test_gap_within_30_days_no_warning():
    t1 = _task("T1", "Предшественик", start=1, duration=5, end=5)
    # gap = 16 - 5 - 1 = 10 days
    t2 = _task("T2", "Наследник", start=16, duration=5, end=20, deps=["T1"])
    result = _v([t1, t2])
    assert not any("празнина" in w for w in result["warnings"])


def test_nonexistent_parent_id_produces_warning():
    task = _task("T1", "Дете", start=1, duration=5, parent_id="NONEXISTENT")
    result = _v([task])
    assert any("parent_id" in w for w in result["warnings"])


def test_valid_schedule_no_warnings():
    t1 = _task("T1", "Изкопни работи", start=1, duration=10, end=10)
    t2 = _task("T2", "Полагане на тръби", start=11, duration=10, end=20, deps=["T1"])
    result = _v([t1, t2])
    assert result["valid"] is True
    assert result["warnings"] == []


# ---------------------------------------------------------------------------
# Edge cases — lines not covered by the above tests
# ---------------------------------------------------------------------------

def test_negative_duration_produces_warning():
    """Line 112: negative duration should generate a warning (not an error)."""
    task = _task("T1", "Задача", start=1, duration=-3, end=1)
    result = _v([task])
    assert any("отрицателна продължителност" in w for w in result["warnings"])


def test_sub_activity_without_end_day_out_of_bounds():
    """Lines 178-179: sub-activity with no end_day; computed end should still
    detect out-of-bounds."""
    # Parent: start=1, dur=5 → end=5.  Sub: start=4, dur=5 → computed end=8 > parent end.
    t = _task(
        "T1", "Родител", start=1, duration=5, end=5,
        sub_activities=[{"name": "Подзадача", "start_day": 4, "duration": 5}],
    )
    result = _v([t])
    assert any("излиза" in e for e in result["errors"])


def test_sub_activity_without_end_day_within_bounds_no_error():
    """Lines 178-179: sub-activity with no end_day that fits inside parent — no error."""
    t = _task(
        "T1", "Родител", start=1, duration=10, end=10,
        sub_activities=[{"name": "Подзадача", "start_day": 2, "duration": 3}],
    )
    result = _v([t])
    assert not any("излиза" in e for e in result["errors"])


def test_team_overlap_task_without_end_day():
    """Lines 257-258: team task missing end_day — should compute it from duration
    and still detect overlap when >2 tasks overlap."""
    # Three tasks in the same team, all overlapping; T3 has no end_day.
    t1 = _task("T1", "А", start=1, duration=10, end=10, team="Екип А")
    t2 = _task("T2", "Б", start=1, duration=10, end=10, team="Екип А")
    t3 = _task("T3", "В", start=1, duration=10, team="Екип А")  # no end_day
    result = _v([t1, t2, t3])
    assert any("Екип А" in w for w in result["warnings"])
