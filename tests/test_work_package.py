"""Unit tests: пространствен работен пакет — слоят между КСС и задачите.

СЪПОСТАВКА С ЕТАЛОН (човешки график, 610 задачи, 2026-08-06): програмата
групираше по ДИАМЕТЪР и по условен „Фронт 1/2", а не по реални трасета.  Двата
структурни дефекта от това:

  1. Фронтовете КЛОНИРАХА количествата — 3880,5 м бордюри във Фронт 1 И още
     3880,5 във Фронт 2.  Сборът беше двойно по-голям от КСС.
  2. Зависимостите бяха само вътре в дисциплината, затова бордюрите тръгваха в
     ден 1 — преди изкопа под тях.

FAILURE означава: количество от КСС може да бъде планирано два пъти (по-дълъг
и по-скъп график, който изглежда коректен), или работа може да бъде тихо
изпусната, защото не попада в нито една стъпка от технологичната верига.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.provenance import QuantityRow, SourceRef  # noqa: E402
from src.work_package import (  # noqa: E402
    PackageItem,
    SpatialWorkPackage,
    assign_fronts,
    check_conservation,
    conservation_messages,
    expand_packages,
    link_cross_discipline,
    load_chains,
    parse_package_name,
)


def _row(ref_row: int, qty: float, sheet: str = "Канализация") -> QuantityRow:
    return QuantityRow(
        description="Тръби PP DN300", quantity=qty, unit="m",
        source=SourceRef(document="КСС.xlsx", sheet=sheet, row=ref_row), raw={},
    )


def _pkg(pid: str, ref: str, qty: float, *, chain="sewer_section",
         cls="laying", network="К", street="ул. Първа") -> SpatialWorkPackage:
    return SpatialWorkPackage(
        id=pid, network=network, chain=chain, street=street,
        items=(PackageItem(source_ref=ref, activity_class=cls, quantity=qty,
                           unit="m"),),
    )


# ---------------------------------------------------------------------------
# Инвариантът: Σ по пакети == КСС
# ---------------------------------------------------------------------------


def test_conservation_ok_when_split_across_packages():
    """40/60 разпределение на един ред е ВАЛИДНО — сборът е точен."""
    rows = [_row(4, 1000.0)]
    ref = rows[0].ref
    packages = [_pkg("P1", ref, 400.0), _pkg("P2", ref, 600.0)]

    report = check_conservation(packages, rows)

    assert report["ok"] is True
    assert not report["over"] and not report["short"]


def test_conservation_blocks_cloned_quantities():
    """КОРЕННИЯТ ДЕФЕКТ: два фронта с ПЪЛНОТО количество → блокиращо."""
    rows = [_row(4, 3880.5)]
    ref = rows[0].ref
    packages = [_pkg("Фронт1", ref, 3880.5), _pkg("Фронт2", ref, 3880.5)]

    report = check_conservation(packages, rows)

    assert report["ok"] is False
    assert ref in report["over"]
    assert report["over"][ref]["planned"] == pytest.approx(7761.0)
    assert report["over"][ref]["required"] == pytest.approx(3880.5)
    assert any("ПРЕВИШЕНО" in m for m in conservation_messages(report))


def test_conservation_flags_short_and_missing():
    rows = [_row(4, 1000.0), _row(5, 500.0)]
    packages = [_pkg("P1", rows[0].ref, 700.0)]

    report = check_conservation(packages, rows)

    assert report["ok"] is False
    assert rows[0].ref in report["short"]
    assert rows[1].ref in report["missing"]


def test_conservation_flags_invented_ref():
    rows = [_row(4, 100.0)]
    packages = [_pkg("P1", rows[0].ref, 100.0),
                _pkg("P2", "КСС.xlsx!Няма!999", 50.0)]

    report = check_conservation(packages, rows)

    assert report["ok"] is False
    assert "КСС.xlsx!Няма!999" in report["unknown_ref"]


def test_conservation_tolerance_absorbs_rounding():
    rows = [_row(4, 1000.0)]
    ref = rows[0].ref
    packages = [_pkg("P1", ref, 333.3), _pkg("P2", ref, 333.3),
                _pkg("P3", ref, 333.3)]

    assert check_conservation(packages, rows)["ok"] is True


def test_package_item_requires_citation():
    """Цитатът е задължителен НА НИВО ТИП — не може да се пропусне мълчаливо."""
    with pytest.raises(ValueError):
        PackageItem(source_ref="", activity_class="laying", quantity=10.0)


# ---------------------------------------------------------------------------
# Фронтове: разпределят пакети, не преписват позиции
# ---------------------------------------------------------------------------


def test_assign_fronts_preserves_total_quantity():
    """Разпределянето по фронтове НЕ МОЖЕ да промени сбора — структурно."""
    rows = [_row(i, 100.0) for i in range(4, 10)]
    packages = [_pkg(f"P{i}", r.ref, 100.0) for i, r in enumerate(rows, 1)]

    spread = assign_fronts(packages, 2)

    assert check_conservation(spread, rows)["ok"] is True
    assert len(spread) == len(packages)
    assert {p.front for p in spread} == {"Фронт 1", "Фронт 2"}


def test_assign_fronts_balances_each_network_separately():
    """Един фронт не бива да получи цялата канализация, а другият водопровода."""
    packages = [
        _pkg("K1", "r!s!1", 100.0, network="К"),
        _pkg("K2", "r!s!2", 100.0, network="К"),
        _pkg("V1", "r!s!3", 100.0, network="В", chain="water_section"),
        _pkg("V2", "r!s!4", 100.0, network="В", chain="water_section"),
    ]

    spread = {p.id: p.front for p in assign_fronts(packages, 2)}

    assert spread["K1"] != spread["K2"]
    assert spread["V1"] != spread["V2"]


def test_assign_fronts_single_front_labels_everything():
    packages = [_pkg("P1", "r!s!1", 10.0), _pkg("P2", "r!s!2", 10.0)]
    assert {p.front for p in assign_fronts(packages, 1)} == {"Фронт 1"}


# ---------------------------------------------------------------------------
# Имена на участъци (форматът на еталона)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,network,start,end", [
    ("кл. 48 от РШ 36 до Пр. Ш 1", "К", "РШ 36", "Пр. Ш 1"),
    ("КЛ. 25 - И от ОТ 27 А до ОТ 25", "В", "ОТ 27 А", "ОТ 25"),
    ("кл. 19 от РШ 25 до РШ 22", "К", "РШ 25", "РШ 22"),
])
def test_parse_package_name_reads_nodes_and_network(name, network, start, end):
    """Мрежата се извежда от ТИПА възел (РШ→К, ОТ/Т→В), не се гадае по думи."""
    parsed = parse_package_name(name)
    assert parsed["network"] == network
    assert parsed["start_node"] == start
    assert parsed["end_node"] == end


def test_parse_package_name_survives_garbage():
    parsed = parse_package_name("нещо без възли")
    assert parsed["network"] == ""
    assert parsed["start_node"] == ""


# ---------------------------------------------------------------------------
# Разгъване: пакет → верига + WBS
# ---------------------------------------------------------------------------


def test_expand_builds_wbs_hierarchy():
    """Еталонът има 3 нива: корен → участък → стъпки.  Нашият имаше 1."""
    pkg = SpatialWorkPackage(
        id="P1", network="К", chain="sewer_section", street="ул. Първа",
        branch="кл. 48", start_node="РШ 36", end_node="РШ 40",
        items=(PackageItem("КСС!К!4", "laying", 300.0, "m"),),
    )

    result = expand_packages([pkg])
    by_id = {t["id"]: t for t in result.tasks}

    root = by_id["WBS_CONSTRUCTION"]
    assert root["is_summary"] is True
    assert by_id["P1"]["parent_id"] == "WBS_CONSTRUCTION"
    assert by_id["P1"]["name"] == "кл. 48 от РШ 36 до РШ 40"
    steps = [t for t in result.tasks if t.get("parent_id") == "P1"]
    assert steps, "участъкът трябва да има стъпки"
    assert all(t["parent_id"] == "P1" for t in steps)


def test_expand_chains_steps_finish_to_start():
    pkg = SpatialWorkPackage(
        id="P1", network="К", chain="sewer_section",
        items=(PackageItem("КСС!К!4", "laying", 300.0, "m"),),
    )
    tasks = expand_packages([pkg]).tasks
    steps = [t for t in tasks if t.get("parent_id") == "P1"]

    assert len(steps) >= 2
    for earlier, later in zip(steps, steps[1:]):
        assert any(d["predecessor_id"] == earlier["id"]
                   for d in later["dependencies"]), \
            f"{later['id']} не зависи от {earlier['id']}"


def test_expand_cites_source_ref_on_quantity_tasks():
    pkg = SpatialWorkPackage(
        id="P1", network="К", chain="sewer_section",
        items=(PackageItem("КСС!К!4", "laying", 300.0, "m"),),
    )
    tasks = expand_packages([pkg]).tasks
    carriers = [t for t in tasks if t.get("source_ref")]

    assert carriers, "количеството трябва да стигне до задача с цитат"
    assert all(t["source_ref"] == "КСС!К!4" for t in carriers)
    assert sum(t["quantity"] for t in carriers) == pytest.approx(300.0)


def test_expand_reports_unplaced_quantity_instead_of_dropping_it():
    """Тихо изпуснато количество е точно дефектът, който модулът предотвратява."""
    pkg = SpatialWorkPackage(
        id="P1", network="К", chain="sewer_section",
        items=(PackageItem("КСС!К!9", "cable", 120.0, "m"),),
    )

    result = expand_packages([pkg])

    assert result.unplaced, "неразположено количество трябва да се докладва"
    assert result.unplaced[0]["source_ref"] == "КСС!К!9"
    assert result.warnings


def test_expand_rejects_unknown_chain():
    pkg = SpatialWorkPackage(
        id="P1", network="К", chain="няма_такава",
        items=(PackageItem("КСС!К!4", "laying", 10.0, "m"),),
    )
    result = expand_packages([pkg])
    assert result.unplaced and result.unplaced[0]["reason"] == "unknown_chain"


def test_expand_attaches_crew_from_template():
    pkg = SpatialWorkPackage(
        id="P1", network="К", chain="sewer_section",
        items=(PackageItem("КСС!К!4", "laying", 300.0, "m"),),
    )
    tasks = expand_packages([pkg]).tasks
    working = [t for t in tasks if t.get("chain_step")]

    assert working
    assert all(t["resources"] for t in working), "бригадата идва от шаблона"
    assert any("Ръководител работна група" in t["resources"] for t in working)


# ---------------------------------------------------------------------------
# Между дисциплините — в РАМКИТЕ на един участък
# ---------------------------------------------------------------------------


def test_pavement_waits_for_backfill_on_same_alignment():
    """КОРЕННИЯТ ДЕФЕКТ №2: настилката тръгваше в ден 1, без връзка с изкопа."""
    sewer = SpatialWorkPackage(
        id="K1", network="К", chain="sewer_section", street="ул. Първа",
        items=(PackageItem("КСС!К!4", "laying", 300.0, "m"),
               PackageItem("КСС!К!5", "backfill", 300.0, "m3"),),
    )
    pavement = SpatialWorkPackage(
        id="P1", network="П", chain="pavement_section", street="ул. Първа",
        items=(PackageItem("КСС!П!7", "pavement", 900.0, "m2"),),
    )

    packages = [sewer, pavement]
    tasks = expand_packages(packages).tasks
    linked = link_cross_discipline(tasks, packages)
    by_id = {t["id"]: t for t in linked}

    base = by_id["P1_base_course"]
    preds = {d["predecessor_id"] for d in base["dependencies"]}
    assert any(p.startswith("K1_connections_backfill") for p in preds), \
        "настилката трябва да чака засипката на СЪЩОТО трасе"


def test_cross_discipline_ignores_other_streets():
    sewer = SpatialWorkPackage(
        id="K1", network="К", chain="sewer_section", street="ул. Първа",
        items=(PackageItem("КСС!К!5", "backfill", 300.0, "m3"),),
    )
    pavement = SpatialWorkPackage(
        id="P9", network="П", chain="pavement_section", street="ул. Друга",
        items=(PackageItem("КСС!П!7", "pavement", 900.0, "m2"),),
    )

    packages = [sewer, pavement]
    linked = link_cross_discipline(expand_packages(packages).tasks, packages)
    by_id = {t["id"]: t for t in linked}

    preds = {d["predecessor_id"] for d in by_id["P9_base_course"]["dependencies"]}
    assert not any(p.startswith("K1_") for p in preds), \
        "различни улици не бива да се обвързват"


# ---------------------------------------------------------------------------
# Конфигурацията
# ---------------------------------------------------------------------------


def test_chains_config_is_loadable_and_complete():
    cfg = load_chains()
    assert set(cfg["chains"]) >= {"sewer_section", "water_section",
                                  "pavement_section", "structure"}
    for name, chain in cfg["chains"].items():
        assert chain["steps"], f"{name} без стъпки"
        for step in chain["steps"]:
            assert step["key"] and step["name"], f"{name}: стъпка без ключ/име"
            assert isinstance(step.get("covers"), list)


def test_chain_covers_use_canonical_activity_classes():
    """`covers` трябва да е на речника на provenance, иначе покритието не ги вижда."""
    from src.provenance import _PRODUCTION_CLASSES

    cfg = load_chains()
    for name, chain in cfg["chains"].items():
        for step in chain["steps"]:
            for cls in step["covers"]:
                assert cls in _PRODUCTION_CLASSES, \
                    f"{name}.{step['key']}: непознат клас {cls!r}"


# ---------------------------------------------------------------------------
# Регресии, хванати при смоук прогон до MS Project XML (2026-08-06)
# ---------------------------------------------------------------------------


def test_one_quantity_produces_exactly_one_task():
    """Количество, пасващо на НЯКОЛКО стъпки, не бива да се размножи.

    Редът за настилка е клас `pavement` и пасва на „основен пласт", „бордюри"
    И „асфалт" — без защита 900 m² стават 2700 m² работа, тоест дефектът,
    който целият модул премахва, се възпроизвежда отвътре.
    """
    pkg = SpatialWorkPackage(
        id="P1", network="П", chain="pavement_section", street="ул. Първа",
        items=(PackageItem("КСС!П!7", "pavement", 900.0, "m2",
                           "Възстановяване на асфалтова настилка"),),
    )

    tasks = expand_packages([pkg]).tasks
    carriers = [t for t in tasks if t.get("source_ref") == "КСС!П!7"]

    assert len(carriers) == 1, [t["id"] for t in carriers]
    assert carriers[0]["quantity"] == pytest.approx(900.0)


def test_keywords_route_row_to_the_right_step():
    """Бордюрите отиват в стъпката за бордюри, асфалтът — в тази за асфалт."""
    pkg = SpatialWorkPackage(
        id="P1", network="П", chain="pavement_section",
        items=(PackageItem("КСС!П!7", "pavement", 900.0, "m2",
                           "Възстановяване на асфалтова настилка"),
               PackageItem("КСС!П!8", "pavement", 300.0, "m",
                           "Доставка и полагане на бетонови бордюри"),),
    )

    by_ref = {t["source_ref"]: t["chain_step"]
              for t in expand_packages([pkg]).tasks if t.get("source_ref")}

    assert by_ref["КСС!П!7"] == "asphalt"
    assert by_ref["КСС!П!8"] == "kerbs"


def test_item_without_description_is_placed_not_dropped():
    """Ключовите думи са разграничител, не гейт — липсващ текст не трие работа."""
    pkg = SpatialWorkPackage(
        id="P1", network="П", chain="pavement_section",
        items=(PackageItem("КСС!П!7", "pavement", 900.0, "m2"),),
    )
    result = expand_packages([pkg])

    assert result.unplaced == []
    assert len([t for t in result.tasks if t.get("source_ref")]) == 1


def test_cross_discipline_link_survives_a_step_the_chain_does_not_define():
    """Резервният избор пази връзката, когато правилото сочи чужда стъпка.

    Откакто стъпките са задължителни (одит 2026-08-07), стъпка от СВОЯТА
    верига винаги съществува.  Резервният избор остава нужен за правило,
    което сочи стъпка, каквато веригата изобщо не дефинира — иначе връзката
    изчезва МЪЛЧАЛИВО и настилката пак тръгва в ден 1.
    """
    chains = load_chains()
    # Правило към стъпка, която `pavement_section` няма.
    chains["cross_discipline"]["rules"] = [{
        "predecessor_chain": "sewer_section",
        "predecessor_step": "connections_backfill",
        "successor_chain": "pavement_section",
        "successor_step": "няма_такава_стъпка",
        "type": "FS", "lag_days": 0, "why": "тест",
    }]

    sewer = SpatialWorkPackage(
        id="K1", network="К", chain="sewer_section", street="ул. Първа",
        items=(PackageItem("КСС!К!5", "backfill", 300.0, "m3", "Обратна засипка"),),
    )
    pavement = SpatialWorkPackage(
        id="P1", network="П", chain="pavement_section", street="ул. Първа",
        items=(PackageItem("КСС!П!7", "pavement", 900.0, "m2",
                           "Възстановяване на асфалтова настилка"),),
    )

    packages = [sewer, pavement]
    linked = link_cross_discipline(
        expand_packages(packages, chains).tasks, packages, chains)
    by_id = {t["id"]: t for t in linked}

    first = by_id["P1_base_course"]     # първата стъпка на наследника
    preds = {d["predecessor_id"] for d in first["dependencies"]}
    assert any(p.startswith("K1_") for p in preds), \
        "настилката трябва да чака подземната работа и при непозната стъпка"


def test_every_chain_step_appears_even_without_a_priced_row():
    """ОДИТ 2026-08-07: от 6-степенната верига в готовия файл оставаха 3 задачи.

    Изкопът, изпитването и засипката се извършват независимо дали КСС ги
    остойностява отделно — в реалния търг те са вътре в тръбния ред.  Затова
    `covers` разпределя количества, но НЕ решава дали дейността съществува.
    """
    sewer = SpatialWorkPackage(
        id="K1", network="К", chain="sewer_section",
        items=(PackageItem("КСС!К!4", "laying", 300.0, "m",
                           "Изграждане на смесена канализационна мрежа"),),
    )
    water = SpatialWorkPackage(
        id="V1", network="В", chain="water_section",
        items=(PackageItem("КСС!В!9", "laying", 500.0, "m",
                           "Реконструкция водопровод Ф200"),),
    )

    cfg = load_chains()
    for pkg in (sewer, water):
        steps = [t for t in expand_packages([pkg], cfg).tasks if t.get("chain_step")]
        expected = len(cfg["chains"][pkg.chain]["steps"])
        assert len(steps) == expected, \
            f"{pkg.chain}: {len(steps)} стъпки вместо {expected}"

    keys = {t["chain_step"] for t in expand_packages([sewer]).tasks if t.get("chain_step")}
    assert {"demolition_excavation", "leak_test", "connections_backfill"} <= keys


# ---------------------------------------------------------------------------
# Инженерен обхват: проектиране, разрешения, приемане (P1)
# ---------------------------------------------------------------------------


def test_design_package_goes_under_its_own_wbs_root():
    """Проектирането НЕ е под „СТРОИТЕЛСТВО" — еталонът има отделен клон."""
    design = SpatialWorkPackage(id="D1", network="ПР", chain="design",
                                name="ПРОЕКТИРАНЕ")
    build = SpatialWorkPackage(
        id="K1", network="К", chain="sewer_section",
        items=(PackageItem("КСС!К!4", "laying", 100.0, "m", "Тръби PP"),))

    by_id = {t["id"]: t for t in expand_packages([design, build]).tasks}

    assert by_id["D1"]["parent_id"] == "WBS_DESIGN"
    assert by_id["K1"]["parent_id"] == "WBS_CONSTRUCTION"
    assert by_id["WBS_DESIGN"]["name"] == "ПРОЕКТИРАНЕ"


