"""Unit tests: графикът може да обясни собствения си срок.

ОДИТ 10.08.2026, P0.4: „Export diagnostics: design/mobilization/construction/
supervision/acceptance span, critical path duration, top 10 resource-induced
delays, top 10 dependency-induced delays."

И P1.3: структурните флагове, без които „чист" не значи нищо.

Централното разграничение е между двата вида забавяне.  Досега те се смесваха
в едно число „срокът е 1246 дни" и не се знаеше кое го причинява — технология
или недостиг на бригади.  Двете се лекуват по различен начин.

FAILURE означава: пак не може да се каже ЗАЩО графикът е толкова дълъг.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.schedule_diagnostics import (  # noqa: E402
    HARD_STRUCTURAL_FLAGS,
    critical_path_days,
    delay_breakdown,
    duration_report,
    phase_spans,
    structural_flags,
)


def _task(tid, root, start, end, deps=None, **kw):
    task = {"id": tid, "name": tid, "wbs_root": root, "start_day": start,
            "end_day": end, "duration": end - start + 1,
            "dependencies": deps or []}
    task.update(kw)
    return task


def _fs(pred, lag=0):
    return [{"predecessor_id": pred, "type": "FS", "lag_days": lag}]


# ---------------------------------------------------------------------------
# Фазите
# ---------------------------------------------------------------------------


def test_each_phase_reports_its_own_span():
    tasks = [
        _task("D1", "design", 1, 120),
        _task("M1", "mobilization", 121, 130),
        _task("K1", "construction", 131, 900),
        _task("S1", "supervision", 131, 900),
        _task("A1", "acceptance", 901, 930),
    ]

    spans = phase_spans(tasks)

    assert spans["design"]["days"] == 120
    assert spans["construction"]["days"] == 770
    assert spans["acceptance"]["start_day"] == 901
    assert set(spans) == {"design", "mobilization", "construction",
                          "supervision", "acceptance"}


def test_summaries_do_not_distort_the_spans():
    """Обобщаващата е сбор на децата си, не отделна работа."""
    tasks = [
        _task("WBS", "construction", 1, 999, is_summary=True),
        _task("K1", "construction", 10, 20),
    ]

    assert phase_spans(tasks)["construction"]["days"] == 11


# ---------------------------------------------------------------------------
# Двата вида забавяне
# ---------------------------------------------------------------------------


def test_waiting_for_a_predecessor_is_a_dependency_delay():
    tasks = [_task("A", "construction", 1, 10),
             _task("B", "construction", 11, 20, _fs("A"))]

    delays = delay_breakdown(tasks)

    assert [d["id"] for d in delays["dependency"]] == ["B"]
    assert delays["dependency"][0]["days"] == 10
    assert delays["resource"] == []


def test_waiting_for_a_free_crew_is_a_resource_delay():
    """Мрежата допуска ден 11, а задачата тръгва ден 40 — разликата е екип."""
    tasks = [_task("A", "construction", 1, 10),
             _task("B", "construction", 40, 50, _fs("A"), team="ЕВ1")]

    delays = delay_breakdown(tasks)

    assert [d["id"] for d in delays["resource"]] == ["B"]
    assert delays["resource"][0]["days"] == 29
    assert delays["resource"][0]["team"] == "ЕВ1"


def test_a_start_to_start_link_does_not_look_like_a_delay():
    """Застъпването е ускорение, не забавяне."""
    tasks = [_task("A", "construction", 1, 50),
             _task("B", "construction", 1, 50,
                   [{"predecessor_id": "A", "type": "SS", "lag_days": 0}])]

    delays = delay_breakdown(tasks)

    assert delays["dependency"] == [] and delays["resource"] == []


def test_the_longest_delays_come_first():
    """Двете чакат РАЗЛИЧНИ предшественици — иначе забавянето им е еднакво."""
    tasks = [_task("SHORT", "construction", 1, 10),
             _task("LONG", "construction", 1, 50),
             _task("S", "construction", 11, 15, _fs("SHORT")),
             _task("L", "construction", 51, 55, _fs("LONG"))]

    assert [d["id"] for d in delay_breakdown(tasks)["dependency"]] == ["L", "S"]


def test_two_tasks_behind_the_same_predecessor_wait_equally():
    """Разликата между тях е ресурс, не технология — и се отчита като такава."""
    tasks = [_task("A", "construction", 1, 10),
             _task("EARLY", "construction", 11, 15, _fs("A")),
             _task("LATE", "construction", 90, 95, _fs("A"))]

    delays = delay_breakdown(tasks)

    assert {d["days"] for d in delays["dependency"]} == {10}
    assert [d["id"] for d in delays["resource"]] == ["LATE"]


def test_only_the_top_n_are_reported():
    tasks = [_task("A", "construction", 1, 5)]
    tasks += [_task(f"T{i}", "construction", 10 + i, 20 + i, _fs("A"))
              for i in range(20)]

    assert len(delay_breakdown(tasks, top=10)["dependency"]) == 10


# ---------------------------------------------------------------------------
# Критичният път и целият отчет
# ---------------------------------------------------------------------------


def test_the_critical_path_is_measured_when_marked():
    tasks = [_task("A", "construction", 1, 10, is_critical=True),
             _task("B", "construction", 11, 40, _fs("A"), is_critical=True),
             _task("C", "construction", 11, 15, _fs("A"))]

    assert critical_path_days(tasks) == 40


def test_an_unmeasured_critical_path_is_zero_not_invented():
    assert critical_path_days([_task("A", "construction", 1, 10)]) == 0


def test_the_report_carries_everything_the_audit_asked_for():
    tasks = [_task("D1", "design", 1, 120),
             _task("K1", "construction", 121, 900, _fs("D1"), is_critical=True)]

    report = duration_report(tasks)

    assert report["total_days"] == 900
    assert set(report) >= {"total_days", "critical_path_days", "phases",
                           "top_dependency_delays", "top_resource_delays"}
    assert report["phases"]["design"]["days"] == 120


# ---------------------------------------------------------------------------
# Структурните флагове
# ---------------------------------------------------------------------------


def _sound_site():
    return [
        _task("D1", "design", 1, 10),
        _task("M1", "mobilization", 11, 15, _fs("D1")),
        _task("K1", "construction", 16, 100, _fs("M1")),
        _task("S1", "supervision", 16, 100, _fs("M1")),
        _task("A1", "acceptance", 101, 110, _fs("K1")),
    ]


def test_a_single_terminal_is_counted():
    flags = structural_flags(_sound_site())

    # Надзорът и приемането нямат наследник — два края, което трябва да се вижда.
    assert flags["terminal_count"] >= 1


def test_supervision_covering_construction_passes():
    assert structural_flags(_sound_site())["supervision_span_ok"]


def test_supervision_ending_early_fails():
    """Точно дефектът от P0.3, но измерен като флаг."""
    tasks = _sound_site()
    next(t for t in tasks if t["id"] == "S1")["end_day"] = 50

    assert not structural_flags(tasks)["supervision_span_ok"]


def test_a_summary_shorter_than_its_children_fails_rollup():
    tasks = _sound_site() + [
        _task("SUM", "construction", 20, 30, is_summary=True),
        _task("KID", "construction", 16, 100, parent_id="SUM"),
    ]

    assert not structural_flags(tasks)["summary_rollup_ok"]


def test_a_dangling_leaf_is_detected():
    tasks = _sound_site() + [_task("ORPHAN", "construction", 16, 20)]

    flags = structural_flags(tasks)

    assert flags["terminal_count"] >= 2


def test_citations_are_measured_against_the_real_index():
    from src.provenance import build_quantity_index

    fixture = Path(__file__).parent / "fixtures" / "kss_anonymized"
    boq = [r for r in build_quantity_index(fixture) if r.quantity is not None]
    tasks = _sound_site()
    tasks[2]["source_ref"] = boq[0].ref
    tasks[3]["source_ref"] = "КСС.xlsx!Няма такъв!999"

    flags = structural_flags(tasks, boq_index=boq)

    assert flags["source_ref_resolvable_pct"] == 50.0


def test_the_hard_flags_are_named_explicitly():
    """„Чист" вече значи нещо проверимо, а не просто exportable."""
    assert "supervision_span_ok" in HARD_STRUCTURAL_FLAGS
    assert "all_leaves_reach_terminal" in HARD_STRUCTURAL_FLAGS
    assert len(HARD_STRUCTURAL_FLAGS) >= 6


