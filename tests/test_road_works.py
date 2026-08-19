"""Работата извън траншеята е ЕДИН непрекъснат ред, както в еталона.

FAILURE означава: src/road_works.py е счупен — или обединяването мести дати
(тогава срокът се мени от нещо, което е само представяне), или губи цитати
(тогава КСС излиза непокрит върху напълно вярна работа), или оставя висящи
връзки към изчезналите задачи.

ЕТАЛОНЪТ (19.08.2026): задача UID 5653 в човешкия график — „Възстановяване на
пътна настилка извън траншеен изкоп … вкл.полагане на средни бет.бордюри и
бет.плочи" — е ЕДНА задача от 595 дни с ЕДИН предшественик, а зад нея стоят и
трите реда от лист „4. Пътна".  Ние правехме 285 задачи по участък.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.provenance import citation_units  # noqa: E402
from src.road_works import (merge_level_of_effort,  # noqa: E402
                            merged_into_level_of_effort)

ВЕРИГИ = {
    "chains": {
        "pavement_section": {
            "network": "П",
            "wbs_root": "construction",
            "label": "Възстановяване на настилка",
            "steps": [
                {"key": "base_course", "median_days": 1.0},
                {"key": "kerbs", "median_days": 2.0},
                {"key": "asphalt", "median_days": 2.0},
            ],
            "level_of_effort": {
                "steps": ["kerbs", "asphalt"],
                "name": "Възстановяване на пътна настилка извън траншеен изкоп",
                "crew": ["Пътен работник", "Валяк"],
            },
        },
        "sewer_section": {
            "network": "К",
            "wbs_root": "construction",
            "steps": [{"key": "laying", "median_days": 3.0}],
        },
    }
}


def _график() -> list[dict]:
    """Два участъка по три стъпки плюс една задача, която ги чака всичките."""
    задачи: list[dict] = []
    for номер, (начало, край) in enumerate(((10, 10), (30, 30)), start=1):
        основа = f"П{номер}"
        задачи.append({
            "id": f"{основа}_base_course", "chain_step": "base_course",
            "network": "П", "parent_id": основа, "type": "task",
            "duration": 1, "start_day": начало, "end_day": край,
            "dependencies": [],
        })
        задачи.append({
            "id": f"{основа}_kerbs", "chain_step": "kerbs", "network": "П",
            "parent_id": основа, "type": "task", "duration": 2,
            "start_day": край + 1, "end_day": край + 2,
            "chain_step_name": "Направа на бордюри",
            "alignment_id": f"Настилка — Етап {номер} от 2 [{основа}]",
            "name": f"Направа на бордюри — Бордюри С18 — Настилка — Етап {номер} от 2",
            "source_ref": "КСС!Пътна!5", "quantity": 100.0, "unit": "м",
            "dependencies": [{"predecessor_id": f"{основа}_base_course",
                              "type": "FS", "lag_days": 0}],
        })
        задачи.append({
            "id": f"{основа}_asphalt", "chain_step": "asphalt", "network": "П",
            "parent_id": основа, "type": "task", "duration": 1,
            "start_day": край + 3, "end_day": край + 3,
            "source_ref": "КСС!Пътна!4", "quantity": 250.0, "unit": "кв. м",
            "dependencies": [{"predecessor_id": f"{основа}_kerbs",
                              "type": "FS", "lag_days": 0}],
        })
    задачи.append({
        "id": "ПР_as_built", "chain_step": "as_built", "network": "ПР",
        "parent_id": "ПР", "type": "task", "duration": 5,
        "start_day": 40, "end_day": 44,
        "dependencies": [{"predecessor_id": "П1_asphalt", "type": "FS",
                          "lag_days": 0},
                         {"predecessor_id": "П2_asphalt", "type": "FS",
                          "lag_days": 0}],
    })
    return задачи


def _обединената(задачи: list[dict]) -> dict:
    само = [t for t in задачи if merged_into_level_of_effort(t)]
    assert len(само) == 1, f"очаква се една непрекъсната дейност, има {len(само)}"
    return само[0]


def test_обединената_дейност_покрива_същия_обхват():
    """Обхватът ѝ е обхватът на частите — нито ден повече."""
    нов, бележки = merge_level_of_effort(_график(), ВЕРИГИ)

    обединена = _обединената(нов)
    # Частите: бордюри 11–12 и 31–32, асфалт 13 и 33.
    assert обединена["start_day"] == 11
    assert обединена["end_day"] == 33
    assert обединена["duration"] == 23
    assert обединена["merged_task_count"] == 4
    assert обединена["merged_steps"] == ["asphalt", "kerbs"]
    assert бележки, "обединяването трябва да КАЖЕ какво е направило"


def test_работното_съдържание_не_изчезва_в_обхвата():
    """553 дни присъствие не са 553 дни работа — двете се записват отделно.

    Без това числото „продължителност" на непрекъснатата дейност е срок, а
    нормите зад него стават непроверими отвън.
    """
    нов, _ = merge_level_of_effort(_график(), ВЕРИГИ)

    обединена = _обединената(нов)
    # Бордюри 2+2 дни, асфалт 1+1 ден = 6 екипо-дни в 23 дни присъствие.
    assert обединена["merged_work_days"] == 6.0
    assert обединена["implied_crews"] == round(6 / 23, 2)
    assert "6 екипо-дни" in обединена["note"]


def test_частите_изчезват_а_другите_стъпки_остават():
    """`base_course` НЕ се пипа: в еталона основният пласт е по участък."""
    нов, _ = merge_level_of_effort(_график(), ВЕРИГИ)

    стъпки = {str(t.get("chain_step")) for t in нов}
    assert "kerbs" not in стъпки
    assert "asphalt" not in стъпки
    assert "base_course" in стъпки
    assert len([t for t in нов if t.get("chain_step") == "base_course"]) == 2


def test_количествата_не_се_губят():
    """Три реда от КСС остават три реда, със СБОРА на частите си.

    Ако сборът се загубеше, редът щеше да излезе непокрит и Σ=КСС щеше да падне
    върху работа, която е налице.
    """
    нов, _ = merge_level_of_effort(_график(), ВЕРИГИ)

    цитати = {c["ref"]: c["quantity"] for c in _обединената(нов)["source_refs"]}
    assert цитати == {"КСС!Пътна!5": 200.0, "КСС!Пътна!4": 500.0}


def test_цитатите_се_разлагат_обратно_за_описа():
    """Описът трябва да види ТРИ количества, а не едно."""
    нов, _ = merge_level_of_effort(_график(), ВЕРИГИ)

    части = citation_units(_обединената(нов))
    assert len(части) == 2
    assert {ч["source_ref"] for ч in части} == {"КСС!Пътна!4", "КСС!Пътна!5"}
    assert {ч["quantity"] for ч in части} == {200.0, 500.0}
    assert {ч["unit"] for ч in части} == {"м", "кв. м"}


def test_цитатът_чете_като_реда_а_не_като_етапа():
    """„Етап 1 от 2" е остатък от първия член и в описа заблуждава."""
    нов, _ = merge_level_of_effort(_график(), ВЕРИГИ)

    имена = {c["ref"]: str(c.get("name") or "")
             for c in _обединената(нов)["source_refs"]}
    assert имена["КСС!Пътна!5"] == "Бордюри С18"


