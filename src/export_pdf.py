"""PDF export for construction schedules (A3 landscape Gantt).

Generates professional A3 landscape PDF with:
- Left table: task number, name, DN, length, team, days
- Right area: Gantt bars with month grid, color-coded by type
- Critical path highlighting, phase separators, milestones
- Multi-page support with repeating headers
- Full Cyrillic support via DejaVu Sans
"""

from __future__ import annotations

import io
import logging
import math
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.colors import HexColor

from src.ai_disclosure import (
    CONTENT_DISCLOSURE_BG,
    SYSTEM_NAME,
    pdf_metadata_keywords,
)
from src.constants import COLOR_PALETTE as _COLOR_PALETTE, TYPE_LABELS  # noqa: F401
from reportlab.lib.pagesizes import A3, landscape
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Page constants
# ---------------------------------------------------------------------------
PAGE_SIZE = landscape(A3)
PAGE_W = PAGE_SIZE[0]  # ~1190 pt (420mm)
PAGE_H = PAGE_SIZE[1]  # ~842 pt (297mm)

# Margins
TOP_MARGIN = 25 * mm
BOTTOM_MARGIN = 15 * mm
# Таблицата свършва тук, Gantt-ът започва вдясно.  Стойността се ИЗВЕЖДА от
# ширината на таблицата (виж по-долу), а не се пише на ръка: когато колоните
# станаха седем, фиксираните 115 mm изкараха първата колона извън листа и
# всяко име губеше първата си буква.
LEFT_MARGIN = 0.0  # изчислява се след ширините на колоните
RIGHT_MARGIN = 10 * mm

# Table column widths
#
# ШАБЛОНЪТ Е ЧОВЕШКИЯТ ГРАФИК НА ИЛИЯНЦИ (изпълнителят, 25.08.2026: „използвай
# като темплейт графика за Илиянци, наименувай колоните по същия начин").
# Прочетено от самия файл (`1.2.А.1.-линеен график Илиянци.pdf`, заглавен ред):
#
#   ID · Вид дейност / Участък · ед.мярка · диаметър · к-во · Срок ·
#   Последователност · Начало (ден) · Край (ден) · ЕКИП · Ресурси
#
# и чак СЛЕД тях започва скалата на дните.  Стойностите там са „46 d", „159 d",
# „6SS+10 d" — дни, не дати.  В процедура датите нямат място: стартът е
# неизвестен, докато не се подпише договорът и не се състави Протокол 2а.
COL_NUM_W = 7 * mm          # ID
COL_NAME_W = 56 * mm        # Вид дейност / Участък
COL_UNIT_W = 9 * mm         # ед.мярка
COL_DN_W = 10 * mm          # диаметър
COL_QTY_W = 13 * mm         # к-во
COL_DAYS_W = 9 * mm         # Срок
COL_PRED_W = 13 * mm        # Последователност
COL_START_W = 11 * mm       # Начало (ден)
COL_END_W = 11 * mm         # Край (ден)
COL_CREW_W = 9 * mm         # ЕКИП
COL_RES_W = 34 * mm         # Ресурси
TABLE_W = (COL_NUM_W + COL_NAME_W + COL_UNIT_W + COL_DN_W + COL_QTY_W
           + COL_DAYS_W + COL_PRED_W + COL_START_W + COL_END_W
           + COL_CREW_W + COL_RES_W)
LEFT_MARGIN = 6 * mm + TABLE_W

#: Скалата почва ПРЕДИ ден 1 — „за да се вижда по-ясно кога започва всичко"
#: (изпълнителят, 25.08.2026).  Еталонът също оставя ден пред началото.
ОСТА_ЗАПОЧВА = -2

# Row heights
ROW_H = 3.8 * mm
BAR_H = 2.8 * mm
HEADER_H = 8 * mm
PHASE_ROW_H = 5 * mm

# Font config
FONT_NAME = "DejaVuSans"
FONT_NAME_BOLD = "DejaVuSans-Bold"
FONT_SIZE = 5.0
FONT_SIZE_SMALL = 4.2
FONT_SIZE_HEADER = 7.0
FONT_SIZE_TITLE = 12.0
FONT_SIZE_SUBTITLE = 8.0

# Gantt area
GANTT_LEFT = LEFT_MARGIN + 4 * mm
GANTT_RIGHT = PAGE_W - RIGHT_MARGIN

# ---------------------------------------------------------------------------
# Color map (matches gantt_chart.py)
# ---------------------------------------------------------------------------
COLOR_MAP = {k: HexColor(v) for k, v in _COLOR_PALETTE.items()}
CRITICAL_COLOR = HexColor("#FF0000")

# ---------------------------------------------------------------------------
# Font registration
# ---------------------------------------------------------------------------
_font_registered = False


