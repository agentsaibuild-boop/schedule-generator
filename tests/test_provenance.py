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
        # 999 НАДХВЪРЛЯ реда — това е дефект.  По-малко от реда е разделяне
        # между зони и вече не е дефект (жив прогон 14.08.2026).
        problem = verify_citations(schedule, index)["problems"][0]
        assert "надхвърля" in problem["note"]


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
        # Одит v9: човешки override само при ИЗРИЧНО посочена задача.
        marked = mark_human_overrides(before, after, "промени T5 на 450")
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
        mark_human_overrides(before, after, "промени T5")
        assert "ръчно" in after[0]["quantity_provenance"]["source"]

    def test_new_task_without_prior_is_not_marked(self):
        """Нова задача няма 'преди' — не е override, а добавяне."""
        marked = mark_human_overrides([], [{"id": "T9", "length_m": 100}])
        assert marked == 0

    def test_quantity_field_change_is_detected(self):
        before = [{"id": "Ш1", "name": "СРС", "quantity": 6}]
        after = [{"id": "Ш1", "name": "СРС", "quantity": 8}]
        assert mark_human_overrides(before, after, "промени Ш1 на 8") == 1

    def test_tasks_without_quantity_are_ignored(self):
        before = [{"id": "M", "name": "ФИНАЛ", "milestone": True}]
        after = [{"id": "M", "name": "ФИНАЛ", "milestone": True}]
        assert mark_human_overrides(before, after) == 0

    def test_multiple_changes_all_marked(self):
        before = [{"id": "A", "length_m": 100}, {"id": "B", "length_m": 200}]
        after = [{"id": "A", "length_m": 150}, {"id": "B", "length_m": 250}]
        assert mark_human_overrides(before, after, "промени A и B") == 2

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


# ===================================================================
# Одит 2026-07-24: строгост на съвпадението
# ===================================================================

from src.provenance import requested_task_ids  # noqa: E402


