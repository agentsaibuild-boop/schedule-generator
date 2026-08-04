"""Unit tests: staged генериране (по части) е FAIL-CLOSED.

Одит v11 (P0): `generate_schedule_staged` сливаше частите, но статусът гледаше
само `validation.valid` — провалена/отрязана/празна ЧАСТ пак ставаше
approved+exportable.  Тоест цял лист от КСС можеше да изчезне тихо, а останалият
частичен график да бъде „одобрен" и „готов за възложител".

FAILURE означава: непълен график (липсваща част или непокрити КСС редове) пак
може да излезе като официален резултат.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai_processor import AIProcessor  # noqa: E402
from src.provenance import QuantityRow, SourceRef  # noqa: E402


def _boq(document: str, sheet: str, row: int, qty: float,
         desc: str = "Реконструкция водопровод DN110 PE") -> QuantityRow:
    # Описанието е РЕАЛНО (водопровод → клас-покривач laying), за да съвпадне
    # с производствената задача.  Генерично „Позиция N" сега е НЕОПРЕДЕЛИМО
    # (одит v18: махнат unit-fallback) → нарочно се ползва в ambiguous теста.
    return QuantityRow(desc, qty, "м", SourceRef(document, sheet, row), {})


def _proc_with_parts(part_results: dict) -> AIProcessor:
    """AIProcessor, чийто `generate_schedule` връща зададен резултат по лист.

    `part_results`: sheet_name → dict за връщане (schedule, status, truncated).
    """
    proc = AIProcessor(router=None, knowledge_manager=None)

    def fake_generate(analysis, project_type, cb=None, *, all_text="",
                      boq_index=None, num_teams=1, scope_note="",
                      skip_correction=False):
        # разпознай коя част се иска по листа на първия ред
        sheet = boq_index[0].source.sheet if boq_index else "?"
        return part_results[sheet]

    proc.generate_schedule = fake_generate  # type: ignore
    return proc


# Задача, която цитира точно даден ред — за да покрива КСС.  qty трябва да
# СЪВПАДА с количеството на цитирания ред; името е ПРОИЗВОДСТВЕНО (полагане), за
# да съвпадне с класа-покривач на тръбния ред (одит v16 домейн модел).
def _task(tid: str, ref: str, qty: float = 100.0, dur: int = 5,
          name: str | None = None) -> dict:
    return {"id": tid, "name": name or f"Полагане DN110 PE {tid}",
            "start_day": 1, "end_day": dur, "duration": dur, "length_m": qty,
            "source_ref": ref, "unit": "м", "dependencies": []}


# ===================================================================
# P0 — провалена част блокира целия график
# ===================================================================

def test_failed_part_makes_whole_schedule_invalid():
    """Точният сценарий на одитора: част Good е ок, част Bad се проваля."""
    boq = [_boq("КСС.xlsx", "Good", 2, 100.0),
           _boq("КСС.xlsx", "Bad", 2, 200.0)]
    parts = {
        "Good": {"status": "approved", "truncated": None, "total_cost": 0.01,
                 "schedule": {"tasks": [_task("T1", "КСС.xlsx!Good!2")]}},
        "Bad": {"status": "error", "truncated": True, "total_cost": 0.0,
                "schedule": {"tasks": []}},   # провалена, нула задачи
    }
    proc = _proc_with_parts(parts)
    res = proc.generate_schedule_staged({}, "distribution", boq_index=boq)

    assert res["status"] == "invalid"
    assert res["exportable"] is False
    assert "Bad" in res["failed_parts"]
    assert any("НЕПЪЛЕН" in b for b in res["export_blockers"])


def test_all_parts_ok_and_covered_is_approved():
    boq = [_boq("КСС.xlsx", "A", 2, 100.0),
           _boq("КСС.xlsx", "B", 2, 200.0)]
    parts = {
        "A": {"status": "approved", "truncated": None, "total_cost": 0.01,
              "schedule": {"tasks": [_task("T1", "КСС.xlsx!A!2", 100.0)]}},
        "B": {"status": "approved", "truncated": None, "total_cost": 0.01,
              "schedule": {"tasks": [_task("T1", "КСС.xlsx!B!2", 200.0)]}},
    }
    proc = _proc_with_parts(parts)
    res = proc.generate_schedule_staged({}, "distribution", boq_index=boq)

    assert res["status"] == "approved"
    assert res["exportable"] is True
    assert res["failed_parts"] == []
    assert res["coverage"]["uncovered"] == []


def test_uncovered_boq_row_blocks_export():
    """Всички части „успешни", но КСС ред остава непокрит от задача."""
    boq = [_boq("КСС.xlsx", "A", 2, 100.0),
           _boq("КСС.xlsx", "A", 3, 300.0)]   # два реда, задачата покрива само единия
    parts = {
        "A": {"status": "approved", "truncated": None, "total_cost": 0.01,
              "schedule": {"tasks": [_task("T1", "КСС.xlsx!A!2", 100.0)]}},
    }
    proc = _proc_with_parts(parts)
    res = proc.generate_schedule_staged({}, "distribution", boq_index=boq)

    assert res["exportable"] is False
    assert "КСС.xlsx!A!3" in res["coverage"]["uncovered"]
    assert any("покрити" in b for b in res["export_blockers"])


