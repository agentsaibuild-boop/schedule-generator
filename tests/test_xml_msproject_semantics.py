"""Unit tests: XML-ът не противоречи сам на себе си и не лъже календара.

Одит 2026-07-23, точка 8 — три дефекта в изхода към MS Project:

1. Milestone получаваше `PT8H0M0S` И `<Milestone>1</Milestone>` едновременно.
   Milestone е точка във времето — не може да трае 8 часа.

2. ВСЯКА задача получаваше `ConstraintType=2` (Must Start On).  Това пази
   датите, но прави зависимостите декоративни: MS Project не може да
   пренареди графика, а рапортува конфликти.

3. Датите се смятаха с `timedelta(days=...)` (КАЛЕНДАРНИ дни), докато
   продължителността е в РАБОТНИ часове.  При 5-дневен календар двете се
   разминават около уикендите и Project премества задачите при отваряне.

FAILURE означава: файлът, който получава възложителят, се преправя сам при
отваряне или съдържа вътрешно противоречиви данни.
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.export_xml import _working_day_to_date, export_to_mspdi_xml  # noqa: E402

NS = "{http://schemas.microsoft.com/project}"


def _tasks(xml: str) -> list[ET.Element]:
    root = ET.fromstring(xml)
    return [t for t in root.iter(f"{NS}Task")
            if (t.findtext(f"{NS}UID") or "") != "0"]


def _xml(schedule, **kw) -> str:
    out = export_to_mspdi_xml(schedule, "Тест", kw.pop("start", "2026-08-03"), **kw)
    return out.decode() if isinstance(out, bytes) else str(out)


MILESTONE = {"id": "M", "name": "ФИНАЛ", "duration": 0, "start_day": 5,
             "end_day": 5, "dependencies": []}
NORMAL = {"id": "A", "name": "Изкоп", "duration": 10, "start_day": 1,
          "end_day": 10, "dependencies": []}


# ===================================================================
# 1. Milestone
# ===================================================================

def test_milestone_has_zero_duration():
    task = _tasks(_xml([MILESTONE]))[0]
    assert task.findtext(f"{NS}Duration") == "PT0H0M0S"


def test_milestone_is_flagged():
    task = _tasks(_xml([MILESTONE]))[0]
    assert task.findtext(f"{NS}Milestone") == "1"


def test_milestone_is_not_self_contradictory():
    """Ядрото: 8 часа + Milestone=1 едновременно е невъзможно състояние."""
    task = _tasks(_xml([MILESTONE]))[0]
    duration = task.findtext(f"{NS}Duration")
    flagged = task.findtext(f"{NS}Milestone") == "1"
    assert not (flagged and duration != "PT0H0M0S")


def test_normal_task_keeps_eight_hours_per_day():
    task = _tasks(_xml([NORMAL]))[0]
    assert task.findtext(f"{NS}Duration") == "PT80H0M0S"
    assert task.findtext(f"{NS}Milestone") == "0"


def test_one_day_task_is_not_a_milestone():
    one = dict(NORMAL, duration=1, end_day=1)
    task = _tasks(_xml([one]))[0]
    assert task.findtext(f"{NS}Duration") == "PT8H0M0S"
    assert task.findtext(f"{NS}Milestone") == "0"


# ===================================================================
# 2. Constraint mode
# ===================================================================

def test_pinned_is_the_default():
    """Досегашното поведение (урок #19) остава по подразбиране."""
    task = _tasks(_xml([NORMAL]))[0]
    assert task.findtext(f"{NS}ConstraintType") == "2"
    assert task.findtext(f"{NS}ConstraintDate")


def test_flexible_mode_lets_msproject_schedule():
    task = _tasks(_xml([NORMAL], constraint_mode="flexible"))[0]
    assert task.findtext(f"{NS}ConstraintType") == "0"   # As Soon As Possible


def test_flexible_mode_sets_no_constraint_date():
    """Закована дата при ASAP е противоречие."""
    task = _tasks(_xml([NORMAL], constraint_mode="flexible"))[0]
    assert task.findtext(f"{NS}ConstraintDate") is None


def test_both_modes_stay_auto_scheduled():
    """Урок #19: Manual=0 — без пин икони в Task Mode колоната."""
    for mode in ("pinned", "flexible"):
        task = _tasks(_xml([NORMAL], constraint_mode=mode))[0]
        assert task.findtext(f"{NS}Manual") == "0"


def test_flexible_mode_keeps_dependencies():
    schedule = [NORMAL, {"id": "B", "name": "Полагане", "duration": 5,
                         "start_day": 11, "end_day": 15, "dependencies": ["A"]}]
    xml = _xml(schedule, constraint_mode="flexible")
    assert len(re.findall(r"<PredecessorUID>", xml)) == 1


# ===================================================================
# 3. Календарна аритметика
# ===================================================================

from datetime import datetime  # noqa: E402


def test_seven_day_calendar_counts_every_day():
    start = datetime(2026, 8, 3)                 # понеделник
    assert _working_day_to_date(start, 1, "7-day") == start
    assert _working_day_to_date(start, 7, "7-day") == datetime(2026, 8, 9)


def test_five_day_calendar_skips_the_weekend():
    start = datetime(2026, 8, 3)                 # понеделник
    # работен ден 5 = петък 7-ми; ден 6 = понеделник 10-ти, не събота 8-ми
    assert _working_day_to_date(start, 5, "5-day") == datetime(2026, 8, 7)
    assert _working_day_to_date(start, 6, "5-day") == datetime(2026, 8, 10)


def test_five_day_calendar_over_two_weeks():
    start = datetime(2026, 8, 3)
    assert _working_day_to_date(start, 11, "5-day") == datetime(2026, 8, 17)


def test_five_day_calendar_handles_weekend_project_start():
    """Проект, започващ в събота, стартира от следващия работен ден."""
    saturday = datetime(2026, 8, 8)
    assert _working_day_to_date(saturday, 1, "5-day") == datetime(2026, 8, 10)


def test_day_index_below_one_is_clamped():
    start = datetime(2026, 8, 3)
    assert _working_day_to_date(start, 0, "7-day") == start


def test_five_day_export_does_not_place_tasks_on_weekends():
    """Регресия: с календарни дни задачите падаха в събота и неделя."""
    schedule = [{"id": f"T{i}", "name": f"Задача {i}", "duration": 1,
                 "start_day": i, "end_day": i, "dependencies": []}
                for i in range(1, 11)]
    xml = _xml(schedule, calendar_type="5-day", start="2026-08-03")

    for task in _tasks(xml):
        start_text = task.findtext(f"{NS}Start") or ""
        day = datetime.strptime(start_text[:10], "%Y-%m-%d")
        assert day.weekday() < 5, f"{task.findtext(f'{NS}Name')} е в почивен ден"


def test_seven_day_export_is_unchanged():
    """7-дневният режим е по подразбиране и не бива да се променя."""
    xml = _xml([NORMAL], calendar_type="7-day", start="2026-08-03")
    task = _tasks(xml)[0]
    assert (task.findtext(f"{NS}Start") or "").startswith("2026-08-03")


def test_duration_format_still_days():
    """Урок #19 — не бива да се губи при тези промени."""
    assert "<DurationFormat>5</DurationFormat>" in _xml([NORMAL])


# ===================================================================
# Зависимости при ЦЕЛОЧИСЛЕНИ ID-та (реален DeepSeek изход, 2026-08)
# ===================================================================

def test_dependencies_survive_integer_task_ids():
    """Регресия: при int ID-та (DeepSeek ги генерира числови) зависимостите
    тихо се ИЗПУСКАХА от MS Project XML — uid_map се пълнеше с int ключ, а
    търсенето беше str(dep). Сега и двете са нормализирани към str."""
    schedule = [
        {"id": 1, "name": "Полагане DN300 PP", "duration": 9, "start_day": 1,
         "dependencies": []},
        {"id": 2, "name": "Засипване", "duration": 4, "start_day": 10,
         "dependencies": [1]},
        {"id": 3, "name": "Финал", "duration": 0, "start_day": 20,
         "milestone": True, "dependencies": [2]},
    ]
    xml = _xml(schedule)
    links = [e for e in ET.fromstring(xml).iter(f"{NS}PredecessorLink")]
    assert len(links) == 2, "зависимостите трябва да оцелеят при int ID-та"
    puids = {l.findtext(f"{NS}PredecessorUID") for l in links}
    assert puids == {"1", "2"}
