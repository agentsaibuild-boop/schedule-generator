"""Unit tests for ScheduleBuilder.adjust_schedule and validate_modification.

FAILURE означава: ScheduleBuilder.adjust_schedule / validate_modification
е счупен — chat-базираното редактиране на графика не работи.
"""

from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.schedule_builder import ScheduleBuilder


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def builder() -> ScheduleBuilder:
    return ScheduleBuilder()


def _task(tid: str, name: str, start: int, dur: int, deps: list[str] | None = None) -> dict:
    return {
        "id": tid,
        "name": name,
        "start_day": start,
        "duration": dur,
        "end_day": start + dur - 1,
        "dependencies": deps or [],
    }


def _chain() -> list[dict]:
    """A → B → C linear chain."""
    return [
        _task("A", "Задача А", 0, 10),
        _task("B", "Задача Б", 10, 5, deps=["A"]),
        _task("C", "Задача В", 15, 3, deps=["B"]),
    ]


# ---------------------------------------------------------------------------
# adjust_schedule — basic field changes
# ---------------------------------------------------------------------------

class TestAdjustScheduleBasic:
    def test_unknown_task_returns_error(self, builder):
        sched = _chain()
        result = builder.adjust_schedule(sched, {"task_id": "Z", "field": "name", "new_value": "X"})
        assert "error" in result
        assert result["affected_count"] == 0

    def test_rename_task(self, builder):
        sched = _chain()
        result = builder.adjust_schedule(sched, {"task_id": "A", "field": "name", "new_value": "Ново"})
        assert "error" not in result
        names = {t["id"]: t["name"] for t in result["schedule"]}
        assert names["A"] == "Ново"
        assert names["B"] == "Задача Б"  # unchanged

    def test_rename_does_not_mutate_original(self, builder):
        sched = _chain()
        orig_name = sched[0]["name"]
        builder.adjust_schedule(sched, {"task_id": "A", "field": "name", "new_value": "Ново"})
        assert sched[0]["name"] == orig_name

    def test_affected_count_is_one_without_cascade(self, builder):
        sched = _chain()
        result = builder.adjust_schedule(sched, {
            "task_id": "A", "field": "duration", "new_value": 20, "cascade": False
        })
        assert result["affected_count"] == 1


# ---------------------------------------------------------------------------
# adjust_schedule — duration + cascade
# ---------------------------------------------------------------------------

class TestAdjustScheduleDurationCascade:
    def test_duration_change_updates_end_day(self, builder):
        sched = _chain()
        result = builder.adjust_schedule(sched, {
            "task_id": "A", "field": "duration", "new_value": 20, "cascade": False
        })
        task_a = next(t for t in result["schedule"] if t["id"] == "A")
        assert task_a["duration"] == 20
        assert task_a["end_day"] == 19  # start=0, dur=20

    def test_cascade_shifts_direct_dependent(self, builder):
        sched = _chain()
        # Extend A by 5 days (10→15), cascade should push B and C
        result = builder.adjust_schedule(sched, {
            "task_id": "A", "field": "duration", "new_value": 15, "cascade": True
        })
        tasks = {t["id"]: t for t in result["schedule"]}
        assert tasks["B"]["start_day"] == 15   # was 10, delta=+5
        assert tasks["C"]["start_day"] == 20   # was 15, delta=+5

    def test_cascade_shifts_transitive_dependent(self, builder):
        sched = _chain()
        result = builder.adjust_schedule(sched, {
            "task_id": "A", "field": "duration", "new_value": 20, "cascade": True
        })
        tasks = {t["id"]: t for t in result["schedule"]}
        # delta = 10 (20-10)
        assert tasks["C"]["start_day"] == 25   # 15 + 10

    def test_cascade_updates_end_day_of_shifted_task(self, builder):
        sched = _chain()
        result = builder.adjust_schedule(sched, {
            "task_id": "A", "field": "duration", "new_value": 15, "cascade": True
        })
        tasks = {t["id"]: t for t in result["schedule"]}
        # B: start=15, dur=5 → end=19
        assert tasks["B"]["end_day"] == 19

    def test_shrink_cascade_shifts_backwards(self, builder):
        sched = _chain()
        # Shrink A from 10 to 5 (delta = -5), B and C should move left
        result = builder.adjust_schedule(sched, {
            "task_id": "A", "field": "duration", "new_value": 5, "cascade": True
        })
        tasks = {t["id"]: t for t in result["schedule"]}
        assert tasks["B"]["start_day"] == 5
        assert tasks["C"]["start_day"] == 10

    def test_no_cascade_leaves_dependents_unchanged(self, builder):
        sched = _chain()
        result = builder.adjust_schedule(sched, {
            "task_id": "A", "field": "duration", "new_value": 20, "cascade": False
        })
        tasks = {t["id"]: t for t in result["schedule"]}
        assert tasks["B"]["start_day"] == 10  # original, not shifted
        assert tasks["C"]["start_day"] == 15

    def test_cascade_affected_count_includes_dependents(self, builder):
        sched = _chain()
        result = builder.adjust_schedule(sched, {
            "task_id": "A", "field": "duration", "new_value": 15, "cascade": True
        })
        # A itself (1) + B + C = 3
        assert result["affected_count"] == 3

    def test_sub_activities_shifted_by_cascade(self, builder):
        sched = [
            _task("A", "А", 0, 10),
            {
                "id": "B",
                "name": "Б",
                "start_day": 10,
                "duration": 5,
                "end_day": 14,
                "dependencies": ["A"],
                "sub_activities": [
                    {"name": "Sub1", "start_day": 10, "end_day": 12, "duration": 3}
                ],
            },
        ]
        result = builder.adjust_schedule(sched, {
            "task_id": "A", "field": "duration", "new_value": 15, "cascade": True
        })
        tasks = {t["id"]: t for t in result["schedule"]}
        sub = tasks["B"]["sub_activities"][0]
        assert sub["start_day"] == 15  # shifted by +5


