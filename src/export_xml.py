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
import math
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from src.ai_disclosure import (
    CONTENT_DISCLOSURE_BG,
    CONTENT_NOTICE_LONG_BG,
    SYSTEM_NAME,
)

logger = logging.getLogger(__name__)


def _working_day_to_date(start_dt: datetime, day_index: int, calendar_type: str) -> datetime:
    """Превърни индекс на РАБОТЕН ден в календарна дата.

    `start_day`/`end_day` в графика броят работни дни (ден 1 = първият
    работен ден).  При 7-дневен календар всеки календарен ден е работен и
    преобразуването е тривиално.  При 5-дневен трябва да се прескачат
    съботите и неделите — иначе датите в XML-а се разминават с
    продължителностите и MS Project премества задачите при отваряне.

    Args:
        start_dt: Начална дата на проекта (ден 1).
        day_index: 1-базиран индекс на работен ден.
        calendar_type: "7-day" или "5-day".

    Returns:
        Календарната дата на този работен ден.
    """
    steps = max(int(day_index), 1) - 1

    if calendar_type != "5-day":
        return start_dt + timedelta(days=steps)

    # Ако проектът стартира в почивен ден, започни от следващия работен.
    current = start_dt
    while current.weekday() >= 5:          # 5=събота, 6=неделя
        current += timedelta(days=1)

    remaining = steps
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current

NAMESPACE = "http://schemas.microsoft.com/project"

# MS Project calendar constants
MINUTES_PER_DAY = 480           # 8h × 60 = 480 min/working day
LINK_LAG_FACTOR = 10            # LinkLag unit = tenths of minutes (480 × 10 = 4800/day)

# Custom field IDs (verified against MS Project)
#: MSPDI DurationFormat: 3=минути, 5=ЧАСОВЕ, 7=ДНИ, 8=седмици.  Проверено в
#: XML-а, който самият MS Project изнася (25.08.2026).
DURATION_FORMAT_DAYS = "7"

FIELD_ID_TEXT1 = "188743731"    # Text1 = DN
FIELD_ID_TEXT2 = "188743734"    # Text2 = Мярка
FIELD_ID_TEXT3 = "188743737"    # Text3 = Екип
FIELD_ID_NUMBER1 = "188743767"  # Number1 = L(м)
# Text4 = вътрешното ID на задачата.  Одит 2026-07-23: ID-то изобщо не
# влизаше в XML-а, тоест round-trip идентичност беше невъзможна — редактиран
# в MS Project и върнат файл не може да се свърже обратно със задачите.
FIELD_ID_TEXT4 = "188743740"    # Text4 = ID
# Text5 = цитатът към КСС (`КСС.xlsx!лист!ред`).  Одит 07.08.2026: „конкретна
# MS Project задача още не може надеждно да се проследи обратно до точния ред
# в анонимизирания КСС."  Цитатът съществуваше в задачата (`source_ref`) и се
# проверяваше от гейта, но никога не влизаше в експорта — тоест беше проверим
# отвътре и невидим отвън.
FIELD_ID_TEXT5 = "188743743"    # Text5 = Източник (КСС)

# Пространствената идентичност на участъка.  Одит 07.08.2026: „спрямо човешкия
# еталон това още е сериозно under-segmentation" — но преди това изобщо не се
# виждаше КАК е сегментирано, защото полетата не излизаха от пакета.
FIELD_ID_TEXT6 = "188743746"    # Text6 = Участък (ID)
FIELD_ID_TEXT7 = "188743749"    # Text7 = Улица
FIELD_ID_TEXT8 = "188743752"    # Text8 = От възел
FIELD_ID_TEXT9 = "188743755"    # Text9 = До възел
FIELD_ID_TEXT10 = "188743758"   # Text10 = Запис (ID)
# ЧИСЛОВИТЕ ПОЛЕТА ВЪРВЯТ ПРЕЗ ЕДНО, НЕ ПРЕЗ ТРИ (25.08.2026).  Text1…Text10
# наистина се увеличават с 3, но Number1…Number20 — с 1.  Тук стояха номера,
# сметнати по правилото на текстовите полета, тоест Number2…Number5 се пишеха с
# НЕСЪЩЕСТВУВАЩИ идентификатори и MS Project ги ИЗХВЪРЛЯШЕ мълчаливо: в готовия
# файл колоните „Начало (ден)" и „Край (ден)" бяха празни и единственото, което
# се четеше, бяха датите — точно това, което изпълнителят не иска.
#
# Числата не са сметнати наизуст, а ПОПИТАНИ от самия MS Project:
#     app.FieldNameToFieldConstant("Number4", 0) → 188743770
FIELD_ID_NUMBER2 = "188743768"  # Number2 = Пикетаж от
FIELD_ID_NUMBER3 = "188743769"  # Number3 = Пикетаж до

# ДЕНЯТ, НЕ ДАТАТА (изпълнителят, 25.08.2026): „това е тръжна процедура,
# трябва да пише само дни — от ден 1 до ден 210".  MSPDI не може без дати —
# схемата ги изисква и MS Project смята по календар — затова датите остават
# като механика, а ДЕНЯТ идва в две собствени колони, които се показват до
# името и се четат като в човешкия график.
FIELD_ID_NUMBER4 = "188743770"  # Number4 = Начало (ден)
FIELD_ID_NUMBER5 = "188743771"  # Number5 = Край (ден)

