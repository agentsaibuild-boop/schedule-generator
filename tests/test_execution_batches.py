"""Етапите ги прави кодът, когато геометрия няма — и Σ = КСС по конструкция.

FAILURE означава: или количествата пак могат да не се сберат до КСС, или
моделът пак бива питан да съчини разчленяване, което го няма във входа.

ИЗМЕРЕНО 18.08.2026, 30 живи прогона на ЕДИН И СЪЩ търг: моделът връща между
22 и 132 пакета, а всичките 21 провала са в получаването на използваем
отговор — 6 мъртви прогона, 7 счупени JSON-а, 8 пъти Σ ≠ КСС.  Нито един
структурен инвариант надолу по веригата не пада.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.execution_batches import (  # noqa: E402
    allocate_execution_batches, split_exactly)


class _Ред:
    def __init__(self, ref, quantity, description, unit="m"):
        self.ref = ref
        self.quantity = quantity
        self.description = description
        self.unit = unit


def _ксс() -> list[_Ред]:
    return [
        _Ред("КСС!Kanalizaciya!3", 1182.0, "Изграждане на смесена канализационна мрежа"),
        _Ред("КСС!Kanalizaciya!4", 260.0, "Изграждане на смесена канализационна мрежа"),
        _Ред("КСС!Vodoprovod!5", 538.12, "Реконструкция на разпределителната мрежа"),
        _Ред("КСС!Vodoprovod!6", 174.0, "СВО", unit="брой"),
        _Ред("КСС!Пътна!7", 10824.0, "възстановяване на пътна настилка", unit="кв. м"),
        _Ред("КСС!ЕЛ и ТТ!8", 500.0, "Подземни ТТ кабели"),
        # Заглавен ред без количество — не бива да ражда работа.
        _Ред("КСС!Kanalizaciya!1", None, "ОБЩО"),
    ]


def _сборове(пакети) -> dict[str, float]:
    сбор: dict[str, float] = defaultdict(float)
    for p in пакети:
        for item in p["items"]:
            сбор[item["source_ref"]] += item["quantity"]
    return сбор


# ---------------------------------------------------------------------------
# Същината: количествата не могат да се загубят
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("етапи", [1, 2, 8, 14, 50])
def test_the_sum_is_exactly_the_boq(етапи):
    """Σ = КСС престава да е гейт, който се надяваме да мине."""
    редове = _ксс()
    сбор = _сборове(allocate_execution_batches(редове, етапи)["packages"])

    for ред in редове:
        if ред.quantity is None:
            assert str(ред.ref) not in сбор, "заглавен ред роди работа"
            continue
        assert сбор[str(ред.ref)] == pytest.approx(ред.quantity, abs=1e-6), (
            f"{ред.ref}: разпределено {сбор[str(ред.ref)]} срещу "
            f"{ред.quantity} в КСС")


@pytest.mark.parametrize("общо,части", [(1000.0, 3), (1182.0, 8), (0.1, 7),
                                        (874.55, 13)])
def test_splitting_never_drifts(общо, части):
    дялове = split_exactly(общо, части)

    assert len(дялове) == части
    assert sum(дялове) == pytest.approx(общо, abs=1e-6)


# ---------------------------------------------------------------------------
# Разчленяването е повторяемо, за разлика от модела
# ---------------------------------------------------------------------------


def test_the_same_input_gives_the_same_packages():
    """22–132 пакета за един и същ вход беше диагнозата.  Тук е 0 разброс."""
    първо = allocate_execution_batches(_ксс(), 8)["packages"]
    второ = allocate_execution_batches(_ксс(), 8)["packages"]

    assert [p["id"] for p in първо] == [p["id"] for p in второ]
    assert първо == второ


def test_every_row_with_a_quantity_is_routed():
    """`_coverer_class` рутира 28 от 28 реда на истинския търг — 100%."""
    резултат = allocate_execution_batches(_ксс(), 8)

    assert резултат["unroutable"] == []


def test_each_network_gets_its_own_chain():
    пакети = allocate_execution_batches(_ксс(), 4)["packages"]
    вериги = {p["chain"] for p in пакети}

    assert вериги == {"sewer_section", "water_section",
                      "pavement_section", "cable_section"}


# ---------------------------------------------------------------------------
# Имената не се преструват на геометрия
# ---------------------------------------------------------------------------


def test_names_do_not_claim_node_to_node_geometry():
    """„кл. 1 от РШ 1 до РШ 2" без чертеж е съчинено — тук такова не се ражда."""
    пакети = allocate_execution_batches(_ксс(), 8)["packages"]

    for p in пакети:
        assert "РШ" not in p["name"] and "КШ" not in p["name"]
        # Всяко име казва, че е ЕТАП НА ИЗПЪЛНЕНИЕ.  Настилките носят и
        # трасето, което възстановяват („Възстановяване на настилката —
        # Етап 3 от 8"), затова проверката е за наличие, не за начало.
        assert "Етап " in p["name"], p["name"]


