"""Unit tests: ръчната модификация минава през същите защити като генерирането.

Одит 2026-07-23, точка 4: `_handle_modify_schedule` изпращаше целия график
към AI, приемаше цял нов, правеше САМО структурен diff, показваше
предупреждения и записваше резултата ВИНАГИ.

Одиторът пусна модификация, която създава duration=-5, самозависимост A→A и
кръгова зависимост.  Резултатът беше „Промяната е приложена." със
schedule_updated=True, докато пълният валидатор намираше три твърди грешки.

Тоест дори генериращият pipeline да е поправен, една команда „намали срока с
20 дни" разрушаваше всички инварианти.

FAILURE означава: модификационният път пак е заобиколка около защитите.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai_processor import AIProcessor  # noqa: E402
from src.chat_handler import ChatHandler  # noqa: E402
from src.schedule_builder import ScheduleBuilder  # noqa: E402

ORIGINAL = [
    {"id": "A", "name": "Изкоп", "start_day": 1, "end_day": 10, "duration": 10,
     "dependencies": []},
    {"id": "B", "name": "Полагане", "start_day": 11, "end_day": 20, "duration": 10,
     "dependencies": ["A"]},
]


def _handler(ai_returns: list[dict]) -> tuple[ChatHandler, MagicMock]:
    """Handler, чийто AI връща зададения „модифициран" график."""
    handler = ChatHandler()
    handler.builder = ScheduleBuilder()
    handler.current_schedule = [dict(t) for t in ORIGINAL]

    # `_progress` се задава в process_message; тук викаме метода директно.
    handler._progress = lambda pct, txt: None

    ai = MagicMock()
    ai.build_system_prompt.return_value = "system"
    ai.build_verification_prompt.return_value = "rules"
    ai.router.chat.return_value = {"content": "{}", "model": "test", "cost": 0.0}
    ai.router.run_correction_cycle.return_value = {
        "status": "approved",
        "schedule": {"tasks": ai_returns},
        "cycles": 1,
        "total_cost": 0.0,
    }
    handler.ai = ai

    project_mgr = MagicMock()
    project_mgr.current_project = {"id": "p1"}
    handler.project_mgr = project_mgr
    return handler, project_mgr


# ===================================================================
# Сценариите на одитора
# ===================================================================

def _validate(tasks: list[dict]) -> dict:
    return AIProcessor._validate_final_schedule({"tasks": tasks})


def test_negative_duration_from_modification_is_invalid():
    bad = [dict(ORIGINAL[0], duration=-5)]
    assert _validate(bad)["valid"] is False


def test_self_dependency_from_modification_is_invalid():
    bad = [{"id": "A", "name": "A", "start_day": 1, "end_day": 10,
            "duration": 10, "dependencies": ["A"]}]
    assert _validate(bad)["valid"] is False


def test_circular_dependency_from_modification_is_invalid():
    bad = [
        {"id": "A", "name": "A", "start_day": 1, "end_day": 10, "duration": 10,
         "dependencies": ["B"]},
        {"id": "B", "name": "B", "start_day": 11, "end_day": 20, "duration": 10,
         "dependencies": ["A"]},
    ]
    assert _validate(bad)["valid"] is False


def test_the_auditors_combined_case_is_caught():
    """duration=-5 + самозависимост + кръгова зависимост наведнъж."""
    bad = [
        {"id": "A", "name": "A", "start_day": 1, "end_day": 10, "duration": -5,
         "dependencies": ["A"]},
        {"id": "B", "name": "B", "start_day": 11, "end_day": 20, "duration": 10,
         "dependencies": ["A"]},
    ]
    result = _validate(bad)
    assert result["valid"] is False
    assert len(result["errors"]) >= 2


# ===================================================================
# Gate поведение
# ===================================================================

def test_invalid_modification_does_not_replace_current_schedule():
    handler, _ = _handler([
        {"id": "A", "name": "Изкоп", "start_day": 1, "end_day": 20,
         "duration": 20, "dependencies": []},
        {"id": "B", "name": "Полагане", "start_day": 5, "end_day": 14,
         "duration": 10, "dependencies": ["A"]},   # започва преди края на A
    ])
    before = [dict(t) for t in handler.current_schedule]

    result = handler._handle_modify_schedule("намали срока с 20 дни")

    assert result["schedule_updated"] is False
    assert handler.current_schedule == before
    assert "ОТХВЪРЛЕНА" in result["response"]