class TestCrossCheck:
    """Съвпадащо число не стига — мярка и материал също се проверяват.

    Одит: „Асфалт 420 м2" цитираше „PE DN110, 420 м" и получаваше verified,
    защото 420=420.  Фалшиво доказателство за произход.
    """

    def test_matching_number_but_wrong_unit_is_mismatch(self, index):
        # ред 4 е "Доставка и полагане PE 100 RC DN110", м, 420
        schedule = [{"id": "T1", "name": "Асфалтова настилка", "quantity": 420,
                     "unit": "м2", "source_ref": "КСС.xlsx!Водопровод!4"}]
        report = verify_citations(schedule, index)
        assert report["mismatch"] == 1
        assert "мярка" in report["problems"][0]["note"]

    def test_matching_number_but_wrong_material_is_mismatch(self, index):
        # ред 4 е PE; задачата казва чугун
        schedule = [{"id": "T1", "name": "Полагане чугун DN300", "length_m": 420,
                     "unit": "м", "source_ref": "КСС.xlsx!Водопровод!4"}]
        report = verify_citations(schedule, index)
        assert report["mismatch"] == 1
        assert "материал" in report["problems"][0]["note"]

    def test_matching_number_and_unit_and_material_is_verified(self, index):
        schedule = [{"id": "T1", "name": "Полагане PE DN110", "length_m": 420,
                     "unit": "м", "source_ref": "КСС.xlsx!Водопровод!4"}]
        report = verify_citations(schedule, index)
        assert report["verified"] == 1

    def test_missing_unit_does_not_block(self, index):
        """Липсваща мярка не обвинява — проверява се само когато и двете я имат."""
        schedule = [{"id": "T1", "name": "Полагане PE DN110", "length_m": 420,
                     "source_ref": "КСС.xlsx!Водопровод!4"}]
        report = verify_citations(schedule, index)
        assert report["verified"] == 1

    def test_unit_synonyms_are_accepted(self, index):
        """'кв.м' и 'м2' са една и съща мярка."""
        from src.provenance import _norm_unit
        assert _norm_unit("кв.м") == _norm_unit("м2")
        assert _norm_unit("куб.м") == _norm_unit("м3")

    def test_unit_normalization_real_project_cases(self):
        """Проба на реален проект 2026-07-31: изписвания, които бяха фалшиви
        разминавания — числото съвпадаше, само мярката се пишеше различно."""
        from src.provenance import _norm_unit
        assert _norm_unit("бр") == _norm_unit("брой") == _norm_unit("бр.")
        assert _norm_unit("м") == _norm_unit("m")           # латиница ↔ кирилица
        assert _norm_unit("м2") == _norm_unit("M2")

    def test_composite_unit_needs_review_not_verified(self):
        """Одит v11 #5 + v12 #6: `m3/m'` НЕ се приравнява на `м3` и НЕ се приема
        за доказано — различни физически размерности → нужен преглед."""
        from src.provenance import _norm_unit, _cross_check, QuantityRow, SourceRef
        assert _norm_unit("м3") != _norm_unit("m3/m'")      # НЕ се колапсва
        row = QuantityRow("Бетонов кожух", 74.5, "m3/m'", SourceRef("f", "s", 1), {})
        note = _cross_check({"unit": "м3", "name": "Кожух"}, row)
        assert note and "преглед" in note                    # НЕ verified

    def test_number_in_unit_column_is_not_a_mismatch(self):
        """Различен layout на лист → в колоната за мярка има число/описание.
        Това НЕ е мярка → не бива да прави фалшиво разминаване (числото пасва)."""
        from src.provenance import _cross_check, QuantityRow, SourceRef
        row = QuantityRow("УО единичен", 100.0, "100", SourceRef("f", "s", 1), {})
        assert _cross_check({"unit": "бр", "name": "Монтаж УО"}, row) == ""
        row2 = QuantityRow("Асфалт", 10824.0, "пътна-възстановяване...",
                           SourceRef("f", "s", 2), {})
        assert _cross_check({"unit": "м2", "name": "Асфалт"}, row2) == ""

    def test_real_unit_mismatch_still_caught(self):
        """Две РЕАЛНИ, но различни единици (м2 vs м) си остават разминаване."""
        from src.provenance import _cross_check, QuantityRow, SourceRef
        row = QuantityRow("PE тръба", 420.0, "м", SourceRef("f", "s", 1), {})
        assert "мярка" in _cross_check({"unit": "м2", "name": "Асфалт"}, row)


class TestAttribution:
    """AI промени на непоискани задачи не се бележат като човешки.

    Одит: човек променя A, AI променя и B; и двете ставаха human_override.
    """

    def test_only_requested_task_is_human(self):
        before = [{"id": "A", "length_m": 100}, {"id": "B", "length_m": 200}]
        after = [{"id": "A", "length_m": 150}, {"id": "B", "length_m": 999}]
        mark_human_overrides(before, after, "промени количеството на A на 150")
        assert after[0]["quantity_provenance"]["status"] == STATUS_HUMAN
        assert after[1]["quantity_provenance"]["status"] == STATUS_AI_REPORTED

    def test_unrequested_change_is_flagged(self):
        before = [{"id": "A", "length_m": 100}, {"id": "B", "length_m": 200}]
        after = [{"id": "A", "length_m": 150}, {"id": "B", "length_m": 999}]
        mark_human_overrides(before, after, "промени A на 150")
        assert "без изрична заявка" in after[1]["quantity_provenance"]["note"]

    def test_global_message_does_not_grant_human_immunity(self):
        """Одит v9, точка 1: заявка без разпозната КОНКРЕТНА задача е
        FAIL-CLOSED — не приписва AI промените на човека, иначе strict-gate-ът
        би пропуснал количествата без проверка срещу КСС."""
        before = [{"id": "A", "length_m": 100}, {"id": "B", "length_m": 200}]
        after = [{"id": "A", "length_m": 90}, {"id": "B", "length_m": 180}]
        n = mark_human_overrides(before, after, "намали всички количества с 10%")
        assert n == 0
        assert after[0]["quantity_provenance"]["status"] == STATUS_AI_REPORTED
        assert after[1]["quantity_provenance"]["status"] == STATUS_AI_REPORTED

    def test_requested_ids_finds_known_tasks(self):
        assert requested_task_ids("промени T5 на 450", {"T5", "T6"}) == {"T5"}

    def test_requested_ids_ignores_unknown(self):
        """Случаен низ не бива да мине за задача."""
        assert requested_task_ids("промени на ул. 5", {"T5"}) == set()

    def test_requested_ids_matches_cyrillic_codes(self):
        assert requested_task_ids("удължи В01", {"В01", "К02"}) == {"В01"}


