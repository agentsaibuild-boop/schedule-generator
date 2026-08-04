"""Unit tests: ръчната модификация минава през същите защити като генерирането.

Одит 2026-07-23, точка 4: `_handle_modify_schedule` изпращаше целия график
към AI, приемаше цял нов, правеше САМО структурен diff, показваше
предупреждения и записваше резултата ВИНАГИ.

Одиторът пусна модификация, която създава duration=-5, самозависимост A→A и
кръгова зависимост.  Резултатът беше „Промяната е приложена." със
schedule_updated=True, докато пълният валидатор намираше три твърди грешки.

Тоест дори генериращият pipeline да е поправен, една команда „намали срока с
20 дни" разрушаваше всички инварианти.

FAILURE означава: модификационният път пак е заобиколка около защитите.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai_processor import AIProcessor  # noqa: E402
from src.chat_handler import ChatHandler  # noqa: E402
from src.provenance import QuantityRow, SourceRef  # noqa: E402
from src.schedule_builder import ScheduleBuilder  # noqa: E402

ORIGINAL = [
    {"id": "A", "name": "Изкоп", "start_day": 1, "end_day": 10, "duration": 10,
     "dependencies": []},
    {"id": "B", "name": "Полагане", "start_day": 11, "end_day": 20, "duration": 10,
     "dependencies": ["A"]},
]


def _handler(ai_returns: list[dict]) -> tuple[ChatHandler, MagicMock]:
    """Handler, чийто AI връща зададения „модифициран" график."""
    handler = ChatHandler()
    handler.builder = ScheduleBuilder()
    handler.current_schedule = [dict(t) for t in ORIGINAL]

    # `_progress` се задава в process_message; тук викаме метода директно.
    handler._progress = lambda pct, txt: None

    ai = MagicMock()
    ai.build_system_prompt.return_value = "system"
    ai.build_verification_prompt.return_value = "rules"
    ai.router.chat.return_value = {"content": "{}", "model": "test", "cost": 0.0}
    ai.router.run_correction_cycle.return_value = {
        "status": "approved",
        "schedule": {"tasks": ai_returns},
        "cycles": 1,
        "total_cost": 0.0,
    }
    handler.ai = ai

    project_mgr = MagicMock()
    project_mgr.current_project = {"id": "p1"}
    handler.project_mgr = project_mgr
    return handler, project_mgr


# ===================================================================
# Сценариите на одитора
# ===================================================================

def _validate(tasks: list[dict]) -> dict:
    return AIProcessor._validate_final_schedule({"tasks": tasks})


def test_negative_duration_from_modification_is_invalid():
    bad = [dict(ORIGINAL[0], duration=-5)]
    assert _validate(bad)["valid"] is False


def test_self_dependency_from_modification_is_invalid():
    bad = [{"id": "A", "name": "A", "start_day": 1, "end_day": 10,
            "duration": 10, "dependencies": ["A"]}]
    assert _validate(bad)["valid"] is False


def test_circular_dependency_from_modification_is_invalid():
    bad = [
        {"id": "A", "name": "A", "start_day": 1, "end_day": 10, "duration": 10,
         "dependencies": ["B"]},
        {"id": "B", "name": "B", "start_day": 11, "end_day": 20, "duration": 10,
         "dependencies": ["A"]},
    ]
    assert _validate(bad)["valid"] is False


def test_the_auditors_combined_case_is_caught():
    """duration=-5 + самозависимост + кръгова зависимост наведнъж."""
    bad = [
        {"id": "A", "name": "A", "start_day": 1, "end_day": 10, "duration": -5,
         "dependencies": ["A"]},
        {"id": "B", "name": "B", "start_day": 11, "end_day": 20, "duration": 10,
         "dependencies": ["A"]},
    ]
    result = _validate(bad)
    assert result["valid"] is False
    assert len(result["errors"]) >= 2


# ===================================================================
# Gate поведение
# ===================================================================