def _register_fonts() -> bool:
    """Register DejaVu Sans fonts for Cyrillic support.

    Searches: project fonts/ dir, system paths, falls back to Helvetica.
    Returns True if Cyrillic-capable font was registered.
    """
    global _font_registered
    if _font_registered:
        return True

    # Search paths for DejaVuSans.ttf
    search_paths = []

    # 1. Project fonts/ directory
    project_fonts = Path(__file__).parent.parent / "fonts"
    search_paths.append(project_fonts / "DejaVuSans.ttf")

    # 2. Windows system fonts
    search_paths.append(Path("C:/Windows/Fonts/DejaVuSans.ttf"))
    search_paths.append(Path("C:/Windows/Fonts/dejavusans.ttf"))

    # 3. User fonts (Windows)
    user_fonts = Path.home() / "AppData/Local/Microsoft/Windows/Fonts"
    search_paths.append(user_fonts / "DejaVuSans.ttf")

    # 4. Linux paths
    search_paths.append(Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
    search_paths.append(Path("/usr/share/fonts/TTF/DejaVuSans.ttf"))

    regular_path = None
    bold_path = None

    for p in search_paths:
        if p.exists():
            regular_path = p
            # Look for bold in same directory
            bold_candidate = p.parent / "DejaVuSans-Bold.ttf"
            if bold_candidate.exists():
                bold_path = bold_candidate
            break

    if regular_path:
        try:
            pdfmetrics.registerFont(TTFont(FONT_NAME, str(regular_path)))
            if bold_path:
                pdfmetrics.registerFont(TTFont(FONT_NAME_BOLD, str(bold_path)))
            else:
                # Use regular as bold fallback
                pdfmetrics.registerFont(TTFont(FONT_NAME_BOLD, str(regular_path)))
            _font_registered = True
            logger.info("Registered DejaVu Sans from %s", regular_path)
            return True
        except Exception as exc:
            logger.warning("Failed to register DejaVu Sans: %s", exc)

    # Try to download fonts
    if _download_dejavu_fonts(project_fonts):
        try:
            pdfmetrics.registerFont(
                TTFont(FONT_NAME, str(project_fonts / "DejaVuSans.ttf"))
            )
            pdfmetrics.registerFont(
                TTFont(FONT_NAME_BOLD, str(project_fonts / "DejaVuSans-Bold.ttf"))
            )
            _font_registered = True
            logger.info("Downloaded and registered DejaVu Sans")
            return True
        except Exception as exc:
            logger.warning("Failed after download: %s", exc)

    logger.warning(
        "DejaVu Sans not found. Cyrillic characters will not render correctly."
    )
    return False


def _download_dejavu_fonts(target_dir: Path) -> bool:
    """Download DejaVu Sans fonts from GitHub releases."""
    import urllib.request
    import zipfile

    url = (
        "https://github.com/dejavu-fonts/dejavu-fonts/releases/download/"
        "version_2_37/dejavu-fonts-ttf-2.37.zip"
    )

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Downloading DejaVu Sans fonts...")

        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()

        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                basename = Path(name).name
                if basename in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"):
                    with zf.open(name) as src:
                        (target_dir / basename).write_bytes(src.read())

        regular = target_dir / "DejaVuSans.ttf"
        bold = target_dir / "DejaVuSans-Bold.ttf"
        if regular.exists() and bold.exists():
            logger.info("DejaVu Sans fonts downloaded to %s", target_dir)
            return True
    except Exception as exc:
        logger.warning("Failed to download DejaVu fonts: %s", exc)

    return False


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


#: Заглавията и ширините — един източник за реда и за шапката.
_КОЛОНИ = (
    ("ID", COL_NUM_W),
    ("Вид дейност / Участък", COL_NAME_W),
    ("ед.мярка", COL_UNIT_W),
    ("диаметър", COL_DN_W),
    ("к-во", COL_QTY_W),
    ("Срок", COL_DAYS_W),
    ("Послед.", COL_PRED_W),
    ("Начало (ден)", COL_START_W),
    ("Край (ден)", COL_END_W),
    ("ЕКИП", COL_CREW_W),
    ("Ресурси", COL_RES_W),
)


def _количество(task: dict) -> str:
    """Количеството, както се пише на български: 545,50."""
    стойност = task.get("quantity")
    if стойност in (None, ""):
        стойност = task.get("length_m")
    if стойност in (None, ""):
        return ""
    try:
        число = float(стойност)
    except (TypeError, ValueError):
        return str(стойност)
    ако = f"{число:,.2f}".replace(",", " ").replace(".", ",")
    return ако.replace(",00", "") if число.is_integer() else ако


def _последователност(task: dict, row_of: dict[str, int]) -> str:
    """Предшествениците с НОМЕРА НА РЕДОВЕ, както ги пише еталонът: „6SS+10 d".

    Вътрешните ключове („В10_excavation") не значат нищо за човека, който чете
    графика; номерът на реда сочи точно нагоре в същата таблица.
    """
    парчета = []
    for dep in task.get("dependencies") or []:
        ид = (str(dep.get("predecessor_id") or "").strip()
              if isinstance(dep, dict) else str(dep or "").strip())
        ред = row_of.get(ид)
        if not ред:
            continue
        вид = str(dep.get("type") or "FS").upper() if isinstance(dep, dict) else "FS"
        лаг = int(dep.get("lag_days") or 0) if isinstance(dep, dict) else 0
        текст = f"{ред}{'' if вид == 'FS' else вид}"
        if лаг:
            текст += f"{'+' if лаг > 0 else ''}{лаг} d"
        парчета.append(текст)
    return ";".join(парчета)


def _day_to_x(day: int, total_days: int, gantt_left: float, gantt_width: float) -> float:
    """Ден → X на листа.  Оста почва на `ОСТА_ЗАПОЧВА`, не на ден 1."""
    обхват = total_days - ОСТА_ЗАПОЧВА + 1
    if обхват <= 0:
        return gantt_left
    return gantt_left + (day - ОСТА_ЗАПОЧВА) / обхват * gantt_width


def _format_task_name(task: dict, is_phase: bool = False) -> str:
    """Format task name for the table column."""
    name = task.get("name", "")
    if len(name) > 40 and not is_phase:
        name = name[:37] + "..."
    return name


def _flatten_schedule(schedule_data: list[dict]) -> list[dict]:
    """Списъкът за показване: дълбочина, обобщаващи редове, номер на ред.

    Приема и двете форми, в които графикът стига дотук: вложена
    (`sub_activities`) и ПЛОСКА с `parent_id` — пакетният път връща плоска и
    дотук цялата йерархия се губеше, тоест всеки ред изглеждаше еднакво важен.
    """
    if any(t.get("sub_activities") for t in schedule_data or []):
        result = []
        for task in schedule_data:
            is_phase = bool(task.get("sub_activities"))
            result.append({**task, "_is_phase": is_phase, "_is_sub": False,
                           "_indent": 0})
            for sub in task.get("sub_activities") or []:
                result.append({**sub, "_is_phase": False, "_is_sub": True,
                               "_indent": 1})
        return _номерирай(result)

    родител = {str(t.get("id")): str(t.get("parent_id") or "")
               for t in schedule_data or []}

    def дълбочина(ид: str) -> int:
        ниво, текущ, пазач = 0, ид, 0
        while родител.get(текущ) and пазач < 8:
            текущ = родител[текущ]
            ниво += 1
            пазач += 1
        return ниво

    result = []
    for task in schedule_data or []:
        ниво = дълбочина(str(task.get("id")))
        обобщаващ = bool(task.get("is_summary") or task.get("type") == "summary")
        result.append({**task,
                       "_is_phase": обобщаващ and ниво == 0,
                       "_is_sub": ниво >= 2,
                       "_indent": ниво})
    return _номерирай(result)


def _номерирай(редове: list[dict]) -> list[dict]:
    """Номерът на реда е ID-то в таблицата — и адресът в „Последователност"."""
    for i, ред in enumerate(редове, 1):
        ред["_row"] = i
    return редове


def _calculate_pages(num_tasks: int, rows_per_page: int) -> int:
    """Calculate the number of pages needed."""
    if num_tasks <= 0:
        return 1
    return math.ceil(num_tasks / rows_per_page)


# ---------------------------------------------------------------------------
# Main export function
# ---------------------------------------------------------------------------


def export_to_pdf(
    schedule_data: list[dict],
    project_name: str,
    project_params: dict | None = None,
    start_date: str = "2026-06-01",
    show_critical_path: bool = True,
    filename: str | None = None,
) -> bytes | None:
    """Generate A3 landscape PDF with Gantt chart.

    Args:
        schedule_data: List of task dicts from the schedule.
        project_name: Project name for the title.
        project_params: Optional dict with version, design_days,
            construction_days, teams.
        start_date: Calendar start date (ISO format).
        show_critical_path: Whether to highlight critical path.
        filename: Optional file path to also save PDF to disk.

    Returns:
        PDF file as bytes, or None on error.
    """
    if not schedule_data:
        logger.warning("No schedule data for PDF export")
        return None

    try:
        has_cyrillic = _register_fonts()
        font = FONT_NAME if has_cyrillic else "Helvetica"
        font_bold = FONT_NAME_BOLD if has_cyrillic else "Helvetica-Bold"
    except Exception:
        font = "Helvetica"
        font_bold = "Helvetica-Bold"

    try:
        return _render_pdf(
            schedule_data, project_name, project_params, start_date,
            show_critical_path, filename, font, font_bold,
        )
    except Exception as exc:
        logger.error("PDF export failed: %s", exc, exc_info=True)
        return None


def _render_pdf(
    schedule_data: list[dict],
    project_name: str,
    project_params: dict | None,
    start_date: str,
    show_critical_path: bool,
    filename: str | None,
    font: str,
    font_bold: str,
) -> bytes:
    """Core PDF rendering logic."""
    params = project_params or {}

    # Flatten schedule for display
    flat = _flatten_schedule(schedule_data)

    # Calculate total days and date range
    all_tasks = flat
    if not all_tasks:
        all_tasks = schedule_data

    max_end_day = max(
        (t.get("end_day", t.get("start_day", 0) + t.get("duration", 0))
         for t in all_tasks),
        default=0,
    )
    total_days = max(max_end_day, 1)


    # Calculate rows per page
    # +4mm за реда с разкриването по EU AI Act чл. 50 (виж _draw_title)
    title_area_h = TOP_MARGIN + 22 * mm  # title block
    legend_area_h = BOTTOM_MARGIN + 10 * mm
    usable_h = PAGE_H - title_area_h - legend_area_h - HEADER_H
    rows_per_page = int(usable_h / ROW_H)

    num_pages = _calculate_pages(len(flat), rows_per_page)

    # Gantt dimensions
    gantt_width = GANTT_RIGHT - GANTT_LEFT

    # Скалата е В ДНИ, не в календар — тръжен график
    months = _generate_day_blocks(total_days)

    # Кой ред е коя задача — „Последователност" сочи НОМЕРА, не вътрешния ключ.
    row_of = {str(t.get("id")): int(t.get("_row") or 0) for t in flat if t.get("id")}

    # Create PDF
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=PAGE_SIZE)
    c.setTitle(f"График — {project_name}")
    # EU AI Act чл. 50(2) — машинно четимо маркиране в метаданните на файла.
    c.setSubject(CONTENT_DISCLOSURE_BG)
    c.setCreator(SYSTEM_NAME)
    c.setKeywords(pdf_metadata_keywords())

    for page_num in range(num_pages):
        start_idx = page_num * rows_per_page
        end_idx = min(start_idx + rows_per_page, len(flat))
        page_tasks = flat[start_idx:end_idx]

        if page_num > 0:
            c.showPage()

        # Draw page contents
        _draw_title(c, project_name, params, start_date, total_days, font, font_bold)

        content_top = PAGE_H - title_area_h
        _draw_table_header(c, content_top, font_bold)
        _draw_month_header(c, content_top, months, total_days, gantt_width)

        # Draw rows
        y = content_top - HEADER_H
        for i, task in enumerate(page_tasks):
            if y < legend_area_h:
                break

            row_h = PHASE_ROW_H if task.get("_is_phase") else ROW_H
            is_sub = task.get("_is_sub", False)

            # Alternating row background
            if i % 2 == 0:
                c.setFillColor(HexColor("#F8F8F8"))
                c.rect(LEFT_MARGIN - TABLE_W, y - row_h, TABLE_W, row_h, fill=1, stroke=0)
                c.rect(GANTT_LEFT - 4 * mm, y - row_h, gantt_width + 4 * mm, row_h, fill=1, stroke=0)

            _draw_task_row(
                c, task, y, row_h, int(task.get("_row", start_idx + i + 1)),
                total_days, gantt_width, show_critical_path,
                font, font_bold, is_sub, row_of,
            )

            y -= row_h

        # Phase separator line (design/construction boundary)
        _draw_phase_separator(c, schedule_data, total_days, gantt_width, content_top, y, font)

        # Month grid lines on Gantt area
        _draw_month_grid(c, months, total_days, gantt_width, content_top - HEADER_H, y)

        # Legend
        _draw_legend(c, font, font_bold, schedule_data)

        # Page number
        if num_pages > 1:
            c.setFont(font, FONT_SIZE_SMALL)
            c.setFillColor(colors.gray)
            c.drawRightString(
                PAGE_W - RIGHT_MARGIN,
                BOTTOM_MARGIN / 2,
                f"Страница {page_num + 1} от {num_pages}",
            )

    c.save()
    pdf_bytes = buffer.getvalue()

    if filename:
        Path(filename).write_bytes(pdf_bytes)
        logger.info("PDF saved to %s", filename)

    return pdf_bytes


