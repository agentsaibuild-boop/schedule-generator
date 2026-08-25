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
from dataclasses import dataclass
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
COL_NUM_W = 8 * mm          # ID
COL_NAME_W = 58 * mm        # Вид дейност / Участък
COL_UNIT_W = 12 * mm        # ед.мярка
COL_DN_W = 12 * mm          # диаметър
COL_QTY_W = 14 * mm         # к-во
COL_DAYS_W = 12 * mm        # Срок
COL_PRED_W = 15 * mm        # Последователност
COL_START_W = 13 * mm       # Начало (ден)
COL_END_W = 13 * mm         # Край (ден)
COL_CREW_W = 10 * mm        # ЕКИП
COL_RES_W = 36 * mm         # Ресурси
TABLE_W = (COL_NUM_W + COL_NAME_W + COL_UNIT_W + COL_DN_W + COL_QTY_W
           + COL_DAYS_W + COL_PRED_W + COL_START_W + COL_END_W
           + COL_CREW_W + COL_RES_W)
LEFT_MARGIN = 6 * mm + TABLE_W

#: Скалата почва ПРЕДИ ден 1 — „за да се вижда по-ясно кога започва всичко"
#: (изпълнителят, 25.08.2026).  Еталонът също оставя ден пред началото.
ОСТА_ЗАПОЧВА = -2

#: ЕДИН ДЕН = ЕДНА КОЛОНКА (изпълнителят, 25.08.2026: „графика трябва да мога
#: да го разгледам и да виждам дните от първия до последния").  Еталонът на
#: Илиянци прави точно това: над таблицата стоят „-1, 1, 2, 3 …" до ден 780, на
#: един-единствен лист 8504 × 8504 pt.  Блоковете по 30 дни, които стояха тук
#: дотогава, показваха срока, но не и деня.
ДЕН_W = 2.9 * mm
#: По-тясно от това числото на деня не се чете — тогава се надписва всеки пети.
ДЕН_W_МИН = 1.1 * mm
#: Границата на PDF формата: 200 инча.  По-голям лист не се отваря никъде.
МАКС_ЛИСТ = 14000.0
#: Заглавният блок и легендата — единственото, което не е таблица или скала.
ЗАГЛАВИЕ_H = 30 * mm
ЛЕГЕНДА_H = 14 * mm

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
    ("Последователност", COL_PRED_W),
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
            текст += f"{'+' if лаг > 0 else ''}{лаг} дни"
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


@dataclass(frozen=True)
class Лист:
    """Размерите на ЕДИН лист, изчислени от самия график.

    Тръжният график не се реже на страници: човекът иска да го разгледа целия
    и да види дните от първия до последния (изпълнителят, 25.08.2026).
    Еталонът на Илиянци е точно такъв — един лист 8504 × 8504 pt.  Затова
    листът тук СЛЕДВА графика, вместо графикът да следва листа.
    """

    page_w: float
    page_h: float
    table_left: float
    gantt_left: float
    gantt_width: float
    day_w: float
    content_top: float


