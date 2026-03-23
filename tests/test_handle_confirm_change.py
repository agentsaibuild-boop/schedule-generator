"""Unit tests for ChatHandler._handle_confirm_change.

Tests all pure-logic decision branches without requiring real AI or git:
  1. evolution not initialised  → error message
  2. user cancels               → "Промяната е отказана."
  3. red level + bad admin code → "Невалиден админ код."
  4. yellow level + unknown reply → "Моля, потвърдете с Да" (pending kept)
  5. yellow level + "да"        → apply_changes called, success → commit
  6. yellow level + "ок"        → alias for confirmation → commit
  7. yellow apply fails         → error message, evolution_cleared=True
  8. red level + valid code     → backup + apply + test + commit
  9. red level + apply fails    → rollback triggered
 10. red level + tests fail     → rollback triggered

FAILURE означава: src/chat_handler.py :: _handle_confirm_change е счупена —
самоеволюцията прилага или отказва промени без правилна валидация на
потребителския отговор / ниво на сигурност.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.chat_handler import ChatHandler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _handler_no_evolution() -> ChatHandler:
    """Handler without evolution system (common startup state)."""
    h = ChatHandler()
    h.evolution = None
    return h


def _handler_with_evolution(
    verify_admin: bool = True,
    apply_failed: int = 0,
    test_passed: bool = True,
) -> tuple[ChatHandler, MagicMock]:
    """Handler with a mock SelfEvolution attached."""
    h = ChatHandler()

    evo = MagicMock()
    evo.verify_admin_code.return_value = verify_admin
    evo.apply_changes.return_value = {
        "applied": 1 - apply_failed,
        "failed": apply_failed,
        "errors": ["apply error"] if apply_failed else [],
    }
    evo.create_backup.return_value = {"success": True, "commit_hash": "abc1234567890"}
    evo.rollback.return_value = {"success": True}
    evo.test_changes.return_value = {
        "passed": test_passed,
        "tests_run": 5,
        "tests_passed": 5 if test_passed else 3,
        "errors": [] if test_passed else ["test failure"],
    }
    evo.commit_changes.return_value = {"commit_hash": "def4567890ab"}
    evo.log_change.return_value = None

    h.evolution = evo
    return h, evo


def _yellow_pending(request: str = "Добави функция X") -> dict:
    return {
        "level": "yellow",
        "plan": {"description": "Добавяне на функция X", "affected_files": []},
        "changes": {"changes": []},
        "request": request,
    }


def _red_pending(request: str = "Промени критична логика") -> dict:
    return {
        "level": "red",
        "plan": {"description": "Критична промяна", "affected_files": []},
        "changes": {"changes": []},
        "request": request,
    }


# ---------------------------------------------------------------------------
# 1. Evolution not initialised
# ---------------------------------------------------------------------------

def test_no_evolution_returns_error():
    h = _handler_no_evolution()
    result = h._handle_confirm_change("да", _yellow_pending())
    assert "не е инициализирана" in result["response"]
    assert result["schedule_updated"] is False
    assert result["evolution_cleared"] is True


# ---------------------------------------------------------------------------
# 2-3. Cancellation
# ---------------------------------------------------------------------------

def test_cancel_не():
    h, _ = _handler_with_evolution()
    result = h._handle_confirm_change("не", _yellow_pending())
    assert "отказана" in result["response"]
    assert result["evolution_cleared"] is True


def test_cancel_no():
    h, _ = _handler_with_evolution()
    result = h._handle_confirm_change("no", _yellow_pending())
    assert "отказана" in result["response"]
    assert result["evolution_cleared"] is True


def test_cancel_откажи():
    h, _ = _handler_with_evolution()
    result = h._handle_confirm_change("откажи", _red_pending())
    assert "отказана" in result["response"]
    assert result["evolution_cleared"] is True


# ---------------------------------------------------------------------------
# 4. Red level — invalid admin code
# ---------------------------------------------------------------------------

def test_red_invalid_admin_code_rejected():
    h, evo = _handler_with_evolution(verify_admin=False)
    result = h._handle_confirm_change("wrongcode", _red_pending())
    assert "Невалиден" in result["response"]
    assert result["evolution_cleared"] is True
    evo.apply_changes.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Yellow level — unrecognized reply keeps pending
# ---------------------------------------------------------------------------

def test_yellow_unrecognized_reply_asks_for_confirmation():
    h, evo = _handler_with_evolution()
    result = h._handle_confirm_change("може би", _yellow_pending())
    assert "Моля" in result["response"] or "потвърдете" in result["response"]
    # pending_change must NOT be cleared — no evolution_cleared key
    assert not result.get("evolution_cleared", False)
    evo.apply_changes.assert_not_called()


# ---------------------------------------------------------------------------
# 6-7. Yellow level — confirmed → apply + commit
# ---------------------------------------------------------------------------

def test_yellow_да_apply_success_commits():
    h, evo = _handler_with_evolution()
    result = h._handle_confirm_change("да", _yellow_pending())
    evo.apply_changes.assert_called_once()
    evo.commit_changes.assert_called_once()
    assert result.get("evolution_cleared") is True
    assert result.get("evolution_applied") is True


def test_yellow_ок_apply_success_commits():
    h, evo = _handler_with_evolution()
    result = h._handle_confirm_change("ок", _yellow_pending())
    evo.apply_changes.assert_called_once()
    evo.commit_changes.assert_called_once()
    assert result.get("evolution_applied") is True


def test_yellow_потвърждавам_apply_success_commits():
    h, evo = _handler_with_evolution()
    result = h._handle_confirm_change("потвърждавам", _yellow_pending())
    evo.apply_changes.assert_called_once()
    assert result.get("evolution_applied") is True


# ---------------------------------------------------------------------------
# 8. Yellow level — apply fails → error, no commit
# ---------------------------------------------------------------------------

def test_yellow_apply_fails_returns_error_no_commit():
    h, evo = _handler_with_evolution(apply_failed=1)
    result = h._handle_confirm_change("да", _yellow_pending())
    assert result["schedule_updated"] is False
    assert result.get("evolution_cleared") is True
    assert result.get("evolution_applied") is not True
    evo.commit_changes.assert_not_called()
    # For yellow level (no backup), rollback should NOT be called
    evo.rollback.assert_not_called()


# ---------------------------------------------------------------------------
# 9. Red level — valid code → backup + apply + test + commit
# ---------------------------------------------------------------------------

def test_red_valid_code_full_success():
    h, evo = _handler_with_evolution(verify_admin=True, test_passed=True)
    result = h._handle_confirm_change("secretcode", _red_pending())
    evo.verify_admin_code.assert_called_once_with("secretcode")
    evo.create_backup.assert_called_once()
    evo.apply_changes.assert_called_once()
    evo.test_changes.assert_called_once()
    evo.commit_changes.assert_called_once()
    assert result.get("evolution_applied") is True


# ---------------------------------------------------------------------------
# 10. Red level — apply fails → rollback triggered
# ---------------------------------------------------------------------------

def test_red_apply_fails_triggers_rollback():
    h, evo = _handler_with_evolution(verify_admin=True, apply_failed=1)
    result = h._handle_confirm_change("secretcode", _red_pending())
    evo.rollback.assert_called_once()
    assert result.get("evolution_applied") is not True


# ---------------------------------------------------------------------------
# 11. Red level — apply OK but tests fail → rollback triggered
# ---------------------------------------------------------------------------

def test_red_tests_fail_triggers_rollback():
    h, evo = _handler_with_evolution(verify_admin=True, test_passed=False)
    result = h._handle_confirm_change("secretcode", _red_pending())
    evo.test_changes.assert_called_once()
    evo.rollback.assert_called_once()
    assert result.get("evolution_applied") is not True


# ---------------------------------------------------------------------------
# 12. Response dict always has required keys
# ---------------------------------------------------------------------------

_REQUIRED_KEYS = {"response", "schedule_updated", "schedule_data",
                  "correction_info", "intent", "model_used"}


def test_response_has_all_required_keys_no_evolution():
    h = _handler_no_evolution()
    result = h._handle_confirm_change("да", _yellow_pending())
    assert _REQUIRED_KEYS <= set(result), f"Missing keys: {_REQUIRED_KEYS - set(result)}"


def test_response_has_all_required_keys_cancel():
    h, _ = _handler_with_evolution()
    result = h._handle_confirm_change("не", _yellow_pending())
    assert _REQUIRED_KEYS <= set(result)


def test_response_has_all_required_keys_success():
    h, _ = _handler_with_evolution()
    result = h._handle_confirm_change("да", _yellow_pending())
    assert _REQUIRED_KEYS <= set(result)


if __name__ == "__main__":
    tests = [
        test_no_evolution_returns_error,
        test_cancel_не,
        test_cancel_no,
        test_cancel_откажи,
        test_red_invalid_admin_code_rejected,
        test_yellow_unrecognized_reply_asks_for_confirmation,
        test_yellow_да_apply_success_commits,
        test_yellow_ок_apply_success_commits,
        test_yellow_потвърждавам_apply_success_commits,
        test_yellow_apply_fails_returns_error_no_commit,
        test_red_valid_code_full_success,
        test_red_apply_fails_triggers_rollback,
        test_red_tests_fail_triggers_rollback,
        test_response_has_all_required_keys_no_evolution,
        test_response_has_all_required_keys_cancel,
        test_response_has_all_required_keys_success,
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
            print(f"  ERROR {t.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
