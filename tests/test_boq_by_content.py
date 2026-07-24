"""Unit tests: разпознаване на количествена сметка по СЪДЪРЖАНИЕ (BACKLOG т.1).

Класификацията решаваше кой файл е КСС само по КЛЮЧОВИ ДУМИ В ИМЕТО, при това
преди конверсията — тоест съдържание още нямаше.

Последица: файл „Техническо предложение.pdf" с таблицата с количества вътре
се класифицираше като незадължителен, не се намираше КСС и генерирането се
блокираше — при налични количества.  В българската практика количествата
често са приложение към предложението или целият пакет е един PDF.

Обратното също: файл, кръстен „КСС.xlsx", но съдържащ друго, минаваше без
никаква проверка.

FAILURE означава: блокировката отново пази от неудобно име на файл, вместо от
липсващи количества.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.file_manager import FileManager  # noqa: E402

BOQ_TABLE = """Наименование на дейността | Мярка | Количество
1 Разваляне на асфалтова настилка | м2 | 1240
2 Изкоп за тръбна траншея DN110 | м3 | 892
3 Доставка и полагане PE 100 RC DN110 | м | 420
4 Монтаж на спирателен кран DN110 | бр. | 6
5 Засипване и уплътняване на траншея | м3 | 892
6 Възстановяване на асфалтова настилка | м2 | 1240"""

PROSE = (
    "Техническо предложение. Изпълнителят предлага да изпълни обекта съгласно "
    "техническата спецификация на възложителя. Ще бъдат ангажирани два екипа. "
    "Срокът за изпълнение е 240 календарни дни. Прилага се програма за "
    "управление на качеството и план за безопасност и здраве. "
) * 4

SITUATION = (
    "Ситуация М 1:500. ул. Христо Ботев. ул. Витоша. ОТ 12. ОТ 18. "
    "Съществуващ водопровод. Проектен водопровод. Шахта. "
) * 6


# ===================================================================
# looks_like_boq
# ===================================================================

def test_plain_boq_table_is_recognised():
    result = FileManager.looks_like_boq(BOQ_TABLE)
    assert result["is_boq"] is True
    assert result["confidence"] >= 0.6


def test_prose_document_is_not_a_boq():
    assert FileManager.looks_like_boq(PROSE)["is_boq"] is False


def test_site_plan_text_is_not_a_boq():
    """Ситуацията споменава обекти, но няма количествена таблица."""
    assert FileManager.looks_like_boq(SITUATION)["is_boq"] is False


def test_proposal_containing_a_boq_is_recognised():
    """Ядрото на поправката: количествата са ВЪТРЕ в предложението."""
    assert FileManager.looks_like_boq(PROSE + "\n" + BOQ_TABLE)["is_boq"] is True


def test_short_text_is_never_a_boq():
    assert FileManager.looks_like_boq("Мярка Количество")["is_boq"] is False


def test_empty_text_is_handled():
    assert FileManager.looks_like_boq("")["is_boq"] is False
    assert FileManager.looks_like_boq(None or "")["confidence"] == 0.0


def test_english_column_headers_are_recognised():
    text = "Description | Unit | Quantity\n" + "\n".join(
        f"{i} Item | m3 | {i * 100}" for i in range(1, 8)
    )
    assert FileManager.looks_like_boq(text)["is_boq"] is True


def test_evidence_explains_the_verdict():
    """Решението трябва да е проверимо от човек, не да се приема наум."""
    evidence = FileManager.looks_like_boq(BOQ_TABLE)["evidence"]
    assert any("колони" in e for e in evidence)
    assert any("мерна единица" in e for e in evidence)


def test_rows_are_counted_per_line_not_per_block():
    """Заварен дефект: групиране по 200-знакови блокове недоброяваше
    сбити таблици — при 6 реда даваше 2."""
    evidence = FileManager.looks_like_boq(BOQ_TABLE)["evidence"]
    assert any("6 реда" in e for e in evidence)


def test_column_headers_alone_are_not_enough():
    """Само заглавия без редове не правят документа количествена сметка."""
    text = "Мярка Количество\n" + PROSE
    result = FileManager.looks_like_boq(text)
    assert result["confidence"] < 1.0


def test_units_alone_are_not_enough():
    """Технически текст, споменаващ м3 многократно, не е КСС."""
    text = "\n".join(f"Дълбочината на изкопа е {i} м на този участък."
                     for i in range(1, 10))
    assert FileManager.looks_like_boq(text)["is_boq"] is False


# ===================================================================
# find_boq_by_content — върху конвертирани файлове
# ===================================================================

def _project(tmp_path: Path, files: dict[str, str]) -> FileManager:
    converted = tmp_path / "converted"
    converted.mkdir(parents=True)
    for name, text in files.items():
        (converted / f"{Path(name).stem}.json").write_text(
            json.dumps({"source_file": name, "full_text": text}, ensure_ascii=False),
            encoding="utf-8",
        )
    manager = FileManager()
    manager.base_path = tmp_path
    return manager


def test_finds_boq_inside_a_differently_named_file(tmp_path):
    manager = _project(tmp_path, {
        "Техническо предложение.pdf": PROSE + "\n" + BOQ_TABLE,
        "Ситуация.pdf": SITUATION,
    })
    result = manager.find_boq_by_content()
    assert result["found"] == ["Техническо предложение.pdf"]


def test_reports_verdict_for_every_file(tmp_path):
    """Отказът трябва да казва какво е видяно във всеки файл."""
    manager = _project(tmp_path, {
        "Договор.pdf": PROSE,
        "Ситуация.pdf": SITUATION,
    })
    result = manager.find_boq_by_content()
    assert result["found"] == []
    assert set(result["details"]) == {"Договор.pdf", "Ситуация.pdf"}


def test_multiple_boq_files_are_all_found(tmp_path):
    manager = _project(tmp_path, {
        "Част 1.pdf": BOQ_TABLE,
        "Част 2.pdf": BOQ_TABLE,
    })
    assert len(manager.find_boq_by_content()["found"]) == 2


def test_missing_converted_dir_is_safe(tmp_path):
    manager = FileManager()
    manager.base_path = tmp_path
    assert manager.find_boq_by_content() == {"found": [], "details": {}}


def test_no_base_path_is_safe():
    manager = FileManager()
    assert manager.find_boq_by_content()["found"] == []


def test_corrupt_json_is_skipped(tmp_path):
    converted = tmp_path / "converted"
    converted.mkdir(parents=True)
    (converted / "broken.json").write_text("{ не е json", encoding="utf-8")
    (converted / "ok.json").write_text(
        json.dumps({"source_file": "КСС.xlsx", "full_text": BOQ_TABLE},
                   ensure_ascii=False), encoding="utf-8")
    manager = FileManager()
    manager.base_path = tmp_path
    assert manager.find_boq_by_content()["found"] == ["КСС.xlsx"]


def test_manifest_is_ignored(tmp_path):
    manager = _project(tmp_path, {"КСС.xlsx": BOQ_TABLE})
    (tmp_path / "converted" / "_manifest.json").write_text(
        json.dumps({"source_file": "_manifest", "full_text": BOQ_TABLE}),
        encoding="utf-8")
    assert manager.find_boq_by_content()["found"] == ["КСС.xlsx"]


# ===================================================================
# Регресия за самата причина
# ===================================================================

def test_app_falls_back_to_content_when_name_search_fails():
    """Без това блокировката пак пази от името, не от липсата на количества."""
    source = (Path(__file__).parent.parent / "app.py").read_text(encoding="utf-8")
    assert "find_boq_by_content" in source
    assert "boq_by_content" in source
