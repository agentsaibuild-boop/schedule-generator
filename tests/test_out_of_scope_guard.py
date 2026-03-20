"""Unit tests for C2 fix — out_of_scope project type guard in _handle_generate_schedule.

Bug: When the AI classifier returned project_type="out_of_scope" (e.g. HDD/trenchless
projects, industrial pipelines), _handle_generate_schedule still proceeded to call
generate_schedule(), which produced a physically-impossible schedule using the wrong
methodology.

Fix: After _extract_project_type(), if project_type == "out_of_scope", return an
error response immediately without calling generate_schedule().

FAILURE означава: src/chat_handler.py :: _handle_generate_schedule НЕ блокира
out_of_scope проекти → AI генерира неправилен график вместо грешка.
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

def _make_out_of_scope_analysis(specifics: str = "") -> dict:
    """Simulate an analyze_documents() result with project_type=out_of_scope."""
    return {
        "status": "ok",
        "model": "deepseek",
        "analysis": json.dumps({
            "project_type": "out_of_scope",
            "scope": "HDD безизкопно полагане",
            "quantities": {"DN90": "200м"},
            "specifics": specifics,
            "conflicts": [],
        }),
    }


def _converted_files() -> list[dict]:
    return [{"name": "КСС.xlsx.json", "converted": '{"items": []}', "type": "json"}]


def _handler() -> ChatHandler:
    """ChatHandler with FileManager and AIProcessor stubs."""
    handler = ChatHandler()
    # _progress is normally set inside process_message; set a no-op for direct calls
    handler._progress = lambda pct, txt: None

    mock_files = MagicMock()
    mock_files.base_path = "/fake/project"
    mock_files.get_converted_files.return_value = _converted_files()
    mock_files.get_all_text.return_value = "КСС HDD проект"
    mock_files.classify_files.return_value = {
        "required": ["КСС.xlsx.json"],
        "useful": [],
        "situation": [],
        "situation_paths": [],
        "unknown": [],
        "can_proceed": True,
        "ai_used": False,
    }
    handler.files = mock_files

    mock_ai = MagicMock()
    mock_ai.router = MagicMock()          # truthy — passes the "AI не е инициализиран" guard
    mock_ai._validate_json_inputs.return_value = None  # no exception = valid
    handler.ai = mock_ai

    return handler


def _call(handler: ChatHandler, project_context: dict | None = None) -> dict:
    return handler._handle_generate_schedule(
        message="Генерирай график",
        project_loaded=True,
        conversion_done=True,
        project_context=project_context,
    )


# ---------------------------------------------------------------------------
# Core guard tests
# ---------------------------------------------------------------------------

def test_out_of_scope_returns_error_without_generating():
    """When project_type=out_of_scope, generate_schedule must NOT be called."""
    handler = _handler()
    handler.ai.analyze_documents.return_value = _make_out_of_scope_analysis()

    result = _call(handler)

    handler.ai.generate_schedule.assert_not_called()
    assert result["schedule_updated"] is False
    assert result["schedule_data"] is None


def test_out_of_scope_response_contains_error_marker():
    """Response for out_of_scope must contain the stop emoji and explanation."""
    handler = _handler()
    handler.ai.analyze_documents.return_value = _make_out_of_scope_analysis()

    result = _call(handler)

    assert "⛔" in result["response"]
    assert "извън обхвата" in result["response"]


def test_out_of_scope_includes_specifics_reason():
    """When AI provides a reason in 'specifics', it must appear in the response."""
    reason = "Проектът използва HDD технология — безизкопно полагане"
    handler = _handler()
    handler.ai.analyze_documents.return_value = _make_out_of_scope_analysis(specifics=reason)

    result = _call(handler)

    assert reason in result["response"]


def test_out_of_scope_without_specifics_still_blocked():
    """Even without a specifics reason, out_of_scope must still be blocked."""
    handler = _handler()
    handler.ai.analyze_documents.return_value = _make_out_of_scope_analysis(specifics="")

    result = _call(handler)

    handler.ai.generate_schedule.assert_not_called()
    assert "⛔" in result["response"]


def test_out_of_scope_intent_is_generate_schedule():
    """The response intent must remain 'generate_schedule' for UI routing."""
    handler = _handler()
    handler.ai.analyze_documents.return_value = _make_out_of_scope_analysis()

    result = _call(handler)

    assert result["intent"] == "generate_schedule"
    assert result["correction_info"] is None


def test_out_of_scope_as_dict_analysis():
    """out_of_scope works when analysis['analysis'] is already a dict (not JSON string)."""
    reason = "Промишлен обект — не е ВиК инфраструктура"
    handler = _handler()
    handler.ai.analyze_documents.return_value = {
        "status": "ok",
        "model": "deepseek",
        "analysis": {
            "project_type": "out_of_scope",
            "scope": "промишлен газопровод",
            "quantities": {},
            "specifics": reason,
            "conflicts": [],
        },
    }

    result = _call(handler)

    handler.ai.generate_schedule.assert_not_called()
    assert reason in result["response"]


def test_valid_project_type_is_not_blocked():
    """Normal project_type (distribution_network) must proceed to generate_schedule."""
    handler = _handler()
    handler.ai.analyze_documents.return_value = {
        "status": "ok",
        "model": "deepseek",
        "analysis": json.dumps({
            "project_type": "distribution_network",
            "scope": "водопровод питейна",
            "quantities": {"DN90": "500м"},
            "conflicts": [],
            "locations": [],
        }),
    }
    handler.ai.extract_situation_locations.return_value = []
    handler.ai.generate_schedule.return_value = {
        "status": "approved",
        "schedule": [],
        "cycles": 1,
        "total_cost": 0.001,
        "history": [],
        "hallucination_warnings": [],
        "gen_model": "deepseek",
    }

    result = _call(handler)

    handler.ai.generate_schedule.assert_called_once()
    assert result["intent"] == "generate_schedule"


def test_empty_project_type_is_not_blocked():
    """Empty project_type (unknown) must NOT trigger the out_of_scope guard."""
    handler = _handler()
    handler.ai.analyze_documents.return_value = {
        "status": "ok",
        "model": "deepseek",
        "analysis": json.dumps({
            "project_type": "",
            "scope": "водопровод",
            "quantities": {},
            "conflicts": [],
            "locations": [],
        }),
    }
    handler.ai.extract_situation_locations.return_value = []
    handler.ai.generate_schedule.return_value = {
        "status": "approved",
        "schedule": [],
        "cycles": 1,
        "total_cost": 0.0,
        "history": [],
        "hallucination_warnings": [],
        "gen_model": "deepseek",
    }

    result = _call(handler)

    handler.ai.generate_schedule.assert_called_once()


if __name__ == "__main__":
    tests = [
        test_out_of_scope_returns_error_without_generating,
        test_out_of_scope_response_contains_error_marker,
        test_out_of_scope_includes_specifics_reason,
        test_out_of_scope_without_specifics_still_blocked,
        test_out_of_scope_intent_is_generate_schedule,
        test_out_of_scope_as_dict_analysis,
        test_valid_project_type_is_not_blocked,
        test_empty_project_type_is_not_blocked,
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
            import traceback
            print(f"  ERROR {t.__name__}: {exc}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