# ---------------------------------------------------------------------------
# adjust_schedule — sub-activity out-of-bounds warning
# ---------------------------------------------------------------------------

class TestAdjustScheduleSubActivityWarning:
    def test_duration_shrink_warns_if_sub_exceeds_new_end(self, builder):
        sched = [
            {
                "id": "A",
                "name": "А",
                "start_day": 0,
                "duration": 20,
                "end_day": 19,
                "dependencies": [],
                "sub_activities": [
                    {"name": "Sub1", "start_day": 15, "end_day": 19, "duration": 5}
                ],
            }
        ]
        result = builder.adjust_schedule(sched, {
            "task_id": "A", "field": "duration", "new_value": 10, "cascade": False
        })
        assert any("поддейност" in w.lower() or "излизат" in w.lower()
                   for w in result["warnings"])


# ---------------------------------------------------------------------------
# validate_modification
# ---------------------------------------------------------------------------

class TestValidateModification:
    def test_identical_schedules_is_valid(self, builder):
        sched = _chain()
        result = builder.validate_modification(sched, deepcopy(sched), "без промяна")
        assert result["valid"] is True
        assert result["unintended_changes"] == []
        assert result["missing_tasks"] == []
        assert result["new_tasks"] == []

    def test_intended_change_in_mentioned_task_is_valid(self, builder):
        # _TASK_ID_RE requires digits (e.g. В01), so use matching IDs
        before = [
            _task("В01", "Водопровод", 0, 10),
            _task("В02", "Монтаж", 10, 5, deps=["В01"]),
        ]
        after = deepcopy(before)
        after[0]["duration"] = 20  # В01 mentioned in request
        result = builder.validate_modification(before, after, "промени В01 на 20 дни")
        assert result["valid"] is True
        assert result["unintended_changes"] == []

    def test_unintended_change_in_unmentioned_task(self, builder):
        before = _chain()
        after = deepcopy(before)
        after[2]["duration"] = 99  # task C changed, only A mentioned
        result = builder.validate_modification(before, after, "промени A")
        assert result["valid"] is False
        unintended_ids = [c["id"] for c in result["unintended_changes"]]
        assert "C" in unintended_ids

    def test_missing_task_is_detected(self, builder):
        before = _chain()
        after = [t for t in deepcopy(before) if t["id"] != "B"]
        result = builder.validate_modification(before, after, "нищо")
        assert result["valid"] is False
        assert "B" in result["missing_tasks"]
        assert any("премахнал" in w for w in result["warnings"])

    def test_new_task_is_detected(self, builder):
        before = _chain()
        after = deepcopy(before)
        after.append(_task("D", "Нова", 20, 5))
        result = builder.validate_modification(before, after, "нищо")
        assert result["valid"] is False
        assert "D" in result["new_tasks"]
        assert any("добавил" in w for w in result["warnings"])

    def test_cascade_successor_change_is_allowed(self, builder):
        """If В01 is mentioned and К01 depends on it, changing К01 is allowed (cascade)."""
        before = [
            _task("В01", "Водопровод", 0, 10),
            _task("К01", "Канализация", 10, 5, deps=["В01"]),
            _task("К02", "Настилки", 15, 3, deps=["К01"]),
        ]
        after = deepcopy(before)
        # В01 extended → К01 and К02 shift (cascade-reachable)
        after[0]["duration"] = 15
        after[1]["start_day"] = 15
        after[1]["end_day"] = 19
        after[2]["start_day"] = 20
        after[2]["end_day"] = 22
        result = builder.validate_modification(before, after, "промени В01 на 15 дни")
        unintended_ids = [c["id"] for c in result["unintended_changes"]]
        assert "К01" not in unintended_ids
        assert "К02" not in unintended_ids
        assert result["valid"] is True

    def test_task_count_match_flag(self, builder):
        before = _chain()
        after = deepcopy(before)
        after.append(_task("D", "Нова", 20, 5))
        result = builder.validate_modification(before, after, "нищо")
        assert result["task_count_match"] is False

    def test_ids_match_flag(self, builder):
        before = _chain()
        after = [t for t in deepcopy(before) if t["id"] != "C"]
        result = builder.validate_modification(before, after, "нищо")
        assert result["ids_match"] is False


