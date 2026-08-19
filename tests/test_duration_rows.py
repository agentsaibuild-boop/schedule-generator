"""Ред с мярка „Календарни Дни" е СРОК, не работа за разпределяне.

FAILURE означава: договорна продължителност, обявена в КСС, пак ще бъде
искана в участък — ще остане неразпределима, ще счупи Σ = КСС и пакетният
път ще се обърне към модела заради нещо, което няма къде да отиде.

ОТКРИТО 19.08.2026: клиентът форматира КСС и добави лист „Проектиране и
надзор“ — ПРОЕКТИРАНЕ 120 и АВТОРСКИ НАДЗОР 660, мярка „Календарни Дни“.
Това е правилно написан търг.  Два реда изключваха детерминистичния ход.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.execution_batches import allocate_execution_batches  # noqa: E402
from src.provenance import build_quantity_index, is_duration_row  # noqa: E402


class _Ред:
    def __init__(self, unit, quantity=1.0, ref="R1", description="X"):
        self.unit = unit
        self.quantity = quantity
        self.ref = ref
        self.description = description


# ---------------------------------------------------------------------------
# Разпознаването е по МЯРКАТА, не по думи в описанието
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("мярка", [
    "Календарни Дни", "календарни дни", "кал. дни", "дни", "работни дни",
    "месеца", "days",
])
def test_time_units_are_durations(мярка):
    assert is_duration_row(_Ред(мярка))


@pytest.mark.parametrize("мярка", ["m", "м", "кв. м", "брой", "бр.", "m3/m'", ""])
def test_work_units_are_not(мярка):
    assert not is_duration_row(_Ред(мярка))


def test_it_does_not_guess_from_the_description():
    """„ПРОЕКТИРАНЕ" в метри си е работа; следващият търг го нарича иначе."""
    assert not is_duration_row(_Ред("m", description="ПРОЕКТИРАНЕ на трасето"))


# ---------------------------------------------------------------------------
# Дългата мярка трябва да оцелее при четенето
# ---------------------------------------------------------------------------


def test_a_long_time_unit_survives_the_reader(tmp_path):
    """„Календарни Дни" е 14 знака, а прагът срещу описания беше 12."""
    conv = tmp_path / "converted"
    conv.mkdir(parents=True)
    (conv / "КСС.json").write_text(json.dumps({
        "source_file": "КСС.xlsx", "type": "excel",
        "sheets": [{"name": "Проектиране и надзор", "rows": [
            {"ДЕЙНОСТ": "ПРОЕКТИРАНЕ", "Мярка": "Календарни Дни",
             "Количество": 120, "__excel_row__": 4},
        ]}]}, ensure_ascii=False), encoding="utf-8")

    ред = build_quantity_index(tmp_path)[0]

    assert ред.unit == "Календарни Дни"
    assert is_duration_row(ред)


def test_a_description_in_the_unit_column_is_still_rejected(tmp_path):
    """Защитата, заради която прагът съществува, остава."""
    conv = tmp_path / "converted"
    conv.mkdir(parents=True)
    (conv / "КСС.json").write_text(json.dumps({
        "source_file": "КСС.xlsx", "type": "excel",
        "sheets": [{"name": "Пътна", "rows": [
            {"Наименование": "Бордюри", "Ед. мярка":
             "Доставка и полагане на средни бетонови бордюри С18 15/25/50 см",
             "Количество": 7761, "__excel_row__": 5},
        ]}]}, ensure_ascii=False), encoding="utf-8")

    ред = build_quantity_index(tmp_path)[0]

    assert ред.unit == "", "описание пак минава за мярка"


# ---------------------------------------------------------------------------
# И не влизат в разпределението
# ---------------------------------------------------------------------------


def test_durations_are_not_allocated_to_packages():
    редове = [
        _Ред("m", 1182.0, "КСС!Kanalizaciya!4", "Изграждане на канализационна мрежа"),
        _Ред("Календарни Дни", 120.0, "КСС!Проектиране!4", "ПРОЕКТИРАНЕ"),
        _Ред("Календарни Дни", 660.0, "КСС!Проектиране!5", "АВТОРСКИ НАДЗОР"),
    ]

    резултат = allocate_execution_batches(редове, 4)

    цитирани = {i["source_ref"] for p in резултат["packages"] for i in p["items"]}
    assert цитирани == {"КСС!Kanalizaciya!4"}
    assert резултат["unroutable"] == [], "срокът мина за неразпределим ред"
    assert len(резултат["durations"]) == 2
    assert any("ПРОДЪЛЖИТЕЛНОСТ" in b for b in резултат["notes"])
