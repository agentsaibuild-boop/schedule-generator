"""Unit tests: паралелните мрежи се подреждат вода → канал → пътни.

ЖИВ ПРОГОН 2026-08-06: частите се генерират независимо и всяка тръгва от ден 1.
В реалния график водопроводът, канализацията, ЕЛ/ТТ и ПЪТНАТА част започваха в
един и същи ден — тоест възстановяването на настилка предхождаше изкопа под
нея, а общият срок излизаше от най-дългата самостоятелна част.

Правило #74 (урок #11): Rolling Wave — вода → канал → пътни с 10-12 дни lag.
Правило #75: пътните не завършват преди канализацията.

FAILURE означава: графикът пак твърди, че всички мрежи започват заедно, тоест
редът на строителството не се вижда — а именно той е смисълът на линейния
график.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.schedule_builder import ScheduleBuilder  # noqa: E402


def _t(tid: str, start: int, dur: int, deps=None) -> dict:
    return {"id": tid, "name": f"Задача {tid}", "start_day": start,
            "end_day": start + dur - 1, "duration": dur,
            "dependencies": deps or []}


def _three_networks() -> tuple[list[dict], dict[str, list[str]]]:
    tasks = [
        _t("В-1", 1, 10), _t("В-2", 11, 10, ["В-1"]),
        _t("К-1", 1, 20), _t("К-2", 21, 20, ["К-1"]),
        _t("П-1", 1, 15), _t("П-2", 16, 15, ["П-1"]),
    ]
    networks = {"В": ["В-1", "В-2"], "К": ["К-1", "К-2"], "П": ["П-1", "П-2"]}
    return tasks, networks


def test_sewer_starts_after_water_by_the_lag():
    tasks, networks = _three_networks()
    res = ScheduleBuilder().link_networks(tasks, networks, lag_days=12)
    by_id = {t["id"]: t for t in res["schedule"]}
    assert by_id["К-1"]["start_day"] == by_id["В-1"]["start_day"] + 12


def test_road_starts_after_sewer_by_the_lag():
    tasks, networks = _three_networks()
    res = ScheduleBuilder().link_networks(tasks, networks, lag_days=12)
    by_id = {t["id"]: t for t in res["schedule"]}
    assert by_id["П-1"]["start_day"] == by_id["К-1"]["start_day"] + 12


def test_road_does_not_finish_before_sewer():
    """Правило #75 — финалната настилка чака цялата канализация."""
    tasks, networks = _three_networks()
    res = ScheduleBuilder().link_networks(tasks, networks, lag_days=12)
    by_id = {t["id"]: t for t in res["schedule"]}
    last_sewer = max(by_id[t]["end_day"] for t in networks["К"])
    last_road = max(by_id[t]["end_day"] for t in networks["П"])
    assert last_road >= last_sewer


def test_waves_overlap_not_queue():
    """SS с lag, не FS: канализацията ТРЪГВА докато водопроводът още върви."""
    tasks, networks = _three_networks()
    res = ScheduleBuilder().link_networks(tasks, networks, lag_days=12)
    by_id = {t["id"]: t for t in res["schedule"]}
    water_end = max(by_id[t]["end_day"] for t in networks["В"])
    assert by_id["К-1"]["start_day"] < water_end, "вълните трябва да се застъпват"


def test_links_are_reported_not_silent():
    tasks, networks = _three_networks()
    res = ScheduleBuilder().link_networks(tasks, networks, lag_days=12)
    kinds = {link["reason"] for link in res["added_links"]}
    assert "rolling_wave" in kinds
    assert "road_not_before_sewer" in kinds


def test_single_network_is_untouched():
    tasks = [_t("В-1", 1, 10), _t("В-2", 11, 10, ["В-1"])]
    res = ScheduleBuilder().link_networks(tasks, {"В": ["В-1", "В-2"]})
    assert res["added_links"] == []
    assert res["schedule"] == tasks


def test_unknown_networks_are_left_alone():
    """ЕЛ/ТТ няма правило за ред — не се пипа."""
    tasks = [_t("Е-1", 1, 5), _t("В-1", 1, 5)]
    res = ScheduleBuilder().link_networks(tasks, {"Е": ["Е-1"], "В": ["В-1"]})
    assert res["added_links"] == []


def test_input_is_not_mutated():
    tasks, networks = _three_networks()
    ScheduleBuilder().link_networks(tasks, networks)
    assert tasks[2]["start_day"] == 1
    assert tasks[2]["dependencies"] == []


def test_cycle_is_refused():
    """Ако канализацията вече зависи от пътната, връзката не се налага."""
    tasks = [_t("В-1", 1, 10), _t("К-1", 1, 10, ["П-1"]), _t("П-1", 1, 10)]
    networks = {"В": ["В-1"], "К": ["К-1"], "П": ["П-1"]}
    res = ScheduleBuilder().link_networks(tasks, networks)
    assert any(s["reason"] == "cycle" for s in res["skipped"])
    assert ScheduleBuilder()._detect_cycle(
        res["schedule"], {t["id"]: t for t in res["schedule"]}) is None


def test_result_is_reproducible():
    tasks, networks = _three_networks()
    a = ScheduleBuilder().link_networks(tasks, networks)
    b = ScheduleBuilder().link_networks(tasks, networks)
    assert a["added_links"] == b["added_links"]
    assert a["schedule"] == b["schedule"]
