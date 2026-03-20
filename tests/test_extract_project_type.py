"""Unit tests for ChatHandler._extract_project_type (C1 fix).

This is the critical function that routes every schedule generation to the
correct methodology (distribution_network, supply_pipeline, engineering_projects,
single_section, out_of_scope).  A bug here causes wrong methodology → wrong
schedule durations for every project.

FAILURE означава: src/chat_handler.py :: _extract_project_type е счупена —
генераторът ще използва грешна методология при всяко генериране.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.chat_handler import ChatHandler

_extract = ChatHandler._extract_project_type


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _analysis_from_str(project_type: str) -> dict:
    """Wrap project_type in the format returned by analyze_documents()."""
    return {"analysis": json.dumps({"project_type": project_type, "scope": "test"})}


def _analysis_from_dict(project_type: str) -> dict:
    """analysis value is already a parsed dict (alternative code path)."""
    return {"analysis": {"project_type": project_type, "scope": "test"}}


# ---------------------------------------------------------------------------
# Tests — primary source: analysis["analysis"] JSON string
# ---------------------------------------------------------------------------

def test_extracts_type_from_json_string():
    """Happy path: analysis value is a JSON string containing project_type."""
    result = _extract(_analysis_from_str("distribution_network"))
    assert result == "distribution_network"


def test_extracts_type_from_parsed_dict():
    """analysis value is already a dict (no JSON parsing needed)."""
    result = _extract(_analysis_from_dict("supply_pipeline"))
    assert result == "supply_pipeline"


def test_all_valid_project_types_are_preserved():
    """Each of the 5 known project types round-trips correctly."""
    valid_types = [
        "distribution_network",
        "supply_pipeline",
        "engineering_projects",
        "single_section",
        "out_of_scope",
    ]
    for pt in valid_types:
        assert _extract(_analysis_from_str(pt)) == pt, f"Failed for type: {pt}"
        assert _extract(_analysis_from_dict(pt)) == pt, f"Failed for dict type: {pt}"


# ---------------------------------------------------------------------------
# Tests — fallback to project_context
# ---------------------------------------------------------------------------

def test_fallback_when_analysis_has_no_project_type():
    """analysis dict has no project_type → falls back to project_context."""
    analysis = {"analysis": json.dumps({"scope": "нещо", "quantities": {}})}
    context = {"type": "single_section"}
    result = _extract(analysis, project_context=context)
    assert result == "single_section"


def test_fallback_when_analysis_value_is_empty_string():
    """analysis["analysis"] is "" (no AI response yet) → falls back to context."""
    analysis = {"analysis": ""}
    context = {"type": "engineering_projects"}
    result = _extract(analysis, project_context=context)
    assert result == "engineering_projects"


def test_fallback_when_analysis_is_invalid_json():
    """Corrupt AI response → JSON parse fails silently → falls back to context."""
    analysis = {"analysis": "не е JSON {broken}"}
    context = {"type": "distribution_network"}
    result = _extract(analysis, project_context=context)
    assert result == "distribution_network"


def test_analysis_type_takes_priority_over_context():
    """When both sources have a type, the AI analysis wins."""
    analysis = _analysis_from_str("supply_pipeline")
    context = {"type": "single_section"}  # manual override, lower priority
    result = _extract(analysis, project_context=context)
    assert result == "supply_pipeline"


# ---------------------------------------------------------------------------
# Tests — empty / missing inputs
# ---------------------------------------------------------------------------

def test_returns_empty_string_when_both_sources_missing():
    """No analysis, no context → returns empty string (not None, not exception)."""
    result = _extract({})
    assert result == ""


def test_returns_empty_string_with_empty_context():
    """Empty project_context dict → returns empty string."""
    result = _extract({}, project_context={})
    assert result == ""


def test_no_error_when_project_context_is_none():
    """project_context=None is the default — must not raise."""
    result = _extract({"analysis": ""}, project_context=None)
    assert result == ""


def test_empty_project_type_string_triggers_fallback():
    """If AI returns project_type="" (empty), fall through to context."""
    analysis = {"analysis": json.dumps({"project_type": ""})}
    context = {"type": "distribution_network"}
    result = _extract(analysis, project_context=context)
    assert result == "distribution_network"


if __name__ == "__main__":
    tests = [
        test_extracts_type_from_json_string,
        test_extracts_type_from_parsed_dict,
        test_all_valid_project_types_are_preserved,
        test_fallback_when_analysis_has_no_project_type,
        test_fallback_when_analysis_value_is_empty_string,
        test_fallback_when_analysis_is_invalid_json,
        test_analysis_type_takes_priority_over_context,
        test_returns_empty_string_when_both_sources_missing,
        test_returns_empty_string_with_empty_context,
        test_no_error_when_project_context_is_none,
        test_empty_project_type_string_triggers_fallback,
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
