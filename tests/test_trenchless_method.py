"""Unit tests: открит изкоп или сондаж — методът идва от ТЪРГА.

ПРОБА 10.08.2026.  `config/tech_chains.json` носеше `method: "HDD"` в стъпката
за полагане на ВСЕКИ водопроводен участък.  Стойността не беше грешка при
извличането — еталонният график наистина е сондажен.  Грешката е
в обобщението: един обект стана правило за всички.

Последиците бяха две, и втората е по-лошата:

1. HDD норми има само за DN90/110/125.  Водопровод DN160 и нагоре оставаше без
   продължителност (`NO_PRODUCTIVITY_RULE`), при положение че открити норми за
   тези диаметри съществуват и се четат от същия конфиг.
2. В MS Project излизаше „стациониране на сондажната машина, направа на
   хоризонтален сондаж" и екип със сондьор и сондажна машина — описание на
   работа, каквато търгът може изобщо да не възлага.

Затова веригите са две, а изборът между тях е ДЕТЕРМИНИСТИЧЕН: чете се от
описанието на количеството.  Мълчи ли КСС, важи откритият изкоп.

FAILURE означава: графикът пак описва метод на изпълнение, който не следва от
документите — или водопровод с реални норми пак остава без продължителност.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.duration_calculator import (  # noqa: E402
    CODE_OK, calculate_task_duration)
from src.work_package import (  # noqa: E402
    PackageItem, SpatialWorkPackage, expand_packages, load_chains,
    trenchless_chain)

CHAINS = load_chains()


def _items(description: str, dn: int = 160) -> tuple[PackageItem, ...]:
    return (PackageItem(
        source_ref="КСС!Вода!1", activity_class="laying", quantity=210.0,
        unit="м", dn=dn, material="PEHD", description=description),)


def _laying_task(description: str, dn: int = 160) -> dict:
    items = _items(description, dn)
    chain = trenchless_chain("water_section", items)
    pkg = SpatialWorkPackage(id="W1", network="В", chain=chain, branch="кл. 1",
                             dn=dn, material="PEHD", items=items)
    tasks = expand_packages([pkg], CHAINS).tasks
    return next(t for t in tasks if t.get("activity_class_hint") == "laying")


# ===================================================================
# Изборът на верига
# ===================================================================

def test_a_silent_boq_means_open_cut():
    """Обичайният метод е откритият изкоп — той е и по-широко нормиран."""
    assert trenchless_chain(
        "water_section",
        _items("Доставка и полагане на тръби PEHD DN160")) == "water_section"


@pytest.mark.parametrize("description", [
    "Полагане на тръби PEHD DN160 чрез хоризонтален сондаж",
    "Безизкопно полагане на водопровод PEHD DN160",
    "Изтегляне на тръба HDD под уличното платно",
    "Полагане чрез микротунелиране",
])
def test_a_boq_that_says_drilling_gets_the_drilling_chain(description):
    assert trenchless_chain("water_section", _items(description)) == \
        "water_section_hdd"


def test_only_the_laying_row_decides():
    """Изкопът не е доказателство за метода на полагане."""
    items = (PackageItem(source_ref="КСС!Вода!2", activity_class="excavation",
                         quantity=520.0, unit="м3",
                         description="Изкоп около съществуващ сондаж"),)
    assert trenchless_chain("water_section", items) == "water_section"


def test_other_networks_are_untouched():
    """Правилото е за водопровод — канализацията няма безизкопен вариант тук."""
    assert trenchless_chain(
        "sewer_section", _items("сондаж")) == "sewer_section"


# ===================================================================
# Какво излиза в графика
# ===================================================================

def test_open_cut_water_finally_has_a_proven_duration():
    """Точно случаят, който досега падаше: DN160 има open норма, не HDD."""
    result = calculate_task_duration(
        _laying_task("Доставка и полагане на тръби PEHD DN160"))

    assert result.code == CODE_OK
    assert result.days == 15          # 210 м ÷ 14 м/ден [DN160_PE_open]


def test_drilling_without_a_verified_norm_stays_unproven():
    """Fail-closed: липсва ли норма за сондаж при този DN, не се измисля."""
    result = calculate_task_duration(
        _laying_task("Полагане на тръби PEHD DN160 чрез хоризонтален сондаж"))

    assert result.days is None


def test_the_open_chain_does_not_describe_drilling_work():
    """Описанието и екипът в готовия файл следват метода."""
    task = _laying_task("Доставка и полагане на тръби PEHD DN160")
    step_name = task["chain_step_name"]

    assert "сондаж" not in step_name.lower()
    assert not any("сонд" in str(r).lower() for r in task.get("resources") or [])


def test_the_drilling_chain_keeps_the_reference_wording():
    """Наблюдаваната верига остава дословна — тя е доказателство.

    От 19.08.2026 цикълът е РАЗДЕЛЕН на две стъпки, по описание на
    изпълнителя: „тръбите се заваряват предварително и след това само се
    полагат със сондажната машина".  Сондата е в стъпката за изтегляне;
    заваряването е своя, върви успоредно на изкопа и НЕ ползва сондажна
    машина — иначе тя би стояла заета, докато се заварява.
    """
    items = _items("Безизкопно полагане на водопровод PEHD DN160", 160)
    pkg = SpatialWorkPackage(id="W1", network="В",
                             chain=trenchless_chain("water_section", items),
                             branch="кл. 1", dn=160, material="PEHD",
                             items=items)
    tasks = expand_packages([pkg], CHAINS).tasks
    по_стъпка = {str(t.get("chain_step")): t for t in tasks}

    изтегляне = по_стъпка["laying"]
    assert "сондаж" in изтегляне["chain_step_name"].lower()
    assert any("сонд" in str(r).lower() for r in изтегляне.get("resources") or [])

    заваряване = по_стъпка["prefab_weld"]
    assert "заваряване" in заваряване["chain_step_name"].lower()
    assert not any("сонд" in str(r).lower()
                   for r in заваряване.get("resources") or [])


def test_both_water_chains_exist_and_declare_their_origin():
    """Построената стъпка не бива да се чете като наблюдавана."""
    chains = CHAINS["chains"]
    assert "water_section" in chains and "water_section_hdd" in chains

    laying = next(s for s in chains["water_section"]["steps"]
                  if s["key"] == "laying")
    assert "_provenance" in laying
    assert "method" not in laying

    drilled = next(s for s in chains["water_section_hdd"]["steps"]
                   if s["key"] == "laying")
    assert drilled["method"] == "HDD"


def test_the_model_is_not_offered_the_drilling_chain():
    """Методът е наше решение по документите, не избор на модела."""
    from src.ai_processor import _SPATIAL_CHAIN_KEYS
    assert "water_section" in _SPATIAL_CHAIN_KEYS
    assert "water_section_hdd" not in _SPATIAL_CHAIN_KEYS
