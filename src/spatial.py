"""Пространствен модел по пикетаж — време И място, не само време.

ЗАЩО (одит 2026-07-23, точка 3): графикът съдържаше само задачи с начало,
край и предшественици.  Това е мрежов график за линеен обект, но НЕ е линеен
график.  Липсваше цялата пространствена страна:

    Изкоп:     км 0+000 → 0+300
    Полагане:  км 0+000 → 0+300
    Засипване: км 0+000 → 0+300

Без пикетаж не може да се докаже, че монтажната бригада не настъпва
изкопната, че откритият изкоп не надвишава допустимата дължина и че
засипването не започва преди изпитването на същия участък.

Моделът е ДОБАВЪЧЕН: задачи без пикетаж се пропускат от проверките, а не
се обявяват за грешни.  Така съществуващите графици продължават да работят,
а новите получават проверка, щом имат данните.

Пикетажът се записва в метри от началото на оста:
    {"alignment_id": "ул. Витоша", "start_chainage": 0, "end_chainage": 300}
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)

# Максимална дължина открит изкоп по подразбиране (м).  Реалната стойност е
# проектна и идва от ПБЗ/ПОИС; тук е предпазна мрежа, не норма.
DEFAULT_MAX_OPEN_TRENCH_M = 300

# Минимално пространствено изоставане между последователни бригади (м).
# Технологично: полагането не бива да е в петите на изкопа.
DEFAULT_CREW_BUFFER_M = 20


class Extent(NamedTuple):
    """Пространствено-времеви обхват на една задача."""

    task_id: str
    name: str
    alignment_id: str
    start_chainage: float
    end_chainage: float
    start_day: int
    end_day: int
    crew: str = ""

    @property
    def length_m(self) -> float:
        return abs(self.end_chainage - self.start_chainage)

    def overlaps_space(self, other: Extent, buffer_m: float = 0.0) -> bool:
        """Дали двата участъка се застъпват по ос (с буфер)."""
        if self.alignment_id != other.alignment_id:
            return False
        low_a, high_a = sorted((self.start_chainage, self.end_chainage))
        low_b, high_b = sorted((other.start_chainage, other.end_chainage))
        return low_a - buffer_m < high_b and low_b - buffer_m < high_a

    def overlaps_time(self, other: Extent) -> bool:
        return self.start_day <= other.end_day and other.start_day <= self.end_day


def _number(value: Any) -> float | None:
    """Пикетаж като число; приема 300, "300", "0+300"."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(" ", "")
    if not text:
        return None
    # Формат „км 0+300" → 300 м
    if "+" in text:
        head, _, tail = text.rpartition("+")
        head = "".join(ch for ch in head if ch.isdigit())
        tail = "".join(ch for ch in tail if ch.isdigit() or ch == ".")
        try:
            return float(head or 0) * 1000 + float(tail or 0)
        except ValueError:
            return None
    try:
        return float("".join(ch for ch in text if ch.isdigit() or ch in ".-"))
    except ValueError:
        return None


def extent_of(task: dict) -> Extent | None:
    """Извлечи пространствено-времевия обхват на задача.

    Returns:
        Extent, или None ако задачата няма пикетаж (тогава просто не участва
        в пространствените проверки).
    """
    start = _number(task.get("start_chainage"))
    end = _number(task.get("end_chainage"))
    if start is None or end is None:
        return None

    alignment = str(task.get("alignment_id") or task.get("alignment") or "").strip()
    if not alignment:
        return None

    start_day = task.get("start_day")
    end_day = task.get("end_day")
    if not isinstance(start_day, (int, float)) or isinstance(start_day, bool):
        return None
    if not isinstance(end_day, (int, float)) or isinstance(end_day, bool):
        duration = task.get("duration", 0)
        duration = duration if isinstance(duration, (int, float)) else 0
        end_day = int(start_day) + max(int(duration), 1) - 1

    return Extent(
        task_id=str(task.get("id", "?")),
        name=str(task.get("name", "?")),
        alignment_id=alignment,
        start_chainage=start,
        end_chainage=end,
        start_day=int(start_day),
        end_day=int(end_day),
        crew=str(task.get("crew_id") or task.get("team") or ""),
    )


def extents(schedule: list[dict]) -> list[Extent]:
    """Обхватите на всички задачи, които имат пикетаж."""
    found = [extent_of(t) for t in schedule if isinstance(t, dict)]
    return [e for e in found if e is not None]


# ---------------------------------------------------------------------------
# Проверки
# ---------------------------------------------------------------------------

