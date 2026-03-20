"""Unit tests for AIProcessor pure (no-API) methods.

Covers: _validate_json_inputs (Rule #0), is_configured, build_system_prompt,
build_minimal_prompt, build_verification_prompt.

FAILURE означава: src/ai_processor.py :: Rule #0 или prompt builder-ите са счупени —
генераторът ще подаде необработени файлове на AI или ще генерира с празен промпт.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai_processor import AIProcessor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _processor(router=None, knowledge=None) -> AIProcessor:
    return AIProcessor(router=router, knowledge_manager=knowledge)


def _file(name: str, converted: str | None = None) -> dict:
    """Minimal file dict as returned by FileManager.get_converted_files()."""
    d: dict = {"name": name}
    if converted is not None:
        d["converted"] = converted
        d["original"] = name
    return d


def _mock_router(deepseek: bool = True, anthropic: bool = False):
    r = MagicMock()
    r.deepseek_available = deepseek
    r.anthropic_available = anthropic
    return r


# ---------------------------------------------------------------------------
# _validate_json_inputs — Rule #0
# ---------------------------------------------------------------------------

class TestValidateJsonInputs:
    def test_all_json_files_pass(self):
        """Happy path: all files have .json converted paths → no exception."""
        proc = _processor()
        files = [
            _file("КСС.xlsx", "converted/КСС.json"),
            _file("spec.pdf", "converted/spec.json"),
        ]
        proc._validate_json_inputs(files)  # must not raise

    def test_single_non_json_raises_value_error(self):
        """One non-JSON converted file → raises ValueError with filename."""
        proc = _processor()
        files = [_file("КСС.xlsx", "converted/КСС.xlsx")]
        with pytest.raises(ValueError, match="КСС.xlsx"):
            proc._validate_json_inputs(files)

    def test_error_message_mentions_rule_zero(self):
        """ValueError message must mention Rule #0 conversion requirement."""
        proc = _processor()
        files = [_file("file.pdf", "converted/file.pdf")]
        with pytest.raises(ValueError, match="Rule #0"):
            proc._validate_json_inputs(files)

    def test_multiple_non_json_raises(self):
        """Two non-JSON files → both names appear in the error."""
        proc = _processor()
        files = [
            _file("a.xlsx", "converted/a.xlsx"),
            _file("b.pdf", "converted/b.pdf"),
        ]
        with pytest.raises(ValueError) as exc_info:
            proc._validate_json_inputs(files)
        msg = str(exc_info.value)
        assert "a.xlsx" in msg
        assert "b.pdf" in msg

    def test_empty_list_passes(self):
        """Empty file list → no exception (nothing to validate)."""
        proc = _processor()
        proc._validate_json_inputs([])

    def test_file_without_converted_key_is_ignored(self):
        """File dict without 'converted' key → skipped, no exception."""
        proc = _processor()
        files = [{"name": "КСС.xlsx"}]  # no "converted" key
        proc._validate_json_inputs(files)  # must not raise

    def test_file_with_none_converted_is_ignored(self):
        """File with converted=None → skipped, no exception."""
        proc = _processor()
        files = [{"name": "КСС.xlsx", "converted": None}]
        proc._validate_json_inputs(files)

    def test_mixed_valid_and_missing_converted_passes(self):
        """Mix of JSON file and file without 'converted' → passes."""
        proc = _processor()
        files = [
            _file("КСС.xlsx", "converted/КСС.json"),
            {"name": "ситуация.pdf"},  # no converted key — not yet converted
        ]
        proc._validate_json_inputs(files)

    def test_original_key_used_in_error_message(self):
        """When 'original' key exists, it should appear in the error message."""
        proc = _processor()
        files = [{"name": "КСС_v2.json", "converted": "КСС.xlsx", "original": "КСС.xlsx"}]
        with pytest.raises(ValueError, match="КСС.xlsx"):
            proc._validate_json_inputs(files)

    def test_json_extension_case_sensitive(self):
        """.JSON (uppercase) is not treated as .json — raises ValueError."""
        proc = _processor()
        files = [_file("КСС.xlsx", "converted/КСС.JSON")]
        with pytest.raises(ValueError):
            proc._validate_json_inputs(files)


