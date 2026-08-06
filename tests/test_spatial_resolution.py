"""Unit tests: пространствените сблъсъци се РАЗРЕШАВАТ, не само се обявяват.

Реален прогон 2026-08: генерацията слагаше два екипа на едни и същи метри в
застъпващи се дни.  Гейтът ги хващаше правилно → графикът е `invalid` → няма
изход.  Молбата към модела „не се застъпвай" не е проверима; преместването във
времето е.  `ScheduleBuilder.resolve_spatial_conflicts` сериализира двойките
детерминистично (FS връзка) и ДОКЛАДВА всяка добавена връзка.

FAILURE означава: или графикът остава невалиден заради застъпване, което може
да се разреши автоматично, или ремонтът мълчаливо разбърква AI логиката —
свързва родител с подзадача, затваря цикъл, или пипа нарушения на буфера,
които са предупреждение, а не сблъсък.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.schedule_builder import ScheduleBuilder  # noqa: E402
from src.spatial import find_crew_collisions  # noqa: E402


def _seg(tid: str, frm: float, to: float, start: int, dur: int, **kw) -> dict:
    task = {
        "id": tid, "name": kw.pop("name", f"Полагане {tid}"),
        "alignment_id": kw.pop("alignment_id", "ул. Първа"),
        "start_chainage": frm, "end_chainage": to,
        "start_day": start, "end_day": start + dur - 1, "duration": dur,
        "dependencies": kw.pop("dependencies", []),
    }
    task.update(kw)
    return task


def _overlaps(schedule: list[dict]) -> list[dict]:
    return [c for c in find_crew_collisions(schedule) if c["kind"] == "overlap"]


# ===================================================================
# Разрешаване
# ===================================================================

def test_overlapping_crews_are_serialized():
    """Двата екипа на едни и същи метри в едни и същи дни се разделят."""
    schedule = [_seg("T1", 0, 300, 1, 10), _seg("T2", 100, 400, 3, 8)]
    assert _overlaps(schedule), "предпоставката на теста: има застъпване"

    result = ScheduleBuilder().resolve_spatial_conflicts(schedule)

    assert _overlaps(result["schedule"]) == []
    assert len(result["added_links"]) == 1
    assert result["added_links"][0]["predecessor"] == "T1"
    assert result["added_links"][0]["successor"] == "T2"
    fixed = {t["id"]: t for t in result["schedule"]}
    assert fixed["T2"]["start_day"] == fixed["T1"]["end_day"] + 1


def test_equal_start_shorter_task_leads():
    """При еднакво начало води по-късата задача — по-малко общо изчакване."""
    schedule = [_seg("T1", 0, 300, 1, 10), _seg("T2", 100, 400, 1, 8)]
    result = ScheduleBuilder().resolve_spatial_conflicts(schedule)
    assert result["added_links"][0]["predecessor"] == "T2"
    assert result["added_links"][0]["successor"] == "T1"
    fixed = {t["id"]: t for t in result["schedule"]}
    assert fixed["T1"]["start_day"] == fixed["T2"]["end_day"] + 1


def test_input_is_not_mutated():
    schedule = [_seg("T1", 0, 300, 1, 10), _seg("T2", 100, 400, 1, 8)]
    ScheduleBuilder().resolve_spatial_conflicts(schedule)
    assert schedule[1]["start_day"] == 1
    assert schedule[1]["dependencies"] == []


def test_earlier_task_leads_regardless_of_order():
    """Редът в списъка не решава — по-ранното начало води."""
    schedule = [_seg("T2", 100, 400, 5, 8), _seg("T1", 0, 300, 1, 10)]
    result = ScheduleBuilder().resolve_spatial_conflicts(schedule)
    assert result["added_links"][0]["predecessor"] == "T1"
    assert result["added_links"][0]["successor"] == "T2"


def test_chain_of_three_is_fully_resolved():
    schedule = [_seg("T1", 0, 300, 1, 6), _seg("T2", 100, 400, 1, 6),
                _seg("T3", 200, 500, 1, 6)]
    result = ScheduleBuilder().resolve_spatial_conflicts(schedule)
    assert _overlaps(result["schedule"]) == []
    assert result["unresolved"] == []


def test_different_alignments_are_left_alone():
    """Паралелни улици не се сериализират — няма какво да се разрешава."""
    schedule = [_seg("T1", 0, 300, 1, 10),
                _seg("T2", 0, 300, 1, 10, alignment_id="ул. Втора")]
    result = ScheduleBuilder().resolve_spatial_conflicts(schedule)
    assert result["added_links"] == []
    assert {t["start_day"] for t in result["schedule"]} == {1}


def test_buffer_violation_is_not_serialized():
    """Допиране/недостатъчно изоставане е warning, не сблъсък — не се пипа."""
    schedule = [_seg("T1", 0, 300, 1, 10), _seg("T2", 310, 500, 1, 10)]
    assert _overlaps(schedule) == []
    result = ScheduleBuilder().resolve_spatial_conflicts(schedule)
    assert result["added_links"] == []


def test_tasks_without_chainage_are_untouched():
    schedule = [{"id": "T1", "name": "Мобилизация", "start_day": 1,
                 "end_day": 3, "duration": 3, "dependencies": []},
                {"id": "T2", "name": "Доставка", "start_day": 1,
                 "end_day": 3, "duration": 3, "dependencies": []}]
    result = ScheduleBuilder().resolve_spatial_conflicts(schedule)
    assert result["added_links"] == []
    assert result["schedule"] == schedule


# ===================================================================
# Какво ремонтът НЕ прави
# ===================================================================

def test_parent_and_child_are_not_serialized():
    """Обобщаваща задача и подзадачата ѝ естествено делят метри и дни."""
    parent = _seg("P1", 0, 400, 1, 20, name="Водопровод — етап 1")
    child = _seg("T1", 0, 300, 1, 10, parent_id="P1")
    result = ScheduleBuilder().resolve_spatial_conflicts([parent, child])
    assert result["added_links"] == []
    assert result["unresolved"], "конфликтът остава видим за гейта"


def test_cycle_is_refused_and_reported():
    """Ако сериализацията би затворила цикъл — не се прави."""
    t1 = _seg("T1", 0, 300, 1, 10, dependencies=["T2"])
    t2 = _seg("T2", 100, 400, 1, 8)
    result = ScheduleBuilder().resolve_spatial_conflicts([t1, t2])
    assert result["added_links"] == []
    assert result["unresolved"]
    fixed = {t["id"]: t for t in result["schedule"]}
    assert fixed["T2"]["dependencies"] == []


def test_existing_link_between_the_pair_is_kept():
    """Умишлено SS припокриване не се пренаписва — гейтът го докладва."""
    t1 = _seg("T1", 0, 300, 1, 10)
    t2 = _seg("T2", 100, 400, 1, 8,
              dependencies=[{"predecessor_id": "T1", "type": "SS", "lag_days": 0}])
    result = ScheduleBuilder().resolve_spatial_conflicts([t1, t2])
    assert result["added_links"] == []
    assert len(result["schedule"][1]["dependencies"]) == 1


def test_added_link_is_fs_dict_not_bare_string():
    """Низова зависимост би наследила `dependency_type` на задачата (напр. SS)."""
    t1 = _seg("T1", 0, 300, 1, 10, dependency_type="SS")
    t2 = _seg("T2", 100, 400, 3, 8, dependency_type="SS")
    result = ScheduleBuilder().resolve_spatial_conflicts([t1, t2])
    fixed = {t["id"]: t for t in result["schedule"]}
    dep = fixed["T2"]["dependencies"][0]
    assert isinstance(dep, dict)
    assert dep["type"] == "FS"
    assert dep["predecessor_id"] == "T1"


def test_result_is_reproducible():
    schedule = [_seg("T1", 0, 300, 1, 6), _seg("T2", 100, 400, 1, 6),
                _seg("T3", 200, 500, 1, 6)]
    a = ScheduleBuilder().resolve_spatial_conflicts(schedule)
    b = ScheduleBuilder().resolve_spatial_conflicts(schedule)
    assert a["added_links"] == b["added_links"]
    assert a["schedule"] == b["schedule"]


def test_gate_accepts_the_repaired_schedule():
    """Смисълът на ремонта: графикът минава пространствената проверка."""
    schedule = [_seg("T1", 0, 300, 1, 10), _seg("T2", 100, 400, 1, 8)]
    builder = ScheduleBuilder()
    before = builder.validate_schedule(schedule)
    assert any("Пространствен конфликт" in e for e in before["errors"])

    after = builder.validate_schedule(
        builder.resolve_spatial_conflicts(schedule)["schedule"])
    assert not any("Пространствен конфликт" in e for e in after["errors"])
