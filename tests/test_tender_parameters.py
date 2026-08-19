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

from src.tender_parameters import (  # noqa: E402
    ПЪРВО_ВОДА, ПЪРВО_КАНАЛ, declared_crews, declared_pace, describe,
    order_of_networks)


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
