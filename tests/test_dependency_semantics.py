"""Unit tests: зависимостите се валидират ПО ТИП, не всички като FS.

Одит 2026-07-23, точка 5: `dependency_ids()` извличаше само ID-то и хвърляше
типа и лага.  След това валидаторът проверяваше всичко като FS
(`successor.start > predecessor.end`).

Последици в двете посоки:
  - валидна SS връзка (изкоп и полагане тръгват заедно — урок #15) се
    обявяваше за ГРЕШКА и блокираше коректен график;
  - реални нарушения на SS/FF/SF минаваха НЕЗАБЕЛЯЗАНО.

Gate, който пропуска нарушения, е по-опасен от липсващ — носи печат
„проверено".

FAILURE означава: валидаторът пак съди всички връзки по един калъп.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.schedule_builder import (  # noqa: E402
    DependencyLink,
    ScheduleBuilder,
    dependency_links,
)

_v = ScheduleBuilder().validate_schedule


def _t(tid: str, start: int, duration: int, deps=None, **kw) -> dict:
    task = {
        "id": tid, "name": f"Задача {tid}", "start_day": start,
        "duration": duration, "end_day": start + duration - 1,
        "dependencies": deps or [],
    }
    task.update(kw)
    return task


def _link(pred: str, link_type: str, lag: int = 0) -> dict:
    return {"predecessor_id": pred, "type": link_type, "lag_days": lag}


# ===================================================================
# dependency_links — извличане на семантиката
# ===================================================================

def test_string_dependency_defaults_to_fs():
    links = dependency_links({"dependencies": ["A"]})
    assert links == [DependencyLink("A", "FS", 0)]


def test_dict_dependency_carries_type_and_lag():
    links = dependency_links({"dependencies": [_link("A", "SS", 3)]})
    assert links == [DependencyLink("A", "SS", 3)]


def test_task_level_type_applies_to_string_deps():
    """Старият формат от enrich_for_msproject: тип върху самата задача."""
    links = dependency_links({
        "dependencies": ["A"], "dependency_type": "FF", "lag_days": 5,
    })
    assert links == [DependencyLink("A", "FF", 5)]


def test_unknown_type_falls_back_to_fs():
    links = dependency_links({"dependencies": [_link("A", "ГЛУПОСТ")]})
    assert links[0].type == "FS"


def test_lowercase_type_is_normalised():
    links = dependency_links({"dependencies": [_link("A", "ss")]})
    assert links[0].type == "SS"


def test_negative_lag_is_kept():
    links = dependency_links({"dependencies": [_link("A", "FS", -3)]})
    assert links[0].lag_days == -3


def test_non_numeric_lag_becomes_zero():
    links = dependency_links({"dependencies": [{"predecessor_id": "A", "lag_days": "три"}]})
    assert links[0].lag_days == 0


def test_alternative_lag_key_is_accepted():
    links = dependency_links({"dependencies": [{"predecessor_id": "A", "lag": 4}]})
    assert links[0].lag_days == 4


def test_garbage_dependency_is_skipped():
    links = dependency_links({"dependencies": [None, {}, [], "A"]})
    assert [l.predecessor_id for l in links] == ["A"]


# ===================================================================
# FS — досегашното поведение остава
# ===================================================================

def test_fs_valid():
    assert _v([_t("A", 1, 10), _t("B", 11, 5, ["A"])])["valid"] is True


def test_fs_violation_is_caught():
    assert _v([_t("A", 1, 10), _t("B", 5, 5, ["A"])])["valid"] is False


def test_fs_with_lag_requires_the_gap():
    """FS+5: наследникът не бива да започне по-рано от ден 16."""
    ok = _v([_t("A", 1, 10), _t("B", 16, 5, [_link("A", "FS", 5)])])
    bad = _v([_t("A", 1, 10), _t("B", 13, 5, [_link("A", "FS", 5)])])
    assert ok["valid"] is True
    assert bad["valid"] is False


# ===================================================================
# SS — урок #15 (изкоп и полагане паралелно)
# ===================================================================

def test_ss_starting_together_is_valid():
    """Точният случай, който преди се обявяваше за грешка."""
    result = _v([_t("A", 1, 10), _t("B", 1, 10, [_link("A", "SS")])])
    assert result["valid"] is True, result["errors"]


def test_ss_with_one_day_lead_is_valid():
    """Урок #15: SS+1d — полагането тръгва ден след изкопа."""
    result = _v([_t("A", 1, 20), _t("B", 2, 19, [_link("A", "SS", 1)])])
    assert result["valid"] is True, result["errors"]


