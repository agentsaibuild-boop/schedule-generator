"""Календарът идва от договора, а не от подразбирането.

FAILURE означава: src/calendar_policy.py е счупен — или договорната работна
седмица не се прилага (тогава при 5/5 срещу 7/7 всяка календарна дата в
подадения .mpp е изместена с около 40 % и никой не разбира), или гейтът спира
износ, който е бил редовен, и работата блокира без причина.

Одиторът, 31.08.2026: „Не бих допуснал production export, ако
contract_calendar_detected != schedule_calendar без изрично човешко override."

Мерилото е Свиленград: Под-Клауза 6.5 обявява „8-17 понеделник-петък", а
човешкият график е 361 работни дни в 531 календарни — тоест петдневен.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.calendar_policy import (  # noqa: E402
    ПЕТ, СЕДЕМ, check, declared_work_week, resolve)

docx = pytest.importorskip("docx", reason="python-docx липсва")


@pytest.fixture(autouse=True)
def _чиста_среда(monkeypatch):
    monkeypatch.delenv("WORK_WEEK", raising=False)


def _договор(tmp_path: Path, работно_време: str) -> Path:
    d = docx.Document()
    t = d.add_table(rows=2, cols=3)
    for j, к in enumerate(("Под-Клауза", "Данни за попълване", "Данни")):
        t.rows[0].cells[j].text = к
    for j, к in enumerate(("6.5", "установено работно време на Площадката:",
                           работно_време)):
        t.rows[1].cells[j].text = к
    d.save(str(tmp_path / "Специфични условия.docx"))
    return tmp_path


# ---------------------------------------------------------------------------
# Откъде идва календарът
# ---------------------------------------------------------------------------

def test_петдневна_седмица_от_договора(tmp_path):
    р = resolve(_договор(tmp_path, "8-17 понеделник-петък"))
    assert р["календар"] == ПЕТ
    assert "договора" in р["източник"]


def test_без_договор_остава_подразбирането(tmp_path):
    р = resolve(tmp_path)
    assert р["календар"] == СЕДЕМ
    assert р["източник"] == "подразбиране"


def test_обявеното_от_човека_надделява(tmp_path, monkeypatch):
    monkeypatch.setenv("WORK_WEEK", "7")
    р = resolve(_договор(tmp_path, "8-17 понеделник-петък"))
    assert р["календар"] == СЕДЕМ
    assert "ВНИМАНИЕ" in р["обяснение"], "разминаването трябва да се каже"


def test_неразбираема_стойност_се_пренебрегва(monkeypatch):
    monkeypatch.setenv("WORK_WEEK", "понякога")
    assert declared_work_week() == ""


# ---------------------------------------------------------------------------
# Гейтът
# ---------------------------------------------------------------------------

def test_износ_със_седемдневен_при_договор_за_пет_се_отказва(tmp_path):
    пречки = check(_договор(tmp_path, "8-17 понеделник-петък"), СЕДЕМ)
    assert пречки, "изместени с 40 % дати не бива да излизат мълчаливо"
    assert "40" in пречки[0]
    assert "WORK_WEEK" in пречки[0], "казва се и КАК се преодолява"


def test_правилният_календар_минава(tmp_path):
    assert check(_договор(tmp_path, "8-17 понеделник-петък"), ПЕТ) == []


def test_изричното_решение_на_човека_отваря_гейта(tmp_path, monkeypatch):
    monkeypatch.setenv("WORK_WEEK", "7")
    assert check(_договор(tmp_path, "8-17 понеделник-петък"), СЕДЕМ) == []


def test_без_договорна_седмица_гейтът_мълчи(tmp_path):
    """Гейт, който вика, когато не знае, спира работата без причина."""
    assert check(tmp_path, СЕДЕМ) == []
    assert check(None, СЕДЕМ) == []