# ---------------------------------------------------------------------------
# Drawing functions
# ---------------------------------------------------------------------------


def _draw_title(
    c: canvas.Canvas,
    project_name: str,
    params: dict,
    start_date: str,
    total_days: int,
    font: str,
    font_bold: str,
) -> None:
    """Draw the title block at the top of the page."""
    y = PAGE_H - 8 * mm

    # Line 1: Main title
    c.setFont(font_bold, FONT_SIZE_TITLE)
    c.setFillColor(colors.black)
    c.drawCentredString(PAGE_W / 2, y, "ЛИНЕЕН ГРАФИК")

    # Line 2: Project info
    y -= 6 * mm
    version = params.get("version", "V1.0")
    date_str = datetime.now().strftime("%d.%m.%Y")
    c.setFont(font, FONT_SIZE_SUBTITLE)
    c.drawCentredString(
        PAGE_W / 2, y,
        f"Проект: {project_name}    Версия: {version}    Дата: {date_str}",
    )

    # Line 3: Duration info
    y -= 5 * mm
    design_days = params.get("design_days", 0)
    construction_days = params.get("construction_days", 0)
    if design_days and construction_days:
        duration_text = (
            f"Срок: {total_days} дни ({design_days}д проектиране + "
            f"{construction_days}д строителство)"
        )
    else:
        duration_text = f"Срок: {total_days} дни"
    # Тръжен график: няма календар, защото няма подписан договор.  Броенето е
    # от ден 1 — денят, в който тръгва изпълнението (Протокол 2а).
    duration_text += "   ·   всички срокове са в ДНИ от ден 1"
    c.setFont(font, FONT_SIZE_SMALL + 1)
    c.drawCentredString(PAGE_W / 2, y, duration_text)

    # Line 4: Teams
    teams = params.get("teams", "")
    if teams:
        y -= 4 * mm
        c.drawCentredString(PAGE_W / 2, y, f"Екипи: {teams}")

    # EU AI Act чл. 50 — видимо разкриване върху самия документ.
    # Стои НАД разделителната линия, в заглавния блок: този PDF отива при
    # възложителя и получателят трябва да види произхода без да рови в
    # метаданните.
    y -= 4 * mm
    c.setFont(font, FONT_SIZE_SMALL)
    c.setFillColor(colors.grey)
    c.drawCentredString(PAGE_W / 2, y, CONTENT_DISCLOSURE_BG)
    c.setFillColor(colors.black)

    # Separator line below title
    y -= 3 * mm
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.5)
    c.line(LEFT_MARGIN - TABLE_W, y, PAGE_W - RIGHT_MARGIN, y)


