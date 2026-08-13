"""Защо графикът е толкова дълъг — и издържа ли структурните инварианти.

ОДИТ 10.08.2026:

  P0.4 „Export diagnostics: design span, mobilization span, construction span,
       supervision span, acceptance span, critical path duration, top 10
       resource-induced delays, top 10 dependency-induced delays."

  P1.3 „rerun_series.py да emit-ва structural flags.  Clean изисква всички
       hard structural flags true."

Двете искат едно и също смятане, затова са тук заедно.

Централното разграничение е между ДВАТА вида забавяне, защото те се лекуват по
различен начин и досега се смесваха в едно число „срокът е дълъг":

    зависимост  задачата чака ПРЕДШЕСТВЕНИК — това е технология
    ресурс      задачата чака СВОБОДНА БРИГАДА — това е капацитет

Първото се съкращава със застъпване (виж топологията на веригите), второто —
с повече хора.  Кое от двете доминира е въпрос, на който графикът трябва да
може да отговори сам.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any, Iterable

__all__ = [
    "phase_spans",
    "critical_path_days",
    "delay_breakdown",
    "structural_flags",
    "duration_report",
    "concurrency_report",
    "concurrency_bottlenecks",
    "widest_join",
    "HARD_STRUCTURAL_FLAGS",
]

#: Структурните флагове, които трябва да са верни.  Липсата на който и да е от
#: тях значи, че файлът изглежда готов, но не издържа проверка отвън.
HARD_STRUCTURAL_FLAGS = (
    "template_complete",
    "contract_scope_complete",
    "all_leaves_reach_terminal",
    "resource_capacity_ok",
    "summary_rollup_ok",
    "supervision_span_ok",
    # Добавени по решение на одитора (10.08.2026): количествата и цитатите не
    # са „меки" показатели — без тях графикът не е проверим отвън.
    "quantity_conservation_ok",
    "source_ref_fully_resolvable",
    "no_fatal_parse_errors",
    "no_unresolved_diameter_conflict",
)

#: Фазите, без които обхватът не е пълен.  „Проектиране" НЕ е сред тях: то
#: съществува само при инженеринг, а при търг само за строителство липсата му
#: е вярна, не дефект.  Първата версия на този гейт вадеше design от проверката
#: с израз, който правеше флага верен и когато фазата липсва — тоест гейт,
#: който не може да падне.
_REQUIRED_PHASES = frozenset({
    "mobilization", "construction", "supervision", "acceptance",
})

_PHASE_LABELS = {
    "design": "Проектиране",
    "mobilization": "Мобилизация",
    "construction": "Строителство",
    "supervision": "Авторски надзор",
    "acceptance": "Приемане",
}


def _leaves(tasks: Iterable[dict]) -> list[dict]:
    return [t for t in tasks if not t.get("is_summary") and not t.get("type") == "summary"]


def _start(task: dict) -> int | None:
    value = task.get("start_day")
    return int(value) if value is not None else None


def _end(task: dict) -> int | None:
    value = task.get("end_day", task.get("start_day"))
    return int(value) if value is not None else None


# ---------------------------------------------------------------------------
# P0.4 — от какво е съставен срокът
# ---------------------------------------------------------------------------


def phase_spans(tasks: Iterable[dict]) -> dict[str, dict]:
    """Начало, край и продължителност на всяка договорна фаза."""
    buckets: dict[str, list[dict]] = defaultdict(list)
    for task in _leaves(tasks):
        if _start(task) is None:
            continue
        buckets[str(task.get("wbs_root") or "construction")].append(task)

    spans: dict[str, dict] = {}
    for key, group in buckets.items():
        start = min(_start(t) for t in group)
        finish = max(_end(t) for t in group)
        spans[key] = {
            "label": _PHASE_LABELS.get(key, key),
            "start_day": start,
            "end_day": finish,
            "days": finish - start + 1,
            "tasks": len(group),
        }
    return spans


def critical_path_days(tasks: Iterable[dict]) -> int:
    """Дължина на критичния път в дни (0, ако не е смятан)."""
    critical = [t for t in _leaves(tasks)
                if t.get("is_critical") or t.get("critical")]
    if not critical:
        return 0
    return max(_end(t) for t in critical) - min(_start(t) for t in critical) + 1


def delay_breakdown(tasks: Iterable[dict], top: int = 10) -> dict[str, list[dict]]:
    """Кои задачи чакат заради технология и кои — заради капацитет.

    За всяка задача се смята НАЙ-РАННОТО начало, което зависимостите допускат.
    Разликата до действителното начало не е обяснена от мрежата, тоест идва от
    ресурсното изравняване.
    """
    tasks = list(tasks)
    by_id = {str(t.get("id")): t for t in tasks}
    scheduled = [t for t in _leaves(tasks) if _start(t) is not None]
    if not scheduled:
        return {"dependency": [], "resource": []}

    project_start = min(_start(t) for t in scheduled)

    dependency: list[dict] = []
    resource: list[dict] = []
    for task in scheduled:
        earliest = project_start
        driver = None
        for dep in (task.get("dependencies") or []):
            pred = by_id.get(str(dep.get("predecessor_id")
                                 if isinstance(dep, dict) else dep))
            if pred is None or _start(pred) is None:
                continue
            lag = int(dep.get("lag_days", 0)) if isinstance(dep, dict) else 0
            kind = (dep.get("type") if isinstance(dep, dict) else "FS") or "FS"
            ready = (_start(pred) + lag if kind.upper() == "SS"
                     else _end(pred) + lag + 1)
            if ready > earliest:
                earliest, driver = ready, pred
        actual = _start(task)

        waited = earliest - project_start
        if waited > 0 and driver is not None:
            dependency.append({
                "id": task.get("id"), "name": task.get("name", ""),
                "days": waited, "after": driver.get("id"),
            })
        held = actual - earliest
        if held > 0:
            resource.append({
                "id": task.get("id"), "name": task.get("name", ""),
                "days": held, "team": task.get("team") or task.get("crew_id") or "",
            })

    dependency.sort(key=lambda d: -d["days"])
    resource.sort(key=lambda d: -d["days"])
    return {"dependency": dependency[:top], "resource": resource[:top]}


def duration_report(tasks: Iterable[dict], top: int = 10) -> dict[str, Any]:
    """Отчетът от P0.4, в един обект."""
    tasks = list(tasks)
    scheduled = [t for t in _leaves(tasks) if _start(t) is not None]
    delays = delay_breakdown(tasks, top=top)
    total = (max(_end(t) for t in scheduled) - min(_start(t) for t in scheduled) + 1
             if scheduled else 0)
    return {
        "total_days": total,
        "critical_path_days": critical_path_days(tasks),
        "phases": phase_spans(tasks),
        "top_dependency_delays": delays["dependency"],
        "top_resource_delays": delays["resource"],
        "resource_delay_days": sum(d["days"] for d in delays["resource"]),
    }


# ---------------------------------------------------------------------------
# P1.3 — структурните флагове
# ---------------------------------------------------------------------------


def _successors(tasks: list[dict]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for task in tasks:
        for dep in (task.get("dependencies") or []):
            pred = str(dep.get("predecessor_id") if isinstance(dep, dict) else dep)
            out[pred].add(str(task.get("id")))
    return out


#: Критериите, които зависят от ВХОДНИЯ ДОКУМЕНТ, а не от прогона.
#: Противоречие в КСС присъства във всеки прогон еднакво — то не мери
#: стабилността на генерацията, а качеството на документа.
_INPUT_DATA_CRITERIA = ("no_unresolved_diameter_conflict",)


def is_clean_but_for_the_input(flags: dict[str, Any]) -> bool:
    """Чист, ако се пренебрегнат противоречията в самия КСС.

    НЕ е заместител на `is_clean` и не бива да се докладва вместо него.
    Съществува, защото иначе 40 прогона върху търг с един противоречив ред
    дават четиридесет еднакви провала и нищо не се научава за стабилността.

    Тоест: `is_clean` отговаря „може ли този график да се обяви за готов",
    а този — „ако документът беше изряден, щеше ли да може".
    """
    return all(bool(flags.get(name)) for name in HARD_STRUCTURAL_FLAGS
               if name not in _INPUT_DATA_CRITERIA)


def is_clean(flags: dict[str, Any]) -> bool:
    """Договорът за „чист", потвърден от одитора на 10.08.2026.

    Експортируем и чист са РАЗЛИЧНИ неща и това е нарочно.  График с
    противоречив входен документ може да бъде предаден като provisional — но
    не може да се обяви за чист, докато противоречието стои.
    """
    return all(bool(flags.get(name)) for name in HARD_STRUCTURAL_FLAGS)


def structural_flags(
    tasks: Iterable[dict],
    *,
    packages: Iterable[Any] = (),
    chains: dict | None = None,
    boq_index: Iterable[Any] = (),
    conservation: dict | None = None,
    parse_errors: Iterable[str] = (),
) -> dict[str, Any]:
    """Флаговете, които одиторът иска да вижда на всеки прогон."""
    tasks = list(tasks)
    leaves = _leaves(tasks)
    successors = _successors(tasks)

    terminals = [t for t in leaves if not successors.get(str(t.get("id")))]

    # Всеки лист трябва да стига до финала — иначе работа виси без приемане.
    reaches = _reachability(leaves, successors, {str(t.get("id")) for t in terminals})

    spans = phase_spans(tasks)
    construction = spans.get("construction")
    supervision = spans.get("supervision")
    supervision_ok = True
    if construction and supervision:
        supervision_ok = (supervision["start_day"] <= construction["start_day"]
                          and supervision["end_day"] >= construction["end_day"])

    refs = {getattr(r, "ref", None) for r in boq_index}
    cited = [t for t in leaves if t.get("source_ref")]
    resolvable = [t for t in cited if t.get("source_ref") in refs] if refs else []

    spatial = [t for t in leaves if t.get("spatial_segment_id")]
    located = [t for t in spatial if str(t.get("street") or "").strip()]

    errors = [str(e) for e in parse_errors]
    fatal = [e for e in errors if "пропуснат" in e or "не е ред от КСС" in e]
    conflicts = [e for e in errors if "DIAMETER_CONFLICT" in e]
    resolvable_pct = round(
        100.0 * len(resolvable) / len(cited), 1) if cited and refs else 0.0

    флагове = {
        "template_complete": _template_complete(packages, chains, tasks),
        "contract_scope_complete": _REQUIRED_PHASES <= set(spans),
        "terminal_count": len(terminals),
        "all_leaves_reach_terminal": len(reaches) == len(leaves),
        "summary_rollup_ok": _rollup_ok(tasks),
        "supervision_span_ok": supervision_ok,
        "resource_capacity_ok": not _capacity_overloads(tasks),
        "resource_overloads": _capacity_overloads(tasks)[:5],
        # Твърди, по решение на одитора.
        "quantity_conservation_ok": bool((conservation or {}).get("ok")),
        "source_ref_resolvable_pct": resolvable_pct,
        "source_ref_fully_resolvable": resolvable_pct == 100.0,
        "parse_fatal": len(fatal),
        "no_fatal_parse_errors": not fatal,
        "diameter_conflicts": len(conflicts),
        "no_unresolved_diameter_conflict": not conflicts,
        # Меки — диагностика, не условие.
        "parse_recovered": len(errors) - len(fatal),
        "spatial_resolved_pct": round(
            100.0 * len(located) / len(spatial), 1) if spatial else 0.0,
    }
    return _mark_unevaluated(флагове, tasks)


#: Флагове, които при ПРАЗЕН график са истина по празнота: няма лист, който да
#: не стига до финала; няма ресурс, който да е претоварен; надзорът покрива
#: строителство, което го няма.
_VACUOUS_WHEN_EMPTY = (
    "all_leaves_reach_terminal", "summary_rollup_ok", "supervision_span_ok",
    "resource_capacity_ok", "template_complete", "contract_scope_complete",
    "quantity_conservation_ok", "source_ref_fully_resolvable",
)


def _mark_unevaluated(флагове: dict[str, Any], tasks: list[dict]) -> dict[str, Any]:
    """Празен график НЕ дава зелени флагове.

    ОДИТ 13.08.2026: „при empty/error schedules някои flags са true по vacuous
    truth... по-добре tri-state: pass / fail / not_evaluated."

    Прав е, и това не е дребно: в серията от 40 прогона петте празни отговора
    на доставчика носеха `supervision_span_ok=true` при НУЛА задачи.  Тоест
    част от зелените числа в телеметрията означаваха „нямаше какво да се
    провери", а се четяха като „проверено и наред".

    Булевите флагове остават за съвместимост, но при празен график стават
    False — зелено вече не се получава даром.  Точното състояние е в
    `flag_states`.
    """
    оценим = bool(tasks)
    състояния = {}
    for име, стойност in флагове.items():
        if not isinstance(стойност, bool):
            continue
        if not оценим and име in _VACUOUS_WHEN_EMPTY:
            флагове[име] = False
            състояния[име] = "not_evaluated"
        else:
            състояния[име] = "pass" if стойност else "fail"
    флагове["flag_states"] = състояния
    флагове["evaluated"] = оценим
    return флагове


def _capacity_overloads(tasks: list[dict]) -> list[dict]:
    """Ресурси, натоварени над обявената си наличност, и кога.

    ОДИТ 10.08.2026: този флаг стоеше в списъка с твърди критерии, но НИКЪДЕ
    не се смяташе — тоест винаги беше `None` и нито един прогон не можеше да
    бъде обявен за чист.  Точното огледало на другия дефект от същия ден:
    `contract_scope_complete` беше написан така, че да не може да падне.

    И двата са един и същ клас грешка — гейт, чийто резултат не зависи от
    това, което проверява.
    """
    from src.schedule_builder import _load_resource_capacity

    config = _load_resource_capacity()
    capacity = config.get("capacity") or {}
    default = int(config.get("default", 2) or 2)

    load: dict[tuple[str, int], int] = defaultdict(int)
    for task in _leaves(tasks):
        start, end = _start(task), _end(task)
        if start is None or task.get("milestone"):
            continue
        for name in (task.get("resources") or []):
            for day in range(int(start), int(end) + 1):
                load[(str(name), day)] += 1

    overloads: list[dict] = []
    for (name, day), used in load.items():
        limit = int(capacity.get(name, default))
        if used > limit:
            overloads.append({"resource": name, "day": day,
                              "used": used, "limit": limit})
    overloads.sort(key=lambda o: (-(o["used"] - o["limit"]), o["day"]))
    return overloads


def _reachability(leaves, successors, terminal_ids) -> set[str]:
    """Кои листа имат път до някой финал."""
    memo: dict[str, bool] = {}

    def can_reach(task_id: str, seen: set[str]) -> bool:
        if task_id in memo:
            return memo[task_id]
        if task_id in terminal_ids:
            memo[task_id] = True
            return True
        if task_id in seen:
            return False
        seen.add(task_id)
        result = any(can_reach(nxt, seen) for nxt in successors.get(task_id, ()))
        memo[task_id] = result
        return result

    return {str(t.get("id")) for t in leaves
            if can_reach(str(t.get("id")), set())}


def _rollup_ok(tasks: list[dict]) -> bool:
    """Обобщаващата трябва да покрива децата си, не да им противоречи."""
    children: dict[str, list[dict]] = defaultdict(list)
    for task in tasks:
        parent = str(task.get("parent_id") or "")
        if parent:
            children[parent].append(task)

    for task in tasks:
        if not (task.get("is_summary") or task.get("type") == "summary"):
            continue
        kids = [k for k in children.get(str(task.get("id")), [])
                if _start(k) is not None]
        if not kids or _start(task) is None:
            continue
        if _start(task) > min(_start(k) for k in kids):
            return False
        if _end(task) < max(_end(k) for k in kids):
            return False
    return True


def _template_complete(packages, chains, tasks) -> bool:
    """Технологичните пакети носят ВСИЧКИ стъпки на веригата си.

    Одит 10.08.2026: „Този флаг трябва да проверява пълнотата на технологичния
    template само за package types, за които template е приложим.  Не трябва
    например липса на „Проектиране" в строителен-only търг да сваля целия run."

    Съгласни.  Проверяват се само веригите, които описват ФИЗИЧЕСКА работа по
    трасе — канализация, водопровод, настилка, кабели.  Договорните фази
    (проектиране, мобилизация, надзор, приемане) са обхват, не шаблон: те се
    създават детерминистично и наличието им се мери от
    `contract_scope_complete`.
    """
    packages = list(packages)
    if not chains:
        return False
    defined = chains.get("chains") or {}
    produced: dict[str, set[str]] = defaultdict(set)
    for task in tasks:
        parent = str(task.get("parent_id") or "")
        if task.get("chain_step"):
            produced[parent].add(str(task["chain_step"]))

    checked = 0
    for package in packages:
        key = getattr(package, "chain", "")
        chain = defined.get(key, {})
        if chain.get("wbs_root", "construction") != "construction":
            continue                      # договорна фаза — не е шаблон
        # ПРИКАЧЕНАТА РАБОТА има собствен, по-къс шаблон.  СВО, СКО, УО и
        # кожухът са операции ВЪРХУ участък, не участъци — от 13.08.2026 те
        # раждат само своите стъпки (`_attachment_scope`).  Без това уточнение
        # гейтът искаше от тях цялата верига и падаше точно защото дублирането
        # е премахнато: 14 от 30 прогона в серията от 14.08 се провалиха така.
        from src.work_package import effective_chain_steps

        expected = {str(s.get("key")) for s in effective_chain_steps(package, chain)}
        if not expected:
            continue
        checked += 1
        if not expected <= produced.get(getattr(package, "id", ""), set()):
            return False
    return checked > 0


# ---------------------------------------------------------------------------
# Едновременност (одит 13.08.2026, P0.3)
# ---------------------------------------------------------------------------


def concurrency_report(tasks: Iterable[dict]) -> dict[str, Any]:
    """Колко работа върви ЕДНОВРЕМЕННО и кой я задържа.

    ОДИТ 13.08.2026: „Броят leaf задачи вече е почти същият (486 срещу 513), но
    span-ът е 1.7× по-дълъг: човекът държи медиана 7 активни задачи и пик 10,
    ние — 2 и 2.  Не е нужно просто ‚още задачи'."

    Това коригира и нашия собствен извод от раздел 11.3 на брийфа, че целта е
    повече задачи.  Целта е повече ЕДНОВРЕМЕННИ задачи — а дали е постигната
    се вижда само ако се мери.  Затова тук няма твърдо зададен срок: числата
    се сравняват с еталона, а не с константа в кода.
    """
    листа = [t for t in _leaves(list(tasks))
             if not t.get("is_milestone") and t.get("type") != "milestone"]
    строителни = [t for t in листа
                  if str(t.get("phase") or "").lower() in ("", "construction")
                  or "строит" in str(t.get("phase") or "").lower()]
    редове = строителни or листа
    if not редове:
        return {"construction_leaf_count": 0, "construction_span_days": 0,
                "peak_active_leaf_tasks": 0, "median_active_leaf_tasks": 0,
                "average_active_leaf_tasks": 0.0, "evaluated": False}

    начало = min(int(t.get("start_day") or 0) for t in редове)
    край = max(int(t.get("end_day") or 0) for t in редове)
    активни: list[int] = []
    for ден in range(начало, край + 1):
        активни.append(sum(
            1 for t in редове
            if int(t.get("start_day") or 0) <= ден <= int(t.get("end_day") or 0)))

    активни_работни = [n for n in активни if n] or [0]
    return {
        "construction_leaf_count": len(редове),
        "construction_span_days": край - начало + 1,
        "peak_active_leaf_tasks": max(активни_работни),
        "median_active_leaf_tasks": round(statistics.median(активни_работни), 1),
        "average_active_leaf_tasks": round(statistics.fmean(активни_работни), 2),
        "idle_days": sum(1 for n in активни if n == 0),
        "evaluated": True,
    }


def concurrency_bottlenecks(tasks: Iterable[dict], top: int = 5) -> list[dict]:
    """Кои предшественици държат най-много работа зад себе си.

    Одиторът посочи конкретния случай: една задача за пътна основа с 41
    предшественика — тоест глобална бариера, представена като локална зона.
    """
    задачи = list(tasks)
    по_ид = {str(t.get("id")): t for t in задачи}
    брой: dict[str, int] = {}
    for t in задачи:
        for dep in (t.get("dependencies") or []):
            pred = str(dep.get("predecessor_id") if isinstance(dep, dict) else dep)
            брой[pred] = брой.get(pred, 0) + 1

    подредени = sorted(брой.items(), key=lambda kv: kv[1], reverse=True)[:top]
    return [{"task_id": ид, "name": str(по_ид.get(ид, {}).get("name", ""))[:80],
             "successors": n} for ид, n in подредени]


def widest_join(tasks: Iterable[dict]) -> dict[str, Any]:
    """Задачата с НАЙ-МНОГО предшественици — най-широкото събиране в графика."""
    най = {"task_id": "", "name": "", "predecessors": 0}
    for t in tasks:
        n = len(t.get("dependencies") or [])
        if n > най["predecessors"]:
            най = {"task_id": str(t.get("id")), "predecessors": n,
                   "name": str(t.get("name", ""))[:80]}
    return най
