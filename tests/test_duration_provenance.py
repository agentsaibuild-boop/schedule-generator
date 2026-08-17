"""Unit tests: произход на всяка продължителност — изчислена или предположена.

Одит 2026-07-23 установи противоречие в заявения инвариант „аритметиката е в
код, не в промпт": когато липсва задължителен параметър, кодът ЗАПАЗВАШЕ
стойността на LLM-а в същото поле `duration`.  След това нищо надолу по
веригата не можеше да различи доказано число от предположение — нито
експортът, нито човекът, нито валидацията.

Реалният инвариант беше „кодът смята, когато има данни; иначе AI решава" —
тоест смяната на модел ВСЕ ПАК можеше да промени числата.

FAILURE означава: графикът отново съдържа неразличими стойности и не може да
се докаже кое е сметнато по норма от config/productivities.json.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.chat_handler import ChatHandler  # noqa: E402
from src.duration_calculator import (  # noqa: E402
    CODE_MILESTONE,
    CODE_MISSING_DN,
    CODE_MISSING_LENGTH,
    CODE_MISSING_MATERIAL,
    CODE_NOT_PARAMETRIC,
    CODE_OK,
    UNRESOLVED_CODES,
    calculate_task_duration,
)
from src.schedule_builder import ScheduleBuilder  # noqa: E402


def _pipe(**kw) -> dict:
    task = {
        "id": "В01", "name": "Полагане DN500 PE", "length_m": 720,
        "diameter": 500, "duration": 10, "start_day": 1, "end_day": 10,
        "dependencies": [],
    }
    task.update(kw)
    return task


# ===================================================================
# Кодовете на причината
# ===================================================================

def test_computed_task_gets_ok_code():
    assert calculate_task_duration(_pipe()).code == CODE_OK


def test_milestone_has_its_own_code():
    result = calculate_task_duration({"name": "ФИНАЛ", "milestone": True})
    assert result.code == CODE_MILESTONE
    assert result.code not in UNRESOLVED_CODES


def test_missing_material_code():
    task = _pipe(name="Полагане тръби DN300 — ул. Х", diameter=300)
    assert calculate_task_duration(task).code == CODE_MISSING_MATERIAL


def test_missing_length_code():
    task = _pipe()
    del task["length_m"]
    assert calculate_task_duration(task).code == CODE_MISSING_LENGTH


def test_missing_dn_code():
    task = _pipe(name="Полагане PE тръби")
    del task["diameter"]
    assert calculate_task_duration(task).code == CODE_MISSING_DN


def test_non_parametric_code():
    task = {"name": "Изкоп за траншея", "quantity": 720, "unit": "м3"}
    assert calculate_task_duration(task).code == CODE_NOT_PARAMETRIC


def test_all_failure_codes_are_marked_unresolved():
    for code in (CODE_MISSING_DN, CODE_MISSING_LENGTH, CODE_MISSING_MATERIAL,
                 CODE_NOT_PARAMETRIC):
        assert code in UNRESOLVED_CODES


def test_success_codes_are_not_unresolved():
    assert CODE_OK not in UNRESOLVED_CODES
    assert CODE_MILESTONE not in UNRESOLVED_CODES


# ===================================================================
# Произходът се записва в задачата
# ===================================================================

def test_calculated_task_is_marked_calculated():
    result = ScheduleBuilder().recompute_durations([_pipe()])
    task = result["schedule"][0]

    assert task["duration_source"] == "calculated"
    assert task["calculated_duration"] == 48
    assert task["duration"] == 48
    assert "suggested_duration" not in task


def test_unresolved_task_is_marked_suggested():
    """Ядрото на поправката: стойността на AI-я вече е ЯВНО предположение."""
    task = _pipe(name="Полагане тръби DN300 — ул. Х", diameter=300, duration=42)
    result = ScheduleBuilder().recompute_durations([task])
    out = result["schedule"][0]

    assert out["duration_source"] == "suggested"
    assert out["duration_status"] == CODE_MISSING_MATERIAL
    assert out["suggested_duration"] == 42
    assert "calculated_duration" not in out
    # `duration` остава използваемо надолу по веригата
    assert out["duration"] == 42


def test_unchanged_task_still_gets_provenance():
    """Съвпадаща стойност не значи недоказана — маркира се като изчислена."""
    result = ScheduleBuilder().recompute_durations([_pipe(duration=48, end_day=48)])
    task = result["schedule"][0]

    assert result["summary"]["unchanged"] == 1
    assert task["duration_source"] == "calculated"
    assert task["calculated_duration"] == 48


def test_stale_suggested_field_is_cleared_when_resolved():
    """Ако задачата се допълни и стане изчислима, старото предположение пада."""
    task = _pipe(suggested_duration=99, duration_source="suggested")
    result = ScheduleBuilder().recompute_durations([task])
    out = result["schedule"][0]

    assert out["duration_source"] == "calculated"
    assert "suggested_duration" not in out


def test_stale_calculated_field_is_cleared_when_unresolved():
    task = _pipe(name="Полагане тръби DN300", diameter=300,
                 calculated_duration=99, duration_source="calculated")
    result = ScheduleBuilder().recompute_durations([task])
    out = result["schedule"][0]

    assert out["duration_source"] == "suggested"
    assert "calculated_duration" not in out


def test_milestone_is_not_counted_as_unresolved():
    tasks = [{"id": "M01", "name": "ФИНАЛ", "milestone": True, "duration": 0,
              "start_day": 1, "end_day": 1, "dependencies": []}]
    result = ScheduleBuilder().recompute_durations(tasks)
    assert result["summary"]["unresolved"] == 0


# ===================================================================
# Отчетът различава видовете
# ===================================================================

def _mixed_schedule() -> list[dict]:
    return [
        _pipe(id="В01"),                                              # изчислима
        _pipe(id="В02", name="Полагане тръби DN300", diameter=300),   # без материал
        {"id": "И01", "name": "Изкоп", "duration": 9, "start_day": 1,
         "end_day": 9, "dependencies": []},                           # без норма
    ]


def test_summary_counts_unresolved_separately_from_skipped():
    result = ScheduleBuilder().recompute_durations(_mixed_schedule())
    summary = result["summary"]

    assert summary["skipped"] == 2
    assert summary["unresolved"] == 2
    assert summary["by_code"][CODE_MISSING_MATERIAL] == 1
    assert summary["by_code"][CODE_NOT_PARAMETRIC] == 1


def test_skipped_entries_carry_code_and_suggested_value():
    result = ScheduleBuilder().recompute_durations(_mixed_schedule())
    entry = next(s for s in result["skipped"] if s["id"] == "В02")

    assert entry["code"] == CODE_MISSING_MATERIAL
    assert entry["suggested_duration"] == 10


def test_fully_resolved_schedule_reports_zero_unresolved():
    result = ScheduleBuilder().recompute_durations([_pipe()])
    assert result["summary"]["unresolved"] == 0


# ===================================================================
# Видимост в чата
# ===================================================================

def _report(by_code: dict, unresolved: int) -> dict:
    return {
        "duration_report": {
            "applied": True,
            "changes": [{"id": "В01", "name": "x", "old": 10, "new": 48,
                         "delta": 38, "reason": "r"}],
            "skipped": [], "warnings": [],
            "summary": {"total": 3, "recomputed": 1, "unchanged": 0,
                        "skipped": unresolved, "unresolved": unresolved,
                        "by_code": by_code,
                        "old_total_duration": 10, "new_total_duration": 48},
        }
    }


def test_unresolved_durations_are_reported():
    lines = ChatHandler._format_duration_report(
        _report({CODE_MISSING_MATERIAL: 2}, 2)
    )
    body = "\n".join(lines)
    assert "НЕДОКАЗАНА" in body
    assert "материалът не е указан" in body


def test_report_uses_human_labels_not_codes():
    lines = ChatHandler._format_duration_report(
        _report({CODE_MISSING_DN: 1, CODE_MISSING_LENGTH: 1}, 2)
    )
    body = "\n".join(lines)
    assert "липсва диаметър" in body
    assert "липсва дължина" in body
    assert "MISSING_DN" not in body


def test_non_parametric_is_explained_as_expected():
    """Изкоп/настилки нямат норми — не бива да изглеждат като дефект."""
    lines = ChatHandler._format_duration_report(
        _report({CODE_NOT_PARAMETRIC: 5}, 5)
    )
    body = "\n".join(lines)
    assert "очаквано" in body


def test_no_unresolved_section_when_everything_calculated():
    lines = ChatHandler._format_duration_report(_report({}, 0))
    assert not any("НЕДОКАЗАНА" in ln for ln in lines)


def test_report_tells_user_to_check_against_boq():
    lines = ChatHandler._format_duration_report(
        _report({CODE_MISSING_MATERIAL: 1}, 1)
    )
    assert any("КСС" in ln for ln in lines)


# ===================================================================
# Регресия за самия одитен извод
# ===================================================================

def test_calculated_and_suggested_are_never_the_same_field():
    """Двете стойности не бива пак да се слеят в едно поле."""
    mixed = ScheduleBuilder().recompute_durations(_mixed_schedule())["schedule"]
    calculated = [t for t in mixed if t.get("duration_source") == "calculated"]
    suggested = [t for t in mixed if t.get("duration_source") == "suggested"]

    assert calculated and suggested
    assert all("calculated_duration" in t for t in calculated)
    assert all("suggested_duration" in t or t.get("duration") is None
               for t in suggested)


def test_every_task_declares_its_source():
    """Задача без произход е точно случаят, който одитът намери."""
    mixed = ScheduleBuilder().recompute_durations(_mixed_schedule())["schedule"]
    assert all("duration_source" in t for t in mixed)


# ===================================================================
# ПРОБА 10.08.2026 — какво всъщност оставаше недоказано и защо
#
# Прогоните съобщаваха 130–220 задачи „с НЕДОКАЗАНА продължителност
# (стойност от AI)" на прогон.  Разбито, числото се оказа съставено от четири
# различни неща, нито едно от които „стойност от AI":
#
#   1. полагането на ВОДОПРОВОД падаше, защото „PEHD" не се разпознаваше
#      като полиетилен — гръбнакът на участъка без нито една норма;
#   2. изкопи и изпитвания минаваха за тръбна работа заради думите
#      „канализац"/„водопровод" в името и се отчитаха като „липсва дължина";
#   3. стойностите от технологичната верига (еталонния ЧОВЕШКИ график) се
#      преетикетираха на „от AI";
#   4. обобщаващите редове, които нямат собствена продължителност, влизаха в
#      същото число — по един на пакет.
#
# FAILURE означава: отчетът за продължителностите пак описва нещо, което не
# се е случило, и числото в него не може да се изпрати на одитор.
# ===================================================================

@pytest.mark.parametrize("material", ["PEHD", "HDPE", "ПЕВП", "PE-HD", "PE100", "PE"])
def test_high_density_polyethylene_names_resolve_to_pe(material):
    """Стандартните имена на PE в българските КСС — всички са един материал."""
    from src.duration_calculator import detect_material
    task = {"name": f"Доставка и полагане на тръби {material} DN110",
            "material": material}
    assert detect_material(task) == "PE"


def test_water_laying_row_is_computable_with_pehd():
    """Гръбнакът на водопроводния участък не бива да пада на името."""
    result = calculate_task_duration({
        "id": "В10", "name": "Полагане — Доставка и полагане на тръби PEHD DN110",
        "material": "PEHD", "dn": 110, "length_m": 210,
        "activity_class_hint": "laying", "unit": "м", "duration": 5,
    })
    assert result.code == CODE_OK
    assert result.days


def test_row_class_beats_the_words_in_the_name():
    """Изкоп с „канализац" в името не е полагане на тръба."""
    result = calculate_task_duration({
        "id": "И10", "name": "Изкоп — Изкоп с багер за канализационен изкоп",
        "activity_class_hint": "excavation", "unit": "м3", "quantity": 430,
        "duration": 3,
    })
    assert result.code == CODE_NOT_PARAMETRIC
    assert result.code != CODE_MISSING_LENGTH


