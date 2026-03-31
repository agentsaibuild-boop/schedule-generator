"""Unit tests for export_xml.py — structural and logic correctness.

Tests: _flatten_schedule, _add_predecessor_links (LinkLag), OutlineNumber,
       duration format, 5-day vs 7-day calendar, custom fields, empty input.

FAILURE означава: src/export_xml.py е счупена —
XML файлът ще бъде невалиден за MS Project (грешна йерархия, погрешни
зависимости или неправилен LinkLag ще счупят импорта в MS Project).
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.export_xml import (
    MINUTES_PER_DAY,
    LINK_LAG_FACTOR,
    _flatten_schedule,
    _add_predecessor_links,
    export_to_mspdi_xml,
)

NS = "http://schemas.microsoft.com/project"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _xml_text(root: ET.Element, tag: str) -> str | None:
    el = root.find(f"{{{NS}}}{tag}")
    return el.text if el is not None else None


def _all_tasks(root: ET.Element) -> list[ET.Element]:
    return root.findall(f".//{{{NS}}}Task")


def _task_by_name(root: ET.Element, name: str) -> ET.Element | None:
    for t in _all_tasks(root):
        n = t.find(f"{{{NS}}}Name")
        if n is not None and n.text == name:
            return t
    return None


def _minimal_task(task_id: str, name: str, duration: int = 5) -> dict:
    return {"id": task_id, "name": name, "duration": duration, "start_day": 1}


def _parse_export(schedule: list[dict]) -> ET.Element:
    xml_bytes = export_to_mspdi_xml(schedule, "Тест проект", start_date="2026-06-01")
    assert xml_bytes is not None, "export_to_mspdi_xml returned None"
    return ET.fromstring(xml_bytes)


# ---------------------------------------------------------------------------
# _flatten_schedule
# ---------------------------------------------------------------------------

def test_flatten_flat_list_unchanged():
    """Flat list (no sub_activities) → same number of entries, _has_children=False."""
    tasks = [_minimal_task("t1", "Задача 1"), _minimal_task("t2", "Задача 2")]
    result = _flatten_schedule(tasks)
    assert len(result) == 2
    assert all(not t["_has_children"] for t in result)


def test_flatten_one_level_nesting():
    """Parent with 2 children → 3 entries total, parent _has_children=True."""
    tasks = [{
        "id": "p1", "name": "Фаза 1", "duration": 10, "start_day": 1,
        "sub_activities": [
            _minimal_task("c1", "Дейност 1"),
            _minimal_task("c2", "Дейност 2"),
        ],
    }]
    result = _flatten_schedule(tasks)
    assert len(result) == 3
    assert result[0]["_has_children"] is True
    assert result[1]["_has_children"] is False
    assert result[2]["_has_children"] is False


def test_flatten_children_inherit_parent_id():
    """Children should have parent_id pointing to their parent."""
    tasks = [{
        "id": "p1", "name": "Фаза", "duration": 10, "start_day": 1,
        "sub_activities": [_minimal_task("c1", "Дете")],
    }]
    result = _flatten_schedule(tasks)
    child = next(t for t in result if t["id"] == "c1")
    assert child.get("parent_id") == "p1"


def test_flatten_two_level_nesting():
    """Two levels of nesting → 4 entries, correct outline levels."""
    tasks = [{
        "id": "l1", "name": "Ниво 1", "duration": 10, "start_day": 1,
        "sub_activities": [{
            "id": "l2", "name": "Ниво 2", "duration": 5, "start_day": 1,
            "sub_activities": [_minimal_task("l3", "Ниво 3")],
        }],
    }]
    result = _flatten_schedule(tasks)
    assert len(result) == 3
    levels = [t["_outline_level"] for t in result]
    assert levels == [1, 2, 3]


def test_flatten_empty_list():
    """Empty input → empty output."""
    assert _flatten_schedule([]) == []


def test_flatten_is_subtask_flag():
    """_is_subtask=True only for depth > 1."""
    tasks = [{
        "id": "p1", "name": "Родител", "duration": 10, "start_day": 1,
        "sub_activities": [_minimal_task("c1", "Дете")],
    }]
    result = _flatten_schedule(tasks)
    parent = next(t for t in result if t["id"] == "p1")
    child = next(t for t in result if t["id"] == "c1")
    assert parent["_is_subtask"] is False
    assert child["_is_subtask"] is True


# ---------------------------------------------------------------------------
# _add_predecessor_links
# ---------------------------------------------------------------------------

def test_predecessor_link_fs_no_lag():
    """FS dependency without lag → Type=1, LinkLag=0."""
    task = {"id": "t2", "name": "Т2", "dependencies": ["t1"],
            "dependency_type": "FS", "lag_days": 0}
    uid_map = {"t1": 1, "t2": 2}
    task_elem = ET.Element("Task")
    _add_predecessor_links(task_elem, task, uid_map)
    pred = task_elem.find("PredecessorLink")
    assert pred is not None
    assert pred.find("PredecessorUID").text == "1"
    assert pred.find("Type").text == "1"       # FS
    assert pred.find("LinkLag").text == "0"


def test_predecessor_link_ss_with_lag():
    """SS+30 → Type=3, LinkLag=30×480×10=144000."""
    task = {"id": "t2", "name": "Т2", "dependencies": ["t1"],
            "dependency_type": "SS", "lag_days": 30}
    uid_map = {"t1": 1, "t2": 2}
    task_elem = ET.Element("Task")
    _add_predecessor_links(task_elem, task, uid_map)
    pred = task_elem.find("PredecessorLink")
    assert pred is not None
    assert pred.find("Type").text == "3"       # SS
    expected_lag = str(30 * MINUTES_PER_DAY * LINK_LAG_FACTOR)  # 144000
    assert pred.find("LinkLag").text == expected_lag


def test_predecessor_link_ff():
    """FF dependency → Type=0."""
    task = {"id": "t2", "name": "Т2", "dependencies": ["t1"],
            "dependency_type": "FF", "lag_days": 0}
    uid_map = {"t1": 1, "t2": 2}
    task_elem = ET.Element("Task")
    _add_predecessor_links(task_elem, task, uid_map)
    assert task_elem.find("PredecessorLink/Type").text == "0"


def test_predecessor_link_missing_uid_skipped():
    """If predecessor not in uid_map → no PredecessorLink element added."""
    task = {"id": "t2", "name": "Т2", "dependencies": ["MISSING"],
            "dependency_type": "FS", "lag_days": 0}
    task_elem = ET.Element("Task")
    _add_predecessor_links(task_elem, task, {"t2": 2})
    assert task_elem.find("PredecessorLink") is None


def test_predecessor_link_no_dependencies():
    """Task with no dependencies → nothing added."""
    task = {"id": "t1", "name": "Т1", "dependencies": []}
    task_elem = ET.Element("Task")
    _add_predecessor_links(task_elem, task, {"t1": 1})
    assert task_elem.find("PredecessorLink") is None


def test_lag_format_is_days():
    """LagFormat must always be '5' (days unit for MS Project)."""
    task = {"id": "t2", "name": "Т2", "dependencies": ["t1"],
            "dependency_type": "SS", "lag_days": 5}
    uid_map = {"t1": 1, "t2": 2}
    task_elem = ET.Element("Task")
    _add_predecessor_links(task_elem, task, uid_map)
    assert task_elem.find("PredecessorLink/LagFormat").text == "5"


# ---------------------------------------------------------------------------
# export_to_mspdi_xml — integration
# ---------------------------------------------------------------------------

def test_empty_schedule_returns_none():
    """Empty schedule → None (no XML generated)."""
    result = export_to_mspdi_xml([], "Тест")
    assert result is None


def test_duration_format_pt_hours():
    """Task with 10 days → Duration=PT80H0M0S (10×8h)."""
    schedule = [_minimal_task("t1", "Задача 1", duration=10)]
    root = _parse_export(schedule)
    task = _task_by_name(root, "Задача 1")
    assert task is not None
    dur = task.find(f"{{{NS}}}Duration")
    assert dur is not None and dur.text == "PT80H0M0S"


def test_duration_format_5_on_tasks():
    """Every task must have DurationFormat=5 (days, not elapsed days)."""
    schedule = [
        _minimal_task("t1", "Задача 1", duration=5),
        _minimal_task("t2", "Задача 2", duration=3),
    ]
    root = _parse_export(schedule)
    for task in _all_tasks(root):
        df = task.find(f"{{{NS}}}DurationFormat")
        assert df is not None and df.text == "5", (
            f"Task '{task.find(f'{{{NS}}}Name').text}' missing DurationFormat=5"
        )


def test_manual_scheduling_on_all_tasks():
    """Every task must have Manual=0 (auto-scheduled, no pin icons)."""
    schedule = [_minimal_task("t1", "Задача 1")]
    root = _parse_export(schedule)
    for task in _all_tasks(root):
        manual = task.find(f"{{{NS}}}Manual")
        assert manual is not None and manual.text == "0"


def test_root_task_uid_zero():
    """First task element must be the project root with UID=0."""
    schedule = [_minimal_task("t1", "Задача 1")]
    root = _parse_export(schedule)
    tasks = _all_tasks(root)
    uid0 = tasks[0].find(f"{{{NS}}}UID")
    assert uid0 is not None and uid0.text == "0"


def test_seven_day_calendar_minutes_per_week():
    """7-day calendar → MinutesPerWeek = 7 × 480 = 3360."""
    schedule = [_minimal_task("t1", "Задача")]
    xml_bytes = export_to_mspdi_xml(schedule, "П", start_date="2026-06-01",
                                    calendar_type="7-day")
    root = ET.fromstring(xml_bytes)
    mpw = _xml_text(root, "MinutesPerWeek")
    assert mpw == str(7 * 480)


def test_five_day_calendar_minutes_per_week():
    """5-day calendar → MinutesPerWeek = 5 × 480 = 2400."""
    schedule = [_minimal_task("t1", "Задача")]
    xml_bytes = export_to_mspdi_xml(schedule, "П", start_date="2026-06-01",
                                    calendar_type="5-day")
    root = ET.fromstring(xml_bytes)
    mpw = _xml_text(root, "MinutesPerWeek")
    assert mpw == str(5 * 480)


def test_outline_numbers_flat():
    """Three flat tasks → OutlineNumbers 1, 2, 3."""
    schedule = [
        _minimal_task("t1", "Задача 1"),
        _minimal_task("t2", "Задача 2"),
        _minimal_task("t3", "Задача 3"),
    ]
    root = _parse_export(schedule)
    # Skip UID=0 root task; collect outline numbers for non-root tasks
    outline_nums = []
    for task in _all_tasks(root):
        uid = task.find(f"{{{NS}}}UID")
        if uid is not None and uid.text == "0":
            continue
        on = task.find(f"{{{NS}}}OutlineNumber")
        if on is not None:
            outline_nums.append(on.text)
    assert outline_nums == ["1", "2", "3"]


def test_outline_numbers_with_children():
    """Parent + 2 children → '1', '1.1', '1.2'."""
    schedule = [{
        "id": "p1", "name": "Фаза", "duration": 10, "start_day": 1,
        "sub_activities": [
            _minimal_task("c1", "Дете 1"),
            _minimal_task("c2", "Дете 2"),
        ],
    }]
    root = _parse_export(schedule)
    outline_nums = []
    for task in _all_tasks(root):
        uid = task.find(f"{{{NS}}}UID")
        if uid is not None and uid.text == "0":
            continue
        on = task.find(f"{{{NS}}}OutlineNumber")
        if on is not None:
            outline_nums.append(on.text)
    assert outline_nums == ["1", "1.1", "1.2"]


def test_custom_field_dn_written():
    """Task with 'dn' → Text1 ExtendedAttribute with correct FieldID."""
    schedule = [{"id": "t1", "name": "Тласкател", "duration": 5,
                 "start_day": 1, "dn": "DN300", "diameter": "DN300"}]
    root = _parse_export(schedule)
    task = _task_by_name(root, "Тласкател")
    assert task is not None
    # Find ExtendedAttribute with FieldID for Text1 (DN)
    ea_found = False
    for ea in task.findall(f"{{{NS}}}ExtendedAttribute"):
        fid = ea.find(f"{{{NS}}}FieldID")
        if fid is not None and fid.text == "188743731":
            ea_found = True
            val = ea.find(f"{{{NS}}}Value")
            assert val is not None and val.text == "DN300"
    assert ea_found, "Text1 (DN) ExtendedAttribute not found"


def test_custom_field_length_m_written():
    """Task with 'length_m' → Number1 ExtendedAttribute."""
    schedule = [{"id": "t1", "name": "Участък 1", "duration": 5,
                 "start_day": 1, "length_m": 350}]
    root = _parse_export(schedule)
    task = _task_by_name(root, "Участък 1")
    assert task is not None
    ea_found = False
    for ea in task.findall(f"{{{NS}}}ExtendedAttribute"):
        fid = ea.find(f"{{{NS}}}FieldID")
        if fid is not None and fid.text == "188743767":
            ea_found = True
            val = ea.find(f"{{{NS}}}Value")
            assert val is not None and val.text == "350"
    assert ea_found, "Number1 (length_m) ExtendedAttribute not found"


def test_resource_and_assignment_created_for_team():
    """Task with 'team' → Resource entry + Assignment entry created."""
    schedule = [{"id": "t1", "name": "Монтаж", "duration": 5,
                 "start_day": 1, "team": "Екип А"}]
    root = _parse_export(schedule)
    resources = root.findall(f".//{{{NS}}}Resource")
    # Should have UID=0 empty + 1 real resource
    assert len(resources) >= 2
    names = [r.find(f"{{{NS}}}Name").text for r in resources if r.find(f"{{{NS}}}Name") is not None]
    assert "Екип А" in names

    assignments = root.findall(f".//{{{NS}}}Assignment")
    assert len(assignments) >= 1


def test_summary_task_no_assignment():
    """Summary tasks (with children) must NOT get assignments."""
    schedule = [{
        "id": "phase", "name": "Фаза", "duration": 20, "start_day": 1,
        "team": "Екип А",
        "sub_activities": [
            {"id": "sub1", "name": "Под-задача", "duration": 10,
             "start_day": 1, "team": "Екип А"},
        ],
    }]
    root = _parse_export(schedule)
    # Find task UID for "Фаза"
    phase_task = _task_by_name(root, "Фаза")
    assert phase_task is not None
    phase_uid = phase_task.find(f"{{{NS}}}UID").text

    # No assignment should reference the summary task's UID
    for asn in root.findall(f".//{{{NS}}}Assignment"):
        task_uid = asn.find(f"{{{NS}}}TaskUID")
        assert task_uid is None or task_uid.text != phase_uid, (
            f"Summary task (UID={phase_uid}) should not have an assignment"
        )


def test_save_version_14():
    """SaveVersion must be 14 for MS Project 2010+ compatibility."""
    schedule = [_minimal_task("t1", "Т")]
    root = _parse_export(schedule)
    assert _xml_text(root, "SaveVersion") == "14"


def test_duration_format_5_on_project():
    """Project-level DurationFormat must be 5."""
    schedule = [_minimal_task("t1", "Т")]
    root = _parse_export(schedule)
    assert _xml_text(root, "DurationFormat") == "5"


if __name__ == "__main__":
    tests = [
        test_flatten_flat_list_unchanged,
        test_flatten_one_level_nesting,
        test_flatten_children_inherit_parent_id,
        test_flatten_two_level_nesting,
        test_flatten_empty_list,
        test_flatten_is_subtask_flag,
        test_predecessor_link_fs_no_lag,
        test_predecessor_link_ss_with_lag,
        test_predecessor_link_ff,
        test_predecessor_link_missing_uid_skipped,
        test_predecessor_link_no_dependencies,
        test_lag_format_is_days,
        test_empty_schedule_returns_none,
        test_duration_format_pt_hours,
        test_duration_format_5_on_tasks,
        test_manual_scheduling_on_all_tasks,
        test_root_task_uid_zero,
        test_seven_day_calendar_minutes_per_week,
        test_five_day_calendar_minutes_per_week,
        test_outline_numbers_flat,
        test_outline_numbers_with_children,
        test_custom_field_dn_written,
        test_custom_field_length_m_written,
        test_resource_and_assignment_created_for_team,
        test_summary_task_no_assignment,
        test_save_version_14,
        test_duration_format_5_on_project,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as exc:
            print(f"  FAIL  {t.__name__}: {exc}")
        except Exception as exc:
            print(f"  ERROR {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