def test_invalid_modification_does_not_replace_current_schedule():
    # Циклична зависимост A↔B: валидаторът я хваща, а каскадата на датите НЕ може
    # да я „поправи" (проба 2026-08-04: датовите нарушения вече се авто-каскадират,
    # затова invalid-тригерът тук е цикъл, не разлика в дати).
    handler, _ = _handler([
        {"id": "A", "name": "Изкоп", "start_day": 1, "end_day": 20,
         "duration": 20, "dependencies": ["B"]},
        {"id": "B", "name": "Полагане", "start_day": 5, "end_day": 14,
         "duration": 10, "dependencies": ["A"]},
    ])
    before = [dict(t) for t in handler.current_schedule]

    result = handler._handle_modify_schedule("намали срока с 20 дни")

    assert result["schedule_updated"] is False
    assert handler.current_schedule == before
    assert "ОТХВЪРЛЕНА" in result["response"]


def test_invalid_modification_is_kept_for_diagnostics():
    handler, _ = _handler([   # цикъл A↔B → invalid (не датова разлика — вж. по-горе)
        {"id": "A", "name": "A", "start_day": 1, "end_day": 20, "duration": 20,
         "dependencies": ["B"]},
        {"id": "B", "name": "B", "start_day": 5, "end_day": 14, "duration": 10,
         "dependencies": ["A"]},
    ])
    handler._handle_modify_schedule("промени нещо")
    assert getattr(handler, "rejected_schedule", None) is not None


def test_invalid_modification_is_not_saved_to_project():
    handler, project_mgr = _handler([   # цикъл A↔B → invalid
        {"id": "A", "name": "A", "start_day": 1, "end_day": 20, "duration": 20,
         "dependencies": ["B"]},
        {"id": "B", "name": "B", "start_day": 5, "end_day": 14, "duration": 10,
         "dependencies": ["A"]},
    ])
    handler._handle_modify_schedule("промени нещо")
    project_mgr.save_progress.assert_not_called()


def test_valid_modification_is_applied():
    handler, project_mgr = _handler([
        {"id": "A", "name": "Изкоп", "start_day": 1, "end_day": 8,
         "duration": 8, "dependencies": []},
        {"id": "B", "name": "Полагане", "start_day": 9, "end_day": 18,
         "duration": 10, "dependencies": ["A"]},
    ])
    result = handler._handle_modify_schedule("скъси изкопа с 2 дни")

    assert result["schedule_updated"] is True
    assert "приложена" in result["response"]
    project_mgr.save_progress.assert_called_once()


def test_result_carries_validation():
    handler, _ = _handler(ORIGINAL)
    result = handler._handle_modify_schedule("нещо")
    assert "validation" in result
    assert result["validation"]["checked"] is True


def test_correction_info_reports_invalid():
    handler, _ = _handler([   # цикъл A↔B → invalid
        {"id": "A", "name": "A", "start_day": 1, "end_day": 20, "duration": 20,
         "dependencies": ["B"]},
        {"id": "B", "name": "B", "start_day": 5, "end_day": 14, "duration": 10,
         "dependencies": ["A"]},
    ])
    result = handler._handle_modify_schedule("нещо")
    assert result["correction_info"]["status"] == "invalid"


# ===================================================================
# Детерминизмът се прилага и тук
# ===================================================================

def test_durations_are_recomputed_after_modification():
    """AI задава 999 на изчислима задача — кодът си я връща и тук."""
    handler, _ = _handler([
        {"id": "В01", "name": "Полагане DN500 PE", "length_m": 720, "dn": 500,
         "material": "PE", "duration": 999, "start_day": 1, "end_day": 999,
         "dependencies": []},
    ])
    result = handler._handle_modify_schedule("удължи полагането")
    tasks = AIProcessor._tasks_from(result["schedule_data"])

    assert tasks[0]["duration"] == 48
    assert tasks[0]["duration_source"] == "calculated"


def test_duration_report_is_returned():
    handler, _ = _handler([
        {"id": "В01", "name": "Полагане DN500 PE", "length_m": 720, "dn": 500,
         "material": "PE", "duration": 999, "start_day": 1, "end_day": 999,
         "dependencies": []},
    ])
    result = handler._handle_modify_schedule("промени")
    assert result["duration_report"]["applied"] is True