def test_unused_wbs_roots_are_not_created():
    """Договор без проектиране не бива да носи празен клон „ПРОЕКТИРАНЕ"."""
    build = SpatialWorkPackage(
        id="K1", network="К", chain="sewer_section",
        items=(PackageItem("КСС!К!4", "laying", 100.0, "m", "Тръби PP"),))

    ids = {t["id"] for t in expand_packages([build]).tasks}

    assert "WBS_CONSTRUCTION" in ids
    assert "WBS_DESIGN" not in ids
    assert "WBS_ACCEPTANCE" not in ids


def test_design_approvals_are_milestones():
    """Съгласуванията са ТОЧКИ, не работа с продължителност."""
    design = SpatialWorkPackage(id="D1", network="ПР", chain="design")
    tasks = {t.get("chain_step"): t for t in expand_packages([design]).tasks}

    assert tasks["building_permit"]["milestone"] is True
    assert tasks["building_permit"]["duration"] == 0
    assert tasks["water"]["duration"] > 0          # проектната част е работа


def test_contractual_flag_reaches_the_task():
    """Само договорните точки получават краен срок в MS Project."""
    acceptance = SpatialWorkPackage(id="A1", network="ПР", chain="acceptance")
    tasks = {t.get("chain_step"): t for t in expand_packages([acceptance]).tasks}

    assert tasks["handover"]["contractual"] is True
    assert tasks["as_built"].get("contractual") is None


