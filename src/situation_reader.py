"""Отсечките на мрежата — прочетени от тръжните чертежи, детерминистично.

ЗАЩО.  Количествата казват КОЛКО, но не казват КЪДЕ.  Еталонният човешки
график кръщава пакетите „кл. 48 от РШ 36 до Пр. Ш 1" — клон плюс двата възела —
а такова нещо в количествената таблица няма.  Дотук единственият четец на
ситуация беше `ai_processor.extract_situation_segments`: рендира чертежа и пита
vision модел.  Той се проваляше — виж бележките от 21.08.2026 — но не защото
данните ги няма, а защото беше грешният инструмент.

ЧЕРТЕЖИТЕ СА ВЕКТОРНИ, НЕ СКАНИРАНИ.  Текстът е машинно четим с координати,
линиите носят цвят, а легендата казва кой цвят какво значи.  Затова тук няма
модел: има четене.

ДВА ИЗТОЧНИКА, ЗАЩОТО ДВЕТЕ ЧАСТИ СА ПИСАНИ РАЗЛИЧНО:

    КАНАЛИЗАЦИЯ   ситуационният чертеж носи тройки етикети до самата линия —
                  „Кл.48" / „DN 700" / „L=618.74м".  Възлите (РШ) са отделни
                  етикети.  Обхватът се решава по ЦВЕТА на линията под етикета,
                  сверен с легендата.
    ВОДОПРОВОД    оразмерителната таблица носи готови колони: КЛОН №, Начална
                  точка, Крайна точка, действ. дължина, D [mm].  Там няма какво
                  да се мери — има какво да се прочете.

ОБХВАТЪТ Е ФИЛТЪР, НЕ ПОДРОБНОСТ.  Инвестиционният чертеж показва И съседните
мрежи: следващ етап, друг проект, съществуващи.  Без филтъра сборът по DN 300
излиза 2736 м срещу 1182 по спецификация (+131 %); с него — 1152 м (−2.5 %), а
броят отсечки пада от 68 на 46, колкото канализационни участъка има еталонният
човешки график.  Измерено 21.08.2026.

ЧЕТЕНЕТО НЕ Е ДОКАЗАТЕЛСТВО.  Дължините тук са от чертежа и служат за
РАЗЧЛЕНЯВАНЕ.  Договорното количество си остава онова от спецификацията —
всяка отсечка носи `source`, за да се вижда откъде идва числото.
"""

from __future__ import annotations

import logging
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)


class Segment(NamedTuple):
    """Една отсечка от мрежата — това, което прави участъка участък."""

    network: str          #: „В" водопровод | „К" канализация
    branch: str           #: „Кл.48" / „ГЛ.КЛ. I"
    start_node: str       #: начален възел, ако документът го дава
    end_node: str
    length_m: float
    dn: int
    street: str
    source: str           #: документът, от който е прочетена
    in_scope: bool        #: влиза ли в ТАЗИ процедура (по легендата)
    scope_reason: str     #: КОЯ легендна позиция го реши


# ---------------------------------------------------------------------------
# Легендата: цвят на перото → какво значи → влиза ли в процедурата
# ---------------------------------------------------------------------------

#: Ред от легендата се познава по тези думи.  Списъкът е нарочно тесен: всичко
#: друго в чертежа (заглавен блок, бележки) не е легенда.
_LEGEND_MARKERS = (
    "инвестиционна програма", "по друг проект", "съществуваща",
    "дъждовна канализация", "нова дъждовна", "битов канал",
)

#: Подредени правила текст-на-легендата → влиза ли.  ПЪРВОТО съвпадение печели,
#: затова изключващите стоят отпред: „Инвестиционна програма за битов канал —
#: СЛЕДВАЩ ЕТАП" съдържа и „инвестиционна програма", и „следващ етап".
_SCOPE_RULES = (
    (False, ("следващ етап", "друг проект", "съществуващ", "отпада")),
    (True, ("инвестиционна програма", "за инвестиция", "нова ")),
)


def _scope_of(text: str) -> tuple[bool | None, str]:
    """Дали легендна позиция влиза в процедурата.  None = не се разпознава."""
    low = text.lower()
    for влиза, думи in _SCOPE_RULES:
        for дума in думи:
            if дума in low:
                return влиза, text.strip()
    return None, text.strip()


def _lines_with_boxes(page: Any) -> list[tuple[str, tuple[float, ...]]]:
    out: list[tuple[str, tuple[float, ...]]] = []
    for блок in page.get_text("dict")["blocks"]:
        for ред in блок.get("lines", []):
            текст = "".join(с["text"] for с in ред.get("spans", [])).strip()
            if текст:
                out.append((текст, tuple(ред["bbox"])))
    return out


