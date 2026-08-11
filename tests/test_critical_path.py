"""Unit tests: критичен път и резерв (CPM).

СЪПОСТАВКА С ЕТАЛОН (2026-08-06): в програмния график НИТО ЕДНА от 204 задачи
не беше критична.  Не защото мрежата има резерв, а защото `is_critical` никой
не го пишеше — полето се четеше от Gantt-а, PDF-а и XML-а, но нямаше кой да го
сметне.  „Критичен път" в продукта беше декорация.

Обратният ход трябва да е ОГЛЕДАЛЕН на `reschedule` — иначе резервът е спрямо
друга мрежа, а не спрямо тази, по която са сметнати датите.

FAILURE означава: графикът показва критичен път, който не е критичният път —
инженерът съкращава грешната дейност и срокът не мърда.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.schedule_builder import ScheduleBuilder  # noqa: E402


@pytest.fixture
def builder() -> ScheduleBuilder:
    return ScheduleBuilder()


def _t(tid, duration, deps=None, **kw):
    task = {"id": tid, "name": tid, "duration": duration, "start_day": 1,
            "dependencies": deps or []}
    task.update(kw)
    return task


def _scheduled(builder, tasks):
    """Форуърдът е `reschedule` — CPM се смята върху неговия резултат."""
    out = builder.reschedule(tasks)
    assert not out["warnings"], out["warnings"]
    return out["schedule"]


# ---------------------------------------------------------------------------
# Основи
# ---------------------------------------------------------------------------


def test_straight_chain_is_fully_critical(builder):
    tasks = _scheduled(builder, [
        _t("A", 3),
        _t("B", 2, [{"predecessor_id": "A", "type": "FS", "lag_days": 0}]),
        _t("C", 4, [{"predecessor_id": "B", "type": "FS", "lag_days": 0}]),
    ])

    result = builder.compute_critical_path(tasks)
    by_id = {t["id"]: t for t in result["schedule"]}

    assert result["critical_count"] == 3
    assert all(by_id[k]["total_float"] == 0 for k in "ABC")
    assert result["project_finish"] == 9          # 3 + 2 + 4


def test_parallel_branch_gets_float(builder):
    """Късият клон има резерв — той НЕ е критичен."""
    tasks = _scheduled(builder, [
        _t("A", 5),
        _t("LONG", 10, [{"predecessor_id": "A", "type": "FS", "lag_days": 0}]),
        _t("SHORT", 2, [{"predecessor_id": "A", "type": "FS", "lag_days": 0}]),
        _t("END", 1, [{"predecessor_id": "LONG", "type": "FS", "lag_days": 0},
                      {"predecessor_id": "SHORT", "type": "FS", "lag_days": 0}]),
    ])

    result = builder.compute_critical_path(tasks)
    by_id = {t["id"]: t for t in result["schedule"]}

    assert by_id["LONG"]["is_critical"] is True
    assert by_id["SHORT"]["is_critical"] is False
    assert by_id["SHORT"]["total_float"] == 8     # 10 - 2
    assert by_id["A"]["is_critical"] and by_id["END"]["is_critical"]


def test_critical_path_is_neither_empty_nor_everything(builder):
    """Приемателният критерий от одита: нито 0%, нито ~100%."""
    tasks = _scheduled(builder, [
        _t("A", 5),
        _t("B", 10, [{"predecessor_id": "A", "type": "FS", "lag_days": 0}]),
        _t("C", 2, [{"predecessor_id": "A", "type": "FS", "lag_days": 0}]),
        _t("D", 3, [{"predecessor_id": "A", "type": "FS", "lag_days": 0}]),
        _t("E", 1, [{"predecessor_id": "B", "type": "FS", "lag_days": 0},
                    {"predecessor_id": "C", "type": "FS", "lag_days": 0},
                    {"predecessor_id": "D", "type": "FS", "lag_days": 0}]),
    ])

    result = builder.compute_critical_path(tasks)
    share = result["critical_count"] / len(tasks)

    assert 0 < share < 1


# ---------------------------------------------------------------------------
# Типовете връзки — огледални на `reschedule`
# ---------------------------------------------------------------------------


def test_ss_link_with_lag(builder):
    """SS+2: наследникът тръгва 2 дни след НАЧАЛОТО на предшественика."""
    tasks = _scheduled(builder, [
        _t("A", 10),
        _t("B", 8, [{"predecessor_id": "A", "type": "SS", "lag_days": 2}]),
    ])
    by_id = {t["id"]: t for t in tasks}
    assert by_id["B"]["start_day"] == 3

    result = builder.compute_critical_path(tasks)
    floats = {t["id"]: t["total_float"] for t in result["schedule"]}

    # A свършва ден 10, B свършва ден 10 → и двете определят края.
    assert floats["A"] == 0
    assert floats["B"] == 0


def test_ff_link_keeps_successor_from_finishing_early(builder):
    tasks = _scheduled(builder, [
        _t("A", 10),
        _t("B", 3, [{"predecessor_id": "A", "type": "FF", "lag_days": 0}]),
    ])
    result = builder.compute_critical_path(tasks)
    by_id = {t["id"]: t for t in result["schedule"]}

    assert by_id["A"]["total_float"] == 0
    assert by_id["B"]["total_float"] == 0


def test_fs_lag_is_absorbed_by_float_downstream(builder):
    tasks = _scheduled(builder, [
        _t("A", 2),
        _t("B", 2, [{"predecessor_id": "A", "type": "FS", "lag_days": 10}]),
    ])
    result = builder.compute_critical_path(tasks)
    by_id = {t["id"]: t for t in result["schedule"]}

    assert by_id["B"]["start_day"] == 13
    assert by_id["A"]["total_float"] == 0
    assert by_id["B"]["total_float"] == 0


# ---------------------------------------------------------------------------
# Договорен срок
# ---------------------------------------------------------------------------


def test_deadline_earlier_than_finish_gives_negative_float(builder):
    """Отрицателен резерв = реално закъснение спрямо договора, не грешка."""
    tasks = _scheduled(builder, [
        _t("A", 5),
        _t("B", 5, [{"predecessor_id": "A", "type": "FS", "lag_days": 0}]),
    ])

    result = builder.compute_critical_path(tasks, deadline_day=7)
    by_id = {t["id"]: t for t in result["schedule"]}

    assert by_id["B"]["total_float"] == -3
    assert by_id["A"]["total_float"] == -3
    assert by_id["B"]["is_critical"] is True


def test_deadline_later_than_finish_gives_slack_to_all(builder):
    tasks = _scheduled(builder, [_t("A", 5)])
    result = builder.compute_critical_path(tasks, deadline_day=20)
    assert result["schedule"][0]["total_float"] == 15
    assert result["schedule"][0]["is_critical"] is False


# ---------------------------------------------------------------------------
# Обобщаващи задачи и milestone-и
# ---------------------------------------------------------------------------


def test_summary_is_critical_when_a_child_is(builder):
    tasks = _scheduled(builder, [
        {"id": "S", "name": "Участък", "duration": 0, "start_day": 1,
         "dependencies": [], "is_summary": True},
        _t("A", 5, parent_id="S"),
    ])

    result = builder.compute_critical_path(tasks)
    by_id = {t["id"]: t for t in result["schedule"]}

    assert by_id["A"]["is_critical"] is True
    assert by_id["S"]["is_critical"] is True


def test_summary_does_not_get_its_own_criticality(builder):
    tasks = _scheduled(builder, [
        {"id": "S", "name": "Участък", "duration": 0, "start_day": 1,
         "dependencies": [], "is_summary": True},
        _t("A", 2, parent_id="S"),
        _t("B", 30),
    ])

    result = builder.compute_critical_path(tasks)
    by_id = {t["id"]: t for t in result["schedule"]}

    assert by_id["B"]["is_critical"] is True
    assert by_id["A"]["is_critical"] is False
    assert by_id["S"]["is_critical"] is False     # детето не е критично


def test_milestone_gets_float_like_any_node(builder):
    tasks = _scheduled(builder, [
        _t("A", 5),
        {"id": "MS", "name": "Край", "duration": 0, "start_day": 1,
         "milestone": True,
         "dependencies": [{"predecessor_id": "A", "type": "FS", "lag_days": 0}]},
    ])
    result = builder.compute_critical_path(tasks)
    by_id = {t["id"]: t for t in result["schedule"]}

    assert by_id["MS"]["total_float"] == 0
    assert by_id["MS"]["is_critical"] is True


# ---------------------------------------------------------------------------
# Защитни случаи
# ---------------------------------------------------------------------------


def test_cycle_does_not_crash_and_reports(builder):
    tasks = [
        _t("A", 2, [{"predecessor_id": "B", "type": "FS", "lag_days": 0}]),
        _t("B", 2, [{"predecessor_id": "A", "type": "FS", "lag_days": 0}]),
    ]
    result = builder.compute_critical_path(tasks)

    assert result["critical_count"] == 0
    assert result["warnings"]
    assert "кръгова" in result["warnings"][0]


def test_empty_schedule_is_safe(builder):
    result = builder.compute_critical_path([])
    assert result["critical"] == [] and result["critical_count"] == 0


def test_input_is_not_mutated(builder):
    tasks = _scheduled(builder, [_t("A", 3)])
    builder.compute_critical_path(tasks)
    assert "is_critical" not in tasks[0]
    assert "total_float" not in tasks[0]


def test_orphan_dependency_is_ignored(builder):
    tasks = [_t("A", 3, [{"predecessor_id": "НЯМА", "type": "FS", "lag_days": 0}])]
    result = builder.compute_critical_path(tasks)
    assert result["schedule"][0]["is_critical"] is True
