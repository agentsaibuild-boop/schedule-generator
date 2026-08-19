"""Обявеното темпо трябва наистина да се достигне, не само да се обяви.

FAILURE означава: казваме „свихме веригата до 8.6 м/ден", а сборът ѝ е останал
друг — тоест числото в бележката не описва графика.

ИЗМЕРЕНО 19.08.2026: пропорционалното свиване НЕ стигаше до целта.  Задача от
един ден, умножена по 0.71, пак е един ден, а 270 от 300-те водни задачи са
точно такива.  Сборът оставаше 450 вместо 378 и два екипа изкарваха
водопровода за 256 дни вместо за 190 — при положение че бяха заети 89% от
времето, тоест не стояха.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.segment_scale import calibrate_to_declared_pace  # noqa: E402


class _Пакет:
    def __init__(self, pid, chain, items):
        self.id = pid
        self.chain = chain
        self.items = items


class _Позиция:
    def __init__(self, ref, quantity):
        self.source_ref = ref
        self.quantity = quantity


class _Ред:
    def __init__(self, ref, quantity, unit="m"):
        self.ref = ref
        self.quantity = quantity
        self.unit = unit


def _случай(дълги: list[float], единици: int):
    """Верига с няколко дълги задачи и много едeдневни — както е в живота."""
    задачи = [{"id": f"D{i}", "parent_id": "W1", "chain_step": "laying",
               "duration": д} for i, д in enumerate(дълги, 1)]
    задачи += [{"id": f"E{i}", "parent_id": "W1", "chain_step": "survey",
                "duration": 1} for i in range(единици)]
    пакети = [_Пакет("W1", "water_section", [_Позиция("КСС!1", 1000.0)])]
    boq = [_Ред("КСС!1", 1000.0)]
    return задачи, пакети, boq


def test_the_declared_pace_is_actually_reached(monkeypatch):
    """1000 м при 10 м/ден = 100 екипо-дни, каквото и да е разпределението."""
    monkeypatch.setenv("PACE_WATER", "10")
    задачи, пакети, boq = _случай([200.0, 100.0], единици=50)   # 350 общо

    calibrate_to_declared_pace(задачи, пакети, boq)

    assert sum(t["duration"] for t in задачи) == pytest.approx(100, abs=6)


def test_one_day_tasks_are_never_squeezed_to_zero(monkeypatch):
    """Операция, която се извършва, не трае нула."""
    monkeypatch.setenv("PACE_WATER", "50")      # цел 20 при 60 налични
    задачи, пакети, boq = _случай([10.0], единици=50)

    calibrate_to_declared_pace(задачи, пакети, boq)

    assert all(t["duration"] >= 1 for t in задачи)


def test_it_says_when_it_cannot_reach_the_target(monkeypatch):
    """Недостигнатата цел се КАЗВА, вместо да мине за постигната."""
    monkeypatch.setenv("PACE_WATER", "100")     # цел 10 при 50 еднодневни
    задачи, пакети, boq = _случай([], единици=50)

    _, бележки = calibrate_to_declared_pace(задачи, пакети, boq)

    assert any("Повече не може" in b for b in бележки), бележки


def test_without_a_declared_pace_nothing_moves(monkeypatch):
    monkeypatch.delenv("PACE_WATER", raising=False)
    задачи, пакети, boq = _случай([200.0], единици=10)
    преди = [t["duration"] for t in задачи]

    calibrate_to_declared_pace(задачи, пакети, boq)

    assert [t["duration"] for t in задачи] == преди


def test_the_note_matches_the_schedule(monkeypatch):
    """Числото в бележката трябва да е това, което наистина е станало."""
    monkeypatch.setenv("PACE_WATER", "10")
    задачи, пакети, boq = _случай([200.0, 100.0], единици=50)

    _, бележки = calibrate_to_declared_pace(задачи, пакети, boq)

    станало = sum(t["duration"] for t in задачи)
    assert any(f"стана {станало:.0f}" in b for b in бележки), бележки
