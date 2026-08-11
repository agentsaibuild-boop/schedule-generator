"""Четене на MSPDI XML обратно във вътрешния формат на графика.

ЗАЩО (одит 2026-07-23, точка 9): валиден XML не доказва, че графикът е
коректен.  Единственият начин да се знае е round-trip — изнеси, прочети
обратно, сравни.  Без това „експортът работи" е допускане.

Пълният round-trip минава през MS Project (изнеси → отвори → запази пак →
сравни) и иска машина с инсталиран Project.  Този модул прави ПЪРВАТА
половина, която не изисква нищо: собствен round-trip, който лови загуба на
данни, разместени зависимости, изгубени продължителности и объркана
идентичност.

Не е пълноценен MSPDI четец — чете това, което този проект пише.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any

from src.export_xml import (
    FIELD_ID_NUMBER1,
    FIELD_ID_TEXT1,
    FIELD_ID_TEXT2,
    FIELD_ID_TEXT3,
    FIELD_ID_TEXT4,
    FIELD_ID_TEXT5,
    LINK_LAG_FACTOR,
    MINUTES_PER_DAY,
    NAMESPACE,
)

logger = logging.getLogger(__name__)

_NS = f"{{{NAMESPACE}}}"
_DURATION_RE = re.compile(r"PT(\d+)H(\d+)M(\d+)S")

# Обратно на _DEPENDENCY_TYPE_MAP в export_xml
_TYPE_BY_CODE = {"0": "FF", "1": "FS", "2": "SS", "3": "SF"}


def _text(elem: ET.Element, tag: str) -> str:
    """Стойност на дъщерен таг (с или без namespace)."""
    node = elem.find(f"{_NS}{tag}")
    if node is None:
        node = elem.find(tag)
    return (node.text or "").strip() if node is not None else ""


def _duration_to_days(value: str) -> int:
    """PT80H0M0S → 10 дни.  Празно/непознато → 0."""
    match = _DURATION_RE.match(value or "")
    if not match:
        return 0
    hours = int(match.group(1))
    return round(hours / (MINUTES_PER_DAY / 60))


def _custom_fields(task_elem: ET.Element) -> dict[str, str]:
    """Прочети ExtendedAttribute стойностите по FieldID."""
    fields: dict[str, str] = {}
    attrs = task_elem.findall(f"{_NS}ExtendedAttribute") or task_elem.findall(
        "ExtendedAttribute")
    for ea in attrs:
        field_id = _text(ea, "FieldID")
        value = _text(ea, "Value")
        if field_id:
            fields[field_id] = value
    return fields


def parse_mspdi(xml_data: bytes | str) -> dict[str, Any]:
    """Прочети MSPDI XML във вътрешния формат.

    Args:
        xml_data: Съдържанието на XML файла.

    Returns:
        {
          "project_name": str,
          "start_date": "YYYY-MM-DD",
          "tasks": [ {id, name, duration, start_day, end_day, dependencies,
                      diameter, length_m, team, unit, milestone} ],
          "warnings": [str],
        }
    """
    if isinstance(xml_data, bytes):
        xml_data = xml_data.decode("utf-8")
    root = ET.fromstring(xml_data)

    warnings: list[str] = []
    project_start = _text(root, "StartDate")[:10]
    start_dt = None
    if project_start:
        try:
            start_dt = datetime.strptime(project_start, "%Y-%m-%d")
        except ValueError:
            warnings.append(f"Неразчетена начална дата: {project_start!r}")

    tasks_elem = root.find(f"{_NS}Tasks")
    if tasks_elem is None:
        tasks_elem = root.find("Tasks")
    if tasks_elem is None:
        return {"project_name": _text(root, "Name"), "start_date": project_start,
                "tasks": [], "warnings": ["Липсва секция Tasks."]}

    # Първи проход: UID → вътрешно ID (нужно, за да се разчетат връзките).
    raw: list[tuple[ET.Element, dict[str, str]]] = []
    uid_to_id: dict[str, str] = {}
    task_elems = tasks_elem.findall(f"{_NS}Task") or tasks_elem.findall("Task")
    for task_elem in task_elems:
        uid = _text(task_elem, "UID")
        if uid == "0":
            continue  # служебната коренова задача
        fields = _custom_fields(task_elem)
        internal_id = fields.get(FIELD_ID_TEXT4) or _text(task_elem, "Name")
        uid_to_id[uid] = internal_id
        raw.append((task_elem, fields))

    # Втори проход: задачите и зависимостите.
    tasks: list[dict[str, Any]] = []
    for task_elem, fields in raw:
        duration = _duration_to_days(_text(task_elem, "Duration"))
        is_milestone = _text(task_elem, "Milestone") == "1"

        start_day = end_day = None
        if start_dt:
            for tag, target in (("Start", "start"), ("Finish", "finish")):
                stamp = _text(task_elem, tag)[:10]
                if not stamp:
                    continue
                try:
                    delta = (datetime.strptime(stamp, "%Y-%m-%d") - start_dt).days + 1
                except ValueError:
                    continue
                if target == "start":
                    start_day = delta
                else:
                    end_day = delta

        dependencies: list[dict[str, Any]] = []
        links = task_elem.findall(f"{_NS}PredecessorLink") or task_elem.findall(
            "PredecessorLink")
        for link in links:
            pred_uid = _text(link, "PredecessorUID")
            pred_id = uid_to_id.get(pred_uid)
            if pred_id is None:
                warnings.append(f"Връзка към непознат UID {pred_uid!r}.")
                continue
            lag_raw = _text(link, "LinkLag")
            try:
                lag_days = int(lag_raw) // (MINUTES_PER_DAY * LINK_LAG_FACTOR)
            except ValueError:
                lag_days = 0
            dependencies.append({
                "predecessor_id": pred_id,
                "type": _TYPE_BY_CODE.get(_text(link, "Type"), "FS"),
                "lag_days": lag_days,
            })

        task: dict[str, Any] = {
            "id": fields.get(FIELD_ID_TEXT4) or _text(task_elem, "Name"),
            "name": _text(task_elem, "Name"),
            "duration": 0 if is_milestone else duration,
            "dependencies": dependencies,
            "milestone": is_milestone,
        }
        if start_day is not None:
            task["start_day"] = start_day
        if end_day is not None:
            task["end_day"] = end_day
        if fields.get(FIELD_ID_TEXT1):
            task["diameter"] = fields[FIELD_ID_TEXT1]
        if fields.get(FIELD_ID_NUMBER1):
            task["length_m"] = fields[FIELD_ID_NUMBER1]
        if fields.get(FIELD_ID_TEXT2):
            task["unit"] = fields[FIELD_ID_TEXT2]
        if fields.get(FIELD_ID_TEXT3):
            task["team"] = fields[FIELD_ID_TEXT3]
        if fields.get(FIELD_ID_TEXT5):
            task["source_ref"] = fields[FIELD_ID_TEXT5]

        tasks.append(task)

    return {
        "project_name": _text(root, "Name"),
        "start_date": project_start,
        "tasks": tasks,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Сравнение
# ---------------------------------------------------------------------------

def compare_schedules(before: list[dict], after: list[dict]) -> dict[str, Any]:
    """Сравни оригиналния график с прочетения обратно.

    Сравняват се само полетата, които експортът ТВЪРДИ, че носи.  Целта е
    да се хване загуба на данни, не да се изисква побитово съвпадение.

    Returns:
        {identical: bool, missing, added, differences: [{id, field, before, after}]}
    """
    before_by_id = {str(t.get("id")): t for t in before if t.get("id")}
    after_by_id = {str(t.get("id")): t for t in after if t.get("id")}

    missing = sorted(set(before_by_id) - set(after_by_id))
    added = sorted(set(after_by_id) - set(before_by_id))
    differences: list[dict[str, Any]] = []

    def _links(task: dict) -> set[tuple[str, str, int]]:
        from src.schedule_builder import dependency_links
        return {(l.predecessor_id, l.type, l.lag_days) for l in dependency_links(task)}

    for tid in sorted(set(before_by_id) & set(after_by_id)):
        src, dst = before_by_id[tid], after_by_id[tid]

        for field in ("name", "duration", "start_day", "end_day"):
            if field not in src:
                continue
            src_value, dst_value = src.get(field), dst.get(field)
            if field != "name":
                src_value = int(src_value) if isinstance(src_value, (int, float)) else src_value
                dst_value = int(dst_value) if isinstance(dst_value, (int, float)) else dst_value
            if src_value != dst_value:
                differences.append({
                    "id": tid, "field": field,
                    "before": src_value, "after": dst_value,
                })

        # Стойностите на custom полетата се връщат като низове.
        for field in ("team", "unit"):
            if field in src and str(src[field]) != str(dst.get(field, "")):
                differences.append({
                    "id": tid, "field": field,
                    "before": src[field], "after": dst.get(field),
                })

        if _links(src) != _links(dst):
            differences.append({
                "id": tid, "field": "dependencies",
                "before": sorted(_links(src)), "after": sorted(_links(dst)),
            })

    return {
        "identical": not (missing or added or differences),
        "missing": missing,
        "added": added,
        "differences": differences,
    }