def _draw_table_header(
    c: canvas.Canvas, content_top: float, font_bold: str
) -> None:
    """Draw the table column headers."""
    y = content_top
    x_start = LEFT_MARGIN - TABLE_W

    # Header background
    c.setFillColor(HexColor("#E0E0E0"))
    c.rect(x_start, y - HEADER_H, TABLE_W, HEADER_H, fill=1, stroke=0)

    # Header border
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.3)
    c.rect(x_start, y - HEADER_H, TABLE_W, HEADER_H, fill=0, stroke=1)

    # Column headers
    c.setFont(font_bold, FONT_SIZE + 1)
    c.setFillColor(colors.black)
    text_y = y - HEADER_H + 2.5 * mm

    x = x_start + 1 * mm
    for заглавие, ширина in _КОЛОНИ:
        c.drawString(x, text_y, заглавие)
        x += ширина


def _draw_month_header(
    c: canvas.Canvas,
    content_top: float,
    months: list[dict],
    total_days: int,
    gantt_width: float,
) -> None:
    """Draw the month scale header above the Gantt area."""
    y = content_top

    # Header background for Gantt area
    c.setFillColor(HexColor("#E0E0E0"))
    c.rect(GANTT_LEFT - 4 * mm, y - HEADER_H, gantt_width + 4 * mm, HEADER_H, fill=1, stroke=0)

    c.setStrokeColor(colors.black)
    c.setLineWidth(0.3)
    c.rect(GANTT_LEFT - 4 * mm, y - HEADER_H, gantt_width + 4 * mm, HEADER_H, fill=0, stroke=1)

    c.setFont(FONT_NAME if _font_registered else "Helvetica", FONT_SIZE)
    c.setFillColor(colors.black)

    for i, month in enumerate(months):
        x1 = _day_to_x(month["start_day"], total_days, GANTT_LEFT, gantt_width)
        x2 = _day_to_x(month["end_day"], total_days, GANTT_LEFT, gantt_width)
        mid_x = (x1 + x2) / 2

        # Month label
        label = month["label"]
        text_y = y - HEADER_H + 2.5 * mm

        # Clip label if column is too narrow
        col_w = x2 - x1
        if col_w > 15 * mm:
            c.drawCentredString(mid_x, text_y, label)
        elif col_w > 8 * mm:
            c.drawCentredString(mid_x, text_y, month["short_label"])

        # Vertical separator line
        if i > 0:
            c.setStrokeColor(HexColor("#CCCCCC"))
            c.setLineWidth(0.2)
            c.line(x1, y, x1, y - HEADER_H)

    # ТУК ЗАПОЧВА ВСИЧКО.  Водещите дни пред ден 1 съществуват само за да се
    # вижда този ръб (изпълнителят, 25.08.2026).  Без надпис ръбът е поредната
    # разделителна чертица.
    x_старт = _day_to_x(1, total_days, GANTT_LEFT, gantt_width)
    c.setStrokeColor(HexColor("#8B0000"))
    c.setLineWidth(0.8)
    c.line(x_старт, y, x_старт, y - HEADER_H)
    c.setFillColor(HexColor("#8B0000"))
    c.setFont(FONT_NAME if _font_registered else "Helvetica", FONT_SIZE_SMALL)
    c.drawString(x_старт + 0.5 * mm, y - 2.2 * mm, "ден 1")
    c.setFillColor(colors.black)