def test_a_row_without_a_quantity_is_skipped():
    редове = [_Ред("КСС!Kanalizaciya!1", None, "ОБЩО")]

    assert allocate_execution_batches(редове, 8)["packages"] == []


def test_it_says_what_it_did():
    """Решението на кода трябва да се вижда, не да се подразбира."""
    бележки = allocate_execution_batches(_ксс(), 8)["notes"]

    assert any("направени от кода" in b for b in бележки)


# ---------------------------------------------------------------------------
# Настилките следват трасето
# ---------------------------------------------------------------------------


def test_pavement_gets_one_package_per_route_section():
    """Човекът възстановява след всеки участък, не накрая.

    Сравнение с еталона (19.08.2026): той прави настилки от ден 131 до 774 —
    през целия строеж, 70 задачи.  Ние ги трупахме в 514→712 и точно те
    определяха края на обекта.  Количествата са общообектови („Пътна —
    възстановяване на настилка", 10824 кв.м за квартала), затова ставаха свой
    пакет; човекът ги пише по участъци.
    """
    пакети = allocate_execution_batches(_ксс(), 8)["packages"]

    трасови = [p for p in пакети
               if p["chain"] in ("sewer_section", "water_section",
                                 "cable_section")]
    настилки = [p for p in пакети if p["chain"] == "pavement_section"]

    assert len(настилки) == len(трасови), (
        "настилките не са по един пакет на участък")
    for настилка, трасе in zip(настилки, трасови):
        assert трасе["name"] in настилка["name"], (
            f"{настилка['name']!r} не сочи своя участък")


def test_pavement_quantities_still_sum_to_the_boq():
    """Разпределянето по участъци не бива да губи или ражда количество."""
    редове = _ксс()
    сбор = {}
    for p in allocate_execution_batches(редове, 8)["packages"]:
        for i in p["items"]:
            сбор[i["source_ref"]] = сбор.get(i["source_ref"], 0.0) + i["quantity"]

    настилки = [r for r in редове if "настилка" in r.description.lower()]
    assert настилки
    for ред in настилки:
        assert сбор[str(ред.ref)] == pytest.approx(ред.quantity, abs=1e-6)


def test_it_says_the_pavement_follows_the_route():
    бележки = allocate_execution_batches(_ксс(), 8)["notes"]

    assert any("следват трасето" in b for b in бележки)


# ---------------------------------------------------------------------------
# Когато чертежът е прочетен, участъците са ТЕ — не равни етапи
# ---------------------------------------------------------------------------
#
# Равните етапи са компромис за липсваща геометрия, не предпочитание.  Върху
# реалния търг ситуацията дава 46 канализационни участъка — точно колкото има
# и еталонният човешки график — вместо 10 етапа „по 8 на верига".

def _тръбни() -> list[_Ред]:
    return [
        _Ред("КСС!Kanalizaciya!3", 300.0,
             "Изграждане на канализационни клонове Ф300 РР"),
        _Ред("КСС!Kanalizaciya!4", 12.0, "Ревизионна шахта Ф300", unit="брой"),
    ]


def _отсечки() -> list[dict]:
    return [
        {"network": "К", "branch": "Кл.48", "street": "ул.Грозден",
         "dn": 300, "length_m": 200.0, "in_scope": True},
        {"network": "К", "branch": "Кл.45", "street": "ул.Комета",
         "dn": 300, "length_m": 100.0, "in_scope": True},
        {"network": "К", "branch": "Кл.73", "street": "ул.Лале",
         "dn": 300, "length_m": 900.0, "in_scope": False},   # следващ етап
    ]


def _тръбни_пакети(резултат) -> list[dict]:
    return [p for p in резултат["packages"] if p.get("branch")]