def test_chain_step_without_a_row_is_not_pipe_work():
    """Стъпка без количество няма ред, по който да се смята — липсва НОРМА,
    не дължина."""
    result = calculate_task_duration({
        "id": "Т10", "chain_step": "leak_test", "duration": 1,
        "name": "Изпитване за непропускливост на канализационния участък",
    })
    assert result.code == CODE_NOT_PARAMETRIC


def test_a_name_without_a_class_still_falls_back_to_the_regex():
    """Плоският път (без пакети) не носи клас — там името остава единственото,
    с което разполагаме."""
    result = calculate_task_duration({
        "id": "В11", "name": "Полагане на тръбопровод DN300", "duration": 4,
    })
    assert result.code == CODE_MISSING_LENGTH


def test_reference_schedule_value_is_not_reported_as_ai():
    """`chain_template` идва от еталонния човешки график, не от модела."""
    from_template = {
        "id": "К10", "name": "Видеоинспекция със CCTV камера",
        "duration": 1, "duration_source": "chain_template",
        "start_day": 1, "end_day": 1, "dependencies": [],
    }
    out = ScheduleBuilder().recompute_durations([from_template])

    task = out["schedule"][0]
    assert task["duration_source"] == "chain_template"
    assert out["skipped"][0]["duration_source"] == "chain_template"