def _draw_task_row(
    c: canvas.Canvas,
    task: dict,
    y: float,
    row_h: float,
    row_num: int,
    total_days: int,
    gantt_width: float,
    show_critical: bool,
    font: str,
    font_bold: str,
    is_sub: bool,
    row_of: dict[str, int] | None = None,
) -> None:
    """Draw a single task row (table + Gantt bar)."""
    row_of = row_of or {}
    x_start = LEFT_MARGIN - TABLE_W
    text_y = y - row_h + 1.0 * mm
    is_phase = task.get("_is_phase", False)

    # Select font
    if is_phase:
        c.setFont(font_bold, FONT_SIZE + 0.5)
    elif is_sub:
        c.setFont(font, FONT_SIZE_SMALL)
    else:
        c.setFont(font, FONT_SIZE)

    c.setFillColor(colors.black)

    # Table columns
    x = x_start + 1 * mm

    # ID
    c.drawString(x, text_y, str(row_num))
    x += COL_NUM_W

    # Вид дейност / Участък
    name = _format_task_name(task, is_phase)
    отстъп = min(int(task.get("_indent", 0)), 3) * 2 * mm
    c.drawString(x + отстъп, text_y,
                 _подрежи(c, name, COL_NAME_W - отстъп - 1 * mm, font, c._fontsize))
    x += COL_NAME_W

    # ед.мярка · диаметър · к-во — стоят на реда, който носи количеството
    c.setFont(font, FONT_SIZE_SMALL)
    c.drawString(x, text_y, str(task.get("unit") or ""))
    x += COL_UNIT_W
    dn = task.get("dn") or task.get("diameter") or ""
    c.drawString(x, text_y, str(dn))
    x += COL_DN_W
    c.drawString(x, text_y, _количество(task))
    x += COL_QTY_W

    # Срок · Последователност · Начало (ден) · Край (ден) — В ДНИ, като еталона
    c.setFont(font if not is_phase else font_bold, FONT_SIZE)
    duration = task.get("duration", 0) or 0
    if duration > 0:
        цяло = int(duration) if float(duration).is_integer() else duration
        c.drawString(x, text_y, f"{цяло} d")
    x += COL_DAYS_W

    c.setFont(font, FONT_SIZE_SMALL)
    c.drawString(x, text_y, _подрежи(c, _последователност(task, row_of),
                                     COL_PRED_W - 1 * mm, font, FONT_SIZE_SMALL))
    x += COL_PRED_W

    c.setFont(font if not is_phase else font_bold, FONT_SIZE)
    start_day = task.get("start_day", 0)
    end_day = task.get("end_day", start_day + max(duration, 1) - 1)
    if duration > 0 or start_day:
        c.drawString(x, text_y, f"{int(start_day)} d")
        c.drawString(x + COL_START_W, text_y, f"{int(end_day)} d")
    x += COL_START_W + COL_END_W

    # ЕКИП
    c.setFont(font, FONT_SIZE_SMALL)
    екип = str(task.get("team") or task.get("crew_id") or "")
    if екип and екип != "—":
        c.drawString(x, text_y, _подрежи(c, екип, COL_CREW_W - 1 * mm,
                                         font, FONT_SIZE_SMALL))
    x += COL_CREW_W

    # Ресурси — както в еталона: имената, разделени с „;"
    ресурси = ";".join(str(r) for r in (task.get("resources") or []))
    if ресурси:
        c.drawString(x, text_y, _подрежи(c, ресурси, COL_RES_W - 1 * mm,
                                         font, FONT_SIZE_SMALL))
    c.setFont(font, FONT_SIZE)

    # Table row bottom border
    c.setStrokeColor(HexColor("#E0E0E0"))
    c.setLineWidth(0.1)
    c.line(x_start, y - row_h, LEFT_MARGIN, y - row_h)

    # --- Gantt bar ---
    start_day = task.get("start_day", 0)
    end_day = task.get("end_day", start_day + max(duration, 1) - 1)
    task_type = task.get("type", "design")
    is_critical = task.get("is_critical", False) and show_critical

    if duration == 0:
        # Milestone — draw diamond
        mx = _day_to_x(start_day, total_days, GANTT_LEFT, gantt_width)
        my = y - row_h / 2
        diamond_size = 2 * mm
        c.setFillColor(HexColor("#FFD700"))
        c.setStrokeColor(colors.black)
        c.setLineWidth(0.3)
        path = c.beginPath()
        path.moveTo(mx, my + diamond_size)
        path.lineTo(mx + diamond_size, my)
        path.lineTo(mx, my - diamond_size)
        path.lineTo(mx - diamond_size, my)
        path.close()
        c.drawPath(path, fill=1, stroke=1)
        return

    bar_x = _day_to_x(start_day, total_days, GANTT_LEFT, gantt_width)
    bar_end_x = _day_to_x(end_day + 1, total_days, GANTT_LEFT, gantt_width)
    bar_w = max(bar_end_x - bar_x, 1)
    bar_y = y - row_h / 2 - BAR_H / 2

    # Bar color
    bar_color = COLOR_MAP.get(task_type, HexColor("#4472C4"))
    if is_critical:
        bar_color = CRITICAL_COLOR

    # Phase bars: lighter, taller
    if is_phase:
        c.setFillColor(bar_color)
        c.setFillAlpha(0.3)
        c.rect(bar_x, bar_y - 0.5 * mm, bar_w, BAR_H + 1 * mm, fill=1, stroke=0)
        c.setFillAlpha(1.0)

        # Top and bottom lines for summary bar
        c.setStrokeColor(bar_color)
        c.setLineWidth(0.8)
        c.line(bar_x, bar_y + BAR_H + 0.5 * mm, bar_end_x, bar_y + BAR_H + 0.5 * mm)
        c.line(bar_x, bar_y - 0.5 * mm, bar_end_x, bar_y - 0.5 * mm)

        # Down triangles at ends
        tri_size = 1 * mm
        for tx in (bar_x, bar_end_x):
            path = c.beginPath()
            path.moveTo(tx - tri_size, bar_y + BAR_H + 0.5 * mm)
            path.lineTo(tx + tri_size, bar_y + BAR_H + 0.5 * mm)
            path.lineTo(tx, bar_y + BAR_H + 0.5 * mm - tri_size)
            path.close()
            c.setFillColor(bar_color)
            c.setFillAlpha(1.0)
            c.drawPath(path, fill=1, stroke=0)
    else:
        # Regular bar
        c.setFillColor(bar_color)
        if is_sub:
            c.setFillAlpha(0.7)
        c.rect(bar_x, bar_y, bar_w, BAR_H, fill=1, stroke=0)
        c.setFillAlpha(1.0)

        # Critical path border
        if is_critical:
            c.setStrokeColor(HexColor("#8B0000"))
            c.setLineWidth(0.8)
            c.rect(bar_x, bar_y, bar_w, BAR_H, fill=0, stroke=1)


