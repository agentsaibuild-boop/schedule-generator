"""Unit tests for ScheduleBuilder.recompute_durations / reschedule.

Covers: детерминистична замяна на LLM продължителности, запазване на
        стойността при непълни данни, презакачване на датите със запазен
        lag (вкл. отрицателен), кръгови зависимости, поддейности, и
        отчета (changes/skipped/summary).

FAILURE означава: src/schedule_builder.py :: recompute_durations/reschedule
е счупен — графикът ще ползва продължителностите, които LLM-ът си е измислил
в промпта (P2 от REVISION_2026-07.md), или датите ще се разминат с
продължителностите след преизчисление.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.schedule_builder import ScheduleBuilder


def _builder() -> ScheduleBuilder:
    return ScheduleBuilder()


def _pipe(tid: str, name: str, length: int, dn: int, **extra) -> dict:
    task = {
        "id": tid,
        "name": name,
        "length_m": length,
        "diameter": dn,
        "duration": 1,
        "start_day": 1,
        "end_day": 1,
        "dependencies": [],
    }
    task.update(extra)
    return task


# ===================================================================
# recompute_durations — замяна
# ===================================================================

def test_recompute_replaces_llm_duration():
    """720м DN500 PE → 48 дни, независимо какво е казал LLM-ът."""
    schedule = [_pipe("В01", "Полагане DN500 PE", 720, 500, duration=10, end_day=10)]
    result = _builder().recompute_durations(schedule)

    assert result["schedule"][0]["duration"] == 48
    assert len(result["changes"]) == 1
    assert result["changes"][0]["old"] == 10
    assert result["changes"][0]["new"] == 48
    assert result["changes"][0]["delta"] == 38


def test_recompute_does_not_mutate_input():
    schedule = [_pipe("В01", "Полагане DN500 PE", 720, 500, duration=10, end_day=10)]
    _builder().recompute_durations(schedule)
    assert schedule[0]["duration"] == 10


def test_recompute_keeps_llm_value_when_it_already_matches():
    schedule = [_pipe("В01", "Полагане DN500 PE", 720, 500, duration=48, end_day=48)]
    result = _builder().recompute_durations(schedule)

    assert result["changes"] == []
    assert result["summary"]["unchanged"] == 1


def test_recompute_skips_task_without_material_and_keeps_llm_value():
    """Урок #35: без материал не гадаем — стойността на LLM-а остава."""
    schedule = [_pipe("В01", "Полагане тръби DN300 — ул. Х", 500, 300, duration=42, end_day=42)]
    result = _builder().recompute_durations(schedule)

    assert result["schedule"][0]["duration"] == 42
    assert result["changes"] == []
    assert len(result["skipped"]) == 1
    assert "материалът не е указан" in result["skipped"][0]["reason"]


def test_recompute_skips_excavation_tasks():
    schedule = [{
        "id": "И01", "name": "Изкоп за тръбна траншея", "quantity": 720,
        "unit": "м3", "duration": 9, "start_day": 1, "end_day": 9, "dependencies": [],
    }]
    result = _builder().recompute_durations(schedule)

    assert result["schedule"][0]["duration"] == 9
    assert result["summary"]["skipped"] == 1


def test_recompute_handles_srs_count_task():
    schedule = [{
        "id": "Ш01", "name": "Монтаж СРС", "quantity": 526, "unit": "бр.",
        "duration": 20, "start_day": 1, "end_day": 20, "dependencies": [],
    }]
    result = _builder().recompute_durations(schedule)

    assert result["schedule"][0]["duration"] == 106


def test_recompute_empty_schedule():
    result = _builder().recompute_durations([])
    assert result["schedule"] == []
    assert result["summary"]["total"] == 0


def test_recompute_summary_counts():
    schedule = [
        _pipe("В01", "Полагане DN500 PE", 720, 500, duration=10, end_day=10),
        _pipe("В02", "Полагане DN110 PE", 240, 110, duration=19, end_day=19),
        {"id": "И01", "name": "Изкоп", "duration": 5, "start_day": 1,
         "end_day": 5, "dependencies": []},
    ]
    result = _builder().recompute_durations(schedule)
    summary = result["summary"]

    assert summary["total"] == 3
    assert summary["recomputed"] == 1   # В01 се променя
    assert summary["unchanged"] == 1    # В02: ceil(240/13)=19 вече съвпада
    assert summary["skipped"] == 1      # изкопът


