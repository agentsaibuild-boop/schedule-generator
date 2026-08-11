"""Deterministic duration calculation — the arithmetic the LLM used to do.

WHY THIS MODULE EXISTS (P2 от REVISION_2026-07.md):
Продължителностите се смятаха вътре в промпта — `ceil(length/rate)`, Долноград
tier lookup, СРС/РШ бройки, дни дезинфекция.  LLM аритметиката е тиха и
недетерминистична: смяна на модел мълчаливо променя числата, а урок #40
(параметрични продължителности) и #35 (CI ≠ PE) се нарушават без следа.

Тук същите правила са код със ЕДИН източник на истина:
`config/productivities.json` (v0.4, верифицирани — урок #18).

ИЗВЕСТНО РАЗМИНАВАНЕ (решено 2026-07-22, не е бъг): ACCURACY.md сочи
~14.6–15.9 м/д за DN90 при проект Опитно, а конфигът дава 12 м/д.
Опитно е канализационен проект, конфигът мери водопровод — числата НЕ се
сливат (урок #42).  Резултатът е консервативен (по-дълъг) график.  Виж
бележката в ACCURACY.md, раздел „Опитно".

Източници на всяко число са цитирани в коментар до него.  Нищо в този
модул не гадае: ако материалът или DN не могат да се установят СИГУРНО,
изчислението се пропуска и се връща причина — по-добре е да остане
стойността на LLM-а, отколкото да сметнем DN300 CI по тарифа за PE
(урок #35: CI и PE имат различни норми, DN300 PE изобщо няма норма).
"""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "productivities.json"

# Минимум работни дни за параметрична дейност (мобилизация/логистика).
# Стойността идва от досегашния промпт в ai_processor.py.
DEFAULT_MIN_DAYS = 5

# Производителности по брой (не по дължина) — от промпта в ai_processor.py.
COUNT_RATES: dict[str, float] = {
    "srs": 5.0,   # СРС (сградни ревизионни шахти) — 5 бр./ден
    "rsh": 2.0,   # РШ (ревизионни шахти) голям DN — 2 бр./ден
    "svo": 4.0,   # СВО (сградни водопроводни отклонения) — 4 бр./ден (полево, 2026-08)
    "sko": 4.0,   # СКО (сградни канализационни отклонения) — 4 бр./ден (полево, 2026-08)
}

# Изпитване на водопровод = 2 дни (урок #34: якост + спад на налягане,
# двата дни са задължителни поотделно).
WATER_TEST_DAYS = 2

# ---------------------------------------------------------------------------
# Фактори на условията (одит 2026-07-23, точка 4)
# ---------------------------------------------------------------------------
# Продължителността на ВиК работа не е функция само на length + DN + material
# + method.  Одитът изброи: дълбочина, категория почва, подземни води,
# укрепване, градска среда, трафик, работен фронт, състав на бригадата.
#
# Всеки фактор УМНОЖАВА времето (>1 = по-бавно).  Стойностите са начални
# инженерни оценки, НЕ верифицирани срещу проекти — затова:
#   1. по подразбиране НЕ се прилагат (`apply_conditions=False`);
#   2. всеки приложен фактор се записва в резултата, за да се вижда какво го
#      е удължило и защо.
#
# Верифицирането им иска данни от реални обекти, както productivities.json.
CONDITION_FACTORS: dict[str, dict[str, float]] = {
    "soil": {                    # категория почва
        "loose": 0.9, "normal": 1.0, "dense": 1.25,
        "rocky": 1.8, "rock": 2.2,
    },
    "depth": {                   # дълбочина на изкопа
        "shallow": 0.9,          # до 1.5 м
        "normal": 1.0,           # 1.5–2.5 м
        "deep": 1.35,            # 2.5–4 м — иска укрепване
        "very_deep": 1.7,        # над 4 м
    },
    "groundwater": {             # подземни води
        "none": 1.0, "present": 1.3, "heavy": 1.6,   # heavy = водочерпене
    },
    "shoring": {                 # укрепване
        "none": 1.0, "simple": 1.15, "sheet_piling": 1.45,
    },
    "environment": {             # среда
        "open": 1.0, "urban": 1.25, "city_centre": 1.5,
    },
    "traffic": {                 # временна организация на движението
        "none": 1.0, "partial": 1.15, "full_closure": 1.05, "under_traffic": 1.4,
    },
    "utilities": {               # съществуващи подземни комуникации
        "none": 1.0, "some": 1.2, "dense": 1.5,      # ръчен изкоп около тях
    },
}

