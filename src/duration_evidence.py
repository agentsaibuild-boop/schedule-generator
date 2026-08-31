# -*- coding: utf-8 -*-
"""Колко трае работата по ДОКАЗАТЕЛСТВА и колко трае в ОФЕРТАТА — поотделно.

ЗАЩО.  Одиторът, 31.08.2026:

    „срокът вече произвежда темпо, вместо мрежата да произвежда срок.  Това е
     приемливо за тръжно планиране, но трябва да пазите две различни понятия:
     feasibility duration и bid duration.  Не позволявайте второто да
     унищожава първото."

Прав е, и дотук точно това ставаше: `calibrate_to_declared_pace` пренаписваше
`duration`, а изходът вече не знаеше каква е била работата преди свиването.
Оставаше само `NOT_PARAMETRIC` — код, който казва „няма норма", но се четеше
като „няма доказателство".  А това са различни неща: медианата от 22 изпълнени
човешки графика Е доказателство, само не е норма.

Затова тук всяка задача носи ПЕТ полета:

    base_duration           колко трае по доказателства
    base_duration_source    какво е доказателството (стълбицата долу)
    bid_duration            колко трае в графика, който подаваме
    calibration_factor      bid ÷ base
    calibration_reason      защо са различни

И тогава изречението, което системата може да каже за всяка задача, е:

    „По наличните доказателства тази работа е 13 дни.  За да се вмести
     офертата, графикът я планира на 10.5 дни."

вместо да мълчи или да се престори, че 10.5 са изчислени.

СТЪЛБИЦАТА е на одитора, подредена от най-силно към най-слабо основание.
`UNSUPPORTED` е единственото, което значи „нямаме основание"; всичко над него
е основание с различна сила и се брои отделно.
"""

from __future__ import annotations

from typing import Any, Iterable

#: Основанията, от най-силно към най-слабо.  Редът има значение: отчетът и
#: сортировките стъпват на него.
СТЪЛБИЦА: tuple[str, ...] = (
    "PARAMETRIC_NORM",            # норма по DN + материал + метод
    "HISTORICAL_CORPUS_MEDIAN",   # медиана от изпълнените човешки графици
    "CONTRACTOR_INPUT",           # изпълнителят е обявил темпо/срок за ТОЗИ обект
    "CONTRACT_SPAN_CALIBRATED",   # изведено от договорния срок, не доказано
    "DEFAULT_ASSUMPTION",         # под/подразбиране от конфигурацията
    "UNSUPPORTED",                # нямаме основание
)

ОБЯСНЕНИЕ = {
    "PARAMETRIC_NORM": "норма по диаметър, материал и метод (productivities.json)",
    "HISTORICAL_CORPUS_MEDIAN": "медиана от изпълнените човешки графици (tech_chains.json)",
    "CONTRACTOR_INPUT": "обявено от изпълнителя за този обект",
    "CONTRACT_SPAN_CALIBRATED": "изведено от договорния срок и заявените екипи",
    "DEFAULT_ASSUMPTION": "подразбиране от конфигурацията, без доказателство за обекта",
    "UNSUPPORTED": "нямаме основание",
}

#: Кои основания се броят за ДОКАЗАНИ, когато се пита „каква част от графика
#: стъпва на нещо".  Договорното калибриране НЕ е доказателство за работата —
#: то е решение за офертата и затова стои отвън.
ДОКАЗАНИ = frozenset({"PARAMETRIC_NORM", "HISTORICAL_CORPUS_MEDIAN",
                      "CONTRACTOR_INPUT"})


def _num(стойност: Any) -> float:
    try:
        n = float(стойност)
    except (TypeError, ValueError):
        return 0.0
    return n if n == n and abs(n) != float("inf") else 0.0


def grade_of(task: dict) -> str:
    """Кое е основанието за продължителността на тази задача.

    Чете се от полетата, които вече се пишат по пътя — не се гадае.
    """
    ако_е = str(task.get("duration_source") or "")
    код = str(task.get("duration_status") or "")

    if ако_е == "calculated" or код == "CALCULATED":
        return "PARAMETRIC_NORM"
    if ако_е == "chain_template":
        return "HISTORICAL_CORPUS_MEDIAN"
    if ако_е == "construction_span":
        # Задача, разтеглена върху цялата фаза (надзор, договорни задължения) —
        # тя няма собствена продължителност и не бива да се брои като доказана.
        return "CONTRACT_SPAN_CALIBRATED"
    if код == "MILESTONE" or _num(task.get("duration")) == 0:
        return "PARAMETRIC_NORM"        # точка по договор — няма какво да трае
    if ако_е == "suggested":
        return "UNSUPPORTED"
    return "UNSUPPORTED" if ако_е else "DEFAULT_ASSUMPTION"


