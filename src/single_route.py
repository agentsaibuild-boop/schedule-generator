"""Еднократната работа се прави ВЕДНЪЖ, когато обектът е едно трасе.

ЗАЩО.  Човешкият график на тласкателя „Образцов чифлик" (ВиК Русе, 1268 м,
[[ruse-tlaskatel-benchmark]]) кара ЕДИН екип последователно по три участъка, но
изпитването (10 дни), дезинфекцията (8 дни) и присъединяването (10 дни) са по
ЕДНА задача за целия водопровод.  И така трябва: водопроводът се изпитва под
налягане като цяло, дезинфекцира се като цяло и се присъединява веднъж към
съществуващата мрежа.

Ние ги повтаряхме на всеки изпълнителски участък — осем етапа × три стъпки =
24 задачи там, където човекът пише 3.  В срока това дълго не личеше, защото
обявеният срок се използва докрай и таванът изравнява разликата
([[declared-terms-are-used-fully]]); личи във ФОРМАТА, а формата е това, което
оценителят чете.

ЗАЩО НЕ Е ПОВЕДЕНИЕ ПО ПОДРАЗБИРАНЕ.  При разпределителна мрежа обратното е
вярното: в еталона за Илиянци всеки клон се изпитва и дезинфекцира сам —
23 водопроводни участъка, 23 дезинфекции.  Разликата между тласкател и мрежа не
се чете от количествата (и двете са „метри тръба"), затова се ОБЯВЯВА —
`SINGLE_ROUTE` / въпросникът — а не се гадае от името на обекта.  Виж
`tender_parameters.single_route_networks`.

КАК.  Стъпките, които са еднократни, са маркирани в самата верига
(`once_per_route` в `config/tech_chains.json`).  Тук те се вадят от участъците
и се слагат ВЕДНЪЖ, накрая, в реда на веригата:

  * задачите БЕЗ цитат към КСС се сливат в една.  Продължителността НЕ се сумира
    тук — анкерът на метър (`segment_scale`) я раздава след това по метрите на
    трасето, и когато задачата е една, тя получава целия анкер;
  * задача С цитат остава — тя доказва конкретен ред от КСС, а един ред не може
    да бъде цитиран от две задачи, без сборът да излъже гейта за количествата.
    Такива задачи не се сливат, а се ПРЕМЕСТВАТ накрая заедно с останалите и се
    навързват една след друга: работата пак става веднъж, като една кампания;
  * кампанията чака ВСИЧКИ участъци — не може да изпитваш трасе, което още се
    полага;
  * това, което участъкът е губил (напр. „арматури" след „изпитване"), се
    закача за онова, за което самата извадена стъпка се е държала — иначе
    участък 1 би чакал края на целия обект.

Всичко се ОБЯВЯВА в бележките на конвейера: колко задачи са станали колко и
защо.  Изключва се с `SINGLE_ROUTE` празно (по подразбиране) или
`ROUTE_WIDE_STEPS=0`.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Iterable

logger = logging.getLogger(__name__)

#: Само строителните вериги имат участъци — договорните фази нямат какво да свият.
_СТРОИТЕЛСТВО = "construction"


def _изключено() -> bool:
    return str(os.getenv("ROUTE_WIDE_STEPS", "1") or "1").strip().lower() in (
        "0", "false", "no", "не")


def _dep_id(dep: Any) -> str:
    if isinstance(dep, dict):
        return str(dep.get("predecessor_id") or dep.get("id") or "")
    return str(dep or "")


def _зависимости(task: dict) -> list[dict]:
    """Зависимостите на задачата, винаги като списък от речници."""
    готови: list[dict] = []
    for dep in task.get("dependencies") or []:
        if isinstance(dep, dict):
            готови.append(dict(dep))
        elif dep:
            готови.append({"predecessor_id": str(dep), "type": "FS",
                           "lag_days": 0})
    return готови


def _без_повторения(deps: list[dict]) -> list[dict]:
    видени: set[tuple[str, str, int]] = set()
    ако: list[dict] = []
    for dep in deps:
        ключ = (_dep_id(dep), str(dep.get("type") or "FS"),
                int(dep.get("lag_days") or 0))
        if ключ[0] and ключ not in видени:
            видени.add(ключ)
            ако.append(dep)
    return ако


def collapse_route_wide_steps(
    tasks: list[dict],
    packages: Iterable[Any],
    chains: dict[str, Any] | None = None,
) -> tuple[list[dict], list[str]]:
    """Свий еднократните стъпки до една кампания за цялото трасе.

    Args:
        tasks: Задачите от `expand_packages` (плосък списък).
        packages: Пакетите, В РЕДА НА ИЗПЪЛНЕНИЕ.
        chains: Технологичните вериги; `None` зарежда конфигурацията.

    Returns:
        Новите задачи и бележките за това какво е направено.
    """
    from src.tender_parameters import single_route_networks
    from src.work_package import load_chains

    мрежи = single_route_networks()
    if not мрежи or _изключено():
        return tasks, []

    cfg = chains if chains is not None else load_chains()
    chain_defs = cfg.get("chains", {}) or {}

    групи: dict[tuple[str, str], list[Any]] = {}
    for pkg in packages:
        верига = chain_defs.get(getattr(pkg, "chain", "")) or {}
        if верига.get("wbs_root", _СТРОИТЕЛСТВО) != _СТРОИТЕЛСТВО:
            continue
        if str(getattr(pkg, "network", "")) not in мрежи:
            continue
        if not any(s.get("once_per_route") for s in верига.get("steps") or []):
            continue
        групи.setdefault((pkg.network, pkg.chain), []).append(pkg)

    бележки: list[str] = []
    for (мрежа, верига), пакети in групи.items():
        if len(пакети) < 2:
            # Едно трасе, един участък — стъпката и без това е една.
            continue
        tasks, нови = _свий_групата(tasks, пакети, chain_defs[верига],
                                    мрежа, верига)
        бележки.extend(нови)

    return tasks, бележки


def _свий_групата(
    tasks: list[dict],
    пакети: list[Any],
    верига: dict,
    мрежа: str,
    ключ_на_веригата: str,
) -> tuple[list[dict], list[str]]:
    ключове = [str(s.get("key")) for s in верига.get("steps") or []
               if s.get("once_per_route")]
    if not ключове:
        return tasks, []

    имена = {str(s.get("key")): str(s.get("name") or s.get("key"))
             for s in верига.get("steps") or []}
    ред_на_пакета = {str(p.id): i for i, p in enumerate(пакети)}
    по_id = {str(t.get("id")): t for t in tasks}

    # Задачите на всяка еднократна стъпка, в реда на участъците.
    по_стъпка: dict[str, list[dict]] = {}
    for task in tasks:
        ключ = str(task.get("chain_step") or "")
        if ключ in ключове and str(task.get("parent_id")) in ред_на_пакета:
            по_стъпка.setdefault(ключ, []).append(task)
    for ключ, редица in по_стъпка.items():
        редица.sort(key=lambda t: ред_на_пакета.get(str(t.get("parent_id")), 0))

    извадени = {str(t.get("id")) for редица in по_стъпка.values() for t in редица}
    if not извадени:
        return tasks, []

    def през_извадените(tid: str, дълбочина: int = 0) -> list[dict]:
        """Зависимостите на извадена задача, изразени през ОСТАВАЩИТЕ.

        Без това участък 1 би чакал края на трасето: неговите „арматури" се
        държат за неговото „изпитване", а изпитването отива накрая.
        """
        task = по_id.get(tid)
        if task is None or дълбочина > len(извадени):
            return []
        ако: list[dict] = []
        for dep in _зависимости(task):
            pid = _dep_id(dep)
            if pid in извадени:
                ако.extend(през_извадените(pid, дълбочина + 1))
            else:
                ако.append(dep)
        return ако

    # 1. Каквото се е държало за извадена задача, се държи за нейната опора.
    for task in tasks:
        if str(task.get("id")) in извадени:
            continue
        deps = _зависимости(task)
        if not any(_dep_id(d) in извадени for d in deps):
            continue
        нови: list[dict] = []
        for dep in deps:
            pid = _dep_id(dep)
            if pid in извадени:
                нови.extend(през_извадените(pid))
            else:
                нови.append(dep)
        task["dependencies"] = _без_повторения(нови)

    # 2. Кои задачи остават в участъците и с какво завършва всеки от тях.
    оставащи = [t for t in tasks if str(t.get("id")) not in извадени]
    в_участък: dict[str, list[dict]] = {}
    for task in оставащи:
        родител = str(task.get("parent_id"))
        if родител in ред_на_пакета:
            в_участък.setdefault(родител, []).append(task)
    опори: list[dict] = []
    for pid, задачи in в_участък.items():
        заети = {_dep_id(d) for t in задачи for d in _зависимости(t)}
        краища = [t for t in задачи if str(t.get("id")) not in заети]
        опори.extend(краища or задачи[-1:])

    # 3. Кампанията: по една задача на стъпка, освен когато цитат я държи.
    последен = str(пакети[-1].id)
    предишни: list[str] = [str(t.get("id")) for t in опори]
    бележки: list[str] = []
    изхвърлени: set[str] = set()

    for ключ in ключове:
        редица = по_стъпка.get(ключ) or []
        if not редица:
            continue
        без_цитат = [t for t in редица if not t.get("source_ref")]
        с_цитат = [t for t in редица if t.get("source_ref")]
        оцелели = ([без_цитат[0]] if без_цитат else []) + с_цитат
        изхвърлени.update(str(t.get("id")) for t in без_цитат[1:])

        for task in оцелели:
            task["parent_id"] = последен
            task["route_wide"] = True
            # Веригата се записва В ЗАДАЧАТА, защото оттук нататък тя вече не
            # принадлежи на своя участък: гейтът за пълнотата на шаблона пита
            # „коя верига я е родила", а родителят вече е чужд пакет.
            task["chain"] = ключ_на_веригата
            task["dependencies"] = _без_повторения(
                [{"predecessor_id": pid, "type": "FS", "lag_days": 0}
                 for pid in предишни])
            if not task.get("source_ref"):
                task["name"] = f"{имена.get(ключ, ключ)} — за цялото трасе"
            elif "за цялото трасе" not in str(task.get("name") or ""):
                task["name"] = f"{task['name']} — за цялото трасе"
            предишни = [str(task.get("id"))]

        ако_цитати = (f"; {len(с_цитат)} остават отделно, защото цитират "
                      f"редове от КСС" if с_цитат else "")
        бележки.append(
            f"ЕДИНИЧНО ТРАСЕ ({мрежа}): „{имена.get(ключ, ключ)}\" се прави "
            f"веднъж за цялото трасе — {len(редица)} задачи по участъци стават "
            f"{len(оцелели)}{ако_цитати}")

    оставащи_задачи = [t for t in tasks if str(t.get("id")) not in изхвърлени]
    return оставащи_задачи, бележки