def test_който_е_чакал_частите_чака_обединената_веднъж():
    """95 връзки към 95 задачи стават ЕДНА връзка, не 95 еднакви."""
    нов, _ = merge_level_of_effort(_график(), ВЕРИГИ)

    as_built = next(t for t in нов if t["id"] == "ПР_as_built")
    връзки = [d["predecessor_id"] for d in as_built["dependencies"]]
    assert връзки == [_обединената(нов)["id"]]


def test_обединената_има_един_предшественик_който_свършва_преди_нея():
    """Тя тръгва щом ПЪРВИЯТ участък отвори фронт, не щом свършат всички."""
    нов, _ = merge_level_of_effort(_график(), ВЕРИГИ)

    обединена = _обединената(нов)
    връзки = обединена["dependencies"]
    assert len(връзки) == 1
    предшественик = next(t for t in нов
                         if t["id"] == връзки[0]["predecessor_id"])
    assert предшественик["end_day"] < обединена["start_day"]
    assert предшественик["id"] == "П1_base_course"


def test_никоя_връзка_не_сочи_към_изчезнала_задача():
    """Висяща връзка чупи и CPM, и износа."""
    нов, _ = merge_level_of_effort(_график(), ВЕРИГИ)

    налични = {str(t["id"]) for t in нов}
    for задача in нов:
        for връзка in (задача.get("dependencies") or []):
            assert str(връзка["predecessor_id"]) in налични, (
                f"{задача['id']} сочи към несъществуваща {връзка['predecessor_id']}")