def test_provenance_survives_modification():
    handler, _ = _handler([
        {"id": "В01", "name": "Полагане DN500 PE", "length_m": 720, "dn": 500,
         "material": "PE", "duration": 48, "start_day": 1, "end_day": 48,
         "dependencies": []},
    ])
    result = handler._handle_modify_schedule("нещо")
    tasks = AIProcessor._tasks_from(result["schedule_data"])
    assert all("duration_source" in t for t in tasks)


# ===================================================================
# Регресия за бъг, въведен при поправката на gate-а
# ===================================================================

def test_modification_flow_has_no_undefined_valid_flag():
    """`_valid` беше добавен глобално и остана недефиниран тук → NameError."""
    handler, _ = _handler(ORIGINAL)
    result = handler._handle_modify_schedule("нещо")   # не бива да гърми
    assert "response" in result


# ===================================================================
# Одит v5, точка 6 — модификацията връща СВЕЖО решение за експорт
# ===================================================================

def test_modification_returns_a_fresh_export_decision():
    """last_export не бива да оцелее от предишната версия.

    Възпроизведено от одитора: чист график (exportable=True) → чат промяна →
    UI обновяваше last_validation, но last_export оставаше True.  Сега този
    път връща собствено `export`, което app.py записва в last_export.
    """
    handler, _ = _handler(ORIGINAL)
    result = handler._handle_modify_schedule("нещо")
    assert "export" in result
    assert "exportable" in result["export"]


def test_invalid_modification_export_is_blocked():
    handler, _ = _handler([
        {"id": "A", "name": "A", "start_day": 1, "end_day": 20, "duration": 20,
         "dependencies": []},
        {"id": "B", "name": "B", "start_day": 5, "end_day": 14, "duration": 10,
         "dependencies": ["A"]},   # невалиден
    ])
    result = handler._handle_modify_schedule("нещо")
    assert result["export"]["exportable"] is False


def test_valid_clean_modification_export_is_allowed():
    # Реален проект с КСС: задача B покрива BOQ реда → произходът е ПРОВЕРЕН
    # (одит v22: без проверим произход модификацията вече не е експортируема).
    # before/after се различават САМО по A (поисканото) — B е идентична, за да
    # не я флагне modification lock-ът като непоискана промяна.
    _covering_B = {"id": "B", "name": "Полагане DN110 PE", "length_m": 100,
                   "unit": "м", "source_ref": "КСС.xlsx!A!2",
                   "duration": 10, "dependencies": ["A"]}
    handler, _ = _handler([
        {"id": "A", "name": "Изкоп", "start_day": 1, "end_day": 6,
         "duration": 6, "dependencies": []},
        {**_covering_B, "start_day": 7, "end_day": 16},
    ])
    handler.current_schedule = [
        {"id": "A", "name": "Изкоп", "start_day": 1, "end_day": 8,
         "duration": 8, "dependencies": []},
        {**_covering_B, "start_day": 9, "end_day": 18},
    ]
    handler._boq_index = lambda: [
        QuantityRow("Реконструкция водопровод DN110 PE", 100.0, "м",
                    SourceRef("КСС.xlsx", "A", 2), {})]
    result = handler._handle_modify_schedule("скъси изкопа")
    # default policy=provisional: валиден + покрит КСС + проверен произход → експорт
    assert result["export"]["exportable"] is True


# ===================================================================
# Одит v6, точка 1 — сринат/спрян контрольор НЕ прилага модификацията
# ===================================================================

def _handler_with_status(tasks: list[dict], ctrl_status: str):
    handler, project_mgr = _handler(tasks)
    handler.ai.router.run_correction_cycle.return_value = {
        "status": ctrl_status,
        "schedule": {"tasks": tasks},
        "cycles": 1,
        "total_cost": 0.0,
    }
    return handler, project_mgr


