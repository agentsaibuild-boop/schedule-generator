"""Unit tests for lesson parsing and relevance retrieval (P3).

Covers: get_lesson_blocks (заглавие + тяло + раздел), токенизация със
        стемване за български, rank_lessons (TF-IDF), select_lessons
        (бюджет знаци) и секцията с уроци в промпта.

FAILURE означава: src/knowledge_manager.py :: подборът на уроци е счупен.
Последици: в промпта влизат само заглавия без телата (числата и причините
изчезват), или се подбират последните 20 по ред във файла — при което
генераторът получава бележки за разработчика (#26 PowerShell -STA) и губи
домейн знанието (#09 дезинфекция per section, #17 терен фактори, #35 CI vs PE).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.knowledge_manager import (  # noqa: E402
    KnowledgeManager,
    rank_lessons,
    select_lessons,
)

REPO = Path(__file__).parent.parent


@pytest.fixture()
def km() -> KnowledgeManager:
    return KnowledgeManager(str(REPO / "knowledge"))


@pytest.fixture()
def blocks(km: KnowledgeManager) -> list[dict]:
    return km.get_lesson_blocks()


def _fake_blocks() -> list[dict]:
    def block(num: int, title: str, body: str) -> dict:
        return {
            "number": num, "title": f"#{num:02d}: {title}", "body": body,
            "section": "тест", "text": f"#{num:02d}: {title}\n{body}",
        }

    return [
        block(1, "Дезинфекция на водопровод",
              "Дезинфекцията се прави след хидравличната проба на участъка."),
        block(2, "PowerShell folder dialog",
              "Изисква -STA флаг, иначе COM грешка при отваряне на диалога."),
        block(3, "Чугунени тръби",
              "Чугунът е по-бавен от полиетилена заради муфите и теглото."),
    ]


# ===================================================================
# get_lesson_blocks — заглавие + ТЯЛО
# ===================================================================

def test_parses_all_lessons(blocks, km):
    assert len(blocks) == len(km.get_lessons())
    assert len(blocks) > 40


def test_block_has_body_not_just_title(blocks):
    """Ядрото на P3: тялото на урока трябва да стига до промпта."""
    for block in blocks:
        assert block["body"], f"Урок {block['title']} е без тяло"


def test_body_contains_the_actual_knowledge(blocks):
    """Числата и причините живеят в тялото, не в заглавието."""
    lesson_17 = next(b for b in blocks if b["number"] == 17)
    assert "0.75" in lesson_17["body"]
    assert "0.6" in lesson_17["body"]
    assert "0.75" not in lesson_17["title"]


def test_block_records_number(blocks):
    numbers = [b["number"] for b in blocks]
    assert numbers[0] == 1
    assert numbers == sorted(numbers)


def test_block_records_section(blocks):
    lesson_09 = next(b for b in blocks if b["number"] == 9)
    assert "Типове проекти" in lesson_09["section"]


def test_format_heading_is_not_treated_as_section(blocks):
    """'## Формат' е служебно заглавие, не раздел от знанието."""
    assert all(b["section"].lower() != "формат" for b in blocks)


def test_text_combines_title_and_body(blocks):
    block = blocks[0]
    assert block["title"] in block["text"]
    assert block["body"] in block["text"]


def test_missing_file_returns_empty(tmp_path):
    empty = KnowledgeManager(str(tmp_path))
    assert empty.get_lesson_blocks() == []


def test_horizontal_rule_ends_a_block(blocks):
    """'---' разделя раздели — не бива да влиза в тялото на урока."""
    assert all("---" not in b["body"] for b in blocks)


# ===================================================================
# rank_lessons
# ===================================================================

def test_ranking_puts_relevant_lesson_first():
    ranked = rank_lessons(_fake_blocks(), "дезинфекция на водопровода")
    assert ranked[0][1]["number"] == 1


def test_ranking_ignores_irrelevant_lesson():
    ranked = rank_lessons(_fake_blocks(), "дезинфекция")
    powershell = next(s for s, b in ranked if b["number"] == 2)
    assert powershell == 0.0


def test_ranking_handles_bulgarian_inflection():
    """„чугун" трябва да намери „Чугунът" — езикът е силно флектиран."""
    ranked = rank_lessons(_fake_blocks(), "чугун")
    assert ranked[0][1]["number"] == 3


def test_empty_query_preserves_order():
    fake = _fake_blocks()
    ranked = rank_lessons(fake, "")
    assert [b["number"] for _s, b in ranked] == [1, 2, 3]


def test_empty_blocks_returns_empty():
    assert rank_lessons([], "нещо") == []


def test_query_of_only_stopwords_preserves_order():
    fake = _fake_blocks()
    ranked = rank_lessons(fake, "при след над и или")
    assert [b["number"] for _s, b in ranked] == [1, 2, 3]


def test_ties_break_towards_newer_lesson():
    fake = _fake_blocks()
    fake.append({
        "number": 9, "title": "#09: Дезинфекция отново", "section": "",
        "body": "Дезинфекцията се прави след хидравличната проба на участъка.",
        "text": "#09: Дезинфекция отново\nДезинфекцията се прави след "
                "хидравличната проба на участъка.",
    })
    ranked = rank_lessons(fake, "хидравличната проба")
    assert ranked[0][1]["number"] == 9


def test_real_project_query_surfaces_domain_lessons(blocks):
    """Реалната цел: заявка за проект да извади уроците за ТОЗИ проект."""
    query = "довеждащ водопровод DN300 чугун горски терен дезинфекция"
    top = [b["number"] for _s, b in rank_lessons(blocks, query)[:6]]

    assert 33 in top   # дезинфекция зависи от DN и дължина
    assert 10 in top   # довеждащ водопровод — дезинфекция след всички секции
    assert 35 in top   # CI и PE имат различни норми
    # Бележките за разработчика не бива да са отгоре.
    assert 26 not in top and 27 not in top


def test_developer_lessons_rank_below_domain_lessons(blocks):
    query = "разпределителна мрежа участъци дезинфекция настилки екипи"
    ranked = rank_lessons(blocks, query)
    positions = {b["number"]: i for i, (_s, b) in enumerate(ranked)}
    assert positions[9] < positions[26]
    assert positions[36] < positions[27]


# ===================================================================
# select_lessons — бюджет
# ===================================================================

def test_everything_fits_under_budget(blocks):
    """При ~45 урока всичко се събира — извличането е за после."""
    selected = select_lessons(blocks, "каквото и да е")
    assert len(selected) == len(blocks)


def test_budget_limits_selection():
    selected = select_lessons(_fake_blocks(), "дезинфекция", char_budget=80)
    assert len(selected) < 3


def test_budget_keeps_the_relevant_one():
    selected = select_lessons(_fake_blocks(), "чугун муфи", char_budget=90)
    assert [b["number"] for b in selected] == [3]


def test_selection_returned_in_file_order():
    """Подредени по номер, за да е четим промптът."""
    fake = _fake_blocks()
    selected = select_lessons(fake, "чугун дезинфекция", char_budget=140)
    assert [b["number"] for b in selected] == sorted(b["number"] for b in selected)


def test_select_from_empty_is_empty():
    assert select_lessons([], "нещо") == []


def test_oversized_lesson_is_skipped_not_truncated():
    huge = {"number": 1, "title": "#01: Огромен", "body": "x" * 500,
            "section": "", "text": "#01: Огромен\n" + "x" * 500}
    small = {"number": 2, "title": "#02: Малък", "body": "чугун",
             "section": "", "text": "#02: Малък\nчугун"}
    selected = select_lessons([huge, small], "чугун", char_budget=100)
    assert [b["number"] for b in selected] == [2]


# ===================================================================
# Интеграция в промпта
# ===================================================================

def test_prompt_contains_lesson_bodies(km):
    prompt = km.build_system_prompt("довеждащ")
    assert "0.75" in prompt          # тялото на #17 (терен фактори)
    assert "16 дни" in prompt        # тялото на #14 (pipeline overlap)


def test_prompt_no_longer_drops_early_lessons(km):
    """Регресия: #09–#17 бяха отрязани от `lessons[-20:]`."""
    prompt = km.build_system_prompt("разпределителна")
    for number in ("#09", "#10", "#14", "#17"):
        assert number in prompt, f"Урок {number} липсва в промпта"


