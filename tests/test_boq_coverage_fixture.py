"""Regression fixture: класификацията на реалния КСС формат е възпроизводима.

Одит v19 (т.6): твърденията „29% → 14% ambiguous, нула останали грешни
класификации" от пробата на реален проект не бяха независимо проверими — липсваше
обезличен fixture със същата СТРУКТУРА.  Тук има такъв: анонимизиран BOQ в
конвертирания формат (`converted/*.json`), който възпроизвежда четирите категории
от пробата, включително РАЗМЕСТЕНАТА колона (описание под грешен хедър).

FAILURE означава: индексирането/класификаторът са регресирали спрямо реалната
структура на КСС (multi-column, глагол-vs-обект, абревиатури, не-ВиК редове).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.provenance import (  # noqa: E402
    _coverer_class, analyze_boq_coverage, build_quantity_index,
)

# Обезличен КСС — СЪЩАТА структура като реалния (проба 2026-08-03), но с
# неутрални описания.  „4. Пътна" нарочно е с РАЗМЕСТЕН хедър: описанието е под
# „Ед. мярка", а под „…мрежа" стои пореден номер — точно както в реалния файл.
_SAMPLE = {
    "source_file": "КСС.xlsx",
    "sheets": [
        {"name": "2. Водопроводна", "rows": [
            {"Наименование": "Реконструкция водопровод DN110 PE",
             "количество": "100", "ед. мярка": "м", "__excel_row__": 3},
            {"Наименование": "Изкоп на траншея за водопровод",
             "количество": "144", "ед. мярка": "м3", "__excel_row__": 4},
        ]},
        {"name": "4. Пътна", "rows": [
            # РАЗМЕСТЕН хедър: описанието е под „Ед. мярка", номерът под „…мрежа"
            {"Канализационна мрежа": "1",
             "Ед. мярка": "Възстановяване на асфалтова настилка",
             "ед. мярка": "кв. м", "количество": "10824", "__excel_row__": 6},
            {"Канализационна мрежа": "2",
             "Ед. мярка": "Доставка и полагане на бетонови бордюри",
             "ед. мярка": "м", "количество": "7761", "__excel_row__": 7},
        ]},
        {"name": "3. Канализация", "rows": [
            {"Наименование": "Индивидуална монолитна РШ",
             "количество": "10", "ед. мярка": "бр", "__excel_row__": 24},
            {"Наименование": "Подземни ЕЛ кабели",
             "количество": "500", "ед. мярка": "м", "__excel_row__": 30},
        ]},
    ],
}


def _index(tmp_path):
    conv = tmp_path / "converted"
    conv.mkdir()
    (conv / "КСС.json").write_text(
        json.dumps(_SAMPLE, ensure_ascii=False), encoding="utf-8")
    return build_quantity_index(tmp_path)


def test_misaligned_column_description_is_recovered(tmp_path):
    """Одит v18/v19: описание под разместен хедър (стойност „1"/„2") се
    възстановява до най-дългата буквена клетка — иначе редът е неопределим."""
    idx = _index(tmp_path)
    patna = [r for r in idx if r.source.sheet == "4. Пътна"]
    descs = [r.description for r in patna]
    assert "1" not in descs and "2" not in descs          # номерата НЕ са описание
    assert any("настилка" in d for d in descs)
    assert any("бордюри" in d for d in descs)


def test_class_distribution_matches_expectation(tmp_path):
    """Четирите категории от пробата, възпроизведени детерминистично."""
    idx = _index(tmp_path)
    got = {r.description: _coverer_class(r) for r in idx}
    expect = {
        "Реконструкция водопровод DN110 PE": "laying",
        "Изкоп на траншея за водопровод": "excavation",
        "Възстановяване на асфалтова настилка": "pavement",     # обект бие глагол
        "Доставка и полагане на бетонови бордюри": "pavement",  # бордюр, не laying
        "Индивидуална монолитна РШ": "manhole",                 # абревиатура РШ
        "Подземни ЕЛ кабели": None,                             # не-ВиК → ambiguous
    }
    for desc, cls in expect.items():
        assert got.get(desc) == cls, f"{desc!r}: очаквано {cls}, получено {got.get(desc)}"


def test_no_unit_based_false_coverage(tmp_path):
    """Одит v18: единичен ред (кабел, м) НЕ се покрива фалшиво от laying само
    защото мярката е „м" — остава ambiguous, не covered."""
    idx = _index(tmp_path)
    cable = next(r for r in idx if "кабел" in r.description)
    laying_task = {"id": "T", "name": "Полагане водопровод DN110 PE",
                   "length_m": 500.0, "unit": "м", "source_ref": cable.ref}
    cov = analyze_boq_coverage([laying_task], [cable])
    assert cov["covered"] == []                    # НЕ е фалшиво покрит
    assert cable.ref in cov["ambiguous"]           # чака човешки преглед