def test_invalid_modification_is_kept_for_diagnostics():
    handler, _ = _handler([
        {"id": "A", "name": "A", "start_day": 1, "end_day": 20, "duration": 20,
         "dependencies": []},
        {"id": "B", "name": "B", "start_day": 5, "end_day": 14, "duration": 10,
         "dependencies": ["A"]},
    ])
    handler._handle_modify_schedule("промени нещо")
    assert getattr(handler, "rejected_schedule", None) is not None


def test_invalid_modification_is_not_saved_to_project():
    handler, project_mgr = _handler([
        {"id": "A", "name": "A", "start_day": 1, "end_day": 20, "duration": 20,
         "dependencies": []},
        {"id": "B", "name": "B", "start_day": 5, "end_day": 14, "duration": 10,
         "dependencies": ["A"]},
    ])
    handler._handle_modify_schedule("промени нещо")
    project_mgr.save_progress.assert_not_called()


def test_valid_modification_is_applied():
    handler, project_mgr = _handler([
        {"id": "A", "name": "Изкоп", "start_day": 1, "end_day": 8,
         "duration": 8, "dependencies": []},
        {"id": "B", "name": "Полагане", "start_day": 9, "end_day": 18,
         "duration": 10, "dependencies": ["A"]},
    ])
    result = handler._handle_modify_schedule("скъси изкопа с 2 дни")

    assert result["schedule_updated"] is True
    assert "приложена" in result["response"]
    project_mgr.save_progress.assert_called_once()


def test_result_carries_validation():
    handler, _ = _handler(ORIGINAL)
    result = handler._handle_modify_schedule("нещо")
    assert "validation" in result
    assert result["validation"]["checked"] is True


def test_correction_info_reports_invalid():
    handler, _ = _handler([
        {"id": "A", "name": "A", "start_day": 1, "end_day": 20, "duration": 20,
         "dependencies": []},
        {"id": "B", "name": "B", "start_day": 5, "end_day": 14, "duration": 10,
         "dependencies": ["A"]},
    ])
    result = handler._handle_modify_schedule("нещо")
    assert result["correction_info"]["status"] == "invalid"


# ===================================================================
# Детерминизмът се прилага и тук
# ===================================================================

def test_durations_are_recomputed_after_modification():
    """AI задава 999 на изчислима задача — кодът си я връща и тук."""
    handler, _ = _handler([
        {"id": "В01", "name": "Полагане DN500 PE", "length_m": 720, "dn": 500,
         "material": "PE", "duration": 999, "start_day": 1, "end_day": 999,
         "dependencies": []},
    ])
    result = handler._handle_modify_schedule("удължи полагането")
    tasks = AIProcessor._tasks_from(result["schedule_data"])

    assert tasks[0]["duration"] == 48
    assert tasks[0]["duration_source"] == "calculated"


def test_duration_report_is_returned():
    handler, _ = _handler([
        {"id": "В01", "name": "Полагане DN500 PE", "length_m": 720, "dn": 500,
         "material": "PE", "duration": 999, "start_day": 1, "end_day": 999,
         "dependencies": []},
    ])
    result = handler._handle_modify_schedule("промени")
    assert result["duration_report"]["applied"] is True


def test_provenance_survives_modification():
    handler, _ = _handler([
        {"id": "В01", "name": "Полагане DN500 PE", "length_m": 720, "dn": 500,
         "material": "PE", "duration": 48, "start_day": 1, "end_day": 48,
         "dependencies": []},
    ])
    result = handler._handle_modify_schedule("нещо")
    tasks = AIProcessor._tasks_from(result["schedule_data"])
    assert all("duration_source" in t for t in tasks)


# ===================================================================
# Регресия за бъг, въведен при поправката на gate-а
# ===================================================================

def test_modification_flow_has_no_undefined_valid_flag():
    """`_valid` беше добавен глобално и остана недефиниран тук → NameError."""
    handler, _ = _handler(ORIGINAL)
    result = handler._handle_modify_schedule("нещо")   # не бива да гърми
    assert "response" in result
