"""Двата измерени срока остават проверими и без данните на клиента.

FAILURE означава: конвейерът е сменил резултата си на входове, които не са се
променили.  Това е регресия, дори да е „по-добър" график — числата в
архитектурния доклад стъпват на тези два прогона.

ЗАЩО СЪЩЕСТВУВА ТОЗИ ТЕСТ.  До 01.09.2026 всички измервания се правеха върху
реални тръжни папки на десктопа.  Те бяха изтрити и с тях изчезна възможността
да се провери каквото и да е твърдение — включително „255 дни, чист", на
което стъпва целият архитектурен доклад.

Затова тук стоят ОБЕЗЛИЧЕНИ входове: същите количества, същият брой и същите
дължини на отсечките, но без имена на обекти, улици и възли.  Проверено:
`grep` за имената на клиента връща нула.

    обект А — разпределителна мрежа   15 846 м, 107 отсечки → 255 дни (45+210)
    обект Б — единично трасе           6 500 м, 13 отсечки  → 620 дни (120+500)

Имената на обектите нарочно не се срещат в този файл — виж последния тест.

ОБХВАТ НА ТЕЗИ ТЕСТОВЕ: те пазят СРОКА и обема на графика.  Структурните
флагове се смятат с повече контекст, отколкото тук се сглобява (вериги, индекс
на КСС, разпределение) — те се проверяват с `tools/offline_dry_run.py`, който
на същите тези две папки дава `ЧИСТ: True` и за двата обекта.  По-добре тест,
който пази точно каквото твърди, отколкото тест, който проверява наполовина.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

ФИКСТУРИ = Path(__file__).parent / "fixtures" / "регресия"


def _прогон(папка: str, среда: dict) -> dict:
    """Целият детерминистичен път, както го кара `offline_dry_run`."""
    from src.ai_processor import AIProcessor
    from src.provenance import build_quantity_index
    from src.schedule_diagnostics import duration_report, is_clean, structural_flags
    from src.work_package import load_chains
    from tools.offline_dry_run import _ScriptedRouter, build_perfect_allocation

    # `converted/` се прави В ДВИЖЕНИЕ.  В хранилището такава папка не влиза:
    # pre-commit hook-ът я забранява по подразбиране, защото там обикновено
    # стоят конвертираните документи на клиента.  Правилото е добро и не му
    # правим изключение — фикстурата държи само количествата.
    import shutil
    import tempfile

    източник = ФИКСТУРИ / папка
    проект = Path(tempfile.mkdtemp()) / папка
    (проект / "converted").mkdir(parents=True)
    shutil.copy2(източник / "количества.json",
                 проект / "converted" / "количества.json")
    shutil.copy2(източник / "отсечки.json", проект / "отсечки.json")

    старо = {к: os.environ.get(к) for к in среда}
    os.environ.update(среда)
    try:
        boq = build_quantity_index(проект)
        assert boq, f"{папка}: няма индексируем КСС"
        разпределение = build_perfect_allocation(boq, 8)
        разпределение.pop("_unroutable", None)
        отсечки = __import__("json").loads(
            (проект / "отсечки.json").read_text(encoding="utf-8"))
        ai = AIProcessor(router=_ScriptedRouter(разпределение))
        резултат = ai.generate_schedule_packaged(
            {"analysis": "инженеринг — проектиране и строителство"}, boq,
            num_teams=2, segments=отсечки, project_path=проект)
        задачи = ((резултат.get("schedule") or {}).get("tasks")
                  or резултат.get("tasks") or [])
        флагове = structural_flags(задачи, chains=load_chains())
        return {"дни": duration_report(задачи)["total_days"],
                "чист": is_clean(флагове), "задачи": len(задачи),
                "флагове": флагове}
    finally:
        for к, v in старо.items():
            if v is None:
                os.environ.pop(к, None)
            else:
                os.environ[к] = v


@pytest.mark.slow
def test_обект_А_разпределителна_мрежа():
    """15 846 м в 107 отсечки, 13 екипа, обявени 45 + 210 дни."""
    р = _прогон("обект-А-мрежа", {
        "NETWORK_ORDER": "К", "TEAMS_PARALLEL": "1",
        "DESIGN_DAYS": "45", "CONSTRUCTION_DAYS": "210",
        "CREWS": "water_section:10,sewer_section:3"})
    assert р["дни"] == 255, "обявените 45+210 дни трябва да се спазват докрай"
    assert р["задачи"] > 900, "мрежата дава над 900 задачи"


@pytest.mark.slow
def test_обект_Б_единично_трасе():
    """6 500 м в 13 отсечки, едно трасе, обявени 120 + 500 дни."""
    р = _прогон("обект-Б-трасе", {
        "SINGLE_ROUTE": "В", "DESIGN_DAYS": "120",
        "CONSTRUCTION_DAYS": "500", "CREWS": "water_section:3"})
    assert р["дни"] == 620
    assert р["задачи"] > 100


def test_фикстурите_не_носят_имена_на_клиента():
    """Гейтът, който прави тези входове годни да стоят в хранилището.

    Имената НЕ се изписват тук.  Веднъж вече се хванахме на това: маркерът
    стоеше на седем места, включително в самия denylist и в теста към него.
    Списъкът се чете от `config/client_names.local.json` (в .gitignore) или от
    `CLIENT_NAMES`; без него тестът се ПРОПУСКА с ясна причина, вместо да мине
    на празен списък и да изглежда като проверка.
    """
    from tools.security_scan import load_terms

    denylist = Path(__file__).parent.parent / "config" / "client_names.local.json"
    ако_среда = os.getenv("CLIENT_NAMES", "")
    if ако_среда:
        имена = [и.strip() for и in ако_среда.split(",") if и.strip()]
    elif denylist.exists():
        имена = load_terms(denylist)
    else:
        pytest.skip("няма denylist — създай config/client_names.local.json "
                    "или задай CLIENT_NAMES")
    assert имена, "празен denylist не е проверка"

    for път in ФИКСТУРИ.rglob("*.json"):
        текст = път.read_text(encoding="utf-8").lower()
        за = [и for и in имена if и.lower() in текст]
        assert not за, f"{път.name} носи {len(за)} забранени термина"
