"""Unit tests: веригата е DAG, а не списък.

ОДИТ 10.08.2026, P0.2: „tech_chains.json съдържа step names, durations и crews,
но не пази predecessor relation type и lag.  Генерираният design chain става
практически FS0 serial.  Човешки design phase: 120 работни дни.  Генериран: 249."

Измерено срещу еталона: 23-те проектантски стъпки имат сбор 245 дни, а фазата
трае 120, защото ОСЕМ връзки застъпват работата.  Имената и продължителностите
бяхме пренесли; застъпванията — не.

Едно уточнение към становището: паралелизмът НЕ идва от разклоняване на
веригата.  Тя е строго линейна и в еталона — най-дългият път минава през
всичките 23 стъпки.  Идва от типа на връзките: осем от тях са start-to-start,
тоест следващата част започва с предната, а не след нея.

FAILURE означава: проектирането пак се реди последователно и срокът се удвоява.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.schedule_builder import ScheduleBuilder  # noqa: E402
from src.work_package import (  # noqa: E402
    contract_packages,
    expand_packages,
    load_chains,
)

#: Човешкият еталон за фазата „Проектиране" — 06.10.2025 → 02.02.2026.
HUMAN_DESIGN_DAYS = 120

#: Сборът на продължителностите, тоест срокът при чисто последователно реждане.
SERIAL_DESIGN_DAYS = 245


def _design_tasks() -> list[dict]:
    chains = load_chains()
    packages = [p for p in contract_packages(chains, with_design=True)
                if p.chain == "design"]
    return [t for t in expand_packages(packages, chains).tasks
            if not t.get("is_summary")]


def _span(tasks: list[dict]) -> int:
    scheduled = ScheduleBuilder().reschedule(tasks)["schedule"]
    return (max(t["end_day"] for t in scheduled)
            - min(t["start_day"] for t in scheduled) + 1)


# ---------------------------------------------------------------------------
# Конфигурацията носи топология
# ---------------------------------------------------------------------------


def test_the_design_chain_declares_its_links():
    steps = load_chains()["chains"]["design"]["steps"]
    linked = [s for s in steps if s.get("predecessor")]

    assert len(linked) == len(steps) - 1, "само първата стъпка е без предшественик"
    assert all(s.get("relation") in {"FS", "SS"} for s in linked)


def test_the_chain_keeps_the_overlaps_from_the_reference():
    steps = load_chains()["chains"]["design"]["steps"]
    overlapping = [s for s in steps if s.get("relation") == "SS"]

    assert len(overlapping) == 8


def test_the_declared_predecessors_exist():
    steps = load_chains()["chains"]["design"]["steps"]
    keys = {s["key"] for s in steps}

    for step in steps:
        if step.get("predecessor"):
            assert step["predecessor"] in keys, step["key"]


def test_the_lag_is_carried_where_the_reference_has_one():
    """Водоснабдяването тръгва 10 дни след геологията, не веднага."""
    steps = {s["key"]: s for s in load_chains()["chains"]["design"]["steps"]}

    assert steps["water"]["relation"] == "SS"
    assert steps["water"]["lag_days"] == 10


# ---------------------------------------------------------------------------
# Топологията стига до задачите
# ---------------------------------------------------------------------------


def test_the_overlaps_reach_the_generated_tasks():
    links = [d for t in _design_tasks() for d in (t.get("dependencies") or [])]

    assert sum(1 for d in links if d.get("type") == "SS") == 8


def test_a_step_is_linked_to_its_declared_predecessor():
    tasks = {t["id"]: t for t in _design_tasks()}
    water = next(t for t in tasks.values() if t["id"].endswith("_water"))

    predecessors = {d["predecessor_id"] for d in water["dependencies"]}

    assert any(p.endswith("_geology") for p in predecessors)


# ---------------------------------------------------------------------------
# Срокът е СЛЕДСТВИЕ, не число
# ---------------------------------------------------------------------------


def test_the_design_phase_is_no_longer_serial():
    """Регресия за самата находка."""
    span = _span(_design_tasks())

    assert span < SERIAL_DESIGN_DAYS * 0.75, (
        f"{span} дни — проектирането пак се реди последователно")


def test_the_design_phase_approaches_the_human_span():
    """Не се гони точното число — проверява се, че топологията го обяснява.

    Допускът е 15%: нашият календар и обработката на милстоуните се различават
    от MS Project с няколко дни, и това е честно да остане видимо.
    """
    span = _span(_design_tasks())

    assert abs(span - HUMAN_DESIGN_DAYS) <= HUMAN_DESIGN_DAYS * 0.15, (
        f"{span} дни срещу {HUMAN_DESIGN_DAYS} в еталона")


def test_the_span_is_derived_and_not_hardcoded():
    """Свали ли се едно застъпване, срокът ТРЯБВА да се удължи.

    Ако не се удължи, значи числото идва отнякъде другаде, а не от топологията.
    """
    tasks = _design_tasks()
    before = _span(tasks)

    for task in tasks:
        for dep in (task.get("dependencies") or []):
            if dep.get("type") == "SS":
                dep["type"] = "FS"
                break
        else:
            continue
        break

    assert _span(tasks) > before