def test_drawn_segments_replace_invented_stages():
    без = allocate_execution_batches(_тръбни(), 8)
    със = allocate_execution_batches(_тръбни(), 8, segments=_отсечки())

    assert not _тръбни_пакети(без), "без чертеж не бива да има имена на клонове"
    assert len(_тръбни_пакети(със)) == 2


def test_package_name_comes_from_the_drawing():
    пакети = _тръбни_пакети(
        allocate_execution_batches(_тръбни(), 8, segments=_отсечки()))

    assert {p["name"] for p in пакети} == {
        "Кл.48 по ул.Грозден", "Кл.45 по ул.Комета"}


def test_quantities_follow_the_measured_lengths():
    """По-дългата отсечка носи повече работа — 200 м срещу 100 м е 2:1."""
    пакети = {p["branch"]: p for p in _тръбни_пакети(
        allocate_execution_batches(_тръбни(), 8, segments=_отсечки()))}

    дълга = sum(i["quantity"] for i in пакети["Кл.48"]["items"]
                if i["source_ref"] == "КСС!Kanalizaciya!3")
    къса = sum(i["quantity"] for i in пакети["Кл.45"]["items"]
               if i["source_ref"] == "КСС!Kanalizaciya!3")

    assert дълга == pytest.approx(200.0)
    assert къса == pytest.approx(100.0)


def test_sum_still_equals_the_boq():
    """Разчленяването по чертеж не бива да губи или ражда количество."""
    редове = _тръбни()
    сбор: dict[str, float] = defaultdict(float)
    for p in allocate_execution_batches(редове, 8, segments=_отсечки())["packages"]:
        for i in p["items"]:
            сбор[i["source_ref"]] += i["quantity"]

    for ред in редове:
        assert сбор[str(ред.ref)] == pytest.approx(ред.quantity, abs=1e-6)


def test_out_of_scope_segments_do_not_become_packages():
    """Кл.73 е „следващ етап" — не бива да влиза в срока на тази процедура."""
    имена = {p["branch"] for p in _тръбни_пакети(
        allocate_execution_batches(_тръбни(), 8, segments=_отсечки()))}

    assert "Кл.73" not in имена


def test_counts_are_not_split_below_one():
    """Дванайсет шахти на две отсечки са 6 и 6, не дванайсет по нищо."""
    пакети = _тръбни_пакети(
        allocate_execution_batches(_тръбни(), 8, segments=_отсечки()))
    шахти = [i["quantity"] for p in пакети for i in p["items"]
             if i["source_ref"] == "КСС!Kanalizaciya!4"]

    assert шахти
    assert all(q >= 1 for q in шахти)
    assert sum(шахти) == pytest.approx(12.0)


def test_it_says_which_packages_came_from_the_drawing():
    """Разликата между догадка и документ трябва да се вижда в бележките."""
    бележки = allocate_execution_batches(
        _тръбни(), 8, segments=_отсечки())["notes"]

    assert any("от чертежа" in b for b in бележки)


def test_segments_of_another_network_are_ignored():
    """Водопроводна отсечка не бива да разчленява канализационен ред."""
    чужди = [{"network": "В", "branch": "ГЛ.КЛ. I", "street": "",
              "dn": 300, "length_m": 500.0, "in_scope": True}]

    assert not _тръбни_пакети(
        allocate_execution_batches(_тръбни(), 8, segments=чужди))


# ---------------------------------------------------------------------------
# Чертежът е ПРОЕКТЪТ, търгът е етап от него
# ---------------------------------------------------------------------------
#
# Измерено 21.08.2026: за водопровода Ф110 чертежът показва 5327 м, а
# спецификацията купува 1760 м.  Ако всичките отсечки станат участъци,
# договорните метри се размазват на 53 парчета по 33 м — количеството е вярно,
# но работата е раздробена и срокът се раздува с неделимите стъпки.


def _един_ред_110() -> list[_Ред]:
    return [_Ред("СПЕЦИФИКАЦИЯ.docx!таблица 1 · Водопроводна мрежа!22", 300.0,
                 "Реконструкция на разпределителната мрежа с Ф 110 РЕ")]


def _много_отсечки() -> list[dict]:
    """Пет по 100 м — начертани 500, а купени 300."""
    return [{"network": "В", "branch": f"КЛ.{i}-И", "street": "",
             "dn": 110, "length_m": 100.0, "in_scope": True} for i in range(1, 6)]


