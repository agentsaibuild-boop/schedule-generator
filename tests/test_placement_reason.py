"""Всяка задача казва ЗАЩО е на този ден.

FAILURE означава: src/placement_reason.py е счупен — или задача остава без
причина (тогава на въпрос „защо тази дейност е чак на ден 180" пак се отговаря
на ръка), или причината е сгрешена и графикът твърди неверни неща за себе си.

Най-опасната грешка е `UNEXPLAINED` да изчезне, като се избере правдоподобна
причина вместо истинската.  Затова тук се проверява и че необяснимото ОСТАВА
необяснимо: изместена задача без следа от изравняване и с свободен екип НЕ бива
да мине за нищо друго.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.placement_reason import (  # noqa: E402
    ПРИЧИНИ, describe, explain, report)


def _з(ид, начало, край, **полета) -> dict:
    т = {"id": ид, "name": f"задача {ид}", "start_day": начало,
         "end_day": край, "dependencies": [], "wbs_root": "construction"}
    т.update(полета)
    return т


# ---------------------------------------------------------------------------
# Основните причини
# ---------------------------------------------------------------------------

def test_задача_след_предшественика_си():
    задачи = [_з("A", 1, 5), _з("B", 6, 8, dependencies=[{
        "predecessor_id": "A", "type": "FS", "lag_days": 0}])]
    explain(задачи)
    assert задачи[1]["placement_reason"] == "CHAIN_PREDECESSOR"
    assert "задача A" in задачи[1]["placement_detail"]


def test_първата_задача_не_чака_нищо():
    задачи = [_з("A", 1, 5)]
    explain(задачи)
    assert задачи[0]["placement_reason"] == "NO_CONSTRAINT"


def test_началото_на_фазата_не_е_закъснение():
    """11 задачи на ден 46 не са „495 дни изгубени" — строителството започва."""
    задачи = [_з("D", 1, 45, wbs_root="design"),
              _з("S1", 46, 50), _з("S2", 46, 52)]
    explain(задачи)
    assert задачи[1]["placement_reason"] == "PHASE_START"
    assert "placement_delay_days" not in задачи[1]


def test_екипът_държи_задачата_когато_е_зает():
    задачи = [
        _з("A", 1, 10, team="ЕВ1"),
        _з("B", 11, 15, team="ЕВ1"),      # можеше от ден 1, но ЕВ1 е зает
    ]
    explain(задачи)
    assert задачи[1]["placement_reason"] == "CREW_QUEUE"
    assert задачи[1]["placement_delay_days"] == 10


def test_изравняването_по_ресурс_се_разпознава_по_следата_си():
    задачи = [_з("A", 1, 5), _з("B", 20, 25, leveled=True,
                                leveled_from_day=1)]
    explain(задачи)
    assert задачи[1]["placement_reason"] == "RESOURCE_LEVELLED"
    assert задачи[1]["placement_delay_days"] == 19


def test_разтегнатата_върху_фазата_е_договорна():
    задачи = [_з("A", 1, 5),
              _з("N", 46, 255, duration_source="construction_span")]
    explain(задачи)
    assert задачи[1]["placement_reason"] == "CONTRACT_PHASE"


# ---------------------------------------------------------------------------
# Необяснимото остава необяснимо
# ---------------------------------------------------------------------------

def test_изместена_без_следа_НЕ_получава_правдоподобна_причина():
    """Свободен екип, никаква следа от изравняване — това е дефект, не причина."""
    задачи = [_з("A", 1, 5, team="ЕВ1"), _з("B", 40, 45, team="ЕВ2")]
    бележки = explain(задачи)
    assert задачи[1]["placement_reason"] == "UNEXPLAINED"
    assert any("ВНИМАНИЕ" in б for б in бележки)


def test_всяка_листна_задача_получава_причина():
    задачи = [_з("A", 1, 5), _з("B", 6, 8, dependencies=["A"]),
              _з("C", 46, 50, team="ЕК1"),
              _з("S", 1, 50, is_summary=True)]
    explain(задачи)
    for т in задачи:
        if т.get("is_summary"):
            assert "placement_reason" not in т
        else:
            assert т["placement_reason"] in ПРИЧИНИ


# ---------------------------------------------------------------------------
# Отчетът
# ---------------------------------------------------------------------------

def test_отчетът_брои_задачи_и_дни():
    задачи = [_з("A", 1, 10, team="ЕВ1"), _з("B", 11, 15, team="ЕВ1")]
    explain(задачи)
    о = report(задачи)
    assert о["задачи"] == 2
    assert о["по_причина"]["CREW_QUEUE"]["задачи"] == 1
    assert о["по_причина"]["CREW_QUEUE"]["дни"] == 10
    assert any("CREW_QUEUE" in ред for ред in describe(о))


def test_празен_график_не_гърми():
    assert explain([]) == []
    assert report([])["задачи"] == 0
