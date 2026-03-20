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

def test_completely_invalid_json_returns_fallback():
    """Non-JSON text returns the standard fallback error dict."""
    raw = "Не мога да отговоря на тази заявка."
    result = _parse(raw)
    assert result.get("approved") is False
    assert "issues" in result
    assert len(result["issues"]) > 0


def test_empty_string_returns_fallback():
    """Empty string returns the standard fallback error dict."""
    result = _parse("")
    assert result.get("approved") is False
    assert "issues" in result


def test_only_whitespace_returns_fallback():
    """String of only whitespace returns the standard fallback error dict."""
    result = _parse("   \n\t  ")
    assert result.get("approved") is False


def test_partial_json_no_closing_brace_returns_fallback():
    """Truncated JSON without closing brace returns fallback."""
    raw = '{"approved": true, "issues": ['
    result = _parse(raw)
    # Can't be parsed — fallback
    assert result.get("approved") is False
    assert "issues" in result


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_json_array_parsed_as_list():
    """A bare JSON array is valid JSON — json.loads parses it as a list.

    The function does not restrict return type to dict, so callers should
    guard against list returns when the AI unexpectedly wraps output in [].
    """
    raw = '[{"task": 1}, {"task": 2}]'
    result = _parse(raw)
    # json.loads succeeds → list is returned (not the fallback dict)
    assert isinstance(result, list)
    assert result[0]["task"] == 1


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
        test_completely_invalid_json_returns_fallback,
        test_empty_string_returns_fallback,
        test_only_whitespace_returns_fallback,
        test_partial_json_no_closing_brace_returns_fallback,
        test_json_array_parsed_as_list,
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