def test_изключено_обединяване_връща_задача_на_участък(monkeypatch):
    """Флагът е изход, не украса — старото поведение трябва да е достижимо."""
    monkeypatch.setenv("ROAD_WORKS_LOE", "0")
    нов, бележки = merge_level_of_effort(_график(), ВЕРИГИ)

    assert нов == _график()
    assert not бележки


def test_верига_без_обявена_непрекъсната_дейност_не_се_пипа():
    """Канализацията няма `level_of_effort` — остава каквато е."""
    задачи = [{"id": "К1_laying", "chain_step": "laying", "network": "К",
               "type": "task", "duration": 3, "start_day": 1, "end_day": 3,
               "dependencies": []},
              {"id": "К2_laying", "chain_step": "laying", "network": "К",
               "type": "task", "duration": 3, "start_day": 4, "end_day": 6,
               "dependencies": []}]
    нов, бележки = merge_level_of_effort(list(задачи), ВЕРИГИ)

    assert нов == задачи
    assert not бележки


def test_една_единствена_част_не_се_обединява():
    """Тя вече Е един ред — обединяване би било само преименуване."""
    задачи = [{"id": "П1_kerbs", "chain_step": "kerbs", "network": "П",
               "type": "task", "duration": 2, "start_day": 5, "end_day": 6,
               "dependencies": []}]
    нов, _ = merge_level_of_effort(list(задачи), ВЕРИГИ)

    assert нов == задачи


def test_непрекъснатата_дейност_не_заема_твърдо_ресурс():
    """Както надзорът: 553 дни присъствие не са 553 дни блокиран ресурс.

    Ако участваше в твърдото изравняване, тя щеше да изтласка всичко, което
    дели ресурс с нея, до края на строежа.
    """
    from src.schedule_builder import ScheduleBuilder

    задачи = [
        {"id": "П_LOE", "type": "task", "duration": 20, "start_day": 1,
         "end_day": 20, "dependencies": [], "resources": ["Валяк"],
         "crew_id": "Пътна бригада", "level_of_effort": True},
        {"id": "К1_laying", "type": "task", "duration": 3, "start_day": 1,
         "end_day": 3, "dependencies": [], "resources": ["Валяк"],
         "crew_id": "Екип К1"},
    ]
    изравнен = ScheduleBuilder().level_resources(задачи, capacity={"Валяк": 1})

    полагането = next(t for t in изравнен["schedule"] if t["id"] == "К1_laying")
    assert полагането["start_day"] == 1, (
        "непрекъснатата дейност е изтласкала работа, която не е блокирала")


def test_гейтът_за_пълнота_не_бърка_обединено_с_липсващо():
    """Стъпка, влязла в непрекъснатата дейност, е НАЛИЦЕ — просто не под пакета.

    Без това правило обединяването само по себе си сваляше `template_complete`
    и графикът излизаше „непълен" точно защото сме го подредили както еталона.
    """
    from src.schedule_diagnostics import structural_flags
    from src.work_package import (PackageItem, SpatialWorkPackage,
                                  expand_packages, load_chains)

    вериги = load_chains()
    пакети = [SpatialWorkPackage(
        id=f"П{i}", network="П", chain="pavement_section",
        name=f"Възстановяване — Етап {i}",
        items=(PackageItem(source_ref="КСС!4. Пътна!5",
                           activity_class="pavement", quantity=100.0,
                           unit="м"),))
        for i in (1, 2)]
    задачи = expand_packages(пакети, вериги).tasks
    for задача in задачи:                    # обединяването иска дати
        задача.setdefault("start_day", 1)
        задача.setdefault("end_day", 2)

    преди = structural_flags(задачи, packages=пакети, chains=вериги)
    обединени, _ = merge_level_of_effort(задачи, вериги)
    след = structural_flags(обединени, packages=пакети, chains=вериги)

    assert преди["template_complete"] is True
    assert след["template_complete"] is True, (
        "обединяването на стъпките се брои за липсващ шаблон")


def test_претоварването_се_брои_по_същото_правило():
    """Гейтът и изравняването трябва да казват едно и също."""
    from src.schedule_diagnostics import _capacity_overloads

    задачи = [
        {"id": f"П_LOE_{i}", "type": "task", "duration": 5, "start_day": 1,
         "end_day": 5, "resources": ["Валяк"], "crew_id": f"бригада {i}",
         "level_of_effort": True}
        for i in range(20)
    ]
    assert _capacity_overloads(задачи) == []
