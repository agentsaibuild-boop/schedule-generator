"""Unit tests: AI correction не може да подмени входовете на изчислението.

Одит 2026-07-24, точка 1 (централната оставаща): `_restore_determinism`
възстановяваше само `duration`.  Но AI correction връща цял график и може да
подмени количество, DN, материал — а после кодът коректно смята ВЪРХУ
подменения вход и резултатът изглежда доказан.

Възпроизведено от одитора: length_m 720 → 15000, задача B премахната,
резултат status=approved, exportable=true, duration=1000 дни.

Тези полета са ИЗМЕРВАНИЯ от документа, не решения на модела.  AI correction
няма право да ги мени; структурна промяна не се одобрява автоматично.

FAILURE означава: AI пак може индиректно да подмени изчислението и грешният
резултат да изглежда доказан.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai_processor import AIProcessor  # noqa: E402


def _proc() -> AIProcessor:
    return AIProcessor(router=None, knowledge_manager=None)


def _after(tasks: list[dict], status: str = "approved") -> dict:
    return {"schedule": {"tasks": tasks}, "status": status}


# ===================================================================
# Защитените полета се връщат
# ===================================================================

def test_length_substitution_is_reverted():
    """Точният сценарий на одитора."""
    after = _after([{"id": "A", "name": "Полагане DN500 PE", "length_m": 15000,
                     "dn": 500, "material": "PE", "duration": 10,
                     "start_day": 1, "end_day": 10, "dependencies": []}])
    updated, report = _proc()._restore_determinism_after_ai(
        after, '{"tasks":[{"id":"A","length_m":720,"dn":500,"material":"PE"}]}')
    task = updated["schedule"]["tasks"][0]
    assert task["length_m"] == 720
    assert any(r["field"] == "length_m" for r in report["reverted_fields"])


def test_reverted_duration_is_computed_on_the_real_input():
    after = _after([{"id": "A", "name": "Полагане DN500 PE", "length_m": 15000,
                     "dn": 500, "material": "PE", "duration": 10,
                     "start_day": 1, "end_day": 10, "dependencies": []}])
    updated, _ = _proc()._restore_determinism_after_ai(
        after, '{"tasks":[{"id":"A","name":"Полагане DN500 PE","length_m":720,'
               '"dn":500,"material":"PE"}]}')
    # 720 ÷ 15 м/ден = 48д, не 1000
    assert updated["schedule"]["tasks"][0]["duration"] == 48


def test_material_substitution_is_reverted():
    """CI → PE е урок #35 — не бива да мине през correction."""
    after = _after([{"id": "A", "name": "Полагане DN300", "length_m": 300,
                     "dn": 300, "material": "PE", "duration": 5,
                     "start_day": 1, "end_day": 5, "dependencies": []}])
    updated, report = _proc()._restore_determinism_after_ai(
        after, '{"tasks":[{"id":"A","length_m":300,"dn":300,"material":"CI"}]}')
    assert updated["schedule"]["tasks"][0]["material"] == "CI"


def test_dn_substitution_is_reverted():
    after = _after([{"id": "A", "name": "Полагане", "length_m": 300, "dn": 110,
                     "material": "PE", "duration": 5, "start_day": 1,
                     "end_day": 5, "dependencies": []}])
    updated, _ = _proc()._restore_determinism_after_ai(
        after, '{"tasks":[{"id":"A","length_m":300,"dn":500,"material":"PE"}]}')
    assert updated["schedule"]["tasks"][0]["dn"] == 500


def test_source_ref_cannot_be_forged_by_correction():
    after = _after([{"id": "A", "name": "X", "length_m": 300,
                     "source_ref": "ИЗМИСЛЕН!Л!9", "duration": 5,
                     "start_day": 1, "end_day": 5, "dependencies": []}])
    updated, _ = _proc()._restore_determinism_after_ai(
        after, '{"tasks":[{"id":"A","length_m":300,"source_ref":"КСС.xlsx!В!4"}]}')
    assert updated["schedule"]["tasks"][0]["source_ref"] == "КСС.xlsx!В!4"