# ---------------------------------------------------------------------------
# Пренасочване на количества, попаднали в грешна верига
# ---------------------------------------------------------------------------


def test_pavement_row_in_a_sewer_package_is_rerouted():
    """ЖИВ ПРОГОН: 6 от 10 отпадаха с „непокрити редове" точно заради това.

    Моделът закача ред за настилка към канализационен пакет.  `sewer_section`
    не покрива клас `pavement`, тоест количеството остава без стъпка и работата
    изчезва от графика — при това Σ=КСС продължава да минава, защото
    количеството Е разпределено.  Затова дефектът беше невидим за инварианта.
    """
    from src.work_package import reroute_uncoverable_items

    pkg = SpatialWorkPackage(
        id="K1", network="К", chain="sewer_section", street="ул. Първа",
        items=(PackageItem("КСС!К!4", "laying", 300.0, "m", "Тръби PP DN300"),
               PackageItem("КСС!П!7", "pavement", 900.0, "m2",
                           "Възстановяване на асфалтова настилка")),
    )

    packages, notes = reroute_uncoverable_items([pkg])
    by_id = {p.id: p for p in packages}

    assert len(by_id["K1"].items) == 1
    twin = next(p for p in packages if p.chain == "pavement_section")
    assert [i.source_ref for i in twin.items] == ["КСС!П!7"]
    assert twin.street == "ул. Първа", "близнакът трябва да е на СЪЩОТО трасе"
    assert notes