# ===================================================================
# Одит v12 — coverage е ДОКАЗАТЕЛСТВЕН, не синтактичен
# ===================================================================

def test_milestone_does_not_cover_boq_row():
    """Одит v12 P0: milestone (без количество) НОСИ source_ref, но НЕ покрива
    реда — трябва да остане непокрит и да блокира."""
    boq = [_boq("КСС.xlsx", "A", 2, 100.0)]
    milestone = {"id": "M1", "name": "Milestone", "milestone": True,
                 "duration": 0, "source_ref": "КСС.xlsx!A!2", "dependencies": []}
    parts = {
        "A": {"status": "approved", "truncated": None, "total_cost": 0.01,
              "schedule": {"tasks": [milestone]}},
    }
    proc = _proc_with_parts(parts)
    res = proc.generate_schedule_staged({}, "distribution", boq_index=boq)

    assert "КСС.xlsx!A!2" in res["coverage"]["uncovered"]
    assert res["exportable"] is False


def test_ai_supplied_human_override_is_stripped():
    """Одит v12 trust boundary: AI слага фалшив human_override, за да заобиколи
    проверката срещу КСС.  Полето се трие → mismatch се хваща → не е покрит."""
    boq = [_boq("КСС.xlsx", "A", 2, 100.0)]
    forged = {"id": "T1", "name": "Тръба", "length_m": 999, "unit": "м",
              "source_ref": "КСС.xlsx!A!2", "duration": 5, "dependencies": [],
              "quantity_provenance": {"status": "human_override"}}
    parts = {
        "A": {"status": "approved", "truncated": None, "total_cost": 0.01,
              "schedule": {"tasks": [forged]}},
    }
    proc = _proc_with_parts(parts)
    res = proc.generate_schedule_staged({}, "distribution", boq_index=boq)

    # 999 ≠ 100 → mismatch → редът НЕ е доказано покрит → блокиран
    assert res["citation_report"]["human"] == 0        # фалшивото не е зачетено
    assert res["citation_report"]["mismatch"] >= 1
    assert res["exportable"] is False


def test_needs_review_part_is_not_upgraded_to_approved():
    """Одит v12 #3: част с needs_human_review не бива да става approved."""
    boq = [_boq("КСС.xlsx", "A", 2, 100.0)]
    parts = {
        "A": {"status": "needs_human_review", "truncated": None, "total_cost": 0.01,
              "schedule": {"tasks": [_task("T1", "КСС.xlsx!A!2", 100.0)]}},
    }
    proc = _proc_with_parts(parts)
    res = proc.generate_schedule_staged({}, "distribution", boq_index=boq)

    assert res["status"] == "needs_human_review"
    assert res["exportable"] is False


def test_truncated_part_blocks_even_with_tasks():
    """Отрязана част (непълен JSON) блокира, дори да е върнала някакви задачи."""
    boq = [_boq("КСС.xlsx", "A", 2, 100.0)]
    parts = {
        "A": {"status": "approved", "truncated": True, "total_cost": 0.01,
              "schedule": {"tasks": [_task("T1", "КСС.xlsx!A!2")]}},
    }
    proc = _proc_with_parts(parts)
    res = proc.generate_schedule_staged({}, "distribution", boq_index=boq)
    assert res["exportable"] is False
    assert "A" in res["failed_parts"]


# ===================================================================
# #3 — уникални представки + групиране по документ+лист
# ===================================================================