#: Поле → (ключ в задачата, псевдоним в MS Project).  Държи дефиницията и
#: стойността на едно място, за да не се разминат както Text2 („Мярка" беше
#: дефинирано, но никога не се пишеше).
_SPATIAL_FIELDS: tuple[tuple[str, str, str], ...] = (
    (FIELD_ID_TEXT6, "spatial_segment_id", "Участък (ID)"),
    (FIELD_ID_TEXT7, "street", "Улица"),
    (FIELD_ID_TEXT8, "from_node", "От възел"),
    (FIELD_ID_TEXT9, "to_node", "До възел"),
    (FIELD_ID_NUMBER2, "start_chainage", "Пикетаж от"),
    (FIELD_ID_NUMBER3, "end_chainage", "Пикетаж до"),
    # Неизменният ключ на реда.  „Източник" казва къде да се отвори документът;
    # това казва кой запис е и оцелява разместване на редове (одит 10.08.2026).
    (FIELD_ID_TEXT10, "source_record_id", "Запис (ID)"),
    (FIELD_ID_NUMBER4, "start_day", "Начало (ден)"),
    (FIELD_ID_NUMBER5, "end_day", "Край (ден)"),
)

#: MSPDI иска и името на полето, не само ID-то.
_FIELD_NAMES = {
    FIELD_ID_TEXT6: "Text6",
    FIELD_ID_TEXT7: "Text7",
    FIELD_ID_TEXT8: "Text8",
    FIELD_ID_TEXT9: "Text9",
    FIELD_ID_TEXT10: "Text10",
    FIELD_ID_NUMBER2: "Number2",
    FIELD_ID_NUMBER3: "Number3",
    FIELD_ID_NUMBER4: "Number4",
    FIELD_ID_NUMBER5: "Number5",
}


# ---------------------------------------------------------------------------
# Main export function
# ---------------------------------------------------------------------------


def export_to_mspdi_xml(
    schedule_data: list[dict],
    project_name: str,
    start_date: str = "2026-06-01",
    calendar_type: str = "7-day",
    filename: str | None = None,
    constraint_mode: str = "milestones",
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
        root = _build_xml(schedule_data, project_name, start_date, calendar_type,
                          constraint_mode)
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
    constraint_mode: str = "milestones",
) -> ET.Element:
    """Build the full MSPDI XML tree."""
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")

    # Calculate project end date — със СЪЩАТА формула като задачите (одит 2026-08):
    #   end_day = start_day + ceil(duration) - 1  (или явен end_day, закръглен).
    # Преди: default беше start_day+duration (без -1) → header с 1 ден повече;
    # плюс FinishDate ползваше ОБИКНОВЕН timedelta (календарни), докато задачите
    # ползват РАБОТНИЯ календар → при 5-дневен календар header-ът свършваше
    # ПРЕДИ последната задача.  Сега и двете съвпадат.
    def _safe_int(v: object, default: int) -> int:
        """Цяло число или default за None/bool/нечислово/НЕ-крайно (NaN/Inf)."""
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return default
        if not math.isfinite(v):
            return default
        return int(math.ceil(v))

    def _end_day(t: dict) -> int:
        ed = t.get("end_day")
        if isinstance(ed, (int, float)) and not isinstance(ed, bool) and math.isfinite(ed):
            return int(math.ceil(ed))
        sd = _safe_int(t.get("start_day", 1), 1)
        return sd + max(1, _safe_int(t.get("duration", 0), 0)) - 1

    # FinishDate по FLATTENED задачи (одит 2026-08 v30): header-ът игнорираше
    # вложените sub_activities, които могат да свършват по-късно от родителя.
    _flat_for_end = _flatten_schedule(schedule_data)
    max_end_day = max((_end_day(t) for t in _flat_for_end), default=1)
    finish_dt = _working_day_to_date(start_dt, max_end_day, calendar_type)

    # Root element
    root = ET.Element("Project")
    root.set("xmlns", NAMESPACE)

    # --- 1. Project properties ---
    ET.SubElement(root, "SaveVersion").text = "14"
    ET.SubElement(root, "Name").text = project_name
    ET.SubElement(root, "StartDate").text = f"{start_date}T08:00:00"
    ET.SubElement(root, "FinishDate").text = finish_dt.strftime("%Y-%m-%dT17:00:00")
    # ГРАФИКЪТ СЕ БРОИ НАПРЕД ОТ ДЕН 1 (25.08.2026).  Без този ред MS Project
    # разписва проекта НАЗАД от датата в заглавието: мерено на Тръстеник, 888
    # задачи лягаха ПРЕДИ началото („наш ден 2, MS Project −2"), а вносът им
    # слагаше „Finish No Later Than" на всяка.  Изнесеният график изглеждаше
    # като чужд график.
    ET.SubElement(root, "ScheduleFromStart").text = "1"
    ET.SubElement(root, "CalendarUID").text = "1"
    # ДНИ, НЕ ЧАСОВЕ (изпълнителят, 25.08.2026: „както и часове, закръгли ги
    # да пише дни").  В MSPDI 5 е ЧАСОВЕ, а 7 е ДНИ — дотук стоеше 5 с
    # коментар „CRITICAL: 5=days" и MS Project показваше „48 hrs" вместо
    # „6 дни".  Числото е проверено в собствения експорт на MS Project:
    # там всяка задача носи <DurationFormat>7</DurationFormat>.
    ET.SubElement(root, "DurationFormat").text = DURATION_FORMAT_DAYS
    ET.SubElement(root, "DefaultStartTime").text = "08:00:00"
    ET.SubElement(root, "DefaultFinishTime").text = "17:00:00"
    ET.SubElement(root, "MinutesPerDay").text = str(MINUTES_PER_DAY)

    if calendar_type == "7-day":
        ET.SubElement(root, "MinutesPerWeek").text = str(7 * MINUTES_PER_DAY)
    else:
        ET.SubElement(root, "MinutesPerWeek").text = str(5 * MINUTES_PER_DAY)

    ET.SubElement(root, "DaysPerMonth").text = "30"

    # EU AI Act чл. 50(2) — маркиране на генерираното съдържание.
    # `Title`/`Subject`/`Comments` са стандартни MSPDI полета и оцеляват
    # при Save As .mpp, т.е. маркерът пътува с файла до възложителя.
    ET.SubElement(root, "Author").text = SYSTEM_NAME
    ET.SubElement(root, "Subject").text = CONTENT_DISCLOSURE_BG
    ET.SubElement(root, "Comments").text = CONTENT_NOTICE_LONG_BG
    ET.SubElement(root, "Category").text = "ai-generated"

    # --- 2. Extended attributes (custom fields) ---
    _build_extended_attributes(root)

    # --- 3. Calendar ---
    _build_calendar(root, calendar_type)

    # --- 4. Tasks ---
    flat_tasks = _flatten_schedule(schedule_data)
    uid_map = _build_tasks(root, flat_tasks, start_dt, project_name,
                           calendar_type, constraint_mode)

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

    # Text4 = вътрешно ID (за round-trip идентичност)
    attr5 = ET.SubElement(ext_attrs, "ExtendedAttribute")
    ET.SubElement(attr5, "FieldID").text = FIELD_ID_TEXT4
    ET.SubElement(attr5, "FieldName").text = "Text4"
    ET.SubElement(attr5, "Alias").text = "ID"

    # Text3 = Екип
    attr4 = ET.SubElement(ext_attrs, "ExtendedAttribute")
    ET.SubElement(attr4, "FieldID").text = FIELD_ID_TEXT3
    ET.SubElement(attr4, "FieldName").text = "Text3"
    ET.SubElement(attr4, "Alias").text = "\u0415\u043a\u0438\u043f"  # Екип


    _add_source_field_definition(ext_attrs)


