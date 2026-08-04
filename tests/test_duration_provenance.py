"""Unit tests: произход на всяка продължителност — изчислена или предположена.

Одит 2026-07-23 установи противоречие в заявения инвариант „аритметиката е в
код, не в промпт": когато липсва задължителен параметър, кодът ЗАПАЗВАШЕ
стойността на LLM-а в същото поле `duration`.  След това нищо надолу по
веригата не можеше да различи доказано число от предположение — нито
експортът, нито човекът, нито валидацията.

Реалният инвариант беше „кодът смята, когато има данни; иначе AI решава" —
тоест смяната на модел ВСЕ ПАК можеше да промени числата.

FAILURE означава: графикът отново съдържа неразличими стойности и не може да
се докаже кое е сметнато по норма от config/productivities.json.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.chat_handler import ChatHandler  # noqa: E402
from src.duration_calculator import (  # noqa: E402
    CODE_MILESTONE,
    CODE_MISSING_DN,
    CODE_MISSING_LENGTH,
    CODE_MISSING_MATERIAL,
    CODE_NOT_PARAMETRIC,
    CODE_OK,
    UNRESOLVED_CODES,
    calculate_task_duration,
)
from src.schedule_builder import ScheduleBuilder  # noqa: E402


def _pipe(**kw) -> dict:
    task = {
        "id": "В01", "name": "Полагане DN500 PE", "length_m": 720,
        "diameter": 500, "duration": 10, "start_day": 1, "end_day": 10,
        "dependencies": [],
    }
    task.update(kw)
    return task


# ===================================================================
# Кодовете на причината
# ===================================================================

def test_computed_task_gets_ok_code():
    assert calculate_task_duration(_pipe()).code == CODE_OK


def test_milestone_has_its_own_code():
    result = calculate_task_duration({"name": "ФИНАЛ", "milestone": True})
    assert result.code == CODE_MILESTONE
    assert result.code not in UNRESOLVED_CODES


def test_missing_material_code():
    task = _pipe(name="Полагане тръби DN300 — ул. Х", diameter=300)
    assert calculate_task_duration(task).code == CODE_MISSING_MATERIAL


def test_missing_length_code():
    task = _pipe()
    del task["length_m"]
    assert calculate_task_duration(task).code == CODE_MISSING_LENGTH


def test_missing_dn_code():
    task = _pipe(name="Полагане PE тръби")
    del task["diameter"]
    assert calculate_task_duration(task).code == CODE_MISSING_DN


def test_non_parametric_code():
    task = {"name": "Изкоп за траншея", "quantity": 720, "unit": "м3"}
    assert calculate_task_duration(task).code == CODE_NOT_PARAMETRIC


def test_all_failure_codes_are_marked_unresolved():
    for code in (CODE_MISSING_DN, CODE_MISSING_LENGTH, CODE_MISSING_MATERIAL,
                 CODE_NOT_PARAMETRIC):
        assert code in UNRESOLVED_CODES


def test_success_codes_are_not_unresolved():
    assert CODE_OK not in UNRESOLVED_CODES
    assert CODE_MILESTONE not in UNRESOLVED_CODES


# ===================================================================
# Произходът се записва в задачата
# ===================================================================

def test_calculated_task_is_marked_calculated():
    result = ScheduleBuilder().recompute_durations([_pipe()])
    task = result["schedule"][0]

    assert task["duration_source"] == "calculated"
    assert task["calculated_duration"] == 48
    assert task["duration"] == 48
    assert "suggested_duration" not in task


def test_unresolved_task_is_marked_suggested():
    """Ядрото на поправката: стойността на AI-я вече е ЯВНО предположение."""
    task = _pipe(name="Полагане тръби DN300 — ул. Х", diameter=300, duration=42)
    result = ScheduleBuilder().recompute_durations([task])
    out = result["schedule"][0]

    assert out["duration_source"] == "suggested"
    assert out["duration_status"] == CODE_MISSING_MATERIAL
    assert out["suggested_duration"] == 42
    assert "calculated_duration" not in out
    # `duration` остава използваемо надолу по веригата
    assert out["duration"] == 42


def test_unchanged_task_still_gets_provenance():
    """Съвпадаща стойност не значи недоказана — маркира се като изчислена."""
    result = ScheduleBuilder().recompute_durations([_pipe(duration=48, end_day=48)])
    task = result["schedule"][0]

    assert result["summary"]["unchanged"] == 1
    assert task["duration_source"] == "calculated"
    assert task["calculated_duration"] == 48


def test_stale_suggested_field_is_cleared_when_resolved():
    """Ако задачата се допълни и стане изчислима, старото предположение пада."""
    task = _pipe(suggested_duration=99, duration_source="suggested")
    result = ScheduleBuilder().recompute_durations([task])
    out = result["schedule"][0]

    assert out["duration_source"] == "calculated"
    assert "suggested_duration" not in out


def test_stale_calculated_field_is_cleared_when_unresolved():
    task = _pipe(name="Полагане тръби DN300", diameter=300,
                 calculated_duration=99, duration_source="calculated")
    result = ScheduleBuilder().recompute_durations([task])
    out = result["schedule"][0]

    assert out["duration_source"] == "suggested"
    assert "calculated_duration" not in out


def test_milestone_is_not_counted_as_unresolved():
    tasks = [{"id": "M01", "name": "ФИНАЛ", "milestone": True, "duration": 0,
              "start_day": 1, "end_day": 1, "dependencies": []}]
    result = ScheduleBuilder().recompute_durations(tasks)
    assert result["summary"]["unresolved"] == 0


# ===================================================================
# Отчетът различава видовете
# ===================================================================

def _mixed_schedule() -> list[dict]:
    return [
        _pipe(id="В01"),                                              # изчислима
        _pipe(id="В02", name="Полагане тръби DN300", diameter=300),   # без материал
        {"id": "И01", "name": "Изкоп", "duration": 9, "start_day": 1,
         "end_day": 9, "dependencies": []},                           # без норма
    ]


def test_summary_counts_unresolved_separately_from_skipped():
    result = ScheduleBuilder().recompute_durations(_mixed_schedule())
    summary = result["summary"]

    assert summary["skipped"] == 2
    assert summary["unresolved"] == 2
    assert summary["by_code"][CODE_MISSING_MATERIAL] == 1
    assert summary["by_code"][CODE_NOT_PARAMETRIC] == 1


def test_skipped_entries_carry_code_and_suggested_value():
    result = ScheduleBuilder().recompute_durations(_mixed_schedule())
    entry = next(s for s in result["skipped"] if s["id"] == "В02")

    assert entry["code"] == CODE_MISSING_MATERIAL
    assert entry["suggested_duration"] == 10


def test_fully_resolved_schedule_reports_zero_unresolved():
    result = ScheduleBuilder().recompute_durations([_pipe()])
    assert result["summary"]["unresolved"] == 0


# ===================================================================
# Видимост в чата
# ===================================================================

def _report(by_code: dict, unresolved: int) -> dict:
    return {
        "duration_report": {
            "applied": True,
            "changes": [{"id": "В01", "name": "x", "old": 10, "new": 48,
                         "delta": 38, "reason": "r"}],
            "skipped": [], "warnings": [],
            "summary": {"total": 3, "recomputed": 1, "unchanged": 0,
                        "skipped": unresolved, "unresolved": unresolved,
                        "by_code": by_code,
                        "old_total_duration": 10, "new_total_duration": 48},
        }
    }


def test_unresolved_durations_are_reported():
    lines = ChatHandler._format_duration_report(
        _report({CODE_MISSING_MATERIAL: 2}, 2)
    )
    body = "\n".join(lines)
    assert "НЕДОКАЗАНА" in body
    assert "материалът не е указан" in body


def test_report_uses_human_labels_not_codes():
    lines = ChatHandler._format_duration_report(
        _report({CODE_MISSING_DN: 1, CODE_MISSING_LENGTH: 1}, 2)
    )
    body = "\n".join(lines)
    assert "липсва диаметър" in body
    assert "липсва дължина" in body
    assert "MISSING_DN" not in body


def test_non_parametric_is_explained_as_expected():
    """Изкоп/настилки нямат норми — не бива да изглеждат като дефект."""
    lines = ChatHandler._format_duration_report(
        _report({CODE_NOT_PARAMETRIC: 5}, 5)
    )
    body = "\n".join(lines)
    assert "очаквано" in body


def test_no_unresolved_section_when_everything_calculated():
    lines = ChatHandler._format_duration_report(_report({}, 0))
    assert not any("НЕДОКАЗАНА" in ln for ln in lines)


def test_report_tells_user_to_check_against_boq():
    lines = ChatHandler._format_duration_report(
        _report({CODE_MISSING_MATERIAL: 1}, 1)
    )
    assert any("КСС" in ln for ln in lines)


# ===================================================================
# Регресия за самия одитен извод
# ===================================================================

def test_calculated_and_suggested_are_never_the_same_field():
    """Двете стойности не бива пак да се слеят в едно поле."""
    mixed = ScheduleBuilder().recompute_durations(_mixed_schedule())["schedule"]
    calculated = [t for t in mixed if t.get("duration_source") == "calculated"]
    suggested = [t for t in mixed if t.get("duration_source") == "suggested"]

    assert calculated and suggested
    assert all("calculated_duration" in t for t in calculated)
    assert all("suggested_duration" in t or t.get("duration") is None
               for t in suggested)


def test_every_task_declares_its_source():
    """Задача без произход е точно случаят, който одитът намери."""
    mixed = ScheduleBuilder().recompute_durations(_mixed_schedule())["schedule"]
    assert all("duration_source" in t for t in mixed)
