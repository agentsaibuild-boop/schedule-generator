"""Unit tests: произход на количествата (BACKLOG т.3, етап 1).

Количество 420 м влизаше в графика без връзка към документа, листа и реда.
На въпроса „откъде е това число" нямаше отговор — нито за човек, нито за
одитор.  По-лошо: нямаше разлика между измерено от документ, предположено от
AI, изчислено от код и въведено от човек, а тези четири имат съвсем различна
тежест при спор с възложител.

Този етап индексира количествените редове от табличните документи и сверява
стойностите в графика срещу тях.  Каквото не се свери, се маркира като
несверено — фалшивата увереност е по-лоша от липсващата.

FAILURE означава: числата в графика отново са без произход.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.chat_handler import ChatHandler  # noqa: E402
from src.provenance import (  # noqa: E402
    STATUS_AI_REPORTED,
    STATUS_EXTRACTED,
    annotate_schedule,
    build_quantity_index,
    find_source,
    similarity,
)

ROWS = [
    {"Наименование": "Разваляне на асфалтова настилка", "Мярка": "м2",
     "Количество": 1240},
    {"Наименование": "Изкоп за тръбна траншея DN110", "Мярка": "м3",
     "Количество": 892},
    {"Наименование": "Доставка и полагане PE 100 RC DN110", "Мярка": "м",
     "Количество": 420},
    {"Наименование": "Монтаж на спирателен кран DN110", "Мярка": "бр.",
     "Количество": 6},
]


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    converted = tmp_path / "converted"
    converted.mkdir()
    (converted / "kss.json").write_text(
        json.dumps({
            "source_file": "КСС.xlsx",
            "sheets": [{"name": "Водопровод",
                        "headers": ["Наименование", "Мярка", "Количество"],
                        "rows": ROWS}],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def index(project: Path):
    return build_quantity_index(project)


# ===================================================================
# Индексиране
# ===================================================================

def test_all_rows_are_indexed(index):
    assert len(index) == len(ROWS)


def test_row_carries_document_sheet_and_row(index):
    row = index[0]
    assert row.source.document == "КСС.xlsx"
    assert row.source.sheet == "Водопровод"
    assert row.source.row == 2      # ред 1 са заглавията


def test_row_numbers_are_sequential(index):
    assert [r.source.row for r in index] == [2, 3, 4, 5]


def test_quantity_is_parsed_as_number(index):
    assert index[2].quantity == 420.0


def test_unit_is_captured(index):
    assert index[2].unit == "м"


def test_source_description_is_human_readable(index):
    described = index[2].source.describe()
    assert "КСС.xlsx" in described
    assert "Водопровод" in described
    assert "ред 4" in described


def test_rows_without_description_are_skipped(tmp_path):
    converted = tmp_path / "converted"
    converted.mkdir()
    (converted / "x.json").write_text(json.dumps({
        "source_file": "X.xlsx",
        "sheets": [{"name": "S", "rows": [{"Наименование": "", "Количество": 5}]}],
    }, ensure_ascii=False), encoding="utf-8")
    assert build_quantity_index(tmp_path) == []


def test_text_documents_are_not_indexed(tmp_path):
    """От свободен текст не може да се посочи клетка — по-добре нищо."""
    converted = tmp_path / "converted"
    converted.mkdir()
    (converted / "doc.json").write_text(json.dumps({
        "source_file": "Договор.pdf", "full_text": "Количество 420 м",
    }, ensure_ascii=False), encoding="utf-8")
    assert build_quantity_index(tmp_path) == []


def test_missing_converted_dir_is_safe(tmp_path):
    assert build_quantity_index(tmp_path) == []


def test_corrupt_json_is_skipped(tmp_path):
    converted = tmp_path / "converted"
    converted.mkdir()
    (converted / "broken.json").write_text("{ не е json", encoding="utf-8")
    assert build_quantity_index(tmp_path) == []


# ===================================================================
# Съответствие
# ===================================================================

def test_similarity_ignores_stopwords():
    assert similarity("Доставка и полагане на тръби", "Полагане тръби") > 0.5


def test_similarity_of_unrelated_texts_is_low():
    assert similarity("Изкоп за траншея", "Дезинфекция и промивка") < 0.2


def test_exact_quantity_match_is_found(index):
    match = find_source("Полагане DN110 PE", 420, index, unit="м")
    assert match is not None
    assert match.quantity_matches is True
    assert match.row.source.row == 4


def test_quantity_within_tolerance_matches(index):
    """2% разлика от закръгляне не бива да разваля съответствието."""
    match = find_source("Полагане DN110 PE", 421, index, unit="м")
    assert match and match.quantity_matches is True


def test_quantity_far_off_does_not_match(index):
    match = find_source("Полагане DN110 PE", 999, index, unit="м")
    assert match is None or match.quantity_matches is False


def test_different_unit_blocks_the_match(index):
    """м3 изкоп не е м тръба, дори имената да си приличат."""
    match = find_source("Изкоп за тръбна траншея DN110", 892, index, unit="м")
    assert match is None or match.row.unit != "м3"


def test_empty_index_returns_nothing():
    assert find_source("каквото и да е", 100, []) is None


def test_unrelated_name_without_quantity_is_not_matched(index):
    assert find_source("Мобилизация на площадката", None, index) is None


# ===================================================================
# Анотиране на графика
# ===================================================================

def test_matched_task_gets_extracted_status(index):
    schedule = [{"id": "T1", "name": "Полагане DN110 PE", "length_m": 420,
                 "unit": "м"}]
    annotate_schedule(schedule, index)
    assert schedule[0]["quantity_provenance"]["status"] == STATUS_EXTRACTED


def test_matched_task_records_the_exact_place(index):
    schedule = [{"id": "T1", "name": "Полагане DN110 PE", "length_m": 420,
                 "unit": "м"}]
    annotate_schedule(schedule, index)
    source = schedule[0]["quantity_provenance"]["source"]
    assert "ред 4" in source
    assert "КСС.xlsx" in source


def test_unmatched_task_is_marked_ai_reported(index):
    schedule = [{"id": "T9", "name": "Измислена дейност", "quantity": 777,
                 "unit": "бр."}]
    annotate_schedule(schedule, index)
    assert schedule[0]["quantity_provenance"]["status"] == STATUS_AI_REPORTED
    assert schedule[0]["quantity_provenance"]["source"] is None


def test_report_counts_both_kinds(index):
    schedule = [
        {"id": "T1", "name": "Полагане DN110 PE", "length_m": 420, "unit": "м"},
        {"id": "T9", "name": "Измислена дейност", "quantity": 777, "unit": "бр."},
    ]
    report = annotate_schedule(schedule, index)
    assert report["verified"] == 1
    assert report["unverified"] == 1
    assert report["total"] == 2


def test_tasks_without_quantity_are_ignored(index):
    schedule = [{"id": "M1", "name": "ФИНАЛ", "milestone": True}]
    report = annotate_schedule(schedule, index)
    assert report["total"] == 0


def test_unverified_details_name_the_closest_row(index):
    schedule = [{"id": "T2", "name": "Изкоп за тръбна траншея DN110",
                 "quantity": 500, "unit": "м3"}]
    report = annotate_schedule(schedule, index)
    assert report["details"][0]["closest"] is not None


# ===================================================================
# Видимост
# ===================================================================

def test_no_report_yields_nothing():
    assert ChatHandler._format_quantity_provenance({}) == []


def test_missing_tables_are_explained():
    lines = ChatHandler._format_quantity_provenance({"no_index": True})
    assert any("непроверен" in ln for ln in lines)


def test_verified_count_is_shown():
    lines = ChatHandler._format_quantity_provenance(
        {"verified": 12, "unverified": 0, "total": 12, "details": []})
    assert any("12 от 12" in ln for ln in lines)


def test_unverified_items_are_listed():
    lines = ChatHandler._format_quantity_provenance({
        "verified": 1, "unverified": 1, "total": 2,
        "details": [{"id": "T9", "name": "Измислена", "quantity": 777,
                     "closest": "Монтаж на спирателен кран"}],
    })
    body = "\n".join(lines)
    assert "T9" in body
    assert "най-близко" in body


def test_user_is_told_the_numbers_came_from_ai():
    lines = ChatHandler._format_quantity_provenance({
        "verified": 0, "unverified": 1, "total": 1,
        "details": [{"id": "T1", "name": "X", "quantity": 5, "closest": None}],
    })
    assert any("идват от AI" in ln for ln in lines)


def test_long_unverified_list_is_truncated():
    details = [{"id": f"T{i}", "name": "X", "quantity": i, "closest": None}
               for i in range(9)]
    lines = ChatHandler._format_quantity_provenance(
        {"verified": 0, "unverified": 9, "total": 9, "details": details})
    assert any("още 4" in ln for ln in lines)


def test_pipeline_calls_the_verification():
    source = (Path(__file__).parent.parent / "src" / "chat_handler.py").read_text(
        encoding="utf-8")
    assert "_verify_quantities" in source
    assert "_format_quantity_provenance" in source


# ===================================================================
# Етап 2 — цитиране вместо търсене назад
# ===================================================================

from src.provenance import (  # noqa: E402
    CITE_MISMATCH,
    CITE_UNCITED,
    CITE_UNKNOWN,
    CITE_VERIFIED,
    format_boq_for_prompt,
    verify_citations,
)


class TestCitations:
    """Моделът сочи реда, кодът проверява цитата.

    Етап 1 сверяваше НАЗАД по сходство — работи, но има граници: количество,
    срещащо се два пъти, се връзва с първото.  Етап 2 обръща посоката.
    """

    def test_ref_is_stable_and_readable(self, index):
        assert index[2].ref == "КСС.xlsx!Водопровод!4"

    def test_prompt_table_lists_refs(self, index):
        table = format_boq_for_prompt(index)
        assert "КСС.xlsx!Водопровод!4" in table
        assert "Доставка и полагане PE 100 RC DN110" in table

    def test_prompt_table_is_empty_without_index(self):
        assert format_boq_for_prompt([]) == ""

    def test_prompt_table_caps_huge_boq(self, index):
        table = format_boq_for_prompt(index, max_rows=2)
        assert "още 2 реда" in table

    def test_correct_citation_is_verified(self, index):
        schedule = [{"id": "T1", "name": "Полагане", "length_m": 420,
                     "source_ref": "КСС.xlsx!Водопровод!4"}]
        report = verify_citations(schedule, index)
        assert report["verified"] == 1
        assert schedule[0]["quantity_provenance"]["citation"] == CITE_VERIFIED

    def test_verified_citation_records_the_place(self, index):
        schedule = [{"id": "T1", "name": "Полагане", "length_m": 420,
                     "source_ref": "КСС.xlsx!Водопровод!4"}]
        verify_citations(schedule, index)
        assert "ред 4" in schedule[0]["quantity_provenance"]["source"]

    def test_wrong_number_with_right_ref_is_a_mismatch(self, index):
        """Най-опасният случай: изглежда подкрепено с документ, но не е."""
        schedule = [{"id": "T1", "name": "Полагане", "length_m": 999,
                     "source_ref": "КСС.xlsx!Водопровод!4"}]
        report = verify_citations(schedule, index)
        assert report["mismatch"] == 1
        assert schedule[0]["quantity_provenance"]["citation"] == CITE_MISMATCH
        assert schedule[0]["quantity_provenance"]["actual"] == 420.0

    def test_invented_ref_is_caught(self, index):
        schedule = [{"id": "T1", "name": "X", "quantity": 100,
                     "source_ref": "ИЗМИСЛЕН.xlsx!Лист!99"}]
        report = verify_citations(schedule, index)
        assert report["unknown_ref"] == 1

    def test_missing_citation_is_counted_separately(self, index):
        schedule = [{"id": "T1", "name": "X", "quantity": 100}]
        report = verify_citations(schedule, index)
        assert report["uncited"] == 1
        assert schedule[0]["quantity_provenance"]["citation"] == CITE_UNCITED

    def test_tolerance_allows_rounding(self, index):
        schedule = [{"id": "T1", "name": "Полагане", "length_m": 421,
                     "source_ref": "КСС.xlsx!Водопровод!4"}]
        assert verify_citations(schedule, index)["verified"] == 1

    def test_only_verified_gets_extracted_status(self, index):
        schedule = [
            {"id": "A", "name": "X", "length_m": 420,
             "source_ref": "КСС.xlsx!Водопровод!4"},
            {"id": "B", "name": "Y", "length_m": 999,
             "source_ref": "КСС.xlsx!Водопровод!4"},
        ]
        verify_citations(schedule, index)
        assert schedule[0]["quantity_provenance"]["status"] == STATUS_EXTRACTED
        assert schedule[1]["quantity_provenance"]["status"] == STATUS_AI_REPORTED

    def test_tasks_without_quantity_are_ignored(self, index):
        schedule = [{"id": "M", "name": "ФИНАЛ", "milestone": True}]
        assert verify_citations(schedule, index)["total"] == 0

    def test_problems_carry_an_explanation(self, index):
        schedule = [{"id": "T1", "name": "X", "length_m": 999,
                     "source_ref": "КСС.xlsx!Водопровод!4"}]
        problem = verify_citations(schedule, index)["problems"][0]
        assert "не съвпада" in problem["note"]


class TestCitationReport:
    """Отчетът разделя четирите изхода — те имат различна тежест."""

    def test_mismatch_is_reported_most_loudly(self):
        lines = ChatHandler._format_quantity_provenance({
            "total": 1, "verified": 0, "mismatch": 1, "unknown_ref": 0,
            "uncited": 0,
            "problems": [{"id": "T1", "name": "Полагане", "status": "mismatch",
                          "ref": "КСС.xlsx!Водопровод!4", "quantity": 999,
                          "actual": 420, "note": "не съвпада"}],
        })
        body = "\n".join(lines)
        assert "НЕ съвпадат" in body
        assert "999" in body and "420" in body

    def test_invented_ref_is_called_out(self):
        lines = ChatHandler._format_quantity_provenance({
            "total": 1, "verified": 0, "mismatch": 0, "unknown_ref": 1,
            "uncited": 0,
            "problems": [{"id": "T1", "name": "X", "status": "unknown_ref",
                          "ref": "ИЗМИСЛЕН!Л!9", "quantity": 1, "actual": None,
                          "note": ""}],
        })
        assert any("несъществуващ ред" in ln for ln in lines)

    def test_uncited_is_a_milder_note(self):
        lines = ChatHandler._format_quantity_provenance({
            "total": 1, "verified": 0, "mismatch": 0, "unknown_ref": 0,
            "uncited": 1, "problems": [],
        })
        body = "\n".join(lines)
        assert "без посочен източник" in body
        assert "НЕ съвпадат" not in body

    def test_all_verified_shows_no_problems(self):
        lines = ChatHandler._format_quantity_provenance({
            "total": 5, "verified": 5, "mismatch": 0, "unknown_ref": 0,
            "uncited": 0, "problems": [],
        })
        assert any("5 от 5" in ln for ln in lines)
        assert not any("НЕ съвпадат" in ln for ln in lines)


def test_prompt_asks_for_citations():
    source = (Path(__file__).parent.parent / "src" / "ai_processor.py").read_text(
        encoding="utf-8")
    assert "source_ref" in source
    assert "НЕ измисляй ref" in source


# ===================================================================
# Етап 3 — ръчна корекция (human_override)
# ===================================================================

from src.provenance import STATUS_HUMAN, mark_human_overrides  # noqa: E402


class TestHumanOverride:
    """Количество, сменено ръчно през чата, идва от ЧОВЕК — не от AI/документ.

    Решение на потребителя 2026-07-24: само маркер, без стара стойност, без
    идентичност. Всеки с достъп може да редактира.
    """

    def test_changed_quantity_is_marked_human(self):
        before = [{"id": "T5", "name": "Полагане", "length_m": 420}]
        after = [{"id": "T5", "name": "Полагане", "length_m": 450}]
        marked = mark_human_overrides(before, after)
        assert marked == 1
        assert after[0]["quantity_provenance"]["status"] == STATUS_HUMAN

    def test_unchanged_quantity_keeps_its_provenance(self):
        """Задача, която човек не е пипал, запазва произхода си."""
        before = [{"id": "T5", "name": "Полагане", "length_m": 420}]
        after = [{"id": "T5", "name": "Полагане", "length_m": 420,
                  "quantity_provenance": {"status": STATUS_EXTRACTED}}]
        mark_human_overrides(before, after)
        assert after[0]["quantity_provenance"]["status"] == STATUS_EXTRACTED

    def test_source_says_manual(self):
        before = [{"id": "T5", "length_m": 420}]
        after = [{"id": "T5", "length_m": 450}]
        mark_human_overrides(before, after)
        assert "ръчно" in after[0]["quantity_provenance"]["source"]

    def test_new_task_without_prior_is_not_marked(self):
        """Нова задача няма 'преди' — не е override, а добавяне."""
        marked = mark_human_overrides([], [{"id": "T9", "length_m": 100}])
        assert marked == 0

    def test_quantity_field_change_is_detected(self):
        before = [{"id": "Ш1", "name": "СРС", "quantity": 6}]
        after = [{"id": "Ш1", "name": "СРС", "quantity": 8}]
        assert mark_human_overrides(before, after) == 1

    def test_tasks_without_quantity_are_ignored(self):
        before = [{"id": "M", "name": "ФИНАЛ", "milestone": True}]
        after = [{"id": "M", "name": "ФИНАЛ", "milestone": True}]
        assert mark_human_overrides(before, after) == 0

    def test_multiple_changes_all_marked(self):
        before = [{"id": "A", "length_m": 100}, {"id": "B", "length_m": 200}]
        after = [{"id": "A", "length_m": 150}, {"id": "B", "length_m": 250}]
        assert mark_human_overrides(before, after) == 2

    def test_human_value_is_not_verified_against_documents(self, index):
        """Ръчната стойност ЗАМЕНЯ документа — не се сверява срещу него."""
        schedule = [{"id": "T5", "name": "Полагане", "length_m": 999,
                     "source_ref": "КСС.xlsx!Водопровод!4",
                     "quantity_provenance": {"status": STATUS_HUMAN}}]
        report = verify_citations(schedule, index)
        assert report["human"] == 1
        assert report["mismatch"] == 0

    def test_report_shows_human_count(self):
        lines = ChatHandler._format_quantity_provenance({
            "total": 3, "verified": 2, "human": 1, "mismatch": 0,
            "unknown_ref": 0, "uncited": 0, "problems": [],
        })
        assert any("ръчно въведени" in ln for ln in lines)


def test_modification_flow_marks_overrides():
    source = (Path(__file__).parent.parent / "src" / "chat_handler.py").read_text(
        encoding="utf-8")
    assert "mark_human_overrides" in source