def _add_source_field_definition(ext_attrs: ET.Element) -> None:
    """Text5 = Източник (КСС) — цитатът лист+ред, проследим отвън.

    Одит 07.08.2026: „конкретна MS Project задача още не може надеждно да се
    проследи обратно до точния ред в анонимизирания КСС."  Цитатът
    (`source_ref`, вида `КСС.xlsx!лист!ред`) съществуваше в задачата и гейтът
    го проверяваше, но не влизаше в експорта — проверим отвътре, невидим отвън.
    """
    attr = ET.SubElement(ext_attrs, "ExtendedAttribute")
    ET.SubElement(attr, "FieldID").text = FIELD_ID_TEXT5
    ET.SubElement(attr, "FieldName").text = "Text5"
    ET.SubElement(attr, "Alias").text = "Източник (КСС)"

    for field_id, _, alias in _SPATIAL_FIELDS:
        spatial = ET.SubElement(ext_attrs, "ExtendedAttribute")
        ET.SubElement(spatial, "FieldID").text = field_id
        ET.SubElement(spatial, "FieldName").text = _FIELD_NAMES[field_id]
        ET.SubElement(spatial, "Alias").text = alias


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
    calendar_type: str = "7-day",
    constraint_mode: str = "milestones",
) -> dict[str, int]:
    """Build the Tasks section. Returns a map of task_id → UID."""
    tasks_elem = ET.SubElement(root, "Tasks")

    # Root task (UID=0, REQUIRED by MS Project)
    root_task = ET.SubElement(tasks_elem, "Task")
    ET.SubElement(root_task, "UID").text = "0"
    ET.SubElement(root_task, "ID").text = "0"
    ET.SubElement(root_task, "Name").text = project_name
    ET.SubElement(root_task, "OutlineLevel").text = "0"
    # ФАЙЛЪТ ОПИСВА САМ СЕБЕ СИ.  Коренът стоеше без начало, край и с
    # Duration = PT0H — тоест външен четец не можеше да каже колко трае
    # графикът, без да пресмята задачите му.  Човешкият еталон носи
    # Start/Finish и Duration = PT6240H (780 × 8 ч) точно тук, и именно това
    # позволява сравнение по календар.
    #
    # НЕЗАВИСИМ ОДИТ 17.08.2026: сравнението „650 срещу 558" беше сгрешено
    # частично и защото нашият файл не обявява собствената си продължителност,
    # та тя се извеждаше отстрани и по чужда календарна основа.
    def _цяло(стойност, по_подразбиране: int) -> int:
        try:
            return int(float(стойност))
        except (TypeError, ValueError):
            return по_подразбиране

    _край = max(
        (_цяло(t.get("end_day"), 0)
         or _цяло(t.get("start_day"), 1) + max(1, _цяло(t.get("duration"), 0)) - 1
         for t in flat_tasks), default=1)
    _край = max(int(_край), 1)
    ET.SubElement(root_task, "Start").text = start_dt.strftime("%Y-%m-%dT08:00:00")
    ET.SubElement(root_task, "Finish").text = _working_day_to_date(
        start_dt, _край, calendar_type).strftime("%Y-%m-%dT17:00:00")
    ET.SubElement(root_task, "Duration").text = f"PT{_край * 8}H0M0S"
    ET.SubElement(root_task, "DurationFormat").text = DURATION_FORMAT_DAYS
    ET.SubElement(root_task, "Manual").text = "0"
    ET.SubElement(root_task, "Summary").text = "1"
    ET.SubElement(root_task, "CalendarUID").text = "1"

    # ------------------------------------------------------------------
    # ПРОХОД 1: раздай UID на ВСИЧКИ задачи, преди да се строят връзки.
    #
    # Одит 2026-07-23: досега uid_map се пълнеше в същия цикъл, в който се
    # добавяха predecessor връзките.  Ако задача стоеше във файла ПРЕДИ своя
    # предшественик, той още нямаше UID и `_add_predecessor_links` го
    # пропускаше с `continue` — БЕЗШУМНО.  Резултатът е MS Project файл,
    # който се отваря нормално, но е без никаква логика: всяка задача се
    # движи независимо.  Възпроизведено с нула predecessor links.
    #
    # Редът на задачите идва от AI и не е гарантирано топологичен.
    # ------------------------------------------------------------------
    uid_map: dict[str, int] = {}
    uid_counter = 1
    for task in flat_tasks:
        task_id = str(task.get("id", "")).strip()
        if task_id and task_id not in uid_map:
            uid_map[task_id] = uid_counter
        uid_counter += 1

    # Order-INDEPENDENT WBS йерархия (одит v28): task_id → (level, "1.2.3").
    # Смята се ПРЕДИ прохода, за да е коректна дори при child-преди-parent.
    hierarchy = _compute_hierarchy(flat_tasks)

    # Кой е РОДИТЕЛ — по `parent_id`, не само по вложени `sub_activities`.
    #
    # 2026-08-06: WBS слоят (work_package.py) строи ПЛОСКА йерархия през
    # `parent_id`, защото така е и в еталонния график.  `_flatten_schedule`
    # обаче слага `_has_children` само за вложените под-дейности, затова
    # обобщаващите излизаха със Summary=0 и — понеже са с нулева
    # продължителност — MS Project ги показваше като MILESTONE-и вместо като
    # обобщаващи ленти.  Проверено с обратен парсър на изхода: summary=0.
    parent_ids = {
        str(t.get("parent_id")).strip() for t in flat_tasks
        if str(t.get("parent_id") or "").strip()
    }

    # ROLL-UP на обобщаващите (одит 2026-08-07): обобщаващата има нулева
    # продължителност, затова досега излизаше със Start = Finish = ден 1.  В
    # одитирания файл всичките 26 обобщаващи бяха с грешни дати — „СТРОИТЕЛСТВО"
    # приключваше на 01.06, докато негово дете течеше до 17.10.
    #
    # Обобщаващата не е работа, а СБОР: започва с първото си дете и свършва с
    # последното.  Смята се РЕКУРСИВНО, за да е вярна и на средното ниво.
    rollup = _compute_rollup(flat_tasks, parent_ids)

    # ------------------------------------------------------------------
    # ПРОХОД 2: изгради задачите и връзките с вече пълна карта на UID.
    # ------------------------------------------------------------------
    uid_counter = 1
    for task in flat_tasks:
        task_id = str(task.get("id", "")).strip()

        task_elem = ET.SubElement(tasks_elem, "Task")
        ET.SubElement(task_elem, "UID").text = str(uid_counter)
        ET.SubElement(task_elem, "ID").text = str(uid_counter)
        ET.SubElement(task_elem, "Name").text = task.get("name", "")

        # OutlineLevel + OutlineNumber от order-independent hierarchy pass
        # (одит v28): нивото се извежда от дълбочината на parent-веригата
        # (дете = 2, не 1), а номерът е коректен независимо от реда на задачите.
        outline, outline_num = hierarchy.get(task_id, (1, str(uid_counter)))
        ET.SubElement(task_elem, "OutlineLevel").text = str(outline)
        ET.SubElement(task_elem, "OutlineNumber").text = outline_num

        # Dates + продължителност — ДОГОВОР: ЦЕЛИ работни дни (одит v28, т.1).
        # Дробна дурация (1.5д) даваше Duration=12ч, но Start 08:00 / Finish
        # 17:00 в ЕДИН ден = 8ч → вътрешно противоречие и round-trip 1.5→2.
        # Затова закръгляме НАГОРЕ до цял ден и извеждаме end_day от него, за да
        # съвпаднат Start/Finish/Duration.  `_duration_days` (по-долу) е чист
        # инвариант, който валидаторът също проверява.
        start_day = int(task.get("start_day", 1) or 1)
        raw_duration = task.get("duration", 0) or 0
        # Обобщаваща задача с нулева продължителност НЕ е milestone — иначе
        # цялата WBS структура влиза в MS Project като поредица от ромбчета.
        is_summary = bool(
            task.get("_has_children") or task.get("is_summary")
            or task_id in parent_ids
        )
        is_milestone = not is_summary and (
            raw_duration == 0
            or bool(task.get("milestone") or task.get("is_milestone")))

        if is_summary and task_id in rollup:
            # Сборът от децата, не собствената нулева продължителност.
            start_day, end_day = rollup[task_id]
            dur_days = max(1, end_day - start_day + 1)
        else:
            dur_days = 0 if is_milestone else max(1, math.ceil(raw_duration))
            end_day = start_day if is_milestone else start_day + dur_days - 1

        # Одит 2026-07-23: индексите start_day/end_day са РАБОТНИ дни —
        # превръщането прескача почивните, иначе MS Project мести задачите.
        task_start = _working_day_to_date(start_dt, start_day, calendar_type)
        task_finish = _working_day_to_date(start_dt, end_day, calendar_type)

        start_str = task_start.strftime("%Y-%m-%dT08:00:00")
        # Milestone е ТОЧКА → Finish == Start (одит т.6).
        finish_str = start_str if is_milestone else task_finish.strftime("%Y-%m-%dT17:00:00")
        ET.SubElement(task_elem, "Start").text = start_str
        ET.SubElement(task_elem, "Finish").text = finish_str

        # Duration = цели дни × 8 часа (цяло число → 'PT16H0M0S', не 'PT12.0H').
        hours = dur_days * 8
        ET.SubElement(task_elem, "Duration").text = f"PT{hours}H0M0S"
        ET.SubElement(task_elem, "DurationFormat").text = DURATION_FORMAT_DAYS

        # Auto-scheduled (Manual=0) — no pin icons in Task Mode column.
        #
        # ConstraintType=2 (Must Start On) закова НАЧАЛОТО на всяка задача.
        # Това пази датите точно както ги е сметнал детерминистичният двигател
        # и ги държи в синхрон с PDF-а, който получава същият възложител —
        # но прави зависимостите декоративни: MS Project не може да пренареди
        # графика при промяна, а рапортува конфликти (одит 2026-07-23).
        #
        # Затова режимът е избираем.  'pinned' пази досегашното поведение
        # (урок #19); 'flexible' оставя MS Project да планира по зависимости.
        ET.SubElement(task_elem, "Manual").text = "0"
        # ФИКСИРАНА ПРОДЪЛЖИТЕЛНОСТ (25.08.2026).  При типа по подразбиране
        # („фиксирани ресурси") MS Project смята продължителността от обема
        # работа — а нашите задачи нямат такъв — и я връща на НУЛА.  Мерено:
        # 328 задачи излизаха „0 days" в готовия файл.  Графикът на
        # изпълнителя носи същия тип на всяка задача.
        ET.SubElement(task_elem, "Type").text = "1"
        # И НЕ Е ЗАВИСИМА ОТ ОБЕМА РАБОТА.  С три назначени ресурса MS Project
        # смята Work = 3 × 8 ч и връща продължителността като 3 ДНИ вместо
        # един — мерено: 120 застъпени дейности в готовия .mpp при нула в
        # самия график.  Продължителността ни идва от дните, не от обема.
        ET.SubElement(task_elem, "EffortDriven").text = "0"
        _apply_constraint(task_elem, task, constraint_mode, start_str, finish_str)

        # Calendar
        ET.SubElement(task_elem, "CalendarUID").text = "1"

        # Summary flag — по `parent_id` ИЛИ по вложени под-дейности (виж горе).
        ET.SubElement(task_elem, "Summary").text = "1" if is_summary else "0"

        # Milestone (0 duration)
        ET.SubElement(task_elem, "Milestone").text = "1" if is_milestone else "0"

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


