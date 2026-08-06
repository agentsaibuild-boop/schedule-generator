"""Unit test: от НЕГОДЕН AI изход до ЕКСПОРТИРУЕМ график — целият път.

Реален прогон 2026-08 (Sonnet през OpenRouter) даде точно този изход:
  - задачи само за част от КСС позициите (6 от 28);
  - два екипа на едни и същи метри в застъпващи се дни.
Резултат: гейтът (правилно) обявяваше графика за невалиден и приложението не
изкарваше НИЩО — нито .mpp, нито .xml.

Тук същият тип изход минава през `generate_schedule_staged` с двата ремонта
(допокриване + пространствено разделяне) и се проверява крайната цел:
валиден график, който се експортира като MSPDI XML за MS Project.

FAILURE означава: приложението пак не изкарва използваем график от изход,
който може да се поправи детерминистично.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai_processor import AIProcessor  # noqa: E402
from src.export_xml import export_to_mspdi_xml  # noqa: E402
from src.provenance import QuantityRow, SourceRef  # noqa: E402
from src.spatial import find_crew_collisions  # noqa: E402

_NS = {"m": "http://schemas.microsoft.com/project"}


def _boq(row: int, qty: float) -> QuantityRow:
    return QuantityRow("Реконструкция водопровод DN110 PE", qty, "м",
                       SourceRef("КСС.xlsx", "Водопровод", row), {})


def _task(tid: str, ref: str, qty: float, start: int, frm: float, to: float) -> dict:
    """Задача с ПИКЕТАЖ — както я връща моделът: всички започват от ден 1."""
    return {
        "id": tid, "name": f"Полагане DN110 PE {tid}", "type": "task",
        "start_day": start, "end_day": start + 4, "duration": 5,
        "length_m": qty, "unit": "м", "source_ref": ref,
        "alignment_id": "ул. Тестова", "start_chainage": frm, "end_chainage": to,
        "dependencies": [],
    }


# Три КСС позиции; моделът покрива само първата, после (на допокриване) още
# две — и трите с ПРЕСИЧАЩ СЕ пикетаж в едни и същи дни.
_BOQ = [_boq(2, 100.0), _boq(3, 200.0), _boq(4, 300.0)]
_RESPONSES = [
    [_task("T1", "КСС.xlsx!Водопровод!2", 100.0, 1, 0, 100)],
    [_task("T1", "КСС.xlsx!Водопровод!3", 200.0, 1, 50, 250),
     _task("T2", "КСС.xlsx!Водопровод!4", 300.0, 1, 200, 500)],
]


def _proc() -> AIProcessor:
    calls = {"n": 0}

    def fake_generate(analysis, project_type, cb=None, *, all_text="",
                      boq_index=None, num_teams=1, extra_locations=None,
                      sequence_constraints=None, scope_note="",
                      skip_correction=False):
        idx = min(calls["n"], len(_RESPONSES) - 1)
        calls["n"] += 1
        return {"status": "approved", "truncated": None, "total_cost": 0.01,
                "schedule": {"tasks": _RESPONSES[idx]}}

    proc = AIProcessor(router=None, knowledge_manager=None)
    proc.generate_schedule = fake_generate      # type: ignore[assignment]
    return proc


def test_incomplete_and_colliding_output_becomes_exportable():
    result = _proc().generate_schedule_staged({}, "distribution", boq_index=_BOQ)
    tasks = result["schedule"]["tasks"]

    # 1. Пълно покритие — всяка КСС позиция има своя дейност.
    assert result["coverage"]["uncovered"] == []
    assert result["coverage"]["over_covered"] == []
    assert len(tasks) == 3

    # 2. Никой екип не е на чужди метри в чужд ден.
    assert [c for c in find_crew_collisions(tasks) if c["kind"] == "overlap"] == []

    # 3. Гейтът пуска графика.
    assert result["validation"]["valid"] is True
    assert result["status"] == "approved"
    assert result["exportable"] is True
    assert result["export_blockers"] == []

    # 4. Ремонтите са ДОКЛАДВАНИ, не тихи.
    assert result["repair_rounds"] >= 1
    assert result["spatial_repair"]["added_links"]
    assert result["spatial_repair"]["unresolved"] == []


def test_repaired_schedule_exports_to_mspdi_xml():
    result = _proc().generate_schedule_staged({}, "distribution", boq_index=_BOQ)
    xml_bytes = export_to_mspdi_xml(result["schedule"]["tasks"], "Тестов обект")

    assert xml_bytes, "експортът трябва да върне XML"
    root = ET.fromstring(xml_bytes)
    tasks = root.findall(".//m:Task", _NS)
    assert len([t for t in tasks if t.find("m:Name", _NS) is not None]) >= 3

    # Договорът с MS Project (режим 'pinned'): дните са дни (DurationFormat=5)
    # и началото е заковано (ConstraintType=2), за да не пренарежда програмата
    # датите, сметнати от детерминистичния двигател.
    for task in tasks:
        fmt = task.find("m:DurationFormat", _NS)
        if fmt is not None:
            assert fmt.text == "5"
    pinned = [t for t in tasks if t.find("m:ConstraintType", _NS) is not None]
    assert pinned, "производствените задачи трябва да са със заковано начало"
    for task in pinned:
        assert task.find("m:ConstraintType", _NS).text == "2"

    # Добавената от ремонта връзка оцелява в експорта.
    assert root.findall(".//m:PredecessorLink", _NS)
