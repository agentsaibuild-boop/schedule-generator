"""Паркът следва броя бригади, който търгът обявява.

FAILURE означава, че графикът пак ще противоречи на собствената си оферта:
обявяваме шест бригади, а ги караме да чакат за три багера, извлечени от
друг обект.  Числата в `resource_capacity.json` са, по собствената му бележка,
„РАЗУМНО ПОДРАЗБИРАНЕ за обект с два фронта, НЕ са измерени".

Обратното също е дефект: бройка, която ЧОВЕКЪТ е обявил (собственият екип за
съоръжения), не бива да се умножава — казаното е по-силно от изведеното.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.schedule_builder as sb


def _чист_кеш():
    sb._capacity_cache = None


def test_two_crews_change_nothing(monkeypatch):
    """Числата са писани за два фронта — при два фронта стоят както са."""
    monkeypatch.setenv("CREWS", "water_section:1,sewer_section:1")
    _чист_кеш()
    конфиг = sb._load_resource_capacity()
    assert конфиг.get("capacity", {}).get("Багер ескаватор") == 3
    assert "_приравнено_към" not in конфиг


def test_six_crews_triple_the_fleet(monkeypatch):
    """Шест бригади искат три пъти повече машини от две."""
    monkeypatch.setenv("CREWS", "water_section:4,sewer_section:2")
    _чист_кеш()
    конфиг = sb._load_resource_capacity()
    assert конфиг["capacity"]["Багер ескаватор"] == 9
    assert конфиг["_приравнено_към"] == 6


def test_declared_numbers_are_never_multiplied(monkeypatch):
    """Собственият екип за съоръжения е ОБЯВЕН — той си остава един."""
    monkeypatch.setenv("CREWS", "water_section:4,sewer_section:2")
    _чист_кеш()
    съоръжения = sb._load_resource_capacity()["headcount"].get(
        "Строителен работник (съоръжения)")
    assert съоръжения and съоръжения["налични"] == 3


def test_the_flag_switches_it_off(monkeypatch):
    """`FLEET_FOLLOWS_CREWS=0` връща стария парк — за сравнение при мерене."""
    monkeypatch.setenv("CREWS", "water_section:4,sewer_section:2")
    monkeypatch.setenv("FLEET_FOLLOWS_CREWS", "0")
    _чист_кеш()
    assert sb._load_resource_capacity()["capacity"]["Багер ескаватор"] == 3


def test_without_declared_crews_nothing_changes(monkeypatch):
    """Търг, който не обявява бригади, върви по подразбирането."""
    monkeypatch.delenv("CREWS", raising=False)
    _чист_кеш()
    конфиг = sb._load_resource_capacity()
    assert "_приравнено_към" not in конфиг
    _чист_кеш()
