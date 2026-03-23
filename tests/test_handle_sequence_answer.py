"""Unit tests for ChatHandler._handle_sequence_answer (previously uncovered).

Covers all four steps: q1, q2, q2_exceptions, and unknown/fallback.
_continue_generation is mocked so no AI calls are made.

FAILURE означава: src/chat_handler.py :: _handle_sequence_answer е счупен —
интерактивният въпросник за последователност В+К не отговаря правилно
на потребителски входове, или не предава правилно constraints към генератора.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.chat_handler import ChatHandler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _handler() -> ChatHandler:
    h = ChatHandler()
    return h


def _base_state(step: str = "q1", sections: list[str] | None = None) -> dict:
    """Minimal pending_sequence state."""
    return {
        "step": step,
        "analysis": {"analysis": json.dumps({
            "project_type": "разпределителна",
            "scope": "водопровод и канализация",
        })},
        "project_context": None,
        "sections": sections if sections is not None else [],
        "constraints": {},
    }


def _mock_continue(h: ChatHandler) -> MagicMock:
    """Patch _continue_generation to return a dummy result."""
    mock = MagicMock(return_value={
        "schedule_updated": True,
        "schedule_data": {"tasks": []},
        "response": "График генериран.",
        "correction_info": None,
        "intent": "generate_schedule",
        "model_used": "deepseek",
    })
    h._continue_generation = mock
    return mock


# ===========================================================================
# Q1 — water or sewer first?
# ===========================================================================

class TestQ1NoSections:
    """Q1 with no named sections — should immediately trigger generation."""

    def test_q1_water_first_triggers_generation(self):
        h = _handler()
        mock = _mock_continue(h)
        state = _base_state("q1", sections=[])
        result = h._handle_sequence_answer("В", state)
        mock.assert_called_once()
        assert result["schedule_updated"] is True

    def test_q1_sewer_first_triggers_generation(self):
        h = _handler()
        mock = _mock_continue(h)
        state = _base_state("q1", sections=[])
        result = h._handle_sequence_answer("К", state)
        mock.assert_called_once()
        _, kwargs_constraints, *_ = mock.call_args[0]
        assert kwargs_constraints["default"] == "sewer_first"

    def test_q1_water_constraints_set_correctly(self):
        h = _handler()
        mock = _mock_continue(h)
        state = _base_state("q1", sections=[])
        h._handle_sequence_answer("Водопровод", state)
        _, constraints, *_ = mock.call_args[0]
        assert constraints["default"] == "water_first"

    def test_q1_invalid_answer_returns_pending(self):
        h = _handler()
        state = _base_state("q1", sections=[])
        result = h._handle_sequence_answer("може би", state)
        assert "pending_sequence" in result
        assert result["pending_sequence"] == state

    def test_q1_invalid_answer_contains_instruction(self):
        h = _handler()
        state = _base_state("q1", sections=[])
        result = h._handle_sequence_answer("нещо", state)
        assert "В" in result["response"] and "К" in result["response"]

    def test_q1_invalid_answer_no_generation(self):
        h = _handler()
        mock = _mock_continue(h)
        state = _base_state("q1", sections=[])
        h._handle_sequence_answer("без отговор", state)
        mock.assert_not_called()


class TestQ1WithSections:
    """Q1 with named sections — should proceed to Q2."""

    def test_q1_water_with_sections_goes_to_q2(self):
        h = _handler()
        state = _base_state("q1", sections=["Участък А", "Участък Б"])
        result = h._handle_sequence_answer("В", state)
        assert "pending_sequence" in result
        assert result["pending_sequence"]["step"] == "q2"

    def test_q1_sewer_with_sections_goes_to_q2(self):
        h = _handler()
        state = _base_state("q1", sections=["Участък А", "Участък Б"])
        result = h._handle_sequence_answer("К", state)
        assert result["pending_sequence"]["step"] == "q2"

    def test_q1_response_contains_all_sections(self):
        h = _handler()
        sections = ["Участък 1", "Участък 2", "Участък 3"]
        state = _base_state("q1", sections=sections)
        result = h._handle_sequence_answer("В", state)
        for section in sections:
            assert section in result["response"]

    def test_q1_response_asks_about_all_sections(self):
        h = _handler()
        state = _base_state("q1", sections=["А", "Б"])
        result = h._handle_sequence_answer("В", state)
        response = result["response"].lower()
        assert "всички" in response

    def test_q1_with_sections_no_immediate_generation(self):
        h = _handler()
        mock = _mock_continue(h)
        state = _base_state("q1", sections=["А", "Б"])
        h._handle_sequence_answer("В", state)
        mock.assert_not_called()

    def test_q1_choice_stored_in_q2_state(self):
        h = _handler()
        state = _base_state("q1", sections=["А"])
        result = h._handle_sequence_answer("К", state)
        assert result["pending_sequence"]["constraints"]["default"] == "sewer_first"


# ===========================================================================
# Q2 — same for all sections?
# ===========================================================================

class TestQ2Yes:
    """Q2 answer 'ДА' — triggers generation."""

    def _q2_state(self, choice: str = "water_first") -> dict:
        state = _base_state("q2", sections=["А", "Б"])
        state["constraints"] = {"default": choice}
        return state

    def test_da_triggers_generation(self):
        h = _handler()
        mock = _mock_continue(h)
        state = self._q2_state()
        result = h._handle_sequence_answer("ДА", state)
        mock.assert_called_once()
        assert result["schedule_updated"] is True

    def test_д_short_triggers_generation(self):
        h = _handler()
        mock = _mock_continue(h)
        state = self._q2_state()
        h._handle_sequence_answer("Д", state)
        mock.assert_called_once()

    def test_yes_english_triggers_generation(self):
        h = _handler()
        mock = _mock_continue(h)
        state = self._q2_state()
        h._handle_sequence_answer("YES", state)
        mock.assert_called_once()

    def test_da_with_sewer_first_passes_correct_constraints(self):
        h = _handler()
        mock = _mock_continue(h)
        state = self._q2_state("sewer_first")
        h._handle_sequence_answer("ДА", state)
        _, constraints, *_ = mock.call_args[0]
        assert constraints["default"] == "sewer_first"


class TestQ2No:
    """Q2 answer 'НЕ' — asks for exceptions."""

    def _q2_state(self, choice: str = "water_first") -> dict:
        state = _base_state("q2", sections=["А", "Б", "В"])
        state["constraints"] = {"default": choice}
        return state

    def test_ne_goes_to_q2_exceptions(self):
        h = _handler()
        state = self._q2_state()
        result = h._handle_sequence_answer("НЕ", state)
        assert result["pending_sequence"]["step"] == "q2_exceptions"

    def test_н_short_goes_to_exceptions(self):
        h = _handler()
        state = self._q2_state()
        result = h._handle_sequence_answer("Н", state)
        assert result["pending_sequence"]["step"] == "q2_exceptions"

    def test_no_english_goes_to_exceptions(self):
        h = _handler()
        state = self._q2_state()
        result = h._handle_sequence_answer("NO", state)
        assert result["pending_sequence"]["step"] == "q2_exceptions"

    def test_ne_response_lists_sections(self):
        h = _handler()
        state = self._q2_state()
        result = h._handle_sequence_answer("НЕ", state)
        for section in ["А", "Б", "В"]:
            assert section in result["response"]

    def test_ne_no_generation(self):
        h = _handler()
        mock = _mock_continue(h)
        state = self._q2_state()
        h._handle_sequence_answer("НЕ", state)
        mock.assert_not_called()

    def test_invalid_answer_returns_pending_with_same_step(self):
        h = _handler()
        state = self._q2_state()
        result = h._handle_sequence_answer("може би", state)
        assert result["pending_sequence"]["step"] == "q2"

    def test_invalid_answer_contains_да_не_hint(self):
        h = _handler()
        state = self._q2_state()
        # "може би" has neither ДА nor НЕ as substring → truly invalid
        result = h._handle_sequence_answer("може би", state)
        assert "ДА" in result["response"] or "НЕ" in result["response"]


# ===========================================================================
# Q2_exceptions — which sections have different order?
# ===========================================================================

class TestQ2Exceptions:
    """Q2_exceptions — parses section numbers from user input."""

    def _exc_state(self, choice: str = "water_first") -> dict:
        state = _base_state("q2_exceptions", sections=["Участък 1", "Участък 2", "Участък 3"])
        state["constraints"] = {"default": choice}
        return state

    def test_valid_number_triggers_generation(self):
        h = _handler()
        mock = _mock_continue(h)
        state = self._exc_state()
        result = h._handle_sequence_answer("2", state)
        mock.assert_called_once()
        assert result["schedule_updated"] is True

    def test_multiple_numbers_all_set_as_exceptions(self):
        h = _handler()
        mock = _mock_continue(h)
        state = self._exc_state("water_first")
        h._handle_sequence_answer("1, 3", state)
        _, constraints, *_ = mock.call_args[0]
        assert constraints.get("Участък 1") == "sewer_first"
        assert constraints.get("Участък 3") == "sewer_first"

    def test_exceptions_default_unchanged(self):
        h = _handler()
        mock = _mock_continue(h)
        state = self._exc_state("water_first")
        h._handle_sequence_answer("2", state)
        _, constraints, *_ = mock.call_args[0]
        assert constraints["default"] == "water_first"
        assert constraints.get("Участък 2") == "sewer_first"

    def test_sewer_first_exceptions_become_water_first(self):
        h = _handler()
        mock = _mock_continue(h)
        state = self._exc_state("sewer_first")
        h._handle_sequence_answer("1", state)
        _, constraints, *_ = mock.call_args[0]
        assert constraints.get("Участък 1") == "water_first"

    def test_out_of_range_number_ignored(self):
        h = _handler()
        mock = _mock_continue(h)
        state = self._exc_state()
        # Only number 99 which is out of range → treated as "no nums"
        result = h._handle_sequence_answer("99", state)
        mock.assert_not_called()
        assert "pending_sequence" in result

    def test_no_numbers_returns_pending(self):
        h = _handler()
        mock = _mock_continue(h)
        state = self._exc_state()
        result = h._handle_sequence_answer("без номера", state)
        mock.assert_not_called()
        assert "pending_sequence" in result

    def test_no_numbers_response_contains_instruction(self):
        h = _handler()
        state = self._exc_state()
        result = h._handle_sequence_answer("абв", state)
        assert "номера" in result["response"].lower()

    def test_response_contains_exception_labels(self):
        h = _handler()
        mock = _mock_continue(h)
        state = self._exc_state("water_first")
        result = h._handle_sequence_answer("2", state)
        assert "Участък 2" in result["response"]


# ===========================================================================
# Unknown / fallback step
# ===========================================================================

class TestUnknownStep:
    """Unknown step value — returns generic error and no pending_sequence."""

    def test_unknown_step_no_pending_sequence(self):
        h = _handler()
        state = _base_state("q99")
        result = h._handle_sequence_answer("нещо", state)
        assert "pending_sequence" not in result

    def test_unknown_step_contains_error_message(self):
        h = _handler()
        state = _base_state("bogus_step")
        result = h._handle_sequence_answer("тест", state)
        assert result["response"]  # non-empty error message

    def test_unknown_step_no_generation(self):
        h = _handler()
        mock = _mock_continue(h)
        state = _base_state("wrong")
        h._handle_sequence_answer("генерирай", state)
        mock.assert_not_called()