def read_legend(page: Any) -> dict[tuple[float, ...], tuple[bool, str]]:
    """Цвят на перото → (влиза ли, текстът на легендата).

    Мострата е късо цветно чертежче ВЛЯВО от текста, на същата височина —
    така се чертаят легендите в AutoCAD и така се четат от човек.

    Returns:
        Речник цвят → (влиза, обяснение).  Празен, ако легенда няма.
    """
    пътища = [d for d in page.get_drawings() if d.get("color")]
    легенда: dict[tuple[float, ...], tuple[bool, str]] = {}

    for текст, (x0, y0, x1, y1) in _lines_with_boxes(page):
        if not any(m in текст.lower() for m in _LEGEND_MARKERS):
            continue
        влиза, обяснение = _scope_of(текст)
        if влиза is None:
            continue
        най_близка: tuple[float, tuple[float, ...]] | None = None
        for d in пътища:
            bx0, by0, bx1, by1 = d["rect"]
            if by1 < y0 - 4 or by0 > y1 + 4:      # не е на същия ред
                continue
            if bx1 > x0 + 2:                      # мострата е ВЛЯВО от текста
                continue
            разстояние = x0 - bx1
            if най_близка is None or разстояние < най_близка[0]:
                най_близка = (разстояние, _pen(d["color"]))
        if най_близка is not None:
            легенда.setdefault(най_близка[1], (влиза, обяснение))

    logger.info("Легенда: %d цвята с обяснение", len(легенда))
    return легенда


def _pen(color: Any) -> tuple[float, ...]:
    """Цветът като сравним ключ — закръглен, за да не се дели на нюанси."""
    return tuple(round(float(c), 2) for c in color)


# ---------------------------------------------------------------------------
# Канализация: етикети до линията, обхват по цвета на линията
# ---------------------------------------------------------------------------

#: „Кл.48", „Кл.30а" — името на клона стои само на своя ред.
_BRANCH = re.compile(r"^Кл\.\s*(\d+[а-яa-z]?)$", re.IGNORECASE)
_DN = re.compile(r"\bDN\s*(\d+)", re.IGNORECASE)
_LENGTH = re.compile(r"\bL\s*=\s*([\d.,]+)\s*м", re.IGNORECASE)
#: Възел: „РШ N12", „Пр.Ш 1", „ОТ 43".
_NODE = re.compile(r"^(РШ|Пр\.?\s*Ш|ОТ|OT)\s*[NН]?\s*[\d]+[а-яa-zA-Z]?$",
                   re.IGNORECASE)
_STREET = re.compile(r"^(ул\.|бул\.|пл\.)\s*(.+)$", re.IGNORECASE)


def _segments_of(drawing: dict) -> list[tuple[float, float, float, float]]:
    """Отсечките на едно чертежче, като четворки координати."""
    out = []
    for оп in drawing["items"]:
        if оп[0] == "l":
            out.append((оп[1].x, оп[1].y, оп[2].x, оп[2].y))
        elif оп[0] == "re":
            r = оп[1]
            out.append((r.x0, r.y0, r.x1, r.y1))
    return out


def _distance(px: float, py: float, s: tuple[float, ...]) -> float:
    ax, ay, bx, by = s
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _nearest_street(cx: float, cy: float,
                    улици: list[tuple[str, float, float]]) -> str:
    if not улици:
        return ""
    име, _, _ = min(улици, key=lambda u: math.hypot(cx - u[1], cy - u[2]))
    return име