def test_rerouting_preserves_the_total_quantity():
    """Преместването не бива да променя сбора — само носителя."""
    from src.work_package import reroute_uncoverable_items

    rows = [_row(4, 300.0), _row(7, 900.0, sheet="Пътна")]
    pkg = SpatialWorkPackage(
        id="K1", network="К", chain="sewer_section", street="ул. Първа",
        items=(PackageItem(rows[0].ref, "laying", 300.0, "m", "Тръби PP"),
               PackageItem(rows[1].ref, "pavement", 900.0, "m2", "Асфалт")),
    )

    before = check_conservation([pkg], rows)["totals"]
    packages, _ = reroute_uncoverable_items([pkg])
    after = check_conservation(packages, rows)["totals"]

    assert before == after


def test_rerouted_quantity_reaches_a_real_task():
    """Целта на преместването: работата да съществува в графика."""
    from src.work_package import reroute_uncoverable_items

    pkg = SpatialWorkPackage(
        id="K1", network="К", chain="sewer_section", street="ул. Първа",
        items=(PackageItem("КСС!П!7", "pavement", 900.0, "m2",
                           "Възстановяване на асфалтова настилка"),),
    )

    packages, _ = reroute_uncoverable_items([pkg])
    result = expand_packages(packages)

    assert result.unplaced == []
    carriers = [t for t in result.tasks if t.get("source_ref") == "КСС!П!7"]
    assert len(carriers) == 1


