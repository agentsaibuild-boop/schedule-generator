"""Unit tests: XML експортът не губи зависимости според реда на задачите.

Одит 2026-07-23: `uid_map` се пълнеше в СЪЩИЯ цикъл, в който се добавяха
predecessor връзките.  Ако задача стоеше във файла преди своя предшественик,
той още нямаше UID и връзката се пропускаше с `continue` — безшумно.

Редът на задачите идва от AI и не е гарантирано топологичен.  Резултатът е
MS Project файл, който се отваря нормално, но е без логика: всяка задача се
движи независимо от останалите.

FAILURE означава: графикът, който получава възложителят, може да е загубил
част или всички зависимости, без никакво предупреждение.
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.export_xml import export_to_mspdi_xml  # noqa: E402

NS = "{http://schemas.microsoft.com/project}"


def _xml(tasks: list[dict]) -> str:
    out = export_to_mspdi_xml(tasks, "Тест", "2026-08-01")
    return out.decode() if isinstance(out, bytes) else str(out)


def _links(tasks: list[dict]) -> list[str]:
    return re.findall(r"<PredecessorUID>(\d+)</PredecessorUID>", _xml(tasks))


def _task(tid: str, deps: list | None = None, **kw) -> dict:
    task = {
        "id": tid, "name": f"Задача {tid}", "duration": 10,
        "start_day": 1, "end_day": 10, "dependencies": deps or [],
    }
    task.update(kw)
    return task


# ===================================================================
# Ред на задачите
# ===================================================================

def test_topological_order_keeps_links():
    tasks = [_task("A"), _task("B", ["A"]), _task("C", ["B"])]
    assert len(_links(tasks)) == 2


def test_reverse_order_keeps_links():
    """Ядрото на дефекта: наследникът стои ПРЕДИ предшественика си."""
    tasks = [_task("C", ["B"]), _task("B", ["A"]), _task("A")]
    assert len(_links(tasks)) == 2


def test_successor_before_predecessor_single_pair():
    tasks = [_task("B", ["A"]), _task("A")]
    assert len(_links(tasks)) == 1


def test_shuffled_order_keeps_all_links():
    tasks = [_task("D", ["C"]), _task("A"), _task("C", ["B"]), _task("B", ["A"])]
    assert len(_links(tasks)) == 3


def test_link_points_at_the_right_task():
    """Не е достатъчно да има връзка — трябва да сочи вярната задача."""
    tasks = [_task("B", ["A"]), _task("A")]
    root = ET.fromstring(_xml(tasks))

    uid_by_name = {}
    for t in root.iter(f"{NS}Task"):
        name = t.findtext(f"{NS}Name") or ""
        uid = t.findtext(f"{NS}UID") or ""
        uid_by_name[name] = uid

    task_b = next(
        t for t in root.iter(f"{NS}Task")
        if (t.findtext(f"{NS}Name") or "") == "Задача B"
    )
    pred_uid = task_b.findtext(f"{NS}PredecessorLink/{NS}PredecessorUID")
    assert pred_uid == uid_by_name["Задача A"]


def test_multiple_predecessors_all_kept():
    tasks = [_task("C", ["A", "B"]), _task("A"), _task("B")]
    assert len(_links(tasks)) == 2


def test_dict_dependency_format_survives_reordering():
    tasks = [
        _task("B", [{"predecessor_id": "A", "type": "SS", "lag_days": 3}]),
        _task("A"),
    ]
    assert len(_links(tasks)) == 1


# ===================================================================
# Наистина липсващ предшественик
# ===================================================================

def test_missing_predecessor_is_dropped():
    tasks = [_task("B", ["НЯМА_ТАКАВА"]), _task("A")]
    assert _links(tasks) == []


def test_missing_predecessor_is_logged_as_warning(caplog):
    """След two-pass това значи РЕАЛНО липсващ предшественик — не бива да е тихо."""
    import logging
    with caplog.at_level(logging.WARNING):
        _xml([_task("B", ["НЯМА_ТАКАВА"])])
    assert any("ИЗПУСНАТА" in rec.message for rec in caplog.records)


def test_self_dependency_still_produces_a_link():
    """Не е валиден график, но експортът не бива да го изяжда мълчаливо —
    валидаторът е този, който трябва да го отхвърли."""
    assert len(_links([_task("A", ["A"])])) == 1


# ===================================================================
# UID-тата остават консистентни
# ===================================================================

def test_uids_are_unique():
    tasks = [_task("C", ["B"]), _task("A"), _task("B", ["A"])]
    root = ET.fromstring(_xml(tasks))
    uids = [t.findtext(f"{NS}UID") for t in root.iter(f"{NS}Task")]
    assert len(uids) == len(set(uids))


def test_uid_zero_is_the_project_root():
    root = ET.fromstring(_xml([_task("A")]))
    uids = [t.findtext(f"{NS}UID") for t in root.iter(f"{NS}Task")]
    assert "0" in uids


def test_duplicate_task_ids_do_not_corrupt_the_map():
    """Два еднакви ID: първият печели, експортът не гърми."""
    tasks = [_task("A"), _task("A"), _task("B", ["A"])]
    assert len(_links(tasks)) >= 1


def test_empty_id_does_not_break_export():
    tasks = [_task(""), _task("B", ["A"]), _task("A")]
    assert len(_links(tasks)) == 1


def test_duration_format_still_days():
    """Урок #19 — two-pass не бива да пипне това."""
    assert "<DurationFormat>7</DurationFormat>" in _xml([_task("A")])


def test_empty_schedule_returns_none_not_a_broken_file():
    """Празен график не бива да произведе XML — по-добре нищо, отколкото
    файл с нула задачи, който изглежда като валиден график."""
    assert export_to_mspdi_xml([], "Празен", "2026-08-01") is None
