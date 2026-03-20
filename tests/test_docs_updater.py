"""Unit tests for DocsUpdater pure / near-pure helpers.

Covers: _parse_requirement, _extract_latest_version, suggest_changelog_entry,
_matches_trigger, check_for_updates (mocked git), and _replace_section.

FAILURE означава: src/docs_updater.py :: DocsUpdater е счупен —
автоматичното обновяване на README/CHANGELOG генерира грешни данни.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.docs_updater import DocsUpdater

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _updater(tmp_path: Path | None = None) -> DocsUpdater:
    """Return a DocsUpdater rooted at tmp_path (or a throwaway temp dir)."""
    if tmp_path is None:
        tmp_path = Path(tempfile.mkdtemp())
    return DocsUpdater(str(tmp_path))


# ---------------------------------------------------------------------------
# _parse_requirement
# ---------------------------------------------------------------------------

def test_parse_requirement_ge():
    """plotly>=5.0 → ('plotly', '>=5.0')"""
    assert DocsUpdater._parse_requirement("plotly>=5.0") == ("plotly", ">=5.0")


def test_parse_requirement_eq():
    """streamlit==1.32.0 → ('streamlit', '==1.32.0')"""
    assert DocsUpdater._parse_requirement("streamlit==1.32.0") == ("streamlit", "==1.32.0")


def test_parse_requirement_no_version():
    """Package with no version spec → ('anthropic', '')"""
    assert DocsUpdater._parse_requirement("anthropic") == ("anthropic", "")


def test_parse_requirement_tilde_eq():
    """openai~=1.0 → ('openai', '~=1.0')"""
    assert DocsUpdater._parse_requirement("openai~=1.0") == ("openai", "~=1.0")


def test_parse_requirement_strips_whitespace():
    """Leading/trailing spaces are stripped on both sides of the separator."""
    pkg, ver = DocsUpdater._parse_requirement("  pandas >= 2.0  ")
    assert pkg == "pandas"
    assert ver == ">=2.0"


def test_parse_requirement_with_extras():
    """plotly[all]>=5.0 → package name includes extras bracket."""
    pkg, ver = DocsUpdater._parse_requirement("plotly[all]>=5.0")
    assert pkg == "plotly[all]"
    assert ver == ">=5.0"


# ---------------------------------------------------------------------------
# _extract_latest_version
# ---------------------------------------------------------------------------

def test_extract_latest_version_standard():
    """## [1.2.3] header → '1.2.3'"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write("# Changelog\n\n## [1.2.3] - 2026-03-01\n\n### Added\n- foo\n")
        tmp = Path(f.name)
    assert DocsUpdater._extract_latest_version(tmp) == "1.2.3"


def test_extract_latest_version_returns_first_match():
    """Multiple versions → returns the first (latest) one."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write("## [2.0.0]\n\n## [1.5.0]\n")
        tmp = Path(f.name)
    assert DocsUpdater._extract_latest_version(tmp) == "2.0.0"


def test_extract_latest_version_no_version():
    """No version header → empty string."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write("# Changelog\n\nNo versions here.\n")
        tmp = Path(f.name)
    assert DocsUpdater._extract_latest_version(tmp) == ""


# ---------------------------------------------------------------------------
# suggest_changelog_entry
# ---------------------------------------------------------------------------

def test_suggest_feat_commit():
    """feat: commit → Добавено group."""
    updater = _updater()
    result = updater.suggest_changelog_entry(["feat: add XML export"])
    assert "### Добавено" in result
    assert "- add XML export" in result


def test_suggest_fix_commit():
    """fix: commit → Поправено group."""
    updater = _updater()
    result = updater.suggest_changelog_entry(["fix: correct duration calc"])
    assert "### Поправено" in result
    assert "- correct duration calc" in result


def test_suggest_refactor_commit():
    """refactor: commit → Променено group."""
    updater = _updater()
    result = updater.suggest_changelog_entry(["refactor: extract constants"])
    assert "### Променено" in result
    assert "- extract constants" in result


def test_suggest_docs_commit():
    """docs: commit → Документация group."""
    updater = _updater()
    result = updater.suggest_changelog_entry(["docs: update README with install steps"])
    assert "### Документация" in result
    assert "- update README with install steps" in result


def test_suggest_docs_with_scope():
    """docs(readme): commit → Документация group (scoped form)."""
    updater = _updater()
    result = updater.suggest_changelog_entry(["docs(readme): fix typo"])
    assert "### Документация" in result
    assert "- fix typo" in result


def test_suggest_feat_with_scope():
    """feat(export): message → scope stripped, in Добавено."""
    updater = _updater()
    result = updater.suggest_changelog_entry(["feat(export): add PDF support"])
    assert "### Добавено" in result
    assert "- add PDF support" in result


