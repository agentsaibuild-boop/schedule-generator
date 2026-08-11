"""Unit tests: авторският надзор обхваща цялото строителство.

ОДИТ 10.08.2026, P0.3: „Construction: 25.05.2027 → 29.12.2029, Author
supervision: 25.05.2027 → 14.03.2029.  Надзорът приключва повече от 9 месеца
преди строителството."

Потвърдено в изнесения файл.  Причината: надзорът получаваше медианата от
човешкия еталон (660 дни) като всяка друга стъпка.  Но авторският надзор не е
дейност с продължителност — той е задължение, което трае колкото обектът.
Пренесена от друг обект, медианата е просто чуждо число.

FAILURE означава: договорна фаза пак свършва преди работата, която придружава.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.work_package import enforce_construction_span  # noqa: E402


def _task(tid, root, start, end, **kw):
    task = {"id": tid, "name": tid, "wbs_root": root,
            "start_day": start, "end_day": end,
            "duration": end - start + 1}
    task.update(kw)
    return task


def _site(supervision_end: int = 400) -> list[dict]:
    """Обект, чието строителство трае по-дълго от надзора."""
    return [
        _task("K1", "construction", 10, 500),
        _task("K2", "construction", 20, 900),
        _task("W1", "construction", 15, 300),
        _task("SUP", "supervision", 10, supervision_end, spans_construction=True),
    ]


def test_supervision_covers_the_whole_construction():
    tasks, notes = enforce_construction_span(_site())
    sup = next(t for t in tasks if t["id"] == "SUP")

    assert sup["start_day"] == 10   # най-ранното строително начало
    assert sup["end_day"] == 900    # най-късният строителен край
    assert notes


def test_the_shipped_defect_is_caught():
    """Регресия за самата находка: надзор, свършващ преди строителството."""
    before = _site(supervision_end=400)
    construction_end = max(t["end_day"] for t in before
                           if t["wbs_root"] == "construction")
    supervision_end = next(t["end_day"] for t in before if t["id"] == "SUP")
    assert supervision_end < construction_end, "фикстурата не възпроизвежда дефекта"

    after, _ = enforce_construction_span(before)
    sup = next(t for t in after if t["id"] == "SUP")

    assert sup["end_day"] >= construction_end


def test_supervision_is_also_stretched_backwards():
    """Инвариантът е двустранен — надзорът не бива да започва по-късно."""
    tasks = _site()
    tasks[3]["start_day"] = 200

    stretched, _ = enforce_construction_span(tasks)

    assert next(t for t in stretched if t["id"] == "SUP")["start_day"] == 10


def test_the_duration_follows_the_span():
    tasks, _ = enforce_construction_span(_site())
    sup = next(t for t in tasks if t["id"] == "SUP")

    assert sup["duration"] == sup["end_day"] - sup["start_day"] + 1
    assert sup["duration_source"] == "construction_span"


def test_ordinary_tasks_are_untouched():
    before = _site()
    after, _ = enforce_construction_span(before)

    for tid in ("K1", "K2", "W1"):
        b = next(t for t in before if t["id"] == tid)
        a = next(t for t in after if t["id"] == tid)
        assert (a["start_day"], a["end_day"]) == (b["start_day"], b["end_day"])


def test_the_input_is_not_mutated():
    before = _site()
    enforce_construction_span(before)

    assert next(t for t in before if t["id"] == "SUP")["end_day"] == 400


def test_without_construction_nothing_is_invented():
    only_supervision = [_task("SUP", "supervision", 1, 10, spans_construction=True)]

    tasks, notes = enforce_construction_span(only_supervision)

    assert tasks[0]["end_day"] == 10
    assert notes and "няма строителни задачи" in notes[0]