# MSPDI ConstraintType — фиксирани от схемата:
#   0=ASAP, 1=ALAP, 2=Must Start On, 3=Must Finish On,
#   4=Start No Earlier Than, 5=Start No Later Than,
#   6=Finish No Earlier Than, 7=Finish No Later Than
CONSTRAINT_ASAP = "0"
CONSTRAINT_MUST_START_ON = "2"
CONSTRAINT_FINISH_NO_LATER = "7"

CONSTRAINT_MODES = ("milestones", "pinned", "flexible")


def _apply_constraint(
    task_elem: ET.Element,
    task: dict,
    mode: str,
    start_str: str,
    finish_str: str,
) -> None:
    """Наложи политиката за constraints върху една задача.

    СЪПОСТАВКА С ЕТАЛОН (2026-08-06): всичките 204 задачи в програмния график
    излизаха с `ConstraintType=2` (Must Start On), докато в човешкия еталон
    НИТО ЕДНА от 610 няма constraint.  Следствието е, че MS Project не
    планира нищо — получава готови дати и ги показва.  Зависимостите стават
    декоративни: промени една продължителност и графикът не се пренарежда, а
    рапортува конфликти.

    Режими:
      `milestones` (по подразбиране) — работните задачи са auto-scheduled
          (ASAP) и датите идват от зависимостите; САМО договорните milestone-и
          получават краен срок.  За тях се пише `Deadline` плюс FNLT, защото
          Deadline дава на MS Project отрицателен резерв при закъснение, без
          да зазижда мрежата.
      `pinned` — досегашното поведение (урок #19): всяко начало заковано.
          Пази датите точно както ги е сметнал детерминистичният двигател.
      `flexible` — всичко ASAP, без нито един constraint.

    Договорен milestone = задача с `contractual` (или `is_contractual`).
    """
    if mode == "pinned":
        ET.SubElement(task_elem, "ConstraintType").text = CONSTRAINT_MUST_START_ON
        ET.SubElement(task_elem, "ConstraintDate").text = start_str
        return

    ET.SubElement(task_elem, "ConstraintType").text = CONSTRAINT_ASAP

    if mode != "milestones":
        return

    is_milestone = bool(task.get("milestone") or task.get("is_milestone")) or \
        (task.get("duration", 0) == 0)
    contractual = bool(task.get("contractual") or task.get("is_contractual"))
    if not (is_milestone and contractual):
        return

    # Договорната дата може да е явна; иначе е там, където графикът я е сложил.
    deadline = str(task.get("contract_date") or "").strip()
    deadline_str = f"{deadline}T17:00:00" if len(deadline) == 10 else finish_str

    # Deadline (не constraint) — маркер + отрицателен резерв при закъснение.
    ET.SubElement(task_elem, "Deadline").text = deadline_str
    # FNLT е „не по-късно от", тоест не мести задачата напред, само назад.
    task_elem.find("ConstraintType").text = CONSTRAINT_FINISH_NO_LATER
    ET.SubElement(task_elem, "ConstraintDate").text = deadline_str


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

    # Text2 = Мярка (unit) — полето беше ДЕФИНИРАНО, но никога не се пишеше
    unit = task.get("unit")
    if unit:
        ea = ET.SubElement(task_elem, "ExtendedAttribute")
        ET.SubElement(ea, "FieldID").text = FIELD_ID_TEXT2
        ET.SubElement(ea, "Value").text = str(unit)

    # Text4 = вътрешното ID — без него round-trip не може да свърже задачите
    task_id = task.get("id")
    if task_id:
        ea = ET.SubElement(task_elem, "ExtendedAttribute")
        ET.SubElement(ea, "FieldID").text = FIELD_ID_TEXT4
        ET.SubElement(ea, "Value").text = str(task_id)

    # Text5 = Източник (КСС) — `КСС.xlsx!лист!ред`.  Само производствените
    # задачи цитират ред; обобщаващите и milestone-ите нямат какво да сочат.
    source_ref = str(task.get("source_ref") or "").strip()
    if source_ref:
        ea = ET.SubElement(task_elem, "ExtendedAttribute")
        ET.SubElement(ea, "FieldID").text = FIELD_ID_TEXT5
        ET.SubElement(ea, "Value").text = source_ref

    # Text6-9, Number2-3 = кой участък е това физически.  Празно поле не се
    # пише: „улица: —" изглежда като отговор, а е липса на отговор.
    for field_id, key, _ in _SPATIAL_FIELDS:
        value = task.get(key)
        if value is None or not str(value).strip():
            continue
        ea = ET.SubElement(task_elem, "ExtendedAttribute")
        ET.SubElement(ea, "FieldID").text = field_id
        ET.SubElement(ea, "Value").text = str(value)

    # Text3 = Екип (team)
    team = task.get("team")
    if team and team != "\u2014":
        ea = ET.SubElement(task_elem, "ExtendedAttribute")
        ET.SubElement(ea, "FieldID").text = FIELD_ID_TEXT3
        ET.SubElement(ea, "Value").text = team