def test_a_model_value_is_still_marked_as_suggested():
    """Разграничението работи и в другата посока."""
    out = ScheduleBuilder().recompute_durations([
        {"id": "И11", "name": "Изкоп", "duration": 9, "start_day": 1,
         "end_day": 9, "dependencies": []}])
    assert out["schedule"][0]["duration_source"] == "suggested"


def test_summary_rows_are_not_counted_as_unproven():
    """Обобщаващият ред няма СОБСТВЕНА продължителност — тя е сбор на децата."""
    out = ScheduleBuilder().recompute_durations([
        {"id": "WBS", "name": "СТРОИТЕЛСТВО", "type": "summary", "duration": 0,
         "dependencies": []},
        {"id": "П1", "name": "кл. 48 от РШ 36 до Пр. Ш 1", "type": "summary",
         "duration": 0, "parent_id": "WBS", "dependencies": []},
        _pipe(id="В20", parent_id="П1"),
    ])

    assert out["summary"]["unresolved"] == 0
    assert not [s for s in out["skipped"] if s["id"] in {"WBS", "П1"}]


# ===================================================================
# ПРОБА 10.08.2026 — клас `laying` с количество, което не е дължина
#
# „Бетонов кожух за тръба DN 500 — 1,04m3*71,64m" се класифицира като `laying`
# заради „тръба" в описанието, но се мери в `m3/m'` — обем на метър.  Сметнат
# по тарифа за полагане, той би дал продължителност по ОБЕМНО число.
#
# Кодът с право отказваше, но с ГРЕШНАТА причина: отчиташе „липсва дължина",
# докато дължината стои в самото описание (71,64 m).  Липсваше му норма за
# бетониране — „друг разговор, който води до друго действие".
#
# ТОЗИ РАЗГОВОР СЕ СЪСТОЯ на 17.08.2026: нормата е изведена от еталона
# (16.5 м³/ден, виж `volume_productivities` в конфига) и редът вече се смята по
# ОБЕМА си, а не получава медианата от шаблона.  Затова тестът вече пази
# новото поведение — но и старото си условие: причината никога да не е
# „липсва дължина".
#
# FAILURE означава: или отчетът пак праща човека да търси данна, която я има,
# или бетонирането пак получава време от шаблона вместо от кубатурата си.
# ===================================================================

