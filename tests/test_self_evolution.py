"""Unit tests for SelfEvolution — pure and filesystem methods (no API calls).

Covers: _get_file_tree, preview_changes, verify_admin_code, apply_changes
        (create/modify/delete/errors), log_change, _load_history, _save_history,
        CHANGE_LEVELS structure, get_change_history.

FAILURE означава: src/self_evolution.py :: SelfEvolution е счупен —
self-модификацията на приложението (GREEN/YELLOW/RED промени, rollback)
не работи, потенциално оставяйки файлове в несъответствие без backup.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.self_evolution import CHANGE_LEVELS, SelfEvolution


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_evo(tmp_path: Path) -> SelfEvolution:
    """Create a SelfEvolution instance with a mock router and isolated tmp dir."""
    router = MagicMock()
    # Silence _load_history (no log file → empty history)
    return SelfEvolution(app_root=str(tmp_path), router=router)


# ---------------------------------------------------------------------------
# CHANGE_LEVELS structure
# ---------------------------------------------------------------------------

class TestChangeLevels:
    def test_has_three_levels(self):
        assert set(CHANGE_LEVELS.keys()) == {"green", "yellow", "red"}

    def test_green_no_admin_no_confirm(self):
        g = CHANGE_LEVELS["green"]
        assert g["requires_admin"] is False
        assert g["requires_confirm"] is False

    def test_yellow_no_admin_needs_confirm(self):
        y = CHANGE_LEVELS["yellow"]
        assert y["requires_admin"] is False
        assert y["requires_confirm"] is True

    def test_red_needs_admin_and_confirm(self):
        r = CHANGE_LEVELS["red"]
        assert r["requires_admin"] is True
        assert r["requires_confirm"] is True

    def test_all_levels_have_emoji_and_name(self):
        for level, info in CHANGE_LEVELS.items():
            assert "emoji" in info, f"Level {level} missing emoji"
            assert "name" in info, f"Level {level} missing name"


# ---------------------------------------------------------------------------
# _get_file_tree
# ---------------------------------------------------------------------------

class TestGetFileTree:
    def test_lists_py_files(self, tmp_path):
        (tmp_path / "app.py").write_text("x = 1", encoding="utf-8")
        evo = _make_evo(tmp_path)
        tree = evo._get_file_tree()
        assert "app.py" in tree

    def test_lists_md_and_json(self, tmp_path):
        (tmp_path / "README.md").write_text("# Test", encoding="utf-8")
        (tmp_path / "config.json").write_text("{}", encoding="utf-8")
        evo = _make_evo(tmp_path)
        tree = evo._get_file_tree()
        assert "README.md" in tree
        assert "config.json" in tree

    def test_excludes_pycache(self, tmp_path):
        cache_dir = tmp_path / "__pycache__"
        cache_dir.mkdir()
        (cache_dir / "foo.pyc").write_bytes(b"")
        (tmp_path / "real.py").write_text("pass", encoding="utf-8")
        evo = _make_evo(tmp_path)
        tree = evo._get_file_tree()
        assert "__pycache__" not in tree
        assert "real.py" in tree

    def test_excludes_non_text_extensions(self, tmp_path):
        (tmp_path / "image.png").write_bytes(b"\x89PNG")
        (tmp_path / "script.py").write_text("pass", encoding="utf-8")
        evo = _make_evo(tmp_path)
        tree = evo._get_file_tree()
        assert "image.png" not in tree
        assert "script.py" in tree

    def test_empty_directory_returns_empty(self, tmp_path):
        evo = _make_evo(tmp_path)
        tree = evo._get_file_tree()
        assert tree == "(empty)"

    def test_nested_files_included(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "module.py").write_text("pass", encoding="utf-8")
        evo = _make_evo(tmp_path)
        tree = evo._get_file_tree()
        assert "module.py" in tree


# ---------------------------------------------------------------------------
# verify_admin_code
# ---------------------------------------------------------------------------

class TestVerifyAdminCode:
    def test_correct_code_returns_true(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ADMIN_CODE", "secret123")
        evo = _make_evo(tmp_path)
        evo.admin_code = "secret123"
        assert evo.verify_admin_code("secret123") is True

    def test_wrong_code_returns_false(self, tmp_path):
        evo = _make_evo(tmp_path)
        evo.admin_code = "secret123"
        assert evo.verify_admin_code("wrongcode") is False

    def test_empty_code_returns_false(self, tmp_path):
        evo = _make_evo(tmp_path)
        evo.admin_code = "secret123"
        assert evo.verify_admin_code("") is False

    def test_no_admin_code_set_returns_false(self, tmp_path):
        evo = _make_evo(tmp_path)
        evo.admin_code = None
        assert evo.verify_admin_code("anything") is False

    def test_case_sensitive_check(self, tmp_path):
        evo = _make_evo(tmp_path)
        evo.admin_code = "Secret123"
        assert evo.verify_admin_code("secret123") is False
        assert evo.verify_admin_code("Secret123") is True


# ---------------------------------------------------------------------------
# apply_changes — create
# ---------------------------------------------------------------------------

class TestApplyChangesCreate:
    def test_create_new_file(self, tmp_path):
        evo = _make_evo(tmp_path)
        changes = {"changes": [{"action": "create", "file_path": "new_file.txt", "content": "hello"}]}
        result = evo.apply_changes(changes)
        assert result["applied"] == 1
        assert result["failed"] == 0
        assert (tmp_path / "new_file.txt").read_text(encoding="utf-8") == "hello"

    def test_create_in_nested_directory(self, tmp_path):
        evo = _make_evo(tmp_path)
        changes = {"changes": [{"action": "create", "file_path": "sub/dir/file.py", "content": "x=1"}]}
        evo.apply_changes(changes)
        assert (tmp_path / "sub" / "dir" / "file.py").exists()

    def test_create_returns_detail_entry(self, tmp_path):
        evo = _make_evo(tmp_path)
        changes = {"changes": [{"action": "create", "file_path": "f.txt", "content": "c"}]}
        result = evo.apply_changes(changes)
        detail = result["details"][0]
        assert detail["action"] == "created"
        assert detail["status"] == "ok"

    def test_create_empty_content(self, tmp_path):
        evo = _make_evo(tmp_path)
        changes = {"changes": [{"action": "create", "file_path": "empty.txt", "content": ""}]}
        result = evo.apply_changes(changes)
        assert result["applied"] == 1
        assert (tmp_path / "empty.txt").read_text(encoding="utf-8") == ""


# ---------------------------------------------------------------------------
# apply_changes — modify
# ---------------------------------------------------------------------------

class TestApplyChangesModify:
    def test_modify_replaces_old_code(self, tmp_path):
        target = tmp_path / "module.py"
        target.write_text("x = 1\ny = 2\n", encoding="utf-8")
        evo = _make_evo(tmp_path)
        changes = {"changes": [{
            "action": "modify",
            "file_path": "module.py",
            "modifications": [{"description": "change x", "old_code": "x = 1", "new_code": "x = 99"}],
        }]}
        result = evo.apply_changes(changes)
        assert result["applied"] == 1
        assert "x = 99" in target.read_text(encoding="utf-8")

    def test_modify_file_not_found(self, tmp_path):
        evo = _make_evo(tmp_path)
        changes = {"changes": [{
            "action": "modify",
            "file_path": "missing.py",
            "modifications": [{"description": "x", "old_code": "a", "new_code": "b"}],
        }]}
        result = evo.apply_changes(changes)
        assert result["failed"] == 1
        assert any("не съществува" in e for e in result["errors"])

    def test_modify_old_code_not_found(self, tmp_path):
        target = tmp_path / "module.py"
        target.write_text("x = 1\n", encoding="utf-8")
        evo = _make_evo(tmp_path)
        changes = {"changes": [{
            "action": "modify",
            "file_path": "module.py",
            "modifications": [{"description": "x", "old_code": "NOTEXISTS", "new_code": "z = 0"}],
        }]}
        result = evo.apply_changes(changes)
        assert result["failed"] == 1
        assert any("NOTEXISTS" in e or "не може да се намери" in e for e in result["errors"])

    def test_modify_only_replaces_first_occurrence(self, tmp_path):
        target = tmp_path / "dup.py"
        target.write_text("a = 1\na = 1\n", encoding="utf-8")
        evo = _make_evo(tmp_path)
        changes = {"changes": [{
            "action": "modify",
            "file_path": "dup.py",
            "modifications": [{"description": "one", "old_code": "a = 1", "new_code": "a = 99"}],
        }]}
        evo.apply_changes(changes)
        content = target.read_text(encoding="utf-8")
        # First occurrence replaced, second remains
        assert content.count("a = 99") == 1
        assert content.count("a = 1") == 1


# ---------------------------------------------------------------------------
# apply_changes — delete
# ---------------------------------------------------------------------------

class TestApplyChangesDelete:
    def test_delete_existing_file(self, tmp_path):
        target = tmp_path / "to_delete.txt"
        target.write_text("bye", encoding="utf-8")
        evo = _make_evo(tmp_path)
        changes = {"changes": [{"action": "delete", "file_path": "to_delete.txt"}]}
        result = evo.apply_changes(changes)
        assert result["applied"] == 1
        assert not target.exists()

    def test_delete_missing_file_records_error(self, tmp_path):
        evo = _make_evo(tmp_path)
        changes = {"changes": [{"action": "delete", "file_path": "phantom.txt"}]}
        result = evo.apply_changes(changes)
        assert result["failed"] == 1

    def test_delete_returns_correct_action_label(self, tmp_path):
        target = tmp_path / "x.txt"
        target.write_text("x", encoding="utf-8")
        evo = _make_evo(tmp_path)
        changes = {"changes": [{"action": "delete", "file_path": "x.txt"}]}
        result = evo.apply_changes(changes)
        assert result["details"][0]["action"] == "deleted"


# ---------------------------------------------------------------------------
# apply_changes — edge cases
# ---------------------------------------------------------------------------

class TestApplyChangesEdgeCases:
    def test_empty_changes_list(self, tmp_path):
        evo = _make_evo(tmp_path)
        result = evo.apply_changes({"changes": []})
        assert result["applied"] == 0
        assert result["failed"] == 0
        assert result["errors"] == []

    def test_missing_changes_key(self, tmp_path):
        evo = _make_evo(tmp_path)
        result = evo.apply_changes({})
        assert result["applied"] == 0

    def test_multiple_changes_counted_correctly(self, tmp_path):
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        (tmp_path / "b.txt").write_text("b", encoding="utf-8")
        evo = _make_evo(tmp_path)
        changes = {"changes": [
            {"action": "create", "file_path": "c.txt", "content": "c"},
            {"action": "delete", "file_path": "a.txt"},
            {"action": "delete", "file_path": "b.txt"},
        ]}
        result = evo.apply_changes(changes)
        assert result["applied"] == 3
        assert result["failed"] == 0


# ---------------------------------------------------------------------------
# preview_changes
# ---------------------------------------------------------------------------

class TestPreviewChanges:
    def _basic_plan(self, level: str = "green") -> dict:
        return {"level": level, "risks": [], "user_impact": "", "affected_files": []}

    def test_preview_create_shows_new_file(self, tmp_path):
        evo = _make_evo(tmp_path)
        plan = self._basic_plan("green")
        changes = {"changes": [{"action": "create", "file_path": "new.md", "content": "# hello\nworld"}], "new_requirements": [], "test_instructions": ""}
        preview = evo.preview_changes(plan, changes)
        assert "new.md" in preview
        assert "➕" in preview or "Нов файл" in preview

    def test_preview_modify_shows_file(self, tmp_path):
        evo = _make_evo(tmp_path)
        plan = self._basic_plan("yellow")
        changes = {"changes": [{"action": "modify", "file_path": "config.json", "modifications": [{"description": "change timeout"}]}], "new_requirements": [], "test_instructions": ""}
        preview = evo.preview_changes(plan, changes)
        assert "config.json" in preview
        assert "change timeout" in preview

    def test_preview_delete_shows_file(self, tmp_path):
        evo = _make_evo(tmp_path)
        plan = self._basic_plan("red")
        changes = {"changes": [{"action": "delete", "file_path": "old_module.py"}], "new_requirements": [], "test_instructions": ""}
        preview = evo.preview_changes(plan, changes)
        assert "old_module.py" in preview

    def test_preview_shows_risks(self, tmp_path):
        evo = _make_evo(tmp_path)
        plan = {"level": "red", "risks": ["може да счупи нещо", "нужен backup"], "user_impact": "", "affected_files": []}
        changes = {"changes": [], "new_requirements": [], "test_instructions": ""}
        preview = evo.preview_changes(plan, changes)
        assert "може да счупи нещо" in preview
        assert "нужен backup" in preview

    def test_preview_shows_new_requirements(self, tmp_path):
        evo = _make_evo(tmp_path)
        plan = self._basic_plan()
        changes = {"changes": [], "new_requirements": ["pandas>=2.0", "openpyxl"], "test_instructions": ""}
        preview = evo.preview_changes(plan, changes)
        assert "pandas" in preview
        assert "openpyxl" in preview

    def test_preview_shows_no_requirements_when_empty(self, tmp_path):
        evo = _make_evo(tmp_path)
        plan = self._basic_plan()
        changes = {"changes": [], "new_requirements": [], "test_instructions": ""}
        preview = evo.preview_changes(plan, changes)
        assert "няма" in preview.lower() or "Нови пакети" in preview

    def test_preview_shows_test_instructions(self, tmp_path):
        evo = _make_evo(tmp_path)
        plan = self._basic_plan()
        changes = {"changes": [], "new_requirements": [], "test_instructions": "Стартирай pytest tests/"}
        preview = evo.preview_changes(plan, changes)
        assert "Стартирай pytest" in preview

    def test_preview_unknown_level_defaults_to_red(self, tmp_path):
        evo = _make_evo(tmp_path)
        plan = {"level": "unknown_level", "risks": [], "user_impact": ""}
        changes = {"changes": [], "new_requirements": [], "test_instructions": ""}
        # Should not raise — falls back to red level
        preview = evo.preview_changes(plan, changes)
        assert isinstance(preview, str)


# ---------------------------------------------------------------------------
# log_change and get_change_history
# ---------------------------------------------------------------------------

class TestLogChange:
    def test_log_change_adds_entry(self, tmp_path):
        evo = _make_evo(tmp_path)
        plan = {"level": "green", "description": "Added lesson", "affected_files": []}
        evo.log_change("add lesson", plan, "abc123", "applied")
        history = evo.get_change_history()
        assert len(history) == 1
        assert history[0]["request"] == "add lesson"
        assert history[0]["status"] == "applied"

    def test_log_change_records_backup_hash(self, tmp_path):
        evo = _make_evo(tmp_path)
        plan = {"level": "red", "description": "fix", "affected_files": []}
        evo.log_change("fix bug", plan, "deadbeef", "applied")
        assert evo.get_change_history()[0]["backup_commit"] == "deadbeef"

    def test_log_change_records_level(self, tmp_path):
        evo = _make_evo(tmp_path)
        plan = {"level": "yellow", "description": "config update", "affected_files": []}
        evo.log_change("update config", plan, "", "applied")
        assert evo.get_change_history()[0]["level"] == "yellow"

    def test_log_change_extracts_affected_file_paths(self, tmp_path):
        evo = _make_evo(tmp_path)
        plan = {
            "level": "red",
            "description": "refactor",
            "affected_files": [{"path": "src/app.py"}, {"path": "src/utils.py"}],
        }
        evo.log_change("refactor", plan, "hash1", "applied")
        entry = evo.get_change_history()[0]
        assert "src/app.py" in entry["affected_files"]
        assert "src/utils.py" in entry["affected_files"]

    def test_log_change_persists_to_disk(self, tmp_path):
        evo = _make_evo(tmp_path)
        plan = {"level": "green", "description": "lesson", "affected_files": []}
        evo.log_change("first", plan, "", "applied")
        # Re-load from disk
        evo2 = _make_evo(tmp_path)
        assert len(evo2.get_change_history()) == 1
        assert evo2.get_change_history()[0]["request"] == "first"

    def test_multiple_log_entries_accumulate(self, tmp_path):
        evo = _make_evo(tmp_path)
        plan = {"level": "green", "description": "x", "affected_files": []}
        evo.log_change("first", plan, "", "applied")
        evo.log_change("second", plan, "", "rolled_back")
        history = evo.get_change_history()
        assert len(history) == 2
        assert history[1]["status"] == "rolled_back"


# ---------------------------------------------------------------------------
# _save_history / _load_history
# ---------------------------------------------------------------------------

class TestHistoryPersistence:
    def test_save_creates_log_file(self, tmp_path):
        evo = _make_evo(tmp_path)
        evo._save_history()
        log_path = tmp_path / "knowledge" / "evolution_log.json"
        assert log_path.exists()

    def test_save_writes_valid_json(self, tmp_path):
        evo = _make_evo(tmp_path)
        evo._save_history()
        log_path = tmp_path / "knowledge" / "evolution_log.json"
        data = json.loads(log_path.read_text(encoding="utf-8"))
        assert "changes" in data
        assert "stats" in data

    def test_save_and_load_roundtrip(self, tmp_path):
        evo = _make_evo(tmp_path)
        plan = {"level": "green", "description": "test", "affected_files": []}
        evo.log_change("roundtrip", plan, "abc", "applied")
        evo2 = _make_evo(tmp_path)
        assert len(evo2.change_history) == 1
        assert evo2.change_history[0]["request"] == "roundtrip"

    def test_load_missing_file_gives_empty_history(self, tmp_path):
        evo = _make_evo(tmp_path)
        assert evo.change_history == []

    def test_load_corrupted_json_gives_empty_history(self, tmp_path):
        log_path = tmp_path / "knowledge" / "evolution_log.json"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("{INVALID JSON{{", encoding="utf-8")
        evo = _make_evo(tmp_path)
        assert evo.change_history == []

    def test_stats_counted_correctly(self, tmp_path):
        evo = _make_evo(tmp_path)
        plan_green = {"level": "green", "description": "g", "affected_files": []}
        plan_red = {"level": "red", "description": "r", "affected_files": []}
        evo.log_change("g1", plan_green, "", "applied")
        evo.log_change("r1", plan_red, "", "rolled_back")
        evo.log_change("g2", plan_green, "", "applied")
        log_path = tmp_path / "knowledge" / "evolution_log.json"
        data = json.loads(log_path.read_text(encoding="utf-8"))
        assert data["stats"]["total_changes"] == 3
        assert data["stats"]["green_changes"] == 2
        assert data["stats"]["red_changes"] == 1
        assert data["stats"]["rollbacks"] == 1