def test_class_without_a_home_chain_is_left_to_be_reported():
    """Не измисляме дом за неизвестен клас — по-добре докладван, отколкото скрит."""
    from src.work_package import reroute_uncoverable_items

    pkg = SpatialWorkPackage(
        id="P1", network="П", chain="pavement_section",
        items=(PackageItem("КСС!X!1", "excavation", 50.0, "m3", "Изкоп"),),
    )

    packages, notes = reroute_uncoverable_items([pkg])

    assert len(packages) == 1 and len(packages[0].items) == 1
    assert notes == []
    assert expand_packages(packages).unplaced, "остава видим като неразположен"


# ---------------------------------------------------------------------------
# Свиване на разпределителен дрейф
# ---------------------------------------------------------------------------


def test_slight_over_allocation_is_scaled_to_the_boq():
    """ЖИВ ПРОГОН: 6 от 10 падаха с „превишено количество" заради дрейф.

    Моделът дели ред между участъци на око и сборът излиза 110%.  Кой участък
    колко поема е негова преценка; ОБЩОТО е факт от документа.
    """
    from src.work_package import normalize_over_allocation

    rows = [_row(4, 1000.0)]
    ref = rows[0].ref
    packages = [_pkg("P1", ref, 600.0), _pkg("P2", ref, 500.0)]   # 1100 = 110%

    adjusted, notes = normalize_over_allocation(packages, rows)

    assert check_conservation(adjusted, rows)["ok"] is True
    total = sum(i.quantity for p in adjusted for i in p.items)
    assert total == pytest.approx(1000.0)
    # Пропорцията на модела е запазена: 600/500 → 6/5
    got = [sum(i.quantity for i in p.items) for p in adjusted]
    assert got[0] / got[1] == pytest.approx(600 / 500)
    assert notes