# MSPDI PredecessorLink/Type — стойностите са фиксирани от схемата:
#   0 = Finish-to-Finish, 1 = Finish-to-Start,
#   2 = Start-to-Start,   3 = Start-to-Finish
#
# Одит 2026-07-23 (round-trip): SS и SF бяха РАЗМЕНЕНИ — SS се записваше
# като 3 (SF) и обратно.  Тоест всяка SS връзка (урок #15: изкоп SS+1d
# полагане — най-често срещаната след FS) влизаше в MS Project като
# Start-to-Finish, което е безсмислено технологично, и Project пренареждаше
# графика по нея.  Хванато чак когато XML-ът беше прочетен обратно.
_DEPENDENCY_TYPE_MAP = {
    "FF": "0",
    "FS": "1",  # default
    "SS": "2",
    "SF": "3",
}


def _add_predecessor_links(
    task_elem: ET.Element,
    task: dict,
    uid_map: dict[str, int],
) -> None:
    """Add dependency links to a task element.

    Reads dependency_type and lag_days from task dict (set by enrich_for_msproject).
    LinkLag is in tenths of minutes: 1 day = MINUTES_PER_DAY × LINK_LAG_FACTOR = 4800.
    """
    deps = task.get("dependencies", [])
    if not deps:
        return

    for dep in deps:
        # Support both formats:
        # Old: ["D01", "D02"]  (array of strings)
        # New: [{"predecessor_id": "D01", "type": "SS", "lag": 20}]  (array of dicts)
        if isinstance(dep, dict):
            dep_id = str(dep.get("predecessor_id") or dep.get("id", "")).strip()
            dep_type_str = (dep.get("type") or "FS").upper()
            lag_days = int(dep.get("lag") or dep.get("lag_days") or 0)
        else:
            # Анотиран низ „D01 (SS+20)" → ID + тип/лаг (иначе тези на задачата).
            from src.schedule_builder import parse_dependency_token
            base, a_type, a_lag = parse_dependency_token(dep)
            dep_id = base
            dep_type_str = (a_type or task.get("dependency_type") or "FS").upper()
            lag_days = a_lag if a_type else int(task.get("lag_days") or 0)

        type_code = _DEPENDENCY_TYPE_MAP.get(dep_type_str, "1")
        link_lag = str(lag_days * MINUTES_PER_DAY * LINK_LAG_FACTOR)

        dep_uid = uid_map.get(dep_id)
        if dep_uid is None:
            # След two-pass това вече значи РЕАЛНО липсващ предшественик, а
            # не въпрос на ред във файла.  Затова е warning, не debug:
            # тихо изпусната зависимост е тихо изгубена логика на графика.
            logger.warning(
                "Зависимост ИЗПУСНАТА в XML: задача %s сочи към %r, което "
                "не съществува в графика.", task.get("id"), dep_id,
            )
            continue

        pred = ET.SubElement(task_elem, "PredecessorLink")
        ET.SubElement(pred, "PredecessorUID").text = str(dep_uid)
        ET.SubElement(pred, "Type").text = type_code
        # Както го пише самият MS Project (проверено в негов експорт): между
        # типа и лага стои CrossProject, а форматът е 7 = ДНИ.  Тук стоеше 5 с
        # коментар „days" — 5 е ЧАСОВЕ и лагът излизаше „80 hrs" вместо
        # „10 дни".
        ET.SubElement(pred, "CrossProject").text = "0"
        ET.SubElement(pred, "LinkLag").text = link_lag
        ET.SubElement(pred, "LagFormat").text = DURATION_FORMAT_DAYS


