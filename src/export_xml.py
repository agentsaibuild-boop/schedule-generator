"""MSPDI XML export for MS Project compatibility.

Generates valid MSPDI XML (Microsoft Project Data Interchange) files
compatible with MS Project 2010+. Key features:
- DurationFormat=5 (days, NOT elapsed days)
- Manual scheduling (prevents MS Project recalculation)
- 7-day work calendar with lunch break (08:00-12:00, 13:00-17:00)
- Custom fields: Text1=DN, Number1=L(m), Text2=Мярка, Text3=Екип
- Proper UID=0 root task and empty resource
- SaveVersion=14 for MS Project 2010+ compatibility

Based on lessons from 8 real projects (85 lessons learned).
"""

from __future__ import annotations

import io
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

NAMESPACE = "http://schemas.microsoft.com/project"

# Custom field IDs (verified against MS Project)
FIELD_ID_TEXT1 = "188743731"    # Text1 = DN
FIELD_ID_TEXT2 = "188743734"    # Text2 = Мярка
FIELD_ID_TEXT3 = "188743737"    # Text3 = Екип
FIELD_ID_NUMBER1 = "188743767"  # Number1 = L(м)


# ---------------------------------------------------------------------------
# Main export function
# ---------------------------------------------------------------------------


def export_to_mspdi_xml(
    schedule_data: list[dict],
    project_name: str,
    start_date: str = "2026-06-01",
    calendar_type: str = "7-day",
    filename: str | None = None,
) -> bytes | None:
    """Generate MSPDI XML file compatible with MS Project 2010+.

    Args:
        schedule_data: List of task dicts from the schedule.
        project_name: Name of the project for the XML header.
        start_date: Calendar start date (ISO format).
        calendar_type: "7-day" (all days working) or "5-day" (Mon-Fri).
        filename: Optional file path to also save XML to disk.

    Returns:
        XML file as bytes, or None on error.
    """
    if not schedule_data:
        logger.warning("No schedule data for XML export")
        return None

    try:
        root = _build_xml(schedule_data, project_name, start_date, calendar_type)
        xml_bytes = _serialize_xml(root)

        if filename:
            Path(filename).write_bytes(xml_bytes)
            logger.info("XML saved to %s", filename)

        return xml_bytes

    except Exception as exc:
        logger.error("XML export failed: %s", exc, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# XML builder
# ---------------------------------------------------------------------------


def _build_xml(
    schedule_data: list[dict],
    project_name: str,
    start_date: str,
    calendar_type: str,
) -> ET.Element:
    """Build the full MSPDI XML tree."""
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")

    # Calculate project end date
    max_end_day = max(
        (t.get("end_day", t.get("start_day", 0) + t.get("duration", 0))
         for t in schedule_data),
        default=1,
    )
    finish_dt = start_dt + timedelta(days=max_end_day - 1)

    # Root element
    root = ET.Element("Project")
    root.set("xmlns", NAMESPACE)

    # --- 1. Project properties ---
    ET.SubElement(root, "SaveVersion").text = "14"
    ET.SubElement(root, "Name").text = project_name
    ET.SubElement(root, "StartDate").text = f"{start_date}T08:00:00"
    ET.SubElement(root, "FinishDate").text = finish_dt.strftime("%Y-%m-%dT17:00:00")
    ET.SubElement(root, "CalendarUID").text = "1"
    ET.SubElement(root, "DurationFormat").text = "5"  # CRITICAL: 5=days
    ET.SubElement(root, "DefaultStartTime").text = "08:00:00"
    ET.SubElement(root, "DefaultFinishTime").text = "17:00:00"
    ET.SubElement(root, "MinutesPerDay").text = "480"

    if calendar_type == "7-day":
        ET.SubElement(root, "MinutesPerWeek").text = "3360"  # 7 × 480
    else:
        ET.SubElement(root, "MinutesPerWeek").text = "2400"  # 5 × 480

    ET.SubElement(root, "DaysPerMonth").text = "30"

    # --- 2. Extended attributes (custom fields) ---
    _build_extended_attributes(root)

    # --- 3. Calendar ---
    _build_calendar(root, calendar_type)

    # --- 4. Tasks ---
    flat_tasks = _flatten_schedule(schedule_data)
    uid_map = _build_tasks(root, flat_tasks, start_dt, project_name)

    # --- 5. Resources ---
    resource_map = _build_resources(root, flat_tasks)

    # --- 6. Assignments ---
    _build_assignments(root, flat_tasks, uid_map, resource_map)

    return root


def _build_extended_attributes(root: ET.Element) -> None:
    """Add custom field definitions (DN, Length, Measure, Team)."""
    ext_attrs = ET.SubElement(root, "ExtendedAttributes")

    # Text1 = DN
    attr1 = ET.SubElement(ext_attrs, "ExtendedAttribute")
    ET.SubElement(attr1, "FieldID").text = FIELD_ID_TEXT1
    ET.SubElement(attr1, "FieldName").text = "Text1"
    ET.SubElement(attr1, "Alias").text = "DN"

    # Number1 = L(м)
    attr2 = ET.SubElement(ext_attrs, "ExtendedAttribute")
    ET.SubElement(attr2, "FieldID").text = FIELD_ID_NUMBER1
    ET.SubElement(attr2, "FieldName").text = "Number1"
    ET.SubElement(attr2, "Alias").text = "L(\u043c)"  # L(м)

    # Text2 = Мярка
    attr3 = ET.SubElement(ext_attrs, "ExtendedAttribute")
    ET.SubElement(attr3, "FieldID").text = FIELD_ID_TEXT2
    ET.SubElement(attr3, "FieldName").text = "Text2"
    ET.SubElement(attr3, "Alias").text = "\u041c\u044f\u0440\u043a\u0430"  # Мярка

    # Text3 = Екип
    attr4 = ET.SubElement(ext_attrs, "ExtendedAttribute")
    ET.SubElement(attr4, "FieldID").text = FIELD_ID_TEXT3
    ET.SubElement(attr4, "FieldName").text = "Text3"
    ET.SubElement(attr4, "Alias").text = "\u0415\u043a\u0438\u043f"  # Екип


def _build_calendar(root: ET.Element, calendar_type: str) -> None:
    """Build the work calendar (7-day or 5-day)."""
    calendars = ET.SubElement(root, "Calendars")
    cal = ET.SubElement(calendars, "Calendar")
    ET.SubElement(cal, "UID").text = "1"
    ET.SubElement(cal, "Name").text = (
        "7-Day Work Calendar" if calendar_type == "7-day" else "5-Day Work Calendar"
    )
    ET.SubElement(cal, "IsBaseCalendar").text = "1"

    weekdays = ET.SubElement(cal, "WeekDays")

    for day_type in range(1, 8):  # 1=Sunday ... 7=Saturday
        weekday = ET.SubElement(weekdays, "WeekDay")
        ET.SubElement(weekday, "DayType").text = str(day_type)

        if calendar_type == "5-day" and day_type in (1, 7):
            # Sunday=1, Saturday=7 → non-working
            ET.SubElement(weekday, "DayWorking").text = "0"
        else:
            ET.SubElement(weekday, "DayWorking").text = "1"
            work_times = ET.SubElement(weekday, "WorkingTimes")

            # Morning shift: 08:00-12:00
            wt1 = ET.SubElement(work_times, "WorkingTime")
            ET.SubElement(wt1, "FromTime").text = "08:00:00"
            ET.SubElement(wt1, "ToTime").text = "12:00:00"

            # Afternoon shift: 13:00-17:00
            wt2 = ET.SubElement(work_times, "WorkingTime")
            ET.SubElement(wt2, "FromTime").text = "13:00:00"
            ET.SubElement(wt2, "ToTime").text = "17:00:00"


def _build_tasks(
    root: ET.Element,
    flat_tasks: list[dict],
    start_dt: datetime,
    project_name: str,
) -> dict[str, int]:
    """Build the Tasks section. Returns a map of task_id → UID."""
    tasks_elem = ET.SubElement(root, "Tasks")

    # Root task (UID=0, REQUIRED by MS Project)
    root_task = ET.SubElement(tasks_elem, "Task")
    ET.SubElement(root_task, "UID").text = "0"
    ET.SubElement(root_task, "ID").text = "0"
    ET.SubElement(root_task, "Name").text = project_name
    ET.SubElement(root_task, "OutlineLevel").text = "0"
    ET.SubElement(root_task, "Duration").text = "PT0H0M0S"
    ET.SubElement(root_task, "DurationFormat").text = "5"
    ET.SubElement(root_task, "Manual").text = "1"
    ET.SubElement(root_task, "Summary").text = "1"
    ET.SubElement(root_task, "CalendarUID").text = "1"

    uid_map: dict[str, int] = {}
    uid_counter = 1

    # Outline number tracking: (level, parent_id) → current count
    _outline_counters: dict[tuple, int] = {}
    _outline_nums: dict[str, str] = {}  # task_id → "1.2.3"

    for task in flat_tasks:
        task_id = task.get("id", "")
        uid_map[task_id] = uid_counter

        task_elem = ET.SubElement(tasks_elem, "Task")
        ET.SubElement(task_elem, "UID").text = str(uid_counter)
        ET.SubElement(task_elem, "ID").text = str(uid_counter)
        ET.SubElement(task_elem, "Name").text = task.get("name", "")

        # Outline level
        outline = _get_outline_level(task)
        ET.SubElement(task_elem, "OutlineLevel").text = str(outline)

        # OutlineNumber (e.g. "1", "1.1", "1.1.2") — required for WBS hierarchy in MS Project
        parent_id = task.get("parent_id")
        parent_num = _outline_nums.get(parent_id, "") if parent_id else ""
        counter_key = (outline, parent_id or "")
        _outline_counters[counter_key] = _outline_counters.get(counter_key, 0) + 1
        outline_num = (
            f"{parent_num}.{_outline_counters[counter_key]}" if parent_num
            else str(_outline_counters[counter_key])
        )
        _outline_nums[task_id] = outline_num
        ET.SubElement(task_elem, "OutlineNumber").text = outline_num

        # Dates
        start_day = task.get("start_day", 1)
        duration = task.get("duration", 0)
        end_day = task.get("end_day", start_day + max(duration, 1) - 1)

        task_start = start_dt + timedelta(days=start_day - 1)
        task_finish = start_dt + timedelta(days=end_day - 1)

        ET.SubElement(task_elem, "Start").text = task_start.strftime("%Y-%m-%dT08:00:00")
        ET.SubElement(task_elem, "Finish").text = task_finish.strftime("%Y-%m-%dT17:00:00")

        # Duration: days × 8 hours → PT{hours}H0M0S
        hours = max(duration, 0) * 8
        ET.SubElement(task_elem, "Duration").text = f"PT{hours}H0M0S"
        ET.SubElement(task_elem, "DurationFormat").text = "5"  # CRITICAL: days

        # Manual scheduling (CRITICAL: prevents MS Project recalculation)
        ET.SubElement(task_elem, "Manual").text = "1"

        # Calendar
        ET.SubElement(task_elem, "CalendarUID").text = "1"

        # Summary flag
        is_summary = bool(task.get("_has_children", False))
        ET.SubElement(task_elem, "Summary").text = "1" if is_summary else "0"

        # Milestone (0 duration)
        ET.SubElement(task_elem, "Milestone").text = "1" if duration == 0 else "0"

        # Critical path
        ET.SubElement(task_elem, "Critical").text = (
            "1" if task.get("is_critical") else "0"
        )

        # Custom fields (Extended Attributes on task)
        _add_task_custom_fields(task_elem, task)

        # Dependencies (Predecessor Links)
        _add_predecessor_links(task_elem, task, uid_map)

        uid_counter += 1

    return uid_map


def _add_task_custom_fields(task_elem: ET.Element, task: dict) -> None:
    """Add custom field values to a task element."""
    # Text1 = DN (diameter)
    diameter = task.get("diameter")
    if diameter:
        ea = ET.SubElement(task_elem, "ExtendedAttribute")
        ET.SubElement(ea, "FieldID").text = FIELD_ID_TEXT1
        ET.SubElement(ea, "Value").text = str(diameter)

    # Number1 = L(м) (length in meters)
    length = task.get("length_m")
    if length:
        ea = ET.SubElement(task_elem, "ExtendedAttribute")
        ET.SubElement(ea, "FieldID").text = FIELD_ID_NUMBER1
        ET.SubElement(ea, "Value").text = str(length)

    # Text3 = Екип (team)
    team = task.get("team")
    if team and team != "\u2014":
        ea = ET.SubElement(task_elem, "ExtendedAttribute")
        ET.SubElement(ea, "FieldID").text = FIELD_ID_TEXT3
        ET.SubElement(ea, "Value").text = team


_DEPENDENCY_TYPE_MAP = {
    "FS": "1",  # Finish-to-Start (default)
    "SS": "3",  # Start-to-Start
    "FF": "0",  # Finish-to-Finish
    "SF": "2",  # Start-to-Finish
}


def _add_predecessor_links(
    task_elem: ET.Element,
    task: dict,
    uid_map: dict[str, int],
) -> None:
    """Add dependency links to a task element.

    Reads dependency_type and lag_days from task dict (set by enrich_for_msproject).
    LinkLag is in tenths of minutes: 1 day = 480 min × 10 = 4800.
    """
    deps = task.get("dependencies", [])
    if not deps:
        return

    dep_type_str = (task.get("dependency_type") or "FS").upper()
    type_code = _DEPENDENCY_TYPE_MAP.get(dep_type_str, "1")
    lag_days = int(task.get("lag_days") or 0)
    link_lag = str(lag_days * 4800)  # tenths of minutes per day

    for dep_id in deps:
        dep_uid = uid_map.get(dep_id)
        if dep_uid is None:
            logger.debug("Predecessor %s not found in uid_map (task %s)", dep_id, task.get("id"))
            continue

        pred = ET.SubElement(task_elem, "PredecessorLink")
        ET.SubElement(pred, "PredecessorUID").text = str(dep_uid)
        ET.SubElement(pred, "Type").text = type_code
        ET.SubElement(pred, "LinkLag").text = link_lag
        ET.SubElement(pred, "LagFormat").text = "5"  # days


def _build_resources(
    root: ET.Element, flat_tasks: list[dict]
) -> dict[str, int]:
    """Build the Resources section. Returns team_name → resource_uid map."""
    resources_elem = ET.SubElement(root, "Resources")

    # Empty resource (UID=0, REQUIRED by MS Project)
    empty_res = ET.SubElement(resources_elem, "Resource")
    ET.SubElement(empty_res, "UID").text = "0"
    ET.SubElement(empty_res, "ID").text = "0"
    ET.SubElement(empty_res, "Name").text = ""

    resource_map: dict[str, int] = {}
    res_uid = 1

    for task in flat_tasks:
        team = task.get("team")
        if team and team != "\u2014" and team not in resource_map:
            resource_map[team] = res_uid

            res = ET.SubElement(resources_elem, "Resource")
            ET.SubElement(res, "UID").text = str(res_uid)
            ET.SubElement(res, "ID").text = str(res_uid)
            ET.SubElement(res, "Name").text = team
            ET.SubElement(res, "Type").text = "1"  # Work resource

            res_uid += 1

    return resource_map


def _build_assignments(
    root: ET.Element,
    flat_tasks: list[dict],
    uid_map: dict[str, int],
    resource_map: dict[str, int],
) -> None:
    """Build the Assignments section (task↔resource links)."""
    assignments_elem = ET.SubElement(root, "Assignments")
    asn_uid = 1

    for task in flat_tasks:
        task_id = task.get("id", "")
        team = task.get("team")
        is_summary = task.get("_has_children", False)

        if not team or team == "\u2014" or is_summary:
            continue

        task_uid = uid_map.get(task_id)
        res_uid = resource_map.get(team)
        if task_uid is None or res_uid is None:
            continue

        asn = ET.SubElement(assignments_elem, "Assignment")
        ET.SubElement(asn, "UID").text = str(asn_uid)
        ET.SubElement(asn, "TaskUID").text = str(task_uid)
        ET.SubElement(asn, "ResourceUID").text = str(res_uid)

        asn_uid += 1


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _flatten_schedule(
    schedule_data: list[dict],
    _parent_id: str | None = None,
    _depth: int = 1,
) -> list[dict]:
    """Flatten hierarchical schedule into a list with proper outline levels.

    Recursively walks sub_activities up to 3 levels deep.
    Sets _has_children, _is_subtask, _outline_level on each entry.
    """
    result = []
    for task in schedule_data:
        subs = task.get("sub_activities") or []
        has_subs = bool(subs)
        entry = {
            **task,
            "_has_children": has_subs,
            "_is_subtask": _depth > 1,
            "_outline_level": _depth,
        }
        if not entry.get("parent_id") and _parent_id:
            entry["parent_id"] = _parent_id
        result.append(entry)

        if has_subs:
            result.extend(
                _flatten_schedule(subs, _parent_id=task.get("id"), _depth=_depth + 1)
            )

    return result


def _get_outline_level(task: dict) -> int:
    """Determine the outline level for a task (1-based, 0 = project root)."""
    return task.get("_outline_level", 1)


def _serialize_xml(root: ET.Element) -> bytes:
    """Serialize XML tree to bytes with proper UTF-8 header."""
    # Write to buffer
    buffer = io.BytesIO()
    tree = ET.ElementTree(root)

    # ET.write with xml_declaration for proper header
    tree.write(buffer, encoding="UTF-8", xml_declaration=True)
    return buffer.getvalue()
