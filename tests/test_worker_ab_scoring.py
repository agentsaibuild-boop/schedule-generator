"""Unit tests for the worker A/B scorer (Етап 4).

Covers: метриките за параметри, покритие на калкулатора, правилен материал,
        задължителни зависимости, фантомни фази и претегления общ резултат.

FAILURE означава: tools/worker_ab.py :: оценяването е счупено — сравнението
между worker модели ще подреди кандидатите погрешно и изборът на модел ще
се основава на грешни числа.

Скорерът е ДЕТЕРМИНИСТИЧЕН (без LLM съдия) точно за да може да се тества.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.worker_ab import WEIGHTS, composite, score_schedule  # noqa: E402

ANALYSIS = "Довеждащ водопровод, гр. Горноград. Чугунени тръби DN300, горски терен."


def _pipe(name="Полагане DN300 CI", **kw) -> dict:
    task = {
        "id": kw.pop("id", "В01"),
        "name": name,
        "dn": 300,
        "material": "CI",
        "length_m": 354,
        "dependencies": [],
        "start_day": 1,
        "end_day": 45,
        "duration": 45,
    }
    task.update(kw)
    return task


# ===================================================================
# Параметри
# ===================================================================

def test_full_parameters_score_one():
    scores = score_schedule([_pipe()], {}, ANALYSIS)
    assert scores["param_dn"] == 1.0
    assert scores["param_length"] == 1.0
    assert scores["param_material"] == 1.0


def test_missing_material_lowers_score():
    task = _pipe()
    del task["material"]
    scores = score_schedule([task], {}, ANALYSIS)
    assert scores["param_material"] == 0.0


def test_partial_material_coverage():
    tasks = [_pipe(id="В01"), _pipe(id="В02")]
    del tasks[1]["material"]
    scores = score_schedule(tasks, {}, ANALYSIS)
    assert scores["param_material"] == 0.5


def test_length_accepted_from_quantity_in_metres():
    task = _pipe()
    del task["length_m"]
    task["quantity"] = 354
    task["unit"] = "м"
    scores = score_schedule([task], {}, ANALYSIS)
    assert scores["param_length"] == 1.0


def test_non_pipe_tasks_do_not_dilute_parameters():
    """Изкоп и асфалт нямат DN — не бива да свалят оценката."""
    tasks = [_pipe(), {"id": "И01", "name": "Изкоп за траншея", "duration": 9}]
    scores = score_schedule(tasks, {}, ANALYSIS)
    assert scores["param_dn"] == 1.0


# ===================================================================
# calculator_coverage — най-важната метрика след P2
# ===================================================================

def test_calculator_coverage_full_when_params_complete():
    scores = score_schedule([_pipe()], {}, ANALYSIS)
    assert scores["calculator_coverage"] == 1.0


def test_calculator_coverage_zero_without_material():
    """Без материал калкулаторът пропуска — точно това мерим."""
    task = _pipe()
    del task["material"]
    task["name"] = "Полагане тръби DN300"     # без маркер и в името
    scores = score_schedule([task], {}, ANALYSIS)
    assert scores["calculator_coverage"] == 0.0


def test_calculator_coverage_zero_without_length():
    task = _pipe()
    del task["length_m"]
    scores = score_schedule([task], {}, ANALYSIS)
    assert scores["calculator_coverage"] == 0.0


# ===================================================================
# material_correct — урок #35
# ===================================================================

def test_correct_material_scores_one():
    scores = score_schedule([_pipe()], {"expect_material": "CI"}, ANALYSIS)
    assert scores["material_correct"] == 1.0


def test_wrong_material_scores_zero():
    """DN300 CI, обявен като PE — критичната грешка от урок #35."""
    scores = score_schedule(
        [_pipe(material="PE")], {"expect_material": "CI"}, ANALYSIS
    )
    assert scores["material_correct"] == 0.0


def test_mixed_material_scores_half():
    tasks = [_pipe(id="В01"), _pipe(id="В02", material="PE")]
    scores = score_schedule(tasks, {"expect_material": "CI"}, ANALYSIS)
    assert scores["material_correct"] == 0.5


def test_expected_dn_present():
    scores = score_schedule([_pipe()], {"expect_dn": 300}, ANALYSIS)
    assert scores["dn_present"] == 1.0


def test_expected_dn_absent():
    scores = score_schedule([_pipe(dn=110)], {"expect_dn": 300}, ANALYSIS)
    assert scores["dn_present"] == 0.0


# ===================================================================
# required_dependency — Тласкател → КПС (урок #38)
# ===================================================================