def _build_resources(
    root: ET.Element, flat_tasks: list[dict]
) -> dict[str, int]:
    """Build the Resources section. Returns resource_name → resource_uid map.

    СЪПОСТАВКА С ЕТАЛОН (2026-08-06): човешкият график има 82 ресурса и 4677
    назначения — реален състав на бригадата (технически ръководител, геодезист,
    багер, тръбополагачи, трамбовка, камиони).  Нашият имаше 57 ресурса и 172
    назначения: по ЕДИН низ „Екип К1" на задача.  С такъв модел не може да се
    отговори на въпроса, който възложителят задава — дали двата фронта изобщо
    са обезпечени с хора и механизация.

    Затова тук се четат и `resources` (съставът от технологичния шаблон), и
    `team` (етикетът на фронта).  Задача без състав се държи както досега.
    """
    resources_elem = ET.SubElement(root, "Resources")

    # Empty resource (UID=0, REQUIRED by MS Project)
    empty_res = ET.SubElement(resources_elem, "Resource")
    ET.SubElement(empty_res, "UID").text = "0"
    ET.SubElement(empty_res, "ID").text = "0"
    ET.SubElement(empty_res, "Name").text = ""

    resource_map: dict[str, int] = {}
    res_uid = 1

    for task in flat_tasks:
        for name in _resource_names(task):
            if name in resource_map:
                continue
            resource_map[name] = res_uid

            res = ET.SubElement(resources_elem, "Resource")
            ET.SubElement(res, "UID").text = str(res_uid)
            ET.SubElement(res, "ID").text = str(res_uid)
            ET.SubElement(res, "Name").text = name
            ET.SubElement(res, "Type").text = "1"  # Work resource
            # MaxUnits е НАЛИЧНОСТТА (1.0 = един брой).  Без нея MS Project
            # приема неограничен ресурс и не показва претоварване — точно
            # затова одитът намери един багер на 16 едновременни задачи.
            # 100% в MSPDI се пише като 1.0 на единица.
            ET.SubElement(res, "MaxUnits").text = f"{_resource_capacity(name):.1f}"

            res_uid += 1

    return resource_map