def test_unrequested_changes_are_reverted_and_flagged():
    """Одит v8, точка 1: човек иска само A, но AI пипа НЕПОИСКАНАТА B.

    Заключените полета на B се връщат към оригинала, статусът става
    needs_human_review и графикът не е експортируем — докато поисканата
    промяна на A оцелява.
    """
    orig = [
        {"id": "A", "name": "Полагане DN500 PE", "length_m": 300, "dn": 500,
         "material": "PE", "start_day": 1, "end_day": 20, "duration": 20,
         "team": "T1", "dependencies": []},
        {"id": "B", "name": "Полагане DN500 PE", "length_m": 720, "dn": 500,
         "material": "PE", "start_day": 21, "end_day": 68, "duration": 48,
         "team": "T2", "crew_id": "C2", "alignment_id": "L1",
         "start_chainage": 0, "end_chainage": 300, "dependencies": ["A"]},
    ]
    # AI скъсява поисканата A, но незаявено мести B към друг екип/ос/пикетаж.
    handler, _ = _handler_with_status([
        {"id": "A", "name": "Полагане DN500 PE", "length_m": 300, "dn": 500,
         "material": "PE", "start_day": 1, "end_day": 18, "duration": 18,
         "team": "T1", "dependencies": []},
        {"id": "B", "name": "Полагане DN500 PE", "length_m": 720, "dn": 500,
         "material": "PE", "start_day": 19, "end_day": 66, "duration": 48,
         "team": "T9", "crew_id": "C9", "alignment_id": "L9",
         "start_chainage": 900, "end_chainage": 1200, "dependencies": ["A"]},
    ], "approved")
    handler.current_schedule = [dict(t) for t in orig]

    result = handler._handle_modify_schedule("Скъси задача A")
    tasks = {t["id"]: t for t in AIProcessor._tasks_from(result["schedule_data"])}

    assert tasks["B"]["team"] == "T2"                    # непоисканото върнато
    assert tasks["B"]["start_chainage"] == 0
    assert tasks["B"]["alignment_id"] == "L1"
    assert result["correction_info"]["status"] == "needs_human_review"
    assert result["export"]["exportable"] is False
    assert "НЕПОИСКАНИ" in result["response"]


def test_requested_task_change_is_allowed():
    """Промяна на ИЗРИЧНО посочена задача (T5) минава без блокиране."""
    orig = [
        {"id": "T5", "name": "Полагане DN500 PE", "length_m": 720, "dn": 500,
         "material": "PE", "source_ref": "КСС.xlsx!A!2",
         "start_day": 1, "end_day": 48, "duration": 48,
         "team": "T1", "dependencies": []},
    ]
    handler, _ = _handler_with_status([
        {"id": "T5", "name": "Полагане DN500 PE", "length_m": 720, "dn": 500,
         "material": "PE", "source_ref": "КСС.xlsx!A!2",
         "start_day": 1, "end_day": 48, "duration": 48,
         "team": "T3", "dependencies": []},   # човекът поиска смяна на екипа на T5
    ], "approved")
    handler.current_schedule = [dict(t) for t in orig]
    handler._boq_index = lambda: [           # проверим произход (одит v22)
        QuantityRow("Реконструкция водопровод DN500 PE", 720.0, "м",
                    SourceRef("КСС.xlsx", "A", 2), {})]

    result = handler._handle_modify_schedule("Смени екипа на T5 на T3")
    tasks = {t["id"]: t for t in AIProcessor._tasks_from(result["schedule_data"])}
    assert tasks["T5"]["team"] == "T3"                   # поисканото оцелява
    assert result["correction_info"]["status"] == "approved"


