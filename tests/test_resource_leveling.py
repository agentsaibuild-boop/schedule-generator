"""Unit tests: ресурсен капацитет и изравняване.

ОДИТ 2026-08-07: ресурсите бяха добавени като ИМЕНА и не ограничаваха нищо.
В експортирания файл един ръководител излизаше назначен на 66 задачи, от които
22 стартират в един и същи ден, а един багер универсален — на 16 едновременни.
Мрежата беше коректна, а графикът физически неизпълним.

FAILURE означава: графикът пак обещава работа, за която няма хора и машини —
най-скъпият вид грешка, защото изглежда изпълним до първия ден на обекта.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.schedule_builder import ScheduleBuilder  # noqa: E402


@pytest.fixture
def builder() -> ScheduleBuilder:
    return ScheduleBuilder()


def _t(tid, duration=2, resources=None, deps=None, **kw):
    task = {"id": tid, "name": tid, "duration": duration, "start_day": 1,
            "dependencies": deps or [], "resources": resources or []}
    task.update(kw)
    return task


def _peak(tasks) -> dict:
    usage = defaultdict(int)
    for t in tasks:
        if t.get("is_summary") or not t.get("duration"):
            continue
        for r in t.get("resources") or []:
            for day in range(t["start_day"], t["end_day"] + 1):
                usage[(r, day)] += 1
    peak = defaultdict(int)
    for (r, _), n in usage.items():
        peak[r] = max(peak[r], n)
    return dict(peak)


def test_leveling_respects_capacity(builder):
    """Четири задачи за един багер при капацитет 1 → не вървят едновременно."""
    tasks = [_t(f"T{i}", 2, ["Багер универсален"]) for i in range(4)]

    result = builder.level_resources(tasks, capacity={"Багер универсален": 1})

    assert _peak(result["schedule"])["Багер универсален"] == 1
    assert len(result["shifted"]) == 3


def test_leveling_allows_work_up_to_capacity(builder):
    tasks = [_t(f"T{i}", 2, ["Багер универсален"]) for i in range(4)]

    result = builder.level_resources(tasks, capacity={"Багер универсален": 2})

    assert _peak(result["schedule"])["Багер универсален"] == 2


def test_leveling_never_breaks_a_dependency(builder):
    """Изравняването само ОТЛАГА — не бива да мести задача преди предшественика."""
    tasks = [
        _t("A", 3, ["Багер универсален"]),
        _t("B", 3, ["Багер универсален"],
           [{"predecessor_id": "A", "type": "FS", "lag_days": 0}]),
    ]

    result = builder.level_resources(tasks, capacity={"Багер универсален": 5})
    by_id = {t["id"]: t for t in result["schedule"]}

    assert by_id["B"]["start_day"] > by_id["A"]["end_day"]


def test_leveling_extends_the_schedule_rather_than_lying(builder):
    """Ограниченият ресурс УДЪЛЖАВА срока — това е честната последица."""
    tasks = [_t(f"T{i}", 5, ["Автокран"]) for i in range(3)]

    before = max(t["start_day"] + t["duration"] - 1 for t in tasks)
    result = builder.level_resources(tasks, capacity={"Автокран": 1})
    after = max(t["end_day"] for t in result["schedule"])

    assert after > before
    assert after == 15                    # 3 × 5 дни последователно


def test_summary_and_milestones_do_not_consume_resources(builder):
    tasks = [
        {"id": "S", "name": "Участък", "duration": 0, "start_day": 1,
         "dependencies": [], "is_summary": True, "resources": ["Багер универсален"]},
        {"id": "MS", "name": "Край", "duration": 0, "start_day": 1,
         "dependencies": [], "milestone": True, "resources": ["Багер универсален"]},
        _t("A", 2, ["Багер универсален"]),
    ]

    result = builder.level_resources(tasks, capacity={"Багер универсален": 1})

    assert result["shifted"] == []


def test_task_without_resources_is_not_delayed(builder):
    tasks = [_t("A", 2, ["Багер универсален"]), _t("B", 2, [])]

    result = builder.level_resources(tasks, capacity={"Багер универсален": 1})
    by_id = {t["id"]: t for t in result["schedule"]}

    assert by_id["B"]["start_day"] == 1


def test_team_label_counts_as_a_resource(builder):
    """Фронтът също е ограничен ресурс — не може да е на две места наведнъж."""
    tasks = [_t(f"T{i}", 2, [], team="Фронт 1") for i in range(3)]

    result = builder.level_resources(tasks, capacity={"Фронт 1": 1})
    starts = sorted(t["start_day"] for t in result["schedule"])

    assert starts == [1, 3, 5]


def test_cycle_is_reported_not_crashed(builder):
    tasks = [
        _t("A", 2, [], [{"predecessor_id": "B", "type": "FS", "lag_days": 0}]),
        _t("B", 2, [], [{"predecessor_id": "A", "type": "FS", "lag_days": 0}]),
    ]

    result = builder.level_resources(tasks)

    assert result["warnings"] and result["shifted"] == []


def test_input_is_not_mutated(builder):
    tasks = [_t("A", 2, ["Багер универсален"]), _t("B", 2, ["Багер универсален"])]

    builder.level_resources(tasks, capacity={"Багер универсален": 1})

    assert all(t["start_day"] == 1 for t in tasks)


def test_capacity_config_is_loadable():
    from src.schedule_builder import _load_resource_capacity

    config = _load_resource_capacity()

    assert int(config["default"]) >= 1
    assert config["capacity"]["Багер универсален"] >= 1
    assert "Ръководител работна група" in config["capacity"]


def test_exported_xml_declares_capacity():
    """XML-ът трябва да обявява СЪЩАТА наличност, с която е сметнат графикът."""
    import xml.etree.ElementTree as ET

    from src.export_xml import export_to_mspdi_xml
    from src.schedule_builder import _load_resource_capacity

    schedule = [{"id": "A", "name": "Изкоп", "duration": 2, "start_day": 1,
                 "end_day": 2, "dependencies": [], "team": "Фронт 1",
                 "resources": ["Багер универсален"]}]
    root = ET.fromstring(export_to_mspdi_xml(schedule, "Тест").decode())
    ns = "{http://schemas.microsoft.com/project}"

    units = {r.findtext(f"{ns}Name"): r.findtext(f"{ns}MaxUnits")
             for r in root.iter(f"{ns}Resource")}

    # Числата НЕ се заковават тук: те се мерят от еталонния график
    # (`tools/extract_resource_capacity.py`) и се менят с него.  Проверимото
    # е тъждеството — XML-ът да обявява това, с което е смятано, иначе MS
    # Project преизчислява по друга наличност и показва друг срок.
    capacity = _load_resource_capacity()["capacity"]
    assert units["Багер универсален"] == f"{float(capacity['Багер универсален'])}"
    assert units["Фронт 1"] == f"{float(capacity['Фронт 1'])}"


# ---------------------------------------------------------------------------
# Roll-up в паметта (одит 2026-08-07)
# ---------------------------------------------------------------------------


def test_summary_is_stretched_over_its_children(builder):
    tasks = [
        {"id": "S", "name": "Участък", "duration": 0, "start_day": 1,
         "dependencies": [], "is_summary": True},
        _t("A", 5, [], parent_id="S", start_day=10),
        _t("B", 3, [], parent_id="S", start_day=30),
    ]
    tasks[1]["end_day"] = 14
    tasks[2]["end_day"] = 32

    result = builder.roll_up_summaries(tasks)
    by_id = {t["id"]: t for t in result["schedule"]}

    assert by_id["S"]["start_day"] == 10
    assert by_id["S"]["end_day"] == 32
    assert result["adjusted"]


def test_rollup_is_recursive(builder):
    tasks = [
        {"id": "PH", "name": "Фаза", "duration": 0, "start_day": 1,
         "dependencies": [], "is_summary": True},
        {"id": "PK", "name": "Пакет", "duration": 0, "start_day": 1,
         "parent_id": "PH", "dependencies": [], "is_summary": True},
        _t("T", 4, [], parent_id="PK", start_day=40, end_day=43),
    ]

    by_id = {t["id"]: t for t in builder.roll_up_summaries(tasks)["schedule"]}

    assert (by_id["PK"]["start_day"], by_id["PK"]["end_day"]) == (40, 43)
    assert (by_id["PH"]["start_day"], by_id["PH"]["end_day"]) == (40, 43)


def test_rollup_leaves_leaf_tasks_alone(builder):
    tasks = [_t("A", 5, [], start_day=7, end_day=11)]

    result = builder.roll_up_summaries(tasks)

    assert result["adjusted"] == []
    assert result["schedule"][0]["start_day"] == 7


# ---------------------------------------------------------------------------
# Затваряне на празнините (измерено 17.08.2026)
# ---------------------------------------------------------------------------
#
# FAILURE означава: src/schedule_builder.py :: закъснение, влязло веднъж в
# графика, пак няма да излиза.  На детерминистичния прогон това остави 65 дни
# ПЪЛНА ПАУЗА — екзекутивната документация чакаше надзор, който вече беше
# свършил, при напълно свободен ресурс.


class TestPullIn:
    def _двойка(self, старт_на_втората: int):
        """A свършва на ден 5; B стои на посочения ден без причина."""
        return [
            {"id": "A", "name": "предшественик", "duration": 5,
             "start_day": 1, "end_day": 5, "dependencies": [],
             "resources": ["Багер универсален"]},
            {"id": "B", "name": "наследник", "duration": 3,
             "start_day": старт_на_втората,
             "end_day": старт_на_втората + 2, "dependencies": ["A"],
             "resources": ["Багер универсален"]},
        ]

    def test_a_stale_gap_is_closed(self):
        резултат = ScheduleBuilder().level_resources(self._двойка(70),
                                                     pull_in=True)
        b = next(t for t in резултат["schedule"] if t["id"] == "B")

        assert b["start_day"] == 6, (
            "наследникът остана да чака предшественик, който е свършил — "
            f"старт {b['start_day']} вместо 6")

    def test_without_pull_in_the_gap_stays(self):
        """Подразбирането НЕ пренарежда — първият проход само отлага."""
        резултат = ScheduleBuilder().level_resources(self._двойка(70))
        b = next(t for t in резултат["schedule"] if t["id"] == "B")

        assert b["start_day"] == 70

    def test_dependencies_are_still_unbreakable(self):
        """Връщането назад не бива да минава пред предшественика."""
        резултат = ScheduleBuilder().level_resources(self._двойка(2),
                                                     pull_in=True)
        a, b = (next(t for t in резултат["schedule"] if t["id"] == x)
                for x in ("A", "B"))

        assert b["start_day"] > a["end_day"], "FS връзката е нарушена"

    def test_resources_are_still_respected(self):
        """Ако ресурсът е зает, задачата не се връща в заетия ден."""
        задачи = [
            {"id": "A", "name": "заемa багера", "duration": 10,
             "start_day": 1, "end_day": 10, "dependencies": [],
             "resources": ["Автокран"]},
            {"id": "B", "name": "също иска багера", "duration": 3,
             "start_day": 60, "end_day": 62, "dependencies": ["A"],
             "resources": ["Автокран"]},
        ]
        резултат = ScheduleBuilder().level_resources(
            задачи, capacity={"Автокран": 1}, pull_in=True)
        b = next(t for t in резултат["schedule"] if t["id"] == "B")

        assert b["start_day"] >= 11, "две задачи взеха един автокран"

    def test_a_task_without_predecessors_keeps_its_date(self):
        """Дата без предшественик е решение отвън, не остатък от смятане."""
        задачи = [{"id": "M", "name": "мобилизация", "duration": 5,
                   "start_day": 30, "end_day": 34, "dependencies": [],
                   "resources": ["Багер универсален"]}]
        резултат = ScheduleBuilder().level_resources(задачи, pull_in=True)

        assert резултат["schedule"][0]["start_day"] == 30
