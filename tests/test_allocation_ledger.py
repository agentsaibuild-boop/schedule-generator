"""Unit tests: анонимизиран КСС fixture + опис на разпределението.

ОДИТ 2026-08-07: „От предоставения пакет не мога независимо да докажа
Σ allocated = КСС, защото самата анонимизирана 28-row КСС липсва.  Имам само
резултата на техния gate."

Справедливо: давали сме присъда, а не доказателство.  Тук са и двете —
fixture със същата структура като реалния търг (без клиентски данни) и опис,
по който сборът може да се пресметне независимо.

FAILURE означава: твърдението „всяко количество е разпределено точно веднъж"
пак не може да бъде проверено отвън.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.provenance import build_quantity_index  # noqa: E402
from src.work_package import (  # noqa: E402
    PackageItem,
    SpatialWorkPackage,
    allocation_ledger,
    check_conservation,
    format_allocation_ledger,
)

FIXTURE = Path(__file__).parent / "fixtures" / "kss_anonymized"


@pytest.fixture
def boq():
    return [r for r in build_quantity_index(FIXTURE) if r.quantity is not None]


# ---------------------------------------------------------------------------
# Fixture-ът
# ---------------------------------------------------------------------------


def test_fixture_reproduces_the_real_tender_shape(boq):
    """Същата структура като реалния търг: 28 реда в четири части."""
    sheets = {r.source.sheet for r in boq}

    assert len(boq) == 28
    assert len(sheets) == 4


def test_fixture_carries_no_client_data():
    """Нито името на обекта, нито цени.

    Регистърът има значение: първата версия на този тест търсеше само
    името на обекта с главна буква и пропусна осем срещания на изписването
    с ГЛАВНИ букви в хедърите на листовете.  Затова сравнението е по
    `lower()`.

    Самото име НЕ стои тук: списъкът е локален и извън git (виж
    `anonymize_kss.load_client_names`), защото литерал в тест пътува в
    историята точно както литерал в код.  Без него проверката за цени пак
    върши работа, а тази за името се пропуска ЯВНО — мълчаливото ѝ
    отпадане би приличало на минал тест.
    """
    sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
    from anonymize_kss import load_client_names

    raw = (FIXTURE / "converted" / "КСС-пример.json").read_text(encoding="utf-8")
    data = json.loads(raw)

    names = load_client_names()
    if not names:
        pytest.skip("няма локален config/client_names.local.json — "
                    "проверката за име на обект не може да се направи")
    lowered = raw.lower()
    assert not [n for n in names if n in lowered]

    priced = [v for sheet in data["sheets"] for row in sheet["rows"]
              for col, v in row.items()
              if "цена" in str(col).lower() and v not in (None, "")]
    assert priced == []


#: Количествата от търга — същите, които стоят в описа на разпределението.
#: Одит 07.08.2026: „fixture ≈ ledger × 0,731396, значи от предоставения пакет
#: не мога да възпроизведа 28/28."  Анонимизацията беше мащабирала числата;
#: количествата в открита процедура не са клиентски данни и се пазят автентични.
TENDER_QUANTITIES = [
    538.12, 1758.86, 68.6, 881.45, 174, 1,               # водопроводна
    1182, 260, 509, 525, 215, 226, 230,                  # смесена канализация
    74.5056, 55.428, 677.6,                              # бетонови кожуси
    620, 308,                                            # дъждовна канализация
    180, 100, 60, 1, 10,                                 # СКО, УО, шахти
    10824, 7761, 18671,                                  # пътна
    500, 500,                                            # ЕЛ и ТТ
]


def test_fixture_quantities_are_the_tender_quantities(boq):
    """Одиторът трябва да смята Σ = КСС от fixture-а, не да ни вярва.

    FAILURE означава: пакетът пак твърди 28/28, без да го доказва отвън.
    """
    assert sorted(float(r.quantity) for r in boq) == sorted(
        float(q) for q in TENDER_QUANTITIES
    )


def test_fixture_counts_are_whole(boq):
    """Редовете на брой носят броя, не мащабирана дължина.

    Мащабирането се прилагаше върху колоната „Дължина /m/", която за тези
    редове държи БРОЯ — fixture-ът даваше 127,26 СВО и 0,73 преливни шахти.
    """
    counted = [r for r in boq if str(r.unit).strip().lower().startswith(("брой", "бр"))]

    assert counted, "нито един ред на брой — fixture-ът не е представителен"
    assert all(float(r.quantity).is_integer() for r in counted), {
        r.description: r.quantity for r in counted
        if not float(r.quantity).is_integer()
    }


def test_fixture_keeps_the_diameter_column(boq):
    """DN се вади от съседна колона — без нея продължителностите не се смятат."""
    from src.work_package import _row_pipe_spec

    pipes = [r for r in boq if "мрежа" in r.description.lower()]
    specs = [_row_pipe_spec(r) for r in pipes]

    assert any(dn for dn, _ in specs), "нито един ред не дава DN"


# ---------------------------------------------------------------------------
# Описът
# ---------------------------------------------------------------------------


def _split(row, *fractions):
    return [
        SpatialWorkPackage(
            id=f"K{i}", network="К", chain="sewer_section",
            items=(PackageItem(row.ref, "laying", row.quantity * f,
                               row.unit, row.description),))
        for i, f in enumerate(fractions, 1)
    ]


def test_ledger_shows_where_every_quantity_went(boq):
    row = boq[0]
    packages = _split(row, 0.4, 0.6)

    entry = next(e for e in allocation_ledger(packages, [row]) if e["ref"] == row.ref)

    assert entry["status"] == "ок"
    assert entry["allocated"] == pytest.approx(row.quantity)
    assert [p["package"] for p in entry["packages"]] == ["K1", "K2"]
    assert sum(p["quantity"] for p in entry["packages"]) == pytest.approx(row.quantity)


def test_ledger_marks_unallocated_rows(boq):
    entries = {e["ref"]: e for e in allocation_ledger([], boq)}

    assert all(e["status"] == "НЕРАЗПРЕДЕЛЕН" for e in entries.values())
    assert len(entries) == 28


def test_ledger_marks_over_and_short(boq):
    row = boq[0]
    over = allocation_ledger(_split(row, 0.7, 0.7), [row])[0]
    short = allocation_ledger(_split(row, 0.3), [row])[0]

    assert over["status"] == "ПРЕВИШЕН" and over["difference"] > 0
    assert short["status"] == "НЕДОСТИГ" and short["difference"] < 0


def test_ledger_agrees_with_the_conservation_gate(boq):
    """Описът и гейтът трябва да казват едно и също — иначе едното лъже."""
    packages = []
    for i, row in enumerate(boq, 1):
        packages.append(SpatialWorkPackage(
            id=f"P{i}", network="К", chain="sewer_section",
            items=(PackageItem(row.ref, "laying", row.quantity,
                               row.unit, row.description),)))

    ledger = allocation_ledger(packages, boq)
    report = check_conservation(packages, boq)

    assert report["ok"] is True
    assert all(e["status"] == "ок" for e in ledger)


def test_ledger_links_the_tasks_that_carry_the_quantity(boq):
    row = boq[0]
    packages = _split(row, 1.0)
    tasks = [{"id": "K1_laying", "source_ref": row.ref},
             {"id": "K1_survey"}]

    entry = allocation_ledger(packages, [row], tasks)[0]

    assert entry["tasks"] == ["K1_laying"]


def test_ledger_renders_a_readable_table(boq):
    row = boq[0]
    text = format_allocation_ledger(allocation_ledger(_split(row, 0.5, 0.5), [row]))

    assert "| Ред от КСС |" in text
    assert "K1: " in text and "K2: " in text
    assert "1 от 1 реда са разпределени точно" in text