# Долноград tier стълба (урок #45 / ACCURACY.md).  Праговете са по Act2+Act3.
_VRATSA_LADDER = (6, 7, 9, 10)
_VRATSA_THRESHOLDS = ((1.0, 6), (2.0, 7), (3.5, 9))

# ---------------------------------------------------------------------------
# Нормализация на вход
# ---------------------------------------------------------------------------

# Диаметърът в българските КСС се пише почти винаги с „Ф" (кирилско/латинско)
# или знака за диаметър Ø/⌀ — напр. „Ф300", „Ф 1000", „Ф90".  Без тези
# префикси всеки канализационен ред (Ф300–Ф1200 РP) падаше в MISSING_DN
# (наблюдавано при жив тест на реален търг, 2026-08).  IGNORECASE покрива
# и малки букви (ф, φ, ø).
_DN_RE = re.compile(r"(?:DN|Ф|Φ|Ø|⌀)\s*[-–]?\s*(\d{2,4})", re.IGNORECASE)
_BARE_DN_RE = re.compile(r"^\s*(\d{2,4})\s*$")

# Материалите се разпознават само по ЕДНОЗНАЧНИ маркери.  „ПЕ" не се търси
# като самостоятелни две букви, защото се среща вътре в думи.
_MATERIAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("CI", re.compile(r"\bCI\b|чугун|ковък\s+чугун|сив\s+чугун", re.IGNORECASE)),
    ("PVC", re.compile(r"\bPVC\b|\bПВЦ\b|поливинил", re.IGNORECASE)),
    # PEHD / HDPE / ПЕВП — стандартните имена на полиетилена ВИСОКА ПЛЪТНОСТ,
    # с които е написан почти всеки български водопроводен КСС ред.
    #
    # ПРОБА 10.08.2026: `\bPE\b` изисква граница СЛЕД „PE", а в „PEHD" такава
    # няма — тоест „Доставка и полагане на тръби PEHD DN110" оставаше
    # MISSING_MATERIAL.  Това не е крайна дейност: полагането на тръбата е
    # ГРЪБНАКЪТ на водопроводния участък и без него участъкът няма нито една
    # доказана продължителност.
    ("PE", re.compile(
        r"\bPEHD\b|\bHDPE\b|\bPE\s*-?\s*(?:HD|100|80)\b"
        r"|\bПЕВП\b|\bПЕ\s*-?\s*ВП\b"
        r"|\bPE\b|\bПЕ\b|полиетилен",
        re.IGNORECASE)),
    # PP (полипропилен) — стандартният материал за канализация в българските
    # КСС („Ф300, РP", „Ф1000, РP").  „РP" минава през normalize_homoglyphs
    # (кирилско Р → латинско P) и става „PP".  Без този шаблон цялата
    # канализационна мрежа оставаше MISSING_MATERIAL (жив тест, 2026-08).
    ("PP", re.compile(r"\bPP\b|\bПП\b|полипропил", re.IGNORECASE)),
    ("AC", re.compile(r"\bAC\b|азбест", re.IGNORECASE)),
    ("GRP", re.compile(r"\bGRP\b|стъклопласт", re.IGNORECASE)),
)

# Единен източник на истина за разпознаваните материали.  Ползва се и от
# промпта в ai_processor (enum) и от JSON schema-та — за да НЕ се разминават
# (жив тест 2026-08: промптът разрешаваше само PE/CI/PVC/AC/GRP, БЕЗ PP →
# моделът пишеше „PE" за PP канализация, защото PP не му беше позволен).
SUPPORTED_MATERIALS: tuple[str, ...] = tuple(m for m, _ in _MATERIAL_PATTERNS)

_HDD_RE = re.compile(
    r"безизкоп|HDD|сондаж|сондир|хоризонтал\w*\s+сонд|pipe\s*burst|микротунел",
    re.IGNORECASE,
)

