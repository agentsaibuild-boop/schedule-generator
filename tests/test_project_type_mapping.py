"""Unit tests: типът проект от анализа намира своята методология.

ЖИВ ПРОГОН 2026-08-06: промптът иска типът да е на БЪЛГАРСКИ
('разпределителна мрежа', 'довеждащ', 'единичен', 'инженеринг'), а картата на
методологиите беше с английски ключове.  Реалният търг се класифицира като
'инженеринг' и в системния промпт влизаше редът

    === METHODOLOGY (инженеринг) ===
    Unknown project type: инженеринг

тоест моделът получаваше СОБСТВЕНАТА грешка на кода вместо методологията за
инженеринг проекти — тихо, без нито едно съобщение.

FAILURE означава: графикът се генерира без методологията за своя тип, а никой
не разбира — нито в лога, нито в промпта.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.knowledge_manager import KnowledgeManager  # noqa: E402

_KNOWLEDGE = str(Path(__file__).parent.parent / "knowledge")


@pytest.fixture
def km() -> KnowledgeManager:
    return KnowledgeManager(_KNOWLEDGE)


# Точните низове, които промптът в `analyze_documents` иска от модела.
_BG_TYPES = ["разпределителна мрежа", "довеждащ", "единичен", "инженеринг"]


@pytest.mark.parametrize("bg_type", _BG_TYPES)
def test_bulgarian_type_from_analysis_finds_methodology(km, bg_type):
    result = km.get_methodology(bg_type)
    assert "Unknown project type" not in result
    assert "not found" not in result
    assert len(result) > 200, "методологията трябва да е реален текст"


@pytest.mark.parametrize("bg_type,expected", [
    ("разпределителна мрежа", "distribution"),
    ("довеждащ", "supply"),
    ("единичен", "single"),
    ("инженеринг", "engineering"),
    ("ИНЖЕНЕРИНГ", "engineering"),
    ("  инженеринг  ", "engineering"),
])
def test_canonical_type(bg_type, expected):
    assert KnowledgeManager.canonical_type(bg_type) == expected


@pytest.mark.parametrize("en_type", ["distribution", "supply", "single", "engineering"])
def test_english_keys_still_work(km, en_type):
    assert "Unknown project type" not in km.get_methodology(en_type)


def test_unknown_type_is_still_reported(km):
    assert "Unknown project type" in km.get_methodology("нещо си")
    assert KnowledgeManager.canonical_type("нещо си") == ""
    assert KnowledgeManager.canonical_type(None) == ""


def test_prompt_never_carries_the_error_string_as_methodology(km):
    """Непознат тип → секцията ЛИПСВА, вместо да носи грешката в промпта."""
    prompt = km.get_all_knowledge_for_prompt(project_type="нещо си", level="full")
    assert "Unknown project type" not in prompt
    assert "METHODOLOGY" not in prompt


def test_known_bulgarian_type_puts_methodology_in_the_prompt(km):
    prompt = km.get_all_knowledge_for_prompt(project_type="инженеринг", level="full")
    assert "=== METHODOLOGY (engineering) ===" in prompt
    assert "Unknown project type" not in prompt


def test_verification_prompt_has_the_same_guarantee(km):
    ok = km.get_all_knowledge_for_prompt(project_type="инженеринг", level="verification")
    assert "=== METHODOLOGY (engineering) ===" in ok
    bad = km.get_all_knowledge_for_prompt(project_type="нещо си", level="verification")
    assert "Unknown project type" not in bad