def _изчисли_листа(total_days: int, редове: list[dict]) -> Лист:
    """Колко голям трябва да е листът, за да се побере всичко наведнъж."""
    дни = max(total_days - ОСТА_ЗАПОЧВА + 1, 1)
    table_left = 6 * mm
    gantt_left = table_left + TABLE_W + 4 * mm

    day_w = ДЕН_W
    page_w = gantt_left + дни * day_w + RIGHT_MARGIN
    if page_w > МАКС_ЛИСТ:
        day_w = max((МАКС_ЛИСТ - gantt_left - RIGHT_MARGIN) / дни, ДЕН_W_МИН)
        page_w = gantt_left + дни * day_w + RIGHT_MARGIN

    висок = sum(PHASE_ROW_H if r.get("_is_phase") else ROW_H for r in редове)
    page_h = min(ЗАГЛАВИЕ_H + HEADER_H + висок + ЛЕГЕНДА_H, МАКС_ЛИСТ)
    return Лист(page_w=page_w, page_h=page_h, table_left=table_left,
                gantt_left=gantt_left, gantt_width=дни * day_w, day_w=day_w,
                content_top=page_h - ЗАГЛАВИЕ_H)


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
    """Целият график на ЕДИН лист: таблицата вляво, скалата по дни вдясно."""
    params = project_params or {}

    flat = _flatten_schedule(schedule_data)
    all_tasks = flat or schedule_data

    max_end_day = max(
        (t.get("end_day", t.get("start_day", 0) + t.get("duration", 0))
         for t in all_tasks),
        default=0,
    )
    total_days = max(max_end_day, 1)

    лист = _изчисли_листа(total_days, flat)

    # Кой ред е коя задача — „Последователност" сочи НОМЕРА, не вътрешния ключ.
    row_of = {str(t.get("id")): int(t.get("_row") or 0) for t in flat if t.get("id")}

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(лист.page_w, лист.page_h))
    c.setTitle(f"График — {project_name}")
    # EU AI Act чл. 50(2) — машинно четимо маркиране в метаданните на файла.
    c.setSubject(CONTENT_DISCLOSURE_BG)
    c.setCreator(SYSTEM_NAME)
    c.setKeywords(pdf_metadata_keywords())

    _draw_title(c, лист, project_name, params, total_days, font, font_bold)
    _draw_table_header(c, лист, font, font_bold)
    _draw_day_axis(c, лист, total_days, font)

    y = лист.content_top - HEADER_H
    долу = y - sum(PHASE_ROW_H if t.get("_is_phase") else ROW_H for t in flat)

    _draw_day_grid(c, лист, total_days, лист.content_top - HEADER_H, долу)

    for i, task in enumerate(flat):
        row_h = PHASE_ROW_H if task.get("_is_phase") else ROW_H
        if i % 2 == 0:
            c.setFillColor(HexColor("#F8F8F8"))
            c.rect(лист.table_left, y - row_h, TABLE_W, row_h, fill=1, stroke=0)
        _draw_task_row(
            c, лист, task, y, row_h, int(task.get("_row", i + 1)),
            total_days, show_critical_path, font, font_bold,
            task.get("_is_sub", False), row_of,
        )
        y -= row_h

    _draw_phase_separator(c, лист, schedule_data, total_days,
                          лист.content_top - HEADER_H, y, font)
    _draw_legend(c, лист, font, font_bold, schedule_data)

    c.save()
    pdf_bytes = buffer.getvalue()

    if filename:
        Path(filename).write_bytes(pdf_bytes)
        logger.info("PDF saved to %s", filename)

    return pdf_bytes

def _draw_title(
    c: canvas.Canvas,
    лист: Лист,
    project_name: str,
    params: dict,
    total_days: int,
    font: str,
    font_bold: str,
) -> None:
    """Заглавният блок.  БЕЗ ДАТИ — нито на съставяне, нито на изпълнение.

    Изпълнителят, 25.08.2026: „никъде да няма дати, а само брой дни".  В
    процедура договор няма, Протокол 2а няма, тоест всяка календарна дата е
    измислена — включително датата, на която е съставен листът.
    """
    y = лист.page_h - 8 * mm

    c.setFont(font_bold, FONT_SIZE_TITLE)
    c.setFillColor(colors.black)
    c.drawCentredString(лист.page_w / 2, y, "ЛИНЕЕН ГРАФИК")

    y -= 6 * mm
    version = params.get("version", "V1.0")
    c.setFont(font, FONT_SIZE_SUBTITLE)
    c.drawCentredString(лист.page_w / 2, y,
                        f"Проект: {project_name}    Версия: {version}")

    y -= 5 * mm
    design_days = params.get("design_days", 0)
    construction_days = params.get("construction_days", 0)
    if design_days and construction_days:
        duration_text = (
            f"Срок: {total_days} дни ({design_days} дни проектиране + "
            f"{construction_days} дни строителство)"
        )
    else:
        duration_text = f"Срок: {total_days} дни"
    duration_text += "   ·   всички срокове са в ДНИ от ден 1"
    c.setFont(font, FONT_SIZE_SMALL + 1)
    c.drawCentredString(лист.page_w / 2, y, duration_text)

    teams = params.get("teams", "")
    if teams:
        y -= 4 * mm
        c.drawCentredString(лист.page_w / 2, y, f"Екипи: {teams}")

    # EU AI Act чл. 50 — видимо разкриване върху самия документ.
    y -= 4 * mm
    c.setFont(font, FONT_SIZE_SMALL)
    c.setFillColor(colors.grey)
    c.drawCentredString(лист.page_w / 2, y, CONTENT_DISCLOSURE_BG)
    c.setFillColor(colors.black)

    y -= 3 * mm
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.5)
    c.line(лист.table_left, y, лист.page_w - RIGHT_MARGIN, y)