def test_recompute_reports_old_and_new_total_duration():
    schedule = [
        _pipe("В01", "Полагане DN500 PE", 720, 500, duration=10, end_day=10),
        _pipe("В02", "Полагане DN500 PE", 300, 500,
              duration=5, start_day=11, end_day=15, dependencies=["В01"]),
    ]
    result = _builder().recompute_durations(schedule)

    assert result["summary"]["old_total_duration"] == 15
    assert result["summary"]["new_total_duration"] == 48 + 20


# ===================================================================
# recompute_durations + reschedule — дати
# ===================================================================

def test_recompute_shifts_dependent_task():
    schedule = [
        _pipe("В01", "Полагане DN500 PE", 720, 500, duration=10, end_day=10),
        _pipe("В02", "Полагане DN500 PE", 150, 500,
              duration=10, start_day=11, end_day=20, dependencies=["В01"]),
    ]
    result = _builder().recompute_durations(schedule)
    by_id = {t["id"]: t for t in result["schedule"]}

    assert by_id["В01"]["duration"] == 48
    assert by_id["В01"]["end_day"] == 48
    assert by_id["В02"]["start_day"] == 49       # веднага след предшественика
    assert by_id["В02"]["end_day"] == 49 + 10 - 1


def test_recompute_preserves_intentional_lag():
    """Урок #36: настилки SS+30 — празнината не се изтрива при преизчисление."""
    schedule = [
        _pipe("В01", "Полагане DN500 PE", 720, 500, duration=10, end_day=10),
        {"id": "Н01", "name": "Асфалтиране", "duration": 8,
         "start_day": 41, "end_day": 48, "dependencies": ["В01"]},
    ]
    result = _builder().recompute_durations(schedule)
    by_id = {t["id"]: t for t in result["schedule"]}

    # Оригиналната празнина: 41 - 10 - 1 = 30 дни. Запазва се след промяната.
    assert by_id["Н01"]["start_day"] - by_id["В01"]["end_day"] - 1 == 30
    assert by_id["Н01"]["start_day"] == 79


def test_recompute_preserves_negative_lag_overlap():
    """Урок #15: SS+1d припокриване (изкоп паралелно с полагане) се запазва."""
    schedule = [
        _pipe("В01", "Полагане DN500 PE", 720, 500, duration=10, end_day=10),
        {"id": "В02", "name": "Засипване", "duration": 10,
         "start_day": 2, "end_day": 11, "dependencies": ["В01"]},
    ]
    result = _builder().recompute_durations(schedule)
    by_id = {t["id"]: t for t in result["schedule"]}

    # Оригинална празнина: 2 - 10 - 1 = -9 (припокриване)
    assert by_id["В02"]["start_day"] - by_id["В01"]["end_day"] - 1 == -9
    assert by_id["В02"]["start_day"] == 40


def test_recompute_without_reschedule_leaves_dates_alone():
    schedule = [
        _pipe("В01", "Полагане DN500 PE", 720, 500, duration=10, end_day=10),
        _pipe("В02", "Полагане DN500 PE", 150, 500,
              duration=10, start_day=11, end_day=20, dependencies=["В01"]),
    ]
    result = _builder().recompute_durations(schedule, reschedule=False)
    by_id = {t["id"]: t for t in result["schedule"]}

    assert by_id["В01"]["duration"] == 48
    assert by_id["В01"]["end_day"] == 10      # непроменена
    assert by_id["В02"]["start_day"] == 11


def test_recompute_min_days_is_configurable():
    schedule = [_pipe("В01", "Полагане DN110 PE", 10, 110, duration=1, end_day=1)]

    default = _builder().recompute_durations(schedule)
    relaxed = _builder().recompute_durations(schedule, min_days=1)

    assert default["schedule"][0]["duration"] == 5
    assert relaxed["schedule"][0]["duration"] == 1


# ===================================================================
# reschedule — самостоятелно
# ===================================================================

def test_reschedule_linear_chain():
    schedule = [
        {"id": "A", "name": "A", "duration": 5, "start_day": 1, "end_day": 5,
         "dependencies": []},
        {"id": "B", "name": "B", "duration": 3, "start_day": 6, "end_day": 8,
         "dependencies": ["A"]},
        {"id": "C", "name": "C", "duration": 2, "start_day": 9, "end_day": 10,
         "dependencies": ["B"]},
    ]
    result = _builder().reschedule(schedule)
    by_id = {t["id"]: t for t in result["schedule"]}

    assert (by_id["A"]["start_day"], by_id["A"]["end_day"]) == (1, 5)
    assert (by_id["B"]["start_day"], by_id["B"]["end_day"]) == (6, 8)
    assert (by_id["C"]["start_day"], by_id["C"]["end_day"]) == (9, 10)
    assert result["shifted"] == []


