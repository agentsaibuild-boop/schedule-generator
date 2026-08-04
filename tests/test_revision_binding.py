"""Unit tests: валидацията принадлежи на КОНКРЕТНА версия на графика.

Одит 2026-07-24 (последният критичен): `last_validation` беше глобален
session резултат, необвързан с конкретния график.  Два обратни риска:
  - сменен график ползва стара „валидна" валидация → невалиден експорт;
  - валиден стар график се блокира от валидацията на отхвърлена ревизия.
Плюс: зареден стар проект се възстановяваше без повторна валидация.

Решение: hash на графика в резултата от валидацията; export gate сравнява
hash-а на текущия график с валидирания.  Разминаване → блокиран.

FAILURE означава: export gate пак може да ползва валидация за ДРУГА версия
на графика.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai_processor import AIProcessor  # noqa: E402

_h = AIProcessor.schedule_hash


def _t(tid, start=1, dur=10, **kw):
    t = {"id": tid, "start_day": start, "duration": dur,
         "end_day": start + dur - 1, "dependencies": []}
    t.update(kw)
    return t


# ===================================================================
# Стабилност и чувствителност
# ===================================================================

def test_same_schedule_same_hash():
    s = [_t("A"), _t("B", 11)]
    assert _h(s) == _h([_t("A"), _t("B", 11)])


def test_task_order_does_not_matter():
    """Разместени задачи са същият график."""
    assert _h([_t("A"), _t("B", 11)]) == _h([_t("B", 11), _t("A")])


def test_duration_change_changes_hash():
    assert _h([_t("A", dur=10)]) != _h([_t("A", dur=48)])


def test_start_day_change_changes_hash():
    assert _h([_t("A", start=1)]) != _h([_t("A", start=5)])


def test_quantity_change_changes_hash():
    assert _h([_t("A", length_m=720)]) != _h([_t("A", length_m=15000)])


def test_material_change_changes_hash():
    assert _h([_t("A", material="PE")]) != _h([_t("A", material="CI")])


def test_dependency_change_changes_hash():
    assert _h([_t("A"), _t("B", 11, dependencies=[])]) != \
           _h([_t("A"), _t("B", 11, dependencies=["A"])])


def test_dependency_type_change_changes_hash():
    d_fs = [{"predecessor_id": "A", "type": "FS", "lag_days": 0}]
    d_ss = [{"predecessor_id": "A", "type": "SS", "lag_days": 0}]
    assert _h([_t("A"), _t("B", dependencies=d_fs)]) != \
           _h([_t("A"), _t("B", dependencies=d_ss)])


def test_legacy_task_level_dep_type_change_changes_hash():
    """Одит v6, точка 4: стар формат (низови deps + task-level type/lag).
    Смяна FS+0 → SS+5 на task-ниво трябва да отмени валидацията."""
    fs = _t("B", dependencies=["A"], dependency_type="FS", lag_days=0)
    ss = _t("B", dependencies=["A"], dependency_type="SS", lag_days=5)
    assert _h([_t("A"), fs]) != _h([_t("A"), ss])


def test_chainage_change_changes_hash():
    assert _h([_t("A", start_chainage=0, end_chainage=300)]) != \
           _h([_t("A", start_chainage=0, end_chainage=500)])


def test_name_change_changes_hash():
    """Одит v5, точка 7: name влияе на класификацията на продължителност,
    затова смяната му ОТМЕНЯ старата валидация — вече е в hash-а."""
    assert _h([_t("A", name="Полагане")]) != _h([_t("A", name="Съвсем друго име")])


def test_cosmetic_change_keeps_hash():
    """Само доказано козметичните полета (бележки) не влияят на hash-а."""
    assert _h([_t("A", notes_msp="бележка")]) == _h([_t("A", notes_msp="друга")])


def test_notes_do_not_affect_hash():
    assert _h([_t("A", notes_msp="бележка 1")]) == _h([_t("A", notes_msp="друга")])


# ===================================================================
# Формати
# ===================================================================

def test_hash_accepts_dict_with_tasks():
    assert _h({"tasks": [_t("A")]}) == _h([_t("A")])


def test_hash_accepts_json_string():
    import json
    s = [_t("A")]
    assert _h(json.dumps({"tasks": s})) == _h(s)


def test_empty_schedule_has_stable_hash():
    assert _h([]) == _h([])


def test_hash_is_short_and_hex():
    h = _h([_t("A")])
    assert len(h) == 16
    assert all(c in "0123456789abcdef" for c in h)


# ===================================================================
# Валидацията носи hash-а
# ===================================================================

def test_validation_result_carries_hash():
    result = AIProcessor._validate_final_schedule({"tasks": [_t("A")]})
    assert "schedule_hash" in result
    assert result["schedule_hash"] == _h([_t("A")])


def test_stale_validation_is_detectable():
    """Сценарият на одитора: валидираме едно, експортираме друго."""
    validated = AIProcessor._validate_final_schedule({"tasks": [_t("A", dur=10)]})
    edited_hash = _h([_t("A", dur=48)])
    assert validated["schedule_hash"] != edited_hash    # → export gate блокира


def test_matching_hash_confirms_same_version():
    schedule = [_t("A"), _t("B", 11)]
    validated = AIProcessor._validate_final_schedule({"tasks": schedule})
    assert validated["schedule_hash"] == _h(schedule)   # → export разрешен