# ---------------------------------------------------------------------------
# is_configured — router availability check
# ---------------------------------------------------------------------------

class TestIsConfigured:
    def test_no_router_returns_false(self):
        """No router set → is_configured is False."""
        proc = _processor()
        assert proc.is_configured is False

    def test_router_both_unavailable_returns_false(self):
        """Router exists but both models are unavailable → False."""
        proc = _processor(router=_mock_router(deepseek=False, anthropic=False))
        assert proc.is_configured is False

    def test_deepseek_available_returns_true(self):
        """DeepSeek available → is_configured is True."""
        proc = _processor(router=_mock_router(deepseek=True, anthropic=False))
        assert proc.is_configured is True

    def test_anthropic_available_returns_true(self):
        """Anthropic available → is_configured is True."""
        proc = _processor(router=_mock_router(deepseek=False, anthropic=True))
        assert proc.is_configured is True

    def test_both_available_returns_true(self):
        """Both models available → is_configured is True."""
        proc = _processor(router=_mock_router(deepseek=True, anthropic=True))
        assert proc.is_configured is True


# ---------------------------------------------------------------------------
# build_system_prompt — full knowledge prompt
# ---------------------------------------------------------------------------

class TestBuildSystemPrompt:
    def test_no_knowledge_returns_fallback(self):
        """No knowledge manager → returns hardcoded fallback string."""
        proc = _processor()
        result = proc.build_system_prompt()
        assert isinstance(result, str)
        assert len(result) > 20
        assert "ВиК" in result or "график" in result.lower()

    def test_delegates_to_knowledge_manager(self):
        """With knowledge manager → calls get_all_knowledge_for_prompt."""
        km = MagicMock()
        km.get_all_knowledge_for_prompt.return_value = "FULL PROMPT CONTENT"
        proc = _processor(knowledge=km)
        result = proc.build_system_prompt(project_type="distribution_network")
        assert result == "FULL PROMPT CONTENT"
        km.get_all_knowledge_for_prompt.assert_called_once_with(
            project_type="distribution_network", level="full"
        )

    def test_project_type_none_is_passed_through(self):
        """project_type=None is passed to knowledge manager."""
        km = MagicMock()
        km.get_all_knowledge_for_prompt.return_value = "PROMPT"
        proc = _processor(knowledge=km)
        proc.build_system_prompt()
        km.get_all_knowledge_for_prompt.assert_called_once_with(
            project_type=None, level="full"
        )

    def test_returns_string_type(self):
        """Return value is always a string."""
        proc = _processor()
        assert isinstance(proc.build_system_prompt(), str)


# ---------------------------------------------------------------------------
# build_minimal_prompt — lightweight knowledge prompt
# ---------------------------------------------------------------------------

class TestBuildMinimalPrompt:
    def test_no_knowledge_returns_fallback(self):
        """No knowledge manager → returns hardcoded fallback string."""
        proc = _processor()
        result = proc.build_minimal_prompt()
        assert isinstance(result, str)
        assert len(result) > 10

    def test_delegates_to_knowledge_manager(self):
        """With knowledge manager → calls get_all_knowledge_for_prompt(level='minimal')."""
        km = MagicMock()
        km.get_all_knowledge_for_prompt.return_value = "MINIMAL CONTENT"
        proc = _processor(knowledge=km)
        result = proc.build_minimal_prompt()
        assert result == "MINIMAL CONTENT"
        km.get_all_knowledge_for_prompt.assert_called_once_with(level="minimal")

    def test_minimal_fallback_shorter_than_full(self):
        """Minimal fallback string should be ≤ full fallback string in length."""
        proc = _processor()
        minimal = proc.build_minimal_prompt()
        full = proc.build_system_prompt()
        assert len(minimal) <= len(full)


# ---------------------------------------------------------------------------
# build_verification_prompt — controller (Anthropic) rules
# ---------------------------------------------------------------------------