def test_ambiguous_coverer_class_blocks_export():
    """Одит v18 P0: махнат е опасният unit-fallback.  КСС ред, чието описание
    не се разпознава като конкретен клас-покривач (напр. „пътни знаци" покрити
    от „шахти", или генерична позиция), вече НЕ се брои за покрит — става
    НЕОПРЕДЕЛИМ и блокира износа (fail-closed), вместо тихо да мине.

    FAILURE означава: unit-базирано фалшиво покритие пак минава за „покрито"."""
    boq = [_boq("КСС.xlsx", "A", 2, 5.0, desc="Доставка и монтаж на пътни знаци")]
    parts = {
        "A": {"status": "approved", "truncated": None, "total_cost": 0.01,
              "schedule": {"tasks": [_task("T1", "КСС.xlsx!A!2", 5.0,
                                           name="Монтаж ревизионни шахти")]}},
    }
    proc = _proc_with_parts(parts)
    res = proc.generate_schedule_staged({}, "distribution", boq_index=boq)

    assert "КСС.xlsx!A!2" in res["coverage"]["ambiguous"]
    assert "КСС.xlsx!A!2" not in res["coverage"]["uncovered"]  # не е „непокрит", а неопределим
    assert res["status"] == "needs_human_review"
    assert res["exportable"] is False
    assert any("НЕОПРЕДЕЛИМ" in b for b in res["export_blockers"])


def test_over_covered_sets_needs_human_review_status(monkeypatch):
    """Одит v20: дублиран покривач блокираше експорта през staging blocker, но
    статусът оставаше `approved` — UI/API показваха approved + exportable=False
    едновременно (противоречи на „най-тежкия статус").  Сега over_covered влиза и
    в статуса.

    FAILURE означава: график с дублиран покривач се води „approved"."""
    monkeypatch.delenv("EXPORT_POLICY", raising=False)
    boq = [_boq("КСС.xlsx", "A", 2, 100.0)]
    parts = {
        "A": {"status": "approved", "truncated": None, "total_cost": 0.01,
              "schedule": {"tasks": [_task("T1", "КСС.xlsx!A!2", 100.0),
                                     _task("T2", "КСС.xlsx!A!2", 100.0)]}},
    }
    proc = _proc_with_parts(parts)
    res = proc.generate_schedule_staged({}, "distribution", boq_index=boq)

    assert "КСС.xlsx!A!2" in res["coverage"]["over_covered"]
    assert res["status"] == "needs_human_review"        # не „approved"
    assert res["exportable"] is False


def test_provenance_exception_is_fail_closed(monkeypatch):
    """Одит v20 P0: ако САМАТА coverage проверка гръмне (checked=False,
    reason=exception), статусът трябва да СЛЕЗЕ — иначе provisional (default)
    експортира недоказан график.  „Не успях да проверя" = „не е доказано".

    FAILURE означава: отказ на защитната проверка пуска експорт при provisional."""
    monkeypatch.delenv("EXPORT_POLICY", raising=False)   # provisional
    monkeypatch.setattr(
        "src.provenance.analyze_boq_coverage",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    boq = [_boq("КСС.xlsx", "A", 2, 100.0)]
    parts = {
        "A": {"status": "approved", "truncated": None, "total_cost": 0.01,
              "schedule": {"tasks": [_task("T1", "КСС.xlsx!A!2", 100.0)]}},
    }
    proc = _proc_with_parts(parts)
    res = proc.generate_schedule_staged({}, "distribution", boq_index=boq)

    assert res["citation_report"]["checked"] is False
    assert res["status"] == "needs_human_review"
    assert res["exportable"] is False


def test_duplicate_sheet_names_get_unique_prefixes():
    """Два „Водопровод" листа не бива да дадат еднакво „В-" (колизия)."""
    boq = [_boq("КСС1.xlsx", "Водопровод", 2, 100.0),
           _boq("КСС2.xlsx", "Водопровод", 2, 200.0)]
    parts = {
        "Водопровод": {"status": "approved", "truncated": None, "total_cost": 0.0,
                       "schedule": {"tasks": [_task("T1", "x")]}},
    }
    proc = _proc_with_parts(parts)
    res = proc.generate_schedule_staged({}, "distribution", boq_index=boq)
    ids = [t["id"] for t in res["schedule"]["tasks"]]
    assert len(ids) == len(set(ids)), f"колизия на ID-та: {ids}"