def test_cloned_quantities_are_not_silently_halved():
    """Двоен сбор е КЛОНИРАНЕ, не закръгляне — трябва да остане блокиращо."""
    from src.work_package import normalize_over_allocation

    rows = [_row(4, 3880.5)]
    ref = rows[0].ref
    packages = [_pkg("F1", ref, 3880.5), _pkg("F2", ref, 3880.5)]

    adjusted, notes = normalize_over_allocation(packages, rows)

    assert notes == []
    assert check_conservation(adjusted, rows)["ok"] is False


def test_a_big_shortfall_is_never_inflated():
    """Липсваща работа не се измисля: 30% недостиг е пропуснат участък."""
    from src.work_package import normalize_over_allocation

    rows = [_row(4, 1000.0)]
    packages = [_pkg("P1", rows[0].ref, 700.0)]

    adjusted, notes = normalize_over_allocation(packages, rows)

    assert notes == []
    assert sum(i.quantity for p in adjusted for i in p.items) == pytest.approx(700.0)
    assert check_conservation(adjusted, rows)["ok"] is False


# ===================================================================
# ПРОБА 10.08.2026 — дрейфът има ДВЕ посоки
#
# Опазването падна в 22 от 26 непразни прогона.  Свиването се прилагаше само
# нагоре; ред, разпределен на 92%, не се поправяше от нищо и блокираше тихо.
# Допитването пита само за редове с НУЛЕВО разпределение, а пренасочването
# сменя носителя, не количеството — тоест частичното разпределение нямаше
# нито един път за поправка.
#
# FAILURE означава: сбор, който се разминава с КСС с няколко процента заради
# закръгляне на модела, пак вали иначе редовен график.
# ===================================================================