def test_a_volume_per_metre_row_is_priced_by_its_volume():
    result = calculate_task_duration({
        "id": "К20", "activity_class_hint": "laying", "unit": "m3/m'",
        "quantity": 74.5056, "dn": 500,
        "name": "Полагане — Бетонов кожух за тръба DN 500  - 1,04m3*71,64m",
    }, min_days=1)

    assert result.code != CODE_MISSING_LENGTH, \
        "отчетът пак праща човека да търси дължина, която стои в описанието"
    assert result.code != CODE_NOT_PARAMETRIC, \
        "кожухът пак е без норма — времето му ще дойде от шаблона"
    assert result.rate_key == "concrete_encasement"
    assert result.days == 5           # 74.5056 ÷ 16.5 = 4.52 → 5


def test_linear_metre_notation_still_counts_as_length():
    """`m'` е линеен метър в българските КСС — той Е дължина."""
    result = calculate_task_duration({
        "id": "В21", "activity_class_hint": "laying", "unit": "m'",
        "quantity": 210.0, "dn": 110, "material": "PEHD",
        "name": "Полагане — Доставка и полагане на тръби PEHD DN110",
    })

    assert result.code == CODE_OK
    assert result.days


def test_a_laying_row_without_a_unit_is_not_disqualified():
    """Липсваща единица не е доказателство, че редът не е тръбен."""
    result = calculate_task_duration({
        "id": "В22", "activity_class_hint": "laying", "length_m": 210,
        "dn": 110, "material": "PE",
        "name": "Полагане — тръби PE DN110",
    })

    assert result.code == CODE_OK