@pytest.mark.parametrize("ctrl_status", ["error", "stopped"])
def test_failed_controller_does_not_apply_modification(ctrl_status):
    """Дори при структурно валиден JSON, error/stopped не сменят графика."""
    valid = [
        {"id": "A", "name": "Изкоп", "start_day": 1, "end_day": 8,
         "duration": 8, "dependencies": []},
        {"id": "B", "name": "Полагане", "start_day": 9, "end_day": 18,
         "duration": 10, "dependencies": ["A"]},
    ]
    handler, project_mgr = _handler_with_status(valid, ctrl_status)
    before = [dict(t) for t in handler.current_schedule]

    result = handler._handle_modify_schedule("нещо")

    assert result["schedule_updated"] is False
    assert handler.current_schedule == before
    assert result["export"]["exportable"] is False
    project_mgr.save_progress.assert_not_called()


# ===================================================================
# Одит v18 P0 #1 — coverage gate ЛИПСВАШЕ в модификацията
# ===================================================================

def test_modification_that_removes_only_coverer_blocks_export(monkeypatch):
    """Одит v18 P0 #1: чат промяна, която оставя КСС ред без производствен
    покривач, вече се хваща и тук — не само в генерирането.

    Единственият покривач (полагане, цитира КСС!A!2) се ПРЕИМЕНУВА на приемане
    (непроизводствена дейност) чрез изрична заявка → структурата не се променя
    (modification lock не блокира), но редът остава НЕПОКРИТ.  Под strict това
    вече прави графика неекспортируем; преди Fix A coverage gate не се пускаше
    при модификация и strict пускаше непокрит график.

    FAILURE означава: изтриване/обезсилване на покривач през чат пак минава за
    „готов за възложител"."""
    monkeypatch.setenv("EXPORT_POLICY", "strict")
    handler, _ = _handler([
        # AI връща същия id, но вече като ПРИЕМАНЕ (непроизводствено)
        {"id": "P1", "name": "Приемане на СМР", "length_m": 100, "unit": "м",
         "source_ref": "КСС.xlsx!A!2", "start_day": 1, "end_day": 5,
         "duration": 5, "dependencies": []},
    ])
    handler.current_schedule = [
        {"id": "P1", "name": "Полагане DN110 PE", "length_m": 100, "unit": "м",
         "source_ref": "КСС.xlsx!A!2", "start_day": 1, "end_day": 5,
         "duration": 5, "dependencies": []},
    ]
    handler._boq_index = lambda: [
        QuantityRow("Реконструкция водопровод DN110 PE", 100.0, "м",
                    SourceRef("КСС.xlsx", "A", 2), {})]

    result = handler._handle_modify_schedule("Преименувай P1 на приемане на СМР")

    # Одит v19: непокритието СВАЛЯ статуса до needs_human_review, затова графикът
    # не е експортируем ПРЕДИ да се стигне до policy-зависимите blockers.
    assert result["export"]["exportable"] is False
    assert result["correction_info"]["status"] == "needs_human_review"


def test_ambiguous_coverage_blocks_export_under_DEFAULT_policy(monkeypatch):
    """Одит v19 P0: точната репродукция на одитора.

    BOQ „пътни знаци 5 бр" + задача „ревизионни шахти 5 бр" → coverage връща
    `ambiguous` (неопределим клас-покривач).  Документацията твърди, че ambiguous
    е fail-closed НАВСЯКЪДЕ, независимо от policy.  Преди поправката provisional
    (default) игнорираше blockers и графикът оставаше експортируем след чат
    промяна.  Сега непокритието СВАЛЯ статуса → неекспортируем при ВСЯКА policy.

    FAILURE означава: непроверима BOQ позиция след чат промяна пак е
    експортируема при стандартната (provisional) инсталация."""
    monkeypatch.delenv("EXPORT_POLICY", raising=False)   # default = provisional
    handler, _ = _handler([
        {"id": "S1", "name": "Монтаж ревизионни шахти", "length_m": 5, "unit": "бр",
         "source_ref": "КСС.xlsx!A!2", "start_day": 1, "end_day": 5,
         "duration": 5, "dependencies": []},
    ])
    handler.current_schedule = [
        {"id": "S1", "name": "Монтаж ревизионни шахти", "length_m": 5, "unit": "бр",
         "source_ref": "КСС.xlsx!A!2", "start_day": 1, "end_day": 5,
         "duration": 5, "dependencies": []},
    ]
    handler._boq_index = lambda: [
        QuantityRow("Доставка и монтаж на пътни знаци", 5.0, "бр",
                    SourceRef("КСС.xlsx", "A", 2), {})]

    result = handler._handle_modify_schedule("Нищо съществено")

    assert result["export"]["export_policy"] == "provisional"   # стандартната инсталация
    assert result["correction_info"]["status"] == "needs_human_review"
    assert result["export"]["exportable"] is False              # въпреки provisional