def test_reschedule_takes_latest_predecessor():
    schedule = [
        {"id": "A", "name": "A", "duration": 5, "start_day": 1, "end_day": 5,
         "dependencies": []},
        {"id": "B", "name": "B", "duration": 20, "start_day": 1, "end_day": 20,
         "dependencies": []},
        {"id": "C", "name": "C", "duration": 2, "start_day": 21, "end_day": 22,
         "dependencies": ["A", "B"]},
    ]
    result = _builder().reschedule(schedule)
    by_id = {t["id"]: t for t in result["schedule"]}

    assert by_id["C"]["start_day"] == 21


def test_reschedule_milestone_end_equals_start():
    schedule = [
        {"id": "A", "name": "A", "duration": 5, "start_day": 1, "end_day": 5,
         "dependencies": []},
        {"id": "M", "name": "ФИНАЛ", "duration": 0, "start_day": 6, "end_day": 6,
         "dependencies": ["A"]},
    ]
    result = _builder().reschedule(schedule)
    milestone = next(t for t in result["schedule"] if t["id"] == "M")

    assert milestone["start_day"] == 6
    assert milestone["end_day"] == 6


def test_reschedule_detects_cycle_and_leaves_schedule_alone():
    schedule = [
        {"id": "A", "name": "A", "duration": 5, "start_day": 1, "end_day": 5,
         "dependencies": ["B"]},
        {"id": "B", "name": "B", "duration": 5, "start_day": 6, "end_day": 10,
         "dependencies": ["A"]},
    ]
    result = _builder().reschedule(schedule)

    assert result["warnings"]
    assert "кръгова" in result["warnings"][0].lower()
    assert result["schedule"][0]["start_day"] == 1
    assert result["shifted"] == []


def test_reschedule_ignores_unknown_dependency():
    schedule = [
        {"id": "A", "name": "A", "duration": 5, "start_day": 3, "end_day": 7,
         "dependencies": ["НЯМА"]},
    ]
    result = _builder().reschedule(schedule)

    assert result["schedule"][0]["start_day"] == 3
    assert result["warnings"] == []


def test_reschedule_never_goes_below_day_one():
    schedule = [
        {"id": "A", "name": "A", "duration": 5, "start_day": 1, "end_day": 5,
         "dependencies": []},
        {"id": "B", "name": "B", "duration": 5, "start_day": 1, "end_day": 5,
         "dependencies": ["A"]},   # празнина -5
    ]
    result = _builder().reschedule(schedule)
    by_id = {t["id"]: t for t in result["schedule"]}

    assert by_id["B"]["start_day"] >= 1


def test_reschedule_shifts_sub_activities_with_parent():
    schedule = [
        _pipe("В01", "Полагане DN500 PE", 720, 500, duration=10, end_day=10),
        {"id": "В02", "name": "Засипване", "duration": 5,
         "start_day": 11, "end_day": 15, "dependencies": ["В01"],
         "sub_activities": [{"name": "под", "start_day": 11, "end_day": 13}]},
    ]
    result = _builder().recompute_durations(schedule)
    child = next(t for t in result["schedule"] if t["id"] == "В02")

    assert child["start_day"] == 49
    assert child["sub_activities"][0]["start_day"] == 49
    assert child["sub_activities"][0]["end_day"] == 51


def test_reschedule_empty():
    result = _builder().reschedule([])
    assert result["schedule"] == []
    assert result["shifted"] == []


def test_reschedule_output_passes_validation():
    """Преизчисленият график не трябва да въвежда нови грешки."""
    builder = _builder()
    schedule = [
        _pipe("В01", "Полагане DN500 PE", 720, 500, duration=10, end_day=10),
        _pipe("В02", "Полагане DN110 PE", 400, 110,
              duration=5, start_day=11, end_day=15, dependencies=["В01"]),
    ]
    result = builder.recompute_durations(schedule)
    validation = builder.validate_schedule(result["schedule"])

    assert validation["valid"], validation["errors"]
