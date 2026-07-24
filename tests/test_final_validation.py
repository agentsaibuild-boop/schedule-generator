"""Unit tests: детерминистичната валидация е ЗАКАЧЕНА в pipeline-а.

Одит 2026-07-23 установи, че `ScheduleBuilder.validate_schedule` съществува,
е покрита с тестове, и НЕ СЕ ВИКА никъде в production код — само в тестове.
Тоест кръгови зависимости, задачи преди края на предшественика си и
несъответствия end_day/duration не се проверяваха от нищо преди експорт.

Тези тестове пазят самото ЗАКАЧАНЕ, не логиката на валидацията (тя си има
tests/test_validate_schedule.py).  Разликата е съществена: там се проверява
че проверката работи; тук — че изобщо се изпълнява.

FAILURE означава: последната дума за логиката на графика отново я има AI, а
не код.  Резултатът е убедително изглеждащ, но инженерно необоснован MS
Project файл, стигнал до възложителя без нито една детерминистична проверка.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai_processor import AIProcessor  # noqa: E402
from src.chat_handler import ChatHandler  # noqa: E402

VALID = [
    {"id": "A", "name": "Изкоп", "start_day": 1, "end_day": 10, "duration": 10,
     "dependencies": []},
    {"id": "B", "name": "Полагане", "start_day": 11, "end_day": 20, "duration": 10,
     "dependencies": ["A"]},
]

CYCLE = [
    {"id": "A", "name": "A", "start_day": 1, "end_day": 10, "duration": 10,
     "dependencies": ["B"]},
    {"id": "B", "name": "B", "start_day": 11, "end_day": 20, "duration": 10,
     "dependencies": ["A"]},
]

# Наследник започва ПРЕДИ края на предшественика — точно това, което
# `enrich_for_msproject` може да причини, променяйки lag_days.
FS_VIOLATION = [
    {"id": "A", "name": "Изкоп", "start_day": 1, "end_day": 20, "duration": 20,
     "dependencies": []},
    {"id": "B", "name": "Полагане", "start_day": 5, "end_day": 14, "duration": 10,
     "dependencies": ["A"]},
]


# ===================================================================
# _validate_final_schedule — приема трите форми по веригата
# ===================================================================

def test_accepts_dict_with_tasks():
    result = AIProcessor._validate_final_schedule({"tasks": VALID})
    assert result["checked"] is True
    assert result["valid"] is True
    assert result["task_count"] == 2


def test_accepts_bare_list():
    result = AIProcessor._validate_final_schedule(VALID)
    assert result["checked"] is True
    assert result["valid"] is True


def test_accepts_json_string():
    import json
    result = AIProcessor._validate_final_schedule(json.dumps({"tasks": VALID}))
    assert result["checked"] is True
    assert result["task_count"] == 2


def test_empty_schedule_is_not_silently_ok():
    """Липсата на задачи не бива да минава за успешна проверка."""
    result = AIProcessor._validate_final_schedule({})
    assert result["checked"] is False
    assert result["valid"] is False


def test_none_schedule_is_not_silently_ok():
    result = AIProcessor._validate_final_schedule(None)
    assert result["checked"] is False


def test_non_dict_entries_are_ignored():
    result = AIProcessor._validate_final_schedule({"tasks": VALID + ["боклук", 42]})
    assert result["task_count"] == 2


# ===================================================================
# Реалните дефекти биват хванати
# ===================================================================

def test_cycle_is_caught():
    result = AIProcessor._validate_final_schedule({"tasks": CYCLE})
    assert result["valid"] is False
    assert any("ръгова" in e for e in result["errors"])


def test_fs_violation_is_caught():
    """Задача, започваща преди края на предшественика си."""
    result = AIProcessor._validate_final_schedule({"tasks": FS_VIOLATION})
    assert result["valid"] is False
    assert any("започва ден" in e for e in result["errors"])


def test_duration_end_day_mismatch_is_caught():
    tasks = [{"id": "A", "name": "A", "start_day": 1, "end_day": 99,
              "duration": 10, "dependencies": []}]
    result = AIProcessor._validate_final_schedule({"tasks": tasks})
    assert result["valid"] is False


def test_dependency_on_missing_task_is_caught():
    tasks = [{"id": "A", "name": "A", "start_day": 1, "end_day": 10,
              "duration": 10, "dependencies": ["НЯМА"]}]
    result = AIProcessor._validate_final_schedule({"tasks": tasks})
    assert result["valid"] is False


def test_clean_schedule_reports_valid():
    result = AIProcessor._validate_final_schedule({"tasks": VALID})
    assert result["valid"] is True
    assert result["errors"] == []


# ===================================================================
# Закачането в generate_schedule
# ===================================================================

def test_pipeline_runs_validation_after_enrichment(monkeypatch):
    """Валидацията ТРЯБВА да види резултата СЛЕД AI обогатяването.

    Смисълът ѝ е да хване точно това, което последната AI стъпка е счупила.
    """
    seen: dict = {}

    def fake_validate(schedule):
        seen["schedule"] = schedule
        return {"valid": True, "checked": True, "task_count": 1,
                "errors": [], "warnings": []}

    monkeypatch.setattr(AIProcessor, "_validate_final_schedule",
                        staticmethod(fake_validate))

    proc = AIProcessor(router=None, knowledge_manager=None)

    # Симулираме края на pipeline-а: обогатяването е върнало ПРОМЕНЕН график.
    enriched = {"tasks": [{"id": "A", "name": "обогатена", "dependencies": []}]}
    result = AIProcessor._validate_final_schedule(enriched)

    assert seen["schedule"] is enriched
    assert result["checked"] is True
    assert proc is not None


def test_generate_schedule_result_carries_validation_key():
    """Договорът на резултата съдържа `validation` — иначе UI-ът няма какво да покаже."""
    import inspect
    source = inspect.getsource(AIProcessor.generate_schedule)
    assert '"validation": validation' in source
    # И е след обогатяването, не преди него.
    assert source.index("enrich_for_msproject") < source.index("_validate_final_schedule")


# ===================================================================
# Видимост в чата
# ===================================================================

def test_clean_validation_is_reported():
    lines = ChatHandler._format_validation_report(
        {"validation": {"valid": True, "checked": True, "task_count": 12,
                        "errors": [], "warnings": []}}
    )
    assert any("чиста" in ln for ln in lines)
    assert any("12 задачи" in ln for ln in lines)


def test_errors_are_reported_prominently():
    lines = ChatHandler._format_validation_report(
        {"validation": {"valid": False, "checked": True, "task_count": 5,
                        "errors": ["Кръгова зависимост: A → B → A."],
                        "warnings": []}}
    )
    body = "\n".join(lines)
    assert "НЕ минава" in body
    assert "Кръгова зависимост" in body


def test_errors_warn_against_sending_to_client():
    """Точката на цялата поправка: човекът да не изпрати невалиден график."""
    lines = ChatHandler._format_validation_report(
        {"validation": {"valid": False, "checked": True, "task_count": 5,
                        "errors": ["нещо"], "warnings": []}}
    )
    assert any("възложителя" in ln for ln in lines)


def test_warnings_shown_without_alarm():
    lines = ChatHandler._format_validation_report(
        {"validation": {"valid": True, "checked": True, "task_count": 5,
                        "errors": [], "warnings": ["Екип X е на 3 задачи"]}}
    )
    body = "\n".join(lines)
    assert "предупреждения" in body
    assert "НЕ минава" not in body


def test_unchecked_validation_is_flagged():
    lines = ChatHandler._format_validation_report(
        {"validation": {"valid": False, "checked": False, "task_count": 0,
                        "errors": ["Няма задачи"], "warnings": []}}
    )
    assert any("не можа да се изпълни" in ln for ln in lines)


def test_missing_validation_key_yields_nothing():
    assert ChatHandler._format_validation_report({}) == []


def test_long_error_list_is_truncated():
    errors = [f"грешка {i}" for i in range(12)]
    lines = ChatHandler._format_validation_report(
        {"validation": {"valid": False, "checked": True, "task_count": 5,
                        "errors": errors, "warnings": []}}
    )
    listed = [ln for ln in lines if ln.strip().startswith("- грешка")]
    assert len(listed) == 6
    assert any("още 6" in ln for ln in lines)


# ===================================================================
# Регресия: проверката не бива пак да остане невикана
# ===================================================================

def test_validate_schedule_is_called_from_production_code():
    """Регресия за одит 2026-07-23: беше дефинирана, тествана и невикана."""
    root = Path(__file__).parent.parent
    production = list((root / "src").glob("*.py")) + [root / "app.py"]
    callers = [
        p.name for p in production
        if "validate_schedule(" in p.read_text(encoding="utf-8")
        and p.name != "schedule_builder.py"
    ]
    assert callers, (
        "validate_schedule не се вика от нито един production модул — "
        "детерминистичната проверка отново е изключена от pipeline-а."
    )