def _раздели_заглавието(заглавие: str, ширина: float, c, шрифт: str,
                        размер: float) -> list[str]:
    """Заглавието на колоната на един или два реда — както в еталона.

    „Начало (ден)" не се побира в 13 mm и лягаше върху съседната колона; в
    еталона то стои на два реда, „Начало" над „(ден)".
    """
    if c.stringWidth(заглавие, шрифт, размер) <= ширина:
        return [заглавие]
    думи = заглавие.split(" ")
    for разрез in range(len(думи) - 1, 0, -1):
        горе, долу = " ".join(думи[:разрез]), " ".join(думи[разрез:])
        if (c.stringWidth(горе, шрифт, размер) <= ширина
                and c.stringWidth(долу, шрифт, размер) <= ширина):
            return [горе, долу]
    # ЕДНА ДУМА, по-дълга от колоната: „Последователност".  Еталонът я реже по
    # средата („Посл" / „едователност") — по-добре разрязана, отколкото легнала
    # върху съседната колона.
    for разрез in range(len(заглавие) - 1, 0, -1):
        горе, долу = заглавие[:разрез], заглавие[разрез:]
        if (c.stringWidth(горе, шрифт, размер) <= ширина
                and c.stringWidth(долу, шрифт, размер) <= ширина):
            return [горе, долу]
    return [заглавие]


def _draw_table_header(c: canvas.Canvas, лист: Лист, font: str,
                       font_bold: str) -> None:
    """Шапката на таблицата: единайсетте колони на еталона, четими."""
    y = лист.content_top
    x_start = лист.table_left

    c.setFillColor(HexColor("#E0E0E0"))
    c.rect(x_start, y - HEADER_H, TABLE_W, HEADER_H, fill=1, stroke=0)
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.3)
    c.rect(x_start, y - HEADER_H, TABLE_W, HEADER_H, fill=0, stroke=1)

    # Шапката е с РАЗМЕРА НА РЕДА, не по-едра: „ед.мярка" и „диаметър" не се
    # побират в своите колони при по-голям шрифт и лягаха върху съседните.
    размер = FONT_SIZE
    c.setFont(font_bold, размер)
    c.setFillColor(colors.black)

    x = x_start
    for заглавие, ширина in _КОЛОНИ:
        редове = _раздели_заглавието(заглавие, ширина - 1.6 * mm, c,
                                     font_bold, размер)
        text_y = y - HEADER_H + (2.4 * mm if len(редове) == 1 else 4.2 * mm)
        for ред in редове:
            c.drawString(x + 0.8 * mm, text_y, ред)
            text_y -= 1.9 * mm
        if x > x_start:
            c.setStrokeColor(HexColor("#BBBBBB"))
            c.setLineWidth(0.2)
            c.line(x, y, x, y - HEADER_H)
        x += ширина

def _draw_day_axis(c: canvas.Canvas, лист: Лист, total_days: int,
                   font: str) -> None:
    """Скалата: всеки ден със своя номер, от първия до последния.

    Изпълнителят, 25.08.2026: „графика трябва да мога да го разгледам и да
    виждам дните от първия до последния".  Еталонът на Илиянци надписва всеки
    ден поотделно; блоковете по 30 дни, които стояха тук, показваха срока, но
    не и деня.  При много дълъг обект колонката става по-тясна от числото — там
    се надписва всеки пети ден, а решетката пак е дневна.
    """
    y = лист.content_top

    c.setFillColor(HexColor("#E0E0E0"))
    c.rect(лист.gantt_left, y - HEADER_H, лист.gantt_width, HEADER_H,
           fill=1, stroke=0)
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.3)
    c.rect(лист.gantt_left, y - HEADER_H, лист.gantt_width, HEADER_H,
           fill=0, stroke=1)

    размер = min(FONT_SIZE_SMALL, лист.day_w / (1.15 * mm) * FONT_SIZE_SMALL)
    c.setFont(font, размер)
    c.setFillColor(colors.black)
    през = 1 if лист.day_w >= 2.2 * mm else (5 if лист.day_w >= 1.4 * mm else 10)

    text_y = y - HEADER_H + 2.4 * mm
    for ден in range(ОСТА_ЗАПОЧВА, total_days + 1):
        x1 = _day_to_x(ден, total_days, лист.gantt_left, лист.gantt_width)
        x2 = _day_to_x(ден + 1, total_days, лист.gantt_left, лист.gantt_width)
        if ден > 0 and (ден % през == 0 or ден == 1 or ден == total_days):
            c.drawCentredString((x1 + x2) / 2, text_y, str(ден))
        c.setStrokeColor(HexColor("#CCCCCC"))
        c.setLineWidth(0.15)
        c.line(x1, y, x1, y - HEADER_H)

    # ТУК ЗАПОЧВА ВСИЧКО.  Водещите дни пред ден 1 съществуват само за да се
    # вижда този ръб (изпълнителят, 25.08.2026).
    x_старт = _day_to_x(1, total_days, лист.gantt_left, лист.gantt_width)
    c.setStrokeColor(HexColor("#8B0000"))
    c.setLineWidth(0.8)
    c.line(x_старт, y, x_старт, y - HEADER_H)
    c.setFillColor(HexColor("#8B0000"))
    c.setFont(font, FONT_SIZE_SMALL)
    c.drawString(x_старт + 0.4 * mm, y - 1.6 * mm, "ден 1")
    c.setFillColor(colors.black)

