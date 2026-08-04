"""Unit tests: пространствен модел по пикетаж (ниво 5).

Одит 2026-07-23, точка 3: графикът нямаше пространствена страна изобщо —
нула срещания на chainage/пикетаж.  Не можеше да се докаже, че монтажната
бригада не настъпва изкопната, нито че откритият изкоп не надвишава
допустимата дължина.  Това е разликата между Gantt за линеен обект и линеен
график.

Моделът е ДОБАВЪЧЕН: задачи без пикетаж не участват в проверките.  Иначе
всеки съществуващ график щеше да стане невалиден за една нощ.

FAILURE означава: два екипа могат да бъдат изпратени на един и същи метър в
един и същи ден, а графикът да изглежда напълно коректен.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.schedule_builder import ScheduleBuilder  # noqa: E402
from src.spatial import (  # noqa: E402
    extent_of,
    extents,
    find_crew_collisions,
    find_open_trench_violations,
    spatial_report,
)


def _seg(tid: str, frm: float, to: float, start: int, end: int, **kw) -> dict:
    task = {
        "id": tid, "name": kw.pop("name", f"Задача {tid}"),
        "alignment_id": kw.pop("alignment_id", "ул. Витоша"),
        "start_chainage": frm, "end_chainage": to,
        "start_day": start, "end_day": end,
        "duration": end - start + 1, "dependencies": [],
    }
    task.update(kw)
    return task


# ===================================================================
# Разчитане на пикетаж
# ===================================================================

def test_numeric_chainage_is_read():
    e = extent_of(_seg("A", 0, 300, 1, 10))
    assert (e.start_chainage, e.end_chainage) == (0.0, 300.0)
    assert e.length_m == 300


def test_kilometre_notation_is_read():
    """„0+300" е стандартният запис на пикетаж."""
    e = extent_of(_seg("A", "0+300", "1+150", 1, 10))
    assert e.start_chainage == 300
    assert e.end_chainage == 1150


def test_string_numbers_are_read():
    e = extent_of(_seg("A", "0", "250", 1, 10))
    assert e.end_chainage == 250


def test_task_without_chainage_is_skipped():
    """Съществуващите графици нямат пикетаж — не бива да стават невалидни."""
    assert extent_of({"id": "A", "name": "Задача", "start_day": 1, "end_day": 5}) is None


def test_task_without_alignment_is_skipped():
    task = _seg("A", 0, 300, 1, 10)
    del task["alignment_id"]
    assert extent_of(task) is None


def test_end_day_is_derived_when_missing():
    task = _seg("A", 0, 300, 1, 10)
    del task["end_day"]
    task["duration"] = 10
    assert extent_of(task).end_day == 10


def test_extents_filters_mixed_schedule():
    schedule = [_seg("A", 0, 300, 1, 10), {"id": "B", "name": "Мобилизация"}]
    assert [e.task_id for e in extents(schedule)] == ["A"]


# ===================================================================
# Сблъсък на бригади — това мрежовият график не може
# ===================================================================

def test_two_crews_on_the_same_metres_at_the_same_time_collide():
    """Точният случай: монтажът настъпва изкопа."""
    schedule = [
        _seg("И01", 0, 300, 1, 10, name="Изкоп", crew_id="ЕВ1"),
        _seg("В01", 0, 300, 5, 15, name="Полагане", crew_id="ЕВ2"),
    ]
    collisions = find_crew_collisions(schedule)
    assert len(collisions) == 1
    assert collisions[0]["overlap_m"] == 300


def test_same_place_different_time_is_fine():
    schedule = [
        _seg("И01", 0, 300, 1, 10, crew_id="ЕВ1"),
        _seg("В01", 0, 300, 11, 20, crew_id="ЕВ2"),
    ]
    assert find_crew_collisions(schedule) == []


def test_same_time_different_place_is_fine():
    schedule = [
        _seg("И01", 0, 300, 1, 10, crew_id="ЕВ1"),
        _seg("И02", 400, 700, 1, 10, crew_id="ЕВ2"),
    ]
    assert find_crew_collisions(schedule) == []