# Задачи, които са полагане на тръба — само те се смятат параметрично по
# дължина.  Изкоп/засипка/асфалт имат собствени норми, които НЕ са в
# productivities.json, и съзнателно не се пипат.
_PIPE_TASK_RE = re.compile(
    r"полаган|тръбопровод|тръби|водопровод|канализац|колектор|клон|тласкател",
    re.IGNORECASE,
)
_PIPE_TYPES = frozenset({"water_pipe", "sewer", "pipe", "pipeline"})

#: Мерни единици, които значат ДЪЛЖИНА.  Един източник за двете места, които
#: питат: `detect_length_m` (колко метра) и `is_pipe_task` (изобщо метри ли са).
#: „m3/m'" НЕ е тук — съставната единица не е дължина, колкото и да ѝ прилича.
_LENGTH_UNITS = frozenset({
    "м", "m", "м.", "m.", "м'", "m'", "метър", "метра", "лм", "лм.",
    "l.m", "lm",
})

_SRS_RE = re.compile(r"\bСРС\b|сградн\w*\s+ревизион", re.IGNORECASE)
_RSH_RE = re.compile(r"\bРШ\b|ревизионн\w*\s+шахт", re.IGNORECASE)
# Сградни отклонения (не са шахти) — водопроводни (СВО) и канализационни (СКО).
# Норма 4 бр/ден по полево правило на изпълнителя (2026-08).  СКО се проверява
# ПРЕДИ СВО в диспечера няма значение — шаблоните са взаимно изключващи се.
_SVO_RE = re.compile(r"\bСВО\b|сградн\w*\s+водопроводн\w*\s+отклонени", re.IGNORECASE)
_SKO_RE = re.compile(r"\bСКО\b|сградн\w*\s+канализационн\w*\s+отклонени", re.IGNORECASE)

_FOREST_RE = re.compile(r"горск|залесен|скален", re.IGNORECASE)
_ASPHALT_RE = re.compile(r"асфалт|настилк|урбанизиран", re.IGNORECASE)

# Площни възстановявания (кв.м) и линейни не-тръбни (бордюри).  Нормите са
# в config (area_productivities / linear_productivities); тук само
# разпознаваме типа.  Плочите се проверяват ПРЕДИ асфалта, защото
# „тротоарни плочи" не съдържа „асфалт", но пази реда ясен.
_PAVERS_RE = re.compile(r"унипаваж|плочи|тротоарн\w*\s+плоч", re.IGNORECASE)
_KERB_RE = re.compile(r"бордюр", re.IGNORECASE)
_AREA_UNITS = frozenset({"кв.м", "кв. м", "м2", "м²", "m2", "квм", "sq.m"})

# Кирилски букви, визуално идентични с латински.  Ползват се за поправяне
# на OCR грешки при разпознаване на материала — виж `normalize_homoglyphs`.
_HOMOGLYPHS: dict[str, str] = {
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X", "І": "I", "Ѕ": "S",
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
}
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


class RateLookup(NamedTuple):
    """Резултат от търсене на производителност."""

    rate: float          # ефективна производителност, м/ден
    key: str             # ключът, по който е намерена
    source: str          # винаги "config" — единствен източник на истина


# Машинно проверими причини защо продължителността не е изчислена.
# Одит 2026-07-23: досега имаше само текстово обяснение, а полето `duration`
# съдържаше или изчислена, или предположена от LLM стойност — неразличими.
# Кодовете позволяват на извикващия да реши какво да прави с всеки случай.
CODE_OK = "CALCULATED"
CODE_MILESTONE = "MILESTONE"
CODE_NOT_PARAMETRIC = "NOT_PARAMETRIC"        # изкоп, извозване, настилки
CODE_MISSING_LENGTH = "MISSING_LENGTH"
CODE_MISSING_DN = "MISSING_DN"
CODE_MISSING_MATERIAL = "MISSING_MATERIAL"
CODE_NO_RULE = "NO_PRODUCTIVITY_RULE"
CODE_COUNT_NO_RATE = "COUNT_NO_RATE"

# Кодове, при които задачата НЯМА доказана продължителност.
UNRESOLVED_CODES = frozenset({
    CODE_NOT_PARAMETRIC, CODE_MISSING_LENGTH, CODE_MISSING_DN,
    CODE_MISSING_MATERIAL, CODE_NO_RULE, CODE_COUNT_NO_RATE,
})