def test_message_passed_from_chat_handler():
    source = (Path(__file__).parent.parent / "src" / "chat_handler.py").read_text(
        encoding="utf-8")
    assert "mark_human_overrides(before_tasks, modified_tasks, message)" in source


class TestV13CoverageBypasses:
    """Одит v13: доказателственият coverage все още имаше обходи."""

    def _row(self, ref_row, qty, unit="м", desc="Тръба PE"):
        from src.provenance import QuantityRow, SourceRef
        return QuantityRow(desc, qty, unit, SourceRef("КСС.xlsx", "A", ref_row), {})

    def test_milestone_with_quantity_is_not_verified(self):
        """Milestone с количество НЕ доказва покритие (одит v13 P0)."""
        from src.provenance import verify_citations
        idx = [self._row(2, 100.0)]
        tasks = [{"id": "M1", "name": "Milestone", "milestone": True,
                  "duration": 0, "length_m": 100, "unit": "м",
                  "source_ref": "КСС.xlsx!A!2"}]
        rep = verify_citations(tasks, idx)
        assert rep["verified"] == 0
        assert "КСС.xlsx!A!2" not in rep["verified_refs"]

    def test_non_numeric_quantity_is_none_not_nan(self):
        """'вж. проект'/'abc' → None, не NaN (одит v13)."""
        from src.provenance import _number
        assert _number("вж. проект") is None
        assert _number("abc") is None
        assert _number(float("nan")) is None
        assert _number(float("inf")) is None
        assert _number("538") == 538.0

    def test_nan_quantity_task_is_not_verified(self):
        """Задача с нечислово количество не бива да мине за verified."""
        from src.provenance import verify_citations
        idx = [self._row(2, 100.0)]
        tasks = [{"id": "T1", "name": "Тръба", "length_m": "abc", "unit": "м",
                  "source_ref": "КСС.xlsx!A!2"}]
        rep = verify_citations(tasks, idx)
        assert rep["verified"] == 0

    def test_pick_prefers_numeric_over_text(self):
        """'Количество'='вж. проект' не бива да засенчва 'Дължина'=538."""
        from src.provenance import _pick, _QTY_KEYS
        row = {"Наименование": "Тръба", "Количество": "вж. проект",
               "Дължина /m/": 538}
        col, val = _pick(row, _QTY_KEYS, prefer_numeric=True)
        assert val == 538

    def test_verify_returns_verified_refs(self):
        """verified_refs съдържа само доказаните редове."""
        from src.provenance import verify_citations
        idx = [self._row(2, 100.0), self._row(3, 200.0)]
        tasks = [{"id": "T1", "name": "Тръба", "length_m": 100, "unit": "м",
                  "source_ref": "КСС.xlsx!A!2"}]   # покрива само ред 2
        rep = verify_citations(tasks, idx)
        assert rep["verified_refs"] == ["КСС.xlsx!A!2"]