def test_suggest_fix_with_scope():
    """fix(xml): message → scope stripped, in Поправено."""
    updater = _updater()
    result = updater.suggest_changelog_entry(["fix(xml): handle empty task list"])
    assert "### Поправено" in result
    assert "- handle empty task list" in result


def test_suggest_unknown_commit_ignored():
    """test: and chore: commits have no group → empty result."""
    updater = _updater()
    result = updater.suggest_changelog_entry(["test: add unit tests", "chore: bump deps"])
    assert result == ""


def test_suggest_empty_list_returns_empty():
    """Empty commit list → empty string."""
    updater = _updater()
    assert updater.suggest_changelog_entry([]) == ""


def test_suggest_mixed_commits():
    """Mix of feat/fix/docs → all three groups present."""
    updater = _updater()
    result = updater.suggest_changelog_entry([
        "feat: gantt export",
        "fix: xml namespace",
        "docs: update CHANGELOG",
    ])
    assert "### Добавено" in result
    assert "### Поправено" in result
    assert "### Документация" in result


# ---------------------------------------------------------------------------
# _matches_trigger
# ---------------------------------------------------------------------------

def test_matches_trigger_exact():
    """requirements.txt trigger matches exactly."""
    updater = _updater()
    assert updater._matches_trigger("requirements.txt", "requirements.txt") is True


def test_matches_trigger_glob_star():
    """src/*.py matches src/ai_router.py."""
    updater = _updater()
    assert updater._matches_trigger("src/ai_router.py", "src/*.py") is True


def test_matches_trigger_no_match():
    """src/*.py does NOT match tests/test_foo.py."""
    updater = _updater()
    assert updater._matches_trigger("tests/test_foo.py", "src/*.py") is False


def test_matches_trigger_backslash_normalised():
    """Windows-style backslashes are normalised to forward slashes."""
    updater = _updater()
    assert updater._matches_trigger("src\\ai_router.py", "src/*.py") is True


# ---------------------------------------------------------------------------
# check_for_updates (mocked git)
# ---------------------------------------------------------------------------

def test_check_for_updates_no_git():
    """When git is unavailable, returns empty list."""
    updater = _updater()
    with patch.object(updater, "_git_available", return_value=False):
        result = updater.check_for_updates()
    assert result == []


def test_check_for_updates_no_changed_files():
    """git available but no changed files → empty list."""
    updater = _updater()
    with patch.object(updater, "_git_available", return_value=True), \
         patch.object(updater, "_git_changed_files", return_value=[]):
        result = updater.check_for_updates()
    assert result == []


def test_check_for_updates_requirements_triggers_readme():
    """requirements.txt change → README.md update suggested."""
    updater = _updater()
    with patch.object(updater, "_git_available", return_value=True), \
         patch.object(updater, "_git_changed_files", return_value=["requirements.txt"]):
        result = updater.check_for_updates()
    docs = [r["doc"] for r in result]
    assert "README.md" in docs


def test_check_for_updates_src_py_triggers_readme_and_arch():
    """src/ai_router.py change → triggers both README.md and ARCHITECTURE.md."""
    updater = _updater()
    with patch.object(updater, "_git_available", return_value=True), \
         patch.object(updater, "_git_changed_files", return_value=["src/ai_router.py"]):
        result = updater.check_for_updates()
    docs = [r["doc"] for r in result]
    assert "README.md" in docs
    assert "docs/ARCHITECTURE.md" in docs


# ---------------------------------------------------------------------------
# _replace_section
# ---------------------------------------------------------------------------

def test_replace_section_replaces_content():
    """Content between markers is replaced."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write("Before\n<!-- TEST_START -->\nOLD\n<!-- TEST_END -->\nAfter\n")
        tmp = Path(f.name)

    updater = _updater()
    changed = updater._replace_section(tmp, "TEST", "NEW CONTENT")
    assert changed is True
    result = tmp.read_text(encoding="utf-8")
    assert "NEW CONTENT" in result
    assert "OLD" not in result
    assert "Before" in result
    assert "After" in result


def test_replace_section_returns_false_when_no_markers():
    """Missing markers → returns False, file unchanged."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write("No markers here.\n")
        tmp = Path(f.name)

    updater = _updater()
    changed = updater._replace_section(tmp, "MISSING", "new")
    assert changed is False
    assert tmp.read_text(encoding="utf-8") == "No markers here.\n"


def test_replace_section_same_content_returns_false():
    """If content is unchanged, returns False (no write)."""
    content = "<!-- X_START -->\nSAME\n<!-- X_END -->\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(content)
        tmp = Path(f.name)

    updater = _updater()
    # Replace with identical content (strip extra newlines to match)
    changed = updater._replace_section(tmp, "X", "SAME")
    # The produced content should equal the original → no change
    assert changed is False


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  FAIL  {t.__name__}: {exc}")
            failed += 1
    print(f"\n{passed}/{passed + failed} passed")