def test_a_small_shortfall_is_stretched_to_the_boq():
    """Общото е факт от документа — същият довод като при превишението."""
    from src.work_package import normalize_over_allocation

    rows = [_row(4, 1000.0)]
    ref = rows[0].ref
    packages = [_pkg("P1", ref, 500.0), _pkg("P2", ref, 420.0)]   # 920 = 92%

    adjusted, notes = normalize_over_allocation(packages, rows)

    assert check_conservation(adjusted, rows)["ok"] is True
    assert sum(i.quantity for p in adjusted
               for i in p.items) == pytest.approx(1000.0)
    got = [sum(i.quantity for i in p.items) for p in adjusted]
    assert got[0] / got[1] == pytest.approx(500 / 420)   # пропорцията е негова
    assert notes and "не достига" in notes[0]


def test_an_unallocated_row_is_left_to_the_follow_up_question():
    """Нула разпределено няма пропорция, която да се мащабира."""
    from src.work_package import normalize_over_allocation

    rows = [_row(4, 1000.0)]
    adjusted, notes = normalize_over_allocation([], rows)

    assert notes == []
    assert check_conservation(adjusted, rows)["missing"] == [rows[0].ref]


def test_drift_within_tolerance_is_left_alone():
    """2% допуск си остава допуск — не се пипа заради самото пипане."""
    from src.work_package import normalize_over_allocation

    rows = [_row(4, 1000.0)]
    packages = [_pkg("P1", rows[0].ref, 995.0)]

    adjusted, notes = normalize_over_allocation(packages, rows)

    assert notes == []
    assert sum(i.quantity for p in adjusted
               for i in p.items) == pytest.approx(995.0)


def test_pipe_row_stranded_in_a_pavement_package_is_rerouted_by_description():
    """`laying` го има и в двете тръбни вериги — описанието решава коя."""
    from src.work_package import reroute_uncoverable_items

    pkg = SpatialWorkPackage(
        id="P1", network="П", chain="pavement_section", street="ул. Първа",
        items=(PackageItem("КСС!К!4", "laying", 300.0, "m",
                           "Изграждане на смесена канализационна мрежа"),
               PackageItem("КСС!В!9", "laying", 200.0, "m",
                           "Реконструкция на водопровод Ф200"),),
    )

    packages, notes = reroute_uncoverable_items([pkg])
    chains = {p.chain for p in packages}

    assert "sewer_section" in chains and "water_section" in chains
    assert len(notes) == 2
    assert expand_packages(packages).unplaced == []