class DurationResult(NamedTuple):
    """Резултат от изчисление на продължителност."""

    days: int | None     # None = не може да се сметне детерминистично
    reason: str          # човешко обяснение (за одит/лог) — винаги попълнено
    rate: float | None = None
    rate_key: str | None = None
    code: str = CODE_OK  # машинно проверима причина


# ---------------------------------------------------------------------------
# Зареждане на конфига
# ---------------------------------------------------------------------------

_cache: dict[str, Any] = {}


def load_productivities(path: str | Path | None = None, *, reload: bool = False) -> dict:
    """Зареди config/productivities.json (кеширано).

    Args:
        path: Алтернативен път (за тестове).
        reload: Игнорирай кеша.

    Returns:
        Целият конфиг като dict.  Празен dict, ако файлът липсва/е невалиден.
    """
    target = Path(path) if path else _CONFIG_PATH
    cache_key = str(target)

    if not reload and cache_key in _cache:
        return _cache[cache_key]

    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("productivities.json не е обект")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.error(
            "Не мога да заредя %s: %s — НИКОЯ продължителност няма да се преизчисли "
            "детерминистично; графикът ще ползва стойностите на LLM-а.", target, exc,
        )
        data = {}

    _cache[cache_key] = data
    return data


def clear_cache() -> None:
    """Изчисти кеша на конфига (за тестове)."""
    _cache.clear()


# ---------------------------------------------------------------------------
# Разпознаване на параметри от задача
# ---------------------------------------------------------------------------