def test_a_complete_contract_scope_passes():
    assert structural_flags(_sound_site())["contract_scope_complete"]


def test_a_missing_contract_phase_fails():
    """Гейт, който не може да падне, не е гейт."""
    without_supervision = [t for t in _sound_site() if t["wbs_root"] != "supervision"]

    assert not structural_flags(without_supervision)["contract_scope_complete"]


def test_design_is_not_required():
    """Търг само за строителство няма проектиране — това не е дефект."""
    without_design = [t for t in _sound_site() if t["wbs_root"] != "design"]

    assert structural_flags(without_design)["contract_scope_complete"]


# ---------------------------------------------------------------------------
# Договорът за „чист", потвърден от одитора на 10.08.2026
# ---------------------------------------------------------------------------
#
# „exportable=yes с ясно предупреждение; clean=no."  Тоест противоречие във
# ВХОДНИЯ документ не спира предаването, но спира твърдението за чистота.

def _clean_flags(**overrides):
    from src.schedule_diagnostics import HARD_STRUCTURAL_FLAGS
    flags = {name: True for name in HARD_STRUCTURAL_FLAGS}
    flags.update(overrides)
    return flags


def test_all_hard_criteria_true_is_clean():
    from src.schedule_diagnostics import is_clean

    assert is_clean(_clean_flags())


def test_broken_conservation_is_not_clean():
    from src.schedule_diagnostics import is_clean

    assert not is_clean(_clean_flags(quantity_conservation_ok=False))