def test_undecidable_description_is_left_visible():
    from src.work_package import reroute_uncoverable_items

    pkg = SpatialWorkPackage(
        id="P1", network="П", chain="pavement_section",
        items=(PackageItem("КСС!X!1", "laying", 10.0, "m", "нещо неясно"),),
    )
    packages, notes = reroute_uncoverable_items([pkg])

    assert notes == []
    assert expand_packages(packages).unplaced


# ---------------------------------------------------------------------------
# Договорен обхват и затваряне на графика (одит 2026-08-07)
# ---------------------------------------------------------------------------


def test_contract_phases_exist_without_any_boq_row():
    """Проектиране, мобилизация, надзор и приемане НЕ идват от КСС.

    В одитирания файл имаше само СТРОИТЕЛСТВО и нула milestone-и, защото тези
    фази съществуваха само като конфигурация и нищо не ги създаваше.
    """
    from src.work_package import contract_packages

    minimal = contract_packages(with_design=False)
    full = contract_packages(with_design=True)

    assert {p.chain for p in minimal} == {"mobilization", "acceptance"}
    assert {p.chain for p in full} == {"design", "mobilization",
                                       "supervision", "acceptance"}
    assert all(p.items == () for p in full), "фазите нямат количества"


def test_contract_phases_do_not_touch_the_quantity_invariant():
    """Фазите са обхват, не работа по сметка — не бива да влизат в Σ=КСС."""
    from src.work_package import contract_packages

    rows = [_row(4, 1000.0)]
    spatial = [_pkg("K1", rows[0].ref, 1000.0)]

    assert check_conservation(spatial + contract_packages(with_design=True),
                              rows)["ok"] is True


def test_schedule_closes_on_a_single_final_milestone():
    """ОДИТ: 12 задачи без наследник и нула milestone-и.

    Щом графикът има дузина висящи краища, „кога свършва обектът" няма
    еднозначен отговор и критичният път не значи нищо.
    """
    from src.work_package import contract_packages, link_contract_phases

    packages = [
        SpatialWorkPackage(id="K1", network="К", chain="sewer_section",
                           street="ул. Първа",
                           items=(PackageItem("КСС!К!4", "laying", 300.0, "m",
                                              "Изграждане на канализационна мрежа"),)),
        SpatialWorkPackage(id="P1", network="П", chain="pavement_section",
                           street="ул. Първа",
                           items=(PackageItem("КСС!П!7", "pavement", 900.0, "m2",
                                              "Възстановяване на асфалтова настилка"),)),
    ]
    packages += contract_packages(with_design=True)

    tasks = link_cross_discipline(expand_packages(packages).tasks, packages)
    tasks, _ = link_contract_phases(tasks, packages)

    successors = {d["predecessor_id"] for t in tasks
                  for d in (t.get("dependencies") or [])}
    loose = [t for t in tasks
             if not t.get("is_summary") and t["id"] not in successors]

    assert len(loose) == 1, [t["id"] for t in loose]
    assert loose[0]["milestone"] is True
    assert loose[0]["contractual"] is True


def test_construction_waits_for_the_site_to_open():
    from src.work_package import contract_packages, link_contract_phases

    packages = [
        SpatialWorkPackage(id="K1", network="К", chain="sewer_section",
                           items=(PackageItem("КСС!К!4", "laying", 300.0, "m",
                                              "Изграждане на канализационна мрежа"),)),
    ] + contract_packages(with_design=False)

    tasks, _ = link_contract_phases(expand_packages(packages).tasks, packages)
    by_id = {t["id"]: t for t in tasks}

    first_build = by_id["K1_survey"]
    preds = {d["predecessor_id"] for d in first_build["dependencies"]}
    assert any(p.startswith("ФАЗА_MOBILIZATION") for p in preds)


def test_phase_wiring_creates_no_cycles():
    from src.schedule_builder import ScheduleBuilder
    from src.work_package import contract_packages, link_contract_phases

    packages = [
        SpatialWorkPackage(id=f"K{i}", network="К", chain="sewer_section",
                           street=f"ул. {i}",
                           items=(PackageItem(f"КСС!К!{i}", "laying", 100.0 * i, "m",
                                              "Изграждане на канализационна мрежа"),))
        for i in (1, 2, 3)
    ] + contract_packages(with_design=True)

    tasks = link_cross_discipline(expand_packages(packages).tasks, packages)
    tasks, _ = link_contract_phases(tasks, packages)

    result = ScheduleBuilder().reschedule(tasks)
    assert result["warnings"] == []