def normalize_dn(value: Any) -> int | None:
    """Извлечи числов DN от 300, '300', 'DN300', 'DN 300' и подобни."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        dn = int(value)
        return dn if dn > 0 else None

    text = str(value)
    match = _DN_RE.search(text) or _BARE_DN_RE.match(text)
    if match:
        return int(match.group(1))
    return None


def detect_dn(task: dict) -> int | None:
    """DN от полетата diameter/dn/DN, иначе от името на задачата.

    Схемата не е единна: промптът иска 'dn', а schedule_builder чете
    'diameter'.  Приемаме и двете.
    """
    for field in ("diameter", "dn", "DN", "nominal_diameter"):
        dn = normalize_dn(task.get(field))
        if dn:
            return dn
    return normalize_dn(task.get("name", ""))


def normalize_homoglyphs(text: str) -> str:
    """Замени кирилски букви с латинските им двойници САМО в токени,
    които се състоят изцяло от такива двойници.

    Защо: OCR на български документи бърка визуално идентичните букви —
    „PE 100 RC" се връща като „РЕ 100 RC" с кирилско Р и Е (наблюдавано
    при жив тест на 2026-07-22).  Латинското търсене на материала тогава
    не съвпада, материалът остава неразпознат и продължителността се
    пропуска.  Една сгрешена буква изключва детерминистичното изчисление.

    Ограничението „целият токен да е от двойници" пази истинските
    български думи: „Разваляне" съдържа „а", „з" — не се пипа.
    """
    def _convert(match: re.Match[str]) -> str:
        token = match.group(0)
        # Смесените случаи са реални: OCR връща „СI" с кирилско С и латинско I.
        # Затова приемаме токен, чиито знаци са или двойници, или вече латиница.
        if not any(ch in _HOMOGLYPHS for ch in token):
            return token
        if all(ch in _HOMOGLYPHS or ch.isascii() for ch in token):
            return "".join(_HOMOGLYPHS.get(ch, ch) for ch in token)
        return token

    return _TOKEN_RE.sub(_convert, text)


def detect_material(task: dict) -> str | None:
    """Материал (PE/CI/PVC/AC/GRP) от поле или име.  None = неустановен.

    СЪЗНАТЕЛНО не гадае.  Урок #35: CI и PE имат различни норми (8 срещу
    15 м/д) — грешно предположение тук е по-скъпо от пропуснато изчисление.
    """
    # Материалът в българските КСС често стои в диаметърната клетка, не в
    # името: „Ф300, РP", „Ф90; РЕ".  Затова гледаме и diameter/dn/описание,
    # а не само material/name (жив тест на реален търг, 2026-08).
    parts = (
        task.get("material"), task.get("pipe_material"), task.get("name"),
        task.get("diameter"), task.get("dn"), task.get("description"),
    )
    haystack = " ".join(str(p) for p in parts if p)
    haystack = f"{haystack} {normalize_homoglyphs(haystack)}"

    for material, pattern in _MATERIAL_PATTERNS:
        if pattern.search(haystack):
            return material
    return None


def detect_method(task: dict) -> str:
    """Метод на полагане: 'HDD' (безизкопно) или 'open' (открит изкоп)."""
    explicit = str(task.get("method") or "")
    haystack = f"{explicit} {task.get('name', '')}"
    return "HDD" if _HDD_RE.search(haystack) else "open"


def detect_terrain(task: dict, default: str = "dirt_road") -> str:
    """Терен: forest / asphalt / dirt_road.  Ползва се само ако е поискано явно."""
    explicit = str(task.get("terrain") or "")
    haystack = f"{explicit} {task.get('name', '')}"
    if _FOREST_RE.search(haystack):
        return "forest"
    if _ASPHALT_RE.search(haystack):
        return "asphalt"
    return default


def is_pipe_task(task: dict) -> bool:
    """Дали задачата е полагане на тръба (единственото, което смятаме по дължина).

    КЛАСЪТ НА РЕДА БИЕ ИМЕТО.  При пакетния път всяка задача с количество носи
    `activity_class_hint` — класификацията на КСС реда, направена от нас.  Тя
    е по-надеждна от думите в името, защото името е описанието на реда.

    ПРОБА 10.08.2026: без това „Изкоп с багер за канализационен изкоп",
    „Изпитване за непропускливост на канализационния участък" и „Дезинфекция
    на водопровода" минаваха за полагане на тръба — заради „канализац" и
    „водопровод" в името — и се отчитаха като MISSING_LENGTH, „липсва
    дължина".  Диагнозата беше подвеждаща: на изкопа не му липсва дължина, а
    норма.  Изпратена на одитор, тя описва липсващи данни там, където има
    липсваща норма.

    Стъпка от веригата БЕЗ количество (геодезия, изпитване, CCTV) също не е
    полагане: тя няма ред, по който да се смята.
    """
    hint = str(task.get("activity_class_hint") or "").strip().lower()
    if hint:
        if hint != "laying":
            return False
        # Клас `laying` с количество, което НЕ Е ДЪЛЖИНА, не е полагане на
        # тръба по метри.  „Бетонов кожух за тръба DN 500 — 1,04m3*71,64m" се
        # класифицира като laying заради „тръба" в описанието, но се мери в
        # `m3/m'` — обем на метър.  Сметнат по тарифа за полагане, той би дал
        # продължителност по ОБЕМНО число (проба 10.08.2026: 12 такива задачи
        # се отчитаха „липсва дължина", докато дължината стои в самото
        # описание — липсва им НОРМА за бетониране, не данна).
        unit = str(task.get("unit") or "").strip().lower()
        return not unit or unit in _LENGTH_UNITS
    if str(task.get("type", "")).lower() in _PIPE_TYPES:
        return True
    if task.get("chain_step"):
        return False
    return bool(_PIPE_TASK_RE.search(task.get("name", "")))


def detect_length_m(task: dict) -> float | None:
    """Дължина в метри от length_m/length, или от quantity при unit='м'."""
    for field in ("length_m", "length", "dyljina_m"):
        value = task.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return float(value)

    unit = str(task.get("unit", "")).strip().lower()
    if unit in _LENGTH_UNITS:
        qty = task.get("quantity")
        if isinstance(qty, (int, float)) and not isinstance(qty, bool) and qty > 0:
            return float(qty)
    return None


# ---------------------------------------------------------------------------
# Търсене на производителност
# ---------------------------------------------------------------------------

def resolve_rate(
    dn: int,
    material: str | None,
    method: str = "open",
    *,
    config: dict | None = None,
) -> RateLookup | None:
    """Намери ЕФЕКТИВНАТА производителност (м/ден) за DN + материал + метод.

    Урок #16: ефективната е ограничена от най-бавната операция в цикъла —
    затова НИКОГА не се ползва drill_rate/dig_rate за продължителност.

    Търси точно съвпадение; при липса връща None (без приблизителни
    съседни DN — тихото приближение е точно грешката, която лекуваме).
    """
    cfg = config if config is not None else load_productivities()
    rates: dict = cfg.get("productivities", {}) if isinstance(cfg, dict) else {}

    candidates = []
    if material:
        candidates.append(f"DN{dn}_{material}_{method}")
    # Записи без материал в ключа (напр. "DN630_open").
    candidates.append(f"DN{dn}_{method}")

    for key in candidates:
        entry = rates.get(key)
        if isinstance(entry, dict):
            rate = entry.get("effective_rate")
            if isinstance(rate, (int, float)) and rate > 0:
                return RateLookup(float(rate), key, "config")

    return None


def condition_factors(task: dict) -> tuple[float, list[dict]]:
    """Комбиниран коефициент от условията на изпълнение + разбивка.

    Всеки фактор носи СВОЯ произход, за да може човек да види какво точно е
    удължило задачата и защо — иначе множителят е магическо число.

    Args:
        task: Задача с полета soil, depth, groundwater, shoring, environment,
            traffic, utilities (всички незадължителни).

    Returns:
        (общ множител, [{фактор, стойност, множител}]).
    """
    total = 1.0
    applied: list[dict] = []

    for factor, table in CONDITION_FACTORS.items():
        raw = task.get(factor)
        if raw is None:
            continue
        key = str(raw).strip().lower()
        multiplier = table.get(key)
        if multiplier is None:
            logger.debug("Непозната стойност за %s: %r — пропусната.", factor, raw)
            continue
        total *= multiplier
        applied.append({"factor": factor, "value": key, "multiplier": multiplier})

    return total, applied


def terrain_factor(terrain: str, *, config: dict | None = None) -> float:
    """Коефициент за терен (урок #17).  Непознат терен → 1.0.

    ВНИМАНИЕ: НЕ се прилага по подразбиране — виж `pipe_duration`.
    """
    cfg = config if config is not None else load_productivities()
    factors: dict = cfg.get("terrain_factors", {}) if isinstance(cfg, dict) else {}
    value = factors.get(terrain)
    return float(value) if isinstance(value, (int, float)) and value > 0 else 1.0


# ---------------------------------------------------------------------------
# Изчисления
# ---------------------------------------------------------------------------

def pipe_duration(
    length_m: float,
    rate: float,
    *,
    factor: float = 1.0,
    min_days: int = DEFAULT_MIN_DAYS,
) -> int:
    """duration = max(ceil(length / (rate × factor)), min_days).

    `factor` по подразбиране е 1.0 — теренният коефициент НЕ се прилага
    автоматично.  Причина: ефективните производителности в конфига вече са
    теренно калибрирани.  ACCURACY.md (Горноград): DN300 CI, горски терен,
    673м при 8 м/д → 84 дни, което е golden standard-ът.  Ако отгоре се
    приложи и ×0.6, се получават 141 дни — 67% надуване.  Коефициентът
    остава достъпен за терени, за които няма отделна ефективна норма.
    """
    if length_m <= 0 or rate <= 0:
        raise ValueError("length_m и rate трябва да са положителни")
    effective = rate * (factor if factor > 0 else 1.0)
    return max(math.ceil(length_m / effective), max(min_days, 1))


def count_duration(quantity: float, per_day: float, *, min_days: int = 1) -> int:
    """duration = max(ceil(quantity / per_day), min_days) — за бройки (СРС/РШ)."""
    if quantity <= 0 or per_day <= 0:
        raise ValueError("quantity и per_day трябва да са положителни")
    return max(math.ceil(quantity / per_day), max(min_days, 1))


def disinfection_days(
    dn: int | None,
    material: str | None,
    *,
    terrain: str = "dirt_road",
    length_m: float | None = None,
    mixed_dn: bool = False,
    config: dict | None = None,
) -> DurationResult:
    """Дни дезинфекция по урок #33 (НЕ е винаги 4 дни).

    Правилата се четат от config/productivities.json → disinfection_days,
    а изборът кой ключ да е валиден е кодиран тук.
    """
    cfg = config if config is not None else load_productivities()
    table: dict = cfg.get("disinfection_days", {}) if isinstance(cfg, dict) else {}

    def _lookup(key: str, fallback: int) -> int:
        value = table.get(key)
        return int(value) if isinstance(value, (int, float)) and value > 0 else fallback

    if mixed_dn:
        return DurationResult(_lookup("mixed_DN_large", 4), "mixed_DN_large (урок #33)")

    if dn == 300 and material == "CI" and terrain == "forest":
        return DurationResult(_lookup("DN300_CI_forest", 6), "DN300_CI_forest (урок #33)")

    if dn == 500 and material == "PE":
        return DurationResult(_lookup("DN500_PE", 4), "DN500_PE (урок #33)")

    if dn in (90, 110) and material == "PE":
        if length_m is not None and length_m > 500:
            return DurationResult(
                _lookup("mixed_DN_large", 4),
                "DN90-110 PE над 500м → голяма мрежа (урок #33)",
            )
        return DurationResult(_lookup("DN90_110_PE_short", 2), "DN90_110_PE_short (урок #33)")

    return DurationResult(None, f"няма правило за DN={dn}, материал={material} (урок #33)")


def vratsa_tier_days(act2_act3_days: float, *, many_svo: bool = False) -> int:
    """Долноград lookup — продължителност/участък по сумата Act2+Act3 (урок #45).

    Act1 (подготовка, 0.5д) и Act7 (почистване, 0.5д) са фиксирани и вече са
    включени в тиера.  При много сградни отклонения се качва едно ниво.
    """
    days = _VRATSA_LADDER[-1]
    for threshold, tier_days in _VRATSA_THRESHOLDS:
        if act2_act3_days <= threshold:
            days = tier_days
            break

    if many_svo:
        index = _VRATSA_LADDER.index(days)
        days = _VRATSA_LADDER[min(index + 1, len(_VRATSA_LADDER) - 1)]
    return days


# ---------------------------------------------------------------------------
# Изчисление за цяла задача
# ---------------------------------------------------------------------------

def calculate_task_duration(
    task: dict,
    *,
    min_days: int = DEFAULT_MIN_DAYS,
    apply_terrain: bool = False,
    apply_conditions: bool = False,
    default_terrain: str = "dirt_road",
    config: dict | None = None,
) -> DurationResult:
    """Сметни продължителността на една задача детерминистично.

    Връща `days=None` с причина, ако задачата не е параметрична или
    липсва информация.  Извикващият тогава ЗАПАЗВА стойността на LLM-а.

    Args:
        task: Задача от генерирания график.
        min_days: Минимум работни дни за параметрична дейност.
        apply_terrain: Дали да умножи по теренния коефициент (виж
            `pipe_duration` защо по подразбиране е False).
        default_terrain: Терен, ако не се разпознае от името.
        config: Готов конфиг (за тестове).

    Returns:
        DurationResult.
    """
    cfg = config if config is not None else load_productivities()
    name = task.get("name", "")

    # Milestone — винаги 0, без предположения.
    if task.get("milestone") or task.get("is_milestone"):
        return DurationResult(0, "milestone", code=CODE_MILESTONE)

    # --- Бройки: СРС / РШ ---
    quantity = task.get("quantity")
    unit = str(task.get("unit", "")).strip().lower()
    if isinstance(quantity, (int, float)) and not isinstance(quantity, bool) and quantity > 0:
        if unit in {"бр", "бр.", "брой", "броя", "бройки", "pcs", "pc"}:
            if _SRS_RE.search(name):
                return DurationResult(
                    count_duration(quantity, COUNT_RATES["srs"]),
                    f"СРС: {quantity:g} бр. ÷ {COUNT_RATES['srs']:g} бр./ден",
                    COUNT_RATES["srs"], "srs",
                )
            if _RSH_RE.search(name):
                return DurationResult(
                    count_duration(quantity, COUNT_RATES["rsh"]),
                    f"РШ: {quantity:g} бр. ÷ {COUNT_RATES['rsh']:g} бр./ден",
                    COUNT_RATES["rsh"], "rsh",
                )
            if _SKO_RE.search(name):
                return DurationResult(
                    count_duration(quantity, COUNT_RATES["sko"]),
                    f"СКО: {quantity:g} бр. ÷ {COUNT_RATES['sko']:g} бр./ден",
                    COUNT_RATES["sko"], "sko",
                )
            if _SVO_RE.search(name):
                return DurationResult(
                    count_duration(quantity, COUNT_RATES["svo"]),
                    f"СВО: {quantity:g} бр. ÷ {COUNT_RATES['svo']:g} бр./ден",
                    COUNT_RATES["svo"], "svo",
                )
            return DurationResult(None, "бройки без известна норма (не е СРС/РШ/СКО/СВО)",
                                  code=CODE_COUNT_NO_RATE)

    # --- Площни настилки (кв.м): асфалт / тротоарни плочи ---
    # Нормите са потвърдени полево за градски обект (2026-08).  Плочите се
    # проверяват преди асфалта заради унипаважа.
    if unit in _AREA_UNITS and isinstance(quantity, (int, float)) \
            and not isinstance(quantity, bool) and quantity > 0:
        area_cfg = cfg.get("area_productivities", {}) if isinstance(cfg, dict) else {}
        if _PAVERS_RE.search(name):
            area_key = "pavers_unipavage"
        elif _ASPHALT_RE.search(name):
            area_key = "asphalt_restoration"
        else:
            area_key = None
        entry = area_cfg.get(area_key) if area_key else None
        rate = entry.get("effective_rate") if isinstance(entry, dict) else None
        if isinstance(rate, (int, float)) and rate > 0:
            return DurationResult(
                count_duration(quantity, rate),
                f"{quantity:g} м² ÷ {rate:g} м²/ден [{area_key}]",
                float(rate), area_key,
            )
        return DurationResult(None, "площна дейност без норма в конфига",
                              code=CODE_NOT_PARAMETRIC)

    # --- Линейни не-тръбни: бордюри (кв.цена на метър, но НЕ тръба) ---
    if _KERB_RE.search(name):
        lin_cfg = cfg.get("linear_productivities", {}) if isinstance(cfg, dict) else {}
        entry = lin_cfg.get("kerb_road")
        rate = entry.get("effective_rate") if isinstance(entry, dict) else None
        metres = detect_length_m(task)
        if isinstance(rate, (int, float)) and rate > 0 and metres:
            return DurationResult(
                count_duration(metres, rate),
                f"{metres:g} м бордюр ÷ {rate:g} м/ден [kerb_road]",
                float(rate), "kerb_road",
            )
        if not metres:
            return DurationResult(None, "бордюр без дължина", code=CODE_MISSING_LENGTH)
        return DurationResult(None, "бордюр без норма в конфига", code=CODE_NOT_PARAMETRIC)

    # --- Дължина: полагане на тръба ---
    if not is_pipe_task(task):
        return DurationResult(None, "не е тръбна дейност — няма норма в конфига",
                              code=CODE_NOT_PARAMETRIC)

    length_m = detect_length_m(task)
    if not length_m:
        return DurationResult(None, "липсва length_m", code=CODE_MISSING_LENGTH)

    dn = detect_dn(task)
    if not dn:
        return DurationResult(None, "неустановен DN", code=CODE_MISSING_DN)

    material = detect_material(task)
    method = detect_method(task)

    lookup = resolve_rate(dn, material, method, config=cfg)
    if lookup is None:
        return DurationResult(
            None,
            f"няма производителност за DN{dn} {material or '?'} {method}"
            + ("" if material else " (материалът не е указан — урок #35)"),
            code=CODE_NO_RULE if material else CODE_MISSING_MATERIAL,
        )

    factor = 1.0
    terrain_name = ""
    if apply_terrain:
        terrain_name = detect_terrain(task, default_terrain)
        factor = terrain_factor(terrain_name, config=cfg)

    # Фактори на условията (почва, дълбочина, води, укрепване, среда...).
    # По подразбиране изключени — стойностите им още не са верифицирани
    # срещу реални обекти, за разлика от productivities.json.
    conditions_note = ""
    if apply_conditions:
        conditions_multiplier, applied = condition_factors(task)
        if applied:
            # Факторите ЗАБАВЯТ (>1), а `pipe_duration` дели на factor —
            # затова тук се дели, за да се получи по-дълга продължителност.
            factor /= conditions_multiplier
            conditions_note = " × " + ", ".join(
                f"{a['factor']}={a['value']}({a['multiplier']:g})" for a in applied
            )

    days = pipe_duration(length_m, lookup.rate, factor=factor, min_days=min_days)

    reason = (
        f"{length_m:g}м ÷ {lookup.rate:g} м/ден"
        + (f" × терен {terrain_name} ({factor:g})" if apply_terrain and terrain_name and factor != 1.0 else "")
        + conditions_note
        + f" → {days}д [{lookup.key}]"
    )
    return DurationResult(days, reason, lookup.rate, lookup.key)
