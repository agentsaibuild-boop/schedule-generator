"""Еднократната работа се прави ВЕДНЪЖ, когато обектът е обявен за едно трасе.

FAILURE означава: src/single_route.py е счупен — или изпитването, дезинфекцията
и присъединяването пак се повтарят на всеки участък (тогава графикът за един
непрекъснат тласкател не прилича на човешкия), или свиването е плъзнало върху
разпределителна мрежа, където всеки клон СЕ изпитва сам (еталонът за Илиянци:
23 водопроводни участъка, 23 дезинфекции).

Мерилото е човешкият график на тласкателя „Образцов чифлик" (ВиК Русе, 1268 м):
три изпълнителски участъка, но изпитване, дезинфекция и присъединяване по
ВЕДНЪЖ за целия водопровод.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.segment_scale import scale_segment_overhead  # noqa: E402
from src.single_route import collapse_route_wide_steps  # noqa: E402
from src.tender_parameters import for_this_run, single_route_networks  # noqa: E402

ВЕРИГИ = {
    "chains": {
        "water_section": {
            "wbs_root": "construction",
            "observed_count": 23,
            "observed_length_m": 3247.0,
            "steps": [
                {"key": "laying", "median_days": 1.0, "covers": ["laying"]},
                {"key": "fittings", "median_days": 1.0, "covers": ["backfill"]},
                {"key": "pressure_test", "median_days": 1.0, "covers": ["testing"],
                 "once_per_route": True},
                {"key": "disinfection", "median_days": 4.0,
                 "covers": ["disinfection"], "once_per_route": True},
                {"key": "valves", "median_days": 1.0, "covers": ["manhole"]},
                {"key": "tie_in", "median_days": 1.0, "covers": ["backfill"],
                 "once_per_route": True},
            ],
        },
    }
}

СТЪПКИ = [s["key"] for s in ВЕРИГИ["chains"]["water_section"]["steps"]]
ЕДНОКРАТНИ = ("pressure_test", "disinfection", "tie_in")


class Количество:
    def __init__(self, метри: float) -> None:
        self.activity_class = "laying"
        self.unit = "m"
        self.quantity = метри


class Пакет:
    def __init__(self, pid: str, метри: float, мрежа: str = "В") -> None:
        self.id = pid
        self.chain = "water_section"
        self.network = мрежа
        self.items = (Количество(метри),)


def _участък(pid: str) -> list[dict]:
    """Един участък по цялата верига, всяка стъпка закачена за предходната."""
    задачи: list[dict] = []
    предходна: str | None = None
    for ключ in СТЪПКИ:
        tid = f"{pid}_{ключ}"
        задачи.append({
            "id": tid, "parent_id": pid, "chain_step": ключ,
            "name": f"{ключ} — {pid}", "network": "В",
            "duration": 1.0, "duration_source": "chain_template",
            "dependencies": ([{"predecessor_id": предходна, "type": "FS",
                               "lag_days": 0}] if предходна else []),
        })
        предходна = tid
    return задачи


def _трасе(брой: int = 3, метри: float = 1268.0) -> tuple[list[dict], list[Пакет]]:
    задачи: list[dict] = []
    пакети: list[Пакет] = []
    for i in range(брой):
        pid = f"В{i + 1}"
        задачи += _участък(pid)
        пакети.append(Пакет(pid, метри / брой))
    return задачи, пакети


def _по_стъпка(задачи: list[dict], ключ: str) -> list[dict]:
    return [t for t in задачи if t.get("chain_step") == ключ]


def _зависи_от(task: dict) -> set[str]:
    return {str(d.get("predecessor_id")) for d in task.get("dependencies") or []}


# ---------------------------------------------------------------------------
# Същината
# ---------------------------------------------------------------------------


def test_declared_single_route_makes_the_one_off_work_happen_once():
    """Три участъка, но едно изпитване, една дезинфекция, едно присъединяване."""
    задачи, пакети = _трасе()
    with for_this_run({"single_route": "В"}):
        свити, бележки = collapse_route_wide_steps(задачи, пакети, ВЕРИГИ)

    for ключ in ЕДНОКРАТНИ:
        останали = _по_стъпка(свити, ключ)
        assert len(останали) == 1, (
            f"„{ключ}\" се прави {len(останали)} пъти на едно непрекъснато "
            "трасе — човекът я прави веднъж")
    assert len(бележки) == len(ЕДНОКРАТНИ), (
        "свиването не се обявява — какво е решило правилото трябва да се вижда")


def test_a_distribution_network_keeps_its_per_section_testing():
    """Без обявено единично трасе всеки клон се изпитва сам — както в еталона."""
    задачи, пакети = _трасе()
    свити, бележки = collapse_route_wide_steps(задачи, пакети, ВЕРИГИ)

    assert свити == задачи and not бележки, (
        "свиването важи по подразбиране — разпределителната мрежа губи "
        "изпитването на 22 от 23 клона")


def test_the_campaign_waits_for_every_section():
    """Не може да изпитваш трасе, което още се полага."""
    задачи, пакети = _трасе()
    with for_this_run({"single_route": "В"}):
        свити, _ = collapse_route_wide_steps(задачи, пакети, ВЕРИГИ)

    изпитване = _по_стъпка(свити, "pressure_test")[0]
    опори = _зависи_от(изпитване)
    for pid in ("В1", "В2", "В3"):
        assert any(o.startswith(pid) for o in опори), (
            f"изпитването за цялото трасе не чака участък {pid}: {опори}")


def test_the_sections_do_not_wait_for_the_whole_route():
    """Извадената стъпка не бива да влачи своя участък след себе си."""
    задачи, пакети = _трасе()
    with for_this_run({"single_route": "В"}):
        свити, _ = collapse_route_wide_steps(задачи, пакети, ВЕРИГИ)

    арматури = next(t for t in _по_стъпка(свити, "valves")
                    if t["parent_id"] == "В1")
    # „Арматури" се държеше за „дезинфекция"; тя отиде накрая, затова сега
    # трябва да се държи за онова, за което се държеше самата дезинфекция.
    assert _зависи_от(арматури) == {"В1_fittings"}, (
        f"участък 1 чака края на целия обект: {_зависи_от(арматури)}")


def test_collapsing_changes_the_form_not_the_amount_of_work():
    """Свиването е решение за форма — дните на трасето остават същите."""
    поотделно, пакети_а = _трасе()
    scale_segment_overhead(поотделно, пакети_а, ВЕРИГИ)
    беше = {к: sum(float(t["duration"]) for t in _по_стъпка(поотделно, к))
            for к in ЕДНОКРАТНИ}

    заедно, пакети_б = _трасе()
    with for_this_run({"single_route": "В"}):
        заедно, _ = collapse_route_wide_steps(заедно, пакети_б, ВЕРИГИ)
    scale_segment_overhead(заедно, пакети_б, ВЕРИГИ)
    стана = {к: sum(float(t["duration"]) for t in _по_стъпка(заедно, к))
             for к in ЕДНОКРАТНИ}

    assert стана == беше, (
        f"работата изчезва при свиването: {беше} → {стана} — свитата задача "
        "получава дела на своя участък вместо анкера за цялото трасе")


def test_a_cited_task_is_not_merged_away():
    """Един КСС ред не може да бъде цитиран от две задачи — затова не се слива."""
    задачи, пакети = _трасе()
    for pid in ("В1", "В2"):
        цитат = next(t for t in задачи if t["id"] == f"{pid}_tie_in")
        цитат["source_ref"] = f"КСС.xlsx!Водопровод!{pid}"

    with for_this_run({"single_route": "В"}):
        свити, _ = collapse_route_wide_steps(задачи, пакети, ВЕРИГИ)

    присъединяване = _по_стъпка(свити, "tie_in")
    цитати = [t for t in присъединяване if t.get("source_ref")]
    assert len(цитати) == 2, (
        "цитираща задача е изтрита — сборът по този ред вече не отговаря на КСС")
    # Кампанията остава ЕДНА: цитиращите задачи вървят една след друга накрая.
    опашки = [t for t in присъединяване if not t.get("source_ref")]
    assert len(опашки) <= 1


# ---------------------------------------------------------------------------
# Как се обявява
# ---------------------------------------------------------------------------


def test_the_declaration_reads_both_alphabets():
    """Кирилското „В" и латинското „B" не се различават на око."""
    for обявено, очаквано in (("В", {"В"}), ("B", {"В"}), ("в,к", {"В", "К"}),
                              ("1", {"В", "К"}), ("", set())):
        with for_this_run({"single_route": обявено}):
            assert single_route_networks() == set(очаквано), обявено
