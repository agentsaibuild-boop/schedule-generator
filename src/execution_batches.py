"""Етапите на изпълнение ги прави КОДЪТ, когато геометрия няма.

ЗАЩО.  Измерено на 18.08.2026 върху 30 живи прогона на ЕДИН И СЪЩ търг:

    брой пакети, върнати от модела:
    22, 30, 32, 35, 35, 36, 36, 40, 40, 42, 47, 51, 52, 52, 55, 61, 62, 63,
    66, 70, 73, 74, 100, 132

Шесткратен разброс.  И всичките провали са на едно място:

    прогонът умира изобщо              6 от 30
    счупен JSON                        7 от 24
    Σ ≠ КСС                            8 от 24
    roll-up, надзор, ресурси, цитати   0 от 24

Нито един структурен инвариант надолу по веригата не пада.  Чупи се само
получаването на използваем отговор от модела.

А отговорът, който искахме от него, е невъзможен по информация, не по промпт:

  * КСС НЕ съдържа разчленяване — 0 от 28 реда с количество носят
    идентификатор на участък; всеки ред е насипно количество за целия квартал;
  * класификацията, за която моделът уж трябваше, кодът я прави сам:
    `_coverer_class` рутира 28 от 28 реда — 100%;
  * без DWG/DXF, GIS или таблица с участъци възлите не могат да бъдат
    твърдени (виж `spatial_source`).

Тоест молехме модела да СЪЧИНИ данни, които ги няма във входа, и после
цялата система поправяше последствията.  Тук това спира: без авторитетна
геометрия участъците са ЕТАПИ НА ИЗПЪЛНЕНИЕ — организационно решение, което
кодът има право да вземе и да обяви — а не физически трасета.

Σ = КСС престава да бъде гейт, който се надяваме да мине: количествата се
разделят тук, тоест нарушаването им е невъзможно по конструкция.

КАКВО ТОВА НЕ Е.  Не е заместител на `PhysicalSegment`.  Щом се появи
DWG/DXF, GIS слой или таблица с участъци, участъците идват ОТТАМ и този модул
не се вика.  Дотогава етапите се именуват като етапи и никой не ги показва за
трасета — за това се грижи `number_execution_batches`.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Iterable

logger = logging.getLogger(__name__)

#: Клас на реда → веригата, която ГАРАНТИРАНО го покрива, независимо от листа.
#: Настилките и кабелите живеят в собствени пакети и в еталонния график.
_CLASS_CHAIN = {"pavement": "pavement_section", "cable": "cable_section"}

_NETWORK_CHAIN = {"В": "water_section", "К": "sewer_section",
                  "П": "pavement_section", "ЕЛ": "cable_section"}

_CHAIN_NETWORK = {v: k for k, v in _NETWORK_CHAIN.items()}

#: Кой лист на КСС е коя мрежа.
_SHEET_NETWORK = (
    ("vodoprovod", "В"),
    ("водопровод", "В"),
    ("kanaliz", "К"),
    ("канализац", "К"),
    ("пътни", "П"),
    ("patni", "П"),
    ("пътна", "П"),
    ("ел и тт", "ЕЛ"),
)

#: Колко етапа на верига.  ИЗМЕРЕНО, не избрано: детерминистичният прогон дава
#: 897 дни при 8 етапа и 956 при 14 — по-финото делене влошава срока, защото
#: всеки етап носи наново неделимите стъпки.  Мени се през средата, защото е
#: организационно решение, не природен закон.
DEFAULT_BATCHES = int(os.getenv("EXECUTION_BATCHES", "8") or 8)


def _sheet_of(ref: str) -> str:
    parts = str(ref).split("!")
    return parts[1] if len(parts) >= 3 else ""


def _network_of(ref: str) -> str:
    sheet = _sheet_of(ref).lower()
    for needle, network in _SHEET_NETWORK:
        if needle in sheet:
            return network
    return ""


def split_exactly(total: float, parts: int) -> list[float]:
    """Раздели на `parts` дяла, чийто сбор е ТОЧНО `total`.

    Последният дял поема остатъка от закръглянето.  Без това самото
    разделяне внася дрейфа, заради който Σ ≠ КСС падаше в 8 от 24 прогона.
    """
    if parts <= 1:
        return [total]
    share = round(total / parts, 6)
    head = [share] * (parts - 1)
    return head + [round(total - share * (parts - 1), 6)]


def allocate_execution_batches(
    boq_index: Iterable[Any],
    batches_per_chain: int = 0,
) -> dict[str, Any]:
    """Разпределя КСС на етапи по вериги — детерминистично и без модел.

    Returns:
        {"packages": [...], "unroutable": [...], "notes": [...]} — `packages`
        е СЪЩИЯТ вид, който `packages_from_ai` очаква, за да мине през
        всичките му проверки (клас от описанието, конфликт на диаметри,
        запазване на количествата).
    """
    брой = int(batches_per_chain or DEFAULT_BATCHES)
    брой = max(1, брой)

    from src.provenance import _coverer_class, is_duration_row

    кофи: dict[str, list[Any]] = {}
    неразпределими: list[str] = []
    продължителности: list[str] = []

    for row in boq_index or []:
        quantity = getattr(row, "quantity", None)
        if not isinstance(quantity, (int, float)) or isinstance(quantity, bool):
            continue                      # заглавия и „ОБЩО" — нямат количество
        if is_duration_row(row):
            # „ПРОЕКТИРАНЕ 120 Календарни Дни" не е работа за разпределяне, а
            # обявен срок на договорна фаза.  Не влиза в участъците.
            продължителности.append(str(row.ref))
            continue
        клас = _coverer_class(row)
        if not клас:
            неразпределими.append(str(row.ref))
            continue
        верига = _CLASS_CHAIN.get(клас) or _NETWORK_CHAIN.get(
            _network_of(row.ref), "")
        if not верига:
            неразпределими.append(str(row.ref))
            continue
        кофи.setdefault(верига, []).append(row)

    packages: list[dict] = []
    for верига, редове in sorted(кофи.items()):
        мрежа = _CHAIN_NETWORK[верига]
        етапи: list[dict] = [
            {
                "id": f"{мрежа}{i + 1}",
                # ИМЕТО КАЗВА КАКВО Е.  Никакви „от РШ 1 до РШ 2": това е етап
                # на изпълнение, не трасе, и не бива да изглежда като прочетена
                # геометрия в MS Project.
                "name": f"Етап {i + 1} от {брой}",
                "network": мрежа,
                "chain": верига,
                "items": [],
            }
            for i in range(брой)
        ]
        for row in редове:
            for pkg, дял in zip(етапи, split_exactly(float(row.quantity), брой)):
                if дял <= 0:
                    continue
                pkg["items"].append(
                    {"source_ref": str(row.ref), "quantity": дял})
        packages.extend(p for p in етапи if p["items"])

    бележки = [
        f"Участъците са направени от кода: {len(packages)} етапа на изпълнение "
        f"по {брой} на верига, количествата разделени точно по КСС."
    ]
    if продължителности:
        бележки.append(
            f"{len(продължителности)} реда обявяват договорна ПРОДЪЛЖИТЕЛНОСТ, "
            "не количество — не се разпределят по участъци.")
    if неразпределими:
        бележки.append(
            f"{len(неразпределими)} реда не попадат в нито една верига по "
            f"описание и лист: {', '.join(неразпределими[:5])}"
            + (" …" if len(неразпределими) > 5 else ""))
    for бележка in бележки:
        logger.info("%s", бележка)

    return {"packages": packages, "unroutable": неразпределими,
            "durations": продължителности, "notes": бележки}