class TestV14CoverageBypasses:
    """Одит v14: „verified coverage" още имаше обходи."""

    def _row(self, r, qty, unit="м", desc="Тръба PE DN110"):
        from src.provenance import QuantityRow, SourceRef
        return QuantityRow(desc, qty, unit, SourceRef("КСС.xlsx", "A", r), {})

    def test_is_summary_task_does_not_cover(self):
        """Одит v14 P0: `is_summary` (което enrichment реално ползва) не покрива."""
        from src.provenance import verify_citations
        idx = [self._row(2, 100.0)]
        tasks = [{"id": "S1", "name": "Полагане DN110 PE", "is_summary": True,
                  "length_m": 100, "unit": "м", "source_ref": "КСС.xlsx!A!2"}]
        rep = verify_citations(tasks, idx)
        assert rep["verified"] == 0
        assert rep["verified_refs"] == []

    def test_has_children_task_does_not_cover(self):
        from src.provenance import verify_citations
        idx = [self._row(2, 100.0)]
        tasks = [{"id": "G1", "name": "Полагане DN110 PE", "_has_children": True,
                  "length_m": 100, "unit": "м", "source_ref": "КСС.xlsx!A!2"}]
        assert verify_citations(tasks, idx)["verified"] == 0

    def test_composite_unit_symmetric(self):
        """Одит v14 P0: task=m3/m vs row=m3 също не е verified (не само обратното)."""
        from src.provenance import _cross_check, QuantityRow, SourceRef
        row = QuantityRow("Кожух", 100.0, "m3", SourceRef("f", "s", 1), {})
        note = _cross_check({"unit": "m3/m'", "name": "Кожух"}, row)
        assert note and "преглед" in note

    def test_duplicate_task_does_not_double_cover(self):
        """Одит v14 P0: две ИДЕНТИЧНИ задачи на един ред → втората е дубликат."""
        from src.provenance import verify_citations
        idx = [self._row(2, 100.0)]
        tasks = [
            {"id": "T1", "name": "Полагане DN110 PE", "length_m": 100, "unit": "м",
             "source_ref": "КСС.xlsx!A!2"},
            {"id": "T2", "name": "Полагане DN110 PE", "length_m": 100, "unit": "м",
             "source_ref": "КСС.xlsx!A!2"},   # точно копие
        ]
        rep = verify_citations(tasks, idx)
        assert rep["verified"] == 1          # само първата
        assert rep["mismatch"] == 1          # втората е дубликат

    def test_different_activities_on_same_row_are_both_ok(self):
        """Легитимно: изкоп и полагане на едни и същи 538м (различни имена)."""
        from src.provenance import verify_citations
        idx = [self._row(2, 538.0)]
        tasks = [
            {"id": "T1", "name": "Изкоп тр. DN110 PE", "length_m": 538, "unit": "м",
             "source_ref": "КСС.xlsx!A!2"},
            {"id": "T2", "name": "Полагане DN110 PE", "length_m": 538, "unit": "м",
             "source_ref": "КСС.xlsx!A!2"},
        ]
        rep = verify_citations(tasks, idx)
        assert rep["verified"] == 2          # различни дейности — и двете ок


def test_pavement_material_mismatch_is_caught():
    """Одит v14: 'Асфалтова настилка 420м²' не е 'Бетонова настилка 420м²'."""
    from src.provenance import _cross_check, QuantityRow, SourceRef
    row = QuantityRow("Бетонова настилка", 420.0, "м2", SourceRef("f", "s", 1), {})
    note = _cross_check({"unit": "м2", "name": "Асфалтова настилка"}, row)
    assert note and "настилков материал" in note


def test_same_pavement_material_is_not_flagged():
    from src.provenance import _cross_check, QuantityRow, SourceRef
    row = QuantityRow("Асфалтова настилка", 420.0, "м2", SourceRef("f", "s", 1), {})
    assert _cross_check({"unit": "м2", "name": "Възстановяване асфалт"}, row) == ""


