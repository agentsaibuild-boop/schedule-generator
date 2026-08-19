"""Трите неща, които се ПИТАТ за всяка процедура, а не се гадаят.

FAILURE означава: пак вадим от документите решение, което е на изпълнителя —
и после обясняваме защо графикът не прилича на неговия.

Изпълнителят, 19.08.2026: „ти за всяка процедура трябва да питаш няколко
неща: кое е първо В или К, В как се полага — на изкоп или безизкопно, колко
екипа се предвиждат за изпълнението".
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.crew_sizing import declared_crews_for  # noqa: E402
from src.tender_parameters import (  # noqa: E402
    ПЪРВО_ВОДА, ПЪРВО_КАНАЛ, declared, declared_crews, declared_pace, describe,
    for_this_run, laying_method, order_of_networks)


# ---------------------------------------------------------------------------
# Ред на мрежите
# ---------------------------------------------------------------------------


def test_water_goes_first_by_default(monkeypatch):
    """Проверено в еталона: от 34 участъка с двете мрежи водата е първа в 15,
    заедно в 15, а каналът пръв само в 4."""
    monkeypatch.delenv("NETWORK_ORDER", raising=False)

    assert order_of_networks() == ПЪРВО_ВОДА


def test_the_order_can_be_declared(monkeypatch):
    monkeypatch.setenv("NETWORK_ORDER", "К")

    assert order_of_networks() == ПЪРВО_КАНАЛ


# ---------------------------------------------------------------------------
# Обявено темпо
# ---------------------------------------------------------------------------


def test_a_declared_pace_is_read(monkeypatch):
    monkeypatch.setenv("PACE_WATER", "8.6")

    assert declared_pace("water_section_hdd") == 8.6
    assert declared_pace("water_section") == 8.6


def test_no_pace_means_the_step_norms_apply(monkeypatch):
    monkeypatch.delenv("PACE_WATER", raising=False)

    assert declared_pace("water_section") is None


def test_a_nonsense_pace_is_ignored(monkeypatch):
    """Нула или буквар не бива да сваля веригата до нищо."""
    for стойност in ("0", "-3", "бързо"):
        monkeypatch.setenv("PACE_WATER", стойност)
        assert declared_pace("water_section") is None


# ---------------------------------------------------------------------------
# Обявени екипи
# ---------------------------------------------------------------------------


def test_declared_crews_are_parsed(monkeypatch):
    monkeypatch.setenv("CREWS", "water_section_hdd:2,sewer_section:3")

    assert declared_crews() == {"water_section_hdd": 2, "sewer_section": 3}


def test_garbage_in_the_crew_list_is_skipped(monkeypatch):
    monkeypatch.setenv("CREWS", "water_section_hdd:2,боклук,sewer_section:нула")

    assert declared_crews() == {"water_section_hdd": 2}


# ---------------------------------------------------------------------------
# Приетото се вижда
# ---------------------------------------------------------------------------


def test_what_was_assumed_is_stated(monkeypatch):
    """Мълчаливото допускане е същото като липсващото."""
    monkeypatch.setenv("LAYING_METHOD", "hdd")
    monkeypatch.setenv("PACE_WATER", "8.6")
    monkeypatch.delenv("CREWS", raising=False)

    редове = " ".join(describe())

    assert "водопровод" in редове
    assert "hdd" in редове
    assert "8.6" in редове
    assert "изчисляват се от срока" in редове


# ---------------------------------------------------------------------------
# Отговорите на въпросника — те важат за прогона, не `.env`
#
# ЗАЩО (19.08.2026).  Въпросникът питаше кое е първо В или К и колко екипа, но
# отговорът стигаше САМО до промпта на модела.  На детерминистичния път — по
# подразбиране — той нямаше никакъв ефект, а методът на полагане изобщо не се
# питаше.  Тоест графикът, който мерим, работникът не можеше да го получи от
# приложението.
# ---------------------------------------------------------------------------


def test_the_answer_wins_over_the_environment(monkeypatch):
    monkeypatch.setenv("NETWORK_ORDER", "В")
    monkeypatch.setenv("LAYING_METHOD", "hdd")

    with for_this_run({"network_order": "К", "laying_method": "open"}):
        assert order_of_networks() == ПЪРВО_КАНАЛ
        assert laying_method() == "open"


def test_the_environment_still_applies_when_nobody_was_asked(monkeypatch):
    monkeypatch.setenv("NETWORK_ORDER", "К")
    monkeypatch.setenv("LAYING_METHOD", "hdd")

    with for_this_run(None):
        assert order_of_networks() == ПЪРВО_КАНАЛ
        assert laying_method() == "hdd"


def test_the_context_does_not_leak_into_the_next_run(monkeypatch):
    """Два проекта в една сесия не бива да делят отговори."""
    monkeypatch.delenv("NETWORK_ORDER", raising=False)

    with for_this_run({"network_order": "К"}):
        assert order_of_networks() == ПЪРВО_КАНАЛ

    assert order_of_networks() == ПЪРВО_ВОДА
    assert declared() == {}


def test_the_context_is_restored_after_a_broken_run(monkeypatch):
    monkeypatch.delenv("NETWORK_ORDER", raising=False)

    try:
        with for_this_run({"network_order": "К"}):
            raise RuntimeError("прекъснат прогон")
    except RuntimeError:
        pass

    assert order_of_networks() == ПЪРВО_ВОДА


def test_the_chain_sees_the_declared_method():
    """`declared_laying_method` решава открит изкоп срещу сондаж."""
    from src.work_package import PackageItem, trenchless_chain

    позиции = (PackageItem(source_ref="r1", activity_class="laying",
                           quantity=100.0, unit="м", description="тръби"),)

    with for_this_run({"laying_method": "hdd"}):
        assert trenchless_chain("water_section", позиции) == "water_section_hdd"
    with for_this_run({"laying_method": "open"}):
        assert trenchless_chain("water_section", позиции) == "water_section"


def test_a_declared_crew_count_beats_the_calculation():
    """Срокът е ограничение от процедурата; с колко бригади се излиза в него
    е решение на изпълнителя."""
    with for_this_run({"declared_teams": 3}):
        assert declared_crews_for({"sewer_section": 2, "water_section": 1}) == {
            "sewer_section": 3, "water_section": 3}


def test_without_a_declared_count_the_calculation_stands():
    with for_this_run({}):
        assert declared_crews_for({"sewer_section": 2}) is None


def test_a_named_crew_list_beats_a_single_number():
    with for_this_run({"crews": {"sewer_section": 4}, "declared_teams": 2}):
        assert declared_crews_for({"sewer_section": 1}) == {"sewer_section": 4}


def test_zero_crews_is_not_an_answer():
    with for_this_run({"declared_teams": 0}):
        assert declared_crews_for({"sewer_section": 2}) is None


def test_a_declared_pace_wins_over_the_environment(monkeypatch):
    monkeypatch.setenv("PACE_WATER", "8.6")

    with for_this_run({"pace": {"water_section": 12.0}}):
        assert declared_pace("water_section") == 12.0