def _draw_phase_separator(
    c: canvas.Canvas,
    schedule_data: list[dict],
    total_days: int,
    gantt_width: float,
    content_top: float,
    content_bottom: float,
    font: str,
) -> None:
    """Draw vertical dashed line at design/construction boundary."""
    design_end = 0
    for task in schedule_data:
        if task.get("phase") == "design":
            end = task.get("end_day", task.get("start_day", 0) + task.get("duration", 0))
            design_end = max(design_end, end)

    if design_end <= 0:
        return

    x = _day_to_x(design_end, total_days, GANTT_LEFT, gantt_width)
    c.setStrokeColor(CRITICAL_COLOR)
    c.setLineWidth(0.5)
    c.setDash(3, 2)
    c.line(x, content_top - HEADER_H, x, content_bottom)
    c.setDash()  # reset

    # Label
    c.setFont(font, FONT_SIZE_SMALL)
    c.setFillColor(CRITICAL_COLOR)
    c.drawCentredString(x, content_top - HEADER_H + 1 * mm, "Протокол обр.2")


def _draw_month_grid(
    c: canvas.Canvas,
    months: list[dict],
    total_days: int,
    gantt_width: float,
    top_y: float,
    bottom_y: float,
) -> None:
    """Draw vertical grid lines for month boundaries and zebra stripes."""
    x_старт = _day_to_x(1, total_days, GANTT_LEFT, gantt_width)
    c.setStrokeColor(HexColor("#8B0000"))
    c.setLineWidth(0.5)
    c.line(x_старт, top_y, x_старт, bottom_y)
    for i, month in enumerate(months):
        x = _day_to_x(month["start_day"], total_days, GANTT_LEFT, gantt_width)

        # Zebra stripe for even months
        if i % 2 == 0:
            x_end = _day_to_x(month["end_day"], total_days, GANTT_LEFT, gantt_width)
            c.setFillColor(HexColor("#F5F5F5"))
            c.setFillAlpha(0.3)
            c.rect(x, bottom_y, x_end - x, top_y - bottom_y, fill=1, stroke=0)
            c.setFillAlpha(1.0)

        # Grid line
        if i > 0:
            c.setStrokeColor(HexColor("#DDDDDD"))
            c.setLineWidth(0.15)
            c.line(x, top_y, x, bottom_y)


