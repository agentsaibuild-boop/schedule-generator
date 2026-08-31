"""Пропуснат ред от КСС не бива да остава мълчалив.

FAILURE означава: количество, което четецът не е видял, пак може да мине
незабелязано — а тогава `quantity_conservation_ok` не значи нищо.  Той сверява
разпределеното срещу ИНДЕКСИРАНОТО: ред, който никога не сме видели, липсва и
от двете страни на сметката и графикът изглежда изряден.

ПРОВЕРЕНО 19.08.2026 върху истинския търг: 28 от 28 реда се четат вярно,
включително слети описания и три различни колони за количество в четири листа.
Този тест пази СЛЕДВАЩИЯ файл, не този.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.provenance import audit_unread_rows, build_quantity_index  # noqa: E402


def _проект(tmp_path: Path, редове: list[dict], име: str = "Част") -> Path:
    conv = tmp_path / "converted"
    conv.mkdir(parents=True, exist_ok=True)
    (conv / "КСС.json").write_text(json.dumps(
        {"source_file": "КСС.xlsx", "type": "excel",
         "sheets": [{"name": име, "rows": редове}]},
        ensure_ascii=False), encoding="utf-8")
    return tmp_path


РЕД = {"Наименование": "Изграждане на канализационна мрежа",
       "Ед. мярка": "m", "Количество": 1182, "__excel_row__": 9}


# ---------------------------------------------------------------------------
# Ловът
# ---------------------------------------------------------------------------


def test_a_row_with_a_number_but_no_description_is_reported(tmp_path):
    """Точният мълчалив случай: количество, което не става позиция."""
    проект = _проект(tmp_path, [РЕД, {"Ед. мярка": "m", "Количество": 640,
                                      "__excel_row__": 10}])

    отчет = audit_unread_rows(проект)

    assert отчет["indexed"] == 1
    assert len(отчет["unread"]) == 1
    assert отчет["unread"][0]["ред"] == 10
    assert 640 in отчет["unread"][0]["числа"]


def test_a_described_row_whose_quantity_is_not_recognised_is_reported(tmp_path):
    """Мярката и описанието ги има, количеството е в неочаквана колона.

    Колоната беше „Кол." — от 31.08.2026 тя Е в речника (реално съкращение в
    българските КСС-та), затова примерът вече е наистина непознато заглавие.
    Тестът пази ГЕЙТА, не конкретното име: речникът винаги ще е непълен и
    точно затова непрочетеното количество трябва да се обявява.
    """
    проект = _проект(tmp_path, [
        РЕД,
        {"Наименование": "Доставка и полагане на бордюри", "Ед. мярка": "м",
         "Показател по норма": 7761, "__excel_row__": 11},
    ])

    отчет = audit_unread_rows(проект)

    assert len(отчет["no_quantity"]) == 1
    assert отчет["no_quantity"][0]["ред"] == 11
    assert "бордюри" in отчет["no_quantity"][0]["описание"]


def test_the_real_tender_reads_completely(tmp_path):
    """Пълен ред минава без тревога — гейт, който вика винаги, е безполезен."""
    проект = _проект(tmp_path, [РЕД])

    отчет = audit_unread_rows(проект)

    assert отчет == {"unread": [], "no_quantity": [], "indexed": 1, "sheets": 1}


# ---------------------------------------------------------------------------
# Какво НЕ бива да вдига тревога
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ред", [
    {"Наименование": "Общо", "Стойност": 12345, "__excel_row__": 13},
    {"Наименование": "ВСИЧКО", "Стойност": 999, "__excel_row__": 21},
    {"Наименование": "Ед. мярка", "Количество": 1, "__excel_row__": 5},
])
def test_totals_and_headers_are_not_missed_rows(tmp_path, ред):
    отчет = audit_unread_rows(_проект(tmp_path, [РЕД, ред]))

    assert отчет["unread"] == []
    assert отчет["no_quantity"] == []


def test_the_ordinal_column_is_not_mistaken_for_a_quantity(tmp_path):
    """Колоната „№“ е 1, 2, 3 — обобщаващият лист е пълен с такива."""
    отчет = audit_unread_rows(_проект(tmp_path, [
        {"№": 1, "ДЕЙНОСТ": "ПРОЕКТИРАНЕ", "СТОЙНОСТ": None, "__excel_row__": 4},
        {"№": 2, "ДЕЙНОСТ": "АВТОРСКИ НАДЗОР", "СТОЙНОСТ": None,
         "__excel_row__": 5},
    ]))

    assert отчет["unread"] == []
    assert отчет["no_quantity"] == []


def test_a_project_without_converted_documents_is_not_an_error(tmp_path):
    assert audit_unread_rows(tmp_path)["indexed"] == 0


def test_the_audit_agrees_with_what_the_index_actually_took(tmp_path):
    """Двете страни трябва да броят едно и също, иначе одитът лъже."""
    редове = [РЕД,
              {"Наименование": "СКО", "Ед. мярка": "брой", "Количество": 180,
               "__excel_row__": 24}]
    проект = _проект(tmp_path, редове)

    отчет = audit_unread_rows(проект)
    индекс = [r for r in build_quantity_index(проект) if r.quantity is not None]

    assert отчет["indexed"] == len(индекс) == 2