def test_unchanged_fields_are_not_reported():
    after = _after([{"id": "A", "name": "Полагане", "length_m": 720, "dn": 500,
                     "material": "PE", "duration": 48, "start_day": 1,
                     "end_day": 48, "dependencies": []}])
    _, report = _proc()._restore_determinism_after_ai(
        after, '{"tasks":[{"id":"A","name":"Полагане","length_m":720,"dn":500,'
               '"material":"PE"}]}')
    assert report["reverted_fields"] == []


def test_new_task_fields_are_not_reverted():
    """Нова задача няма 'преди' — стойностите ѝ се оставят."""
    after = _after([
        {"id": "A", "name": "Стара", "length_m": 720, "dn": 500,
         "material": "PE", "duration": 48, "start_day": 1, "end_day": 48,
         "dependencies": []},
        {"id": "NEW", "name": "Нова", "length_m": 300, "dn": 110,
         "material": "PE", "duration": 5, "start_day": 49, "end_day": 53,
         "dependencies": []},
    ])
    updated, _ = _proc()._restore_determinism_after_ai(
        after, '{"tasks":[{"id":"A","length_m":720,"dn":500,"material":"PE"}]}')
    new = next(t for t in updated["schedule"]["tasks"] if t["id"] == "NEW")
    assert new["length_m"] == 300


# ===================================================================
# Структурна промяна → needs_human_review
# ===================================================================

def test_removed_task_forces_human_review():
    after = _after([{"id": "A", "name": "Полагане", "length_m": 720, "dn": 500,
                     "material": "PE", "duration": 48, "start_day": 1,
                     "end_day": 48, "dependencies": []}])
    updated, report = _proc()._restore_determinism_after_ai(
        after, '{"tasks":[{"id":"A","length_m":720},{"id":"B","name":"Изпитване"}]}')
    assert updated["status"] == "needs_human_review"
    assert report["structural_change"] is True
    assert "B" in report["removed_tasks"]


def test_added_task_forces_human_review():
    after = _after([
        {"id": "A", "name": "Полагане", "length_m": 720, "dn": 500,
         "material": "PE", "duration": 48, "start_day": 1, "end_day": 48,
         "dependencies": []},
        {"id": "PHANTOM", "name": "Измислена", "duration": 5, "start_day": 49,
         "end_day": 53, "dependencies": []},
    ])
    updated, report = _proc()._restore_determinism_after_ai(
        after, '{"tasks":[{"id":"A","length_m":720}]}')
    assert updated["status"] == "needs_human_review"
    assert "PHANTOM" in report["added_tasks"]


def test_structural_change_adds_a_remaining_issue():
    after = _after([{"id": "A", "name": "X", "length_m": 720, "dn": 500,
                     "material": "PE", "duration": 48, "start_day": 1,
                     "end_day": 48, "dependencies": []}])
    updated, _ = _proc()._restore_determinism_after_ai(
        after, '{"tasks":[{"id":"A"},{"id":"B"}]}')
    assert any("структ" in i.lower() for i in updated["remaining_issues"])


def test_no_structural_change_keeps_approved():
    after = _after([{"id": "A", "name": "X", "length_m": 720, "dn": 500,
                     "material": "PE", "duration": 48, "start_day": 1,
                     "end_day": 48, "dependencies": []}])
    updated, report = _proc()._restore_determinism_after_ai(
        after, '{"tasks":[{"id":"A","name":"X","length_m":720,"dn":500,'
               '"material":"PE"}]}')
    assert updated["status"] == "approved"
    assert report["structural_change"] is False