def _draw_legend(
    c: canvas.Canvas,
    font: str,
    font_bold: str,
    schedule_data: list[dict],
) -> None:
    """Draw horizontal legend at the bottom of the page."""
    y = BOTTOM_MARGIN - 2 * mm
    x = LEFT_MARGIN - TABLE_W

    # Separator line
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.5)
    c.line(x, y + 6 * mm, PAGE_W - RIGHT_MARGIN, y + 6 * mm)

    c.setFont(font_bold, FONT_SIZE)
    c.setFillColor(colors.black)
    c.drawString(x, y + 1 * mm, "ЛЕГЕНДА:")
    x += 18 * mm

    # Collect types present in schedule
    present_types = set()
    for task in schedule_data:
        present_types.add(task.get("type", ""))
        for sub in task.get("sub_activities", []):
            present_types.add(sub.get("type", ""))

    # Draw legend items
    c.setFont(font, FONT_SIZE)
    box_size = 3 * mm
    spacing = 3 * mm

    for type_code, label in TYPE_LABELS.items():
        if type_code not in present_types:
            continue

        color = COLOR_MAP.get(type_code, HexColor("#4472C4"))
        c.setFillColor(color)
        c.rect(x, y, box_size, box_size, fill=1, stroke=0)
        x += box_size + 1 * mm

        c.setFillColor(colors.black)
        c.drawString(x, y + 0.5 * mm, label)
        x += c.stringWidth(label, font, FONT_SIZE) + spacing

    # Milestone symbol
    c.setFillColor(HexColor("#FFD700"))
    diamond_x = x + 1.5 * mm
    diamond_y = y + 1.5 * mm
    ds = 1.5 * mm
    path = c.beginPath()
    path.moveTo(diamond_x, diamond_y + ds)
    path.lineTo(diamond_x + ds, diamond_y)
    path.lineTo(diamond_x, diamond_y - ds)
    path.lineTo(diamond_x - ds, diamond_y)
    path.close()
    c.drawPath(path, fill=1, stroke=0)
    x += 4 * mm

    c.setFillColor(colors.black)
    c.drawString(x, y + 0.5 * mm, "Етап")
    x += c.stringWidth("Етап", font, FONT_SIZE) + spacing

    # Critical path indicator
    c.setStrokeColor(CRITICAL_COLOR)
    c.setLineWidth(1.5)
    c.line(x, y + 1.5 * mm, x + 8 * mm, y + 1.5 * mm)
    x += 10 * mm

    c.setFillColor(colors.black)
    c.drawString(x, y + 0.5 * mm, "Критичен път")


