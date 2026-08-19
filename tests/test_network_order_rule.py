"""„Кое е първо, В или К" мени графика, а не само промпта.

FAILURE означава: отговорът на въпросника пак не стига до детерминистичния път
— приложението пита, показва отговора и генерира същото.

ЗАЩО ТАКА (19.08.2026).  Правилото е СЛАБО нарочно: в еталона от 34 участъка с
двете мрежи в 15 водата тръгва първа, в 15 ЗАЕДНО и в 4 каналът е пръв.  Тоест
не е последователност участък по участък, а под — втората мрежа не започва
преди първата.

Мястото, където редът реално се решава, е РАЗДАВАНЕТО НА РЕСУРС: изравняването
минава задачите в топологичен ред и при две готови печели онази, която излезе
първа.  Изразен като зависимости, редът или не хваща нищо (двете мрежи и без
това не се изпреварват), или слага десетки излишни ребра, които разместват
раздаването — измерено: 741 → 777 дни без нито един ден истинско чакане.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.schedule_builder import ScheduleBuilder  # noqa: E402
from src.tender_parameters import for_this_run  # noqa: E402
from src.work_package import (PackageItem, SpatialWorkPackage,  # noqa: E402
                              enforce_network_order, link_cross_discipline,
                              load_chains)


def _задача(tid: str, мрежа: str, ден: int, pid: str) -> dict:
    return {"id": tid, "name": tid, "network": мрежа, "parent_id": pid,
            "chain_step": "laying", "type": "task", "duration": 3,
            "start_day": ден, "end_day": ден + 2, "dependencies": [],
            "resources": ["Багер универсален"], "crew_id": pid}


def _пакет(pid: str, верига: str, мрежа: str) -> SpatialWorkPackage:
    return SpatialWorkPackage(
        id=pid, network=мрежа, chain=верига, name=pid,
        items=(PackageItem(source_ref=f"КСС!{pid}", activity_class="laying",
                           quantity=100.0, unit="м"),))


def _обект():
    пакети = [_пакет("В1", "water_section", "В"), _пакет("В2", "water_section", "В"),
              _пакет("К1", "sewer_section", "К"), _пакет("К2", "sewer_section", "К")]
    задачи = [_задача("В1_laying", "В", 10, "В1"),
              _задача("В2_laying", "В", 20, "В2"),
              _задача("К1_laying", "К", 5, "К1"),
              _задача("К2_laying", "К", 30, "К2")]
    return задачи, пакети


class TestРедътСеПрилага:
    def test_нарушителят_получава_връзка(self):
        """К1 тръгва на ден 5, водата — на 10.  Това е нарушение."""
        задачи, пакети = _обект()

        with for_this_run({"network_order": "В"}):
            нови, бележки = enforce_network_order(задачи, пакети, load_chains())

        к1 = next(t for t in нови if t["id"] == "К1_laying")
        assert [d["predecessor_id"] for d in к1["dependencies"]] == ["В1_laying"]
        assert к1["dependencies"][0]["type"] == "SS"
        assert бележки, "правилото трябва да КАЖЕ какво е направило"

    def test_обратната_посока_връзва_водата(self):
        задачи, пакети = _обект()

        with for_this_run({"network_order": "К"}):
            нови, _ = enforce_network_order(задачи, пакети, load_chains())

        # Котвата е К1 (ден 5); В1 (ден 10) и В2 (ден 20) са след нея, затова
        # само най-ранният получава връзката, за да се вижда правилото.
        вързани = {t["id"] for t in нови
                   if any(d["predecessor_id"] == "К1_laying"
                          for d in (t.get("dependencies") or []))}
        assert вързани == {"В1_laying"}

    def test_който_вече_спазва_реда_остава_свободен(self):
        """Излишните ребра струваха 36 дни без нито един ден чакане."""
        задачи, пакети = _обект()

        with for_this_run({"network_order": "В"}):
            нови, _ = enforce_network_order(задачи, пакети, load_chains())

        к2 = next(t for t in нови if t["id"] == "К2_laying")
        assert к2["dependencies"] == [], (
            "участък, който и без това тръгва след водата, е вързан излишно")

    def test_входът_не_се_мутира(self):
        задачи, пакети = _обект()

        with for_this_run({"network_order": "В"}):
            enforce_network_order(задачи, пакети, load_chains())

        assert all(not t["dependencies"] for t in задачи)

    def test_междудисциплинните_правила_не_го_прилагат(self):
        """Там няма дати — изборът на котва би бил мълчаливо произволен."""
        задачи, пакети = _обект()
        без_дати = [{k: v for k, v in t.items() if k not in ("start_day", "end_day")}
                    for t in задачи]

        with for_this_run({"network_order": "В"}):
            нови = link_cross_discipline(без_дати, пакети, load_chains(),
                                         spatial_authoritative=False)

        assert all(not t.get("dependencies") for t in нови)


class TestИзравняванетоСпазваРеда:
    """Редът се решава при раздаването на ресурс, не само в зависимостите."""

    def _спор(self) -> list[dict]:
        """Две задачи, готови в един ден, които делят един багер."""
        return [
            {"id": "К_спор", "network": "К", "type": "task", "duration": 5,
             "start_day": 1, "end_day": 5, "dependencies": [],
             "resources": ["Багер универсален"], "crew_id": "Екип К1"},
            {"id": "В_спор", "network": "В", "type": "task", "duration": 5,
             "start_day": 1, "end_day": 5, "dependencies": [],
             "resources": ["Багер универсален"], "crew_id": "Екип В1"},
        ]

    def _старт(self, разписан: list[dict], tid: str) -> int:
        return int(next(t for t in разписан if t["id"] == tid)["start_day"])

    def test_водата_печели_спора_когато_е_първа(self):
        with for_this_run({"network_order": "В"}):
            разписан = ScheduleBuilder().level_resources(
                self._спор(), capacity={"Багер универсален": 1})["schedule"]

        assert self._старт(разписан, "В_спор") < self._старт(разписан, "К_спор")

    def test_каналът_печели_спора_когато_е_първи(self):
        with for_this_run({"network_order": "К"}):
            разписан = ScheduleBuilder().level_resources(
                self._спор(), capacity={"Багер универсален": 1})["schedule"]

        assert self._старт(разписан, "К_спор") < self._старт(разписан, "В_спор")

    def test_другите_мрежи_не_се_разместват(self):
        """Пипа се САМО отношението между двете мрежи."""
        задачи = [
            {"id": f"П{i}", "network": "П", "type": "task", "duration": 2,
             "start_day": 1, "end_day": 2, "dependencies": [],
             "resources": ["Валяк"], "crew_id": f"Екип П{i}"}
            for i in range(1, 4)
        ]
        with for_this_run({"network_order": "В"}):
            а = ScheduleBuilder().level_resources(
                [dict(t) for t in задачи], capacity={"Валяк": 1})["schedule"]
        with for_this_run({"network_order": "К"}):
            б = ScheduleBuilder().level_resources(
                [dict(t) for t in задачи], capacity={"Валяк": 1})["schedule"]

        assert [(t["id"], t["start_day"]) for t in а] == \
               [(t["id"], t["start_day"]) for t in б]