def _дни(стойност) -> str:
    """Продължителност, изписана на български: „45 дни", „1 ден"."""
    try:
        число = float(стойност)
    except (TypeError, ValueError):
        return ""
    цяло = int(число) if float(число).is_integer() else число
    return f"{цяло} ден" if цяло == 1 else f"{цяло} дни"


def _draw_task_row(
    c: canvas.Canvas,
    лист: Лист,
    task: dict,
    y: float,
    row_h: float,
    row_num: int,
    total_days: int,
    show_critical: bool,
    font: str,
    font_bold: str,
    is_sub: bool,
    row_of: dict[str, int] | None = None,
) -> None:
    """Един ред: единайсетте колони вляво, лентата вдясно."""
    row_of = row_of or {}
    x_start = лист.table_left
    text_y = y - row_h + 1.0 * mm
    is_phase = task.get("_is_phase", False)

    if is_phase:
        c.setFont(font_bold, FONT_SIZE + 0.5)
    elif is_sub:
        c.setFont(font, FONT_SIZE_SMALL)
    else:
        c.setFont(font, FONT_SIZE)

    c.setFillColor(colors.black)
    x = x_start + 0.8 * mm

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
        c.drawString(x, text_y, _дни(duration))
    x += COL_DAYS_W

    c.setFont(font, FONT_SIZE_SMALL)
    c.drawString(x, text_y, _подрежи(c, _последователност(task, row_of),
                                     COL_PRED_W - 1 * mm, font, FONT_SIZE_SMALL))
    x += COL_PRED_W

    c.setFont(font if not is_phase else font_bold, FONT_SIZE)
    start_day = task.get("start_day", 0)
    end_day = task.get("end_day", start_day + max(duration, 1) - 1)
    if duration > 0 or start_day:
        c.drawString(x, text_y, f"ден {int(start_day)}")
        c.drawString(x + COL_START_W, text_y, f"ден {int(end_day)}")
    x += COL_START_W + COL_END_W

    # ЕКИП
    c.setFont(font, FONT_SIZE_SMALL)
    екип = str(task.get("team") or task.get("crew_id") or "")
    if екип and екип != "—":
        c.drawString(x, text_y, _подрежи(c, екип, COL_CREW_W - 1 * mm,
                                         font, FONT_SIZE_SMALL))
    x += COL_CREW_W

    # Ресурси — ПОСЛЕДНАТА колона от таблицата, вляво от графиката.
    # „След графичното обозначаване да няма нищо" (изпълнителят, 25.08.2026):
    # вдясно от лентите не се пише нито ресурс, нито име.
    ресурси = ";".join(str(r) for r in (task.get("resources") or []))
    if ресурси:
        c.drawString(x, text_y, _подрежи(c, ресурси, COL_RES_W - 1 * mm,
                                         font, FONT_SIZE_SMALL))
    c.setFont(font, FONT_SIZE)

    c.setStrokeColor(HexColor("#E0E0E0"))
    c.setLineWidth(0.1)
    c.line(x_start, y - row_h, x_start + TABLE_W, y - row_h)

    # --- лентата ---
    task_type = task.get("type", "design")
    is_critical = task.get("is_critical", False) and show_critical

    if duration == 0:
        mx = _day_to_x(start_day, total_days, лист.gantt_left, лист.gantt_width)
        my = y - row_h / 2
        diamond_size = min(2 * mm, row_h / 2)
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

    bar_x = _day_to_x(start_day, total_days, лист.gantt_left, лист.gantt_width)
    bar_end_x = _day_to_x(end_day + 1, total_days, лист.gantt_left, лист.gantt_width)
    bar_w = max(bar_end_x - bar_x, 1)
    bar_y = y - row_h / 2 - BAR_H / 2

    bar_color = COLOR_MAP.get(task_type, HexColor("#4472C4"))
    if is_critical:
        bar_color = CRITICAL_COLOR

    if is_phase:
        c.setFillColor(bar_color)
        c.setFillAlpha(0.3)
        c.rect(bar_x, bar_y - 0.5 * mm, bar_w, BAR_H + 1 * mm, fill=1, stroke=0)
        c.setFillAlpha(1.0)

        c.setStrokeColor(bar_color)
        c.setLineWidth(0.8)
        c.line(bar_x, bar_y + BAR_H + 0.5 * mm, bar_end_x, bar_y + BAR_H + 0.5 * mm)
        c.line(bar_x, bar_y - 0.5 * mm, bar_end_x, bar_y - 0.5 * mm)

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
        c.setFillColor(bar_color)
        if is_sub:
            c.setFillAlpha(0.7)
        c.rect(bar_x, bar_y, bar_w, BAR_H, fill=1, stroke=0)
        c.setFillAlpha(1.0)

        if is_critical:
            c.setStrokeColor(HexColor("#8B0000"))
            c.setLineWidth(0.8)
            c.rect(bar_x, bar_y, bar_w, BAR_H, fill=0, stroke=1)