def _подрежи(c: canvas.Canvas, текст: str, ширина: float, шрифт: str, размер: float) -> str:
    """Съкращава текста, за да се побере в колоната (иначе влиза в съседната)."""
    if c.stringWidth(текст, шрифт, размер) <= ширина:
        return текст
    while текст and c.stringWidth(текст + "…", шрифт, размер) > ширина:
        текст = текст[:-1]
    return текст + "…"


def _generate_day_blocks(total_days: int, стъпка: int = 30) -> list[dict]:
    """Дели срока на блокове по 30 дни — оста брои ДНИ, не календарни месеци.

    Тръжният график не може да носи дати: началото е Протокол 2а, който още го
    няма.  Човешките графици, с които се сравняваме, също броят дни.
    """
    блокове = [{"start_day": ОСТА_ЗАПОЧВА, "end_day": 0,
                "label": f"ден {ОСТА_ЗАПОЧВА}", "short_label": "0"}]
    ден = 1
    n = 1
    while ден <= max(total_days, 1):
        край = min(ден + стъпка - 1, total_days)
        блокове.append({
            "start_day": ден,
            "end_day": край,
            "label": f"дни {ден}–{край}",
            "short_label": str(край),
        })
        ден = край + 1
        n += 1
    return блокове


def _generate_months(start_dt: datetime, end_dt: datetime) -> list[dict]:
    """Generate month metadata for the time axis."""
    months = []
    current = start_dt.replace(day=1)

    month_names = {
        1: "Яну", 2: "Фев", 3: "Мар", 4: "Апр", 5: "Май", 6: "Юни",
        7: "Юли", 8: "Авг", 9: "Сеп", 10: "Окт", 11: "Ное", 12: "Дек",
    }

    while current <= end_dt:
        # Start day relative to project start
        month_start = max((current - start_dt).days + 1, 1)

        # End of this month
        if current.month == 12:
            next_month = current.replace(year=current.year + 1, month=1)
        else:
            next_month = current.replace(month=current.month + 1)
        month_end = (next_month - start_dt).days

        short_name = month_names.get(current.month, "?")
        label = f"{short_name} {current.year}"
        short_label = f"М{(current.year - start_dt.year) * 12 + current.month - start_dt.month + 1}"

        months.append({
            "start_day": month_start,
            "end_day": month_end,
            "label": label,
            "short_label": short_label,
            "date": current,
        })

        current = next_month

    return months
