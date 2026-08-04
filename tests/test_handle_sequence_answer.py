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
    """Q2 answer 'ДА' — advances to Q3 (teams), NOT straight to generation.

    До 2026-07 въпросникът беше 2 стъпки и 'ДА' генерираше веднага.  После
    бяха добавени q3_teams и q4_parallel, но тези тестове останаха на стария
    поток.  Сега проверяват реалния: q2 → q3_teams със запазени constraints.
    """

    def _q2_state(self, choice: str = "water_first") -> dict:
        state = _base_state("q2", sections=["А", "Б"])
        state["constraints"] = {"default": choice}
        return state

    def test_da_advances_to_teams_question(self):
        h = _handler()
        mock = _mock_continue(h)
        state = self._q2_state()
        result = h._handle_sequence_answer("ДА", state)
        assert result["pending_sequence"]["step"] == "q3_teams"
        mock.assert_not_called()

    def test_д_short_advances_to_teams_question(self):
        h = _handler()
        state = self._q2_state()
        result = h._handle_sequence_answer("Д", state)
        assert result["pending_sequence"]["step"] == "q3_teams"

    def test_yes_english_advances_to_teams_question(self):
        h = _handler()
        state = self._q2_state()
        result = h._handle_sequence_answer("YES", state)
        assert result["pending_sequence"]["step"] == "q3_teams"

    def test_teams_question_is_asked(self):
        h = _handler()
        state = self._q2_state()
        result = h._handle_sequence_answer("ДА", state)
        assert "екипи" in result["response"].lower()

    def test_da_carries_constraints_forward(self):
        h = _handler()
        state = self._q2_state("sewer_first")
        result = h._handle_sequence_answer("ДА", state)
        assert result["pending_sequence"]["constraints"]["default"] == "sewer_first"

    def test_da_does_not_generate_yet(self):
        """Генерирането чака отговор за екипите — иначе num_teams се губи."""
        h = _handler()
        mock = _mock_continue(h)
        state = self._q2_state()
        result = h._handle_sequence_answer("ДА", state)
        mock.assert_not_called()
        assert result["schedule_updated"] is False


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

    def test_valid_number_advances_to_teams_question(self):
        h = _handler()
        mock = _mock_continue(h)
        state = self._exc_state()
        result = h._handle_sequence_answer("2", state)
        assert result["pending_sequence"]["step"] == "q3_teams"
        mock.assert_not_called()

    def test_multiple_numbers_all_set_as_exceptions(self):
        h = _handler()
        state = self._exc_state("water_first")
        result = h._handle_sequence_answer("1, 3", state)
        constraints = result["pending_sequence"]["constraints"]
        assert constraints.get("Участък 1") == "sewer_first"
        assert constraints.get("Участък 3") == "sewer_first"

    def test_exceptions_default_unchanged(self):
        h = _handler()
        state = self._exc_state("water_first")
        result = h._handle_sequence_answer("2", state)
        constraints = result["pending_sequence"]["constraints"]
        assert constraints["default"] == "water_first"
        assert constraints.get("Участък 2") == "sewer_first"

    def test_sewer_first_exceptions_become_water_first(self):
        h = _handler()
        state = self._exc_state("sewer_first")
        result = h._handle_sequence_answer("1", state)
        constraints = result["pending_sequence"]["constraints"]
        assert constraints.get("Участък 1") == "water_first"

    def test_unlisted_sections_keep_the_default(self):
        h = _handler()
        state = self._exc_state("water_first")
        result = h._handle_sequence_answer("2", state)
        constraints = result["pending_sequence"]["constraints"]
        assert "Участък 1" not in constraints
        assert "Участък 3" not in constraints

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

    def test_exception_label_is_carried_to_the_summary(self):
        """Етикетът се показва при генериране (_generate_with_sequence), не на q3."""
        h = _handler()
        state = self._exc_state("water_first")
        result = h._handle_sequence_answer("2", state)
        assert result["pending_sequence"]["_exc_label"] == "Участък 2"

    def test_multiple_exception_labels_are_joined(self):
        h = _handler()
        state = self._exc_state("water_first")
        result = h._handle_sequence_answer("1, 3", state)
        assert result["pending_sequence"]["_exc_label"] == "Участък 1, Участък 3"


# ===========================================================================
# Q3 — how many teams?
# ===========================================================================