class TestBuildVerificationPrompt:
    def test_no_knowledge_returns_base_string(self):
        """No knowledge manager → returns base 'Проверявай СТРИКТНО' string."""
        proc = _processor()
        result = proc.build_verification_prompt()
        assert "Проверявай" in result
        assert isinstance(result, str)

    def test_with_knowledge_includes_skills(self):
        """With knowledge manager that has skills → skills appear in result."""
        km = MagicMock()
        km.get_skills.return_value = "SKILL RULES"
        km.skills_path = MagicMock()
        # Make the reference file paths return non-existent paths
        refs_mock = MagicMock()
        km.skills_path.__truediv__ = MagicMock(return_value=refs_mock)
        checklist = MagicMock()
        checklist.exists.return_value = False
        workflow = MagicMock()
        workflow.exists.return_value = False
        refs_mock.__truediv__ = MagicMock(side_effect=lambda x: checklist if "checklist" in x else workflow)
        proc = _processor(knowledge=km)
        result = proc.build_verification_prompt()
        assert "SKILL RULES" in result

    def test_with_knowledge_empty_skills_skipped(self):
        """With knowledge manager that returns empty skills → no empty section added."""
        km = MagicMock()
        km.get_skills.return_value = ""  # empty skills
        km.skills_path = MagicMock()
        refs_mock = MagicMock()
        km.skills_path.__truediv__ = MagicMock(return_value=refs_mock)
        f = MagicMock()
        f.exists.return_value = False
        refs_mock.__truediv__ = MagicMock(return_value=f)
        proc = _processor(knowledge=km)
        result = proc.build_verification_prompt()
        assert isinstance(result, str)
        # Base header is always present
        assert "Проверявай" in result

    def test_includes_checklist_when_file_exists(self, tmp_path):
        """Checklist file exists → its content appears in the verification prompt."""
        skills_dir = tmp_path / "skills"
        refs_dir = skills_dir / "references"
        refs_dir.mkdir(parents=True)
        checklist_file = refs_dir / "verification-checklist.md"
        checklist_file.write_text("CHECKLIST CONTENT", encoding="utf-8")

        km = MagicMock()
        km.get_skills.return_value = ""
        km.skills_path = skills_dir
        proc = _processor(knowledge=km)
        result = proc.build_verification_prompt()
        assert "CHECKLIST CONTENT" in result
        assert "VERIFICATION CHECKLIST" in result

    def test_includes_workflow_when_file_exists(self, tmp_path):
        """Workflow rules file exists → its content appears in the prompt."""
        skills_dir = tmp_path / "skills"
        refs_dir = skills_dir / "references"
        refs_dir.mkdir(parents=True)
        workflow_file = refs_dir / "workflow-rules.md"
        workflow_file.write_text("WORKFLOW RULES CONTENT", encoding="utf-8")

        km = MagicMock()
        km.get_skills.return_value = ""
        km.skills_path = skills_dir
        proc = _processor(knowledge=km)
        result = proc.build_verification_prompt()
        assert "WORKFLOW RULES CONTENT" in result
        assert "WORKFLOW RULES" in result


# ---------------------------------------------------------------------------
# generate_schedule prompt — verified productivity rates (no-API path)
# Verifies that the schedule-generation prompt embeds the correct effective
# rates from productivities.md v0.4 and does NOT contain the old wrong values.
# ---------------------------------------------------------------------------

