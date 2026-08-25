"""Проектната фаза: частите следват мрежите, а затварящите стъпки — частите.

FAILURE означава:

* „Водоснабдяване" и „Канализация" пак излизат с еднакви дни, докато мрежите
  са 11 664 срещу 4 183 метра (Тръстеник, 25.08.2026) — час проектант не се
  харчи еднакво за трикратно по-голяма мрежа;
* сметната документация пак свършва ПРЕДИ частта, която остойностява;
* или обратното — затварящата стъпка е вързана НАПРЕД, към съгласуванията,
  които вече чакат нея, и графикът престава да се подрежда топологично.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.work_package import (PackageItem, SpatialWorkPackage,
                              close_design_after_all_parts,
                              scale_design_parts_to_networks)

ВЕРИГИ = {"design": {"steps": [
    {"key": "water"}, {"key": "sewer"}, {"key": "boq"},
    {"key": "handover"}, {"key": "approvals"}, {"key": "building_permit"},
]}}


def _пакет(ид, верига, метри):
    мрежа = "К" if верига == "sewer_section" else "В"
    return SpatialWorkPackage(
        id=ид, name=ид, network=мрежа, chain=верига, street="—", front="Ф1",
        items=(PackageItem(source_ref=f"{ид}!1", activity_class="laying",
                           quantity=метри, unit="м", description="полагане"),))


def _задача(ид, стъпка, дни, родител="ПРО"):
    return {"id": ид, "name": стъпка, "chain_step": стъпка, "parent_id": родител,
            "duration": дни, "start_day": 1, "end_day": дни}


def _проектни_задачи():
    return [_задача("t_water", "water", 20), _задача("t_sewer", "sewer", 20),
            _задача("t_boq", "boq", 2), _задача("t_hand", "handover", 2),
            _задача("t_appr", "approvals", 1)]


def _пакети():
    return [SpatialWorkPackage(id="ПРО", name="Проектиране", network="В",
                               chain="design", street="—", front="Ф1"),
            _пакет("В1", "water_section", 11664.0),
            _пакет("К1", "sewer_section", 4183.0)]


def test_design_parts_follow_their_network():
    """При 2.8 : 1 метри водоснабдяването получава повече дни от канала."""
    задачи, бележки = scale_design_parts_to_networks(
        _проектни_задачи(), _пакети(), ВЕРИГИ)
    по_ид = {t["id"]: t for t in задачи}
    assert по_ид["t_water"]["duration"] > по_ид["t_sewer"]["duration"], бележки


def test_the_sum_of_the_two_parts_is_preserved():
    """Преразпределя се, не се раздува — срокът за проектиране е обявен."""
    вход = _проектни_задачи()
    беше = sum(t["duration"] for t in вход if t["chain_step"] in ("water", "sewer"))
    задачи, _ = scale_design_parts_to_networks(вход, _пакети(), ВЕРИГИ)
    сега = sum(t["duration"] for t in задачи if t["chain_step"] in ("water", "sewer"))
    assert abs(сега - беше) <= 1, f"{беше} → {сега}"


def test_closing_step_waits_for_every_part():
    """Сметната документация чака и водоснабдяването, и канализацията."""
    задачи, _ = close_design_after_all_parts(_проектни_задачи(), _пакети(), ВЕРИГИ)
    boq = next(t for t in задачи if t["id"] == "t_boq")
    предшественици = {d["predecessor_id"] for d in boq.get("dependencies") or []}
    assert {"t_water", "t_sewer"} <= предшественици


def test_closing_step_is_never_linked_forward():
    """Съгласуванията идват СЛЕД предаването — връзка към тях е цикъл."""
    задачи, _ = close_design_after_all_parts(_проектни_задачи(), _пакети(), ВЕРИГИ)
    предаване = next(t for t in задачи if t["id"] == "t_hand")
    предшественици = {d["predecessor_id"] for d in предаване.get("dependencies") or []}
    assert "t_appr" not in предшественици, "затворен цикъл проектиране → съгласуване"


def test_without_design_packages_nothing_happens():
    """Търг само за строителство: няма проектна фаза, няма и намеса."""
    задачи = _проектни_задачи()
    изход, бележки = close_design_after_all_parts(
        задачи, [_пакет("В1", "water_section", 100.0)], ВЕРИГИ)
    assert изход == задачи and not бележки
