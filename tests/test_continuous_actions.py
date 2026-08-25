"""Действията вървят едно след друго, а екипът е на едно място.

FAILURE означава едно от трите, и трите се виждат с просто око в графика:

* участъкът пак диша — седем дни работа, пет дни пауза, отново и отново
  (мерено на Тръстеник преди поправката: 257 празни дни в 140 прехода).
  Човешките графици нямат такива дупки: Илиянци е чист FS с лаг 0;
* разтягането затваря дупка, за която няма ресурс — тогава графикът обещава
  бригада, каквато обектът няма;
* или екипът е на два клона наведнъж, защото редицата се прави по веригата, а
  не по екипа (изпълнителят, 25.08.2026: „различните екипи В работят по
  различни клонове").
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.work_package import (PackageItem, SpatialWorkPackage,
                              chain_sections_sequentially,
                              make_actions_continuous)

ВЕРИГИ = {"chains": {
    "water_section": {"steps": [{"key": "survey"}, {"key": "excavation"},
                                {"key": "laying"}]},
    "structure": {"steps": [{"key": "bottom"}, {"key": "walls"}]},
}}


def _задача(ид, родител, стъпка, начало, дни, **още):
    задача = {"id": ид, "parent_id": родител, "chain_step": стъпка,
              "name": стъпка, "start_day": начало, "duration": дни,
              "end_day": начало + дни - 1, "wbs_root": "construction"}
    задача.update(още)
    return задача


def _пакет(ид, верига="water_section", мрежа="В", фронт="ЕВ1"):
    return SpatialWorkPackage(
        id=ид, name=ид, network=мрежа, chain=верига, street="—", front=фронт,
        items=(PackageItem(source_ref=f"{ид}!1", activity_class="laying",
                           quantity=100.0, unit="м", description="полагане"),))


# ---------------------------------------------------------------------------
# Непрекъснатите действия
# ---------------------------------------------------------------------------


def test_gap_between_two_actions_is_closed():
    """Пет празни дни между изкопа и полагането изчезват."""
    задачи = [_задача("t1", "P1", "excavation", 1, 5),
              _задача("t2", "P1", "laying", 11, 5)]
    изход, бележки = make_actions_continuous(задачи)
    първа = изход[0]
    assert първа["end_day"] == 10, бележки
    assert първа["duration"] == 10
    assert изход[1]["start_day"] == 11, "второто действие НЕ бива да мърда"


def test_the_original_duration_stays_visible():
    """Сметнатото не се губи — стои в `computed_duration`."""
    задачи = [_задача("t1", "P1", "excavation", 1, 5),
              _задача("t2", "P1", "laying", 11, 5)]
    изход, _ = make_actions_continuous(задачи)
    assert изход[0]["computed_duration"] == 5
    assert изход[0]["continuous_fill"] == 5


def test_the_last_action_is_never_stretched():
    """Последното действие няма какво да чака — то си остава."""
    задачи = [_задача("t1", "P1", "excavation", 1, 5),
              _задача("t2", "P1", "laying", 6, 5)]
    изход, бележки = make_actions_continuous(задачи)
    assert изход[1]["duration"] == 5 and изход[1]["end_day"] == 10
    assert not бележки, "няма празнина, няма и бележка"


def test_a_successor_caps_the_stretch():
    """Щом някой чака задачата, тя не се разлива върху него."""
    задачи = [_задача("t1", "P1", "excavation", 1, 5),
              _задача("t2", "P1", "laying", 11, 5),
              _задача("t3", "P2", "survey", 8, 2,
                      dependencies=[{"predecessor_id": "t1", "type": "FS"}])]
    изход, _ = make_actions_continuous(задачи)
    assert изход[0]["end_day"] == 7, "разтегна се върху наследника си"


def test_design_is_left_alone():
    """Проектните части чакат вход, не стоят празни — тях не ги пипаме."""
    задачи = [_задача("d1", "ПРО", "water", 1, 5, wbs_root="design"),
              _задача("d2", "ПРО", "sewer", 11, 5, wbs_root="design")]
    изход, бележки = make_actions_continuous(задачи)
    assert изход[0]["duration"] == 5 and not бележки


def test_the_flag_switches_it_off(monkeypatch):
    """`CONTINUOUS_ACTIONS=0` връща старото поведение — за сравнение."""
    monkeypatch.setenv("CONTINUOUS_ACTIONS", "0")
    задачи = [_задача("t1", "P1", "excavation", 1, 5),
              _задача("t2", "P1", "laying", 11, 5)]
    изход, бележки = make_actions_continuous(задачи)
    assert изход[0]["duration"] == 5 and not бележки


# ---------------------------------------------------------------------------
# Редицата на екипа
# ---------------------------------------------------------------------------


def _редица(пакети, задачи):
    return chain_sections_sequentially(задачи, пакети, ВЕРИГИ)


def test_one_crew_takes_its_sections_one_after_another():
    """ЕВ1 не може да кара два клона наведнъж."""
    задачи = [_задача("a_laying", "A", "laying", 1, 5),
              _задача("b_survey", "B", "survey", 1, 5)]
    изход, _ = _редица([_пакет("A"), _пакет("B")], задачи)
    начало = next(t for t in изход if t["id"] == "b_survey")
    assert {d["predecessor_id"] for d in начало["dependencies"]} == {"a_laying"}


def test_a_facility_joins_its_crews_queue():
    """Шахтите на екипа вървят СЛЕД клоновете му, не върху тях."""
    задачи = [_задача("a_laying", "A", "laying", 1, 5),
              _задача("s_bottom", "S", "bottom", 1, 5)]
    пакети = [_пакет("A"), _пакет("S", верига="structure")]
    изход, _ = _редица(пакети, задачи)
    шахта = next(t for t in изход if t["id"] == "s_bottom")
    assert {d["predecessor_id"] for d in шахта["dependencies"]} == {"a_laying"}


def test_different_crews_do_not_wait_for_each_other():
    """Различните екипи В работят по различни клонове — паралелно."""
    задачи = [_задача("a_laying", "A", "laying", 1, 5),
              _задача("b_survey", "B", "survey", 1, 5)]
    пакети = [_пакет("A", фронт="ЕВ1"), _пакет("B", фронт="ЕВ2")]
    изход, _ = _редица(пакети, задачи)
    начало = next(t for t in изход if t["id"] == "b_survey")
    assert not (начало.get("dependencies") or [])


def test_a_link_that_would_close_a_circle_is_skipped():
    """Кръстосаните връзки вече вървят обратно — тогава редицата отстъпва."""
    задачи = [_задача("a_laying", "A", "laying", 1, 5,
                      dependencies=[{"predecessor_id": "b_survey",
                                     "type": "FS"}]),
              _задача("b_survey", "B", "survey", 1, 5)]
    изход, бележки = _редица([_пакет("A"), _пакет("B")], задачи)
    начало = next(t for t in изход if t["id"] == "b_survey")
    assert not (начало.get("dependencies") or []), "затворен кръг"
    assert any("кръг" in b for b in бележки), "мълчаливо пропусната връзка"