def test_different_alignments_never_collide():
    schedule = [
        _seg("A", 0, 300, 1, 10, alignment_id="ул. Витоша"),
        _seg("B", 0, 300, 1, 10, alignment_id="ул. Четвърта"),
    ]
    assert find_crew_collisions(schedule) == []


def test_buffer_catches_crews_that_are_too_close():
    """Полагането не бива да е в петите на изкопа — иска изоставане."""
    schedule = [
        _seg("И01", 0, 100, 1, 10, crew_id="ЕВ1"),
        _seg("В01", 110, 200, 1, 10, crew_id="ЕВ2"),
    ]
    assert find_crew_collisions(schedule, buffer_m=0) == []
    assert find_crew_collisions(schedule, buffer_m=20)


def test_partial_overlap_reports_the_metres():
    schedule = [
        _seg("A", 0, 300, 1, 10),
        _seg("B", 200, 500, 5, 15),
    ]
    collision = find_crew_collisions(schedule, buffer_m=0)[0]
    assert collision["overlap_m"] == 100


def test_collision_names_both_tasks():
    schedule = [
        _seg("И01", 0, 300, 1, 10, name="Изкоп"),
        _seg("В01", 0, 300, 5, 15, name="Полагане"),
    ]
    collision = find_crew_collisions(schedule)[0]
    assert collision["name_a"] == "Изкоп"
    assert collision["name_b"] == "Полагане"


# ===================================================================
# Дължина на отворен изкоп — сума по МЯСТО за даден ДЕН
# ===================================================================

def test_open_trench_within_limit_is_fine():
    schedule = [_seg("И01", 0, 200, 1, 10, name="Изкоп траншея")]
    assert find_open_trench_violations(schedule, max_open_m=300) == []


def test_open_trench_over_limit_is_reported():
    schedule = [_seg("И01", 0, 500, 1, 10, name="Изкоп траншея")]
    violations = find_open_trench_violations(schedule, max_open_m=300)
    assert len(violations) == 1
    assert violations[0]["open_m"] == 500


def test_simultaneous_excavations_sum_up():
    """Две по 200м едновременно са 400м открит изкоп, не 200."""
    schedule = [
        _seg("И01", 0, 200, 1, 10, name="Изкоп участък 1"),
        _seg("И02", 300, 500, 5, 15, name="Изкоп участък 2"),
    ]
    violations = find_open_trench_violations(schedule, max_open_m=300)
    assert violations and violations[0]["open_m"] == 400


def test_sequential_excavations_do_not_sum():
    schedule = [
        _seg("И01", 0, 200, 1, 10, name="Изкоп 1"),
        _seg("И02", 300, 500, 11, 20, name="Изкоп 2"),
    ]
    assert find_open_trench_violations(schedule, max_open_m=300) == []


def test_non_excavation_tasks_are_ignored():
    schedule = [_seg("В01", 0, 900, 1, 10, name="Полагане DN500")]
    assert find_open_trench_violations(schedule, max_open_m=300) == []


def test_explicit_open_trench_flag_is_honoured():
    schedule = [_seg("X", 0, 500, 1, 10, name="Дейност", is_open_trench=True)]
    assert find_open_trench_violations(schedule, max_open_m=300)


# ===================================================================
# Интеграция във валидатора
# ===================================================================

def test_collision_becomes_a_validation_error():
    schedule = [
        _seg("И01", 0, 300, 1, 10, name="Изкоп"),
        _seg("В01", 0, 300, 5, 15, name="Полагане"),
    ]
    result = ScheduleBuilder().validate_schedule(schedule)
    assert result["valid"] is False
    assert any("Пространствен конфликт" in e for e in result["errors"])


def test_open_trench_becomes_a_warning_not_an_error():
    """Лимитът е проектен — предупреждава, но не блокира."""
    schedule = [_seg("И01", 0, 900, 1, 10, name="Изкоп траншея")]
    result = ScheduleBuilder().validate_schedule(schedule)
    assert any("Открит изкоп" in w for w in result["warnings"])


