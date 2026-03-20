"""Unit tests for KnowledgeManager.

FAILURE означава: src/knowledge_manager.py :: KnowledgeManager е счупена —
AI-ят работи без knowledge context (уроци, методологии, производителности).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.knowledge_manager import KnowledgeManager


@pytest.fixture
def km(tmp_path):
    """KnowledgeManager с временна директория за тестове."""
    knowledge_dir = tmp_path / "knowledge"
    (knowledge_dir / "lessons").mkdir(parents=True)
    (knowledge_dir / "methodologies").mkdir(parents=True)
    (knowledge_dir / "skills" / "references").mkdir(parents=True)
    (tmp_path / "config").mkdir(parents=True)
    return KnowledgeManager(str(knowledge_dir))


# ---------------------------------------------------------------------------
# get_lessons
# ---------------------------------------------------------------------------

class TestGetLessons:
    def test_no_file_returns_empty(self, km):
        assert km.get_lessons() == []

    def test_empty_file_returns_empty(self, km):
        (km.lessons_path / "lessons_learned.md").write_text("# Уроци\n", encoding="utf-8")
        assert km.get_lessons() == []

    def test_parses_lesson_lines(self, km):
        (km.lessons_path / "lessons_learned.md").write_text(
            "# Уроци\n**#1**: Урок едно\n**#2**: Урок две\n", encoding="utf-8"
        )
        lessons = km.get_lessons()
        assert len(lessons) == 2
        assert lessons[0] == "**#1**: Урок едно"
        assert lessons[1] == "**#2**: Урок две"

    def test_skips_non_lesson_lines(self, km):
        (km.lessons_path / "lessons_learned.md").write_text(
            "# Заглавие\nПроизволен текст\n**#1**: Реален урок\n", encoding="utf-8"
        )
        assert km.get_lessons() == ["**#1**: Реален урок"]


# ---------------------------------------------------------------------------
# get_pending_lessons
# ---------------------------------------------------------------------------

class TestGetPendingLessons:
    def test_no_file_returns_empty(self, km):
        assert km.get_pending_lessons() == []

    def test_parses_list_items(self, km):
        (km.lessons_path / "pending_lessons.md").write_text(
            "# Pending\n- Първи урок\n- Втори урок\n", encoding="utf-8"
        )
        pending = km.get_pending_lessons()
        assert pending == ["Първи урок", "Втори урок"]

    def test_skips_empty_list_items(self, km):
        (km.lessons_path / "pending_lessons.md").write_text(
            "# Pending\n- \n- Валиден\n", encoding="utf-8"
        )
        assert km.get_pending_lessons() == ["Валиден"]


# ---------------------------------------------------------------------------
# add_lesson
# ---------------------------------------------------------------------------

class TestAddLesson:
    def test_creates_file_when_missing(self, km):
        km.add_lesson("Нов урок")
        filepath = km.lessons_path / "pending_lessons.md"
        assert filepath.exists()
        assert "Нов урок" in filepath.read_text(encoding="utf-8")

    def test_appends_to_existing_file(self, km):
        filepath = km.lessons_path / "pending_lessons.md"
        filepath.write_text("# Pending\n- Първи\n", encoding="utf-8")
        km.add_lesson("Втори")
        content = filepath.read_text(encoding="utf-8")
        assert "Първи" in content
        assert "Втори" in content

    def test_invalidates_cache_after_write(self, km):
        filepath = km.lessons_path / "pending_lessons.md"
        filepath.write_text("# Pending\n", encoding="utf-8")
        km.get_pending_lessons()  # напълни кеша
        km.add_lesson("Нов")
        assert "Нов" in km.get_pending_lessons()


# ---------------------------------------------------------------------------
# approve_lesson
# ---------------------------------------------------------------------------

class TestApproveLesson:
    def test_moves_from_pending_to_learned(self, km):
        (km.lessons_path / "lessons_learned.md").write_text("# Уроци\n", encoding="utf-8")
        (km.lessons_path / "pending_lessons.md").write_text(
            "# Pending\n- Тестов урок\n", encoding="utf-8"
        )
        km.approve_lesson("Тестов урок")
        learned = (km.lessons_path / "lessons_learned.md").read_text(encoding="utf-8")
        assert "Тестов урок" in learned
        pending = (km.lessons_path / "pending_lessons.md").read_text(encoding="utf-8")
        assert "Тестов урок" not in pending

    def test_creates_learned_file_when_missing(self, km):
        """approve_lesson не трябва да хвърля FileNotFoundError при липсващ файл."""
        (km.lessons_path / "pending_lessons.md").write_text(
            "# Pending\n- Тестов урок\n", encoding="utf-8"
        )
        km.approve_lesson("Тестов урок")  # не трябва да raise
        learned = (km.lessons_path / "lessons_learned.md").read_text(encoding="utf-8")
        assert "Тестов урок" in learned

    def test_numbering_continues_from_existing(self, km):
        (km.lessons_path / "lessons_learned.md").write_text(
            "# Уроци\n**#1**: Стар урок\n", encoding="utf-8"
        )
        km.approve_lesson("Нов урок")
        learned = (km.lessons_path / "lessons_learned.md").read_text(encoding="utf-8")
        assert "**#2**" in learned

    def test_lesson_number_1_when_no_existing(self, km):
        (km.lessons_path / "lessons_learned.md").write_text("# Уроци\n", encoding="utf-8")
        km.approve_lesson("Първи урок")
        learned = (km.lessons_path / "lessons_learned.md").read_text(encoding="utf-8")
        assert "**#1**" in learned


# ---------------------------------------------------------------------------
# get_methodology
# ---------------------------------------------------------------------------

class TestGetMethodology:
    def test_known_type_returns_content(self, km):
        (km.methodologies_path / "distribution_network.md").write_text(
            "# Разпределителна\nПравило А", encoding="utf-8"
        )
        result = km.get_methodology("distribution")
        assert "Правило А" in result

    def test_unknown_type_returns_error_message(self, km):
        result = km.get_methodology("nonexistent_type")
        assert "Unknown project type" in result

    def test_missing_file_returns_not_found(self, km):
        result = km.get_methodology("engineering")
        assert "not found" in result

    def test_all_four_types_have_valid_mapping(self, km):
        """Четирите известни типа не трябва да връщат 'Unknown project type'."""
        for pt in ("engineering", "distribution", "supply", "single"):
            result = km.get_methodology(pt)
            assert "Unknown project type" not in result, f"Тип '{pt}' няма mapping"


# ---------------------------------------------------------------------------
# get_knowledge_stats
# ---------------------------------------------------------------------------

class TestGetKnowledgeStats:
    def test_empty_returns_zeros(self, km):
        stats = km.get_knowledge_stats()
        assert stats["lessons"] == 0
        assert stats["pending"] == 0
        assert stats["methodologies"] == 0
        assert stats["skill_references"] == 0

    def test_counts_lessons_and_methodologies(self, km):
        (km.lessons_path / "lessons_learned.md").write_text(
            "**#1**: Урок А\n**#2**: Урок Б\n", encoding="utf-8"
        )
        (km.methodologies_path / "distribution_network.md").write_text("x", encoding="utf-8")
        (km.methodologies_path / "README.md").write_text("readme", encoding="utf-8")
        stats = km.get_knowledge_stats()
        assert stats["lessons"] == 2
        assert stats["methodologies"] == 1  # README.md не се брои

    def test_counts_pending(self, km):
        (km.lessons_path / "pending_lessons.md").write_text(
            "# Pending\n- Урок А\n- Урок Б\n", encoding="utf-8"
        )
        assert km.get_knowledge_stats()["pending"] == 2


# ---------------------------------------------------------------------------
# Cache (_read_cached + invalidate_cache)
# ---------------------------------------------------------------------------

class TestCache:
    def test_nonexistent_file_returns_empty(self, km):
        fake = km.lessons_path / "nonexistent.md"
        assert km._read_cached(fake) == ""

    def test_repeated_reads_return_same_content(self, km):
        filepath = km.lessons_path / "lessons_learned.md"
        filepath.write_text("**#1**: Кеширан\n", encoding="utf-8")
        assert km._read_cached(filepath) == km._read_cached(filepath)

    def test_invalidate_forces_reread(self, km):
        filepath = km.lessons_path / "lessons_learned.md"
        filepath.write_text("Версия 1\n", encoding="utf-8")
        km._read_cached(filepath)  # напълни кеша
        filepath.write_text("Версия 2\n", encoding="utf-8")
        km.invalidate_cache()
        result = km._read_cached(filepath)
        assert "Версия 2" in result


# ---------------------------------------------------------------------------
# build_system_prompt / get_all_knowledge_for_prompt
# ---------------------------------------------------------------------------

class TestBuildPrompt:
    def test_minimal_contains_core_rules(self, km):
        result = km.get_all_knowledge_for_prompt(level="minimal")
        assert "CORE RULES" in result

    def test_full_includes_methodology_when_given(self, km):
        (km.methodologies_path / "single_section.md").write_text(
            "# Единичен участък\nПравило X", encoding="utf-8"
        )
        result = km.get_all_knowledge_for_prompt(project_type="single", level="full")
        assert "Правило X" in result

    def test_full_without_project_type(self, km):
        result = km.get_all_knowledge_for_prompt(level="full")
        assert isinstance(result, str)

    def test_verification_includes_all_lessons_header(self, km):
        (km.lessons_path / "lessons_learned.md").write_text(
            "**#1**: Урок А\n**#2**: Урок Б\n", encoding="utf-8"
        )
        result = km.get_all_knowledge_for_prompt(level="verification")
        assert "ALL LESSONS" in result

    def test_build_system_prompt_delegates_to_full(self, km):
        """build_system_prompt е алиас за get_all_knowledge_for_prompt(level='full')."""
        result_a = km.build_system_prompt()
        result_b = km.get_all_knowledge_for_prompt(level="full")
        assert result_a == result_b


# ---------------------------------------------------------------------------
# update_methodology
# ---------------------------------------------------------------------------

class TestUpdateMethodology:
    def test_known_type_writes_correct_file(self, km):
        km.update_methodology("distribution", "# Разпределителна\nПравило А")
        filepath = km.methodologies_path / "distribution_network.md"
        assert filepath.exists()
        assert "Правило А" in filepath.read_text(encoding="utf-8")

    def test_all_four_types_map_to_correct_files(self, km):
        expected = {
            "engineering": "engineering_projects.md",
            "distribution": "distribution_network.md",
            "supply": "supply_pipeline.md",
            "single": "single_section.md",
        }
        for project_type, filename in expected.items():
            km.update_methodology(project_type, f"content for {project_type}")
            assert (km.methodologies_path / filename).exists()

    def test_unknown_type_does_nothing(self, km):
        km.update_methodology("nonexistent_type", "some content")
        # Нито един файл не трябва да е създаден
        files = list(km.methodologies_path.glob("*.md"))
        assert files == []

    def test_overwrites_existing_content(self, km):
        (km.methodologies_path / "single_section.md").write_text("Стар текст", encoding="utf-8")
        km.update_methodology("single", "Нов текст")
        content = (km.methodologies_path / "single_section.md").read_text(encoding="utf-8")
        assert "Нов текст" in content
        assert "Стар текст" not in content

    def test_invalidates_cache_after_write(self, km):
        (km.methodologies_path / "supply_pipeline.md").write_text("Версия 1", encoding="utf-8")
        _ = km.get_methodology("supply")  # напълни кеша
        km.update_methodology("supply", "Версия 2")
        result = km.get_methodology("supply")
        assert "Версия 2" in result


# ---------------------------------------------------------------------------
# get_skills
# ---------------------------------------------------------------------------

class TestGetSkills:
    def test_returns_empty_when_file_missing(self, km):
        assert km.get_skills() == ""

    def test_returns_content_when_file_exists(self, km):
        (km.skills_path / "SKILL.md").write_text("# Умения\nПравило X", encoding="utf-8")
        result = km.get_skills()
        assert "Правило X" in result

    def test_caches_content_between_reads(self, km):
        filepath = km.skills_path / "SKILL.md"
        filepath.write_text("Cached content", encoding="utf-8")
        first = km.get_skills()
        second = km.get_skills()
        assert first == second == "Cached content"


# ---------------------------------------------------------------------------
# get_productivities
# ---------------------------------------------------------------------------

class TestGetProductivities:
    def test_returns_empty_when_file_missing(self, km):
        assert km.get_productivities() == ""

    def test_returns_json_content_as_string(self, km):
        productivities_path = km.knowledge_path.parent / "config" / "productivities.json"
        productivities_path.write_text('{"DN90": 55}', encoding="utf-8")
        result = km.get_productivities()
        assert '"DN90"' in result
        assert "55" in result

    def test_returns_empty_for_empty_file(self, km):
        productivities_path = km.knowledge_path.parent / "config" / "productivities.json"
        productivities_path.write_text("", encoding="utf-8")
        assert km.get_productivities() == ""


# ---------------------------------------------------------------------------
# get_workflow_rules
# ---------------------------------------------------------------------------

class TestGetWorkflowRules:
    def test_returns_empty_when_file_missing(self, km):
        assert km.get_workflow_rules() == ""

    def test_returns_rules_when_file_exists(self, km):
        rules_path = km.skills_path / "references" / "workflow-rules.md"
        rules_path.write_text("# Работни правила\nПравило 1", encoding="utf-8")
        result = km.get_workflow_rules()
        assert "Правило 1" in result

    def test_caches_content_between_reads(self, km):
        rules_path = km.skills_path / "references" / "workflow-rules.md"
        rules_path.write_text("Правило за кеш", encoding="utf-8")
        first = km.get_workflow_rules()
        second = km.get_workflow_rules()
        assert first == second == "Правило за кеш"