class TestV15ActivityClass:
    """Одит v15: каноничен activity_class вместо display-name евристики."""

    def _idx(self, qty=100.0, desc="Тръба PE DN110"):
        from src.provenance import QuantityRow, SourceRef
        return [QuantityRow(desc, qty, "м", SourceRef("КСС.xlsx", "A", 2), {})]

    def _t(self, name, qty=100.0):
        return {"id": name[:3], "name": name, "length_m": qty, "unit": "м",
                "source_ref": "КСС.xlsx!A!2"}

    def test_activity_class_canonicalizes(self):
        from src.provenance import activity_class
        assert activity_class({"name": "Изкоп тр. DN110"}) == "excavation"
        assert activity_class({"name": "Полагане DN110 PE"}) == "laying"
        assert activity_class({"name": "Засипване и уплътняване"}) == "backfill"
        assert activity_class({"name": "Приемане на обекта"}) == "acceptance"

    def test_pavement_noun_beats_generic_verb(self):
        """Проба 2026-08-03 (реален КСС „4. Пътна"): pavement-СЪЩЕСТВИТЕЛНОТО е
        по-специфично от общия ГЛАГОЛ.

        „полагане на бордюри/плочи" е НАСТИЛКА, не тръбополагане; „възстановяване
        на настилка извън траншеен изкоп" е настилка, не изкоп.  Но „полагане на
        тръби" (без pavement-съществително) си остава laying.

        FAILURE означава: laying-задача би покрила ФАЛШИВО настилков ред."""
        from src.provenance import activity_class as ac
        assert ac("Доставка и полагане на средни бетонови бордюри") == "pavement"
        assert ac("Доставка и полагане на тротоарни плочи (унипаваж)") == "pavement"
        assert ac("Възстановяване на пътна настилка извън траншеен изкоп") == "pavement"
        # без pavement-съществително → глаголът решава (тръбите остават laying)
        assert ac("Полагане на PEHD тръби DN110") == "laying"
        assert ac("Полагане на бетонови тръби DN400") == "laying"
        # „бетонов" вече НЕ значи настилка (бетонова тръба ≠ настилка)
        assert ac("Бетонови тръби DN400") != "pavement"

    def test_duplicate_survives_name_variation(self):
        """Одит v15 P0: '+ една дума' в името вече не заобикаля дубликата."""
        from src.provenance import verify_citations
        tasks = [self._t("Полагане DN110 PE"), self._t("Полагане на DN110 PE")]
        rep = verify_citations(tasks, self._idx())
        assert rep["verified"] == 1 and rep["mismatch"] == 1

    def test_duplicate_survives_tolerance_quantity(self):
        """Одит v15 P0: количество в рамките на 2% толеранс не заобикаля."""
        from src.provenance import verify_citations
        tasks = [self._t("Полагане DN110 PE", 100.0),
                 self._t("Полагане DN110 PE", 101.9)]
        rep = verify_citations(tasks, self._idx())
        assert rep["verified"] == 1 and rep["mismatch"] == 1

    def test_administrative_task_does_not_cover(self):
        """Одит v15 P0: приемателна/административна дейност не доказва BOQ."""
        from src.provenance import verify_citations, _is_production_task
        adm = {"id": "A1", "name": "Приемане на изпълнените 100 m",
               "type": "approval", "length_m": 100, "unit": "м",
               "source_ref": "КСС.xlsx!A!2"}
        assert _is_production_task(adm) is False
        assert verify_citations([adm], self._idx())["verified"] == 0

    def test_different_activities_share_row_legitimately(self):
        """Изкоп/полагане/засипване на един ред → различни класове → всички ок."""
        from src.provenance import verify_citations
        tasks = [self._t("Изкоп тр. DN110"), self._t("Полагане DN110 PE"),
                 self._t("Засипване и уплътняване")]
        assert verify_citations(tasks, self._idx())["verified"] == 3

    def test_text_composite_unit_is_flagged(self):
        """Одит v15 #7: 'm3 на m' (без символ /) също иска преглед."""
        from src.provenance import _is_composite_unit, _cross_check, QuantityRow, SourceRef
        assert _is_composite_unit("m3 на m")
        assert _is_composite_unit("м3 за л.м")
        assert not _is_composite_unit("м3")
        row = QuantityRow("Кожух", 10.0, "m3", SourceRef("f", "s", 1), {})
        assert _cross_check({"unit": "m3 на m", "name": "Кожух"}, row)


