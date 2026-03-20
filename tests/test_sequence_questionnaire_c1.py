"""Unit tests for C1 fix — project_context propagation through sequence questionnaire.

Bug: _start_sequence_questionnaire did not store project_context in the pending state,
so _continue_generation could not fall back to the manually-selected project type.
This caused project_type="" when the AI analysis lacked it (e.g. short doc, bad JSON),
routing the schedule generator to the wrong methodology.

FAILURE означава: при двойна В+К мрежа, ако потребителят е избрал project_type ръчно,
типът се губи след въпросника → AI генерира с грешна методика.
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.chat_handler import ChatHandler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dual_network_analysis() -> dict:
    """Analysis that triggers the sequence questionnaire (water + sewer)."""
    return {
        "analysis": json.dumps({
            "project_type": "",          # empty — will need project_context fallback
            "scope": "водопровод и канализация",
            "quantities": {"DN90": "500м"},
        })
    }


def _water_only_analysis() -> dict:
    """Analysis without sewer → questionnaire should NOT trigger."""
    return {
        "analysis": json.dumps({
            "project_type": "single_section",
            "scope": "водопровод питейна мрежа",
            "quantities": {"DN90": "300м"},
        })
    }


# ---------------------------------------------------------------------------
# Tests — _start_sequence_questionnaire stores project_context
# ---------------------------------------------------------------------------

def test_questionnaire_state_contains_project_context():
    """State dict returned by _start_sequence_questionnaire must include project_context."""
    handler = ChatHandler()
    analysis = _dual_network_analysis()
    context = {"type": "distribution_network", "name": "Тест"}

    state = handler._start_sequence_questionnaire(analysis, project_context=context)

    assert state is not None, "Expected questionnaire to trigger for dual network"
    assert "project_context" in state, "project_context must be stored in questionnaire state"
    assert state["project_context"] == context


def test_questionnaire_state_project_context_defaults_to_none():
    """When project_context is not provided, state stores None (not missing key)."""
    handler = ChatHandler()
    analysis = _dual_network_analysis()

    state = handler._start_sequence_questionnaire(analysis)

    assert state is not None
    assert "project_context" in state
    assert state["project_context"] is None


def test_questionnaire_returns_none_for_water_only():
    """No questionnaire for water-only projects — project_context fix must not break this."""
    handler = ChatHandler()
    analysis = _water_only_analysis()
    context = {"type": "single_section"}

    state = handler._start_sequence_questionnaire(analysis, project_context=context)

    assert state is None, "Questionnaire should not trigger for water-only projects"


# ---------------------------------------------------------------------------
# Tests — _continue_generation signature accepts project_context
# ---------------------------------------------------------------------------

def test_continue_generation_accepts_project_context_param():
    """_continue_generation must accept project_context as keyword argument."""
    sig = inspect.signature(ChatHandler._continue_generation)
    assert "project_context" in sig.parameters, (
        "_continue_generation must have project_context parameter (C1 fix)"
    )


def test_continue_generation_project_context_has_default_none():
    """project_context parameter must default to None (backward compatible)."""
    sig = inspect.signature(ChatHandler._continue_generation)
    param = sig.parameters["project_context"]
    assert param.default is None, "project_context must default to None"


# ---------------------------------------------------------------------------
# Tests — _start_sequence_questionnaire signature
# ---------------------------------------------------------------------------

def test_start_questionnaire_signature_has_project_context():
    """_start_sequence_questionnaire must declare project_context parameter."""
    sig = inspect.signature(ChatHandler._start_sequence_questionnaire)
    assert "project_context" in sig.parameters


def test_start_questionnaire_project_context_defaults_to_none():
    """project_context must default to None for backward compatibility."""
    sig = inspect.signature(ChatHandler._start_sequence_questionnaire)
    param = sig.parameters["project_context"]
    assert param.default is None


if __name__ == "__main__":
    tests = [
        test_questionnaire_state_contains_project_context,
        test_questionnaire_state_project_context_defaults_to_none,
        test_questionnaire_returns_none_for_water_only,
        test_continue_generation_accepts_project_context_param,
        test_continue_generation_project_context_has_default_none,
        test_start_questionnaire_signature_has_project_context,
        test_start_questionnaire_project_context_defaults_to_none,
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