def find_crew_collisions(
    schedule: list[dict], buffer_m: float = DEFAULT_CREW_BUFFER_M
) -> list[dict]:
    """Намери бригади, които се застъпват едновременно по място и по време.

    Това е проверката, която мрежовият график не може да направи: две задачи
    може да са коректни по зависимости и пак да изпращат два екипа на един и
    същи метър в един и същи ден.

    Args:
        schedule: Списък задачи.
        buffer_m: Минимално изоставане между бригади (м).

    Returns:
        Списък от {task_a, task_b, alignment, overlap_m, days}.
    """
    result: list[dict] = []
    items = extents(schedule)

    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if a.crew and b.crew and a.crew == b.crew:
                pass  # един и същ екип — конфликтът е ресурсен, виж validate_schedule
            if not a.overlaps_time(b):
                continue
            if not a.overlaps_space(b, buffer_m):
                continue

            low = max(min(a.start_chainage, a.end_chainage),
                      min(b.start_chainage, b.end_chainage))
            high = min(max(a.start_chainage, a.end_chainage),
                       max(b.start_chainage, b.end_chainage))
            overlap = max(high - low, 0.0)
            # Разграничение: реално застъпване (двата екипа са на едни и същи
            # метри) срещу нарушен буфер (участъците се допират или са твърде
            # близо).  Второто е технологично изискване, не физически сблъсък,
            # и съобщението не бива да казва „застъпват се на 0м".
            result.append({
                "task_a": a.task_id, "name_a": a.name,
                "task_b": b.task_id, "name_b": b.name,
                "alignment": a.alignment_id,
                "overlap_m": overlap,
                "kind": "overlap" if overlap > 0 else "buffer",
                "buffer_m": buffer_m,
                "days": (max(a.start_day, b.start_day), min(a.end_day, b.end_day)),
                "crew_a": a.crew, "crew_b": b.crew,
            })
    return result


def find_open_trench_violations(
    schedule: list[dict], max_open_m: float = DEFAULT_MAX_OPEN_TRENCH_M
) -> list[dict]:
    """Намери дни, в които откритият изкоп по една ос надвишава лимита.

    Дължината на отворения изкоп е ограничена от ПБЗ и от безопасността.
    Мрежовият график няма как да я види — тя е сума по МЯСТО за даден ДЕН.

    Търси задачи, чието име сочи изкоп, и сумира дължините им по ос и ден.
    """
    result: list[dict] = []
    excavations = [
        e for t, e in ((t, extent_of(t)) for t in schedule if isinstance(t, dict))
        if e is not None and _is_excavation(t)
    ]
    if not excavations:
        return result

    by_alignment: dict[str, list[Extent]] = {}
    for extent in excavations:
        by_alignment.setdefault(extent.alignment_id, []).append(extent)

    for alignment, items in by_alignment.items():
        first_day = min(e.start_day for e in items)
        last_day = max(e.end_day for e in items)
        worst: dict | None = None

        for day in range(first_day, last_day + 1):
            open_today = [e for e in items if e.start_day <= day <= e.end_day]
            total = sum(e.length_m for e in open_today)
            if total > max_open_m and (worst is None or total > worst["open_m"]):
                worst = {
                    "alignment": alignment,
                    "day": day,
                    "open_m": total,
                    "limit_m": max_open_m,
                    "tasks": [e.task_id for e in open_today],
                }
        if worst:
            result.append(worst)
    return result


_EXCAVATION_WORDS = ("изкоп", "траншея", "разкопаване")


def _is_excavation(task: dict) -> bool:
    name = str(task.get("name", "")).lower()
    if task.get("is_open_trench") is True:
        return True
    return any(word in name for word in _EXCAVATION_WORDS)


def spatial_report(
    schedule: list[dict],
    *,
    buffer_m: float = DEFAULT_CREW_BUFFER_M,
    max_open_m: float = DEFAULT_MAX_OPEN_TRENCH_M,
) -> dict[str, Any]:
    """Обобщена пространствена проверка.

    Returns:
        {covered, total, collisions, open_trench, alignments}
    """
    total = len([t for t in schedule if isinstance(t, dict)])
    items = extents(schedule)
    alignments = sorted({e.alignment_id for e in items})

    return {
        "covered": len(items),
        "total": total,
        "alignments": alignments,
        "collisions": find_crew_collisions(schedule, buffer_m),
        "open_trench": find_open_trench_violations(schedule, max_open_m),
    }
