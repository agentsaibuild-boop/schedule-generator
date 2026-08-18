"""Етапите ги прави кодът, когато геометрия няма — и Σ = КСС по конструкция.

FAILURE означава: или количествата пак могат да не се сберат до КСС, или
моделът пак бива питан да съчини разчленяване, което го няма във входа.

ИЗМЕРЕНО 18.08.2026, 30 живи прогона на ЕДИН И СЪЩ търг: моделът връща между
22 и 132 пакета, а всичките 21 провала са в получаването на използваем
отговор — 6 мъртви прогона, 7 счупени JSON-а, 8 пъти Σ ≠ КСС.  Нито един
структурен инвариант надолу по веригата не пада.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.execution_batches import (  # noqa: E402
    allocate_execution_batches, split_exactly)


class _Ред:
    def __init__(self, ref, quantity, description, unit="m"):
        self.ref = ref
        self.quantity = quantity
        self.description = description
        self.unit = unit


def _ксс() -> list[_Ред]:
    return [
        _Ред("КСС!Kanalizaciya!3", 1182.0, "Изграждане на смесена канализационна мрежа"),
        _Ред("КСС!Kanalizaciya!4", 260.0, "Изграждане на смесена канализационна мрежа"),
        _Ред("КСС!Vodoprovod!5", 538.12, "Реконструкция на разпределителната мрежа"),
        _Ред("КСС!Vodoprovod!6", 174.0, "СВО", unit="брой"),
        _Ред("КСС!Пътна!7", 10824.0, "възстановяване на пътна настилка", unit="кв. м"),
        _Ред("КСС!ЕЛ и ТТ!8", 500.0, "Подземни ТТ кабели"),
        # Заглавен ред без количество — не бива да ражда работа.
        _Ред("КСС!Kanalizaciya!1", None, "ОБЩО"),
    ]


def _сборове(пакети) -> dict[str, float]:
    сбор: dict[str, float] = defaultdict(float)
    for p in пакети:
        for item in p["items"]:
            сбор[item["source_ref"]] += item["quantity"]
    return сбор


# ---------------------------------------------------------------------------
# Същината: количествата не могат да се загубят
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("етапи", [1, 2, 8, 14, 50])
def test_the_sum_is_exactly_the_boq(етапи):
    """Σ = КСС престава да е гейт, който се надяваме да мине."""
    редове = _ксс()
    сбор = _сборове(allocate_execution_batches(редове, етапи)["packages"])

    for ред in редове:
        if ред.quantity is None:
            assert str(ред.ref) not in сбор, "заглавен ред роди работа"
            continue
        assert сбор[str(ред.ref)] == pytest.approx(ред.quantity, abs=1e-6), (
            f"{ред.ref}: разпределено {сбор[str(ред.ref)]} срещу "
            f"{ред.quantity} в КСС")


@pytest.mark.parametrize("общо,части", [(1000.0, 3), (1182.0, 8), (0.1, 7),
                                        (874.55, 13)])
def test_splitting_never_drifts(общо, части):
    дялове = split_exactly(общо, части)

    assert len(дялове) == части
    assert sum(дялове) == pytest.approx(общо, abs=1e-6)


# ---------------------------------------------------------------------------
# Разчленяването е повторяемо, за разлика от модела
# ---------------------------------------------------------------------------


def test_the_same_input_gives_the_same_packages():
    """22–132 пакета за един и същ вход беше диагнозата.  Тук е 0 разброс."""
    първо = allocate_execution_batches(_ксс(), 8)["packages"]
    второ = allocate_execution_batches(_ксс(), 8)["packages"]

    assert [p["id"] for p in първо] == [p["id"] for p in второ]
    assert първо == второ


def test_every_row_with_a_quantity_is_routed():
    """`_coverer_class` рутира 28 от 28 реда на истинския търг — 100%."""
    резултат = allocate_execution_batches(_ксс(), 8)

    assert резултат["unroutable"] == []


def test_each_network_gets_its_own_chain():
    пакети = allocate_execution_batches(_ксс(), 4)["packages"]
    вериги = {p["chain"] for p in пакети}

    assert вериги == {"sewer_section", "water_section",
                      "pavement_section", "cable_section"}


# ---------------------------------------------------------------------------
# Имената не се преструват на геометрия
# ---------------------------------------------------------------------------


def test_names_do_not_claim_node_to_node_geometry():
    """„кл. 1 от РШ 1 до РШ 2" без чертеж е съчинено — тук такова не се ражда."""
    пакети = allocate_execution_batches(_ксс(), 8)["packages"]

    for p in пакети:
        assert "РШ" not in p["name"] and "КШ" not in p["name"]
        assert p["name"].startswith("Етап ")


def test_a_row_without_a_quantity_is_skipped():
    редове = [_Ред("КСС!Kanalizaciya!1", None, "ОБЩО")]

    assert allocate_execution_batches(редове, 8)["packages"] == []


def test_it_says_what_it_did():
    """Решението на кода трябва да се вижда, не да се подразбира."""
    бележки = allocate_execution_batches(_ксс(), 8)["notes"]

    assert any("направени от кода" in b for b in бележки)
