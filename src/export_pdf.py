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
from datetime import datetime, timedelta
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.colors import HexColor
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
LEFT_MARGIN = 115 * mm
RIGHT_MARGIN = 10 * mm

# Table column widths
COL_NUM_W = 6 * mm
COL_NAME_W = 55 * mm
COL_DN_W = 10 * mm
COL_LENGTH_W = 12 * mm
COL_TEAM_W = 10 * mm
COL_DAYS_W = 8 * mm
TABLE_W = COL_NUM_W + COL_NAME_W + COL_DN_W + COL_LENGTH_W + COL_TEAM_W + COL_DAYS_W

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
COLOR_MAP = {
    "design": HexColor("#4472C4"),
    "water_pipe": HexColor("#5B9BD5"),
    "sewer": HexColor("#ED7D31"),
    "kps": HexColor("#FFC000"),
    "road": HexColor("#A5A5A5"),
    "electrical": HexColor("#70AD47"),
    "mobilization": HexColor("#9DC3E6"),
    "completion": HexColor("#BF8F00"),
    "supervision": HexColor("#7030A0"),
}
CRITICAL_COLOR = HexColor("#FF0000")

TYPE_LABELS = {
    "design": "Проектиране",
    "water_pipe": "Водоснабдяване",
    "sewer": "Канализация",
    "kps": "КПС",
    "road": "Пътни работи",
    "electrical": "ЕЛ/ТТ",
    "mobilization": "Мобилизация",
    "completion": "Завършване",
    "supervision": "Авт. надзор",
}

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


def _day_to_x(day: int, total_days: int, gantt_left: float, gantt_width: float) -> float:
    """Convert day number to X coordinate on the PDF page."""
    if total_days <= 0:
        return gantt_left
    return gantt_left + (day - 1) / total_days * gantt_width


def _format_task_name(task: dict, is_phase: bool = False) -> str:
    """Format task name for the table column."""
    name = task.get("name", "")
    if len(name) > 40 and not is_phase:
        name = name[:37] + "..."
    return name


def _flatten_schedule(schedule_data: list[dict]) -> list[dict]:
    """Flatten hierarchical schedule into a display list with metadata."""
    result = []
    for task in schedule_data:
        is_phase = bool(task.get("sub_activities"))
        result.append({**task, "_is_phase": is_phase, "_is_sub": False, "_indent": 0})
        if task.get("sub_activities"):
            for sub in task["sub_activities"]:
                result.append({**sub, "_is_phase": False, "_is_sub": True, "_indent": 1})
    return result


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

    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = start_dt + timedelta(days=total_days)

    # Calculate rows per page
    title_area_h = TOP_MARGIN + 18 * mm  # title block
    legend_area_h = BOTTOM_MARGIN + 10 * mm
    usable_h = PAGE_H - title_area_h - legend_area_h - HEADER_H
    rows_per_page = int(usable_h / ROW_H)

    num_pages = _calculate_pages(len(flat), rows_per_page)

    # Gantt dimensions
    gantt_width = GANTT_RIGHT - GANTT_LEFT

    # Generate months for the time axis
    months = _generate_months(start_dt, end_dt)

    # Create PDF
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=PAGE_SIZE)
    c.setTitle(f"График — {project_name}")

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
                c, task, y, row_h, start_idx + i + 1,
                total_days, gantt_width, show_critical_path,
                font, font_bold, is_sub,
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
    c.setFont(font, FONT_SIZE_SMALL + 1)
    c.drawCentredString(PAGE_W / 2, y, duration_text)

    # Line 4: Teams
    teams = params.get("teams", "")
    if teams:
        y -= 4 * mm
        c.drawCentredString(PAGE_W / 2, y, f"Екипи: {teams}")

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
    c.drawString(x, text_y, "№")
    x += COL_NUM_W
    c.drawString(x, text_y, "Дейност")
    x += COL_NAME_W
    c.drawString(x, text_y, "DN")
    x += COL_DN_W
    c.drawString(x, text_y, "L(м)")
    x += COL_LENGTH_W
    c.drawString(x, text_y, "Екип")
    x += COL_TEAM_W
    c.drawString(x, text_y, "Дни")


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
) -> None:
    """Draw a single task row (table + Gantt bar)."""
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

    # Row number
    if not is_sub:
        c.drawString(x, text_y, str(row_num))
    x += COL_NUM_W

    # Task name
    name = _format_task_name(task, is_phase)
    if is_sub:
        x += 3 * mm  # indent
        c.drawString(x, text_y, name)
        x = x_start + 1 * mm + COL_NUM_W + COL_NAME_W
    else:
        c.drawString(x, text_y, name)
        x += COL_NAME_W

    # DN
    dn = task.get("diameter", "")
    if dn:
        c.drawString(x, text_y, str(dn))
    x += COL_DN_W

    # Length
    length = task.get("length_m", "")
    if length:
        c.drawString(x, text_y, str(int(length)) if isinstance(length, float) else str(length))
    x += COL_LENGTH_W

    # Team
    team = task.get("team", "")
    if team and team != "\u2014":
        c.setFont(font if not is_phase else font_bold, FONT_SIZE_SMALL)
        c.drawString(x, text_y, team)
    x += COL_TEAM_W

    # Days
    duration = task.get("duration", 0)
    if duration > 0:
        c.setFont(font, FONT_SIZE)
        c.drawString(x, text_y, str(duration))

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
