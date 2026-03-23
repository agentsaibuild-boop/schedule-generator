"""Unit tests for ChatHandler pure handler methods (no AI, no I/O).

Covers: _handle_export, _offline_response, _handle_select_recent, _handle_save_lesson.
All methods are deterministic and require no mocking of AI or files.

FAILURE означава: export handler, offline fallback, project selection,
или lesson-save handler не работят правилно → потребителят получава грешни
съобщения при критични операции (свали PDF/XML, офлайн режим, избор на
скорошен проект, запис на урок).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.chat_handler import ChatHandler


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _handler() -> ChatHandler:
    """Minimal ChatHandler with no AI, no files."""
    return ChatHandler()


def _handler_with_schedule() -> ChatHandler:
    """ChatHandler with a minimal current_schedule set."""
    h = ChatHandler()
    h.current_schedule = {"tasks": [{"name": "Тест"}]}
    return h


# ===========================================================================
# _handle_export
# ===========================================================================

class TestHandleExport:
    """Tests for _handle_export — the export intent handler."""

    # ------------------------------------------------------------------
    # No schedule
    # ------------------------------------------------------------------

    def test_no_schedule_returns_no_schedule_message(self):
        h = _handler()
        result = h._handle_export("свали pdf")
        assert "Няма генериран график" in result["response"]

    def test_no_schedule_schedule_updated_false(self):
        h = _handler()
        result = h._handle_export("свали xml")
        assert result["schedule_updated"] is False

    def test_no_schedule_intent_is_export(self):
        h = _handler()
        result = h._handle_export("свали нещо")
        assert result["intent"] == "export"

    def test_no_schedule_schedule_data_is_none(self):
        h = _handler()
        result = h._handle_export("pdf")
        assert result["schedule_data"] is None

    # ------------------------------------------------------------------
    # PDF-specific response
    # ------------------------------------------------------------------

    def test_pdf_keyword_triggers_pdf_message(self):
        h = _handler_with_schedule()
        result = h._handle_export("свали pdf")
        assert "PDF" in result["response"]

    def test_pdf_uppercase_keyword(self):
        h = _handler_with_schedule()
        result = h._handle_export("свали PDF")
        assert "PDF" in result["response"]

    def test_печат_keyword_triggers_pdf_message(self):
        h = _handler_with_schedule()
        result = h._handle_export("печат на графика")
        assert "PDF" in result["response"]

    def test_пдф_keyword_triggers_pdf_message(self):
        h = _handler_with_schedule()
        result = h._handle_export("свали пдф")
        assert "PDF" in result["response"]

    def test_pdf_response_contains_export_tab_guidance(self):
        h = _handler_with_schedule()
        result = h._handle_export("pdf")
        assert "Експорт" in result["response"]

    # ------------------------------------------------------------------
    # XML-specific response
    # ------------------------------------------------------------------

    def test_xml_keyword_triggers_xml_message(self):
        h = _handler_with_schedule()
        result = h._handle_export("свали xml")
        assert "XML" in result["response"]

    def test_mspdi_keyword_triggers_xml_message(self):
        h = _handler_with_schedule()
        result = h._handle_export("мспди mspdi файл")
        assert "XML" in result["response"]

    def test_project_keyword_triggers_xml_message(self):
        h = _handler_with_schedule()
        result = h._handle_export("за ms project")
        assert "XML" in result["response"]

    def test_xml_response_mentions_ms_project(self):
        h = _handler_with_schedule()
        result = h._handle_export("xml")
        assert "MS Project" in result["response"]

    # ------------------------------------------------------------------
    # Both PDF and XML
    # ------------------------------------------------------------------

    def test_pdf_and_xml_keywords_both_appear_in_response(self):
        h = _handler_with_schedule()
        result = h._handle_export("свали pdf и xml")
        assert "PDF" in result["response"]
        assert "XML" in result["response"]

    # ------------------------------------------------------------------
    # General export (no format specified)
    # ------------------------------------------------------------------

    def test_no_format_keyword_returns_general_message(self):
        h = _handler_with_schedule()
        result = h._handle_export("свали графика")
        assert "PDF" in result["response"]
        assert "XML" in result["response"]
        assert "JSON" in result["response"]

    def test_general_response_mentions_three_formats(self):
        h = _handler_with_schedule()
        result = h._handle_export("eksprt")
        # Response should mention all three available formats
        response = result["response"]
        assert "PDF" in response
        assert "XML" in response

    # ------------------------------------------------------------------
    # Return shape
    # ------------------------------------------------------------------

    def test_schedule_updated_always_false(self):
        h = _handler_with_schedule()
        for msg in ["pdf", "xml", "свали всичко"]:
            assert h._handle_export(msg)["schedule_updated"] is False

    def test_schedule_data_always_none(self):
        h = _handler_with_schedule()
        for msg in ["pdf", "xml", "свали"]:
            assert h._handle_export(msg)["schedule_data"] is None

    def test_correction_info_always_none(self):
        h = _handler_with_schedule()
        assert h._handle_export("pdf")["correction_info"] is None

    def test_intent_always_export(self):
        h = _handler_with_schedule()
        for msg in ["pdf", "xml", "свали"]:
            assert h._handle_export(msg)["intent"] == "export"

    def test_model_used_is_none_string(self):
        h = _handler_with_schedule()
        assert h._handle_export("pdf")["model_used"] == "none"

    # ------------------------------------------------------------------
    # project_mgr progress saving
    # ------------------------------------------------------------------

    def test_saves_exported_status_when_project_mgr_present(self):
        h = _handler_with_schedule()
        mock_mgr = MagicMock()
        mock_mgr.current_project = {"id": "proj-123"}
        h.project_mgr = mock_mgr
        h._handle_export("pdf")
        mock_mgr.save_progress.assert_called_once_with("proj-123", {"status": "exported"})

    def test_no_save_when_no_project_mgr(self):
        h = _handler_with_schedule()
        h.project_mgr = None
        # Should not raise
        result = h._handle_export("pdf")
        assert result["intent"] == "export"

    def test_no_save_when_no_current_project(self):
        h = _handler_with_schedule()
        mock_mgr = MagicMock()
        mock_mgr.current_project = None
        h.project_mgr = mock_mgr
        h._handle_export("pdf")
        mock_mgr.save_progress.assert_not_called()

    def test_no_save_when_project_has_no_id(self):
        h = _handler_with_schedule()
        mock_mgr = MagicMock()
        mock_mgr.current_project = {"name": "Без ID"}
        h.project_mgr = mock_mgr
        h._handle_export("pdf")
        mock_mgr.save_progress.assert_not_called()


# ===========================================================================
# _offline_response
# ===========================================================================

class TestOfflineResponse:
    """Tests for _offline_response — used when AI is unavailable."""

    def test_returns_dict(self):
        h = _handler()
        result = h._offline_response("здравей")
        assert isinstance(result, dict)

    def test_intent_is_general(self):
        h = _handler()
        result = h._offline_response("каквото и да е")
        assert result["intent"] == "general"

    def test_schedule_updated_false(self):
        h = _handler()
        assert h._offline_response("нещо")["schedule_updated"] is False

    def test_schedule_data_none(self):
        h = _handler()
        assert h._offline_response("нещо")["schedule_data"] is None

    def test_mentions_api_keys_check(self):
        h = _handler()
        result = h._offline_response("помощ")
        assert "API" in result["response"] or ".env" in result["response"]

    def test_response_contains_ai_unavailable_message(self):
        h = _handler()
        result = h._offline_response("генерирай")
        assert "AI" in result["response"]

    def test_no_knowledge_manager_returns_zero_stats(self):
        h = _handler()
        h.knowledge = None
        result = h._offline_response("нещо")
        # Should not raise; response may mention 0 lessons/methodologies
        assert "response" in result

    def test_with_knowledge_manager_shows_lesson_count(self):
        h = _handler()
        mock_km = MagicMock()
        mock_km.get_knowledge_stats.return_value = {"lessons": 42, "methodologies": 5}
        h.knowledge = mock_km
        result = h._offline_response("нещо")
        assert "42" in result["response"]

    def test_with_knowledge_manager_shows_methodology_count(self):
        h = _handler()
        mock_km = MagicMock()
        mock_km.get_knowledge_stats.return_value = {"lessons": 7, "methodologies": 3}
        h.knowledge = mock_km
        result = h._offline_response("нещо")
        assert "3" in result["response"]


# ===========================================================================
# _handle_select_recent
# ===========================================================================

class TestHandleSelectRecent:
    """Tests for _handle_select_recent — picks project by 1-based number."""

    def _projects(self):
        return [
            {"id": "p1", "name": "Плевен", "path": "/a", "exists": True},
            {"id": "p2", "name": "Враца", "path": "/b", "exists": True},
            {"id": "p3", "name": "Стара Загора", "path": "/c", "exists": False},
        ]

    # ------------------------------------------------------------------
    # Valid index
    # ------------------------------------------------------------------

    def test_valid_number_returns_load_project_intent(self):
        h = _handler()
        result = h._handle_select_recent(1, self._projects())
        assert result["intent"] == "select_recent"

    def test_valid_number_loads_correct_project_id(self):
        h = _handler()
        result = h._handle_select_recent(2, self._projects())
        assert result["load_project_id"] == "p2"

    def test_valid_number_response_contains_project_name(self):
        h = _handler()
        result = h._handle_select_recent(1, self._projects())
        assert "Плевен" in result["response"]

    def test_valid_number_sets_load_project_path(self):
        h = _handler()
        result = h._handle_select_recent(1, self._projects())
        assert result["load_project_path"] == "/a"

    def test_valid_number_schedule_updated_false(self):
        h = _handler()
        result = h._handle_select_recent(1, self._projects())
        assert result["schedule_updated"] is False

    # ------------------------------------------------------------------
    # Out-of-range index
    # ------------------------------------------------------------------

    def test_number_too_large_returns_no_project_message(self):
        h = _handler()
        result = h._handle_select_recent(99, self._projects())
        assert "99" in result["response"]

    def test_number_too_large_intent_is_select_recent(self):
        h = _handler()
        result = h._handle_select_recent(99, self._projects())
        assert result["intent"] == "select_recent"

    def test_number_too_large_no_load_project_id(self):
        h = _handler()
        result = h._handle_select_recent(99, self._projects())
        assert "load_project_id" not in result

    def test_empty_list_number_1_returns_no_project_message(self):
        h = _handler()
        result = h._handle_select_recent(1, [])
        assert "1" in result["response"]

    # ------------------------------------------------------------------
    # Non-existent project path
    # ------------------------------------------------------------------

    def test_nonexistent_project_returns_path_error(self):
        h = _handler()
        result = h._handle_select_recent(3, self._projects())
        assert "не съществува" in result["response"]

    def test_nonexistent_project_shows_name(self):
        h = _handler()
        result = h._handle_select_recent(3, self._projects())
        assert "Стара Загора" in result["response"]

    def test_nonexistent_project_shows_path(self):
        h = _handler()
        result = h._handle_select_recent(3, self._projects())
        assert "/c" in result["response"]

    def test_nonexistent_project_no_load_project_id(self):
        h = _handler()
        result = h._handle_select_recent(3, self._projects())
        assert "load_project_id" not in result

    # ------------------------------------------------------------------
    # Return shape invariants
    # ------------------------------------------------------------------

    def test_schedule_data_always_none(self):
        h = _handler()
        for n in [1, 2, 99]:
            assert h._handle_select_recent(n, self._projects())["schedule_data"] is None

    def test_correction_info_always_none(self):
        h = _handler()
        for n in [1, 2, 99]:
            assert h._handle_select_recent(n, self._projects())["correction_info"] is None


# ===========================================================================
# _handle_save_lesson
# ===========================================================================

class TestHandleSaveLesson:
    """Tests for _handle_save_lesson — pure paths only (no AI calls).

    The method has two AI-free paths:
    1. No AI initialised → error response immediately.
    2. Lesson text too short (< 10 chars after trigger stripping) → prompt to
       provide more detail.

    Both paths are fully deterministic and require no mocking of AI or I/O.
    """

    # ------------------------------------------------------------------
    # Guard: no AI initialised
    # ------------------------------------------------------------------

    def test_no_ai_returns_dict(self):
        h = _handler()
        result = h._handle_save_lesson("запиши урок: DN300 е бавен")
        assert isinstance(result, dict)

    def test_no_ai_intent_is_save_lesson(self):
        h = _handler()
        result = h._handle_save_lesson("запиши урок: DN300 е бавен")
        assert result["intent"] == "save_lesson"

    def test_no_ai_schedule_updated_false(self):
        h = _handler()
        assert h._handle_save_lesson("запиши урок: нещо")["schedule_updated"] is False

    def test_no_ai_schedule_data_none(self):
        h = _handler()
        assert h._handle_save_lesson("запиши урок: нещо")["schedule_data"] is None

    def test_no_ai_correction_info_none(self):
        h = _handler()
        assert h._handle_save_lesson("запиши урок: нещо")["correction_info"] is None

    def test_no_ai_model_used_is_none_string(self):
        h = _handler()
        assert h._handle_save_lesson("запиши урок: нещо")["model_used"] == "none"

    def test_no_ai_response_mentions_ai(self):
        h = _handler()
        result = h._handle_save_lesson("запиши урок: нещо")
        assert "AI" in result["response"]

    # ------------------------------------------------------------------
    # Guard: lesson text too short
    # ------------------------------------------------------------------

    def _handler_with_ai(self) -> ChatHandler:
        """ChatHandler with a minimal AI stub (router present but save_lesson not called)."""
        h = ChatHandler()
        mock_router = MagicMock()
        mock_ai = MagicMock()
        mock_ai.router = mock_router
        h.ai = mock_ai
        return h

    def test_short_lesson_after_trigger_returns_prompt(self):
        h = self._handler_with_ai()
        # "abc" is 3 chars — below the 10-char minimum
        result = h._handle_save_lesson("запиши урок: abc")
        assert "подробно" in result["response"] or "формулир" in result["response"]

    def test_empty_lesson_after_trigger_returns_prompt(self):
        h = self._handler_with_ai()
        result = h._handle_save_lesson("запиши урок:")
        assert "подробно" in result["response"] or "формулир" in result["response"]

    def test_short_lesson_intent_is_save_lesson(self):
        h = self._handler_with_ai()
        result = h._handle_save_lesson("запиши урок: кратко")
        assert result["intent"] == "save_lesson"

    def test_short_lesson_schedule_updated_false(self):
        h = self._handler_with_ai()
        assert h._handle_save_lesson("запиши урок: ok")["schedule_updated"] is False

    def test_short_lesson_no_ai_call(self):
        """Short lesson should NOT trigger any AI call."""
        h = self._handler_with_ai()
        h._handle_save_lesson("запиши урок: кратко")
        h.ai.router.save_lesson.assert_not_called()

    # ------------------------------------------------------------------
    # Trigger keyword extraction
    # ------------------------------------------------------------------

    def test_trigger_запиши_урок_extracted(self):
        """Text after 'запиши урок' is passed to AI as the lesson."""
        h = self._handler_with_ai()
        h.ai.router.save_lesson.return_value = {
            "approved": True,
            "formatted_lesson": "DN300 CI е 8 м/ден",
            "reason": "Валидно",
            "model": "deepseek",
        }
        h._handle_save_lesson("запиши урок: DN300 CI е 8 м/ден ефективна производителност")
        call_args = h.ai.router.save_lesson.call_args
        lesson_text = call_args[0][0]
        assert "DN300" in lesson_text

    def test_trigger_научен_урок_extracted(self):
        h = self._handler_with_ai()
        h.ai.router.save_lesson.return_value = {
            "approved": True,
            "formatted_lesson": "Засипването е след 24ч",
            "reason": "Валидно",
            "model": "deepseek",
        }
        h._handle_save_lesson("научен урок: засипването трябва да е след 24 часа")
        call_args = h.ai.router.save_lesson.call_args
        lesson_text = call_args[0][0]
        assert "засипването" in lesson_text.lower()

    def test_trigger_запомни_extracted(self):
        h = self._handler_with_ai()
        h.ai.router.save_lesson.return_value = {
            "approved": False,
            "formatted_lesson": "нещо",
            "reason": "Не е достатъчно конкретен",
            "model": "anthropic",
        }
        h._handle_save_lesson("запомни: КПС стартира СЛЕД завършване на Тласкател")
        call_args = h.ai.router.save_lesson.call_args
        lesson_text = call_args[0][0]
        assert "КПС" in lesson_text

    # ------------------------------------------------------------------
    # Approved lesson response shape
    # ------------------------------------------------------------------

    def test_approved_lesson_response_contains_formatted_lesson(self):
        h = self._handler_with_ai()
        h.ai.router.save_lesson.return_value = {
            "approved": True,
            "formatted_lesson": "DN500 PE: 15 м/ден ефективна",
            "reason": "Валидно — съответства на productivities.json",
            "model": "deepseek",
        }
        result = h._handle_save_lesson("запиши урок: DN500 PE е 15 м/ден ефективна производителност")
        assert "DN500" in result["response"]

    def test_approved_lesson_intent_is_save_lesson(self):
        h = self._handler_with_ai()
        h.ai.router.save_lesson.return_value = {
            "approved": True,
            "formatted_lesson": "Урок А",
            "reason": "ОК",
            "model": "deepseek",
        }
        result = h._handle_save_lesson("запиши урок: Урок А е важен урок за запомняне")
        assert result["intent"] == "save_lesson"

    def test_approved_lesson_calls_knowledge_add_lesson(self):
        h = self._handler_with_ai()
        mock_km = MagicMock()
        h.knowledge = mock_km
        h.ai.router.save_lesson.return_value = {
            "approved": True,
            "formatted_lesson": "Урок Б за записване в базата",
            "reason": "ОК",
            "model": "deepseek",
        }
        h._handle_save_lesson("запиши урок: Урок Б за записване в базата знания")
        mock_km.add_lesson.assert_called_once_with("Урок Б за записване в базата")

    def test_approved_lesson_no_knowledge_no_crash(self):
        h = self._handler_with_ai()
        h.knowledge = None
        h.ai.router.save_lesson.return_value = {
            "approved": True,
            "formatted_lesson": "Урок без knowledge manager",
            "reason": "ОК",
            "model": "deepseek",
        }
        # Should not raise even without knowledge manager
        result = h._handle_save_lesson("запиши урок: Урок без knowledge manager в системата")
        assert result["schedule_updated"] is False

    # ------------------------------------------------------------------
    # Rejected lesson response shape
    # ------------------------------------------------------------------

    def test_rejected_lesson_response_contains_reason(self):
        h = self._handler_with_ai()
        h.ai.router.save_lesson.return_value = {
            "approved": False,
            "formatted_lesson": "Преформулиран урок",
            "reason": "Твърде неясен — посочи конкретен DN и производителност",
            "model": "anthropic",
        }
        result = h._handle_save_lesson("запиши урок: тръбите са по-бавни в горски терен наистина")
        assert "Твърде неясен" in result["response"]

    def test_rejected_lesson_does_not_call_add_lesson(self):
        h = self._handler_with_ai()
        mock_km = MagicMock()
        h.knowledge = mock_km
        h.ai.router.save_lesson.return_value = {
            "approved": False,
            "formatted_lesson": "Нещо",
            "reason": "Неприемливо",
            "model": "anthropic",
        }
        h._handle_save_lesson("запиши урок: тръбите са по-бавни в горски терен в България")
        mock_km.add_lesson.assert_not_called()

    def test_rejected_lesson_intent_is_save_lesson(self):
        h = self._handler_with_ai()
        h.ai.router.save_lesson.return_value = {
            "approved": False,
            "formatted_lesson": "Нещо",
            "reason": "Неприемливо",
            "model": "anthropic",
        }
        result = h._handle_save_lesson("запиши урок: тръбите са по-бавни в горски терен в България")
        assert result["intent"] == "save_lesson"
