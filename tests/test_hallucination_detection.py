"""Unit tests for _validate_task_locations — anti-hallucination detection.

Covers: no hallucinations, whitelist match, corpus match, real hallucination,
skip-word filtering, partial whitelist substring, and empty inputs.

FAILURE означава: src/ai_processor.py :: _validate_task_locations е счупена —
генераторът ще пуска халюцинирани имена на улици/места без предупреждение.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai_processor import AIProcessor

# Convenient alias — static method, no instance needed
_validate = AIProcessor._validate_task_locations


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _task(id_: str, name: str) -> dict:
    return {"id": id_, "name": name}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_no_warnings_when_location_in_whitelist():
    """Task mentions 'Витоша' which is in the whitelist → no warning."""
    tasks = [_task("1", "Изкопни работи — ул. Витоша")]
    # 'Изкопни' is a skip word; 'Витоша' is in whitelist → zero warnings
    warnings = _validate(tasks, locations_whitelist=["Витоша"], all_text="")
    assert warnings == [], f"Expected no warnings, got: {warnings}"


def test_no_warnings_when_location_in_corpus():
    """Task mentions 'Илиенци' found in raw document text → no warning."""
    tasks = [_task("2", "Полагане — кв. Илиенци")]
    warnings = _validate(tasks, locations_whitelist=[], all_text="квартал Илиенци, общ. Враца")
    assert warnings == [], f"Expected no warnings, got: {warnings}"


def test_warning_for_hallucinated_location():
    """Task mentions 'Младост' absent from both whitelist and corpus → warning issued."""
    tasks = [_task("3", "Монтаж — ул. Младост")]
    # Corpus and whitelist contain only 'Витоша' — 'Младост' is hallucinated
    warnings = _validate(tasks, locations_whitelist=["Витоша"], all_text="обект в гр. Плевен")
    assert len(warnings) == 1
    assert "Младост" in warnings[0]
    assert "3" in warnings[0]


def test_skip_words_produce_no_warning():
    """Construction-domain words like 'Монтаж', 'Изкоп' are in _SKIP_WORDS → never flagged."""
    tasks = [_task("4", "Монтаж Изкоп Засипване Уплътняване")]
    warnings = _validate(tasks, locations_whitelist=[], all_text="")
    assert warnings == [], f"Skip words must not trigger warnings: {warnings}"


def test_multiple_tasks_only_hallucinated_ones_flagged():
    """Mix of clean and hallucinated tasks — only the hallucinated one is flagged."""
    tasks = [
        _task("5", "Изкоп — ул. Плевен"),   # 'Плевен' in corpus
        _task("6", "Полагане — ул. Химера"),  # 'Химера' invented
    ]
    corpus = "обект в гр. Плевен, Плевенска община"
    warnings = _validate(tasks, locations_whitelist=[], all_text=corpus)
    assert len(warnings) == 1
    assert "Химера" in warnings[0]
    assert "6" in warnings[0]


def test_whitelist_partial_substring_match():
    """Token 'Смолян' should match whitelist entry 'гр. Смолян' (substring)."""
    tasks = [_task("7", "Дезинфекция — Смолян")]
    warnings = _validate(tasks, locations_whitelist=["гр. Смолян"], all_text="")
    assert warnings == [], f"Substring match in whitelist should suppress warning: {warnings}"


def test_empty_task_list_returns_no_warnings():
    """Empty task list must not raise and must return empty list."""
    warnings = _validate([], locations_whitelist=[], all_text="")
    assert warnings == []


def test_task_with_no_name_is_skipped():
    """Task without a 'name' key must be silently skipped."""
    tasks = [{"id": "8"}]
    warnings = _validate(tasks, locations_whitelist=[], all_text="")
    assert warnings == []


def test_short_tokens_below_four_chars_ignored():
    """Regex requires ≥4-char tokens. Short capitalised words like 'ДН' are ignored."""
    tasks = [_task("9", "ДН90 РЕ DN90")]
    warnings = _validate(tasks, locations_whitelist=[], all_text="")
    assert warnings == [], f"Short tokens must not be flagged: {warnings}"


if __name__ == "__main__":
    tests = [
        test_no_warnings_when_location_in_whitelist,
        test_no_warnings_when_location_in_corpus,
        test_warning_for_hallucinated_location,
        test_skip_words_produce_no_warning,
        test_multiple_tasks_only_hallucinated_ones_flagged,
        test_whitelist_partial_substring_match,
        test_empty_task_list_returns_no_warnings,
        test_task_with_no_name_is_skipped,
        test_short_tokens_below_four_chars_ignored,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as exc:
            print(f"  FAIL  {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
