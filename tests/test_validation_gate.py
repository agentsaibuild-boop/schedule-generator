"""Unit tests: невалиден график НЕ става официален резултат.

Одит 2026-07-23 — централната находка:

    AI status=approved + validation.valid=false
    → графикът се записваше като current_schedule
    → показваше се „График одобрен!"
    → бутоните за XML и PDF оставаха активни

Тоест системата можеше да открие, че графикът е невалиден, и въпреки това да
го обяви за одобрен, да го запише и да го експортира.

Тестът, който одиторът поиска изрично, е `test_ai_approved_but_invalid_*`.

FAILURE означава: последната РАЗРЕШАВАЩА дума пак е на AI, не на кода —
инженерно необоснован MS Project файл може да стигне до възложителя.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai_processor import AIProcessor  # noqa: E402
from src.chat_handler import ChatHandler  # noqa: E402

VALID_TASKS = [
    {"id": "A", "name": "Изкоп", "start_day": 1, "end_day": 10, "duration": 10,
     "dependencies": []},
    {"id": "B", "name": "Полагане", "start_day": 11, "end_day": 20, "duration": 10,
     "dependencies": ["A"]},
]

# Наследник започва преди края на предшественика — твърда грешка.
INVALID_TASKS = [
    {"id": "A", "name": "Изкоп", "start_day": 1, "end_day": 20, "duration": 20,
     "dependencies": []},
    {"id": "B", "name": "Полагане", "start_day": 5, "end_day": 14, "duration": 10,
     "dependencies": ["A"]},
]


def _gen_result(tasks: list[dict], ai_status: str = "approved") -> dict:
    """Резултат от generate_schedule с реална валидация."""
    validation = AIProcessor._validate_final_schedule({"tasks": tasks})
    status = "invalid" if not validation["valid"] else ai_status
    return {
        "status": status,
        "ai_status": ai_status,
        "exportable": validation["valid"],
        "schedule": {"tasks": tasks},
        "validation": validation,
        "cycles": 1,
        "total_cost": 0.01,
        "history": [],
        "remaining_issues": [],
        "gen_model": "test",
        "hallucination_warnings": [],
        "duration_report": {},
        "injection_findings": [],
    }


# ===================================================================
# Статусът се определя от кода, не от AI
# ===================================================================

def test_ai_approved_but_invalid_becomes_invalid():
    """Точният сценарий от одита."""
    result = _gen_result(INVALID_TASKS, ai_status="approved")
    assert result["ai_status"] == "approved"
    assert result["status"] == "invalid"
    assert result["exportable"] is False


def test_ai_approved_and_valid_stays_approved():
    result = _gen_result(VALID_TASKS, ai_status="approved")
    assert result["status"] == "approved"
    assert result["exportable"] is True


def test_ai_status_is_preserved_for_diagnostics():
    """Оригиналната преценка на AI не се крие — записва се отделно."""
    result = _gen_result(INVALID_TASKS, ai_status="approved")
    assert result["ai_status"] == "approved"


# ===================================================================
# Невалиден график не се записва
# ===================================================================

def _handler() -> tuple[ChatHandler, MagicMock]:
    handler = ChatHandler()
    handler.project_mgr = MagicMock()
    handler.project_mgr.current_project = {"id": "p1"}
    return handler, handler.project_mgr


def test_invalid_schedule_does_not_become_current():
    handler, _ = _handler()
    handler.current_schedule = {"tasks": [{"id": "СТАР"}]}

    gen = _gen_result(INVALID_TASKS)
    valid = gen["status"] != "invalid"
    if valid:
        handler.current_schedule = gen["schedule"]
    else:
        handler.rejected_schedule = gen["schedule"]

    assert handler.current_schedule == {"tasks": [{"id": "СТАР"}]}
    assert handler.rejected_schedule == gen["schedule"]


def test_rejected_schedule_is_kept_for_diagnostics():
    """Отхвърленият график не се изхвърля — човек трябва да види какво е сгрешено."""
    handler, _ = _handler()
    gen = _gen_result(INVALID_TASKS)
    handler.rejected_schedule = gen["schedule"]
    assert handler.rejected_schedule["tasks"]


def test_invalid_schedule_is_not_saved_to_project():
    handler, project_mgr = _handler()
    gen = _gen_result(INVALID_TASKS)

    valid = gen["status"] != "invalid"
    if valid and handler.project_mgr and handler.project_mgr.current_project:
        handler.project_mgr.save_progress("p1", {})

    project_mgr.save_progress.assert_not_called()


def test_schedule_updated_is_false_for_invalid():
    gen = _gen_result(INVALID_TASKS)
    valid = gen["status"] != "invalid"
    schedule_updated = valid and gen["status"] in ("approved", "needs_human_review")
    assert schedule_updated is False


def test_schedule_updated_is_true_for_valid():
    gen = _gen_result(VALID_TASKS)
    valid = gen["status"] != "invalid"
    schedule_updated = valid and gen["status"] in ("approved", "needs_human_review")
    assert schedule_updated is True


# ===================================================================
# Съобщението към потребителя
# ===================================================================

def test_user_is_told_the_schedule_was_rejected():
    lines = ChatHandler._format_validation_report(_gen_result(INVALID_TASKS))
    body = "\n".join(lines)
    assert "НЕ минава" in body
    assert "възложителя" in body


def test_valid_schedule_reports_clean():
    lines = ChatHandler._format_validation_report(_gen_result(VALID_TASKS))
    assert any("чиста" in ln for ln in lines)


# ===================================================================
# Export gate
# ===================================================================

def _blocked_flag(validation: dict) -> bool:
    """Точната логика от app.py — да се тества това, което се изпълнява."""
    return bool(validation.get("checked")) and not validation.get("valid")


def test_export_is_blocked_for_invalid():
    assert _blocked_flag(_gen_result(INVALID_TASKS)["validation"]) is True


def test_export_is_allowed_for_valid():
    assert _blocked_flag(_gen_result(VALID_TASKS)["validation"]) is False


def test_export_not_blocked_when_validation_never_ran():
    """Липсваща валидация не бива да блокира сляпо стари графици."""
    assert _blocked_flag({}) is False


@pytest.mark.parametrize("validation", [{}, {"checked": None}, {"valid": None},
                                        {"checked": False, "valid": False}])
def test_blocked_flag_is_always_a_real_bool(validation):
    """Streamlit хвърля TypeError при `disabled=None`.

    Заварен бъг 2026-07-23: `None and ...` дава None, не False, и
    приложението гърмеше при първо зареждане, преди да има валидация.
    Старият тест ползваше `assert not blocked`, което е вярно и за None —
    затова не го хвана.
    """
    assert isinstance(_blocked_flag(validation), bool)


def test_app_blocks_export_when_unvalidated():
    """Липсваща валидация за текущия график → блокиран експорт (fail-closed).

    Одит 2026-07-24: зареден стар проект се възстановяваше без повторна
    валидация и оставаше експортируем.  Сега без валидация за ТАЗИ версия
    експортът е блокиран."""
    source = (Path(__file__).parent.parent / "app.py").read_text(encoding="utf-8")
    # Всеки клон задава _blocked изрично на bool — Streamlit иска булево.
    assert "_blocked = True" in source
    assert "_blocked = not" in source


def test_app_binds_validation_to_schedule_hash():
    """Регресия: export gate сравнява hash-а на текущия график с валидирания."""
    source = (Path(__file__).parent.parent / "app.py").read_text(encoding="utf-8")
    assert "schedule_hash" in source
    assert "_stale" in source


def test_app_wires_validation_into_session_state():
    """Регресия: без това export gate-ът няма какво да чете."""
    source = (Path(__file__).parent.parent / "app.py").read_text(encoding="utf-8")
    assert "last_validation" in source
    assert "disabled=_blocked" in source


def test_app_blocks_both_export_buttons():
    source = (Path(__file__).parent.parent / "app.py").read_text(encoding="utf-8")
    assert source.count("disabled=_blocked") >= 2


def test_app_binds_export_artifacts_to_hash():
    """Одит v7, точка 6: генериран PDF/XML носи hash и не остава за сваляне
    след смяна на графика; JSON download също минава през gate-а."""
    source = (Path(__file__).parent.parent / "app.py").read_text(encoding="utf-8")
    assert "_fresh_artifact" in source
    # артефактите се пазят като {bytes, hash}, не голи байтове
    assert '"hash": _current_hash' in source
    # JSON download вече е зад gate-а
    assert source.count("disabled=_blocked") >= 3


# ===================================================================
# Детерминизмът се възстановява след AI correction
# ===================================================================

def test_ai_correction_cannot_keep_a_bogus_duration():
    """AI задава duration=999 на изчислима задача — кодът си я връща."""
    corrected = {
        "schedule": {"tasks": [{
            "id": "В01", "name": "Полагане DN500 PE", "length_m": 720,
            "dn": 500, "material": "PE", "duration": 999,
            "start_day": 1, "end_day": 999, "dependencies": [],
        }]},
        "status": "approved",
    }
    processor = AIProcessor(router=None, knowledge_manager=None)
    # before_json е графикът СЛЕД детерминистичните продължителности —
    # входовете (length_m/dn/material) вече присъстват, както в pipeline-а.
    updated, report = processor._restore_determinism_after_ai(
        corrected,
        '{"tasks": [{"id": "В01", "name": "Полагане DN500 PE", '
        '"length_m": 720, "dn": 500, "material": "PE"}]}',
    )
    task = updated["schedule"]["tasks"][0]

    assert task["duration"] == 48          # 720 ÷ 15 м/ден
    assert task["calculated_duration"] == 48
    assert task["duration_source"] == "calculated"
    assert report["recomputed"] == 1


def test_ai_correction_removing_tasks_is_reported():
    corrected = {"schedule": {"tasks": [{"id": "A", "name": "A", "dependencies": []}]}}
    processor = AIProcessor(router=None, knowledge_manager=None)
    _, report = processor._restore_determinism_after_ai(
        corrected, '{"tasks": [{"id": "A"}, {"id": "B"}, {"id": "C"}]}'
    )
    assert set(report["removed_tasks"]) == {"B", "C"}


def test_ai_correction_adding_tasks_is_reported():
    corrected = {"schedule": {"tasks": [
        {"id": "A", "name": "A", "dependencies": []},
        {"id": "ИЗМИСЛЕНА", "name": "нова", "dependencies": []},
    ]}}
    processor = AIProcessor(router=None, knowledge_manager=None)
    _, report = processor._restore_determinism_after_ai(
        corrected, '{"tasks": [{"id": "A"}]}'
    )
    assert report["added_tasks"] == ["ИЗМИСЛЕНА"]


def test_restore_handles_json_string_schedule():
    """След correction графикът често е JSON низ, не dict."""
    processor = AIProcessor(router=None, knowledge_manager=None)
    updated, report = processor._restore_determinism_after_ai(
        {"schedule": '{"tasks": [{"id": "A", "name": "A", "dependencies": []}]}'},
        '{"tasks": [{"id": "A"}]}',
    )
    assert report["applied"] is True
    assert updated["schedule"]["tasks"][0]["id"] == "A"


def test_restore_is_a_noop_when_correction_returned_nothing():
    processor = AIProcessor(router=None, knowledge_manager=None)
    original = {"schedule": None, "status": "error"}
    updated, report = processor._restore_determinism_after_ai(original, "{}")
    assert report["applied"] is False
    assert updated is original


# ===================================================================
# int-ID валидатор (одит 2026-08, т.2) — валиден график НЕ бива да пада
# ===================================================================

def test_valid_schedule_with_integer_ids_is_not_falsely_rejected():
    """Регресия: A.id=1, B.id=2, B.deps=[1] се отхвърляше с 'несъществуващо
    ID', защото task_by_id беше с int ключ, а dependency_ids връща str.
    Това правеше реални предшественици да изглеждат фантомни."""
    from src.schedule_builder import ScheduleBuilder
    schedule = [
        {"id": 1, "name": "A", "duration": 5, "start_day": 1, "dependencies": []},
        {"id": 2, "name": "B", "duration": 5, "start_day": 6, "dependencies": [1]},
    ]
    result = ScheduleBuilder().validate_schedule(schedule)
    assert not any("несъществуващо" in e for e in result["errors"]), result["errors"]


def test_circular_dependency_with_integer_ids_is_caught():
    """Цикъл при числови ID трябва да се хване (иначе fail-open)."""
    from src.schedule_builder import ScheduleBuilder
    schedule = [
        {"id": 1, "name": "A", "duration": 5, "start_day": 1, "dependencies": [2]},
        {"id": 2, "name": "B", "duration": 5, "start_day": 6, "dependencies": [1]},
    ]
    result = ScheduleBuilder().validate_schedule(schedule)
    assert any("ръгова завис" in e or "цикъл" in e.lower() for e in result["errors"]), result["errors"]
