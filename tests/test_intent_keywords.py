"""Unit tests for ChatHandler._detect_intent_keywords and _extract_sections_from_analysis.

Both are pure, no-API functions used on the critical offline fallback path.

FAILURE означава: при липса на AI (офлайн режим), intent detection не работи
правилно → ChatHandler рутира съобщенията към грешния handler → потребителят
не може да генерира/зарежда/експортира без API ключ.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.chat_handler import ChatHandler


# ---------------------------------------------------------------------------
# Fixture — minimal ChatHandler (no AI, no file manager)
# ---------------------------------------------------------------------------

def _handler() -> ChatHandler:
    return ChatHandler()


# ===========================================================================
# _detect_intent_keywords
# ===========================================================================

class TestDetectIntentKeywords:
    """Tests for the keyword-based fallback intent detector."""

    def test_generate_schedule_primary_keyword(self):
        h = _handler()
        assert h._detect_intent_keywords("генерирай нов график") == "generate_schedule"

    def test_generate_schedule_secondary_keyword(self):
        h = _handler()
        assert h._detect_intent_keywords("направи gantt за проекта") == "generate_schedule"

    def test_generate_schedule_bg_only(self):
        h = _handler()
        assert h._detect_intent_keywords("искам нов график за водопровода") == "generate_schedule"

    def test_load_project_phrase_priority(self):
        """LOAD_PROJECT_PHRASES should match before keyword scoring."""
        h = _handler()
        assert h._detect_intent_keywords("зареди проект Плевен моля") == "load_project"

    def test_load_project_open_phrase(self):
        h = _handler()
        assert h._detect_intent_keywords("отвори проект от папката") == "load_project"

    def test_load_project_close_phrase(self):
        h = _handler()
        assert h._detect_intent_keywords("затвори проект") == "load_project"

    def test_load_project_keyword_zatvoriy(self):
        h = _handler()
        assert h._detect_intent_keywords("затвори текущия") == "load_project"

    def test_load_project_windows_path(self):
        """Windows absolute paths should resolve to load_project."""
        h = _handler()
        assert h._detect_intent_keywords(r"C:\Users\ivan\project") == "load_project"

    def test_load_project_unix_path(self):
        h = _handler()
        assert h._detect_intent_keywords("/home/ivan/project") == "load_project"

    def test_export_pdf(self):
        h = _handler()
        assert h._detect_intent_keywords("свали pdf") == "export"

    def test_export_xml(self):
        h = _handler()
        assert h._detect_intent_keywords("експорт xml") == "export"

    def test_export_mspdi(self):
        h = _handler()
        assert h._detect_intent_keywords("изтегли mspdi файл") == "export"

    def test_modify_schedule_promeni(self):
        h = _handler()
        assert h._detect_intent_keywords("промени дата на Клон 1") == "modify_schedule"

    def test_modify_schedule_korektsiya(self):
        h = _handler()
        assert h._detect_intent_keywords("корекция на графика") == "modify_schedule"

    def test_modify_schedule_dobavi(self):
        h = _handler()
        assert h._detect_intent_keywords("добави нова дейност") == "modify_schedule"

    def test_modify_schedule_mahni(self):
        h = _handler()
        assert h._detect_intent_keywords("махни последната задача") == "modify_schedule"

    def test_ask_question_kakvo(self):
        h = _handler()
        assert h._detect_intent_keywords("какво е методиката?") == "ask_question"

    def test_ask_question_kak(self):
        h = _handler()
        assert h._detect_intent_keywords("как работи дезинфекцията?") == "ask_question"

    def test_ask_question_pokaji(self):
        h = _handler()
        assert h._detect_intent_keywords("покажи уроците") == "ask_question"

    def test_save_lesson(self):
        h = _handler()
        assert h._detect_intent_keywords("запиши урок: CI тръби са бавни") == "save_lesson"

    def test_save_lesson_nauchenurок(self):
        h = _handler()
        assert h._detect_intent_keywords("научен урок — DN300 CI = 5м/ден") == "save_lesson"

    def test_evolve_nova_funksiya(self):
        h = _handler()
        assert h._detect_intent_keywords("добави функционалност за BIM") == "evolve"

    def test_evolve_nova_vazmojnost(self):
        h = _handler()
        assert h._detect_intent_keywords("нова възможност в приложението") == "evolve"

    def test_evolve_evolution_keyword(self):
        h = _handler()
        assert h._detect_intent_keywords("evolution на кода") == "evolve"

    def test_general_default_empty(self):
        h = _handler()
        assert h._detect_intent_keywords("") == "general"

    def test_general_unrelated(self):
        h = _handler()
        assert h._detect_intent_keywords("здравейте") == "general"

    def test_general_only_punctuation(self):
        h = _handler()
        assert h._detect_intent_keywords("!!!") == "general"

    def test_case_insensitive_keyword(self):
        """Keywords must match regardless of case in user message."""
        h = _handler()
        assert h._detect_intent_keywords("ГЕНЕРИРАЙ ГРАФИК") == "generate_schedule"

    def test_case_insensitive_phrase(self):
        h = _handler()
        assert h._detect_intent_keywords("Зареди Проект тест") == "load_project"

    def test_highest_score_wins(self):
        """When multiple intent keywords appear, highest score wins."""
        h = _handler()
        # "промени" → modify, "график" → generate — tie broken by iteration order
        # but this tests that scoring logic runs without error
        result = h._detect_intent_keywords("промени графика, корекция и обнови")
        assert result == "modify_schedule"  # 3 modify keywords vs 1 generate keyword

    def test_load_project_folder_phrase(self):
        h = _handler()
        assert h._detect_intent_keywords("зареди папка с проект") == "load_project"


# ===========================================================================
# _extract_sections_from_analysis (static method)
# ===========================================================================

class TestExtractSectionsFromAnalysis:
    """Tests for the static section-name extractor."""

    def test_dict_quantities(self):
        analysis = {
            "analysis": json.dumps({
                "quantities": {
                    "Клон 1": {"length_m": 350},
                    "Клон 2": {"length_m": 200},
                    "total": {"length_m": 550},  # should be excluded
                }
            })
        }
        result = ChatHandler._extract_sections_from_analysis(analysis)
        assert "Клон 1" in result
        assert "Клон 2" in result
        assert "total" not in result

    def test_list_quantities_with_name(self):
        analysis = {
            "analysis": json.dumps({
                "quantities": [
                    {"name": "ул. Витоша", "length_m": 100},
                    {"name": "ул. Раковски", "length_m": 150},
                ]
            })
        }
        result = ChatHandler._extract_sections_from_analysis(analysis)
        assert "ул. Витоша" in result
        assert "ул. Раковски" in result

    def test_list_quantities_with_section_key(self):
        analysis = {
            "analysis": json.dumps({
                "quantities": [
                    {"section": "Участък А"},
                    {"section": "Участък Б"},
                ]
            })
        }
        result = ChatHandler._extract_sections_from_analysis(analysis)
        assert "Участък А" in result
        assert "Участък Б" in result

    def test_list_quantities_with_branch_key(self):
        analysis = {
            "analysis": json.dumps({
                "quantities": [
                    {"branch": "Клон А"},
                ]
            })
        }
        result = ChatHandler._extract_sections_from_analysis(analysis)
        assert "Клон А" in result

    def test_dict_analysis_already_parsed(self):
        """analysis field can be a dict directly (not a JSON string)."""
        analysis = {
            "analysis": {
                "quantities": {"Клон X": {"length_m": 80}}
            }
        }
        result = ChatHandler._extract_sections_from_analysis(analysis)
        assert "Клон X" in result

    def test_empty_quantities_dict(self):
        analysis = {"analysis": json.dumps({"quantities": {}})}
        assert ChatHandler._extract_sections_from_analysis(analysis) == []

    def test_empty_quantities_list(self):
        analysis = {"analysis": json.dumps({"quantities": []})}
        assert ChatHandler._extract_sections_from_analysis(analysis) == []

    def test_no_quantities_key(self):
        analysis = {"analysis": json.dumps({"scope": "test"})}
        assert ChatHandler._extract_sections_from_analysis(analysis) == []

    def test_missing_analysis_key(self):
        assert ChatHandler._extract_sections_from_analysis({}) == []

    def test_invalid_json_analysis(self):
        analysis = {"analysis": "NOT JSON {{{{"}
        assert ChatHandler._extract_sections_from_analysis(analysis) == []

    def test_analysis_is_none(self):
        analysis = {"analysis": None}
        assert ChatHandler._extract_sections_from_analysis(analysis) == []

    def test_total_key_excluded_from_dict(self):
        """The 'total' key must never appear in the returned sections."""
        analysis = {
            "analysis": json.dumps({
                "quantities": {"total": {"length_m": 999}}
            })
        }
        assert ChatHandler._extract_sections_from_analysis(analysis) == []

    def test_empty_name_excluded_from_list(self):
        analysis = {
            "analysis": json.dumps({
                "quantities": [{"name": ""}, {"name": "Клон 5"}]
            })
        }
        result = ChatHandler._extract_sections_from_analysis(analysis)
        assert "" not in result
        assert "Клон 5" in result
