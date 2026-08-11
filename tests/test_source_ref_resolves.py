"""Unit tests: цитатът от готовия XML сочи РЕАЛНИЯ ред в КСС.

ОДИТ 10.08.2026, P0.1: „XML съдържа поле Източник (КСС), но номерът след ! не е
__excel_row__ от fixture-а.  Offset-ът е различен по sheet, следователно това не
е истински Excel row."

Проверката срещу самия .xlsx показа, че fixture-ът е верният: 1182 m наистина
стои на ред 9, асфалтът на ред 8, кабелът на ред 4.  Кодът също беше верен —
конверторът записва истинския ред.  Сгрешен беше ПРОЦЕСЪТ: изнесохме график,
генериран върху конвертирани данни от стара версия на конвертора, чиито номера
бяха с четири реда назад.  Нищо не изгърмя, защото цитатът се сверява само
срещу същия остарял индекс.

Затова тук се проверява това, което одиторът прави отвън: взима се цитат от
изхода и се търси редът в предоставения fixture.

FAILURE означава: одиторът пак не може да стигне от задача в MS Project до реда
в КСС, който тя изпълнява.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.export_xml import (  # noqa: E402
    FIELD_ID_TEXT5,
    FIELD_ID_TEXT10,
    NAMESPACE,
    export_to_mspdi_xml,
)
from src.provenance import build_quantity_index  # noqa: E402
from src.work_package import (  # noqa: E402
    PackageItem,
    SpatialWorkPackage,
    expand_packages,
    load_chains,
    packages_from_ai,
)

FIXTURE = Path(__file__).parent / "fixtures" / "kss_anonymized"

#: Одиторските контролни точки — количество → лист и РЕАЛЕН Excel ред.
#: Сверени срещу самия .xlsx, не срещу нашия индекс.
EXPECTED_ROWS = {
    1182.0: ("3. Chast Kanalizaciya", 9),
    538.12: ("2. Chast Vodoprovodna", 9),
    10824.0: ("4. Пътна", 8),
    500.0: ("5. ЕЛ и ТТ", 4),
    677.6: ("3. Chast Kanalizaciya", 18),
}


@pytest.fixture
def boq():
    return [r for r in build_quantity_index(FIXTURE) if r.quantity is not None]


# ---------------------------------------------------------------------------
# Индексът сочи реалния ред
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("quantity,expected", sorted(EXPECTED_ROWS.items()))
def test_the_index_points_at_the_real_excel_row(boq, quantity, expected):
    matches = [r for r in boq if r.quantity == pytest.approx(quantity)]

    assert matches, f"няма ред с количество {quantity}"
    assert (matches[0].source.sheet, matches[0].source.row) == expected


def test_the_citation_is_workbook_sheet_and_row(boq):
    row = next(r for r in boq if r.quantity == pytest.approx(1182.0))

    assert row.ref == "КСС-пример.xlsx!3. Chast Kanalizaciya!9"


# ---------------------------------------------------------------------------
# Ключът на записа преживява разместване на редове
# ---------------------------------------------------------------------------


def test_the_record_id_does_not_depend_on_the_row_number(boq):
    """Вмъкнат ред отгоре мести всичко отдолу; съдържанието не се мени."""
    from src.provenance import QuantityRow, SourceRef

    row = next(r for r in boq if r.quantity == pytest.approx(1182.0))
    moved = QuantityRow(
        description=row.description, quantity=row.quantity, unit=row.unit,
        source=SourceRef(row.source.document, row.source.sheet, 99, row.source.column),
        raw=row.raw)

    assert moved.record_id == row.record_id
    assert moved.ref != row.ref


def test_the_record_id_changes_when_the_quantity_changes(boq):
    """Смяна на количеството ТРЯБВА да се забележи — за това е ключът."""
    from src.provenance import QuantityRow

    row = next(r for r in boq if r.quantity == pytest.approx(1182.0))
    altered = QuantityRow(row.description, 1183.0, row.unit, row.source, row.raw)

    assert altered.record_id != row.record_id


def test_record_ids_are_unique_across_the_tender(boq):
    ids = [r.record_id for r in boq]

    assert len(set(ids)) == len(ids) == 28


# ---------------------------------------------------------------------------
# Цитатът стига до готовия файл и се резолвва обратно
# ---------------------------------------------------------------------------


def _export(boq) -> str:
    """Пълен път: КСС ред → пакет → задачи → MS Project XML."""
    payload = {"packages": [{
        "id": "K1", "network": "К", "chain": "sewer_section",
        "items": [{"source_ref": r.ref, "quantity": r.quantity} for r in boq[:6]],
    }]}
    packages, _ = packages_from_ai(payload, boq_index=boq)
    tasks = expand_packages(packages, load_chains()).tasks
    return export_to_mspdi_xml(tasks, "Тест", "2026-09-01").decode("utf-8")


def _cited_tasks(xml: str) -> list[tuple[str, str]]:
    ns = f"{{{NAMESPACE}}}"
    out = []
    for task in ET.fromstring(xml).iter(f"{ns}Task"):
        fields = {ea.findtext(f"{ns}FieldID"): ea.findtext(f"{ns}Value")
                  for ea in task.findall(f"{ns}ExtendedAttribute")}
        if fields.get(FIELD_ID_TEXT5):
            out.append((fields[FIELD_ID_TEXT5], fields.get(FIELD_ID_TEXT10) or ""))
    return out


def test_every_exported_citation_resolves_into_the_fixture(boq):
    """Точно това, което одиторът прави отвън."""
    by_ref = {r.ref: r for r in boq}
    cited = _cited_tasks(_export(boq))

    assert cited, "нито една задача не носи цитат"
    unresolved = [ref for ref, _ in cited if ref not in by_ref]
    assert unresolved == []


def test_every_exported_citation_carries_its_record_id(boq):
    by_id = {r.record_id: r for r in boq}
    cited = _cited_tasks(_export(boq))

    assert all(rec for _, rec in cited), "задача с цитат без ключ на записа"
    assert all(rec in by_id for _, rec in cited)


def test_the_citation_and_the_record_id_describe_the_same_row(boq):
    """Разминат ли се, индексът е от друга версия на документа."""
    by_ref = {r.ref: r for r in boq}
    by_id = {r.record_id: r for r in boq}

    for ref, record in _cited_tasks(_export(boq)):
        assert by_ref[ref].record_id == by_id[record].record_id, (
            f"цитатът {ref} и ключът {record} сочат различни редове")
