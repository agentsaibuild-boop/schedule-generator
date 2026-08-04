"""Unit tests for ChatHandler._format_duration_report — видимост на преизчислението.

Covers: празен изход при пропусната/непроменяща стъпка, обобщение, списък
        промени, отрязване след 8 реда, предупреждения.

FAILURE означава: src/chat_handler.py :: _format_duration_report е счупен —
детерминистичната замяна на продължителностите става ТИХА.  Потребителят вижда
числа, различни от обявените от AI-я, без обяснение защо.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.chat_handler import ChatHandler


def _report(changes: list[dict], **summary_overrides) -> dict:
    summary = {
        "total": 10,
        "recomputed": len(changes),
        "unchanged": 0,
        "skipped": 3,
        "old_total_duration": 169,
        "new_total_duration": 332,
    }
    summary.update(summary_overrides)
    return {
        "duration_report": {
            "applied": True,
            "changes": changes,
            "skipped": [],
            "warnings": [],
            "summary": summary,
        }
    }


def _change(tid: str = "В01", old: int = 23, new: int = 48) -> dict:
    return {
        "id": tid,
        "name": "Полагане DN500 PE",
        "old": old,
        "new": new,
        "delta": new - old,
        "reason": "720м ÷ 15 м/ден → 48д [DN500_PE_open]",
    }


def test_missing_report_yields_nothing():
    assert ChatHandler._format_duration_report({}) == []


def test_not_applied_yields_nothing():
    result = ChatHandler._format_duration_report(
        {"duration_report": {"applied": False, "reason": "няма tasks"}}
    )
    assert result == []


def test_no_changes_yields_nothing():
    """Ако AI-ят вече е бил точен, няма какво да се съобщава."""
    assert ChatHandler._format_duration_report(_report([])) == []


def test_reports_recomputed_count():
    lines = ChatHandler._format_duration_report(_report([_change()]))
    assert any("1 задачи" in line for line in lines)
    assert any("productivities.json" in line for line in lines)


def test_reports_total_duration_shift():
    lines = ChatHandler._format_duration_report(_report([_change()]))
    assert any("169д → **332д**" in line for line in lines)


def test_omits_total_when_unchanged():
    lines = ChatHandler._format_duration_report(
        _report([_change()], old_total_duration=100, new_total_duration=100)
    )
    assert not any("Обща продължителност" in line for line in lines)


def test_lists_individual_changes_with_reason():
    lines = ChatHandler._format_duration_report(_report([_change()]))
    body = "\n".join(lines)
    assert "В01" in body
    assert "23д → 48д" in body
    assert "720м ÷ 15 м/ден" in body


def test_truncates_after_eight_changes():
    changes = [_change(f"В{i:02d}") for i in range(12)]
    lines = ChatHandler._format_duration_report(_report(changes))
    listed = [line for line in lines if line.strip().startswith("- В")]
    assert len(listed) == 8
    assert any("още 4" in line for line in lines)


def test_no_truncation_note_at_exactly_eight():
    changes = [_change(f"В{i:02d}") for i in range(8)]
    lines = ChatHandler._format_duration_report(_report(changes))
    assert not any("още" in line for line in lines)


def test_includes_warnings():
    report = _report([_change()])
    report["duration_report"]["warnings"] = ["Датите не са преизчислени — кръгова зависимост."]
    lines = ChatHandler._format_duration_report(report)
    assert any("кръгова зависимост" in line for line in lines)


def test_reports_skipped_count():
    lines = ChatHandler._format_duration_report(_report([_change()]))
    assert any("пропуснати 3" in line for line in lines)