# ---------------------------------------------------------------------------
# adjust_schedule — cascade guard (>50 affected tasks, lines 442-454)
# ---------------------------------------------------------------------------

def _fan_out(n: int = 52) -> list[dict]:
    """Root task A with n direct dependents — triggers the >50 cascade guard."""
    root = _task("A", "Корен", 0, 10)
    dependents = [_task(f"T{i:03d}", f"Задача {i}", 10, 5, deps=["A"]) for i in range(n)]
    return [root] + dependents


class TestAdjustScheduleCascadeGuard:
    def test_cascade_guard_emits_warning_when_over_50(self, builder):
        sched = _fan_out(52)  # 52 direct dependents → len(cascaded)=52 > 50
        result = builder.adjust_schedule(sched, {
            "task_id": "A", "field": "duration", "new_value": 15, "cascade": True,
        })
        assert any("каскадна" in w.lower() or "Каскадна" in w for w in result["warnings"])

    def test_cascade_guard_returns_affected_count_one(self, builder):
        sched = _fan_out(52)
        result = builder.adjust_schedule(sched, {
            "task_id": "A", "field": "duration", "new_value": 15, "cascade": True,
        })
        assert result["affected_count"] == 1

    def test_cascade_guard_does_not_shift_dependents(self, builder):
        sched = _fan_out(52)
        result = builder.adjust_schedule(sched, {
            "task_id": "A", "field": "duration", "new_value": 15, "cascade": True,
        })
        # Dependents must stay at their original start_day (no cascade applied)
        for t in result["schedule"]:
            if t["id"].startswith("T"):
                assert t["start_day"] == 10

    def test_cascade_guard_still_applies_target_change(self, builder):
        sched = _fan_out(52)
        result = builder.adjust_schedule(sched, {
            "task_id": "A", "field": "duration", "new_value": 15, "cascade": True,
        })
        root = next(t for t in result["schedule"] if t["id"] == "A")
        assert root["duration"] == 15
        assert root["end_day"] == 14  # start=0, dur=15

    def test_cascade_under_limit_applies_normally(self, builder):
        """Exactly 50 dependents → no guard (50 == limit, not strictly greater)."""
        sched = _fan_out(50)
        result = builder.adjust_schedule(sched, {
            "task_id": "A", "field": "duration", "new_value": 15, "cascade": True,
        })
        # All 50 dependents should be shifted
        for t in result["schedule"]:
            if t["id"].startswith("T"):
                assert t["start_day"] == 15


# ---------------------------------------------------------------------------
# adjust_schedule — old_end is None (line 405-406)
# ---------------------------------------------------------------------------

class TestAdjustScheduleMissingEndDay:
    def test_task_without_end_day_computes_delta_correctly(self, builder):
        """Target task has no end_day — delta must still be computed from duration."""
        sched = [
            {"id": "A", "name": "А", "start_day": 0, "duration": 10, "dependencies": []},
            _task("B", "Б", 10, 5, deps=["A"]),
        ]
        result = builder.adjust_schedule(sched, {
            "task_id": "A", "field": "duration", "new_value": 15, "cascade": True,
        })
        tasks = {t["id"]: t for t in result["schedule"]}
        assert tasks["B"]["start_day"] == 15  # delta = 15-10 = +5

    def test_sub_activity_without_end_day_does_not_raise(self, builder):
        """Sub-activity missing end_day should not crash the sub-bounds check."""
        sched = [
            {
                "id": "A", "name": "А", "start_day": 0, "duration": 20,
                "dependencies": [],
                "sub_activities": [
                    {"name": "Sub", "start_day": 15, "duration": 3},  # no end_day
                ],
            }
        ]
        # Shrinking A so that sub would exceed — just must not raise
        result = builder.adjust_schedule(sched, {
            "task_id": "A", "field": "duration", "new_value": 10, "cascade": False,
        })
        assert "schedule" in result


# ---------------------------------------------------------------------------
# _diff_task
# ---------------------------------------------------------------------------

class TestDiffTask:
    def test_identical_tasks_no_diff(self):
        t = _task("A", "Тест", 0, 5)
        assert ScheduleBuilder._diff_task(t, deepcopy(t)) == []

    def test_duration_change_detected(self):
        old = _task("A", "Тест", 0, 5)
        new = {**old, "duration": 10}
        assert "duration" in ScheduleBuilder._diff_task(old, new)

    def test_name_change_detected(self):
        old = _task("A", "Стар", 0, 5)
        new = {**old, "name": "Нов"}
        assert "name" in ScheduleBuilder._diff_task(old, new)

    def test_dependency_change_detected(self):
        old = _task("A", "Тест", 0, 5, deps=["X"])
        new = {**old, "dependencies": ["Y"]}
        assert "dependencies" in ScheduleBuilder._diff_task(old, new)

    def test_irrelevant_field_ignored(self):
        old = _task("A", "Тест", 0, 5)
        new = {**old, "notes_msp": "нова бележка"}
        assert ScheduleBuilder._diff_task(old, new) == []