@pytest.mark.parametrize("policy", ["strict", "provisional", "lenient"])
def test_missing_boq_index_blocks_modification_under_ALL_policies(monkeypatch, policy):
    """Одит v22 P0 #1 (acceptance): чат модификация при ЛИПСВАЩ BOQ индекс трябва
    ВИНАГИ да завършва с needs_human_review и exportable=False — вкл. provisional
    И lenient.  (v21 пропускаше липсващия индекс при модификация; одитът го
    отхвърли: без проверим произход промяната е недоказана.)

    FAILURE означава: модификация без проверим произход е експортируема при някоя
    policy."""
    monkeypatch.setenv("EXPORT_POLICY", policy)
    handler, _ = _handler([
        {"id": "A", "name": "Изкоп", "start_day": 1, "end_day": 8,
         "duration": 8, "dependencies": []},
        {"id": "B", "name": "Полагане", "start_day": 9, "end_day": 18,
         "duration": 10, "dependencies": ["A"]},
    ])
    handler._boq_index = lambda: []          # ЛИПСВАЩ индекс (напр. провалена конверсия)

    result = handler._handle_modify_schedule("скъси изкопа")

    assert result["correction_info"]["status"] == "needs_human_review"
    assert result["export"]["exportable"] is False
    assert result["export"]["export_policy"] == policy


