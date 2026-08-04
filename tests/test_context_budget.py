"""Unit tests: документното съдържание се побира в контекста прозрачно.

BACKLOG т.2: лимитът беше 120 000 знака, зашит в кода, и се режеше по
позиция — насред изречение, без да се каже кое е отпаднало.

Проверено 2026-07-23: това е ~21% от контекста на текущия модел
(deepseek/deepseek-chat — 163 840 токена ≈ 573 000 знака).  Ограничението
беше самоналожено, не моделно.

Последица: при голям пакет КСС-то можеше изобщо да не стигне до модела,
защото е трето по азбучен ред — и никой не научаваше.

FAILURE означава: документи пак изчезват тихо по пътя към AI-я.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai_processor import DOC_CONTEXT_CHAR_BUDGET, AIProcessor  # noqa: E402
from src.chat_handler import ChatHandler  # noqa: E402
from src.file_manager import FileManager  # noqa: E402


def _doc(name: str, size: int) -> str:
    return f"=== {name} ===\n" + ("x" * size) + "\n"


# ===================================================================
# Бюджетът
# ===================================================================

def test_budget_is_far_above_the_old_limit():
    """Старите 120 000 знака ползваха ~21% от контекста на модела."""
    assert DOC_CONTEXT_CHAR_BUDGET > 120_000


def test_budget_leaves_room_for_the_system_prompt():
    """Системният промпт е ~32 000 знака; бюджетът не бива да заема всичко."""
    deepseek_context_chars = 163_840 * 3.5
    assert DOC_CONTEXT_CHAR_BUDGET < deepseek_context_chars * 0.85


# ===================================================================
# _fit_to_context
# ===================================================================

def test_short_text_passes_through_untouched():
    text = _doc("КСС.xlsx", 1000)
    fitted, report = AIProcessor._fit_to_context(text)
    assert fitted == text
    assert report["truncated"] is False


def test_oversized_text_is_truncated():
    text = _doc("A.pdf", DOC_CONTEXT_CHAR_BUDGET) + _doc("B.pdf", 1000)
    _, report = AIProcessor._fit_to_context(text)
    assert report["truncated"] is True


def test_truncation_happens_on_document_boundaries():
    """Не бива да се реже насред таблица."""
    first = _doc("КСС.xlsx", DOC_CONTEXT_CHAR_BUDGET - 500)
    second = _doc("Договор.pdf", 5000)
    fitted, _ = AIProcessor._fit_to_context(first + second)

    assert fitted.startswith("=== КСС.xlsx ===")
    assert "=== Договор.pdf ===" not in fitted


def test_dropped_documents_are_named():
    text = (_doc("КСС.xlsx", DOC_CONTEXT_CHAR_BUDGET - 100)
            + _doc("Договор.pdf", 5000)
            + _doc("Ситуация.pdf", 5000))
    _, report = AIProcessor._fit_to_context(text)
    assert "Договор.pdf" in report["dropped_documents"]
    assert "Ситуация.pdf" in report["dropped_documents"]


def test_prompt_states_what_was_dropped():
    """AI-ят трябва да знае, че не вижда всичко."""
    text = (_doc("КСС.xlsx", DOC_CONTEXT_CHAR_BUDGET - 100)
            + _doc("Договор.pdf", 5000))
    fitted, _ = AIProcessor._fit_to_context(text)
    assert "НЕ са включени" in fitted
    assert "Договор.pdf" in fitted


def test_first_document_is_always_kept():
    text = _doc("КСС.xlsx", 2000) + _doc("Голям.pdf", DOC_CONTEXT_CHAR_BUDGET)
    fitted, _ = AIProcessor._fit_to_context(text)
    assert "=== КСС.xlsx ===" in fitted


def test_single_oversized_document_is_cut_but_reported():
    text = _doc("Огромен.pdf", DOC_CONTEXT_CHAR_BUDGET * 2)
    fitted, report = AIProcessor._fit_to_context(text)
    assert report["truncated"] is True
    assert len(fitted) <= DOC_CONTEXT_CHAR_BUDGET + 500
    assert report["dropped_documents"]


def test_report_counts_characters():
    text = _doc("A.pdf", 500)
    _, report = AIProcessor._fit_to_context(text)
    assert report["chars"] == len(text)


# ===================================================================
# Приоритет по роля
# ===================================================================

def _project(tmp_path: Path, docs: dict[str, str]) -> FileManager:
    converted = tmp_path / "converted"
    converted.mkdir(parents=True)
    for name, text in docs.items():
        (converted / f"{Path(name).stem}.json").write_text(
            json.dumps({"source_file": name, "full_text": text}, ensure_ascii=False),
            encoding="utf-8",
        )
    manager = FileManager()
    manager.base_path = tmp_path
    return manager


def test_priority_document_comes_first(tmp_path):
    """КСС-то е трето по азбука — но първо по важност."""
    manager = _project(tmp_path, {
        "Aнекс.pdf": "анекс",
        "Bдоговор.pdf": "договор",
        "КСС.xlsx": "количества",
    })
    text = manager.get_all_text(priority=["КСС.xlsx"])
    assert text.index("КСС.xlsx") < text.index("Aнекс.pdf")


def test_without_priority_order_is_alphabetical(tmp_path):
    manager = _project(tmp_path, {"Bдоговор.pdf": "б", "Aнекс.pdf": "а"})
    text = manager.get_all_text()
    assert text.index("Aнекс.pdf") < text.index("Bдоговор.pdf")


def test_all_documents_still_included_with_priority(tmp_path):
    manager = _project(tmp_path, {
        "КСС.xlsx": "количества", "Договор.pdf": "договор", "Ситуация.pdf": "ситуация",
    })
    text = manager.get_all_text(priority=["КСС.xlsx"])
    for name in ("КСС.xlsx", "Договор.pdf", "Ситуация.pdf"):
        assert name in text


def test_unknown_priority_name_is_harmless(tmp_path):
    manager = _project(tmp_path, {"КСС.xlsx": "количества"})
    assert "КСС.xlsx" in manager.get_all_text(priority=["НЯМА.pdf"])


# ===================================================================
# Видимост за потребителя
# ===================================================================

def test_no_warning_when_nothing_was_dropped():
    assert ChatHandler._format_truncation_warning({"truncation": {"truncated": False}}) == []


def test_no_warning_when_no_truncation_key():
    assert ChatHandler._format_truncation_warning({}) == []


def test_warning_names_the_dropped_documents():
    lines = ChatHandler._format_truncation_warning({
        "truncation": {"truncated": True, "chars": 400_000, "total_chars": 600_000,
                       "dropped_documents": ["Договор.pdf", "Приложение 3.pdf"]}
    })
    body = "\n".join(lines)
    assert "Договор.pdf" in body
    assert "Приложение 3.pdf" in body


def test_warning_shows_how_much_was_used():
    lines = ChatHandler._format_truncation_warning({
        "truncation": {"truncated": True, "chars": 400_000, "total_chars": 600_000,
                       "dropped_documents": ["X.pdf"]}
    })
    body = "\n".join(lines)
    assert "400,000" in body and "600,000" in body


def test_warning_truncates_long_lists():
    lines = ChatHandler._format_truncation_warning({
        "truncation": {"truncated": True, "chars": 1, "total_chars": 2,
                       "dropped_documents": [f"Ф{i}.pdf" for i in range(10)]}
    })
    assert any("още 4" in ln for ln in lines)


def test_warning_explains_the_priority_rule():
    lines = ChatHandler._format_truncation_warning({
        "truncation": {"truncated": True, "chars": 1, "total_chars": 2,
                       "dropped_documents": ["X.pdf"]}
    })
    assert any("КСС" in ln for ln in lines)


# ===================================================================
# Регресия
# ===================================================================

def test_old_hardcoded_limit_is_gone():
    source = (Path(__file__).parent.parent / "src" / "ai_processor.py").read_text(
        encoding="utf-8")
    assert "all_text[:120_000]" not in source


def test_pipeline_passes_priority_to_get_all_text():
    source = (Path(__file__).parent.parent / "src" / "chat_handler.py").read_text(
        encoding="utf-8")
    assert "get_all_text(priority=" in source