def test_a_partly_resolvable_citation_is_not_clean():
    """Одиторът иска 100%, не „почти"."""
    from src.schedule_diagnostics import is_clean

    assert not is_clean(_clean_flags(source_ref_fully_resolvable=False))


def test_a_fatal_parse_error_is_not_clean():
    from src.schedule_diagnostics import is_clean

    assert not is_clean(_clean_flags(no_fatal_parse_errors=False))


def test_an_unresolved_diameter_conflict_is_not_clean():
    from src.schedule_diagnostics import is_clean

    assert not is_clean(_clean_flags(no_unresolved_diameter_conflict=False))


def test_the_conflict_is_counted_from_the_reported_errors():
    tasks = _sound_site()

    flags = structural_flags(tasks, parse_errors=[
        "DIAMETER_CONFLICT КСС.xlsx!2!12: описанието казва DN200, колоната DN225",
        "нещо друго",
    ])

    assert flags["diameter_conflicts"] == 1
    assert not flags["no_unresolved_diameter_conflict"]


def test_recovered_parse_errors_are_soft():
    """Възстановена грешка не бива да вали прогона — само фаталната."""
    tasks = _sound_site()

    flags = structural_flags(tasks, parse_errors=["класът за X идва от модела"])

    assert flags["parse_recovered"] == 1
    assert flags["no_fatal_parse_errors"]


def test_a_dropped_row_is_fatal():
    tasks = _sound_site()

    flags = structural_flags(
        tasks, parse_errors=["пакет W1: цитат X не е ред от КСС — пропуснат"])

    assert flags["parse_fatal"] == 1
    assert not flags["no_fatal_parse_errors"]


def test_contract_phases_do_not_decide_template_completeness():
    """Одит 10.08.2026: липсата на „Проектиране" в строителен търг не е дефект.

    Проверяват се само веригите на ФИЗИЧЕСКА работа; договорните фази се мерят
    от `contract_scope_complete`.
    """
    class _Pkg:
        def __init__(self, pkg_id, chain):
            self.id, self.chain = pkg_id, chain

    chains = {"chains": {
        "sewer_section": {"wbs_root": "construction",
                          "steps": [{"key": "dig"}, {"key": "lay"}]},
        "design": {"wbs_root": "design", "steps": [{"key": "kickoff"}]},
    }}
    tasks = [
        _task("K1_dig", "construction", 1, 5, parent_id="K1", chain_step="dig"),
        _task("K1_lay", "construction", 6, 9, parent_id="K1", chain_step="lay"),
    ]

    flags = structural_flags(
        tasks, packages=[_Pkg("K1", "sewer_section"), _Pkg("D1", "design")],
        chains=chains)

    assert flags["template_complete"], "договорната фаза не бива да вали флага"


# ---------------------------------------------------------------------------
# Ресурсният капацитет — гейтът, който не се смяташе
# ---------------------------------------------------------------------------
#
# ОДИТ 10.08.2026: `resource_capacity_ok` стоеше сред твърдите критерии, но
# никъде не се изчисляваше.  Връщаше None, тоест НИТО ЕДИН прогон не можеше да
# бъде обявен за чист.  Огледалото на `contract_scope_complete`, който пък не
# можеше да падне.
#
# FAILURE означава: пак имаме гейт, чийто резултат не зависи от проверяваното.

def test_a_free_resource_passes():
    tasks = [_task("A", "construction", 1, 5, resources=["Валяк"])]

    assert structural_flags(tasks)["resource_capacity_ok"]


def test_an_overloaded_resource_fails():
    """„Валяк" е с наличност 1 — две едновременни задачи не се побират."""
    tasks = [_task("A", "construction", 1, 5, resources=["Валяк"]),
             _task("B", "construction", 3, 8, resources=["Валяк"])]

    flags = structural_flags(tasks)

    assert not flags["resource_capacity_ok"]
    assert flags["resource_overloads"][0]["resource"] == "Валяк"


def test_tasks_that_do_not_overlap_are_fine():
    tasks = [_task("A", "construction", 1, 5, resources=["Валяк"]),
             _task("B", "construction", 6, 9, resources=["Валяк"])]

    assert structural_flags(tasks)["resource_capacity_ok"]


def test_capacity_above_one_allows_overlap():
    """„Проектант" е с наличност 4 — четири паралелни задачи минават."""
    tasks = [_task(f"D{i}", "design", 1, 10, resources=["Проектант"])
             for i in range(4)]

    assert structural_flags(tasks)["resource_capacity_ok"]


def test_the_fifth_concurrent_task_breaks_it():
    tasks = [_task(f"D{i}", "design", 1, 10, resources=["Проектант"])
             for i in range(5)]

    assert not structural_flags(tasks)["resource_capacity_ok"]


def test_milestones_consume_nothing():
    """Точка без продължителност не заема бригада."""
    tasks = [_task("A", "construction", 1, 5, resources=["Валяк"]),
             _task("M", "construction", 3, 3, resources=["Валяк"], milestone=True)]

    assert structural_flags(tasks)["resource_capacity_ok"]