def test_provenance_exception_blocks_modification_export(monkeypatch):
    """Одит v20 P0: ако coverage проверката при модификация гръмне
    (checked=False, reason=exception), статусът СЛИЗА до needs_human_review —
    иначе provisional експортира недоказана промяна.

    FAILURE означава: отказ на проверката пуска експорт на модификация при
    provisional."""
    monkeypatch.delenv("EXPORT_POLICY", raising=False)   # provisional
    monkeypatch.setattr(
        "src.provenance.analyze_boq_coverage",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    handler, _ = _handler([
        {"id": "A", "name": "Изкоп", "start_day": 1, "end_day": 8,
         "duration": 8, "dependencies": []},
        {"id": "B", "name": "Полагане", "start_day": 9, "end_day": 18,
         "duration": 10, "dependencies": ["A"]},
    ])
    handler._boq_index = lambda: [
        QuantityRow("Реконструкция водопровод DN110 PE", 100.0, "м",
                    SourceRef("КСС.xlsx", "A", 2), {})]

    result = handler._handle_modify_schedule("скъси изкопа")

    assert result["correction_info"]["status"] == "needs_human_review"
    assert result["export"]["exportable"] is False


def test_removed_coverer_uncovered_blocks_export_under_DEFAULT_policy(monkeypatch):
    """Физически тест 2026-08-03 (P0): чат-промяна ПРЕМАХВА единствения покривач
    на BOQ ред (напр. „Полагане канализация DN400 PVC — 55 m").  Под strict
    правилно блокираше, но под DEFAULT provisional графикът оставаше
    exportable=True въпреки uncovered=1 — непълен график минаваше за възложител.

    Сега непокритието СВАЛЯ статуса → неекспортируем при ВСЯКА policy.

    FAILURE означава: изтриване на покривач през чат пак дава „готов" непълен
    график при стандартната (provisional) инсталация."""
    monkeypatch.delenv("EXPORT_POLICY", raising=False)   # default = provisional
    ref = "КСС.xlsx!Канализация!4"
    handler, _ = _handler([
        # AI връща график БЕЗ покривача (премахнат) — остава само друга задача
        {"id": "B", "name": "Изпитване канал", "length_m": 55, "unit": "м",
         "source_ref": "x", "start_day": 1, "end_day": 2, "duration": 2,
         "dependencies": []},
    ])
    handler.current_schedule = [
        {"id": "К04", "name": "Полагане канализационна тръба DN400 PVC",
         "length_m": 55, "unit": "м", "source_ref": ref,
         "start_day": 1, "end_day": 6, "duration": 6, "dependencies": []},
        {"id": "B", "name": "Изпитване канал", "length_m": 55, "unit": "м",
         "source_ref": "x", "start_day": 7, "end_day": 8, "duration": 2,
         "dependencies": ["К04"]},
    ]
    handler._boq_index = lambda: [
        QuantityRow("Полагане канализационна тръба DN400 PVC", 55.0, "м",
                    SourceRef("КСС.xlsx", "Канализация", 4), {})]

    result = handler._handle_modify_schedule("Премахни полагането на канализация DN400")

    assert result["export"]["export_policy"] == "provisional"
    assert result["correction_info"]["status"] == "needs_human_review"
    assert result["export"]["exportable"] is False


# ===================================================================
# Одит v9 — цел по ИМЕ, field-level, fail-closed при неясна заявка
# ===================================================================

def _lock(after, before, message):
    return AIProcessor.enforce_modification_lock(after, before, message)


def test_natural_language_target_by_name_protects_others():
    """Заявка по ИМЕ (без task ID) намира целта; непоисканите се заключват."""
    before = [
        {"id": "A", "name": "Полагане на водопровод DN110 PE", "length_m": 100,
         "team": "T1", "dependencies": []},
        {"id": "B", "name": "Канализация DN400", "length_m": 720, "team": "T2",
         "start_chainage": 0, "end_chainage": 720, "dependencies": []},
    ]
    after = [
        {"id": "A", "name": "Полагане на водопровод DN110 PE", "length_m": 450,
         "team": "T1", "dependencies": []},
        {"id": "B", "name": "Канализация DN400", "length_m": 15000, "team": "T9",
         "start_chainage": 900, "end_chainage": 15900, "dependencies": []},
    ]
    rep = _lock(after, before, "Промени количеството на водопровода на 450 м")
    by = {t["id"]: t for t in after}
    assert by["A"]["length_m"] == 450          # поисканото оцелява
    assert by["B"]["length_m"] == 720          # непоисканото върнато
    assert by["B"]["team"] == "T2"
    assert rep["unrequested_change"] is True


def test_field_level_intent_locks_other_fields_of_target():
    """Посочен екип на T5 → само екипът е свободен; количеството се връща."""
    before = [{"id": "T5", "name": "Полагане DN500 PE", "length_m": 720,
               "team": "T1", "crew_id": "C1", "start_chainage": 0,
               "end_chainage": 720, "dependencies": []}]
    after = [{"id": "T5", "name": "Полагане DN500 PE", "length_m": 15000,
              "team": "T3", "crew_id": "C9", "start_chainage": 900,
              "end_chainage": 15900, "dependencies": []}]
    rep = _lock(after, before, "Смени екипа на T5 на T3")
    t = after[0]
    assert t["team"] == "T3"                    # поисканото поле оцелява
    assert t["length_m"] == 720                 # непоисканото поле върнато
    assert t["start_chainage"] == 0
    assert rep["unrequested_change"] is True


def test_vague_request_is_fail_closed():
    """Неясна заявка без цел → всичко заключено; замяна на задача се хваща."""
    before = [{"id": "A", "name": "Изкоп", "team": "T1", "dependencies": []},
              {"id": "B", "name": "Полагане", "dependencies": []}]
    after = [{"id": "A", "name": "Изкоп", "team": "T9", "dependencies": []},
             {"id": "C", "name": "Ново", "dependencies": []}]
    rep = _lock(after, before, "Оптимизирай графика")
    assert after[0]["team"] == "T1"             # промяната на A върната
    assert "C" in rep["added"] and "B" in rep["removed"]
    assert rep["unrequested_change"] is True
