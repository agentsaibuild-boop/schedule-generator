"""Срокът е ДАДЕН — броят екипи се изчислява от него.

FAILURE означава: пак искаме от човека да каже с колко екипа се работи, а
после му обясняваме защо графикът не се събира в срока на процедурата.

ОБРЪЩАНЕТО (изпълнителят, 19.08.2026): „имаш 780 дни за всичко: 120
проектиране и останалите за строителство… изчисляваш, в зависимост от
параметрите на тръбите, с колко екипа трябва да се работи В и с колко К".
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.crew_sizing import (  # noqa: E402
    add_crews_while_they_pay, crews_for_deadline, fit_crews_to_deadline,
    work_by_chain)


class _Пакет:
    def __init__(self, pid, chain):
        self.id = pid
        self.chain = chain


def _задачи(pid: str, дни: list[float]) -> list[dict]:
    return [{"id": f"{pid}_{i}", "parent_id": pid, "chain_step": "laying",
             "duration": д} for i, д in enumerate(дни, 1)]


# ---------------------------------------------------------------------------
# Сметката
# ---------------------------------------------------------------------------


def test_work_is_summed_per_chain():
    пакети = [_Пакет("K1", "sewer_section"), _Пакет("V1", "water_section")]
    задачи = _задачи("K1", [10, 20]) + _задачи("V1", [5])

    assert work_by_chain(задачи, пакети) == {"sewer_section": 30.0,
                                             "water_section": 5.0}


def test_contract_phases_get_no_crews():
    """Проектирането и надзорът не са бригадна работа."""
    пакети = [_Пакет("D", "design"), _Пакет("S", "supervision")]
    задачи = _задачи("D", [100]) + _задачи("S", [660])

    assert work_by_chain(задачи, пакети) == {}


def test_crews_follow_from_the_deadline():
    """1000 екипо-дни за 660 налични → два екипа, не един."""
    пакети = [_Пакет("K1", "sewer_section")]
    задачи = _задачи("K1", [1000])

    екипи, бележки = crews_for_deadline(задачи, пакети, 660)

    assert екипи == {"sewer_section": 2}
    assert any("660" in b for b in бележки), "сметката не се вижда"


def test_a_short_chain_still_gets_one_crew():
    пакети = [_Пакет("EL1", "cable_section")]

    екипи, _ = crews_for_deadline(_задачи("EL1", [12]), пакети, 660)

    assert екипи == {"cable_section": 1}


# ---------------------------------------------------------------------------
# Дозирането с проба
# ---------------------------------------------------------------------------


def test_a_chain_over_the_deadline_gets_another_crew():
    """Теоретичният минимум не стига: измерено 48% използваемост."""
    обхвати = {1: {"sewer_section": 800}, 2: {"sewer_section": 500}}

    екипи, бележки = fit_crews_to_deadline(
        {"sewer_section": 1}, lambda e: обхвати[e["sewer_section"]], 660)

    assert екипи == {"sewer_section": 2}
    assert any("вдигам" in b for b in бележки)


def test_it_stops_when_everything_fits():
    извикан = []

    def разпиши(екипи):
        извикан.append(dict(екипи))
        return {"sewer_section": 400}

    екипи, бележки = fit_crews_to_deadline({"sewer_section": 2}, разпиши, 660)

    assert екипи == {"sewer_section": 2}
    assert len(извикан) == 1, "разписва повече пъти, отколкото трябва"
    assert any("събират" in b for b in бележки)


def test_it_gives_up_when_more_crews_do_not_help():
    """Верига, ограничена от ЗАВИСИМОСТИ, не се ускорява с хора.

    Без този изход дозирането би вдигало екипи до тавана и би обявило срок,
    който никой брой бригади не постига.
    """
    екипи, бележки = fit_crews_to_deadline(
        {"sewer_section": 1}, lambda e: {"sewer_section": 900}, 660, опити=6)

    assert екипи["sewer_section"] < 6, "вдига екипи, без да помагат"
    assert any("ограничени от зависимости" in b for b in бележки)


def test_the_cap_is_respected():
    екипи, _ = fit_crews_to_deadline(
        {"sewer_section": 1},
        lambda e: {"sewer_section": 900 - 10 * e["sewer_section"]},
        660, max_crews=3)

    assert екипи["sewer_section"] <= 3


# ---------------------------------------------------------------------------
# Екип, който се изплаща
# ---------------------------------------------------------------------------


class TestCrewsThatPayForThemselves:
    """Събирането в срока не е единственият въпрос.

    ИЗМЕРЕНО на Илиянци: водопроводът има 472 екипо-дни, което при ЕДИН екип е
    536 дни — и се „събира" в 660-те, затова дозирането спираше.  Но никой не
    кара един воден екип година и половина, когато с два свършва за 339, а
    човешкият еталон го прави за 190 с ДВА.
    """

    def test_a_crew_that_halves_the_chain_is_added(self):
        обхвати = {1: {"water": 536}, 2: {"water": 339}, 3: {"water": 300}}

        екипи, бележки = add_crews_while_they_pay(
            {"water": 1}, lambda e: обхвати[min(e["water"], 3)], печалба=0.20)

        assert екипи["water"] >= 2
        assert any("скъсява" in b for b in бележки), "решението не се вижда"

    def test_a_crew_that_changes_little_is_not_added(self):
        """Верига, ограничена от зависимости, не се ускорява с хора."""
        екипи, бележки = add_crews_while_they_pay(
            {"sewer": 2}, lambda e: {"sewer": 500}, печалба=0.20)

        assert екипи == {"sewer": 2}
        assert any("не биха скъсили" in b for b in бележки)

    def test_the_cap_holds(self):
        екипи, _ = add_crews_while_they_pay(
            {"water": 1},
            lambda e: {"water": 1000 // max(e["water"], 1)},
            печалба=0.10, max_crews=3)

        assert екипи["water"] <= 3
