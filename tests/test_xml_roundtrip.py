"""Round-trip тестове: графикът оцелява изнасяне и прочитане обратно.

Одит 2026-07-23, точка 9: валиден XML не доказва, че графикът е коректен.
Единственият начин да се знае е round-trip.

Пълният round-trip минава през MS Project (изнеси → отвори → запази пак →
сравни) и иска машина с инсталиран Project — виж tools/msproject_roundtrip.py.
Тези тестове правят СОБСТВЕНИЯ round-trip, който не изисква нищо и лови
същия клас дефекти: загуба на данни, разместени типове връзки, изгубена
идентичност.

ТОЗИ ФАЙЛ ВЕЧЕ НАМЕРИ ЕДИН ДЕФЕКТ: `_DEPENDENCY_TYPE_MAP` разменяше SS и SF.
Всяка SS връзка (урок #15: изкоп SS+1d полагане) влизаше в MS Project като
Start-to-Finish.  Хванато чак когато XML-ът беше прочетен обратно.

FAILURE означава: експортът губи или изкривява данни по пътя към възложителя.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.export_xml import export_to_mspdi_xml  # noqa: E402
from src.import_xml import compare_schedules, parse_mspdi  # noqa: E402


def _roundtrip(schedule: list[dict], **kw) -> tuple[list[dict], dict]:
    xml = export_to_mspdi_xml(schedule, "Тест", kw.pop("start", "2026-08-03"), **kw)
    parsed = parse_mspdi(xml)
    return parsed["tasks"], compare_schedules(schedule, parsed["tasks"])


def _task(tid: str, start: int, duration: int, deps=None, **kw) -> dict:
    task = {
        "id": tid, "name": f"Задача {tid}", "start_day": start,
        "duration": duration, "end_day": start + duration - 1,
        "dependencies": deps or [],
    }
    task.update(kw)
    return task


# ===================================================================
# Идентичност
# ===================================================================

def test_task_ids_survive():
    """Одит: вътрешното ID изобщо не влизаше в XML-а."""
    tasks, cmp = _roundtrip([_task("В01", 1, 10), _task("К02", 11, 5)])
    assert {t["id"] for t in tasks} == {"В01", "К02"}
    assert cmp["missing"] == []
    assert cmp["added"] == []


def test_cyrillic_ids_survive():
    tasks, _ = _roundtrip([_task("МС01", 1, 5)])
    assert tasks[0]["id"] == "МС01"


def test_duplicate_names_stay_distinguishable():
    """Без ID в XML-а две еднакви имена бяха неразличими."""
    schedule = [
        {"id": "A", "name": "Полагане", "start_day": 1, "duration": 5,
         "end_day": 5, "dependencies": []},
        {"id": "B", "name": "Полагане", "start_day": 6, "duration": 5,
         "end_day": 10, "dependencies": ["A"]},
    ]
    tasks, cmp = _roundtrip(schedule)
    assert {t["id"] for t in tasks} == {"A", "B"}
    assert cmp["identical"] is True


# ===================================================================
# Типове зависимости — тук беше дефектът
# ===================================================================

@pytest.mark.parametrize("link_type", ["FS", "SS", "FF", "SF"])
def test_dependency_type_survives(link_type):
    schedule = [
        _task("A", 1, 10),
        _task("B", 11, 10, [{"predecessor_id": "A", "type": link_type, "lag_days": 0}]),
    ]
    tasks, _ = _roundtrip(schedule)
    assert tasks[1]["dependencies"][0]["type"] == link_type


def test_ss_is_not_turned_into_sf():
    """Регресия за намерения дефект — най-често срещаната връзка след FS."""
    schedule = [
        _task("A", 1, 20),
        _task("B", 2, 19, [{"predecessor_id": "A", "type": "SS", "lag_days": 1}]),
    ]
    tasks, _ = _roundtrip(schedule)
    assert tasks[1]["dependencies"][0]["type"] == "SS"


def test_lag_survives():
    schedule = [
        _task("A", 1, 10),
        _task("B", 41, 10, [{"predecessor_id": "A", "type": "FS", "lag_days": 30}]),
    ]
    tasks, _ = _roundtrip(schedule)
    assert tasks[1]["dependencies"][0]["lag_days"] == 30


def test_string_dependency_becomes_fs():
    tasks, _ = _roundtrip([_task("A", 1, 10), _task("B", 11, 5, ["A"])])
    dep = tasks[1]["dependencies"][0]
    assert dep["predecessor_id"] == "A"
    assert dep["type"] == "FS"


def test_multiple_predecessors_survive():
    schedule = [_task("A", 1, 10), _task("B", 1, 10), _task("C", 11, 5, ["A", "B"])]
    tasks, _ = _roundtrip(schedule)
    preds = {d["predecessor_id"] for d in tasks[2]["dependencies"]}
    assert preds == {"A", "B"}


def test_dependencies_survive_reversed_task_order():
    """Two-pass експортът трябва да оцелее и в round-trip."""
    schedule = [_task("B", 11, 5, ["A"]), _task("A", 1, 10)]
    tasks, _ = _roundtrip(schedule)
    by_id = {t["id"]: t for t in tasks}
    assert by_id["B"]["dependencies"][0]["predecessor_id"] == "A"


# ===================================================================
# Данни на задачата
# ===================================================================

def test_durations_survive():
    tasks, _ = _roundtrip([_task("A", 1, 48)])
    assert tasks[0]["duration"] == 48


def test_milestone_survives_as_zero():
    tasks, _ = _roundtrip([_task("M", 5, 0)])
    assert tasks[0]["duration"] == 0
    assert tasks[0]["milestone"] is True


def test_team_survives():
    tasks, _ = _roundtrip([_task("A", 1, 5, team="ЕВ1")])
    assert tasks[0]["team"] == "ЕВ1"


def test_unit_survives():
    """Text2 (Мярка) беше дефинирано, но никога не се пишеше."""
    tasks, _ = _roundtrip([_task("A", 1, 5, unit="м3")])
    assert tasks[0]["unit"] == "м3"


def test_diameter_and_length_survive():
    tasks, _ = _roundtrip([_task("A", 1, 5, diameter=500, length_m=720)])
    assert tasks[0]["diameter"] == "500"
    assert tasks[0]["length_m"] == "720"


def test_dates_survive_seven_day_calendar():
    tasks, cmp = _roundtrip([_task("A", 1, 10), _task("B", 11, 5, ["A"])])
    assert cmp["identical"] is True
    assert tasks[0]["start_day"] == 1
    assert tasks[1]["start_day"] == 11


# ===================================================================
# Пълен график
# ===================================================================

REALISTIC = [
    {"id": "П01", "name": "Подготовка на площадка", "start_day": 1, "duration": 10,
     "end_day": 10, "dependencies": [], "team": "ЕВ1"},
    {"id": "И01", "name": "Изкоп ул. Витоша", "start_day": 11, "duration": 9,
     "end_day": 19, "dependencies": ["П01"], "team": "ЕВ1", "unit": "м3"},
    {"id": "В01", "name": "Полагане DN500 PE", "start_day": 12, "duration": 48,
     "end_day": 59, "dependencies": [{"predecessor_id": "И01", "type": "SS",
                                      "lag_days": 1}],
     "diameter": 500, "length_m": 720, "team": "ЕВ1", "unit": "м"},
    {"id": "Н01", "name": "Асфалтиране", "start_day": 90, "duration": 8,
     "end_day": 97, "dependencies": [{"predecessor_id": "В01", "type": "FS",
                                      "lag_days": 30}],
     "team": "Настилки"},
    {"id": "M01", "name": "ФИНАЛ: Приемане", "start_day": 98, "duration": 0,
     "end_day": 98, "dependencies": ["Н01"]},
]


def test_realistic_schedule_survives_intact():
    _, cmp = _roundtrip(REALISTIC)
    assert cmp["identical"] is True, cmp["differences"]


def test_realistic_schedule_has_no_parse_warnings():
    xml = export_to_mspdi_xml(REALISTIC, "Тест", "2026-08-03")
    assert parse_mspdi(xml)["warnings"] == []


def test_flexible_mode_also_survives():
    _, cmp = _roundtrip(REALISTIC, constraint_mode="flexible")
    assert cmp["identical"] is True, cmp["differences"]


# ===================================================================
# Самото сравнение работи
# ===================================================================

def test_comparison_detects_a_changed_duration():
    before = [_task("A", 1, 10)]
    after = [_task("A", 1, 99)]
    assert compare_schedules(before, after)["identical"] is False


def test_comparison_detects_a_missing_task():
    result = compare_schedules([_task("A", 1, 5), _task("B", 6, 5)], [_task("A", 1, 5)])
    assert result["missing"] == ["B"]


def test_comparison_detects_a_changed_dependency_type():
    before = [_task("B", 1, 5, [{"predecessor_id": "A", "type": "SS"}])]
    after = [_task("B", 1, 5, [{"predecessor_id": "A", "type": "FS"}])]
    assert compare_schedules(before, after)["identical"] is False


def test_comparison_of_identical_schedules_is_clean():
    assert compare_schedules(REALISTIC, REALISTIC)["identical"] is True
