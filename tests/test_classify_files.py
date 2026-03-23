"""Unit tests for FileManager.classify_files() — keyword-based classification.

Covers: КСС detection, situation priority, useful files, unknown fallback,
situation path collection, empty directory, and priority ordering.

FAILURE означава: src/file_manager.py :: classify_files е счупена —
генераторът ще пропусне КСС файл или ще блокира с can_proceed=False
при валидна тендерна документация.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.file_manager import FileManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_paths(*names: str) -> list[Path]:
    """Create fake Path objects with the given file names (no real I/O)."""
    return [Path(f"/fake/project/{name}") for name in names]


def _classify(file_names: list[str]) -> dict:
    """Run classify_files with a patched _list_supported_files."""
    fm = FileManager()
    fake_paths = _make_paths(*file_names)
    with patch.object(fm, "_list_supported_files", return_value=fake_paths):
        return fm.classify_files(ai_processor=None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_kss_keyword_required_can_proceed():
    """File with 'ксс' in name → required=True, can_proceed=True."""
    result = _classify(["КСС_обект_А.xlsx"])
    assert result["can_proceed"] is True
    assert "КСС_обект_А.xlsx" in result["required"]
    assert result["ai_used"] is False


def test_kolichestvena_smetka_is_required():
    """File with 'количествен' in name (case-insensitive) → required."""
    result = _classify(["Количествено_оценъчна_сметка.xlsx"])
    assert result["can_proceed"] is True
    assert len(result["required"]) == 1


def test_boq_english_is_required():
    """File with 'boq' in name → required (English тендерна документация)."""
    result = _classify(["BOQ_main.xlsx"])
    assert result["can_proceed"] is True
    assert "BOQ_main.xlsx" in result["required"]


def test_situation_file_classified_correctly():
    """File with 'ситуация' → situation list, not required."""
    result = _classify(["ситуация_трасе.pdf"])
    assert "ситуация_трасе.pdf" in result["situation"]
    assert "ситуация_трасе.pdf" not in result["required"]


def test_situation_paths_are_absolute():
    """situation_paths contains full absolute paths for AI vision access."""
    result = _classify(["трасировъчен_план.pdf"])
    assert len(result["situation_paths"]) == 1
    assert result["situation_paths"][0].endswith("трасировъчен_план.pdf")


def test_useful_file_classified_correctly():
    """File with 'технич' → useful list."""
    result = _classify(["технически_проект.pdf"])
    assert "технически_проект.pdf" in result["useful"]
    assert "технически_проект.pdf" not in result["required"]
    assert "технически_проект.pdf" not in result["unknown"]


def test_unknown_file_goes_to_unknown():
    """File with no matching keywords → unknown list."""
    result = _classify(["снимки_на_обекта.jpg"])
    assert "снимки_на_обекта.jpg" in result["unknown"]
    assert result["can_proceed"] is False


def test_empty_file_list_cannot_proceed():
    """No files → can_proceed=False, all lists empty."""
    result = _classify([])
    assert result["can_proceed"] is False
    assert result["required"] == []
    assert result["useful"] == []
    assert result["situation"] == []
    assert result["unknown"] == []


def test_situation_takes_priority_over_required_keywords():
    """File name containing both 'ситуация' and 'ксс' → classified as situation, not required."""
    result = _classify(["ситуация_ксс_overlap.pdf"])
    assert "ситуация_ксс_overlap.pdf" in result["situation"]
    assert "ситуация_ксс_overlap.pdf" not in result["required"]
    # can_proceed is False since no pure required file found
    assert result["can_proceed"] is False


def test_mixed_files_correct_routing():
    """Multiple files → each routed to the right bucket."""
    result = _classify([
        "КСС_финал.xlsx",          # required
        "техническа_спецификация.pdf",  # useful
        "ситуация_1.pdf",          # situation
        "снимки.zip",              # unknown
    ])
    assert result["can_proceed"] is True
    assert "КСС_финал.xlsx" in result["required"]
    assert "техническа_спецификация.pdf" in result["useful"]
    assert "ситуация_1.pdf" in result["situation"]
    assert "снимки.zip" in result["unknown"]
    assert result["ai_used"] is False


def test_multiple_required_files():
    """Two КСС files → both in required, can_proceed=True."""
    result = _classify(["КСС_водопровод.xlsx", "КСС_канализация.xlsx"])
    assert result["can_proceed"] is True
    assert len(result["required"]) == 2


# ---------------------------------------------------------------------------
# AI fallback path tests (no real API calls — router is mocked)
# ---------------------------------------------------------------------------

def _make_mock_router(content: str):
    """Build a minimal mock router whose chat() returns the given content string."""
    from unittest.mock import MagicMock
    router = MagicMock()
    router.chat.return_value = {"content": content}
    # Use the real parse_json_response from AIRouter
    from src.ai_router import AIRouter
    router.parse_json_response.side_effect = AIRouter.parse_json_response
    return router


def _make_mock_ai_processor(content: str):
    """Build a minimal mock ai_processor wrapping a mock router."""
    from unittest.mock import MagicMock
    ai = MagicMock()
    ai.router = _make_mock_router(content)
    return ai


def _classify_with_ai(file_names: list[str], ai_content: str) -> dict:
    """Run classify_files with no keyword match, triggering AI fallback."""
    fm = FileManager()
    # Use names that don't match any keyword → AI fallback is triggered
    fake_paths = _make_paths(*file_names)
    ai = _make_mock_ai_processor(ai_content)
    with patch.object(fm, "_list_supported_files", return_value=fake_paths):
        return fm.classify_files(ai_processor=ai)


def test_ai_fallback_plain_json():
    """AI returns plain JSON → classify_files uses it and sets ai_used=True."""
    import json
    payload = json.dumps({
        "required": ["проект_А.xlsx"],
        "useful": [],
        "situation": [],
        "unknown": ["снимки.zip"],
    })
    result = _classify_with_ai(["проект_А.xlsx", "снимки.zip"], payload)
    assert result["ai_used"] is True
    assert result["can_proceed"] is True
    assert "проект_А.xlsx" in result["required"]


def test_ai_fallback_markdown_wrapped_json():
    """AI wraps response in ```json ... ``` → parse_json_response strips it correctly."""
    import json
    inner = json.dumps({
        "required": ["разчет.xlsx"],
        "useful": ["договор.pdf"],
        "situation": [],
        "unknown": [],
    })
    markdown_content = f"```json\n{inner}\n```"
    result = _classify_with_ai(["разчет.xlsx", "договор.pdf"], markdown_content)
    assert result["ai_used"] is True
    assert result["can_proceed"] is True
    assert "разчет.xlsx" in result["required"]
    assert "договор.pdf" in result["useful"]


def test_ai_fallback_invalid_json_degrades_gracefully():
    """AI returns garbage → parse_json_response returns fallback dict (no keys match),
    so required=[] and can_proceed=False. ai_used=True because we did attempt AI."""
    result = _classify_with_ai(["непознат.zip"], "ГРЕШКА: не мога да класифицирам")
    assert result["can_proceed"] is False
    assert result["required"] == []
    # ai_used=True because the AI call itself succeeded; only the JSON was unparseable
    assert result["ai_used"] is True


def test_ai_fallback_empty_required_sets_can_proceed_false():
    """AI returns valid JSON but required=[] → can_proceed=False."""
    import json
    payload = json.dumps({
        "required": [],
        "useful": ["договор.pdf"],
        "situation": [],
        "unknown": [],
    })
    result = _classify_with_ai(["договор.pdf"], payload)
    assert result["ai_used"] is True
    assert result["can_proceed"] is False


if __name__ == "__main__":
    tests = [
        test_kss_keyword_required_can_proceed,
        test_kolichestvena_smetka_is_required,
        test_boq_english_is_required,
        test_situation_file_classified_correctly,
        test_situation_paths_are_absolute,
        test_useful_file_classified_correctly,
        test_unknown_file_goes_to_unknown,
        test_empty_file_list_cannot_proceed,
        test_situation_takes_priority_over_required_keywords,
        test_mixed_files_correct_routing,
        test_multiple_required_files,
        test_ai_fallback_plain_json,
        test_ai_fallback_markdown_wrapped_json,
        test_ai_fallback_invalid_json_degrades_gracefully,
        test_ai_fallback_empty_required_sets_can_proceed_false,
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
            print(f"  ERROR {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