class TestV16DomainCoverage:
    """Одит v16: BOQ позиция ↔ дейност-покривач ↔ производни дейности."""

    def _pipe(self, r=2, q=100.0):
        from src.provenance import QuantityRow, SourceRef
        return QuantityRow("Реконструкция на водопровод DN110", q, "м",
                           SourceRef("КСС.xlsx", "A", r), {})

    def _exc(self, r=3, q=100.0):
        from src.provenance import QuantityRow, SourceRef
        return QuantityRow("Изкоп на траншея за тръби", q, "м3",
                           SourceRef("КСС.xlsx", "A", r), {})

    def _t(self, name, q=100.0, unit="м", ref="КСС.xlsx!A!2"):
        return {"id": name[:8], "name": name, "length_m": q, "unit": unit,
                "source_ref": ref}

    def test_pipe_row_covered_by_laying_others_derived(self):
        from src.provenance import analyze_boq_coverage
        tasks = [self._t("Изкоп тр. DN110"), self._t("Полагане DN110 PE"),
                 self._t("Засипване и уплътняване")]
        cov = analyze_boq_coverage(tasks, [self._pipe()])
        assert cov["covered"] == ["КСС.xlsx!A!2"]
        assert cov["uncovered"] == []
        assert not cov["over_covered"]
        assert len(cov["derived"]) == 2      # изкоп + засип са производни

    def test_two_layings_is_over_covered(self):
        from src.provenance import analyze_boq_coverage
        tasks = [self._t("Полагане DN110 PE"), self._t("Полагане на тръба DN110")]
        cov = analyze_boq_coverage(tasks, [self._pipe()])
        assert "КСС.xlsx!A!2" in cov["over_covered"]

    def test_backfill_does_not_cover_excavation_row(self):
        """Насип цитира ИЗКОП-ред → производен → редът остава непокрит."""
        from src.provenance import analyze_boq_coverage
        tasks = [self._t("Насипване с трошен камък", 100.0, "м3", "КСС.xlsx!A!3")]
        cov = analyze_boq_coverage(tasks, [self._exc()])
        assert cov["uncovered"] == ["КСС.xlsx!A!3"]
        assert cov["covered"] == []

    def test_excavation_covers_excavation_row(self):
        from src.provenance import analyze_boq_coverage
        tasks = [self._t("Изкоп тр. DN110", 100.0, "м3", "КСС.xlsx!A!3")]
        cov = analyze_boq_coverage(tasks, [self._exc()])
        assert cov["covered"] == ["КСС.xlsx!A!3"]

    def test_administrative_is_not_a_coverer(self):
        from src.provenance import analyze_boq_coverage
        tasks = [{"id": "A1", "name": "Приемане на изпълнените", "type": "approval",
                  "length_m": 100, "unit": "м", "source_ref": "КСС.xlsx!A!2"}]
        cov = analyze_boq_coverage(tasks, [self._pipe()])
        assert cov["uncovered"] == ["КСС.xlsx!A!2"]