def test_combined_attack_is_fully_contained():
    """Подмяна на вход + премахната задача едновременно — сценарият на одитора."""
    after = _after([{"id": "A", "name": "Полагане DN500 PE", "length_m": 15000,
                     "dn": 500, "material": "PE", "duration": 10,
                     "start_day": 1, "end_day": 10, "dependencies": []}])
    updated, report = _proc()._restore_determinism_after_ai(
        after,
        '{"tasks":[{"id":"A","name":"Полагане DN500 PE","length_m":720,'
        '"dn":500,"material":"PE"},{"id":"B","name":"Изпитване"}]}')
    task = updated["schedule"]["tasks"][0]
    assert task["length_m"] == 720                    # входът върнат
    assert task["duration"] == 48                     # смятано върху верния вход
    assert updated["status"] == "needs_human_review"  # структурата хваната
    assert report["reverted_fields"]
    assert report["removed_tasks"] == ["B"]


# ===================================================================
# Регресия
# ===================================================================

def test_protected_fields_list_covers_the_inputs():
    # Одит v5: заключват се и класификационните полета (type/milestone/unit),
    # защото те определят КАК калкулаторът смята продължителност.
    assert set(AIProcessor._LOCKED_FIELDS) >= {
        "length_m", "quantity", "dn", "material", "method", "source_ref",
        "type", "milestone", "is_milestone", "unit"}


# ===================================================================
# Одит v5 — байпаси на blacklist-а (allowlist ги затваря)
# ===================================================================

def test_ai_cannot_add_a_missing_input(_proc=_proc):
    """Точка 1: AI ДОБАВЯ липсвал material → не бива да става авторитетен вход.

    Възпроизведено: material липсва в оригинала, AI слага material='PE',
    кодът смята и бележи 'calculated'.  Сега добавеното поле се трие.
    """
    after = _after([{"id": "A", "name": "Полагане", "length_m": 300, "dn": 300,
                     "material": "PE", "duration": 5, "start_day": 1,
                     "end_day": 5, "dependencies": []}])
    updated, report = _proc()._restore_determinism_after_ai(
        after, '{"tasks":[{"id":"A","name":"Полагане","length_m":300,"dn":300}]}')
    task = updated["schedule"]["tasks"][0]
    assert task.get("material") is None                 # добавеното е изтрито
    assert updated["status"] == "needs_human_review"
    assert any(r["field"] == "material" for r in report["reverted_fields"])


def test_ai_cannot_bypass_dn_lock_via_diameter_alias(_proc=_proc):
    """Точка 2: dn=500 стои, AI добавя diameter=110; detect_dn чете 110."""
    from src.duration_calculator import detect_dn
    after = _after([{"id": "A", "name": "Полагане", "length_m": 300, "dn": 500,
                     "diameter": 110, "material": "PE", "duration": 5,
                     "start_day": 1, "end_day": 5, "dependencies": []}])
    updated, _ = _proc()._restore_determinism_after_ai(
        after, '{"tasks":[{"id":"A","length_m":300,"dn":500,"material":"PE"}]}')
    task = updated["schedule"]["tasks"][0]
    assert task.get("diameter") is None                 # alias изтрит
    assert detect_dn(task) == 500                        # каноничният dn печели


def test_conflicting_dn_aliases_flag_review(_proc=_proc):
    """dn=500 и diameter=110 (различни) в самия AI изход → конфликт + преглед."""
    after = _after([{"id": "A", "name": "X", "dn": 500, "diameter": 110,
                     "duration": 5, "start_day": 1, "end_day": 5,
                     "dependencies": []}])
    updated, report = _proc()._restore_determinism_after_ai(
        after, '{"tasks":[{"id":"A","dn":500}]}')
    assert report["alias_conflicts"]
    assert updated["status"] == "needs_human_review"


def test_ai_cannot_turn_task_into_milestone(_proc=_proc):
    """Точка 3: milestone=true → калкулаторът връща 0 и бележи 'calculated'."""
    after = _after([{"id": "A", "name": "Изкоп", "type": "other",
                     "length_m": 720, "dn": 500, "material": "PE",
                     "milestone": True, "duration": 999, "start_day": 1,
                     "end_day": 10, "dependencies": []}])
    updated, report = _proc()._restore_determinism_after_ai(
        after, '{"tasks":[{"id":"A","name":"Полагане DN500 PE",'
               '"type":"pipe_laying","length_m":720,"dn":500,"material":"PE"}]}')
    task = updated["schedule"]["tasks"][0]
    assert not task.get("milestone")                     # milestone отменен
    assert task.get("type") == "pipe_laying"             # type върнат
    assert updated["status"] == "needs_human_review"
    assert any(r["field"] == "milestone" for r in report["reverted_fields"])