def test_schedule_without_chainage_is_unaffected():
    """Регресия: старите графици не бива да станат невалидни."""
    schedule = [
        {"id": "A", "name": "Изкоп", "start_day": 1, "end_day": 10,
         "duration": 10, "dependencies": []},
        {"id": "B", "name": "Полагане", "start_day": 11, "end_day": 20,
         "duration": 10, "dependencies": ["A"]},
    ]
    result = ScheduleBuilder().validate_schedule(schedule)
    assert result["valid"] is True
    assert result["spatial"]["covered"] == 0


def test_validation_reports_spatial_coverage():
    schedule = [_seg("A", 0, 300, 1, 10), {"id": "B", "name": "Мобилизация",
                                           "start_day": 1, "end_day": 2,
                                           "duration": 2, "dependencies": []}]
    result = ScheduleBuilder().validate_schedule(schedule)
    assert result["spatial"]["covered"] == 1
    assert result["spatial"]["total"] == 2


def test_report_lists_alignments():
    schedule = [
        _seg("A", 0, 300, 1, 10, alignment_id="ул. Витоша"),
        _seg("B", 0, 300, 1, 10, alignment_id="ул. Четвърта"),
    ]
    report = spatial_report(schedule)
    assert report["alignments"] == ["ул. Витоша", "ул. Четвърта"]


# ===================================================================
# Разграничение: застъпване срещу нарушен буфер
# ===================================================================

def test_real_overlap_is_marked_as_overlap():
    schedule = [_seg("A", 0, 300, 1, 10), _seg("B", 100, 400, 5, 15)]
    collision = find_crew_collisions(schedule, buffer_m=0)[0]
    assert collision["kind"] == "overlap"
    assert collision["overlap_m"] > 0


def test_touching_segments_are_marked_as_buffer():
    """Допиращи се участъци не са сблъсък — нарушен буфер са."""
    schedule = [_seg("A", 0, 300, 1, 10), _seg("B", 300, 600, 1, 10)]
    collision = find_crew_collisions(schedule, buffer_m=20)[0]
    assert collision["kind"] == "buffer"
    assert collision["overlap_m"] == 0


def test_real_overlap_is_an_error():
    schedule = [_seg("A", 0, 300, 1, 10), _seg("B", 100, 400, 5, 15)]
    result = ScheduleBuilder().validate_schedule(schedule)
    assert result["valid"] is False
    assert any("едни и същи" in e for e in result["errors"])


def test_buffer_violation_is_only_a_warning():
    """Технологично изискване, не физически сблъсък — не блокира графика."""
    schedule = [_seg("A", 0, 300, 1, 10), _seg("B", 300, 600, 1, 10)]
    result = ScheduleBuilder().validate_schedule(schedule)
    assert result["valid"] is True
    assert any("Недостатъчно изоставане" in w for w in result["warnings"])


def test_message_never_says_zero_metre_overlap():
    """Регресия: „застъпват се на 0м" беше безсмислено съобщение."""
    schedule = [_seg("A", 0, 300, 1, 10), _seg("B", 300, 600, 1, 10)]
    result = ScheduleBuilder().validate_schedule(schedule)
    body = " ".join(result["errors"] + result["warnings"])
    assert "0м" not in body or "по-малко от" in body


def test_team_overlap_warning_survives_integer_task_ids():
    """Регресия (реален DeepSeek тест, 2026-08): екип-застъпването гърмеше с
    TypeError, когато ID-тата са int (join на int като низ).  Валидацията НЕ
    бива да се чупи заради типа на ID-то."""
    schedule = [
        {"id": 1, "name": "A", "team": "ЕВ1", "start_day": 0, "duration": 10},
        {"id": 2, "name": "B", "team": "ЕВ1", "start_day": 2, "duration": 10},
        {"id": 3, "name": "C", "team": "ЕВ1", "start_day": 4, "duration": 10},
    ]
    result = ScheduleBuilder().validate_schedule(schedule)  # не бива да хвърля
    assert any("едновременно" in w for w in result["warnings"])