class TestGenerateSchedulePromptRates:
    """Checks that AI prompt has correct productivity rates (verified v0.4).

    If these tests fail it means someone has edited the prompt in
    ai_processor.py with incorrect rates that contradict productivities.md.
    """

    def _build_prompt(self) -> str:
        """Build the generate-schedule system prompt (knowledge-free path)."""
        km = MagicMock()
        km.get_skills.return_value = ""
        km.get_methodology.return_value = ""
        km.get_productivities.return_value = ""
        km.get_lessons.return_value = ""
        km.get_workflow_rules.return_value = ""
        proc = _processor(knowledge=km)
        # Call build_system_prompt which is used inside generate_schedule
        return proc.build_system_prompt()

    def _build_generate_prompt_fragment(self) -> str:
        """Extract the hardcoded production-rate section from generate_schedule source.

        We inspect the source string directly to avoid needing a real router call.
        We grab the relevant constant strings from the module.
        """
        import inspect
        import src.ai_processor as mod
        src_text = inspect.getsource(mod.AIProcessor.generate_schedule)
        return src_text

    def test_dn300_ci_correct_rate_8(self):
        """Prompt must contain DN300 CI effective rate of 8 м/ден (verified)."""
        fragment = self._build_generate_prompt_fragment()
        # The correct verified rate is 8 м/ден; old wrong value was 3-5 м/ден
        assert "8 м/ден" in fragment, (
            "DN300 CI effective rate must be 8 м/ден (verified productivities.md). "
            "Old wrong value '3-5 м/ден' was overestimating schedule duration by 1.6-2.7×."
        )

    def test_dn300_ci_old_wrong_rate_absent(self):
        """Prompt must NOT contain the old wrong DN300 CI rate of '3-5 м/ден'."""
        fragment = self._build_generate_prompt_fragment()
        assert "3-5 м/ден" not in fragment, (
            "Old wrong DN300 CI rate '3-5 м/ден' found in prompt. "
            "This contradicts productivities.md which specifies effective_rate: 8."
        )

    def test_dn500_pe_correct_rate_15(self):
        """Prompt must contain DN500 PE effective rate of 15 м/ден (verified)."""
        fragment = self._build_generate_prompt_fragment()
        assert "15 м/ден" in fragment, (
            "DN500 PE effective rate must be 15 м/ден (verified productivities.md). "
            "Old wrong value '20-25 м/ден' underestimated schedule duration by 33-67%."
        )

    def test_dn500_pe_old_wrong_rate_absent(self):
        """Prompt must NOT contain the old wrong DN500 PE rate of '20-25 м/ден'."""
        fragment = self._build_generate_prompt_fragment()
        assert "20-25 м/ден" not in fragment, (
            "Old wrong DN500 PE rate '20-25 м/ден' found in prompt. "
            "Correct verified rate from productivities.md is 15 м/ден."
        )

    def test_hdd_effective_rate_not_drill_rate(self):
        """Prompt must clarify that HDD effective rate (12-13 м/ден) ≠ drill rate (56 м/ден)."""
        fragment = self._build_generate_prompt_fragment()
        # Should explicitly mention the word "ЕФЕКТИВНА" near HDD to prevent using drill rate
        assert "ЕФЕКТИВНА" in fragment, (
            "Prompt must explicitly mark HDD effective rate as 'ЕФЕКТИВНА' "
            "to prevent AI from using the drill rate (56 м/ден) which is 4-5× higher."
        )

    def test_hdd_56_is_drill_rate_warning_present(self):
        """Prompt must warn that 56 м/ден is drill rate, not effective rate."""
        fragment = self._build_generate_prompt_fragment()
        # The drill rate of 56 should still be mentioned, but labeled as пробивна (drill)
        assert "пробивна" in fragment.lower(), (
            "Prompt must label '56 м/ден' as 'пробивна скорост' (drill rate) "
            "so AI knows NOT to use it for duration calculations."
        )

    def test_dn90_pe_rate_12(self):
        """Prompt must contain DN90 PE rate of 12 м/ден (verified)."""
        fragment = self._build_generate_prompt_fragment()
        assert "12 м/ден" in fragment, (
            "DN90 PE effective rate must be 12 м/ден per productivities.md v0.4."
        )

    def test_ci_multiplier_comment_is_realistic(self):
        """CI slowdown vs PE should be ~3-4×, not the old wrong '10-12×'."""
        fragment = self._build_generate_prompt_fragment()
        assert "10-12×" not in fragment, (
            "Old wrong comment '10-12× по-бавно от PE' found. "
            "Correct: DN300 CI (8 м/ден) vs DN300 PE (~25 м/ден) ≈ 3× slower, not 10-12×."
        )


if __name__ == "__main__":
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v"],
        capture_output=False
    )
    sys.exit(result.returncode)
