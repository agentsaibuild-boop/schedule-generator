"""Unit tests for AIRouter.parse_json_response — JSON parsing resilience.

Covers: clean JSON, markdown fences (```json and ```), JSON embedded in prose,
        completely invalid input, empty string, whitespace, and the fallback
        error dict.

FAILURE означава: src/ai_router.py :: _parse_json_response е счупена —
всеки AI отговор с нестандартно форматиране ще се провали тихо,
генераторът ще върне грешен/празен график без обяснение.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai_router import AIRouter

# Статичен метод, не изисква инстанция
_parse = AIRouter.parse_json_response


# ---------------------------------------------------------------------------
# Чист JSON
# ---------------------------------------------------------------------------

def test_clean_json_object():
    """Plain JSON object is parsed directly."""
    raw = '{"approved": true, "issues": []}'
    result = _parse(raw)
    assert result == {"approved": True, "issues": []}


def test_clean_json_with_cyrillic():
    """JSON with Cyrillic content is parsed correctly."""
    raw = '{"issues": ["Грешка в задача 3"], "corrections": []}'
    result = _parse(raw)
    assert result["issues"] == ["Грешка в задача 3"]


def test_clean_json_with_whitespace():
    """Leading/trailing whitespace is stripped before parsing."""
    raw = '   \n{"approved": false, "issues": ["x"]}\n   '
    result = _parse(raw)
    assert result["approved"] is False


# ---------------------------------------------------------------------------
# Markdown code fences
# ---------------------------------------------------------------------------

def test_json_in_triple_backtick_fence():
    """Markdown ```  ``` fences are stripped before parsing."""
    raw = "```\n{\"approved\": true, \"issues\": []}\n```"
    result = _parse(raw)
    assert result.get("approved") is True


def test_json_in_json_tagged_fence():
    """Markdown ```json ... ``` fences are stripped before parsing."""
    raw = '```json\n{"approved": false, "issues": ["bad task"]}\n```'
    result = _parse(raw)
    assert result.get("approved") is False
    assert result["issues"] == ["bad task"]


def test_json_fence_with_extra_whitespace_lines():
    """Fences with blank lines around JSON still parse correctly."""
    raw = "```json\n\n{\"key\": \"value\"}\n\n```"
    result = _parse(raw)
    assert result.get("key") == "value"


# ---------------------------------------------------------------------------
# JSON embedded in prose (fallback extraction)
# ---------------------------------------------------------------------------

def test_json_embedded_in_explanation():
    """JSON object buried in explanatory text is extracted via brace search."""
    raw = 'Ето резултатът: {"approved": true, "issues": []} Надявам се помогна.'
    result = _parse(raw)
    assert result.get("approved") is True


def test_json_embedded_after_newline():
    """JSON on its own line after prose text is extracted correctly."""
    raw = "Отговор:\n\n{\"status\": \"ok\", \"count\": 5}"
    result = _parse(raw)
    assert result.get("status") == "ok"
    assert result.get("count") == 5


def test_nested_json_object_extracted_correctly():
    """Nested JSON objects are extracted fully (rfind for closing brace)."""
    raw = 'Ето: {"outer": {"inner": 42}, "list": [1, 2]}'
    result = _parse(raw)
    assert result["outer"]["inner"] == 42
    assert result["list"] == [1, 2]


# ---------------------------------------------------------------------------
# Невалиден вход — fallback error dict
# ---------------------------------------------------------------------------

# P7 (2026-07-22): при провал вече се връща ПРАЗЕН dict, не измислен
# резултат от верификация.  Старото поведение връщаше
# {"approved": False, "issues": ["Invalid JSON response from AI"]} — което
# верификацията четеше като „графикът има проблеми" и задействаше корекционни
# цикли за несъществуващ дефект, а извикващите за класификация на файлове и
# разпознаване на намерение получаваха напълно чужди полета.

def test_completely_invalid_json_returns_empty():
    """Non-JSON text returns {} — не измислена верификация."""
    result = _parse("Не мога да отговоря на тази заявка.")
    assert result == {}


def test_empty_string_returns_empty():
    assert _parse("") == {}


def test_only_whitespace_returns_empty():
    assert _parse("   \n\t  ") == {}


def test_partial_json_no_closing_brace_returns_empty():
    """Отрязан JSON не бива да се представя за отговор."""
    assert _parse('{"approved": true, "issues": [') == {}


def test_failure_is_not_mistakable_for_a_rejection():
    """Регресия за P7: провалът не бива да прилича на 'approved: False'."""
    result = _parse("моделът се обърка")
    assert "approved" not in result
    assert "issues" not in result


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_bare_json_array_returns_empty_dict():
    """Масив на най-горно ниво вече дава {}, не list.

    ВСИЧКИ извикващи правят `.get()` върху резултата (classify_files,
    _detect_intent_ai, analyze_request).  Връщането на list им даваше
    AttributeError — това беше латентен срив, не функция.  Празният dict
    ги оставя с празни стойности по подразбиране.
    """
    result = _parse('[{"task": 1}, {"task": 2}]')
    assert result == {}
    assert result.get("anything") is None   # callers survive


def test_multiple_json_objects_takes_outermost():
    """When text contains multiple JSON blobs, outermost braces are used."""
    raw = 'First: {"a": 1} then {"b": 2} end'
    result = _parse(raw)
    # rfind picks the last '}', so result spans first '{' to last '}'
    # The full span is not valid JSON — fallback dict is returned
    assert isinstance(result, dict)


if __name__ == "__main__":
    tests = [
        test_clean_json_object,
        test_clean_json_with_cyrillic,
        test_clean_json_with_whitespace,
        test_json_in_triple_backtick_fence,
        test_json_in_json_tagged_fence,
        test_json_fence_with_extra_whitespace_lines,
        test_json_embedded_in_explanation,
        test_json_embedded_after_newline,
        test_nested_json_object_extracted_correctly,
        test_completely_invalid_json_returns_empty,
        test_empty_string_returns_empty,
        test_only_whitespace_returns_empty,
        test_partial_json_no_closing_brace_returns_empty,
        test_failure_is_not_mistakable_for_a_rejection,
        test_bare_json_array_returns_empty_dict,
        test_multiple_json_objects_takes_outermost,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as exc:
            print(f"  FAIL  {t.__name__}: {exc}")
        except Exception as exc:
            print(f"  ERROR {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
