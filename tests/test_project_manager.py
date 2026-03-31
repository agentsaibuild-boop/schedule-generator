"""Unit tests for ProjectManager.

Covers: get_time_ago, get_status_label, get_welcome_message,
register_project, save_progress, get_recent_projects, get_last_active_project.

FAILURE означава: src/project_manager.py е счупен —
историята на проектите, time-ago форматирането или
welcome съобщенията ще показват грешни данни на потребителя.
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.project_manager import ProjectManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pm(tmp_path: str) -> ProjectManager:
    """Create a ProjectManager backed by a temp directory."""
    return ProjectManager(tmp_path)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _ago(seconds: int) -> str:
    """Return ISO timestamp that is `seconds` in the past."""
    return _iso(datetime.now(tz=timezone.utc) - timedelta(seconds=seconds))


# ---------------------------------------------------------------------------
# get_time_ago
# ---------------------------------------------------------------------------

def test_time_ago_empty_string_returns_unknown():
    assert ProjectManager.get_time_ago("") == "неизвестно"


def test_time_ago_invalid_string_returns_unknown():
    assert ProjectManager.get_time_ago("not-a-date") == "неизвестно"


def test_time_ago_just_now_under_60s():
    assert ProjectManager.get_time_ago(_ago(30)) == "току-що"


def test_time_ago_minutes():
    result = ProjectManager.get_time_ago(_ago(300))  # 5 min
    assert result == "преди 5 мин."


def test_time_ago_one_hour():
    result = ProjectManager.get_time_ago(_ago(3700))
    assert result == "днес"


def test_time_ago_multiple_hours():
    result = ProjectManager.get_time_ago(_ago(7200))  # 2 hours
    assert result == "днес"


def test_time_ago_yesterday():
    result = ProjectManager.get_time_ago(_ago(90000))  # ~25 hours
    assert result == "вчера"


def test_time_ago_days():
    result = ProjectManager.get_time_ago(_ago(3 * 86400))
    assert result == "преди 3 дни"


def test_time_ago_one_week():
    result = ProjectManager.get_time_ago(_ago(8 * 86400))
    assert result == "преди 1 седмица"


def test_time_ago_multiple_weeks():
    result = ProjectManager.get_time_ago(_ago(14 * 86400))
    assert result == "преди 2 седмици"


def test_time_ago_months():
    result = ProjectManager.get_time_ago(_ago(35 * 86400))
    assert "месец" in result


# ---------------------------------------------------------------------------
# get_status_label
# ---------------------------------------------------------------------------

def test_status_label_known_statuses():
    assert ProjectManager.get_status_label("new") == "Нов"
    assert ProjectManager.get_status_label("analyzed") == "Анализиран"
    assert ProjectManager.get_status_label("schedule_generated") == "График готов"
    assert ProjectManager.get_status_label("exported") == "Експортиран"


def test_status_label_unknown_returns_status_itself():
    assert ProjectManager.get_status_label("foobar") == "foobar"


# ---------------------------------------------------------------------------
# get_welcome_message
# ---------------------------------------------------------------------------

def test_welcome_new_project_mentions_file_count(tmp_path):
    pm = _make_pm(str(tmp_path))
    msg = pm.get_welcome_message({
        "name": "Плевен",
        "path": "/tmp/plevn",
        "status": "new",
        "progress": {"files_total": 7, "files_converted": 0},
    })
    assert "Плевен" in msg
    assert "7" in msg


def test_welcome_schedule_generated_mentions_tasks_and_days(tmp_path):
    pm = _make_pm(str(tmp_path))
    schedule = [
        {"id": "1", "start_day": 1, "duration": 10, "end_day": 11},
        {"id": "2", "start_day": 11, "duration": 20, "end_day": 31},
    ]
    msg = pm.get_welcome_message({
        "name": "Обект А",
        "path": "/tmp/a",
        "status": "schedule_generated",
        "last_accessed": _ago(7200),
        "progress": {
            "last_schedule": schedule,
            "schedule_version": "v2",
            "files_total": 3,
            "files_converted": 3,
        },
    })
    assert "2" in msg      # task count
    assert "31" in msg     # total days
    assert "v2" in msg


def test_welcome_exported_mentions_export_type(tmp_path):
    pm = _make_pm(str(tmp_path))
    msg = pm.get_welcome_message({
        "name": "Враца",
        "path": "/tmp/vr",
        "status": "exported",
        "progress": {
            "exports": [{"type": "PDF", "date": "2026-03-01"}],
        },
    })
    assert "PDF" in msg


def test_welcome_unknown_status_fallback(tmp_path):
    pm = _make_pm(str(tmp_path))
    msg = pm.get_welcome_message({
        "name": "X",
        "path": "/tmp/x",
        "status": "mystery_state",
        "progress": {},
    })
    assert "X" in msg


# ---------------------------------------------------------------------------
# register_project
# ---------------------------------------------------------------------------

def test_register_project_creates_entry(tmp_path):
    pm = _make_pm(str(tmp_path))
    proj = pm.register_project(str(tmp_path), name="TestProject")
    assert proj["name"] == "TestProject"
    assert proj["status"] == "new"
    assert proj["stats"]["total_sessions"] == 1


def test_register_project_id_is_deterministic(tmp_path):
    pm = _make_pm(str(tmp_path))
    p1 = pm.register_project(str(tmp_path), name="A")
    p2 = pm.register_project(str(tmp_path), name="A")
    assert p1["id"] == p2["id"]


def test_register_project_increments_sessions_on_revisit(tmp_path):
    pm = _make_pm(str(tmp_path))
    pm.register_project(str(tmp_path))
    proj = pm.register_project(str(tmp_path))
    assert proj["stats"]["total_sessions"] == 2


def test_register_project_defaults_name_to_folder(tmp_path):
    pm = _make_pm(str(tmp_path))
    proj = pm.register_project(str(tmp_path))
    assert proj["name"] == tmp_path.name


# ---------------------------------------------------------------------------
# get_recent_projects
# ---------------------------------------------------------------------------

def test_get_recent_projects_empty_db(tmp_path):
    pm = _make_pm(str(tmp_path))
    assert pm.get_recent_projects() == []


def test_get_recent_projects_respects_limit(tmp_path):
    pm = _make_pm(str(tmp_path))
    for i in range(6):
        sub = tmp_path / f"proj{i}"
        sub.mkdir()
        pm.register_project(str(sub), name=f"P{i}")
    recent = pm.get_recent_projects(limit=3)
    assert len(recent) == 3


def test_get_recent_projects_adds_ui_fields(tmp_path):
    pm = _make_pm(str(tmp_path))
    sub = tmp_path / "myproj"
    sub.mkdir()
    pm.register_project(str(sub))
    recent = pm.get_recent_projects()
    assert len(recent) == 1
    assert "exists" in recent[0]
    assert "status_label" in recent[0]
    assert "time_ago" in recent[0]


# ---------------------------------------------------------------------------
# save_progress
# ---------------------------------------------------------------------------

def test_save_progress_updates_status(tmp_path):
    pm = _make_pm(str(tmp_path))
    proj = pm.register_project(str(tmp_path))
    pm.save_progress(proj["id"], {"status": "analyzed"})
    loaded = pm.projects["projects"][proj["id"]]
    assert loaded["status"] == "analyzed"


def test_save_progress_accumulates_ai_cost(tmp_path):
    pm = _make_pm(str(tmp_path))
    proj = pm.register_project(str(tmp_path))
    pm.save_progress(proj["id"], {"ai_cost": 0.05})
    pm.save_progress(proj["id"], {"ai_cost": 0.03})
    loaded = pm.projects["projects"][proj["id"]]
    assert abs(loaded["stats"]["total_ai_cost"] - 0.08) < 1e-9


def test_save_progress_tracks_exports(tmp_path):
    pm = _make_pm(str(tmp_path))
    proj = pm.register_project(str(tmp_path))
    pm.save_progress(proj["id"], {"export": {"type": "PDF", "date": "2026-03-20"}})
    pm.save_progress(proj["id"], {"export": {"type": "XML", "date": "2026-03-20"}})
    loaded = pm.projects["projects"][proj["id"]]
    assert len(loaded["progress"]["exports"]) == 2


# ---------------------------------------------------------------------------
# get_last_active_project / clear_last_active
# ---------------------------------------------------------------------------

def test_get_last_active_returns_none_when_empty(tmp_path):
    pm = _make_pm(str(tmp_path))
    assert pm.get_last_active_project() is None


def test_clear_last_active_removes_marker(tmp_path):
    pm = _make_pm(str(tmp_path))
    pm.register_project(str(tmp_path))
    assert pm.get_last_active_project() is not None
    pm.clear_last_active()
    assert pm.get_last_active_project() is None


# ---------------------------------------------------------------------------
# Persistence: data survives reload
# ---------------------------------------------------------------------------

def test_projects_persist_after_reload(tmp_path):
    pm1 = _make_pm(str(tmp_path))
    proj = pm1.register_project(str(tmp_path), name="Постоянен")
    pm1.save_progress(proj["id"], {"status": "analyzed"})

    # Create a new ProjectManager instance pointing to same dir
    pm2 = _make_pm(str(tmp_path))
    reloaded = pm2.projects["projects"].get(proj["id"])
    assert reloaded is not None
    assert reloaded["status"] == "analyzed"
    assert reloaded["name"] == "Постоянен"
