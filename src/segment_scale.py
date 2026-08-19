"""Овърхедът на веригата е ЕДИН, независимо на колко пакета сме я разделили.

ЗАЩО.  Одит 07.08.2026, P1: „`PhysicalSegment` ≠ `ExecutionBatch` — разделянето
на участък дублира фиксираните стъпки".  Измерено на 18.08.2026 (детерминистичен
прогон, 8 срещу 14 участъка на верига, +652 задача-дни):

    calculated, но непропорционално          +268
    chain_template (медиана на пакет)        +252   ← ТУК
    авторски надзор (LOE, следствие)         +120
    под от 1 ден                              +12

Стъпките без `covers` (`survey`, `cctv`), които брийфът сочи като причина, дават
+24 от 652.  Истинският механизъм е друг: технологичната верига има
ЗАДЪЛЖИТЕЛНИ стъпки, за които КСС няма отделен ред — изкопът, изпитването за
непропускливост и дезинфекцията са вътре в тръбния ред на търга.  Такава стъпка
не може да бъде сметната от нормите и запазва `median_days` от еталона.
Медианата обаче е наблюдавана върху ЕДИН еталонен участък, а нашият пакет
събира по няколко от тях.  Затова:

  * участък от 1182 м и участък от 74 м получаваха еднакви 4 дни дезинфекция;
  * разделянето на един пакет на два УДВОЯВАШЕ овърхеда вместо да го запази.

КАК.  Анкерът е самият еталон, не пакетът.  Еталонният човешки график съдържа
46 канализационни и 23 водопроводни участъка (`observed_count`), тоест общата
работа по стъпка `k` за цялата верига е:

    еталон(k) = median_days(k) × observed_count

Това число НЕ зависи от нашето разделяне — то е това, което човекът реално е
записал.  Разпределя се между нашите пакети по дела им от ДОКАЗАНАТА работа
(продължителностите, сметнати от нормите), а където такава няма — поравно.

СЛЕДСТВИЕ, което е и проверката: 8 пакета и 14 пакета получават един и същи
общ овърхед.  `PhysicalSegment` и `ExecutionBatch` престават да са едно и също
нещо по срок, без да се въвежда втора йерархия.

ВНИМАНИЕ, което не бива да се загуби: това вдига срока, а не го сваля.
Досегашният модел е ПОДЦЕНЯВАЛ работата — начислявал е продължителност за
еталонен участък на пакет, който събира по пет-шест такива.

ЗНАЙНА ГРАНИЦА: `water_section` и `water_section_hdd` носят ЕДИН И СЪЩИ
`observed_count = 23` — това са същите 23 наблюдавани участъка, разгледани
веднъж като открит изкоп и веднъж като сондаж.  Проект, който ползва и двете
вериги, ще получи анкера два пъти.  Днес такъв няма; когато се появи, числото
трябва да се раздели между тях, не да се удвои.

Пипа САМО вериги с `wbs_root == "construction"` И с `observed_count > 0`.
Проектирането, мобилизацията, приемането и надзорът имат продължителности от
договора — там няма какво да се мащабира.  `pavement_section` и
`cable_section` са с `observed_count = 0` (не са извлечени от еталона) и
остават на шаблона — по-честно е от анкер, изведен от нищо.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Iterable

logger = logging.getLogger(__name__)

#: Само тук участък има размер, който значи нещо за срока.
_SCALED_ROOT = "construction"

#: Произходът остава `chain_template`: числото пак НЕ идва от нормите.
_TEMPLATE = "chain_template"


def _num(value: Any) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) else 0.0


def _attr(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _apportion(общо: int, тегла: list[float]) -> list[int]:
    """Цели дни по НАЙ-ГОЛЯМ ОСТАТЪК: сборът е точно `общо`, всеки поне 1.

    Закръглянето нагоре на всеки пакет поотделно връща същия дефект, който
    поправяме: при 46 дни еталон 8 пакета дават 48, а 14 дават 56 — тоест
    разделянето пак ражда работа.  Тук остатъците се събират и допълват
    сбора, вместо всеки да плаща цял ден нагоре.

    Подът от един ден е по-силен от сбора: стъпка, която се извършва, не може
    да трае нула.  Когато пакетите са повече от дните, сборът излиза равен на
    броя им — и това се вижда, вместо да се крие.
    """
    n = len(тегла)
    if n == 0:
        return []
    сума = sum(тегла)
    ако = [(общо * w / сума) if сума > 0 else (общо / n) for w in тегла]
    цели = [max(1, int(math.floor(x))) for x in ако]
    остава = общо - sum(цели)
    if остава > 0:
        ред = sorted(range(n), key=lambda i: ако[i] - math.floor(ако[i]),
                     reverse=True)
        for i in range(остава):
            цели[ред[i % n]] += 1
    return цели


def scale_segment_overhead(
    tasks: list[dict],
    packages: Iterable[Any],
    chains: dict,
) -> tuple[list[dict], list[str]]:
    """Разпределя овърхеда на веригата между пакетите ѝ.

    Връща (задачи, бележки).  Всяка пипната задача носи `segment_share` и
    `template_days`, за да е четимо откъде идва числото и обратимо, ако се
    окаже грешно.
    """
    chain_defs = (chains or {}).get("chains", chains) or {}

    chain_of: dict[str, str] = {}
    for pkg in packages or []:
        pid = _attr(pkg, "id")
        if pid:
            chain_of[str(pid)] = str(_attr(pkg, "chain") or "")

    # Листата по пакет; пакетите по верига.
    листа_на: dict[str, list[dict]] = {}
    for task in tasks or []:
        if task.get("chain_step"):
            листа_на.setdefault(str(task.get("parent_id") or ""), []).append(task)

    пакети_на_верига: dict[str, list[str]] = {}
    for pkg_id in листа_на:
        верига = chain_of.get(pkg_id, "")
        if верига:
            пакети_на_верига.setdefault(верига, []).append(pkg_id)

    пипнати = 0
    без_анкер: set[str] = set()
    бележки: list[str] = []

    for верига, pkg_ids in пакети_на_верига.items():
        chain = chain_defs.get(верига)
        if not isinstance(chain, dict):
            continue
        if str(chain.get("wbs_root") or "") != _SCALED_ROOT:
            continue
        наблюдавани = int(_num(chain.get("observed_count")))
        steps = {str(s.get("key")): s for s in (chain.get("steps") or [])}
        if наблюдавани <= 0:
            if any(str(t.get("duration_source") or "") == _TEMPLATE
                   for pid in pkg_ids for t in листа_на[pid]):
                без_анкер.add(верига)
            continue

        # Делът на пакета от ДОКАЗАНАТА работа.  Стъпката е една, дори когато
        # носи няколко КСС реда: паралелните задачи в нея минават заедно,
        # затова се брои най-дългата, не сборът им.
        доказани: dict[str, float] = {}
        for pid in pkg_ids:
            по_стъпка: dict[str, float] = {}
            for t in листа_на[pid]:
                if str(t.get("duration_source") or "") == "calculated":
                    k = str(t.get("chain_step") or "")
                    по_стъпка[k] = max(по_стъпка.get(k, 0.0),
                                       _num(t.get("duration")))
            доказани[pid] = sum(по_стъпка.values())

        общо = sum(доказани.values())
        дял = {pid: (доказани[pid] / общо if общо > 0 else 1.0 / len(pkg_ids))
               for pid in pkg_ids}

        # Стъпката се разпределя ЦЯЛА, между пакетите, които я носят като
        # шаблон.  Пакет, за който същата стъпка е сметната от нормите, вече
        # носи своя дял — неговата част от еталона не се раздава втори път.
        по_стъпка_шаблонни: dict[str, list[str]] = {}
        задачи_на: dict[tuple[str, str], list[dict]] = {}
        for pid in pkg_ids:
            for t in листа_на[pid]:
                if str(t.get("duration_source") or "") != _TEMPLATE:
                    continue
                k = str(t.get("chain_step") or "")
                if k not in steps:
                    continue
                if pid not in по_стъпка_шаблонни.setdefault(k, []):
                    по_стъпка_шаблонни[k].append(pid)
                задачи_на.setdefault((k, pid), []).append(t)

        for k, носители in по_стъпка_шаблонни.items():
            медиана = _num(steps[k].get("median_days"))
            if медиана <= 0:
                continue
            дял_на_носителите = sum(дял[pid] for pid in носители)
            цел = int(round(медиана * наблюдавани * дял_на_носителите))
            цел = max(цел, len(носители))
            раздадени = _apportion(цел, [дял[pid] for pid in носители])
            for pid, дни in zip(носители, раздадени):
                for t in задачи_на[(k, pid)]:
                    t["segment_share"] = round(дял[pid], 4)
                    t["template_days"] = медиана
                    if дни != _num(t.get("duration")):
                        t["duration"] = дни
                        пипнати += 1

    if пипнати:
        бележки.append(
            f"Овърхед на участък: {пипнати} задължителни стъпки без количество "
            "получиха дела си от еталонния обем, вместо медианата за един "
            "еталонен участък.")
    if без_анкер:
        бележки.append(
            "Без анкер от еталона остават вериги " + ", ".join(sorted(без_анкер))
            + " — техните задължителни стъпки пазят медианата на шаблона.")
    for бележка in бележки:
        logger.info("%s", бележка)
    return tasks, бележки


def scale_structures_to_declared_days(
    tasks: list[dict],
    packages: Iterable[Any],
    chains: dict,
) -> tuple[list[dict], list[str]]:
    """Свива веригата на едно съоръжение до ОБЯВЕНИЯ му срок.

    Шаблонът `structure` сумира 19 дни — толкова е траел единственият
    екземпляр, наблюдаван в еталонния график.  Изпълнителят обаче дава срок за
    ВСЯКА позиция (19.08.2026): преливна шахта 14 дни, индивидуална монолитна
    РШ 7 дни, водомерна шахта 5.  Числото е за ЕДНО съоръжение, от изкопа до
    покривните панели.

    Дните се разпределят между стъпките пропорционално на шаблонните им
    медиани, по най-голям остатък, за да е сборът точно обявеният срок.

    КОГАТО СРОКЪТ Е ПО-МАЛЪК ОТ БРОЯ СТЪПКИ, това се КАЗВА, а не се замазва:
    осем последователни операции не се побират в седем цели дни.  Тогава всяка
    получава по един ден и графикът излиза с толкова, колкото операциите
    изискват — с бележка, че обявеният срок предполага сливане на операции,
    което шаблонът не описва.
    """
    chain_defs = (chains or {}).get("chains", chains) or {}
    структура = chain_defs.get("structure")
    if not isinstance(структура, dict):
        return tasks, []

    правила = ((chains or {}).get("standalone_structures") or {}).get(
        "по_позиция") or {}
    норми = _дни_на_брой()
    if not правила or not норми:
        return tasks, []

    steps = {str(s.get("key")): s for s in (структура.get("steps") or [])}
    име_на = {}
    for pkg in packages or []:
        pid = _attr(pkg, "id")
        if pid and str(_attr(pkg, "chain") or "") == "structure":
            име_на[str(pid)] = str(_attr(pkg, "label") or _attr(pkg, "name") or "")

    по_пакет: dict[str, list[dict]] = {}
    for task in tasks or []:
        pid = str(task.get("parent_id") or "")
        if pid in име_на and task.get("chain_step"):
            по_пакет.setdefault(pid, []).append(task)

    пипнати = 0
    не_се_побират: set[str] = set()
    for pid, листа in по_пакет.items():
        ключ = _норма_за(име_на[pid], правила)
        цел = норми.get(ключ) if ключ else None
        if not цел:
            continue
        цел = int(round(float(цел)))
        подред = sorted(листа, key=lambda t: str(t.get("chain_step")))
        тегла = [_num(steps.get(str(t.get("chain_step")), {}).get("median_days")) or 1.0
                 for t in подред]
        if цел < len(подред):
            не_се_побират.add(f"{име_на[pid]} ({цел} дни за {len(подред)} операции)")
            цел = len(подред)
        раздадени = _apportion(цел, тегла)
        for задача, дни in zip(подред, раздадени):
            задача["declared_structure_days"] = цел
            if дни != _num(задача.get("duration")):
                задача["duration"] = int(дни)
                задача["duration_source"] = "declared_structure"
                пипнати += 1

    бележки: list[str] = []
    if пипнати:
        бележки.append(
            f"Съоръжения: {пипнати} стъпки свити до обявения срок на "
            "съоръжението вместо шаблонните 19 дни.")
    if не_се_побират:
        бележки.append(
            "Обявеният срок предполага СЛИВАНЕ на операции, което шаблонът не "
            "описва — " + "; ".join(sorted(не_се_побират))
            + ".  Графикът показва по един ден на операция.")
    for бележка in бележки:
        logger.info("%s", бележка)
    return tasks, бележки


def _дни_на_брой() -> dict[str, float]:
    from src.duration_calculator import load_productivities

    блок = (load_productivities() or {}).get("count_productivities") or {}
    return {к: float(v["дни_на_брой"]) for к, v in блок.items()
            if isinstance(v, dict) and isinstance(v.get("дни_на_брой"), (int, float))}


def _норма_за(име: str, правила: dict) -> str | None:
    import re

    for шаблон, ключ in правила.items():
        if шаблон.startswith("_"):
            continue
        if re.search(шаблон, име, re.IGNORECASE):
            return str(ключ)
    return None