def _resource_capacity(name: str) -> float:
    """Наличност на ресурса — същата таблица, по която се прави изравняването.

    Ако XML-ът обявява друга наличност от тази, с която е сметнат графикът,
    MS Project ще покаже претоварване там, където ние сме изравнили.
    """
    from src.schedule_builder import _load_resource_capacity

    config = _load_resource_capacity()
    table = config.get("capacity") or {}
    return float(table.get(name, config.get("default", 1)))


def _resource_names(task: dict) -> list[str]:
    """\u0420\u0435\u0441\u0443\u0440\u0441\u0438\u0442\u0435 \u043d\u0430 \u0435\u0434\u043d\u0430 \u0437\u0430\u0434\u0430\u0447\u0430: \u0441\u044a\u0441\u0442\u0430\u0432\u044a\u0442 \u043d\u0430 \u0431\u0440\u0438\u0433\u0430\u0434\u0430\u0442\u0430 \u043f\u043b\u044e\u0441 \u0435\u0442\u0438\u043a\u0435\u0442\u044a\u0442 \u043d\u0430 \u0444\u0440\u043e\u043d\u0442\u0430.

    \u0420\u0435\u0434\u044a\u0442 \u0435 \u0441\u0442\u0430\u0431\u0438\u043b\u0435\u043d (\u0441\u044a\u0441\u0442\u0430\u0432, \u043f\u043e\u0441\u043b\u0435 \u0435\u043a\u0438\u043f) \u0438 \u0431\u0435\u0437 \u043f\u043e\u0432\u0442\u043e\u0440\u0435\u043d\u0438\u044f, \u0437\u0430 \u0434\u0430 \u0441\u0430
    \u043f\u043e\u0432\u0442\u043e\u0440\u044f\u0435\u043c\u0438 \u0438 UID-\u0438\u0442\u0435, \u0438 \u043d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u044f\u0442\u0430.
    """
    names: list[str] = []
    for raw in task.get("resources") or []:
        name = str(raw).strip()
        if name and name != "\u2014" and name not in names:
            names.append(name)
    team = str(task.get("team") or "").strip()
    if team and team != "\u2014" and team not in names:
        names.append(team)
    return names


