"""Договорът на телеметрията: какво ТРЯБВА да носи всеки записан прогон.

НЕЗАВИСИМ ОДИТ 18.08.2026:

  „Документите казват, че `template_applicability_ok` е ТВЪРД флаг, но в 0/40
   сурови прогона това поле съществува.  Документите казват, че
   `concurrency_construction_span_days` е поправен, но при 32/32 evaluated
   runs той още е равен на `total_days`, а не на `phase_days['construction']`.
   Следователно 15/40 не е clean rate на текущата версия."

Прав е и по двете.  Коренът е един: артефактите не носеха версия, затова
разминаването между „поправено в кода" и „доказано от прогона" се хващаше само
на ръка, от одитора, и то през ден.

Тук същото се проверява машинно.  Двата вида тест са НАРОЧНО разделени:

  * договорът върху ПРЯСНО сметната телеметрия — той не бива да пада никога;
  * състоянието на ЗАПИСАНИТЕ файлове — той пада, докато те са от стара версия,
    и точно това е полезното: превръща „още не е доказано" в червен тест
    вместо в изречение в документ.

FAILURE означава: или телеметрията пак не носи каквото твърдим, или сме
пакетирали прогони от една версия с документи от друга.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.schedule_diagnostics import (  # noqa: E402
    HARD_STRUCTURAL_FLAGS, concurrency_report, phase_spans, structural_flags)

RUNS_DIR = Path(__file__).parent.parent / "docs" / "прогони"


def _график() -> list[dict]:
    """Малък график с РАЗЛИЧНИ фази — иначе проверката е празна."""
    return [
        {"id": "D1", "name": "проектиране", "duration": 30, "start_day": 1,
         "end_day": 30, "dependencies": [], "wbs_root": "design"},
        {"id": "К1", "name": "изкоп", "duration": 5, "start_day": 40,
         "end_day": 44, "dependencies": [], "wbs_root": "construction"},
        {"id": "К2", "name": "полагане", "duration": 5, "start_day": 45,
         "end_day": 49, "dependencies": ["К1"], "wbs_root": "construction"},
        {"id": "A1", "name": "приемане", "duration": 10, "start_day": 60,
         "end_day": 69, "dependencies": ["К2"], "wbs_root": "acceptance"},
    ]


# ---------------------------------------------------------------------------
# Договорът върху прясно сметната телеметрия
# ---------------------------------------------------------------------------


def test_construction_span_equals_the_construction_phase():
    """P0.3 на одитора: показателят мери СТРОИТЕЛСТВОТО, не проекта."""
    задачи = _график()

    отчет = concurrency_report(задачи)
    фази = phase_spans(задачи)

    assert отчет["construction_span_days"] == фази["construction"]["days"], (
        "показателят за строителството пак мери нещо друго: "
        f"{отчет['construction_span_days']} срещу "
        f"{фази['construction']['days']}")


def test_construction_span_is_not_the_whole_project():
    задачи = _график()
    целият = max(t["end_day"] for t in задачи) - min(t["start_day"] for t in задачи) + 1

    assert concurrency_report(задачи)["construction_span_days"] < целият, (
        "строителният обхват съвпада с целия проект — филтърът пак пропуска "
        "проектирането и приемането вътре")


def test_every_hard_flag_is_emitted():
    """Твърд флаг, който го няма в отчета, не може да падне."""
    флагове = structural_flags(_график(), packages=[], chains={}, boq_index=[],
                               conservation={}, parse_errors=[])

    липсват = [f for f in HARD_STRUCTURAL_FLAGS if f not in флагове]
    assert not липсват, f"твърди флагове без стойност в отчета: {липсват}"


def test_template_applicability_is_among_them():
    """Изрично, защото точно него одиторът не намери в 40/40 прогона."""
    флагове = structural_flags(_график(), packages=[], chains={}, boq_index=[],
                               conservation={}, parse_errors=[])

    assert "template_applicability_ok" in флагове
    assert "template_applicability_ok" in HARD_STRUCTURAL_FLAGS


# ---------------------------------------------------------------------------
# Състоянието на ЗАПИСАНИТЕ прогони
# ---------------------------------------------------------------------------


def _записани() -> list[tuple[str, dict]]:
    редове: list[tuple[str, dict]] = []
    for файл in sorted(RUNS_DIR.glob("серия-[1-4]-*.json")):
        for запис in json.loads(файл.read_text(encoding="utf-8")):
            редове.append((f"{файл.stem}#{запис.get('run')}", запис))
    return редове


@pytest.mark.snapshot
@pytest.mark.skipif(not RUNS_DIR.exists(), reason="няма записани прогони")
def test_every_recorded_run_carries_its_version():
    """Прогон без отпечатък не може да бъде отнесен към версия.

    ЩЕ ПАДА, докато записаните прогони са отпреди `audit_manifest` — и това е
    целта: одиторът го установи на ръка, сега го казва тестът.
    """
    без = [име for име, запис in _записани() if not запис.get("manifest_id")]

    assert not без, (
        f"{len(без)} записани прогона са без manifest_id — не може да се каже "
        "от коя версия са.  Пусни серията наново с текущия код.")


@pytest.mark.snapshot
@pytest.mark.skipif(not RUNS_DIR.exists(), reason="няма записани прогони")
def test_recorded_runs_come_from_one_version():
    версии = {запис.get("manifest_id") for _, запис in _записани()
              if запис.get("manifest_id")}

    assert len(версии) <= 1, (
        f"записаните прогони са от {len(версии)} различни версии: {версии}")


@pytest.mark.snapshot
@pytest.mark.skipif(not RUNS_DIR.exists(), reason="няма записани прогони")
def test_recorded_runs_carry_the_current_hard_flags():
    """Прогон без `template_applicability_ok` е от преди гейта."""
    без = [име for име, запис in _записани()
           if запис.get("evaluated") and "template_applicability_ok" not in запис]

    assert not без, (
        f"{len(без)} оценени прогона нямат `template_applicability_ok` — "
        "тоест са произведени преди флагът да съществува и техният `clean` "
        "не е по днешното определение.")
