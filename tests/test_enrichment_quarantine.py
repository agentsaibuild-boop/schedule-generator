"""Unit tests: AI обогатяването не пипа логиката на графика.

Одит 2026-07-23: `enrich_for_msproject` беше ПОСЛЕДНИЯТ модификатор преди
XML експорта и сливаше седем полета от AI отговор направо в задачите. Три от
тях променят кога и в какъв ред се изпълнява работата:

  dependency_type → чете се от export_xml.py, сменя FS на SS/FF/SF
  lag_days        → чете се от export_xml.py, мести задачи в MS Project
  is_milestone    → кара duration_calculator да занули продължителността

Тоест едно AI решение можеше да пренареди графика, след като кодът вече го е
изчислил и проверил — без нито една проверка след това.

FAILURE означава: AI отново може мълчаливо да смени зависимост, лаг или да
превърне реална дейност в milestone с нулева продължителност, и това да
стигне до MS Project файла на възложителя.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai_processor import (  # noqa: E402
    SAFE_ENRICHMENT_FIELDS,
    SCHEDULING_ENRICHMENT_FIELDS,
)


# ===================================================================
# Класификацията на полетата
# ===================================================================

@pytest.mark.parametrize(
    "field", ["dependency_type", "lag_days", "is_milestone",
              "constraint_type", "risk_buffer_days"],
)
def test_scheduling_fields_are_quarantined(field):
    assert field in SCHEDULING_ENRICHMENT_FIELDS
    assert field not in SAFE_ENRICHMENT_FIELDS


@pytest.mark.parametrize("field", ["wbs", "notes_msp"])
def test_descriptive_fields_are_safe(field):
    assert field in SAFE_ENRICHMENT_FIELDS
    assert field not in SCHEDULING_ENRICHMENT_FIELDS


def test_the_two_sets_never_overlap():
    assert not (SAFE_ENRICHMENT_FIELDS & SCHEDULING_ENRICHMENT_FIELDS)


def test_fields_read_by_export_are_quarantined():
    """Регресия: точно тези две се четат от export_xml.py."""
    assert "dependency_type" in SCHEDULING_ENRICHMENT_FIELDS
    assert "lag_days" in SCHEDULING_ENRICHMENT_FIELDS


def test_field_that_zeroes_duration_is_quarantined():
    """`is_milestone` кара duration_calculator да върне 0 дни."""
    assert "is_milestone" in SCHEDULING_ENRICHMENT_FIELDS


# ===================================================================
# Поведение при сливане
# ===================================================================

def _merge(orig: dict, delta: dict) -> tuple[dict, int]:
    """Възпроизвежда логиката на сливане от enrich_for_msproject."""
    merged = {**orig}
    withheld = 0
    for key in SAFE_ENRICHMENT_FIELDS:
        if key in delta:
            merged[key] = delta[key]
    proposals = {k: delta[k] for k in SCHEDULING_ENRICHMENT_FIELDS if k in delta}
    if proposals:
        merged["msp_suggestions"] = proposals
        withheld += len(proposals)
    return merged, withheld


def test_safe_fields_are_applied():
    merged, _ = _merge({"id": "A"}, {"wbs": "1.2", "notes_msp": "бележка"})
    assert merged["wbs"] == "1.2"
    assert merged["notes_msp"] == "бележка"


def test_scheduling_fields_do_not_reach_the_task():
    merged, _ = _merge(
        {"id": "A", "dependencies": ["B"]},
        {"dependency_type": "SS", "lag_days": -10},
    )
    assert "dependency_type" not in merged
    assert "lag_days" not in merged


def test_scheduling_fields_are_kept_as_suggestions():
    """Не се изхвърлят — човек може да реши да ги приложи."""
    merged, _ = _merge({"id": "A"}, {"dependency_type": "SS", "lag_days": 5})
    assert merged["msp_suggestions"] == {"dependency_type": "SS", "lag_days": 5}


def test_withheld_count_is_reported():
    _, withheld = _merge(
        {"id": "A"}, {"dependency_type": "SS", "lag_days": 5, "wbs": "1.1"}
    )
    assert withheld == 2


def test_no_suggestions_key_when_nothing_withheld():
    merged, withheld = _merge({"id": "A"}, {"wbs": "1.1"})
    assert "msp_suggestions" not in merged
    assert withheld == 0


def test_original_task_fields_survive():
    orig = {"id": "A", "name": "Полагане", "duration": 48,
            "calculated_duration": 48, "duration_source": "calculated",
            "dependencies": ["B"], "start_day": 10, "end_day": 57}
    merged, _ = _merge(orig, {"dependency_type": "SS", "wbs": "1.1"})

    for key, value in orig.items():
        assert merged[key] == value


def test_ai_cannot_convert_task_into_milestone():
    """Задача с 48 дни не бива да стане milestone с 0 дни по AI решение."""
    merged, _ = _merge(
        {"id": "A", "duration": 48, "calculated_duration": 48},
        {"is_milestone": True},
    )
    assert merged.get("is_milestone") is not True
    assert merged["duration"] == 48
    assert merged["msp_suggestions"]["is_milestone"] is True


def test_ai_cannot_pin_dates_with_constraint():
    merged, _ = _merge({"id": "A"}, {"constraint_type": 2})
    assert "constraint_type" not in merged


def test_calculated_duration_is_untouched_by_enrichment():
    """Доказаната продължителност не бива да се променя от последна AI стъпка."""
    merged, _ = _merge(
        {"id": "A", "duration": 85, "calculated_duration": 85,
         "duration_source": "calculated"},
        {"lag_days": 20, "risk_buffer_days": 5, "is_milestone": True},
    )
    assert merged["calculated_duration"] == 85
    assert merged["duration_source"] == "calculated"


# ===================================================================
# Регресия за самия одитен извод
# ===================================================================

def test_enrichment_merge_loop_uses_the_field_sets():
    """Списъкът с полета не бива пак да се зашие в тялото на функцията."""
    import inspect
    from src.ai_processor import AIProcessor

    source = inspect.getsource(AIProcessor.enrich_for_msproject)
    assert "SAFE_ENRICHMENT_FIELDS" in source
    assert "SCHEDULING_ENRICHMENT_FIELDS" in source
    # Старият вид: един кортеж с всички полета накуп.
    assert '"wbs", "dependency_type", "lag_days"' not in source


def test_validation_runs_after_enrichment_in_pipeline():
    """Карантината не отменя проверката след последната AI стъпка."""
    import inspect
    from src.ai_processor import AIProcessor

    source = inspect.getsource(AIProcessor.generate_schedule)
    assert source.index("enrich_for_msproject") < source.index("_validate_final_schedule")
