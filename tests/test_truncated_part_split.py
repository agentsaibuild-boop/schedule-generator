"""Unit tests: отрязана част се РАЗДЕЛЯ, вместо да изчезне цял лист.

ЖИВ ПРОГОН 2026-08-06: листът „Канализация" (17 позиции) се отряза и в трите
опита — 0 задачи, цяла мрежа липсва в графика.  Причината е таванът за партида
(50 реда при Claude работник), сметнат по размера на КОНТЕКСТА, докато лимитът
е ИЗХОДЪТ: един КСС ред ражда 7-10 дейности по 2 фронта.

Вместо ново магическо число, при отрязване частта се разполовява и всяка
половина се пуска пак — размерът се напасва към реалния лимит на модела.

FAILURE означава: голям лист от КСС пак изчезва тихо (или графикът се обявява
за провален), макар че същата работа минава на две по-малки извиквания.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai_processor import AIProcessor  # noqa: E402
from src.provenance import QuantityRow, SourceRef  # noqa: E402


def _boq(row: int, qty: float) -> QuantityRow:
    return QuantityRow("Реконструкция водопровод DN110 PE", qty, "м",
                       SourceRef("КСС.xlsx", "A", row), {})


def _task(tid: str, ref: str, qty: float) -> dict:
    return {"id": tid, "name": f"Полагане DN110 PE {tid}", "start_day": 1,
            "end_day": 5, "duration": 5, "length_m": qty, "unit": "м",
            "source_ref": ref, "dependencies": []}


class _Splitter:
    """Модел, който се отрязва над `limit` реда и работи под него."""

    def __init__(self, limit: int):
        self.limit = limit
        self.calls: list[int] = []

    def __call__(self, analysis, project_type, cb=None, *, all_text="",
                 boq_index=None, num_teams=1, extra_locations=None,
                 sequence_constraints=None, scope_note="", skip_correction=False):
        rows = boq_index or []
        self.calls.append(len(rows))
        if len(rows) > self.limit:
            return {"status": "error", "truncated": True, "total_cost": 0.01,
                    "schedule": {"tasks": []}}
        return {"status": "approved", "truncated": None, "total_cost": 0.01,
                "schedule": {"tasks": [_task(f"T{i}", r.ref, r.quantity)
                                       for i, r in enumerate(rows)]}}


def _proc(fake) -> AIProcessor:
    proc = AIProcessor(router=None, knowledge_manager=None)
    proc.generate_schedule = fake        # type: ignore[assignment]
    return proc


def test_truncated_part_is_split_until_it_fits():
    boq = [_boq(i, 100.0 + i) for i in range(2, 10)]      # 8 позиции
    fake = _Splitter(limit=2)                            # моделът поема 2 наведнъж
    res = _proc(fake).generate_schedule_staged({}, "distribution", boq_index=boq)

    assert res["coverage"]["uncovered"] == []
    assert res["status"] == "approved"
    assert res["exportable"] is True
    assert min(fake.calls) <= 2, fake.calls   # стигнало е до размер, който минава


def test_split_parts_do_not_make_the_schedule_invalid():
    """Разделената част не е „провалена" — работата ѝ е поета от половините."""
    boq = [_boq(i, 100.0 + i) for i in range(2, 6)]
    res = _proc(_Splitter(limit=2)).generate_schedule_staged(
        {}, "distribution", boq_index=boq)
    assert res["failed_parts"] == []
    assert [p for p in res["parts"] if p.get("split")], "разделянето се отчита"


def test_all_tasks_survive_the_split():
    boq = [_boq(i, 100.0 + i) for i in range(2, 6)]
    res = _proc(_Splitter(limit=2)).generate_schedule_staged(
        {}, "distribution", boq_index=boq)
    tasks = res["schedule"]["tasks"]
    assert len(tasks) == 4
    ids = [t["id"] for t in tasks]
    assert len(ids) == len(set(ids)), f"дублирани ID след разделяне: {ids}"


def test_single_row_that_truncates_is_reported_not_looped():
    """Един ред не може да се дели — частта се обявява за провалена."""
    boq = [_boq(2, 100.0)]
    fake = _Splitter(limit=0)                            # отрязва се винаги
    res = _proc(fake).generate_schedule_staged({}, "distribution", boq_index=boq)

    # Един ред не се дели; допокриването пак опитва (2 кръга), но без разделяне.
    assert all(n == 1 for n in fake.calls)
    assert len(fake.calls) <= 3
    assert res["status"] == "invalid"
    assert res["exportable"] is False
    assert res["failed_parts"]


def test_split_depth_is_bounded():
    """Модел, който се отрязва винаги, не върти безкрайно разделяне."""
    boq = [_boq(i, 100.0 + i) for i in range(2, 34)]      # 32 позиции
    fake = _Splitter(limit=0)
    res = _proc(fake).generate_schedule_staged({}, "distribution", boq_index=boq)

    splits = [p for p in res["parts"] if p.get("split")]
    assert len(splits) <= 12, f"разделянията надхвърлят тавана: {len(splits)}"
    # 32 реда → 7 партиди + най-много 12 разделяния + допокривания.
    assert len(fake.calls) < 80, f"твърде много извиквания: {len(fake.calls)}"
    assert res["status"] == "invalid"
    assert res["exportable"] is False