def test_verification_prompt_contains_bodies(km):
    prompt = km.get_all_knowledge_for_prompt(level="verification")
    assert "0.75" in prompt
    assert "ALL LESSONS LEARNED" in prompt


def test_prompt_reports_when_lessons_were_filtered(km, monkeypatch):
    monkeypatch.setattr("src.knowledge_manager._LESSONS_CHAR_BUDGET", 400)
    from src.knowledge_manager import select_lessons as real_select

    def small_budget(blocks, query="", char_budget=400):
        return real_select(blocks, query, char_budget=400)

    monkeypatch.setattr("src.knowledge_manager.select_lessons", small_budget)
    prompt = km.build_system_prompt("довеждащ", query="дезинфекция чугун")
    assert "най-релевантни" in prompt


def test_query_changes_which_lessons_are_selected(km, monkeypatch):
    from src.knowledge_manager import select_lessons as real_select

    def small_budget(blocks, query="", char_budget=600):
        return real_select(blocks, query, char_budget=600)

    monkeypatch.setattr("src.knowledge_manager.select_lessons", small_budget)

    water = km.build_system_prompt(query="дезинфекция водопровод хидравлична проба")
    ocr = km.build_system_prompt(query="сканиран PDF OCR извличане на текст")
    assert water != ocr