def _kps_tasks(explicit: bool) -> list[dict]:
    return [
        {"id": "Т01", "name": "Тласкател DN160", "dependencies": [],
         "start_day": 1, "end_day": 100, "duration": 100},
        {"id": "К01", "name": "КПС — изграждане",
         "dependencies": ["Т01"] if explicit else [],
         "start_day": 101, "end_day": 160, "duration": 60},
    ]


def test_explicit_dependency_scores_one():
    scores = score_schedule(
        _kps_tasks(True), {"require_dependency": ("тласкател", "кпс")}, ANALYSIS
    )
    assert scores["required_dependency"] == 1.0


def test_implicit_ordering_scores_half():
    """Правилен ред без явна зависимост е половин точка — MS Project ще ги разлепи."""
    scores = score_schedule(
        _kps_tasks(False), {"require_dependency": ("тласкател", "кпс")}, ANALYSIS
    )
    assert scores["required_dependency"] == 0.5


def test_kps_before_tlaskatel_scores_zero():
    tasks = _kps_tasks(False)
    tasks[1]["start_day"] = 1
    tasks[1]["end_day"] = 60
    scores = score_schedule(
        tasks, {"require_dependency": ("тласкател", "кпс")}, ANALYSIS
    )
    assert scores["required_dependency"] == 0.0


# ===================================================================
# Фантомни фази — урок #41
# ===================================================================

def test_clean_schedule_has_no_phantoms():
    scores = score_schedule(
        [_pipe()], {"forbid_phases": ["административна подготовка"]}, ANALYSIS
    )
    assert scores["no_phantom_phases"] == 1.0


def test_phantom_phase_detected():
    tasks = [_pipe(), {"id": "А01", "name": "Административна подготовка", "duration": 12}]
    scores = score_schedule(
        tasks, {"forbid_phases": ["административна подготовка"]}, ANALYSIS
    )
    assert scores["no_phantom_phases"] == 0.0
    assert scores["_phantom_hits"]


# ===================================================================
# composite
# ===================================================================

def test_composite_is_percentage():
    perfect = {k: 1.0 for k in WEIGHTS}
    assert composite(perfect) == pytest.approx(100.0)


def test_composite_zero_for_all_failures():
    assert composite({k: 0.0 for k in WEIGHTS}) == 0.0


def test_composite_ignores_unknown_keys():
    assert composite({"param_dn": 1.0, "_debug": 999}) == pytest.approx(100.0)


def test_composite_weights_calculator_coverage_highest():
    """Покритието на калкулатора трябва да тежи най-много — то е целта на P2."""
    assert WEIGHTS["calculator_coverage"] == max(WEIGHTS.values())


def test_composite_of_empty_scores_is_zero():
    assert composite({}) == 0.0


def test_missing_optional_metrics_do_not_penalise():
    """Сценарий без expect_material не бива да губи точки заради липсата ѝ."""
    partial = {"calculator_coverage": 1.0, "param_material": 1.0, "param_dn": 1.0,
               "param_length": 1.0, "no_phantom_phases": 1.0,
               "no_hallucinated_places": 1.0, "decomposition": 1.0}
    assert composite(partial) == pytest.approx(100.0)


# ===================================================================
# Устойчивост на скорера към нестандартен вход от модел
# ===================================================================

def test_scorer_survives_dict_task_id():
    """Модел върна dict за 'id' — скорерът не бива да гърми (наблюдавано)."""
    tasks = [
        {"id": {"nested": "T1"}, "name": "Тласкател DN160", "dependencies": [],
         "start_day": 1, "end_day": 100},
        {"id": "К01", "name": "КПС", "dependencies": [], "start_day": 101,
         "end_day": 160},
    ]
    scores = score_schedule(tasks, {"require_dependency": ("тласкател", "кпс")}, ANALYSIS)
    assert "required_dependency" in scores


def test_scorer_survives_dict_dependency():
    tasks = [
        {"id": "Т01", "name": "Тласкател", "dependencies": [], "start_day": 1,
         "end_day": 100},
        {"id": "К01", "name": "КПС", "dependencies": [{"id": "Т01"}],
         "start_day": 101, "end_day": 160},
    ]
    scores = score_schedule(tasks, {"require_dependency": ("тласкател", "кпс")}, ANALYSIS)
    assert scores["required_dependency"] >= 0.0


def test_scorer_survives_empty_task_list():
    scores = score_schedule([], {"expect_material": "CI"}, ANALYSIS)
    assert composite(scores) >= 0.0
