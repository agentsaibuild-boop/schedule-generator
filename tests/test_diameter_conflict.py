"""Unit tests: документ, който си противоречи, не се решава мълчаливо.

ОДИТ 10.08.2026, P1.4: „Редът за главни водопроводни клонове съдържа конфликт:
description F200 E, diameter column F225.  Това трябва да стане
DIAMETER_CONFLICT и да изисква human resolution."

Досега описанието и всички клетки на реда се слепваха в един низ и `detect_dn`
взимаше първото намерено число.  Тоест при противоречие програмата избираше —
и после смяташе продължителност по избраното, без да каже, че е избирала.

Това е същият дефект като „уверено сгрешена продължителност": наличието на
отговор не значи, че въпросът е решен.

FAILURE означава: КСС ред с два различни диаметъра пак дава уверено число.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.provenance import build_quantity_index  # noqa: E402
from src.work_package import (  # noqa: E402
    _row_pipe_spec,
    diameter_conflict,
    packages_from_ai,
)

FIXTURE = Path(__file__).parent / "fixtures" / "kss_anonymized"

#: Редът от реалния търг, който одиторът посочи.
CONFLICTING_REF = "КСС-пример.xlsx!2. Chast Vodoprovodna!12"


@pytest.fixture
def boq():
    return [r for r in build_quantity_index(FIXTURE) if r.quantity is not None]


@pytest.fixture
def conflicting(boq):
    return next(r for r in boq if r.ref == CONFLICTING_REF)


@pytest.fixture
def unresolved(conflicting):
    """Същото противоречие, но в документ БЕЗ човешко решение.

    Реалният ред вече е решен (Ф225, 10.08.2026), затова общият механизъм се
    проверява върху ред, за който решение няма — иначе тестовете щяха да
    описват само този един случай.
    """
    from src.provenance import QuantityRow, SourceRef

    return QuantityRow(
        description=conflicting.description, quantity=conflicting.quantity,
        unit=conflicting.unit,
        source=SourceRef("ДругТърг.xlsx", conflicting.source.sheet, 12,
                         conflicting.source.column),
        raw=conflicting.raw)


# ---------------------------------------------------------------------------
# Разпознаване
# ---------------------------------------------------------------------------


def test_the_audited_row_is_flagged(unresolved):
    assert diameter_conflict(unresolved) == (200, 225)


def test_only_one_row_in_the_tender_ever_conflicted(boq, unresolved):
    """Гейт, който вика при всеки ред, е безполезен.

    Проверява се, че противоречието е било точно едно: този ред, преди да
    бъде решен.  Останалите 27 не са спорни в никакъв вид.
    """
    from src.work_package import resolved_value

    others = [r for r in boq if r.ref != CONFLICTING_REF]

    assert diameter_conflict(unresolved) is not None
    assert all(diameter_conflict(r) is None for r in others)
    assert all(resolved_value(r, "dn") is None for r in others)


def test_agreeing_sources_are_not_a_conflict(boq):
    """Ф300 в описанието и Ф300 в колоната е съгласие, не спор."""
    agreeing = [r for r in boq
                if r.ref != CONFLICTING_REF and "мрежа" in r.description.lower()]

    assert agreeing
    assert all(diameter_conflict(r) is None for r in agreeing)


def test_a_silent_source_is_not_a_conflict(boq):
    """Ред без колона за диаметър няма с какво да противоречи."""
    pavement = [r for r in boq if "асфалт" in r.description.lower()
                or "бордюр" in r.description.lower()]

    assert pavement
    assert all(diameter_conflict(r) is None for r in pavement)


# ---------------------------------------------------------------------------
# Последствие: диаметърът не се приема
# ---------------------------------------------------------------------------


def test_no_diameter_is_derived_from_an_undecided_contradiction(unresolved):
    """Недоказано е по-добре от уверено сгрешено."""
    dn, _ = _row_pipe_spec(unresolved)

    assert dn is None


def test_the_conflict_does_not_touch_the_material(conflicting):
    """Спорът е за диаметъра.  Материалът се извлича, както винаги —
    независимо дали го има (в този ред „Ф200 E" не дава разпознаваем материал).
    """
    from src.duration_calculator import detect_material

    raw = conflicting.raw
    cells = " ".join(str(v) for k, v in raw.items()
                     if v not in (None, "") and not str(k).startswith("__"))
    expected = detect_material({"name": f"{conflicting.description} {cells}"}) or ""

    assert _row_pipe_spec(conflicting)[1] == expected


def test_other_rows_still_get_their_diameter(boq):
    sewer = next(r for r in boq
                 if "смесена канализационна" in r.description.lower())

    assert _row_pipe_spec(sewer)[0] is not None


# ---------------------------------------------------------------------------
# Конфликтът се вижда отвън
# ---------------------------------------------------------------------------


def test_an_undecided_conflict_is_reported_not_swallowed(unresolved):
    payload = {"packages": [{
        "id": "W1", "network": "В", "chain": "water_section",
        "items": [{"source_ref": unresolved.ref, "quantity": unresolved.quantity}],
    }]}

    packages, errors = packages_from_ai(payload, boq_index=[unresolved])

    assert packages, "пакетът не бива да изчезва — количеството е валидно"
    conflicts = [e for e in errors if "DIAMETER_CONFLICT" in e]
    assert len(conflicts) == 1
    assert "DN200" in conflicts[0] and "DN225" in conflicts[0]


def test_a_decided_conflict_is_no_longer_reported(boq, conflicting):
    payload = {"packages": [{
        "id": "W1", "network": "В", "chain": "water_section",
        "items": [{"source_ref": conflicting.ref, "quantity": conflicting.quantity}],
    }]}

    _, errors = packages_from_ai(payload, boq_index=boq)

    assert [e for e in errors if "DIAMETER_CONFLICT" in e] == []


def test_the_quantity_is_untouched_by_the_conflict(boq, conflicting):
    """Спорът е за диаметъра, не за количеството — Σ = КСС не се влияе."""
    payload = {"packages": [{
        "id": "W1", "network": "В", "chain": "water_section",
        "items": [{"source_ref": conflicting.ref, "quantity": conflicting.quantity}],
    }]}

    packages, _ = packages_from_ai(payload, boq_index=boq)

    assert packages[0].items[0].quantity == pytest.approx(conflicting.quantity)


# ---------------------------------------------------------------------------
# Човешкото решение — и защо ключът е съдържанието, а не редът
# ---------------------------------------------------------------------------
#
# 10.08.2026: възложителят реши — тръбите са Ф225.  Описанието в КСС носи стар
# диаметър.  Решението се записва в `config/boq_resolutions.json` с автор и
# дата, а НЕ се вписва в кода или в самия документ.
#
# FAILURE означава: или решението не се прилага, или се прилага на грешен ред.

def test_the_decided_diameter_is_used(conflicting):
    from src.work_package import resolved_value

    assert resolved_value(conflicting, "dn") == 225
    assert _row_pipe_spec(conflicting)[0] == 225


def test_a_decided_row_is_no_longer_a_conflict(conflicting):
    assert diameter_conflict(conflicting) is None


def test_the_tender_has_no_unresolved_conflicts_left(boq):
    assert [r.ref for r in boq if diameter_conflict(r)] == []


def test_the_decision_is_keyed_by_content_not_by_row(conflicting):
    """Вмъкнат ред в Excel мести номерата.  Решението трябва да оцелее."""
    from src.provenance import QuantityRow, SourceRef
    from src.work_package import resolved_value

    moved = QuantityRow(
        description=conflicting.description, quantity=conflicting.quantity,
        unit=conflicting.unit,
        source=SourceRef(conflicting.source.document, conflicting.source.sheet,
                         777, conflicting.source.column),
        raw=conflicting.raw)

    assert resolved_value(moved, "dn") == 225


def test_a_changed_quantity_invalidates_the_decision(conflicting):
    """Друго количество значи друг ред — решението трябва да се вземе наново."""
    from src.provenance import QuantityRow
    from src.work_package import resolved_value

    altered = QuantityRow(conflicting.description, 999.0, conflicting.unit,
                          conflicting.source, conflicting.raw)

    assert resolved_value(altered, "dn") is None


def test_the_decision_does_not_leak_onto_other_rows(boq):
    from src.work_package import resolved_value

    others = [r for r in boq if r.ref != CONFLICTING_REF]

    assert all(resolved_value(r, "dn") is None for r in others)


def test_the_decision_records_who_and_when():
    """Решение без автор и дата не е проследимо — а точно това го прави решение."""
    from src.work_package import load_boq_resolutions

    entry = next(r for r in load_boq_resolutions() if r.get("field") == "dn")

    assert entry.get("decided_by") and entry.get("decided_on")
    assert entry.get("conflict") and entry.get("note")