def _build_assignments(
    root: ET.Element,
    flat_tasks: list[dict],
    uid_map: dict[str, int],
    resource_map: dict[str, int],
) -> None:
    """Build the Assignments section (task↔resource links)."""
    assignments_elem = ET.SubElement(root, "Assignments")
    asn_uid = 1

    # Същото определение като в `_build_tasks`: родител е и този, посочен през
    # `parent_id`.  Иначе обобщаващите получават ресурс и часовете се броят
    # двойно — веднъж на родителя, веднъж на децата.
    parent_ids = {
        str(t.get("parent_id")).strip() for t in flat_tasks
        if str(t.get("parent_id") or "").strip()
    }

    for task in flat_tasks:
        task_id = str(task.get("id", "")).strip()
        is_summary = bool(task.get("_has_children") or task.get("is_summary")
                          or task_id in parent_ids)
        if is_summary:
            continue

        task_uid = uid_map.get(task_id)
        if task_uid is None:
            continue

        # \u041f\u043e \u0435\u0434\u043d\u043e \u043d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435 \u0437\u0430 \u0412\u0421\u0415\u041a\u0418 \u0440\u0435\u0441\u0443\u0440\u0441 \u043d\u0430 \u0437\u0430\u0434\u0430\u0447\u0430\u0442\u0430 (\u0441\u044a\u0441\u0442\u0430\u0432 + \u0435\u043a\u0438\u043f), \u043d\u0435
        # \u0435\u0434\u043d\u043e \u0437\u0430 \u0446\u0435\u043b\u0438\u044f \u201e\u0435\u043a\u0438\u043f" \u2014 \u0438\u043d\u0430\u0447\u0435 \u043e\u0431\u0435\u0437\u043f\u0435\u0447\u0435\u043d\u043e\u0441\u0442\u0442\u0430 \u043d\u0435 \u0441\u0435 \u0432\u0438\u0436\u0434\u0430 \u0432 MS Project.
        for name in _resource_names(task):
            res_uid = resource_map.get(name)
            if res_uid is None:
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


def _compute_rollup(
    flat_tasks: list[dict], parent_ids: set[str]
) -> dict[str, tuple[int, int]]:
    """За всяка обобщаваща задача: (начало на първото дете, край на последното).

    Смята се от ЛИСТАТА нагоре, за да е вярно и за средните нива: участък,
    чието дете е отложено от ресурсното изравняване, трябва да се разтегне, а
    заедно с него и фазата над него.
    """
    children: dict[str, list[str]] = defaultdict(list)
    by_id: dict[str, dict] = {}
    for task in flat_tasks:
        tid = _sid(task)
        if not tid:
            continue
        by_id[tid] = task
        parent = str(task.get("parent_id") or "").strip()
        if parent and parent != tid:
            children[parent].append(tid)

    def own_span(task: dict) -> tuple[int, int]:
        start = int(task.get("start_day", 1) or 1)
        duration = task.get("duration", 0) or 0
        if duration <= 0:
            return start, start
        return start, start + max(1, math.ceil(duration)) - 1

    result: dict[str, tuple[int, int]] = {}
    guard: set[str] = set()

    def span(tid: str) -> tuple[int, int]:
        if tid in result:
            return result[tid]
        if tid in guard:                      # счупена йерархия (цикъл)
            return own_span(by_id[tid])
        guard.add(tid)

        kids = children.get(tid, [])
        if not kids:
            value = own_span(by_id[tid])
        else:
            spans = [span(k) for k in kids]
            value = (min(s for s, _ in spans), max(e for _, e in spans))
        guard.discard(tid)
        result[tid] = value
        return value

    return {tid: span(tid) for tid in parent_ids if tid in by_id}


def _sid(task: dict) -> str:
    """ID на задачата като str (числовите ID на DeepSeek чупеха съвпаденията)."""
    return str(task.get("id", "")).strip()


def _compute_hierarchy(flat_tasks: list[dict]) -> dict[str, tuple[int, str]]:
    """Order-INDEPENDENT WBS йерархия: task_id → (OutlineLevel, OutlineNumber).

    Одит 2026-08 т.3 (частично) → т.2 (v28): предишният подход смяташе номера в
    същия проход, в който строеше задачите, тоест:
      * OutlineLevel на детето оставаше 1 (не 2) — `_get_outline_level` връщаше
        константа;
      * при child-ПРЕДИ-parent йерархията се губеше и номерата се дублираха.
    Тук строим дърво по `parent_id` (нормализиран към str), после DFS от
    корените дава ниво = дълбочина+1 и номер „1", „1.1", „1.2", независимо от
    реда на AI задачите.  Липсващ/самопрепращащ parent → коренно ниво.  Цикли и
    осиротели възли се поемат защитно (visited set + опашка от неспигнатите).
    """
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for t in flat_tasks:
        tid = _sid(t)
        if tid and tid not in by_id:
            by_id[tid] = t
            order.append(tid)

    def parent_of(tid: str) -> str | None:
        raw = by_id[tid].get("parent_id")
        pid = str(raw).strip() if raw is not None and str(raw).strip() else ""
        return pid if (pid and pid in by_id and pid != tid) else None

    children: dict[str | None, list[str]] = defaultdict(list)
    for tid in order:
        children[parent_of(tid)].append(tid)

    result: dict[str, tuple[int, str]] = {}
    seen: set[str] = set()

    def assign(tid_list: list[str], prefix: str, level: int) -> None:
        for i, tid in enumerate(tid_list, 1):
            if tid in seen:
                continue
            seen.add(tid)
            num = f"{prefix}.{i}" if prefix else str(i)
            result[tid] = (level, num)
            assign(children.get(tid, []), num, level + 1)

    assign(children.get(None, []), "", 1)

    # Осиротели (напр. в цикъл, недостижими от корен) → продължи номерирането
    # като корени, за да не останат без OutlineNumber.
    next_root = len(children.get(None, [])) + 1
    for tid in order:
        if tid not in result:
            result[tid] = (1, str(next_root))
            next_root += 1
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
