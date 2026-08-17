"""Unit tests: настилките са ЗОНА, не ред от количествената сметка.

ОДИТ 07.08.2026: „Сега всяка от трите КСС позиции — асфалт, бордюри, унипаваж
— получава цялата 3-стъпкова pavement chain.  Пакетът само за асфалт съдържа
основен пласт, направа на бордюри и асфалт.  Пакетът за бордюри съдържа и
асфалт.  Това означава: quantity conservation може да е 100% вярно, а
execution scope пак да е дублиран."

Това е нов вариант на стария „mapped ≠ covered", вече като
`allocated quantity ≠ executed scope` — и е по-коварен, защото гейтът за
Σ = КСС го обявява за чист.  Сборът по редове наистина е точен; обектът просто
се асфалтира три пъти.

FAILURE означава: обектът пак получава повече изпълнение, отколкото има работа,
и нито един количествен гейт няма да го хване.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.work_package import (  # noqa: E402
    PackageItem,
    SpatialWorkPackage,
    expand_packages,
    load_chains,
    merge_restoration_zones,
)

#: Трите пътни реда от реалния търг, всеки в собствен пакет — точно както
#: моделът ги връща и както изглеждаха в одитирания файл.
_ROWS = (
    ("КСС.xlsx!4. Пътна!8", "Пътна - възстановяване на асфалтова настилка", 10824.0),
    ("КСС.xlsx!4. Пътна!9", "Доставка и полагане на средни бетонови бордюри", 7761.0),
    ("КСС.xlsx!4. Пътна!10", "Доставка и полагане на тротоарни плочи (унипаваж)", 18671.0),
)


def _pavement_packages(street: str = "ул. Петуния",
                       prefix: str = "P") -> list[SpatialWorkPackage]:
    return [
        SpatialWorkPackage(
            id=f"{prefix}{i}", network="П", chain="pavement_section", street=street,
            items=(PackageItem(ref, "pavement", qty, "кв. м", desc),))
        for i, (ref, desc, qty) in enumerate(_ROWS, 1)
    ]


def _tasks_for(packages: list[SpatialWorkPackage]) -> list[dict]:
    return [t for t in expand_packages(packages, load_chains()).tasks
            if not t.get("is_summary")]


def _step_counts(tasks: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for task in tasks:
        key = str(task.get("chain_step") or "")
        counts[key] = counts.get(key, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Самият дефект
# ---------------------------------------------------------------------------


def test_one_package_per_boq_row_asphalts_the_street_three_times():
    """Дефектът, преди поправката — записан, за да не се върне мълчаливо."""
    counts = _step_counts(_tasks_for(_pavement_packages()))

    assert counts["asphalt"] == 3
    assert counts["kerbs"] == 3
    assert counts["base_course"] == 3


def test_a_zone_asphalts_the_street_once():
    """Всяка стъпка се изпълнява веднъж — освен когато ДВА реда от КСС
    наистина ѝ принадлежат.

    Бордюрите и унипаважът са различна работа, паднала в една и съща стъпка
    („Направа на бордюри и тротоарна настилка"), и затова са две задачи.
    Асфалтът обаче е един ред и трябва да е една задача — трите му копия бяха
    дефектът.
    """
    counts = _step_counts(_tasks_for(merge_restoration_zones(_pavement_packages())[0]))

    assert counts["asphalt"] == 1
    assert counts["base_course"] == 1
    assert counts["kerbs"] == 2  # бордюри + унипаваж, две отделни количества


def test_a_zone_creates_no_work_without_a_quantity_behind_it():
    """Единствената задача без цитат е основният пласт.

    Той няма ред в КСС (в този търг е вътре в друга позиция), но се извършва —
    затова е задължителна стъпка.  Всичко останало трябва да сочи количество;
    незацитираната работа е точно призрачното изпълнение, което се появи.
    """
    packages, _ = merge_restoration_zones(_pavement_packages())
    uncited = [t for t in _tasks_for(packages) if not t.get("source_ref")]

    assert [t["chain_step"] for t in uncited] == ["base_course"]


def test_merging_keeps_every_quantity():
    """Сливането мести носителя, не количеството — Σ = КСС не се променя."""
    before = _pavement_packages()
    after, _ = merge_restoration_zones(before)

    assert sorted(i.source_ref for p in after for i in p.items) == \
        sorted(i.source_ref for p in before for i in p.items)
    assert sum(i.quantity for p in after for i in p.items) == \
        pytest.approx(sum(i.quantity for p in before for i in p.items))


def test_every_row_still_reaches_a_step_of_its_own():
    """Едно количество → точно една задача; трите реда не се сливат в един."""
    packages, _ = merge_restoration_zones(_pavement_packages())
    cited = [t for t in _tasks_for(packages) if t.get("source_ref")]

    assert sorted(t["source_ref"] for t in cited) == sorted(r[0] for r in _ROWS)


# ---------------------------------------------------------------------------
# Зоната е МЯСТО
# ---------------------------------------------------------------------------


def test_different_streets_stay_different_zones():
    packages = _pavement_packages("ул. Петуния") + _pavement_packages("ул. Дунав")

    merged, notes = merge_restoration_zones(packages)

    assert len(merged) == 2
    assert {p.street for p in merged} == {"ул. Петуния", "ул. Дунав"}
    assert len(notes) == 2


def test_without_a_street_the_whole_site_is_one_zone():
    """Грубо, но честно: една верига вместо три, и толкова разделителна
    способност, колкото моделът реално е дал."""
    merged, _ = merge_restoration_zones(_pavement_packages(street=""))

    assert len(merged) == 1
    assert _step_counts(_tasks_for(merged))["asphalt"] == 1


def test_the_zone_is_named_after_the_place_not_the_first_boq_row():
    merged, _ = merge_restoration_zones(_pavement_packages())

    assert "ул. Петуния" in merged[0].label
    assert "бордюри" not in merged[0].label.lower()


def test_underground_packages_are_untouched():
    """Сливането важи само за възстановяването — тръбните участъци са трасета."""
    sewer = [
        SpatialWorkPackage(
            id=f"K{i}", network="К", chain="sewer_section", street="ул. Петуния",
            items=(PackageItem(f"КСС.xlsx!3!{i}", "laying", 100.0, "m", "тръба"),))
        for i in (1, 2)
    ]

    merged, notes = merge_restoration_zones(sewer)

    assert [p.id for p in merged] == ["K1", "K2"]
    assert notes == []


def test_a_single_pavement_package_is_left_alone():
    merged, notes = merge_restoration_zones(_pavement_packages()[:1])

    assert [p.id for p in merged] == ["P1"]
    assert notes == []


# ---------------------------------------------------------------------------
# Rolling wave: зона X чака подземните работи в зона X
# ---------------------------------------------------------------------------
#
# ОДИТ 07.08.2026: „Трите пътни пакета чакат един и същ глобален набор от
# основните водопроводни и канализационни крайни дейности.  На практика е
# всички подземни работи → всички пътни работи, а не подземни работи в улица X
# → възстановяване на зона X.  Така губите истинското rolling-wave изпълнение."

def _sewer(pkg_id: str, street: str) -> SpatialWorkPackage:
    return SpatialWorkPackage(
        id=pkg_id, network="К", chain="sewer_section", street=street,
        items=(PackageItem(f"КСС.xlsx!3!{pkg_id}", "laying", 500.0, "m",
                           "Изграждане на смесена канализационна мрежа"),))


def _predecessors(tasks: list[dict], task_id: str) -> set[str]:
    task = next(t for t in tasks if t["id"] == task_id)
    return {d["predecessor_id"] for d in (task.get("dependencies") or [])}


def _linked(packages: list[SpatialWorkPackage]) -> list[dict]:
    from src.work_package import link_cross_discipline
    chains = load_chains()
    return link_cross_discipline(expand_packages(packages, chains).tasks,
                                 packages, chains)


def test_a_zone_waits_only_for_the_underground_work_beneath_it():
    packages, _ = merge_restoration_zones(
        [_sewer("K1", "ул. Петуния"), _sewer("K2", "ул. Дунав")]
        + _pavement_packages("ул. Петуния")
        + _pavement_packages("ул. Дунав", prefix="D")[:1]
    )
    tasks = _linked(packages)

    petunia = next(p for p in packages if p.street == "ул. Петуния"
                   and p.chain == "pavement_section")
    dunav = next(p for p in packages if p.street == "ул. Дунав"
                 and p.chain == "pavement_section")

    assert "K1_connections_backfill" in _predecessors(tasks, f"{petunia.id}_base_course")
    assert "K2_connections_backfill" not in _predecessors(tasks, f"{petunia.id}_base_course")
    assert "K2_connections_backfill" in _predecessors(tasks, f"{dunav.id}_base_course")
    assert "K1_connections_backfill" not in _predecessors(tasks, f"{dunav.id}_base_course")


# ---------------------------------------------------------------------------
# Пространствената идентичност излиза само когато чертежът я потвърждава
# ---------------------------------------------------------------------------
#
# Пакет за одитора 10.08.2026: прогон БЕЗ ситуационен чертеж изнесе в MS
# Project „от ОТ 1 до ОТ 2", „от П1 до П2", „от К1 до К2" — поредни номера с
# измислени конвенции — и една и съща улица за всичките 16
# пакета.  Валидни полета, правдоподобна форма, съчинено съдържание.
#
# FAILURE означава: програмата пак изнася съчинена геометрия, която в готовия
# файл изглежда като прочетена от чертеж.

_AI_PACKAGE = {
    "id": "K1", "network": "К", "chain": "sewer_section",
    "street": "кв. Пример", "start_node": "РШ 36", "end_node": "РШ 37",
    "items": [{"source_ref": "КСС.xlsx!3!9", "quantity": 100.0}],
}

_DRAWING = [{"branch": "кл. 48", "start_node": "РШ 36", "end_node": "РШ 37",
             "street": "ул. Петуния", "network": "К"}]


def _from_ai(segments, source="structured_segments"):
    """По подразбиране участъците идват от АВТОРИТЕТЕН източник.

    Одит 10.08.2026, P1.1: потвърждаването вече иска две неща — възлите да са
    в подадените участъци И източникът да е геометрия, а не четене на картинка.
    """
    from src.provenance import build_quantity_index
    from src.work_package import packages_from_ai

    fixture = Path(__file__).parent / "fixtures" / "kss_anonymized"
    boq = [r for r in build_quantity_index(fixture) if r.quantity is not None]
    ref = boq[0].ref
    payload = {"packages": [dict(_AI_PACKAGE,
                                 items=[{"source_ref": ref, "quantity": 1.0}])]}
    packages, _ = packages_from_ai(payload, boq_index=boq, segments=segments,
                                   spatial_source=source)
    return packages


def test_nodes_confirmed_by_an_authoritative_source_are_exported():
    packages = _from_ai(_DRAWING)

    assert packages and packages[0].spatial_verified
    task = _tasks_for(packages)[0]
    assert task["from_node"] == "РШ 36" and task["to_node"] == "РШ 37"


def test_nodes_invented_without_a_drawing_stay_inside():
    """Без чертеж възлите са съчинени и не напускат програмата."""
    packages = _from_ai(None)

    assert packages and not packages[0].spatial_verified
    task = _tasks_for(packages)[0]
    assert task["from_node"] == "" and task["to_node"] == "" and task["street"] == ""
    # Нашето собствено id обаче винаги е вярно и остава.
    assert task["spatial_segment_id"] == "K1"


def test_nodes_absent_from_the_drawing_stay_inside():
    """Чертежът съществува, но не съдържа тази двойка — пак непотвърдено."""
    other = [dict(_DRAWING[0], start_node="РШ 90", end_node="РШ 91")]

    assert not _from_ai(other)[0].spatial_verified


def test_node_direction_does_not_matter():
    """Участък от РШ 36 до РШ 37 е същият като обратното."""
    reversed_pair = [dict(_DRAWING[0], start_node="РШ 37", end_node="РШ 36")]

    assert _from_ai(reversed_pair)[0].spatial_verified


def test_a_pdf_reading_names_the_section_but_is_not_geometry():
    """Одит 10.08.2026, P1.1: „PDF Vision не трябва да е authoritative source."

    Същите възли, същото съвпадение — но източникът е четене на чертеж.
    Имената остават годни за наименуване; геометрия не излиза.
    """
    packages = _from_ai(_DRAWING, source="pdf_suggestions_only")

    assert not packages[0].spatial_verified
    assert packages[0].start_node == "РШ 36"          # за етикета — остава
    task = _tasks_for(packages)[0]
    assert task["from_node"] == "" and task["to_node"] == ""


def test_geometry_from_a_drawing_file_is_trusted():
    assert _from_ai(_DRAWING, source="dwg_dxf")[0].spatial_verified
    assert _from_ai(_DRAWING, source="gis")[0].spatial_verified


# ===================================================================
# Възстановяването е ПРОЦЕС, а не бариера накрая (17.08.2026)
# ===================================================================
#
# Проверено в човешкия еталон: стъпката „обратно засипване с уплътняване на
# пластове, полагане и уплътняване на трошен камък" се среща 46 пъти — по
# веднъж на участък, медиана 2 дни, разхвърляна през целия строеж.  Редът
# „възстановяване извън траншеен изкоп" пък е една задача, която ТЕЧЕ 595 дни
# успоредно с всичко.  Никъде няма момент, в който целият обект чака последния
# изкоп.  Ръководителят на проекта потвърди: „не е един път накрая, а процес —
# след всеки приключен етап се възстановява настилката".
#
# FAILURE означава: настилките пак ще чакат последната тръба на обекта.


def _sections(count: int, chain: str, network: str, prefix: str,
              refs: tuple[str, ...]) -> list[SpatialWorkPackage]:
    """`count` участъка, всеки с ДЯЛ от същите КСС редове."""
    return [
        SpatialWorkPackage(
            id=f"{prefix}{i}", network=network, chain=chain,
            branch=f"кл. {i}",
            items=tuple(PackageItem(ref, "pavement" if network == "П" else "pipe",
                                    100.0, "кв. м", ref)
                        for ref in refs))
        for i in range(1, count + 1)
    ]


class TestRestorationFollowsTheStages:
    def test_sections_sharing_rows_are_not_merged(self):
        """Един ред в осем пакета значи осем МЕСТА, не едно място осем пъти."""
        участъци = _sections(8, "pavement_section", "П", "П",
                             tuple(ref for ref, _, _ in _ROWS))

        слети, _ = merge_restoration_zones(участъци, spatial_authoritative=False)

        assert len(слети) == 8, (
            "участъците с разделени количества бяха слети в една зона — "
            "възстановяването пак чака последния изкоп")

    def test_rows_split_into_steps_are_still_merged(self):
        """Класическият дефект: по един пакет на КСС ред → едно място."""
        пакети = _pavement_packages()

        слети, бележки = merge_restoration_zones(пакети,
                                                 spatial_authoritative=False)

        assert len(слети) == 1, "обектът пак се асфалтира три пъти"
        assert бележки

    def test_each_section_waits_for_its_own_wave(self):
        """Настилка n чака вълна n от подземните работи, не всичките."""
        from src.work_package import link_cross_discipline

        тръби = _sections(4, "sewer_section", "К", "К", ("КСС.xlsx!Канал!1",))
        настилки = _sections(4, "pavement_section", "П", "П",
                             tuple(ref for ref, _, _ in _ROWS))
        пакети = тръби + настилки
        задачи = expand_packages(пакети, load_chains()).tasks

        свързани = link_cross_discipline(задачи, пакети, load_chains(),
                                         spatial_authoritative=False)
        по_ид = {str(t.get("id")): t for t in свързани}

        първа = [t for t in свързани
                 if str(t.get("parent_id")) == "П1" and t.get("chain_step")]
        предшественици = {
            str(d.get("predecessor_id") if isinstance(d, dict) else d)
            for t in първа for d in (t.get("dependencies") or [])
        }
        чужди = {p for p in предшественици
                 if p.startswith("К") and not p.startswith("К1_")}

        assert not чужди, (
            f"първата настилка чака и чужди участъци: {sorted(чужди)[:5]}")

    def test_the_last_restoration_still_waits_for_everything(self):
        """Последната настилка не бива да свърши преди последния изкоп."""
        from src.work_package import link_cross_discipline

        тръби = _sections(4, "sewer_section", "К", "К", ("КСС.xlsx!Канал!1",))
        настилки = _sections(4, "pavement_section", "П", "П",
                             tuple(ref for ref, _, _ in _ROWS))
        пакети = тръби + настилки
        свързани = link_cross_discipline(
            expand_packages(пакети, load_chains()).tasks, пакети, load_chains(),
            spatial_authoritative=False)

        последна = [t for t in свързани
                    if str(t.get("parent_id")) == "П4" and t.get("chain_step")]
        предшественици = {
            str(d.get("predecessor_id") if isinstance(d, dict) else d)
            for t in последна for d in (t.get("dependencies") or [])
        }
        участъци = {p.split("_")[0] for p in предшественици if p.startswith("К")}

        assert участъци == {"К1", "К2", "К3", "К4"}, (
            f"последната настилка чака само {sorted(участъци)}")