class TestV16ActivityClassTrust:
    """Одит v16 P0: activity_class е server-derived, не AI-controlled."""

    def _idx(self):
        from src.provenance import QuantityRow, SourceRef
        return [QuantityRow("Реконструкция водопровод DN110", 100.0, "м",
                            SourceRef("КСС.xlsx", "A", 2), {})]

    def test_ai_supplied_class_is_ignored(self):
        """AI слага laying_a/laying_b, за да избегне дубликат → игнорира се."""
        from src.provenance import activity_class, analyze_boq_coverage
        t1 = {"id": "T1", "name": "Полагане DN110 PE", "activity_class": "laying_a",
              "length_m": 100, "unit": "м", "source_ref": "КСС.xlsx!A!2"}
        t2 = {"id": "T2", "name": "Полагане DN110 PE", "activity_class": "laying_b",
              "length_m": 100, "unit": "м", "source_ref": "КСС.xlsx!A!2"}
        assert activity_class(t1) == activity_class(t2) == "laying"
        cov = analyze_boq_coverage([t1, t2], self._idx())
        assert "КСС.xlsx!A!2" in cov["over_covered"]        # дубликат хванат

    def test_ai_cannot_self_certify_administrative_as_production(self):
        """Приемателна задача + activity_class=laying → пак не е производство."""
        from src.provenance import activity_class, _is_production_task
        t = {"name": "Приемане на изпълнените работи", "activity_class": "laying",
             "length_m": 100, "unit": "м", "source_ref": "КСС.xlsx!A!2"}
        assert activity_class(t) == "acceptance"
        assert _is_production_task(t) is False

    def test_class_is_stripped_by_strip_ai_provenance(self):
        from src.provenance import strip_ai_provenance
        tasks = [{"id": "T1", "activity_class": "laying", "activity_role": "x"}]
        strip_ai_provenance(tasks)
        assert "activity_class" not in tasks[0]
        assert "activity_role" not in tasks[0]

    def test_negation_trenchless_is_laying_not_excavation(self):
        from src.provenance import activity_class
        assert activity_class("Безизкопно полагане DN110 чрез HDD") == "laying"

    def test_role_wins_over_content(self):
        from src.provenance import activity_class
        assert activity_class("Приемане на изкопа") == "acceptance"
        assert activity_class("Документация за изкопни работи") == "documentation"

    def test_three_letter_codes_match_whole_word_not_substring(self):
        """Одит v18: „срс/сво/ско" (сградни отклонения) са 3-буквени кодове.

        Голият подниз „ско"/„сво" лъжливо съвпадаше вътре в обичайни думи
        (геодезичеСКО, оСВОбождаване) → фалшив клас-покривач `manhole`, а оттам
        фалшиво покритие.  Сега се матчват само като ЦЯЛА ДУМА.

        FAILURE означава: substring-евристиката пак бърка код с част от дума."""
        from src.provenance import activity_class
        # ЦЯЛА дума → истински код за сградно отклонение → manhole
        assert activity_class("СВО — сградно водопроводно отклонение") == "manhole"
        assert activity_class("Изпълнение на СКО") == "manhole"
        assert activity_class("Направа на СРС") == "manhole"
        # подниз в обичайна дума → НЕ е manhole
        assert activity_class("Геодезическо заснемане и трасиране") != "manhole"
        assert activity_class("Освобождаване на строителна площадка") != "manhole"
        # fill-гласна: „спирателен" (не „спирателн") пак се хваща
        assert activity_class("Монтаж на спирателен кран DN100") == "manhole"

    def test_unknown_activity_does_not_cover(self):
        """Fail-closed: неразпозната/административна дейност не покрива ред."""
        from src.provenance import analyze_boq_coverage
        t = {"id": "C", "name": "Координация с възложителя", "length_m": 100,
             "unit": "м", "source_ref": "КСС.xlsx!A!2"}
        cov = analyze_boq_coverage([t], self._idx())
        assert cov["covered"] == [] and cov["uncovered"] == ["КСС.xlsx!A!2"]