def test_only_as_many_sections_as_the_contract_pays_for():
    """300 м при отсечки по 100 м са ТРИ участъка, не пет по 60."""
    пакети = _тръбни_пакети(
        allocate_execution_batches(_един_ред_110(), 8, segments=_много_отсечки()))

    assert len(пакети) == 3


def test_sections_keep_their_real_size():
    """Участъкът остава с истинската си дължина; броят следва договора."""
    пакети = _тръбни_пакети(
        allocate_execution_batches(_един_ред_110(), 8, segments=_много_отсечки()))
    количества = [sum(i["quantity"] for i in p["items"]) for p in пакети]

    assert all(q == pytest.approx(100.0) for q in количества)
    assert sum(количества) == pytest.approx(300.0)


def test_the_trim_is_announced():
    """Произволният избор се КАЗВА — иначе минава за прочетен от чертежа."""
    бележки = allocate_execution_batches(
        _един_ред_110(), 8, segments=_много_отсечки())["notes"]

    assert any("документите не казват кои клонове" in b for b in бележки)


def test_no_trim_when_the_drawing_matches_the_contract():
    """Когато начертаното е колкото купеното, не се реже нищо."""
    отсечки = _много_отсечки()[:3]
    бележки = allocate_execution_batches(
        _един_ред_110(), 8, segments=отсечки)["notes"]

    assert not any("не казват кои клонове" in b for b in бележки)
    assert len(_тръбни_пакети(
        allocate_execution_batches(_един_ред_110(), 8, segments=отсечки))) == 3


# ---------------------------------------------------------------------------
# Мрежата не винаги е в името на ЛИСТА
# ---------------------------------------------------------------------------
#
# Проба 24.08.2026, търг „Община Казанлък": файлът се казва „КСС ВОДОПРОВОД
# Енина - първи етап.xlsx", а листът вътре — просто „КСС първи етап".  Без
# прочит на името на файла 1311 от 1647 реда с количество оставаха без верига
# и генерацията спираше, преди да излезе график.


def test_network_is_read_from_the_file_name_too():
    from src.execution_batches import _network_of

    ref = "КСС водопровод Енина - първи етап.xlsx!КСС първи етап!19"

    assert _network_of(ref) == "В"


def test_the_sheet_still_wins_over_the_file():
    """При КСС на няколко листа листът е по-точният източник."""
    from src.execution_batches import _network_of

    ref = "КСС водопровод и канализация.xlsx!3. Chast Kanalizacia!19"

    assert _network_of(ref) == "К"


def test_a_row_named_only_in_the_file_still_gets_packages():
    редове = [_Ред("КСС водопровод Енина.xlsx!КСС първи етап!19", 500.0,
                   "Изкоп с багер в земни почви", unit="m3")]

    резултат = allocate_execution_batches(редове, 8)

    assert резултат["packages"], "редът пак остава без верига"
    assert not резултат["unroutable"]


def test_network_is_read_from_the_row_itself_last():
    """Когато и листът, и файлът мълчат, редът сам си казва мрежата.

    Проба 24.08.2026, търг „ВиК Хасково — Харманли довеждащ": цялата
    спецификация е ЕДИН ред „Главни водопроводни клонове DN300 CI — 673,09 m"
    в таблица без раздели.  Нито името на листа („таблица 1"), нито името на
    файла казват мрежата — а редът я пише.  Без този прочит генерацията
    спираше при напълно четим вход.
    """
    редове = [_Ред("ТЕХНИЧЕСКА СПЕЦИФИКАЦИЯ ОП3.docx!таблица 1!2", 673.09,
                   "Главни водопроводни клонове DN300 CI")]

    резултат = allocate_execution_batches(редове, 8)

    assert резултат["packages"]
    assert not резултат["unroutable"]
    assert {p["network"] for p in резултат["packages"]} == {"В"}


def test_the_container_still_wins_over_the_description():
    """Ред „възстановяване на настилка над водопровода" е ПЪТНА работа.

    Описанието е последният източник нарочно — то е най-шумното.
    """
    from src.execution_batches import _network_of_row

    class _Р:
        ref = "КСС.xlsx!4. Пътна!7"
        description = "Възстановяване на настилка над водопровода"

    assert _network_of_row(_Р()) == "П"