def read_sewer_situation(path: str | Path) -> list[Segment]:
    """Отсечките на канализацията от ситуационен чертеж.

    Етикетът е тройка последователни реда — име на клон, диаметър, дължина —
    поставена до самата линия.  Обхватът се решава по цвета на НАЙ-БЛИЗКАТА
    мрежова линия, сверен с легендата на същия чертеж.

    Args:
        path: PDF на ситуацията.

    Returns:
        Списък `Segment`; празен, ако чертежът е нечетим или без легенда.
    """
    try:
        import fitz
    except ImportError:
        logger.warning("PyMuPDF липсва — ситуацията се пропуска.")
        return []

    try:
        doc = fitz.open(str(path))
    except Exception as exc:                       # noqa: BLE001 — чужд формат
        logger.error("Ситуацията %s не се отваря: %s", path, exc)
        return []

    име_на_документа = Path(path).name
    отсечки: list[Segment] = []

    with doc:
        for page in doc:
            легенда = read_legend(page)
            if not легенда:
                continue

            мрежови: dict[tuple[float, ...], list] = defaultdict(list)
            for d in page.get_drawings():
                цвят = d.get("color")
                if цвят and _pen(цвят) in легенда:
                    мрежови[_pen(цвят)].extend(_segments_of(d))
            if not мрежови:
                continue

            редове = _lines_with_boxes(page)
            улици = [(m.group(0), (b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
                     for т, b in редове if (m := _STREET.match(т))]

            for i, (текст, bbox) in enumerate(редове):
                клон = _BRANCH.match(текст)
                if not клон:
                    continue
                опашка = " ".join(т for т, _ in редове[i + 1:i + 3])
                dn, дължина = _DN.search(опашка), _LENGTH.search(опашка)
                if not (dn and дължина):
                    continue

                cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
                най = (float("inf"), None)
                for цвят, сегменти in мрежови.items():
                    for s in сегменти:
                        d = _distance(cx, cy, s)
                        if d < най[0]:
                            най = (d, цвят)
                влиза, причина = легенда.get(най[1], (False, "без легенда"))

                отсечки.append(Segment(
                    network="К",
                    branch=f"Кл.{клон.group(1)}",
                    start_node="", end_node="",
                    length_m=float(дължина.group(1).replace(",", ".")),
                    dn=int(dn.group(1)),
                    street=_nearest_street(cx, cy, улици),
                    source=име_на_документа,
                    in_scope=влиза,
                    scope_reason=причина,
                ))

    в_обхват = sum(1 for о in отсечки if о.in_scope)
    logger.info("Ситуация %s: %d отсечки, %d в обхвата на процедурата",
                име_на_документа, len(отсечки), в_обхват)
    return отсечки


# ---------------------------------------------------------------------------
# Водопровод: оразмерителната таблица дава отсечките наготово
# ---------------------------------------------------------------------------

#: Заглавие на колона → роля.  Достатъчна е ПЪРВАТА дума: заглавията се пренасят
#: на два реда („Крайна" / „точка") и само първата е сигурна.
_WATER_COLUMNS = (
    ("клон", "branch"),
    ("начална", "start"),
    ("крайна", "end"),
    ("действ", "length"),
    ("d", "dn"),
)

_WATER_ROW = re.compile(r"^(ГЛ\.)?КЛ\.", re.IGNORECASE)


def _header_columns(header_words: list[tuple]) -> list[tuple[float, float, str]]:
    """Колоните на таблицата: (ляв край, десен край, заглавие).

    ВСИЧКИ колони, не само търсените.  Ако се вземат само те, границата преди
    „D [mm]" пада насред четирите колони между тях и в диаметъра влиза числото
    на съседно поле — измерено 21.08.2026: DN излизаше 63, 27, 16 (това са
    оразмерителните дебити).

    Заглавието се пренася на два реда („действ." / „дължина") и се пише с
    интервали („КЛОН №"), затова думите се слепват по две правила: на един ред
    — когато са долепени; между редовете — когато се застъпват по хоризонтала.
    """
    редове: dict[int, list[tuple]] = defaultdict(list)
    for w in header_words:
        редове[round(w[1] / 4)].append(w)

    групи: list[list[float | str]] = []            # [ляв, десен, текст]
    for y in sorted(редове):
        for w in sorted(редове[y], key=lambda w: w[0]):
            if групи and w[0] - групи[-1][1] < 4 and групи[-1][3] == y:
                групи[-1][1] = w[2]
                групи[-1][2] = f"{групи[-1][2]} {w[4]}"
            else:
                групи.append([w[0], w[2], w[4], y])

    слети: list[list[float | str]] = []
    for ляв, десен, текст, _ in sorted(групи, key=lambda g: g[0]):
        застъпена = next((s for s in слети if ляв < s[1] and десен > s[0]), None)
        if застъпена:
            застъпена[0] = min(застъпена[0], ляв)
            застъпена[1] = max(застъпена[1], десен)
            застъпена[2] = f"{застъпена[2]} {текст}"
        else:
            слети.append([ляв, десен, текст])
    return [(g[0], g[1], str(g[2])) for g in sorted(слети, key=lambda g: g[0])]


def _roles_of(колони: list[tuple[float, float, str]]) -> list[tuple[float, str]]:
    """Границите между колоните + ролята на всяка от търсените.

    Границата е по средата между ДЕСНИЯ край на едната и ЛЕВИЯ на следващата —
    не между левите им краища.  „КЛОН №" стига до x≈85, а „Начална точка"
    започва на 96.8; средата между левите краища е 76 и римското „I" от името
    на клона попада в грешната колона.
    """
    граници: list[tuple[float, str]] = []
    for i, (ляв, _десен, текст) in enumerate(колони):
        начало = ((колони[i - 1][1] + ляв) / 2) if i else float("-inf")
        # Сравнява се ВСЯКА дума от заглавието, не началото на слепения надпис:
        # заглавието се пренася на два реда и словоредът му се обръща, когато
        # долният ред започва по-вляво („дължина действ." вместо „действ.
        # дължина").  Освен това се съкращава с точка, затова — по начало.
        думи = текст.lower().strip().split()
        роля = next((r for з, r in _WATER_COLUMNS
                     if any(д.strip("№[]. ").startswith(з) for д in думи)), "")
        граници.append((начало, роля))
    return граници


def _assign(x: float, граници: list[tuple[float, str]]) -> str | None:
    """В коя колона попада дума с център `x`.  Празен низ = колона без роля."""
    роля: str | None = None
    for начало, име in граници:
        if x >= начало:
            роля = име
    return роля or None


def read_water_table(path: str | Path) -> list[Segment]:
    """Отсечките на водопровода от оразмерителната таблица.

    Таблицата дава клон, двата възела, действителната дължина и диаметъра —
    тоест целия участък, без да се мери каквото и да е.

    Args:
        path: PDF на оразмерителната таблица.

    Returns:
        Списък `Segment`.  Всички са `in_scope=True`: таблицата описва само
        проектираната мрежа, за разлика от ситуационния чертеж.
    """
    try:
        import fitz
    except ImportError:
        logger.warning("PyMuPDF липсва — оразмерителната таблица се пропуска.")
        return []

    try:
        doc = fitz.open(str(path))
    except Exception as exc:                       # noqa: BLE001
        logger.error("Таблицата %s не се отваря: %s", path, exc)
        return []

    име_на_документа = Path(path).name
    отсечки: list[Segment] = []

    # ТАБЛИЦАТА ПРОДЪЛЖАВА, ХЕДЪРЪТ — НЕ.  Оразмерителната таблица на реалния
    # проект е 3 страници, а заглавният ред е само на първата.  Докато
    # границите се търсеха на всяка страница поотделно, 50 от 89 отсечки се
    # изхвърляха мълчаливо (измерено 21.08.2026).  Затова колоните веднъж
    # намерени важат и за следващите страници — както ги чете и човек.
    граници: list[tuple[float, str]] = []

    with doc:
        for page in doc:
            думи = page.get_text("words")
            if not думи:
                continue

            # Хедърът се пренася на няколко реда, затова се събира по ЛЕНТА
            # височина, а не по един ред: първата дума „клон" дава лентата.
            заглавни = [w for w in думи if w[4].lower().strip("№[]. ") == "клон"]
            if заглавни:
                y_хедър = заглавни[0][1]
                лента = [w for w in думи if abs(w[1] - y_хедър) <= 14]
                кандидат = _roles_of(_header_columns(лента))
                липсва = ({r for _, r in _WATER_COLUMNS}
                          - {р for _, р in кандидат if р})
                if липсва:
                    logger.warning("Таблица %s: не са намерени колони %s",
                                   име_на_документа, sorted(липсва))
                else:
                    граници = кандидат
            elif граници:
                y_хедър = float("-inf")        # продължение: цялата страница е данни
            if not граници:
                continue

            по_ред: dict[int, list] = defaultdict(list)
            for w in думи:
                if w[1] > y_хедър + 14:
                    по_ред[round(w[1] / 4)].append(w)

            for y in sorted(по_ред):
                клетки = sorted(по_ред[y], key=lambda w: w[0])
                if not _WATER_ROW.match(" ".join(w[4] for w in клетки)):
                    continue
                поле: dict[str, list[str]] = defaultdict(list)
                for w in клетки:
                    роля = _assign((w[0] + w[2]) / 2, граници)
                    if роля:
                        поле[роля].append(w[4])
                try:
                    дължина = float(поле["length"][0].replace(",", "."))
                    dn = int(float(поле["dn"][0].replace(",", ".")))
                except (KeyError, IndexError, ValueError):
                    continue
                отсечки.append(Segment(
                    network="В",
                    branch=" ".join(поле.get("branch", [])),
                    start_node=" ".join(поле.get("start", [])),
                    end_node=" ".join(поле.get("end", [])),
                    length_m=дължина,
                    dn=dn,
                    street="",
                    source=име_на_документа,
                    in_scope=True,
                    scope_reason="оразмерителна таблица на проекта",
                ))

    logger.info("Таблица %s: %d отсечки", име_на_документа, len(отсечки))
    return отсечки


def summarize(segments: list[Segment]) -> dict[str, Any]:
    """Сборът по диаметър — за сверка срещу количествата от спецификацията."""
    по_dn: dict[int, list] = defaultdict(lambda: [0, 0.0])
    for о in segments:
        if not о.in_scope:
            continue
        по_dn[о.dn][0] += 1
        по_dn[о.dn][1] += о.length_m
    return {
        "segments": sum(1 for о in segments if о.in_scope),
        "dropped": sum(1 for о in segments if not о.in_scope),
        "by_dn": {dn: {"count": n, "length_m": round(L, 2)}
                  for dn, (n, L) in sorted(по_dn.items())},
        "total_m": round(sum(L for _, L in по_dn.values()), 2),
    }
