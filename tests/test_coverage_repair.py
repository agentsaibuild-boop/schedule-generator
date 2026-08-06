"""Unit tests: под-покритието се ДОПИТВА, вместо само да се обяви за провал.

Реален прогон 2026-08: моделът върна 6 задачи за 28 КСС реда.  Гейтът каза
(правилно) „непълен график" и изходът беше нула.  „Покрий всички редове" в
промпта не е проверимо; повторното питане САМО за непокритите редове е —
и се спира след таван, за да няма безкраен цикъл.

FAILURE означава: или графикът пак излиза непълен, без изобщо да е направен
втори опит, или ремонтът трови графика (дублиран покривач, сблъскващи се ID-та,
безкрайно питане).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai_processor import AIProcessor  # noqa: E402
from src.provenance import QuantityRow, SourceRef  # noqa: E402


def _boq(sheet: str, row: int, qty: float,
         desc: str = "Реконструкция водопровод DN110 PE") -> QuantityRow:
    return QuantityRow(desc, qty, "м", SourceRef("КСС.xlsx", sheet, row), {})


def _task(tid: str, ref: str, qty: float, dur: int = 5) -> dict:
    return {"id": tid, "name": f"Полагане DN110 PE {tid}",
            "start_day": 1, "end_day": dur, "duration": dur, "length_m": qty,
            "source_ref": ref, "unit": "м", "dependencies": []}


class _Recorder:
    """Фалшив `generate_schedule` със СЪСТОЯНИЕ — връща различно на всяко викане.

    `responses` е списък: i-тото извикване връща i-тия елемент (последният се
    повтаря).  Записва с какви редове е било питано всяко извикване.
    """

    def __init__(self, responses: list[list[dict]]):
        self.responses = responses
        self.calls: list[list[str]] = []
        self.scopes: list[str] = []

    def __call__(self, analysis, project_type, cb=None, *, all_text="",
                 boq_index=None, num_teams=1, extra_locations=None,
                 sequence_constraints=None, scope_note="",
                 skip_correction=False):
        self.calls.append([r.ref for r in (boq_index or [])])
        self.scopes.append(scope_note)
        idx = min(len(self.calls) - 1, len(self.responses) - 1)
        return {"status": "approved", "truncated": None, "total_cost": 0.01,
                "schedule": {"tasks": self.responses[idx]}}


def _proc(recorder: _Recorder) -> AIProcessor:
    proc = AIProcessor(router=None, knowledge_manager=None)
    proc.generate_schedule = recorder          # type: ignore[assignment]
    return proc


# ===================================================================
# Допокриване
# ===================================================================

def test_missing_row_is_asked_again_and_schedule_becomes_complete():
    boq = [_boq("A", 2, 100.0), _boq("A", 3, 300.0)]
    rec = _Recorder([
        [_task("T1", "КСС.xlsx!A!2", 100.0)],          # под-покритие
        [_task("T1", "КСС.xlsx!A!3", 300.0)],          # допокриване
    ])
    res = _proc(rec).generate_schedule_staged({}, "distribution", boq_index=boq)

    assert len(rec.calls) == 2, "непокритият ред трябва да се поиска пак"
    assert res["coverage"]["uncovered"] == []
    assert res["repair_rounds"] == 1
    assert res["status"] == "approved"
    assert res["exportable"] is True


def test_repair_asks_only_for_the_missing_rows():
    boq = [_boq("A", 2, 100.0), _boq("A", 3, 300.0)]
    rec = _Recorder([
        [_task("T1", "КСС.xlsx!A!2", 100.0)],
        [_task("T1", "КСС.xlsx!A!3", 300.0)],
    ])
    _proc(rec).generate_schedule_staged({}, "distribution", boq_index=boq)

    assert rec.calls[0] == ["КСС.xlsx!A!2", "КСС.xlsx!A!3"]
    assert rec.calls[1] == ["КСС.xlsx!A!3"], "не се пита пак за покрит ред"
    assert "допокриване" in rec.scopes[1]


def test_repair_tasks_do_not_collide_with_original_ids():
    boq = [_boq("A", 2, 100.0), _boq("A", 3, 300.0)]
    rec = _Recorder([
        [_task("T1", "КСС.xlsx!A!2", 100.0)],
        [_task("T1", "КСС.xlsx!A!3", 300.0)],          # СЪЩОТО ID от модела
    ])
    res = _proc(rec).generate_schedule_staged({}, "distribution", boq_index=boq)

    ids = [t["id"] for t in res["schedule"]["tasks"]]
    assert len(ids) == len(set(ids)), f"дублирани ID-та: {ids}"


def test_repair_stops_at_the_configured_cap(monkeypatch):
    """Модел, който упорито не покрива, не води до безкрайно питане."""
    monkeypatch.setenv("COVERAGE_REPAIR_ROUNDS", "2")
    boq = [_boq("A", 2, 100.0), _boq("A", 3, 300.0)]
    rec = _Recorder([[_task("T1", "КСС.xlsx!A!2", 100.0)]])   # винаги едно и също

    res = _proc(rec).generate_schedule_staged({}, "distribution", boq_index=boq)

    assert len(rec.calls) == 3, "1 опит + 2 допокривания"
    assert "КСС.xlsx!A!3" in res["coverage"]["uncovered"]
    assert res["exportable"] is False, "непълният график остава неекспортируем"


def test_repair_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("COVERAGE_REPAIR_ROUNDS", "0")
    boq = [_boq("A", 2, 100.0), _boq("A", 3, 300.0)]
    rec = _Recorder([[_task("T1", "КСС.xlsx!A!2", 100.0)]])

    res = _proc(rec).generate_schedule_staged({}, "distribution", boq_index=boq)

    assert len(rec.calls) == 1
    assert res["repair_rounds"] == 0
    assert res["exportable"] is False


def test_no_repair_when_first_attempt_is_complete():
    boq = [_boq("A", 2, 100.0)]
    rec = _Recorder([[_task("T1", "КСС.xlsx!A!2", 100.0)]])

    res = _proc(rec).generate_schedule_staged({}, "distribution", boq_index=boq)

    assert len(rec.calls) == 1
    assert res["repair_rounds"] == 0
    assert res["status"] == "approved"


def test_repair_task_for_already_covered_row_is_discarded():
    """Ремонтът не бива да прави ВТОРИ покривач на вече покрит ред."""
    boq = [_boq("A", 2, 100.0), _boq("A", 3, 300.0)]
    rec = _Recorder([
        [_task("T1", "КСС.xlsx!A!2", 100.0)],
        # допокриващата част връща и дубликат на вече покрития ред A!2
        [_task("T1", "КСС.xlsx!A!3", 300.0),
         _task("T2", "КСС.xlsx!A!2", 100.0)],
    ])
    res = _proc(rec).generate_schedule_staged({}, "distribution", boq_index=boq)

    assert res["coverage"]["over_covered"] == []
    assert res["coverage"]["uncovered"] == []
    assert res["status"] == "approved"
    assert len(res["schedule"]["tasks"]) == 2


def test_ambiguous_row_is_not_re_asked():
    """Неопределим клас-покривач иска ЧОВЕК, не още едно питане към модела."""
    boq = [_boq("A", 2, 100.0, desc="Позиция 2 по спецификация")]
    rec = _Recorder([[_task("T1", "КСС.xlsx!A!2", 100.0)]])

    res = _proc(rec).generate_schedule_staged({}, "distribution", boq_index=boq)

    assert len(rec.calls) == 1
    assert res["coverage"]["ambiguous"] == ["КСС.xlsx!A!2"]
    assert res["status"] == "needs_human_review"
