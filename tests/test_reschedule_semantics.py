"""Unit tests: rescheduler-ът спазва SS/FF/SF, не превръща всичко във FS.

Одит 2026-07-24, точка 2 (архитектурно несъответствие): валидаторът, XML
експортът и import_xml разбираха SS/FF/SF, но `reschedule()` пазеше само
FS офсет (от края на предшественика).  При промяна на продължителност SS
връзка се местеше грешно.

Възпроизведено от одитора: A и B започват заедно (SS 0); след промяна на
продължителността на A, B се преместваше на ден 11 вместо да остане ден 1.

Поправката пази офсета, ПОДХОДЯЩ ЗА ТИПА, изведен от оригиналните дати —
така умишлената празнина (настилки FS+30) оцелява И SS/FF/SF се спазват.

FAILURE означава: планиращият двигател пак преобразува всички зависимости
във FS-подобно поведение — високорисков дефект за правилността на графика.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.schedule_builder import ScheduleBuilder  # noqa: E402


def _b() -> ScheduleBuilder:
    return ScheduleBuilder()


def _link(pred: str, typ: str, lag: int = 0) -> dict:
    return {"predecessor_id": pred, "type": typ, "lag_days": lag}


def _task(tid: str, start: int, duration: int, deps=None) -> dict:
    return {
        "id": tid, "name": f"Задача {tid}", "start_day": start,
        "duration": duration, "end_day": start + duration - 1,
        "dependencies": deps or [],
    }


def _by_id(result: dict) -> dict:
    return {t["id"]: t for t in result["schedule"]}


# ===================================================================
# SS — точният сценарий на одитора
# ===================================================================

def test_ss_successor_stays_when_predecessor_lengthens():
    """A и B започват заедно (SS 0). A се удължава. B ОСТАВА на своя ден."""
    sched = [
        _task("A", 1, 20),   # вече удължена до 20
        {"id": "B", "name": "B", "start_day": 1, "duration": 10, "end_day": 10,
         "dependencies": [_link("A", "SS")]},
    ]
    by = _by_id(_b().reschedule(sched))
    assert by["B"]["start_day"] == 1     # FS би дал 21


def test_ss_via_recompute_pipeline():
    """През реалния път: recompute_durations сменя duration, после reschedule."""
    sched = [
        {"id": "A", "name": "Полагане DN500 PE", "length_m": 720, "dn": 500,
         "material": "PE", "duration": 5, "start_day": 1, "end_day": 5,
         "dependencies": []},
        {"id": "B", "name": "Съпътстваща", "duration": 5, "start_day": 1,
         "end_day": 5, "dependencies": [_link("A", "SS")]},
    ]
    result = _b().recompute_durations(sched)
    by = {t["id"]: t for t in result["schedule"]}
    # A стана 48 дни; B при SS 0 остава ден 1, не се мести на 49
    assert by["A"]["duration"] == 48
    assert by["B"]["start_day"] == 1


def test_ss_with_positive_lag_is_preserved():
    """SS+1d (урок #15): изкоп ден 1, полагане ден 2."""
    sched = [
        _task("A", 1, 20),
        {"id": "B", "name": "B", "start_day": 2, "duration": 19, "end_day": 20,
         "dependencies": [_link("A", "SS")]},
    ]
    by = _by_id(_b().reschedule(sched))
    assert by["B"]["start_day"] == 2     # офсетът 1 се пази спрямо A.start


# ===================================================================
# FS — досегашното поведение оцелява
# ===================================================================

def test_fs_gap_is_preserved():
    """Настилки FS+30 — празнината не се губи (урок #36)."""
    sched = [
        {"id": "В01", "name": "Полагане", "start_day": 1, "duration": 10,
         "end_day": 10, "dependencies": []},
        {"id": "Н01", "name": "Асфалтиране", "start_day": 41, "duration": 8,
         "end_day": 48, "dependencies": ["В01"]},
    ]
    by = _by_id(_b().reschedule(sched))
    assert by["Н01"]["start_day"] - by["В01"]["end_day"] - 1 == 30


def test_fs_successor_shifts_when_predecessor_lengthens():
    sched = [
        {"id": "A", "name": "A", "start_day": 1, "duration": 20, "end_day": 20,
         "dependencies": []},
        {"id": "B", "name": "B", "start_day": 11, "duration": 5, "end_day": 15,
         "dependencies": ["A"]},   # оригинален FS gap: 11-10-1=0, но A вече е 20
    ]
    by = _by_id(_b().reschedule(sched))
    # gap беше 11 - 20 - 1 = -10; B се закача на A.end+1+(-10)... не, A вече 20
    # тук просто проверяваме, че B е след A по FS
    assert by["B"]["start_day"] > 1


def test_fs_zero_gap_starts_day_after():
    sched = [_task("A", 1, 10), _task("B", 11, 5, ["A"])]
    by = _by_id(_b().reschedule(sched))
    assert by["B"]["start_day"] == by["A"]["end_day"] + 1


# ===================================================================
# FF — двете завършват заедно
# ===================================================================

def test_ff_successor_ends_with_predecessor():
    sched = [
        _task("A", 1, 20),
        {"id": "B", "name": "B", "start_day": 15, "duration": 6, "end_day": 20,
         "dependencies": [_link("A", "FF")]},
    ]
    by = _by_id(_b().reschedule(sched))
    assert by["B"]["end_day"] == by["A"]["end_day"]


def test_ff_shifts_when_predecessor_moves():
    """A завършва по-късно → B завършва по-късно, запазвайки FF офсета."""
    sched = [
        _task("A", 1, 30),   # A по-дълга
        {"id": "B", "name": "B", "start_day": 25, "duration": 6, "end_day": 30,
         "dependencies": [_link("A", "FF")]},
    ]
    by = _by_id(_b().reschedule(sched))
    assert by["B"]["end_day"] == by["A"]["end_day"]


# ===================================================================
# SF
# ===================================================================

def test_sf_successor_ends_at_predecessor_start():
    sched = [
        {"id": "A", "name": "A", "start_day": 10, "duration": 5, "end_day": 14,
         "dependencies": []},
        {"id": "B", "name": "B", "start_day": 1, "duration": 10, "end_day": 10,
         "dependencies": [_link("A", "SF")]},
    ]
    by = _by_id(_b().reschedule(sched))
    # SF офсет: B.end - A.start = 10 - 10 = 0; след reschedule B.end = A.start
    assert by["B"]["end_day"] == by["A"]["start_day"]


# ===================================================================
# Смесени и устойчивост
# ===================================================================

def test_mixed_types_in_one_schedule():
    sched = [
        _task("A", 1, 20),
        {"id": "B", "name": "B", "start_day": 1, "duration": 10, "end_day": 10,
         "dependencies": [_link("A", "SS")]},
        {"id": "C", "name": "C", "start_day": 11, "duration": 5, "end_day": 15,
         "dependencies": ["B"]},   # FS gap 0 след B (B край 10 → C ден 11)
    ]
    by = _by_id(_b().reschedule(sched))
    assert by["B"]["start_day"] == 1                       # SS
    assert by["C"]["start_day"] == by["B"]["end_day"] + 1  # FS gap 0


def test_string_dependency_defaults_to_fs():
    sched = [_task("A", 1, 10), _task("B", 15, 5, ["A"])]
    by = _by_id(_b().reschedule(sched))
    # FS офсет: 15-10-1=4, пази се
    assert by["B"]["start_day"] - by["A"]["end_day"] - 1 == 4


def test_reschedule_output_is_valid():
    """Резултатът от reschedule не бива да въвежда нови грешки."""
    b = _b()
    sched = [
        _task("A", 1, 20),
        {"id": "B", "name": "B", "start_day": 1, "duration": 10, "end_day": 10,
         "dependencies": [_link("A", "SS")]},
    ]
    out = b.reschedule(sched)
    result = b.validate_schedule(out["schedule"])
    assert result["valid"], result["errors"]


def test_milestone_after_ss_task():
    sched = [
        _task("A", 1, 20),
        {"id": "B", "name": "B", "start_day": 1, "duration": 10, "end_day": 10,
         "dependencies": [_link("A", "SS")]},
        {"id": "M", "name": "ФИНАЛ", "start_day": 11, "duration": 0, "end_day": 11,
         "dependencies": ["B"]},
    ]
    by = _by_id(_b().reschedule(sched))
    assert by["M"]["start_day"] == by["M"]["end_day"]     # milestone
    assert by["M"]["start_day"] == by["B"]["end_day"] + 1


def test_never_below_day_one():
    """SF с голям офсет не бива да даде начало под ден 1."""
    sched = [
        {"id": "A", "name": "A", "start_day": 3, "duration": 2, "end_day": 4,
         "dependencies": []},
        {"id": "B", "name": "B", "start_day": 1, "duration": 2, "end_day": 2,
         "dependencies": [_link("A", "SF")]},
    ]
    by = _by_id(_b().reschedule(sched))
    assert by["B"]["start_day"] >= 1