class TestQ3Teams:
    """Q3_teams — брой екипи. Добавен след първоначалните тестове, дотук непокрит."""

    def _q3_state(self, choice: str = "water_first") -> dict:
        state = _base_state("q3_teams", sections=["А", "Б"])
        state["constraints"] = {"default": choice}
        return state

    def test_one_team_generates_immediately(self):
        """При 1 екип въпросът за паралелност е безсмислен — генерира се веднага."""
        h = _handler()
        mock = _mock_continue(h)
        result = h._handle_sequence_answer("1", self._q3_state())
        mock.assert_called_once()
        assert result["schedule_updated"] is True

    def test_one_team_passes_num_teams_one(self):
        h = _handler()
        mock = _mock_continue(h)
        h._handle_sequence_answer("1", self._q3_state())
        assert mock.call_args.kwargs["num_teams"] == 1

    def test_two_teams_asks_parallel_question(self):
        h = _handler()
        mock = _mock_continue(h)
        result = h._handle_sequence_answer("2", self._q3_state())
        assert result["pending_sequence"]["step"] == "q4_parallel"
        assert result["pending_sequence"]["num_teams"] == 2
        mock.assert_not_called()

    def test_team_count_is_clamped_to_at_least_one(self):
        h = _handler()
        mock = _mock_continue(h)
        h._handle_sequence_answer("0", self._q3_state())
        assert mock.call_args.kwargs["num_teams"] == 1

    def test_constraints_survive_the_teams_question(self):
        h = _handler()
        mock = _mock_continue(h)
        h._handle_sequence_answer("1", self._q3_state("sewer_first"))
        _, constraints, *_ = mock.call_args[0]
        assert constraints["default"] == "sewer_first"

    def test_non_numeric_answer_repeats_the_question(self):
        h = _handler()
        mock = _mock_continue(h)
        result = h._handle_sequence_answer("много", self._q3_state())
        assert result["pending_sequence"]["step"] == "q3_teams"
        mock.assert_not_called()
        assert "число" in result["response"].lower()


# ===========================================================================
# Q4 — parallel or sequential?
# ===========================================================================

class TestQ4Parallel:
    """Q4_parallel — паралелно ли работят екипите. Дотук непокрит."""

    def _q4_state(self, num_teams: int = 2) -> dict:
        state = _base_state("q4_parallel", sections=["А", "Б"])
        state["constraints"] = {"default": "water_first"}
        state["num_teams"] = num_teams
        return state

    def test_da_generates_with_parallel_teams(self):
        h = _handler()
        mock = _mock_continue(h)
        result = h._handle_sequence_answer("ДА", self._q4_state(3))
        mock.assert_called_once()
        assert mock.call_args.kwargs["num_teams"] == 3
        assert result["schedule_updated"] is True

    def test_ne_generates_sequentially_as_one_team(self):
        """Последователно = един екип наведнъж, независимо от обявения брой."""
        h = _handler()
        mock = _mock_continue(h)
        h._handle_sequence_answer("НЕ", self._q4_state(3))
        assert mock.call_args.kwargs["num_teams"] == 1

    def test_parallel_summary_mentions_team_count(self):
        h = _handler()
        _mock_continue(h)
        result = h._handle_sequence_answer("ДА", self._q4_state(2))
        assert "2" in result["response"]
        assert "паралелно" in result["response"]

    def test_sequential_summary_says_so(self):
        h = _handler()
        _mock_continue(h)
        result = h._handle_sequence_answer("НЕ", self._q4_state(2))
        assert "последователно" in result["response"]

    def test_invalid_answer_repeats_the_question(self):
        h = _handler()
        mock = _mock_continue(h)
        result = h._handle_sequence_answer("може би", self._q4_state())
        assert result["pending_sequence"]["step"] == "q4_parallel"
        mock.assert_not_called()

    def test_generation_result_has_no_pending_sequence(self):
        """След генериране въпросникът трябва да е приключил."""
        h = _handler()
        _mock_continue(h)
        result = h._handle_sequence_answer("ДА", self._q4_state())
        assert "pending_sequence" not in result


# ===========================================================================
# Целият въпросник — от q1 до генериране
# ===========================================================================

class TestFullQuestionnaireWalk:
    """Проверява, че четирите стъпки се навързват и нищо не се губи по пътя.

    Точно това липсваше: тестовете покриваха стъпките поотделно със стария
    2-стъпков поток и не хванаха, че q2 вече не генерира.
    """

    def _walk(self, answers: list[str], sections: list[str]) -> tuple[dict, MagicMock]:
        h = _handler()
        mock = _mock_continue(h)
        state = _base_state("q1", sections=sections)
        result: dict = {}
        for answer in answers:
            result = h._handle_sequence_answer(answer, state)
            state = result.get("pending_sequence", state)
        return result, mock

    def test_water_first_all_sections_one_team(self):
        result, mock = self._walk(["В", "ДА", "1"], ["Участък 1", "Участък 2"])
        mock.assert_called_once()
        _, constraints, *_ = mock.call_args[0]
        assert constraints == {"default": "water_first"}
        assert mock.call_args.kwargs["num_teams"] == 1
        assert result["schedule_updated"] is True

    def test_sewer_first_with_exception_two_parallel_teams(self):
        result, mock = self._walk(
            ["К", "НЕ", "2", "2", "ДА"], ["Участък 1", "Участък 2", "Участък 3"]
        )
        mock.assert_called_once()
        _, constraints, *_ = mock.call_args[0]
        assert constraints["default"] == "sewer_first"
        assert constraints["Участък 2"] == "water_first"
        assert mock.call_args.kwargs["num_teams"] == 2
        assert "Участък 2" in result["response"]

    def test_exception_label_reaches_the_final_summary(self):
        result, _ = self._walk(
            ["В", "НЕ", "1, 3", "1"], ["Участък 1", "Участък 2", "Участък 3"]
        )
        assert "Участък 1, Участък 3" in result["response"]

    def test_no_sections_skips_straight_to_generation(self):
        result, mock = self._walk(["В"], [])
        mock.assert_called_once()
        assert result["schedule_updated"] is True


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