def test_ai_removing_a_dependency_is_structural(_proc=_proc):
    """Точка 4: B губи зависимостта си от A — не е промяна на task ID,
    но е промяна на планиращата логика → структурна → преглед."""
    after = _after([
        {"id": "A", "name": "Изкоп", "start_day": 1, "end_day": 5,
         "duration": 5, "dependencies": []},
        {"id": "B", "name": "Полагане", "start_day": 6, "end_day": 10,
         "duration": 5, "dependencies": []}])
    updated, report = _proc()._restore_determinism_after_ai(
        after, '{"tasks":[{"id":"A","dependencies":[]},'
               '{"id":"B","dependencies":["A"]}]}')
    assert "B" in report["dependency_changes"]
    assert updated["status"] == "needs_human_review"


def test_ai_cannot_reclassify_via_name(_proc=_proc):
    """Одит v6, точка 2: калкулаторът извлича DN/материал/тип от името.
    Смяна само на името („Подготвителни" → „Полагане DN110 PE") пре-
    класифицира входа — затова name е заключено."""
    after = _after([{"id": "A", "name": "Полагане на водопровод DN110 PE",
                     "length_m": 300, "dn": 110, "material": "PE",
                     "duration": 24, "start_day": 1, "end_day": 24,
                     "dependencies": []}])
    updated, report = _proc()._restore_determinism_after_ai(
        after, '{"tasks":[{"id":"A","name":"Подготвителни работи",'
               '"length_m":300,"dn":110,"material":"PE"}]}')
    task = updated["schedule"]["tasks"][0]
    assert task["name"] == "Подготвителни работи"       # името върнато
    assert updated["status"] == "needs_human_review"
    assert any(r["field"] == "name" for r in report["reverted_fields"])


def test_ai_cannot_reassign_team_to_dodge_conflict(_proc=_proc):
    """Одит v7, точка 2: AI мести задача към друг екип/ос/пикетаж, за да
    заобиколи ресурсен или пространствен конфликт."""
    after = _after([{"id": "A", "name": "Полагане", "length_m": 300, "dn": 110,
                     "material": "PE", "team": "T9", "crew_id": "C9",
                     "alignment_id": "L9", "start_chainage": 900,
                     "end_chainage": 960, "duration": 24, "start_day": 1,
                     "end_day": 24, "dependencies": []}])
    updated, report = _proc()._restore_determinism_after_ai(
        after, '{"tasks":[{"id":"A","name":"Полагане","length_m":300,"dn":110,'
               '"material":"PE","team":"T1","crew_id":"C1","alignment_id":"L1",'
               '"start_chainage":0,"end_chainage":60}]}')
    task = updated["schedule"]["tasks"][0]
    assert task["team"] == "T1"
    assert task["start_chainage"] == 0 and task["end_chainage"] == 60
    assert updated["status"] == "needs_human_review"
    reverted = {r["field"] for r in report["reverted_fields"]}
    assert {"team", "crew_id", "alignment_id",
            "start_chainage", "end_chainage"} <= reverted


def test_scalar_equality_tolerates_int_vs_str(_proc=_proc):
    """500 (int) срещу '500' (низ) не е промяна — без фалшив reverted."""
    after = _after([{"id": "A", "name": "X", "dn": "500", "length_m": "720",
                     "duration": 48, "start_day": 1, "end_day": 48,
                     "dependencies": []}])
    _, report = _proc()._restore_determinism_after_ai(
        after, '{"tasks":[{"id":"A","name":"X","dn":500,"length_m":720}]}')
    assert report["reverted_fields"] == []
