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
        # Кабелът има СВОЙ клас (жив прогон 2026-08-06).  Преди беше None
        # („ambiguous"), тоест редът не можеше да бъде покрит от НИЩО и всеки
        # реален проект с ЕЛ/ТТ част оставаше блокиран.  Защитата от одит v18 е
        # запазена по друг начин: полагане на ВОДОПРОВОД е 'laying' ≠ 'cable',
        # затова пак не покрива кабелен ред — виж теста по-долу.
        "Подземни ЕЛ кабели": "cable",
    }
    for desc, cls in expect.items():
        assert got.get(desc) == cls, f"{desc!r}: очаквано {cls}, получено {got.get(desc)}"


def test_no_unit_based_false_coverage(tmp_path):
    """Одит v18: кабелен ред (м) НЕ се покрива фалшиво от полагане на водопровод
    само защото мярката е „м" и числото съвпада.

    2026-08-06: редът вече има свой клас-покривач ('cable'), затова остава в
    `uncovered`, а не в `ambiguous`.  И двете блокират експорта — важното
    (никакво фалшиво покритие) не се е променило."""
    idx = _index(tmp_path)
    cable = next(r for r in idx if "кабел" in r.description)
    laying_task = {"id": "T", "name": "Полагане водопровод DN110 PE",
                   "length_m": 500.0, "unit": "м", "source_ref": cable.ref}
    cov = analyze_boq_coverage([laying_task], [cable])
    assert cov["covered"] == []                    # НЕ е фалшиво покрит
    assert cable.ref in cov["uncovered"]           # блокира експорта
    assert any(d["ref"] == cable.ref for d in cov["derived"])   # видяно, но не покрива


def test_cable_row_is_covered_by_cable_laying(tmp_path):
    """Обратната страна: полагане на КАБЕЛ покрива кабелния ред."""
    idx = _index(tmp_path)
    cable = next(r for r in idx if "кабел" in r.description)
    task = {"id": "T", "name": "Доставка и полагане на подземен ЕЛ кабел",
            "length_m": cable.quantity, "unit": "м", "source_ref": cable.ref}
    cov = analyze_boq_coverage([task], [cable])
    assert cov["covered"] == [cable.ref]


def test_cable_trench_works_do_not_double_cover(tmp_path):
    """Изкоп/засипване по кабела НЕ са покривачи — иначе сборът би препокрил."""
    idx = _index(tmp_path)
    cable = next(r for r in idx if "кабел" in r.description)
    half = (cable.quantity or 0) / 2
    tasks = [
        {"id": "L1", "name": "Полагане на ЕЛ кабел — Фронт 1",
         "length_m": half, "unit": "м", "source_ref": cable.ref},
        {"id": "L2", "name": "Полагане на ЕЛ кабел — Фронт 2",
         "length_m": half, "unit": "м", "source_ref": cable.ref},
        {"id": "E1", "name": "Изкоп за кабелна траншея — ЕЛ кабел",
         "length_m": half, "unit": "м", "source_ref": cable.ref},
        {"id": "B1", "name": "Засипване на траншея — ЕЛ кабел",
         "length_m": half, "unit": "м", "source_ref": cable.ref},
    ]
    cov = analyze_boq_coverage(tasks, [cable])
    assert cov["covered"] == [cable.ref]
    assert cov["over_covered"] == {}


def test_synthetic_kss_coverage_is_reproducible():
    """Одит 2026-08 т.7: независимо възпроизводим before/after артефакт.

    Синтетичният КСС (генерична нотация, без клиентски данни) трябва да дава
    стабилно покритие — доказва, че детекцията+нормите работят на реалистична
    българска нотация (Ф-диаметри, PP, 'брой', кв.м), проверимо от пакета."""
    from tools.kss_coverage_demo import run
    out = run()
    # 13 → 14 на 19.08.2026: „Водомерна шахта" получи норма от еталонния
    # график Илиянци (5 работни дни за 1 брой).  Числото е ЗАПИС на покритието,
    # не цел — расте, когато норма влезе, и това трябва да се вижда в диф.
    assert out["proven"] == 14, out
    assert out["total"] == 16
    assert not out["mismatches"], out["mismatches"]
    assert out["codes"]["CALCULATED"] == 14


# ---------------------------------------------------------------------------
# Нецитираните количества — сляпото петно, през което минаваше дублирането
# ---------------------------------------------------------------------------


def test_cloned_front_without_citation_is_caught(tmp_path):
    """КОРЕННИЯТ ДЕФЕКТ (съпоставка с еталон, 2026-08-06).

    В реалния прогон „Фронт 1" и „Фронт 2" носеха ПЪЛНОТО количество бордюри
    (3880,5 + 3880,5 при 7761 в КСС).  Проверката за дублиране не се задейства,
    ако клонингът НЯМА `source_ref`: сборът се смята само по цитиращите задачи,
    затова редът излизаше чисто покрит, а в графика стоеше двойна работа.

    FAILURE означава: непроследима работа пак може да влезе в графика невидимо.
    """
    idx = _index(tmp_path)
    kerbs = next(r for r in idx if "бордюри" in r.description)
    tasks = [
        {"id": "F1", "name": "Доставка и полагане на бордюри — Фронт 1",
         "quantity": kerbs.quantity, "unit": "м", "source_ref": kerbs.ref},
        # Клонингът: същото количество, БЕЗ цитат.
        {"id": "F2", "name": "Доставка и полагане на бордюри — Фронт 2",
         "quantity": kerbs.quantity, "unit": "м", "source_ref": ""},
    ]

    cov = analyze_boq_coverage(tasks, [kerbs])

    assert cov["covered"] == [kerbs.ref]        # цитиращата задача покрива реда
    flagged = {u["id"] for u in cov["uncited_production"]}
    assert "F2" in flagged, "нецитираният клонинг трябва да е уловен"
    assert cov["uncited_production"][0]["reason"] == "missing_ref"


def test_uncited_task_without_quantity_is_not_flagged(tmp_path):
    """Геодезия/ВОБД/изпитване нямат количество — те не могат да надуят сбора."""
    idx = _index(tmp_path)
    kerbs = next(r for r in idx if "бордюри" in r.description)
    tasks = [
        {"id": "S1", "name": "Въвеждане на ВОБД и трасиране", "source_ref": ""},
        {"id": "F1", "name": "Доставка и полагане на бордюри",
         "quantity": kerbs.quantity, "unit": "м", "source_ref": kerbs.ref},
    ]

    cov = analyze_boq_coverage(tasks, [kerbs])

    assert cov["uncited_production"] == []


def test_invented_ref_with_quantity_is_flagged(tmp_path):
    """Цитат към несъществуващ ред е по-лош от липсващ — изглежда като доказателство."""
    idx = _index(tmp_path)
    kerbs = next(r for r in idx if "бордюри" in r.description)
    tasks = [
        {"id": "F1", "name": "Доставка и полагане на бордюри",
         "quantity": kerbs.quantity, "unit": "м", "source_ref": kerbs.ref},
        {"id": "X1", "name": "Доставка и полагане на бордюри — измислен ред",
         "quantity": 500.0, "unit": "м", "source_ref": "КСС.xlsx!Няма!999"},
    ]

    cov = analyze_boq_coverage(tasks, [kerbs])

    flagged = {u["id"]: u["reason"] for u in cov["uncited_production"]}
    assert flagged.get("X1") == "invalid_ref"