# ===================================================================
# Мярката идва от клетка, която ГОДИ за мярка
# ===================================================================
#
# Одит 07.08.2026: в експортирания XML стояха „Мярка = 100" (УО единичен),
# „Мярка = 1" (Преливна шахта) и цялото описание на дейността при пътните
# редове.  И трите идват от едно място: колоната се избираше само по заглавие,
# а в реалния КСС под заглавие „Ед. мярка" стои ту броят, ту описанието.
#
# FAILURE означава: провенансът в готовия файл пак твърди нещо, което
# документът не казва.

class TestUnitExtraction:

    def _index(self, tmp_path, rows, sheet="Лист"):
        import json as _json
        from src.provenance import build_quantity_index
        converted = tmp_path / "converted"
        converted.mkdir(parents=True, exist_ok=True)
        (converted / "КСС.json").write_text(
            _json.dumps({"source_file": "КСС.xlsx",
                         "sheets": [{"name": sheet, "rows": rows}]},
                        ensure_ascii=False),
            encoding="utf-8")
        return [r for r in build_quantity_index(tmp_path) if r.quantity is not None]

    def test_a_bare_number_is_not_a_unit(self, tmp_path):
        """„УО единичен | Ед. мярка = 100" — 100 е броят, не мярката."""
        rows = [{"Наименование": "УО единичен", "Ед. мярка": 100,
                 "количество": 100, "__excel_row__": 28}]

        assert self._index(tmp_path, rows)[0].unit == ""

    def test_a_whole_sentence_is_not_a_unit(self, tmp_path):
        """Разместен хедър: описанието стои под „Ед. мярка", мярката — под „ед. мярка"."""
        rows = [{"Канализационна мрежа": 1,
                 "Ед. мярка": "Доставка и полагане на средни бетонови бордюри "
                              "С18 15/25/50 см, БДС EN 1340:2005/NA : 2013",
                 "ед. мярка": "м", "количество": 7761, "__excel_row__": 9}]

        assert self._index(tmp_path, rows)[0].unit == "м"

    def test_a_composite_unit_survives(self, tmp_path):
        """m3/m' съдържа цифра, но не е число — кожухът не бива да губи мярката си."""
        rows = [{"Наименование": "Бетонов кожух за тръба DN 1000",
                 "Ед. мярка": "m3/m'", "количество": 677.6, "__excel_row__": 18}]

        assert self._index(tmp_path, rows)[0].unit == "m3/m'"

    def test_a_missing_unit_stays_empty_rather_than_guessed(self, tmp_path):
        """Празно е честният отговор — измислена мярка обвинява задачата."""
        rows = [{"Наименование": "Подземни ТТ кабели",
                 "количество": 500, "__excel_row__": 4}]

        assert self._index(tmp_path, rows)[0].unit == ""


class TestSplitQuantityIsNotAMismatch:
    """Част от ред НЕ е несъвпадение — един ред нарочно се дели между зони.

    ЖИВ ПРОГОН 14.08.2026: 115 от 134 цитата излязоха „не съвпадат" и
    приложението показа червена тревога върху коректен график.  Сравняваше се
    количеството на ЕДНА задача с ЦЯЛОТО количество на реда: 7761 m² бордюри
    стават 1500 + 3000 + 600 + …

    Че сборът е верен, го пази `check_conservation` (Σ = КСС).

    FAILURE означава: работещ график пак ще изглежда счупен.
    """

    def test_a_part_of_the_row_is_verified(self, index):
        schedule = [{"id": "T1", "name": "X", "length_m": 100,
                     "source_ref": "КСС.xlsx!Водопровод!4"}]
        отчет = verify_citations(schedule, index)
        assert отчет.get("mismatch", 0) == 0, отчет.get("problems")

    def test_more_than_the_row_is_still_a_defect(self, index):
        schedule = [{"id": "T1", "name": "X", "length_m": 10_000,
                     "source_ref": "КСС.xlsx!Водопровод!4"}]
        assert verify_citations(schedule, index)["mismatch"] == 1