def _draw_phase_separator(
    c: canvas.Canvas,
    лист: Лист,
    schedule_data: list[dict],
    total_days: int,
    content_top: float,
    content_bottom: float,
    font: str,
) -> None:
    """Пунктирът, на който свършва проектирането и тръгва строителството."""
    design_end = 0
    for task in schedule_data:
        if task.get("phase") == "design":
            end = task.get("end_day", task.get("start_day", 0) + task.get("duration", 0))
            design_end = max(design_end, end)

    if design_end <= 0:
        return

    x = _day_to_x(design_end, total_days, лист.gantt_left, лист.gantt_width)
    c.setStrokeColor(CRITICAL_COLOR)
    c.setLineWidth(0.5)
    c.setDash(3, 2)
    c.line(x, content_top, x, content_bottom)
    c.setDash()

    c.setFont(font, FONT_SIZE_SMALL)
    c.setFillColor(CRITICAL_COLOR)
    c.drawCentredString(x, content_top + 1 * mm, "Протокол обр.2")

def _draw_day_grid(c: canvas.Canvas, лист: Лист, total_days: int,
                   top_y: float, bottom_y: float) -> None:
    """Дневната решетка под скалата, с по-тъмна черта на всеки десети ден."""
    for ден in range(ОСТА_ЗАПОЧВА, total_days + 2):
        x = _day_to_x(ден, total_days, лист.gantt_left, лист.gantt_width)
        десети = ден > 0 and ден % 10 == 0
        c.setStrokeColor(HexColor("#D0D0D0") if десети else HexColor("#EFEFEF"))
        c.setLineWidth(0.2 if десети else 0.1)
        c.line(x, top_y, x, bottom_y)

    x_старт = _day_to_x(1, total_days, лист.gantt_left, лист.gantt_width)
    c.setStrokeColor(HexColor("#8B0000"))
    c.setLineWidth(0.5)
    c.line(x_старт, top_y, x_старт, bottom_y)

def _draw_legend(
    c: canvas.Canvas,
    лист: Лист,
    font: str,
    font_bold: str,
    schedule_data: list[dict],
) -> None:
    """Легендата — под таблицата, вляво.  Вдясно от графиката няма нищо."""
    y = BOTTOM_MARGIN - 2 * mm
    x = лист.table_left

    c.setStrokeColor(colors.black)
    c.setLineWidth(0.5)
    c.line(x, y + 6 * mm, лист.page_w - RIGHT_MARGIN, y + 6 * mm)

    c.setFont(font_bold, FONT_SIZE)
    c.setFillColor(colors.black)
    c.drawString(x, y + 1 * mm, "ЛЕГЕНДА:")
    x += 18 * mm

    present_types = set()
    for task in schedule_data:
        present_types.add(task.get("type", ""))
        for sub in task.get("sub_activities", []):
            present_types.add(sub.get("type", ""))

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
