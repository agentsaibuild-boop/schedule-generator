"""КСС НЕ Е задължителна за линеен график — стигат количества.

Проверено на 21.08.2026 с прогон през целия детерминистичен път: шест реда
„мрежа, дължина, диаметър, материал“ дават чист, валиден и експортируем
график, без нито един файл с количествено-стойностна сметка.

Разликата, която тези тестове пазят:
    КОЛИЧЕСТВА  — какво, колко, в каква мярка.  ТОВА е нужно.
    КСС         — същото плюс фасонни части, единични цени и суми.
                  Нищо от добавеното не влиза в срока.

Единственото, което КСС дава и никой друг вход не дава, е доказателство за
пълнота (проследимост 100 %) — одиторска функция, не графична.

FAILURE означава: или разпознаването на количества (src/file_manager.py ::
classify_files) пак иска именно КСС по име, или пакетният път
(src/ai_processor.py :: generate_packages) е спрял да произвежда график от
гола таблица с дължини.  И в двата случая човек с половин страница количества
пак ще бъде спрян на входа.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.file_manager import FileManager
from src.provenance import QuantityRow, SourceRef


# ---------------------------------------------------------------------------
# 1. Входът се разпознава по име, без думата „КСС“
# ---------------------------------------------------------------------------

def _classify(*names: str) -> dict:
    fm = FileManager()
    paths = [Path(f"/fake/project/{n}") for n in names]
    with patch.object(fm, "_list_supported_files", return_value=paths):
        return fm.classify_files(ai_processor=None)


@pytest.mark.parametrize("name", [
    "Дължини по диаметри.xlsx",
    "Количества водопровод.xlsx",
    "Ведомост.xlsx",
    "quantities.xlsx",
])
def test_quantity_table_is_accepted_without_the_word_kss(name):
    """Таблица с количества минава входа, макар да не се казва „КСС“.

    Преди 21.08.2026 такъв файл падаше в „непознати“, целият пакет се
    конвертираше напразно и количествата се намираха чак по съдържание.
    """
    result = _classify(name)
    assert result["can_proceed"] is True, f"{name} беше отхвърлен на входа"
    assert name in result["required"]


def test_kss_still_works():
    """Смяната не отнема нищо: КСС продължава да минава."""
    assert _classify("КСС.xlsx")["can_proceed"] is True


# ---------------------------------------------------------------------------
# 2. Шест реда дължини дават цял график
# ---------------------------------------------------------------------------

_ЛИСТ = {"В": "Водопроводна", "К": "Канализация"}


def _ред(мрежа: str, описание: str, количество: float, мярка: str, номер: int):
    return QuantityRow(
        description=описание,
        quantity=float(количество),
        unit=мярка,
        source=SourceRef(document="Дължини.xlsx", sheet=_ЛИСТ[мрежа], row=номер),
        raw={"Описание": описание, "Ед. мярка": мярка, "Количество": количество},
    )


#: Най-простата таблица, която един човек би подал — половин страница.
САМО_ДЪЛЖИНИ = [
    _ред("В", "Полагане на водопровод Ф90, PE", 600, "m", 1),
    _ред("В", "Полагане на водопровод Ф110, PE", 1800, "m", 2),
    _ред("В", "Полагане на водопровод Ф160, PE", 400, "m", 3),
    _ред("К", "Полагане на канализация Ф300, PP", 1200, "m", 1),
    _ред("К", "Полагане на канализация Ф400, PP", 800, "m", 2),
    _ред("К", "Полагане на канализация Ф600, PP", 500, "m", 3),
]


class _ПодготвенРаботник:
    """Модел, който връща наготово разпределението — пътят надолу е кодът."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.deepseek_available = True
        self.anthropic_available = False

    def chat(self, messages, system_prompt, **kwargs) -> dict:
        import json
        return {
            "content": json.dumps(self._payload, ensure_ascii=False),
            "model": "test", "usage": {"input_tokens": 0, "output_tokens": 0},
            "cost": 0.0, "fallback": False, "truncated": False,
        }


def _генерирай(boq: list) -> dict:
    sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
    from offline_dry_run import build_perfect_allocation

    from src.ai_processor import AIProcessor

    allocation = build_perfect_allocation(boq, 4)
    allocation.pop("_unroutable", None)
    ai = AIProcessor(router=_ПодготвенРаботник(allocation))
    return ai.generate_schedule_packaged(
        {"analysis": "инженеринг — проектиране и строителство"},
        boq, num_teams=2, project_path=None)


@pytest.fixture(scope="module")
def график():
    return _генерирай(САМО_ДЪЛЖИНИ)


def test_lengths_alone_produce_a_schedule(график):
    """Шест реда дължини → задачи.  Без КСС, без чертежи, без бройки."""
    tasks = ((график.get("schedule") or {}).get("tasks")
             or график.get("tasks") or [])
    assert tasks, график.get("message")
    assert len(tasks) > 50, f"само {len(tasks)} задачи — веригите не са се разгънали"


def test_lengths_alone_produce_a_clean_schedule(график):
    """Графикът минава структурните проверки, не просто съществува."""
    from src.schedule_diagnostics import is_clean, structural_flags
    from src.work_package import load_chains

    tasks = ((график.get("schedule") or {}).get("tasks")
             or график.get("tasks") or [])
    flags = structural_flags(
        tasks, packages=график.get("packages") or [], chains=load_chains(),
        boq_index=САМО_ДЪЛЖИНИ, conservation=график.get("conservation") or {},
        parse_errors=график.get("parse_errors") or [])
    паднали = [k for k, v in flags.items() if isinstance(v, bool) and not v]
    assert not паднали, f"паднали флагове без КСС: {паднали}"
    assert is_clean(flags)


def test_every_input_row_is_traceable(график):
    """Проследимостта не зависи от КСС — важи за всяка таблица с количества."""
    from src.schedule_diagnostics import structural_flags
    from src.work_package import load_chains

    tasks = ((график.get("schedule") or {}).get("tasks")
             or график.get("tasks") or [])
    flags = structural_flags(
        tasks, packages=график.get("packages") or [], chains=load_chains(),
        boq_index=САМО_ДЪЛЖИНИ, conservation=график.get("conservation") or {},
        parse_errors=график.get("parse_errors") or [])
    assert flags["source_ref_resolvable_pct"] == 100.0


def test_schedule_exports_to_ms_project(график):
    """Изходът е .xml за MS Project, не доклад — това е мярката за успех."""
    from src.export_xml import export_to_mspdi_xml

    tasks = ((график.get("schedule") or {}).get("tasks")
             or график.get("tasks") or [])
    payload = export_to_mspdi_xml(tasks, "Само дължини")
    assert payload, "експортът не върна нищо"
    текст = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    assert "DurationFormat" in текст


def test_empty_quantities_say_what_is_needed():
    """Празен вход спира — но съобщението иска количества, не КСС."""
    from src.ai_processor import AIProcessor

    ai = AIProcessor(router=_ПодготвенРаботник({"packages": []}))
    result = ai.generate_schedule_packaged(
        {"analysis": "инженеринг"}, [], num_teams=2, project_path=None)
    assert result["status"] == "error"
    съобщение = result["message"]
    assert "КОЛИЧЕСТВА" in съобщение
    assert "не е задължителна" in съобщение
