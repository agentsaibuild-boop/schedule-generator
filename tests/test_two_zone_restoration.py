"""Unit tests: възстановяването е ЛОКАЛНО — зона X чака само зона X.

ОДИТ 10.08.2026, P1.2: „Има една обща restoration zone, но няма populated
street topology.  Следователно не е доказано: underground in zone X ->
restoration X.  Нужен е synthetic two-zone regression."

Справедливо.  Досегашният тест проверяваше, че зона А зависи от подземните
работи в А и не зависи от тези в Б.  Това не е достатъчно: зависимостите могат
да са верни, а двете зони пак да са свързани ПРЕЗ трети път — и тогава
rolling-wave изпълнението пак го няма, само че по-скрито.

Затова тук се проверява и третото: между двете зони НЯМА път в мрежата, тоест
те могат да текат едновременно.

FAILURE означава: улица Б чака работа по улица А без причина, и обектът пак се
изпълнява на един фронт.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.work_package import (  # noqa: E402
    PackageItem,
    SpatialWorkPackage,
    expand_packages,
    link_cross_discipline,
    load_chains,
    merge_restoration_zones,
)

STREET_A = "ул. Петуния"
STREET_B = "ул. Гергини"


def _sewer(pkg_id: str, street: str) -> SpatialWorkPackage:
    return SpatialWorkPackage(
        id=pkg_id, network="К", chain="sewer_section", street=street,
        items=(PackageItem(f"КСС.xlsx!3!{pkg_id}", "laying", 500.0, "m",
                           "Изграждане на смесена канализационна мрежа"),))


def _pavement(pkg_id: str, street: str, ref: str, description: str,
              quantity: float) -> SpatialWorkPackage:
    return SpatialWorkPackage(
        id=pkg_id, network="П", chain="pavement_section", street=street,
        items=(PackageItem(ref, "pavement", quantity, "кв. м", description),))


def _two_streets() -> list[SpatialWorkPackage]:
    """Два независими фронта: всеки с канал и с трите пътни позиции."""
    return [
        _sewer("KA", STREET_A),
        _sewer("KB", STREET_B),
        _pavement("PA1", STREET_A, "КСС.xlsx!4!8", "асфалтова настилка", 5000.0),
        _pavement("PA2", STREET_A, "КСС.xlsx!4!9", "бетонови бордюри", 3000.0),
        _pavement("PB1", STREET_B, "КСС.xlsx!4!10", "асфалтова настилка", 5824.0),
        _pavement("PB2", STREET_B, "КСС.xlsx!4!11", "тротоарни плочи", 4000.0),
    ]


def _build() -> tuple[list[dict], dict[str, SpatialWorkPackage]]:
    packages, _ = merge_restoration_zones(_two_streets())
    chains = load_chains()
    tasks = link_cross_discipline(
        expand_packages(packages, chains).tasks, packages, chains)
    zones = {p.street: p for p in packages if p.chain == "pavement_section"}
    return tasks, zones


def _predecessors(tasks: list[dict], task_id: str) -> set[str]:
    task = next(t for t in tasks if t["id"] == task_id)
    return {d["predecessor_id"] for d in (task.get("dependencies") or [])}


def _ancestors(tasks: list[dict], task_id: str) -> set[str]:
    """Всичко, което задачата чака — пряко или през верига от връзки."""
    by_id = {str(t["id"]): t for t in tasks}
    seen: set[str] = set()
    stack = [task_id]
    while stack:
        current = stack.pop()
        for dep in (by_id.get(current, {}).get("dependencies") or []):
            pred = str(dep["predecessor_id"])
            if pred not in seen:
                seen.add(pred)
                stack.append(pred)
    return seen


def _tasks_of(tasks: list[dict], package_id: str) -> set[str]:
    return {str(t["id"]) for t in tasks if t.get("parent_id") == package_id}


# ---------------------------------------------------------------------------
# Всяка зона чака своето трасе
# ---------------------------------------------------------------------------


def test_zone_a_waits_for_the_pipes_under_it():
    tasks, zones = _build()

    preds = _predecessors(tasks, f"{zones[STREET_A].id}_base_course")

    assert "KA_connections_backfill" in preds


def test_zone_b_waits_for_the_pipes_under_it():
    tasks, zones = _build()

    preds = _predecessors(tasks, f"{zones[STREET_B].id}_base_course")

    assert "KB_connections_backfill" in preds


def test_neither_zone_waits_for_the_other_street():
    tasks, zones = _build()

    assert "KB_connections_backfill" not in _predecessors(
        tasks, f"{zones[STREET_A].id}_base_course")
    assert "KA_connections_backfill" not in _predecessors(
        tasks, f"{zones[STREET_B].id}_base_course")


# ---------------------------------------------------------------------------
# И двете могат да текат едновременно
# ---------------------------------------------------------------------------


def test_the_two_zones_are_independent_along_the_whole_network():
    """Прякото сравнение не стига: зоните могат да са свързани ПРЕЗ трети път.

    Ако съществува верига от връзки от улица А до възстановяването на улица Б,
    двата фронта не са паралелни, колкото и правилни да са преките връзки.
    """
    tasks, zones = _build()

    a_side = _tasks_of(tasks, "KA") | _tasks_of(tasks, zones[STREET_A].id)
    b_side = _tasks_of(tasks, "KB") | _tasks_of(tasks, zones[STREET_B].id)

    for task_id in b_side:
        assert not (_ancestors(tasks, task_id) & a_side), (
            f"{task_id} по {STREET_B} чака работа по {STREET_A}")
    for task_id in a_side:
        assert not (_ancestors(tasks, task_id) & b_side), (
            f"{task_id} по {STREET_A} чака работа по {STREET_B}")


def test_each_street_gets_its_own_restoration_zone():
    _, zones = _build()

    assert set(zones) == {STREET_A, STREET_B}
    assert zones[STREET_A].id != zones[STREET_B].id


def test_each_zone_asphalts_once():
    tasks, zones = _build()

    for street in (STREET_A, STREET_B):
        asphalt = [t for t in tasks
                   if t.get("parent_id") == zones[street].id
                   and t.get("chain_step") == "asphalt"]
        assert len(asphalt) == 1, f"{street}: {len(asphalt)} асфалтирания"


# ---------------------------------------------------------------------------
# Договорът за `suggested` (одит 10.08.2026)
# ---------------------------------------------------------------------------
#
# „suggested данните могат да се използват само за име/етикет, не за quantity
# allocation, topology, dependencies или доказателство за spatial coverage."
#
# Локалността на възстановяването Е твърдение за покритие: „тази работа е тук
# и никъде другаде".  Затова без авторитетна геометрия зоната е една.

def _build_unverified() -> list[dict]:
    packages, _ = merge_restoration_zones(_two_streets(),
                                          spatial_authoritative=False)
    chains = load_chains()
    return link_cross_discipline(
        expand_packages(packages, chains).tasks, packages, chains,
        spatial_authoritative=False), packages


def test_without_authoritative_geometry_there_is_one_zone():
    _, packages = _build_unverified()

    zones = [p for p in packages if p.chain == "pavement_section"]

    assert len(zones) == 1


def test_the_single_zone_does_not_duplicate_the_scope():
    """Сливането пази смисъла си: всеки ред от КСС ражда ТОЧНО една задача.

    В тази фикстура има два асфалтови реда — по един на улица — значи две
    задачи за асфалт са правилни.  Дефектът, който гейтът лови, е ДРУГ: една
    и съща позиция да се изпълнява по няколко пъти.
    """
    tasks, packages = _build_unverified()
    zone = next(p for p in packages if p.chain == "pavement_section")

    cited = [t.get("source_ref") for t in tasks
             if t.get("parent_id") == zone.id and t.get("source_ref")]

    assert sorted(cited) == sorted({*cited})
    assert len(cited) == 4          # асфалт×2, бордюри, тротоарни плочи


def test_a_pdf_street_does_not_split_the_dependencies():
    """Консервативно и вярно: настилката чака ВСИЧКИ подземни работи."""
    tasks, packages = _build_unverified()
    zone = next(p for p in packages if p.chain == "pavement_section")

    preds = _predecessors(tasks, f"{zone.id}_base_course")

    assert {"KA_connections_backfill", "KB_connections_backfill"} <= preds
