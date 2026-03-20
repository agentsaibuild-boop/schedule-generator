"""Unit tests for _ensure_schedule_list — schedule data normalisation in app.py.

Covers: list passthrough, dict with tasks key, JSON string, markdown-fenced JSON,
empty/None inputs, and the enriched-dict bug (enrich_for_msproject returns dict).

FAILURE означава: app.py :: _ensure_schedule_list е счупена — Gantt диаграмата
показва празно след успешно генериране (enriched schedule е dict, не list).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Import the function directly from app module
import importlib
import types

# We only need _ensure_schedule_list; avoid running Streamlit on import
_app_path = Path(__file__).parent.parent / "app.py"
_source = _app_path.read_text(encoding="utf-8")
# Extract only the function definition (stop before Streamlit calls)
_func_lines = []
_in_func = False
for line in _source.splitlines():
    if line.startswith("def _ensure_schedule_list("):
        _in_func = True
    if _in_func:
        _func_lines.append(line)
        # Function ends at first non-indented non-empty line after start
        if _func_lines and len(_func_lines) > 1 and line and not line[0].isspace():
            _func_lines.pop()  # remove the line that ended the function
            break

_func_src = "\n".join(_func_lines)
_ns: dict = {"json": json}
exec(_func_src, _ns)  # noqa: S102
_ensure_schedule_list = _ns["_ensure_schedule_list"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def _task(id_: str, name: str) -> dict:
    return {"id": id_, "name": name, "duration": 5, "start_day": 0}


def test_list_passthrough():
    """list[dict] must be returned unchanged."""
    tasks = [_task("1", "Изкоп")]
    result = _ensure_schedule_list(tasks)
    assert result is tasks


def test_dict_with_tasks_returns_tasks():
    """dict with 'tasks' key (enriched schedule from enrich_for_msproject) must return tasks."""
    tasks = [_task("1", "Монтаж"), _task("2", "Засипване")]
    enriched = {"tasks": tasks, "milestones": [], "msp_notes": ""}
    result = _ensure_schedule_list(enriched)
    assert result == tasks, f"Expected tasks list, got: {result}"


def test_dict_without_tasks_returns_empty():
    """dict missing 'tasks' key must return empty list (not raise)."""
    result = _ensure_schedule_list({"project_name": "Тест"})
    assert result == []


def test_json_string_list():
    """JSON string representing a list must be parsed and returned."""
    tasks = [_task("1", "Хидравлична проба")]
    result = _ensure_schedule_list(json.dumps(tasks))
    assert result == tasks


def test_json_string_dict_with_tasks():
    """JSON string of a dict with 'tasks' must return the task list."""
    tasks = [_task("1", "Дезинфекция")]
    data = json.dumps({"tasks": tasks, "total_duration": 5})
    result = _ensure_schedule_list(data)
    assert result == tasks


def test_markdown_fenced_json():
    """JSON inside markdown code fence must be parsed correctly."""
    tasks = [_task("1", "Настилки")]
    fenced = "```json\n" + json.dumps(tasks) + "\n```"
    result = _ensure_schedule_list(fenced)
    assert result == tasks


def test_none_returns_empty():
    """None input must return empty list without raising."""
    assert _ensure_schedule_list(None) == []


def test_empty_string_returns_empty():
    """Empty/whitespace string must return empty list."""
    assert _ensure_schedule_list("") == []
    assert _ensure_schedule_list("   ") == []


def test_invalid_json_returns_empty():
    """Malformed JSON string must return empty list without raising."""
    assert _ensure_schedule_list("{not valid json}") == []


def test_integer_returns_empty():
    """Unexpected int input must return empty list without raising."""
    assert _ensure_schedule_list(42) == []


if __name__ == "__main__":
    tests = [
        test_list_passthrough,
        test_dict_with_tasks_returns_tasks,
        test_dict_without_tasks_returns_empty,
        test_json_string_list,
        test_json_string_dict_with_tasks,
        test_markdown_fenced_json,
        test_none_returns_empty,
        test_empty_string_returns_empty,
        test_invalid_json_returns_empty,
        test_integer_returns_empty,
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