def test_ss_violation_is_caught():
    """Наследникът започва ПРЕДИ предшественика — нарушение и при SS."""
    result = _v([_t("A", 5, 10), _t("B", 1, 10, [_link("A", "SS")])])
    assert result["valid"] is False
    assert any("[SS]" in e for e in result["errors"])


def test_ss_lag_violation_is_caught():
    result = _v([_t("A", 1, 20), _t("B", 3, 10, [_link("A", "SS", 5)])])
    assert result["valid"] is False


# ===================================================================
# FF и SF
# ===================================================================

def test_ff_finishing_together_is_valid():
    result = _v([_t("A", 1, 10), _t("B", 5, 6, [_link("A", "FF")])])
    assert result["valid"] is True, result["errors"]


def test_ff_violation_is_caught():
    """Наследникът завършва преди предшественика."""
    result = _v([_t("A", 1, 20), _t("B", 1, 5, [_link("A", "FF")])])
    assert result["valid"] is False
    assert any("[FF]" in e for e in result["errors"])


def test_sf_valid():
    result = _v([_t("A", 10, 5), _t("B", 1, 12, [_link("A", "SF")])])
    assert result["valid"] is True, result["errors"]


def test_sf_violation_is_caught():
    result = _v([_t("A", 10, 5), _t("B", 1, 3, [_link("A", "SF")])])
    assert result["valid"] is False
    assert any("[SF]" in e for e in result["errors"])


# ===================================================================
# Съобщенията са разбираеми
# ===================================================================

def test_error_names_the_link_type():
    result = _v([_t("A", 5, 10), _t("B", 1, 10, [_link("A", "SS")])])
    assert any("[SS]" in e for e in result["errors"])


def test_error_mentions_the_lag():
    result = _v([_t("A", 1, 10), _t("B", 12, 5, [_link("A", "FS", 5)])])
    assert any("лаг 5д" in e for e in result["errors"])


def test_fs_error_has_no_type_marker():
    """FS е подразбиращият се тип — не се маркира, за да не шуми."""
    result = _v([_t("A", 1, 10), _t("B", 5, 5, ["A"])])
    assert not any("[FS]" in e for e in result["errors"])


# ===================================================================
# Застъпване на екипи
# ===================================================================

def test_two_simultaneous_tasks_for_one_team_warn():
    """Одит: прагът беше 3 задачи; за неделим екип конфликтът е при 2."""
    result = _v([
        _t("A", 1, 10, team="ЕВ1"),
        _t("B", 5, 10, team="ЕВ1", deps=None),
    ])
    assert any("ЕВ1" in w for w in result["warnings"])


def test_sequential_tasks_for_one_team_do_not_warn():
    result = _v([
        _t("A", 1, 10, team="ЕВ1"),
        _t("B", 11, 10, team="ЕВ1", deps=["A"]),
    ])
    assert not any("ЕВ1" in w for w in result["warnings"])


def test_different_teams_may_work_in_parallel():
    result = _v([
        _t("A", 1, 10, team="ЕВ1"),
        _t("B", 1, 10, team="ЕВ2"),
    ])
    assert result["warnings"] == []


def test_overlap_warning_reports_the_count():
    result = _v([
        _t("A", 1, 10, team="ЕВ1"),
        _t("B", 2, 10, team="ЕВ1"),
        _t("C", 3, 10, team="ЕВ1"),
    ])
    assert any("3 задачи" in w for w in result["warnings"])


def test_team_overlap_is_a_warning_not_an_error():
    """Паралелна работа може да е умишлена — не блокира графика."""
    result = _v([
        _t("A", 1, 10, team="ЕВ1"),
        _t("B", 5, 10, team="ЕВ1"),
    ])
    assert result["valid"] is True