def stamp_base(tasks: Iterable[dict]) -> list[str]:
    """Записва `base_duration` и `base_duration_source` — ПРЕДИ калибрирането.

    Вика се веднъж, точно след като нормите и шаблоните са казали думата си и
    ПРЕДИ обявеното темпо да пренапише `duration`.  Повторно извикване не
    презаписва: базата е първото състояние, не последното.
    """
    брой: dict[str, int] = {}
    for task in tasks or []:
        if task.get("is_summary"):
            continue
        оценка = grade_of(task)
        task.setdefault("base_duration", _num(task.get("duration")))
        task.setdefault("base_duration_source", оценка)
        брой[оценка] = брой.get(оценка, 0) + 1

    редове = [f"{ОБЯСНЕНИЕ[о]}: {брой[о]}"
              for о in СТЪЛБИЦА if брой.get(о)]
    return [f"Продължителности по основание — {'; '.join(редове)}."] if редове else []


def stamp_bid(tasks: Iterable[dict]) -> list[str]:
    """Записва `bid_duration`, `calibration_factor`, `calibration_reason`.

    Вика се НАКРАЯ, върху продължителностите, които излизат в офертата.
    """
    калибрирани = 0
    свити = разтеглени = 0
    for task in tasks or []:
        if task.get("is_summary"):
            continue
        офертна = _num(task.get("duration"))
        база = _num(task.get("base_duration"))
        task["bid_duration"] = офертна
        if база <= 0:
            continue
        множител = офертна / база
        task["calibration_factor"] = round(множител, 4)
        if abs(множител - 1.0) < 0.005:
            task.pop("calibration_reason", None)
            continue
        калибрирани += 1
        свити += множител < 1
        разтеглени += множител > 1
        темпо = task.get("declared_pace")
        произход = str(task.get("pace_origin") or "")
        if темпо and произход == "deadline":
            причина = (f"свита до темпо {float(темпо):g} м/ден, изведено от "
                       f"договорния срок и заявените екипи")
            task["base_duration_source"] = task.get("base_duration_source")
            task["duration_evidence"] = "CONTRACT_SPAN_CALIBRATED"
        elif темпо:
            причина = (f"приведена към темпо {float(темпо):g} м/ден, обявено "
                       f"от изпълнителя")
            task["duration_evidence"] = "CONTRACTOR_INPUT"
        else:
            причина = "напасната към договорните фази и зависимостите"
            task["duration_evidence"] = "CONTRACT_SPAN_CALIBRATED"
        task["calibration_reason"] = причина

    if not калибрирани:
        return []
    return [f"Офертни продължителности: {калибрирани} задачи се различават от "
            f"доказаните ({свити} свити, {разтеглени} разтеглени) — всяка носи "
            f"`base_duration`, `calibration_factor` и причината."]


def report(tasks: Iterable[dict]) -> dict:
    """Обобщение в ДВА разреза, защото въпросите са два.

    `по_основание` отговаря на „върху какво стъпва тази работа" — гледа
    `base_duration_source`, тоест състоянието ПРЕДИ калибрирането.  Точно това
    се пита, когато някой каже „821 задачи без доказателство".

    `калибрирани` отговаря на „кое е местено заради офертата".  Задача може да
    има силно основание И да е свита — двете не се изключват и затова не бива
    да се смесват в една колона.
    """
    по_основание: dict[str, dict[str, float]] = {}
    калибрирани = 0
    база_общо = оферта_общо = 0.0
    листа = 0
    for task in tasks or []:
        if task.get("is_summary"):
            continue
        листа += 1
        основание = str(task.get("base_duration_source") or grade_of(task))
        клетка = по_основание.setdefault(основание, {"задачи": 0, "дни": 0.0})
        клетка["задачи"] += 1
        клетка["дни"] += _num(task.get("duration"))
        база_общо += _num(task.get("base_duration"))
        оферта_общо += _num(task.get("duration"))
        if task.get("calibration_reason"):
            калибрирани += 1

    доказани = sum(к["задачи"] for о, к in по_основание.items() if о in ДОКАЗАНИ)
    return {
        "по_основание": {о: по_основание[о] for о in СТЪЛБИЦА
                         if о in по_основание},
        "задачи": листа,
        "доказани": доказани,
        "дял_доказани": round(доказани / листа, 4) if листа else 0.0,
        "калибрирани": калибрирани,
        "база_дни": round(база_общо, 1),
        "оферта_дни": round(оферта_общо, 1),
        "калибриране": round(оферта_общо / база_общо, 4) if база_общо else 1.0,
    }


def describe(отчет: dict) -> list[str]:
    """Отчетът с думи — за прогона и за брийфа."""
    редове = [f"ОСНОВАНИЕ ЗА ПРОДЪЛЖИТЕЛНОСТИТЕ "
              f"({отчет['задачи']} задачи, "
              f"{отчет['дял_доказани']:.0%} на доказателство):"]
    for основание, клетка in отчет["по_основание"].items():
        редове.append(f"  {основание:<26} {клетка['задачи']:>4} задачи "
                      f"{клетка['дни']:>8.0f} дни   {ОБЯСНЕНИЕ[основание]}")
    if отчет["база_дни"]:
        редове.append(
            f"  сбор: доказано {отчет['база_дни']:.0f} задача-дни → "
            f"в офертата {отчет['оферта_дни']:.0f} "
            f"(×{отчет['калибриране']:.2f}); "
            f"{отчет['калибрирани']} задачи са местени заради срока")
    return редове
