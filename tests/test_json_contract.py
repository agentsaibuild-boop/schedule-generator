"""Unit tests for src/json_contract.py — договор за JSON отговорите (P7).

Covers: parse_json_strict (разграничава провал от отговор), coerce
        (привеждане към форма + докладване на проблеми), parse_contract
        (хвърля при неизползваем отговор) и трите спецификации.

FAILURE означава: src/json_contract.py е счупен — провалено парсване отново
ще се маскира като валиден отговор.  Конкретно: счупен JSON от контрольора
ще изглежда като „графикът има проблеми", ще задейства корекционни цикли за
несъществуващ дефект и ще завърши с „needs_human_review".
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.json_contract import (  # noqa: E402
    CORRECTION_SPEC,
    LESSON_SPEC,
    VERIFICATION_SPEC,
    JSONContractError,
    coerce,
    parse_contract,
    parse_json_strict,
)


# ===================================================================
# parse_json_strict
# ===================================================================

def test_clean_object_parses():
    result = parse_json_strict('{"approved": true}')
    assert result.data == {"approved": True}
    assert result.error == ""
    assert result.recovered is False


def test_markdown_fence_is_stripped():
    result = parse_json_strict('```json\n{"a": 1}\n```')
    assert result.data == {"a": 1}
    assert result.recovered is False


def test_object_embedded_in_prose_is_recovered():
    result = parse_json_strict('Ето резултата: {"a": 1} готово.')
    assert result.data == {"a": 1}
    assert result.recovered is True


@pytest.mark.parametrize("raw", ["", "   \n\t ", None])
def test_empty_input_reports_failure(raw):
    result = parse_json_strict(raw)
    assert result.data is None
    assert "празен" in result.error


def test_invalid_json_reports_failure():
    result = parse_json_strict("моделът се обърка напълно")
    assert result.data is None
    assert result.error


def test_truncated_json_reports_failure():
    result = parse_json_strict('{"approved": true, "issues": [')
    assert result.data is None


def test_top_level_array_is_not_an_object():
    result = parse_json_strict('[{"a": 1}]')
    assert result.data is None
    assert "обект" in result.error


def test_top_level_scalar_is_not_an_object():
    result = parse_json_strict("42")
    assert result.data is None


def test_failure_never_returns_a_usable_looking_dict():
    """Ядрото на P7: провалът не бива да прилича на отговор."""
    assert parse_json_strict("боклук").data is None


# ===================================================================
# coerce
# ===================================================================

def test_coerce_passes_valid_data_through():
    data, problems = coerce(
        {"approved": True, "issues": [], "corrections": [], "summary": "ок"},
        VERIFICATION_SPEC,
    )
    assert data["approved"] is True
    assert problems == []


def test_coerce_fills_missing_fields_and_reports():
    data, problems = coerce({"approved": True}, VERIFICATION_SPEC)
    assert data["issues"] == []
    assert data["summary"] == ""
    assert any("issues" in p for p in problems)


def test_coerce_defaults_are_not_shared_between_calls():
    """Изменяемите стойности по подразбиране трябва да са пресни всеки път."""
    first, _ = coerce({}, VERIFICATION_SPEC)
    first["issues"].append("замърсяване")
    second, _ = coerce({}, VERIFICATION_SPEC)
    assert second["issues"] == []


def test_coerce_rejects_wrong_type_and_reports():
    data, problems = coerce({"approved": "да", "issues": []}, VERIFICATION_SPEC)
    assert data["approved"] is False
    assert any("approved" in p for p in problems)


def test_coerce_does_not_accept_int_as_bool():
    """1 не е True — иначе 'approved: 1' минава за одобрение."""
    data, problems = coerce({"approved": 1}, VERIFICATION_SPEC)
    assert data["approved"] is False
    assert any("approved" in p for p in problems)


def test_coerce_does_not_accept_bool_as_number():
    data, problems = coerce({"count": True}, {"count": (int, 0)})
    assert data["count"] == 0
    assert problems


def test_coerce_treats_null_list_as_empty():
    data, problems = coerce({"issues": None}, {"issues": (list, list)})
    assert data["issues"] == []
    assert problems


def test_coerce_stringifies_numbers_for_string_fields():
    data, problems = coerce({"summary": 42}, {"summary": (str, "")})
    assert data["summary"] == "42"
    assert problems == []


def test_coerce_ignores_extra_fields():
    data, _ = coerce({"approved": True, "неочаквано": "x"}, VERIFICATION_SPEC)
    assert "неочаквано" not in data


# ===================================================================
# parse_contract
# ===================================================================

def test_parse_contract_returns_coerced_data():
    result = parse_contract(
        '{"approved": true, "issues": ["x"], "corrections": [], "summary": "s"}',
        VERIFICATION_SPEC,
        "верификация",
    )
    assert result["approved"] is True
    assert result["issues"] == ["x"]


def test_parse_contract_raises_on_garbage():
    with pytest.raises(JSONContractError) as exc:
        parse_contract("моделът се обърка", VERIFICATION_SPEC, "верификация")
    assert "верификация" in str(exc.value)


def test_parse_contract_raises_on_empty():
    with pytest.raises(JSONContractError):
        parse_contract("", VERIFICATION_SPEC, "верификация")


def test_parse_contract_tolerates_missing_fields():
    """Липсващо поле е дефект във формата, но не прави отговора неизползваем."""
    result = parse_contract('{"approved": false}', VERIFICATION_SPEC, "верификация")
    assert result["issues"] == []
    assert result["corrections"] == []


def test_parse_contract_recovers_embedded_json():
    result = parse_contract(
        'Проверих: {"approved": true, "summary": "ок"} край',
        VERIFICATION_SPEC,
        "верификация",
    )
    assert result["approved"] is True


# ===================================================================
# Спецификации
# ===================================================================

def test_correction_spec_shape():
    result = parse_contract(
        '{"schedule": {"tasks": []}, "applied": ["a"]}', CORRECTION_SPEC, "корекция"
    )
    assert result["schedule"] == {"tasks": []}
    assert result["applied"] == ["a"]


def test_correction_spec_defaults_are_safe():
    result = parse_contract("{}", CORRECTION_SPEC, "корекция")
    assert result["schedule"] == {}
    assert result["applied"] == []


def test_lesson_spec_shape():
    result = parse_contract(
        '{"approved": true, "formatted_lesson": "текст", "reason": "ок"}',
        LESSON_SPEC,
        "урок",
    )
    assert result["approved"] is True
    assert result["formatted_lesson"] == "текст"


def test_lesson_spec_defaults_to_not_approved():
    """При съмнение урокът НЕ е одобрен."""
    result = parse_contract("{}", LESSON_SPEC, "урок")
    assert result["approved"] is False
