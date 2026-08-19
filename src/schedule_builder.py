"""Schedule builder — constructs, validates, adjusts, and diffs schedule data."""

from __future__ import annotations

import copy
import json
import logging
import math
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, NamedTuple

import pandas as pd

from src.duration_calculator import (
    DEFAULT_MIN_DAYS,
    UNRESOLVED_CODES,
    calculate_task_duration,
)
from src.gantt_chart import day_to_date, generate_demo_schedule, get_type_label
from src.spatial import (
    DEFAULT_CREW_BUFFER_M,
    find_crew_collisions,
    spatial_report,
)

logger = logging.getLogger(__name__)

_CAPACITY_PATH = Path(__file__).resolve().parent.parent / "config" / "resource_capacity.json"
_capacity_cache: dict[str, Any] | None = None


def _load_resource_capacity() -> dict[str, Any]:
    """Наличният ресурс по вид — колко едновременни задачи може да поеме.

    Числата са РАЗУМНО ПОДРАЗБИРАНЕ, не измерване (виж бележката във файла).
    """
    global _capacity_cache
    if _capacity_cache is None:
        try:
            _capacity_cache = json.loads(_CAPACITY_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("resource_capacity.json не се чете (%s): %s",
                           _CAPACITY_PATH, exc)
            _capacity_cache = {"default": 1, "capacity": {}}
    return _capacity_cache


def _task_resources(task: dict, *, leveling_only: bool = False) -> list[str]:
    """Ресурсите, които задачата заема — съставът на бригадата плюс екипа.

    `leveling_only` изключва НАДЗОРНИТЕ роли.  Виж `_is_leveling_resource`.
    """
    names: list[str] = []
    for raw in task.get("resources") or []:
        name = str(raw).strip()
        if name and name not in names:
            names.append(name)
    team = str(task.get("team") or "").strip()
    if team and team != "—" and team not in names:
        names.append(team)
    if leveling_only:
        на_екипа = _per_crew_roles()
        names = [n for n in names
                 if _is_leveling_resource(n) and n not in на_екипа]
    return names



#: КОЙ Е БРИГАДАТА: тази за ОПЕРАЦИЯ (конвейер) или тази за УЧАСТЪК.
#:
#: Еталонът планира по участък — ЕВ1 прави целия си водопроводен клон.  Затова
#: изглежда, че и ние трябва.  Измерено обаче на 19.08.2026, форматираният КСС,
#: детерминистичен прогон:
#:
#:     модел                          срок  вода канал настилки
#:     операция + етапи, 4 фронта      690   411   423   518
#:     операция + истински участъци    724   342   379     —
#:     участък  + етапи, 4 фронта      878   590   552   706
#:     участък  + истински участъци    997   622   555     —
#:     ЧОВЕКЪТ                         780   190   480   644
#:
#: ПРЕМЕРЕНО НАНОВО 19.08.2026, след като хората на екипа престанаха да се
#: броят за общообектов ресурс.  Дотогава бригадата на участък излизаше
#: по-лоша при всяка комбинация — но защото ограничението се броеше ДВА пъти:
#: веднъж като „бригадата е заета", втори път като „няма свободни общи
#: работници", при положение че всеки екип си има свои.
#:
#: С поправеното броене, при 32 участъка на верига:
#:
#:     модел                    срок  едновр  канал  вода  настилки
#:     по УЧАСТЪК, 6 екипа       719     5.3    460   544      547
#:     по УЧАСТЪК, 4 екипа      1032     4.0    694   852      860
#:     по ОПЕРАЦИЯ, 4 фронта     562     6.5    330   351      390
#:     ЧОВЕКЪТ                   780     6.9    480   190      644
#:
#: Бригадата на участък с шест екипа е ОРГАНИЗАЦИЯТА НА ЕТАЛОНА — ЕВ1 прави
#: целия си клон — и дава най-близкото до него: канал 460 срещу 480.
#: Затова тя е по подразбиране, а конвейерът остава за сравнение.
_ПО_ОПЕРАЦИЯ = os.getenv("CREW_PER_OPERATION", "0") not in ("0", "false")



def _headcount() -> dict[str, dict[str, int]]:
    """Колко души влизат в една задача и колко има на обекта.

    ИЗМЕРЕНО 19.08.2026 от еталонния график: `capacity` брои ЕДНОВРЕМЕННИ
    ЗАДАЧИ, а не хора — грешна мерна единица навсякъде, където бригадата е
    повече от един човек.  Еталонът записва Units на всяко назначение:

        Каналджия             3 на задача, 14 на обекта  (наш таван: 6 задачи)
        Строителен работник   3 на задача, 11 на обекта  (наш таван: 8 задачи)
        Товарен автомобил     1 на задача,  9 на обекта  (наш таван: 6 задачи)

    Тоест обектът има четиринайсет каналджии, а ние допускахме шест задачи,
    всяка уж с един каналджия.  Оттам идваше и 82-процентното чакане на
    водопроводните пакети: те стояха зад канализацията за техника, която в
    действителност стига.
    """
    конфиг = _load_resource_capacity() or {}
    блок = конфиг.get("headcount") or {}
    return {име: {"на_задача": max(1, int(v.get("на_задача") or 1)),
                  "налични": max(1, int(v.get("налични") or 1))}
            for име, v in блок.items() if isinstance(v, dict)}


def _occupancy_key(task: dict) -> str:
    """Кой ЗАЕМА ресурса — бригадата на участъка, не отделният ред от КСС.

    ИЗМЕРЕНО 19.08.2026, и това е грешка в МЕРНАТА ЕДИНИЦА, не в числата.
    `resource_capacity.json` брои ЕДНОВРЕМЕННИ ЗАДАЧИ и е извлечен от
    еталонния график, където една задача е един участък, една стъпка.  Нашите
    задачи са по-ситни: разделяме стъпката на по една задача за КСС ред, за да
    може всяка да доказва своя ред.  Затова шестте слота на „Ръководител
    работна група" в еталона значат „шест УЧАСТЪКА вървят", а при нас значеха
    „шест РЕДА ОТ КСС вървят" — един и същи таван, дванайсет пъти по-малко
    напредък.

    Следствието се виждаше на око: задачи, чиито предшественици са готови на
    ден 239, тръгваха на ден 589 — 350 дни чакане за ресурс, който физически
    е свободен.  Един участък отнемаше 517 дни при 70 дни работа в него.

    Единицата е БРИГАДАТА ЗА ОПЕРАЦИЯ, минаваща по маршрута.  Строежът на
    линеен обект е поточна линия: багерът копае участък 1, после минава на 2,
    докато в 1 полагат тръбите; когато полагането мине в 2, в 1 засипват.
    Процесът не спира — всяка бригада върви напред и не чака съседната.

    Затова ключът е (мрежа, операция, бригада): всички изкопи на един фронт
    са ЕДНА багерна бригада, която ги прави един след друг, а полагането е
    друга бригада, която върви зад нея.  Вътре в участъка редът се пази от
    самите зависимости (изкоп → полагане → засипка), не от заетостта.

    Опитаните преди ключове и защо не стават:
      * задачата (както беше) — 12 паралелни реда от КСС заемаха 12
        ръководители, тоест 12 бригади там, където има 6;
      * (участък, бригада) — 32 бригади, всяка чакаща ред: 1047 дни;
      * бригадата сама — тя владее целия участък от трасирането до CCTV и
        не пуска, докато не свърши: 943 дни, при 63% чакане вътре.

    И ОБРАТНОТО СЪЩО Е ВЯРНО, иначе поправката става по-лоша от дефекта: щом
    бригадата е една, тя прави ЕДНО нещо в даден ден.  Първият опит само
    сподели слота и махна всичко, което държеше редовете подредени — излезе
    връх от 37 едновременни задачи при шест бригади, тоест една бригада на
    шест места наведнъж, и срокът падна на 496 дни.  Затова заемателят се
    брои и като собствен ресурс с капацитет 1.
    """
    бригада = str(task.get("crew_id") or task.get("team") or "").strip()
    операция = str(task.get("chain_step") or "").strip()
    мрежа = str(task.get("network") or "").strip()
    if _ПО_ОПЕРАЦИЯ and операция and бригада and бригада != "—":
        # Багерът не чака полагането: щом свърши изкопа на един участък,
        # минава на следващия, а зад него вървят тръбите и засипката.
        return f"{мрежа}|{операция}|{бригада}"
    if бригада and бригада != "—":
        return бригада
    # Задача без бригада (проектиране, приемане, надзор) заема сама себе си —
    # там паралелността се урежда от собствените ѝ ресурси.
    return f"~{_task_key(task)}"


def _per_crew_roles() -> frozenset[str]:
    """Хора, които принадлежат на ЕКИПА, не на обекта.

    Изпълнителят, 19.08.2026: „всеки екип, без значение В или К, си има свои
    общи работници, не ги делят".  Значи те не ограничават колко работа върви
    наведнъж на обекта — ограничението е броят екипи и общите МАШИНИ.

    Измерено защо има значение: `Общ работник` е 8 души по 2 на задача, тоест
    4 едновременни задачи за целия обект.  Едновременността ни стоеше на 4.9
    при 6.9 в еталона, на колкото и участъка да делим обекта, а водопроводът
    чакаше зад канализацията за работници, които са си негови.
    """
    конфиг = _load_resource_capacity() or {}
    блок = конфиг.get("per_crew") or {}
    return frozenset(str(r) for r in (блок.get("роли") or []))


from src.road_works import merged_into_level_of_effort  # noqa: E402


def _is_leveling_resource(name: str) -> bool:
    """Ограничава ли този ресурс колко работа може да върви едновременно.

    ОДИТ 14.08.2026: „Ръководител работна група е hard-leveling ресурс върху
    всичките 200 construction leaf tasks с MaxUnits=2, което превръща целия
    проект в глобален semaphore с максимум две едновременни задачи."

    Проверимо е и е точно така: 201 назначения при таван 2, докато Фронт 1 и
    Фронт 2 имат по 3 — тоест надзорна роля отменя логиката на фронтовете.
    Числата го затварят: 1672 задача-дни при капацитет 2 дават теоретичен
    минимум 836 дни, тоест еталонните 660 са недостижими по конструкция,
    независимо от мрежата.

    Ръководителят НАДЗИРАВА едновременна работа, не я изпълнява — той не е
    машина, която може да е само на едно място.  Затова надзорните роли излизат
    от твърдото изравняване: остават назначени и видими в графика, но
    ограничението идва от фронтовете, бригадите и техниката.

    Числото НЕ се вдига на око до 6, за да улучи 660 дни: колко ръководители
    има реално е организационен въпрос към изпълнителя, а не настройка, с която
    да се постигне желан срок.
    """
    config = _load_resource_capacity()
    надзорни = config.get("supervisory") or []
    return str(name).strip() not in {str(n).strip() for n in надзорни}

# Над този брой задачи DFS проверката за цикли не се изпълнява и графикът
# се ОТХВЪРЛЯ (fail-closed).  DFS е O(V+E), затова лимитът е висок — реален
# ВиК график рядко минава няколко хиляди задачи; над 20 000 се разделя.
_MAX_TASKS_FOR_CYCLE_CHECK = 20_000

# Cascade safety limit
_MAX_CASCADE_TASKS = 50

# Regex for extracting task IDs from Bulgarian text (e.g. В01, К03, МС01, П12)
_TASK_ID_RE = re.compile(r"[А-ЯA-Z]{1,3}\d{1,3}")


class DependencyLink(NamedTuple):
    """Връзка към предшественик — с тип и лаг, не само ID.

    Одит 2026-07-23: валидаторът извличаше само ID-то и проверяваше ВСИЧКО
    като FS.  Валидна SS връзка (двете задачи започват заедно) се обявяваше
    за грешка, а реални нарушения на SS/FF/SF минаваха незабелязано.
    """

    predecessor_id: str
    type: str = "FS"      # FS | SS | FF | SF
    lag_days: int = 0


_VALID_LINK_TYPES = frozenset({"FS", "SS", "FF", "SF"})


def _as_lag(value: Any) -> int:
    """Лаг в дни; всичко неразпознато е 0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


# Анотирана зависимост като низ: "V03 (SS+30)", "К-1 [FS-2]", "A2(SS)" — някои
# модели (Sonnet, 2026-08) вграждат тип/лаг В ID-то вместо в отделни полета.
# Без парсване валидаторът търси задача с ID „V03 (SS+30)" (няма) и валиден
# график изглежда счупен.  Хващаме ID + опционален тип (FS/SS/FF/SF) + лаг.
_DEP_ANNOT_RE = re.compile(
    r"^\s*(?P<id>.+?)\s*[\(\[]\s*"
    r"(?P<type>FS|SS|FF|SF)?\s*(?P<lag>[+-]\s*\d+)?\s*[\)\]]\s*$",
    re.IGNORECASE,
)


def parse_dependency_token(raw: Any) -> tuple[str, str, int]:
    """Разбий низ-зависимост на (base_id, type, lag).

    „V03" → ("V03", "", 0);  „V03 (SS+30)" → ("V03", "SS", 30);
    „К-1 [FS-2]" → ("К-1", "FS", -2).  Без анотация типът/лагът са празни/0 и
    извикващият ползва своите подразбирания.
    """
    s = str(raw).strip()
    m = _DEP_ANNOT_RE.match(s)
    if not m:
        return s, "", 0
    base = m.group("id").strip()
    typ = (m.group("type") or "").upper()
    lag_raw = (m.group("lag") or "").replace(" ", "")
    lag = int(lag_raw) if lag_raw else 0
    return base, typ, lag


def dependency_links(task: dict) -> list[DependencyLink]:
    """Извлечи зависимостите СЪС семантиката им.

    Приема същите формати като `dependency_ids`, плюс типа и лага, когато
    са налични.  Задачите, зададени като низ, наследяват `dependency_type` и
    `lag_days` от самата задача (старият формат от `enrich_for_msproject`).
    """
    links: list[DependencyLink] = []
    task_type = str(task.get("dependency_type") or "FS").upper()
    task_lag = _as_lag(task.get("lag_days"))

    for dep in task.get("dependencies") or []:
        if isinstance(dep, dict):
            pred = None
            for key in ("predecessor_id", "id", "task_id", "uid"):
                value = dep.get(key)
                if isinstance(value, (str, int)) and not isinstance(value, bool):
                    pred = str(value)
                    break
            if pred is None:
                continue
            link_type = str(dep.get("type") or dep.get("dependency_type") or "FS").upper()
            lag = _as_lag(dep.get("lag_days", dep.get("lag")))
        elif isinstance(dep, str) and dep:
            # Анотираните низове („V03 (SS+30)") носят СВОЙ тип/лаг; иначе
            # наследяват тези на задачата.
            base, a_type, a_lag = parse_dependency_token(dep)
            if not base:
                continue
            pred = base
            link_type = a_type or task_type
            lag = a_lag if a_type else task_lag
        elif isinstance(dep, int) and not isinstance(dep, bool):
            pred, link_type, lag = str(dep), task_type, task_lag
        else:
            continue

        if link_type not in _VALID_LINK_TYPES:
            link_type = "FS"
        links.append(DependencyLink(pred, link_type, lag))

    return links


def dependency_ids(task: dict) -> list[str]:
    """Извлечи ID-тата на предшествениците, независимо от формата.

    Одит 2026-07-23: `export_xml` поддържа зависимости и като речници
    ({"predecessor_id": "T1", "type": "SS", "lag_days": 3}), а валидаторът
    приемаше само низове и гърмеше с `TypeError: unhashable type: 'dict'`.
    Тоест структура, поддържана от експорта, сриваше проверката преди него.

    Приема: "T1" | {"predecessor_id": "T1"} | {"id": "T1"} | {"task_id": "T1"}
    Игнорира всичко останало — валидаторът не бива да пада заради вход.
    """
    ids: list[str] = []
    for dep in task.get("dependencies") or []:
        if isinstance(dep, str):
            if dep:
                base = parse_dependency_token(dep)[0]
                if base:
                    ids.append(base)
        elif isinstance(dep, int) and not isinstance(dep, bool):
            ids.append(str(dep))
        elif isinstance(dep, dict):
            for key in ("predecessor_id", "id", "task_id", "uid"):
                value = dep.get(key)
                if isinstance(value, (str, int)) and not isinstance(value, bool):
                    ids.append(str(value))
                    break
    return ids


def _task_key(task: dict) -> str:
    """ID на задачата като СТРИНГ — за ключове и сравнения със зависимости.

    `dependency_ids` връща низове, а `task['id']` може да е int (DeepSeek
    генерира числови ID).  Без тази нормализация всяко търсене
    `dep_id in task_by_id` / `new_end[dep_id]` се проваля и ВАЛИДЕН график
    изглежда счупен — валидаторът рапортуваше „зависи от несъществуващо ID"
    за реални предшественици (одит 2026-08, точка 2: числовите ID даваха
    false-positive „фантомни зависимости").
    """
    return str(task.get("id", "")).strip()


class ScheduleBuilder:
    """Builds and validates schedule data structures."""

    def build_from_ai_response(self, ai_data: dict) -> list[dict]:
        """Build a schedule task list from an AI response.

        Args:
            ai_data: Dict with schedule data from AI processor.

        Returns:
            List of task dicts in the standard schedule format.
        """
        if not ai_data:
            return generate_demo_schedule()

        tasks = ai_data.get("tasks", [])
        if tasks:
            return tasks

        return generate_demo_schedule()

    # ------------------------------------------------------------------
    # Deterministic durations (P2 — арифметиката излиза от промпта)
    # ------------------------------------------------------------------

    def recompute_durations(
        self,
        schedule: list[dict],
        *,
        min_days: int = DEFAULT_MIN_DAYS,
        apply_terrain: bool = False,
        reschedule: bool = True,
        config: dict | None = None,
    ) -> dict[str, Any]:
        """Преизчисли продължителностите детерминистично, вместо да вярваш на LLM-а.

        За всяка задача, за която `duration_calculator` може да сметне
        СИГУРНО (тръбна дейност с DN + материал + дължина, или СРС/РШ по
        бройки), продължителността се заменя.  Всичко останало запазва
        стойността от AI-я и се отчита в `skipped` — модулът не гадае.

        Args:
            schedule: Списък задачи от генерирания график.
            min_days: Минимум работни дни за параметрична дейност.
            apply_terrain: Дали да се приложи теренният коефициент.  По
                подразбиране False — ефективните норми вече са теренно
                калибрирани (виж `duration_calculator.pipe_duration`).
            reschedule: Дали да презакачи start_day/end_day след промяната.
            config: Готов конфиг с производителности (за тестове).

        Returns:
            Dict с ключове:
                schedule: Нов списък задачи (оригиналът не се мутира).
                changes: Списък от {id, name, old, new, delta, reason}.
                skipped: Списък от {id, name, reason}.
                warnings: Списък предупреждения.
                summary: {total, recomputed, unchanged, skipped,
                          old_total_duration, new_total_duration}.
        """
        if not schedule:
            return {
                "schedule": [],
                "changes": [],
                "skipped": [],
                "warnings": [],
                "summary": {
                    "total": 0, "recomputed": 0, "unchanged": 0, "skipped": 0,
                    "old_total_duration": 0, "new_total_duration": 0,
                },
            }

        updated = copy.deepcopy(schedule)
        changes: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        warnings: list[str] = []
        unchanged = 0

        old_total = self._total_duration(updated)

        for task in updated:
            tid = _task_key(task) or "?"
            name = task.get("name", "?")

            # Обобщаващият ред НЯМА собствена продължителност — тя е сборът на
            # децата му (`_rollup_ok`).  Досега „СТРОИТЕЛСТВО" и всеки пакетен
            # ред влизаха в `skipped` като NOT_PARAMETRIC и надуваха отчета за
            # недоказани продължителности с по един ред на пакет — при 38
            # пакета това е 39 несъществуващи липси в число, което отива при
            # одитора (проба 10.08.2026).
            if str(task.get("type", "")).lower() == "summary":
                continue

            try:
                result = calculate_task_duration(
                    task,
                    min_days=min_days,
                    apply_terrain=apply_terrain,
                    config=config,
                )
            except (ValueError, TypeError) as exc:
                skipped.append({"id": tid, "name": name, "reason": f"грешка: {exc}"})
                continue

            if result.days is None:
                # НЕРАЗРЕШЕНА продължителност.  Одит 2026-07-23: досега тук
                # стойността на LLM-а просто оставаше в `duration` и ставаше
                # неразличима от изчислена.  Сега произходът се записва явно,
                # за да може експортът и човекът да знаят кое е доказано.
                old_duration = task.get("duration")
                # НЕДОКАЗАНА не значи „от AI".  При пакетния път стойността
                # идва от `median_days` на технологичната верига, извлечена от
                # ЕТАЛОННИЯ ЧОВЕШКИ график (46 канализационни, 23 водопроводни
                # участъка) — това е най-силното доказателство, което имаме
                # извън нормите, и не бива да се слива с гадаене на модел.
                #
                # ПРОБА 10.08.2026: тук произходът се затриваше на „suggested"
                # за всичко и логът съобщаваше „стойност от AI" за 130–220
                # задачи на прогон, от които нито една не беше от AI.
                prior_source = str(task.get("duration_source") or "")
                task["duration_source"] = (
                    "chain_template" if prior_source == "chain_template"
                    else "suggested")
                task["duration_status"] = result.code
                if old_duration is not None:
                    task["suggested_duration"] = old_duration
                task.pop("calculated_duration", None)
                # ДОГОВОР (одит 2026-08 v30): canonical моделът е с ЦЕЛИ работни
                # дни.  Закръгля се НАГОРЕ ТУК само ПОЛОЖИТЕЛНА КРАЙНА дробна
                # стойност.  Отрицателна/NaN/Inf НЕ се пипат — оставят се на
                # валидатора да ги отхвърли (иначе -0.5 → ceil → 0 → milestone,
                # т.е. невъзможна стойност минаваше authoritative gate-а).
                if (isinstance(old_duration, float) and not isinstance(old_duration, bool)
                        and math.isfinite(old_duration) and old_duration > 0
                        and old_duration != int(old_duration)):
                    task["duration"] = math.ceil(old_duration)
                    warnings.append(
                        f"Задача '{name}' ({tid}): дробна продължителност "
                        f"{old_duration} → {task['duration']} (цели работни дни).")

                skipped.append({
                    "id": tid, "name": name,
                    "reason": result.reason, "code": result.code,
                    "suggested_duration": old_duration,
                    "duration_source": task["duration_source"],
                })
                continue

            old_duration = task.get("duration")

            # Доказана стойност — записва се и отделно от `duration`, за да
            # оцелее, ако някой по-надолу пипне `duration`.
            task["calculated_duration"] = result.days
            task["duration_source"] = "calculated"
            task["duration_status"] = result.code
            task.pop("suggested_duration", None)

            if old_duration == result.days:
                unchanged += 1
                continue

            task["duration"] = result.days
            changes.append({
                "id": tid,
                "name": name,
                "old": old_duration,
                "new": result.days,
                "delta": (result.days - old_duration)
                if isinstance(old_duration, (int, float)) and not isinstance(old_duration, bool)
                else None,
                "reason": result.reason,
            })

        # Каскадата на датите (start/end по графа на зависимостите) е ИНВАРИАНТ на
        # коректността — не зависи от това дали продължителност е сменена.  Проба
        # 2026-08-04 (реален ВиК проект): при график, чиито дейности са изцяло
        # NOT_PARAMETRIC (изкоп/извозване/настилки — няма норма → `changes` празно),
        # каскадата се пропускаше и грешните AI-дати оцеляваха (напр. извозване
        # почва ден 2, а изкопът-предшественик свършва ден 148 → 16 грешки в gate-а).
        # Сега каскадата се пуска ВИНАГИ при reschedule=True (идемпотентна: вече
        # консистентни дати остават същите; задачи без предшественик пазят AI-датата).
        if reschedule:
            resched = self.reschedule(updated)
            updated = resched["schedule"]
            warnings.extend(resched["warnings"])

        # Гаранция за `end_day` (проба на реален проект 2026-07-31): reschedule попълва
        # датите само когато има променени продължителности.  Непараметрична
        # задача (изкоп/извозване/настилки — няма норма, нищо не се променя)
        # оставаше с end_day=None → чупи експорта (XML/PDF искат край).  Тук
        # всяка задача без край го получава от start_day + duration.
        for task in updated:
            if task.get("end_day") is None:
                sd = self._as_int(task.get("start_day"), 1)
                dur = self._as_int(task.get("duration"), 0)
                task["end_day"] = sd if dur <= 0 else sd + dur - 1

        new_total = self._total_duration(updated)

        # Разбивка по причина — за да се вижда КАКВО липсва, не само колко.
        by_code: dict[str, int] = {}
        for entry in skipped:
            code = entry.get("code", "UNKNOWN")
            by_code[code] = by_code.get(code, 0) + 1

        unresolved = sum(
            count for code, count in by_code.items() if code in UNRESOLVED_CODES
        )
        if unresolved:
            # Разделено по ПРОИЗХОД, а не само по код: „от еталонния график" и
            # „от модела" не бива да се четат като едно и също число.
            from_template = sum(
                1 for e in skipped
                if e.get("code") in UNRESOLVED_CODES
                and e.get("duration_source") == "chain_template")
            logger.warning(
                "%d задачи остават с НЕДОКАЗАНА продължителност "
                "(%d от еталонния график, %d от модела): %s",
                unresolved, from_template, unresolved - from_template,
                ", ".join(f"{c}={n}" for c, n in sorted(by_code.items())),
            )

        return {
            "schedule": updated,
            "changes": changes,
            "skipped": skipped,
            "warnings": warnings,
            "summary": {
                "total": len(updated),
                "recomputed": len(changes),
                "unchanged": unchanged,
                "skipped": len(skipped),
                "unresolved": unresolved,
                "by_code": by_code,
                "old_total_duration": old_total,
                "new_total_duration": new_total,
            },
        }

    def reschedule(self, schedule: list[dict]) -> dict[str, Any]:
        """Преизчисли start_day/end_day след промяна на продължителности.

        Запазва ПРАЗНИНАТА (lag) на всяко ребро такава, каквато я е замислил
        AI-ят — включително отрицателна (SS припокриване, урок #15) и
        големите умишлени lag-ове (настилки SS+30, урок #36).  Така промяна
        в продължителност мести наследниците, без да изтрива логиката.

        При кръгова зависимост връща графика непроменен с предупреждение.

        Args:
            schedule: Списък задачи (не се мутира).

        Returns:
            Dict с schedule, warnings, shifted (списък ID-та с нови дати).
        """
        if not schedule:
            return {"schedule": [], "warnings": [], "shifted": []}

        updated = copy.deepcopy(schedule)
        task_by_id: dict[str, dict] = {_task_key(t): t for t in updated}

        cycle = self._detect_cycle(updated, task_by_id)
        if cycle:
            return {
                "schedule": updated,
                "warnings": [
                    f"Датите не са преизчислени — кръгова зависимост: {' → '.join(cycle)}."
                ],
                "shifted": [],
            }

        # --- Запомни оригиналните позиции ---
        orig_start: dict[str, int] = {}
        orig_end: dict[str, int] = {}
        for task in updated:
            tid = _task_key(task)
            start = self._as_int(task.get("start_day"), 1)
            orig_start[tid] = start
            end = task.get("end_day")
            # NaN/Inf НЕ бива да гърми reschedule с int() (одит 2026-08 v30) —
            # пада към изчисление от продължителността; валидаторът после
            # отхвърля не-крайните стойности fail-closed.
            if (isinstance(end, (int, float)) and not isinstance(end, bool)
                    and math.isfinite(end)):
                orig_end[tid] = int(end)
            else:
                orig_end[tid] = start + max(self._as_int(task.get("duration"), 0), 1) - 1

        # --- Офсет по ребро = ДЕКЛАРИРАНИЯТ lag, не разликата между старите дати ---
        #
        # Одит #3 + проба 2026-07-24 (реален проект): досега офсетът
        # се извеждаше от AI-датите (`succ.start - pred.end - 1`).  Така всяка
        # AI грешка в датите се превръщаше в СКРИТ lag: реалният проект даде
        # T15 (Засипване) да започва в деня, в който T14 (Полагане) свършва →
        # изведен офсет -1 → графикът невалиден (FS иска +1).
        #
        # Сега офсетът е ФОРМАЛНОТО `lag_days` от връзката.  Умишлените празнини
        # (настилки) трябва да са ДЕКЛАРИРАН lag (промптът вече иска SS+30, урок
        # #36), а не разлика в дати.  Произволна AI празнина вече не оцелява.
        #
        # NB: началото на задачите БЕЗ предшественик още идва от AI-датата
        # (`orig_start`) — заключването на самите дати е отделна отворена
        # находка (#2), не се пипа тук.
        edges: dict[tuple[str, str], tuple[str, int]] = {}
        for task in updated:
            tid = _task_key(task)
            for link in dependency_links(task):
                dep_id = link.predecessor_id
                if dep_id not in orig_end:
                    continue
                edges[(dep_id, tid)] = (link.type, link.lag_days)

        # --- Топологичен ред (Kahn) ---
        order = self._topological_order(updated, task_by_id)
        if order is None:
            return {
                "schedule": updated,
                "warnings": ["Датите не са преизчислени — не може да се подреди топологично."],
                "shifted": [],
            }

        new_start: dict[str, int] = {}
        new_end: dict[str, int] = {}
        shifted: list[str] = []

        for tid in order:
            task = task_by_id[tid]
            duration = self._as_int(task.get("duration"), 0)
            span = max(duration, 1) - 1  # end = start + span

            # Всяка връзка налага най-ранно начало според ТИПА си + ДЕКЛАРИРАНИЯ lag.
            candidates: list[int] = []
            for dep_id in dependency_ids(task):
                if dep_id not in new_end:
                    continue
                link_type, lag = edges.get((dep_id, tid), ("FS", 0))
                if link_type == "SS":
                    # succ.start = pred.start + lag
                    candidates.append(new_start[dep_id] + lag)
                elif link_type == "FF":
                    # succ.end = pred.end + lag → start = end - span
                    candidates.append(new_end[dep_id] + lag - span)
                elif link_type == "SF":
                    # succ.end = pred.start + lag → start = end - span
                    candidates.append(new_start[dep_id] + lag - span)
                else:  # FS
                    candidates.append(new_end[dep_id] + 1 + lag)

            start = max(candidates) if candidates else orig_start[tid]
            start = max(start, 1)
            end = start if duration <= 0 else start + span

            delta = start - orig_start[tid]
            if delta or end != orig_end[tid]:
                shifted.append(tid)

            if delta:
                for sub in task.get("sub_activities", []) or []:
                    sub["start_day"] = self._as_int(sub.get("start_day"), 1) + delta
                    if isinstance(sub.get("end_day"), (int, float)):
                        sub["end_day"] = int(sub["end_day"]) + delta

            task["start_day"] = start
            task["end_day"] = end
            new_start[tid] = start
            new_end[tid] = end

        return {"schedule": updated, "warnings": [], "shifted": shifted}

    def roll_up_summaries(self, schedule: list[dict]) -> dict[str, Any]:
        """Разтегни обобщаващите задачи по децата им.

        ОДИТ 2026-08-07: обобщаващата има нулева продължителност и затова
        оставаше на ден 1, докато децата ѝ течаха месеци напред.  Експортът
        вече смята този сбор сам, но същото трябва да важи и в паметта —
        иначе Gantt-ът, таблицата и PDF-ът показват друго от MS Project.

        Returns:
            {schedule, adjusted} — `adjusted` са поправените обобщаващи.
        """
        if not schedule:
            return {"schedule": [], "adjusted": []}

        updated = copy.deepcopy(schedule)
        by_id = {_task_key(t): t for t in updated}
        children: dict[str, list[str]] = defaultdict(list)
        for task in updated:
            parent = str(task.get("parent_id") or "").strip()
            tid = _task_key(task)
            if parent and parent in by_id and parent != tid:
                children[parent].append(tid)

        adjusted: list[dict] = []
        seen: set[str] = set()

        def span(tid: str) -> tuple[int, int]:
            task = by_id[tid]
            kids = children.get(tid, [])
            if not kids or tid in seen:
                start = self._as_int(task.get("start_day"), 1)
                return start, self._task_end(task)
            seen.add(tid)
            spans = [span(k) for k in kids]
            start, end = min(s for s, _ in spans), max(e for _, e in spans)
            old = (self._as_int(task.get("start_day"), 1), self._task_end(task))
            if old != (start, end):
                adjusted.append({"id": tid, "name": task.get("name"),
                                 "from": old, "to": (start, end)})
                task["start_day"] = start
                task["end_day"] = end
                task["duration"] = max(1, end - start + 1)
            return start, end

        for tid in list(children):
            span(tid)
        return {"schedule": updated, "adjusted": adjusted}

    # ------------------------------------------------------------------
    # Ресурсно изравняване (2026-08-07)
    # ------------------------------------------------------------------

    def level_resources(
        self,
        schedule: list[dict],
        *,
        capacity: dict[str, int] | None = None,
        default_capacity: int | None = None,
        horizon_days: int = 3650,
        pull_in: bool = False,
    ) -> dict[str, Any]:
        """Разсрочи задачите така, че да не искат повече ресурс, отколкото има.

        ОДИТ 2026-08-07: ресурсите бяха само ИМЕНА.  Един ръководител излизаше
        назначен на 66 задачи, от които 22 стартират в един и същи ден; един
        багер — на 16 едновременни.  Мрежата беше коректна, а графикът
        физически неизпълним, защото нищо не ограничаваше паралелизацията.

        Алгоритъмът е сериен (serial SGS): задачите се минават в топологичен
        ред и всяка се слага на НАЙ-РАННИЯ ден, на който едновременно:
          * всички предшественици са изпълнени (зависимостите са ненарушими);
          * всеки от ресурсите ѝ има свободен капацитет за целия ѝ период.

        Зависимостите никога не се нарушават — изравняването само ОТЛАГА.
        Обобщаващите задачи и milestone-ите не заемат ресурс.

        Args:
            schedule: Списък задачи (не се мутира).
            capacity: {име на ресурс: брой едновременни задачи}.
            default_capacity: За ресурс извън таблицата.
            horizon_days: Предпазен таван при търсене на свободен ден.
            pull_in: Позволи задача да се ВЪРНЕ по-рано, ако зависимостите и
                ресурсите ѝ го позволяват.

                ИЗМЕРЕНО 17.08.2026: подът на всяка задача беше собствената ѝ
                дата отпреди изравняването, тоест закъснение веднъж влязло, не
                излизаше.  На детерминистичния прогон това направи 65 дни ПЪЛНА
                ПАУЗА (737–801): екзекутивната документация чакаше надзора,
                който преди изравняването свършваше на 801, а след него — на
                736.  Ресурсът ѝ беше свободен през цялото време, всичките ѝ
                24 предшественика — готови.  Никой не я върна.

                Затова се минава втори път СЛЕД като обхватът на надзора е
                наложен: задача със свързани предшественици тръгва от тях, а не
                от старата си дата.  Задача БЕЗ разрешени предшественици си
                остава на място — нейната дата е решение отвън (мобилизация,
                договорен старт), не остатък от предишно смятане.

        Returns:
            {schedule, shifted, warnings, peak} — `peak` е върховото
            натоварване по ресурс СЛЕД изравняването.
        """
        if not schedule:
            return {"schedule": [], "shifted": [], "warnings": [], "peak": {}}

        config = _load_resource_capacity()
        table = dict(config.get("capacity") or {})
        #: Изрично подадените тавани НАДДЕЛЯВАТ над извлечения състав: който
        #: вика с `capacity={...}`, казва „толкова едновременни, точка" — и
        #: правилото за хора не бива да го отменя мълчаливо.
        изрични = set(capacity or {})
        if capacity:
            table.update(capacity)
        fallback = (default_capacity if default_capacity is not None
                    else int(config.get("default", 1)))

        updated = copy.deepcopy(schedule)
        by_id: dict[str, dict] = {_task_key(t): t for t in updated}

        order = self._topological_order(updated, by_id)
        if order is None:
            return {"schedule": updated, "shifted": [],
                    "warnings": ["Ресурсите не са изравнени — графикът не може "
                                 "да се подреди топологично."], "peak": {}}

        edges: dict[tuple[str, str], tuple[str, int]] = {}
        for task in updated:
            tid = _task_key(task)
            for link in dependency_links(task):
                edges[(link.predecessor_id, tid)] = (link.type, link.lag_days)

        # Кои бригади държат ресурса на този ден — множество, не брояч:
        # две задачи на един участък не са две бригади.
        usage: dict[tuple[str, int], set[str]] = defaultdict(set)
        #: Колко ДУШИ от ресурса са заети на този ден.  Мерната единица е
        #: човек, не слот за задача — виж `_headcount`.
        глави: dict[tuple[str, int], int] = defaultdict(int)
        състав = _headcount()
        #: Кои дни бригадата вече е заета — тя прави едно нещо наведнъж.
        busy: dict[str, set[int]] = defaultdict(set)
        new_start: dict[str, int] = {}
        new_end: dict[str, int] = {}
        shifted: list[dict] = []
        warnings: list[str] = []

        for tid in order:
            task = by_id[tid]
            duration = self._as_int(task.get("duration"), 0)
            span = max(duration, 1) - 1
            original = self._as_int(task.get("start_day"), 1)

            # Подът е СОБСТВЕНАТА дата само когато не пренареждаме.  При
            # `pull_in` задача със СВЪРЗАНИ предшественици тръгва от тях —
            # виж защо в описанието на аргумента.
            resolved = [d for d in dependency_ids(task) if d in new_end]
            earliest = 1 if (pull_in and resolved) else original
            for dep_id in resolved:
                link_type, lag = edges.get((dep_id, tid), ("FS", 0))
                if link_type == "SS":
                    earliest = max(earliest, new_start[dep_id] + lag)
                elif link_type == "FF":
                    earliest = max(earliest, new_end[dep_id] + lag - span)
                elif link_type == "SF":
                    earliest = max(earliest, new_start[dep_id] + lag - span)
                else:
                    earliest = max(earliest, new_end[dep_id] + 1 + lag)
            earliest = max(earliest, 1)

            resources = _task_resources(task, leveling_only=True)
            # НЕПРЕКЪСНАТАТА ДЕЙНОСТ не се изравнява твърдо, както и надзорът:
            # тя описва присъствие на обекта през целия строеж, а не такт на
            # производство.  Ако участваше, 595-дневната ѝ заетост щеше да
            # изтласка всичко, което дели ресурс с нея.  Виж `road_works`.
            consumes = (bool(resources) and duration > 0
                        and not self._is_summary(task)
                        and not merged_into_level_of_effort(task))
            occupant = _occupancy_key(task)

            start = earliest
            if consumes:
                limit = earliest + horizon_days
                while start <= limit:
                    дни = range(start, start + span + 1)
                    свободна_бригада = all(day not in busy[occupant] for day in дни)
                    има_ресурс = True
                    for r in resources:
                        сведение = None if r in изрични else състав.get(r)
                        for day in дни:
                            if сведение:
                                # ХОРА: задачата взима `на_задача` души от
                                # `налични`.  Същият заемател не плаща втори
                                # път — той вече ги държи.
                                ако_вземе = глави[(r, day)] + (
                                    0 if occupant in usage[(r, day)]
                                    else сведение["на_задача"])
                                ок = ако_вземе <= сведение["налични"]
                            else:
                                ок = (occupant in usage[(r, day)]
                                      or len(usage[(r, day)])
                                      < table.get(r, fallback))
                            if not ок:
                                има_ресурс = False
                                break
                        if not има_ресурс:
                            break
                    if свободна_бригада and има_ресурс:
                        break
                    start += 1
                else:
                    warnings.append(
                        f"Задача '{task.get('name')}' ({tid}) не намери свободен "
                        f"ресурс в {horizon_days} дни — оставена на ден {earliest}.")
                    start = earliest

            end = start if duration <= 0 else start + span
            if consumes:
                for day in range(start, end + 1):
                    busy[occupant].add(day)
                for r in resources:
                    сведение = None if r in изрични else състав.get(r)
                    for day in range(start, end + 1):
                        нов = occupant not in usage[(r, day)]
                        usage[(r, day)].add(occupant)
                        if сведение and нов:
                            глави[(r, day)] += сведение["на_задача"]

            if start != original:
                shifted.append({"id": tid, "name": task.get("name"),
                                "from": original, "to": start})
            task["start_day"] = start
            task["end_day"] = end
            new_start[tid] = start
            new_end[tid] = end

        peak: dict[str, int] = defaultdict(int)
        for (resource, _), заематели in usage.items():
            peak[resource] = max(peak[resource], len(заематели))

        return {"schedule": updated, "shifted": shifted, "warnings": warnings,
                "peak": dict(peak), "capacity": table, "default_capacity": fallback}

    # ------------------------------------------------------------------
    # CPM — критичен път и резерв (2026-08-06)
    # ------------------------------------------------------------------

    def compute_critical_path(
        self, schedule: list[dict], *, deadline_day: int | None = None
    ) -> dict[str, Any]:
        """Обратен ход: късни дати, пълен резерв и критичен път.

        СЪПОСТАВКА С ЕТАЛОН (2026-08-06): в програмния график НИТО ЕДНА от 204
        задачи не беше критична — не защото мрежата е с резерв, а защото
        `is_critical` НИКОЙ не го пишеше.  Полето се четеше от Gantt-а, PDF-а и
        XML-а ([export_xml.py], `Critical`), но нямаше кой да го сметне.
        Тоест „критичен път" в продукта беше декорация.

        Обратният ход ОГЛЕДАЛНО повтаря семантиката на `reschedule` — иначе
        резервът би бил спрямо друга мрежа, а не спрямо тази, по която са
        сметнати датите:

            FS: succ.start >= pred.end + 1 + lag   →  LF_pred = LS_succ - 1 - lag
            SS: succ.start >= pred.start + lag     →  LS_pred = LS_succ - lag
            FF: succ.end   >= pred.end + lag       →  LF_pred = LF_succ - lag
            SF: succ.end   >= pred.start + lag     →  LS_pred = LF_succ - lag

        Обобщаващите (summary) задачи не получават собствен резерв — те са
        сбор, а не работа; маркират се като критични, ако критично е някое
        тяхно дете, точно както прави MS Project.

        Args:
            schedule: Списък задачи (не се мутира).
            deadline_day: Договорен краен ден.  Ако е зададен и е ПО-РАНЕН от
                края на графика, резервът става отрицателен — това е реално
                закъснение спрямо договора, не грешка в сметката.

        Returns:
            {schedule, critical, critical_count, project_finish, total_float,
             warnings}
        """
        if not schedule:
            return {"schedule": [], "critical": [], "critical_count": 0,
                    "project_finish": 0, "warnings": []}

        updated = copy.deepcopy(schedule)
        by_id: dict[str, dict] = {_task_key(t): t for t in updated}

        cycle = self._detect_cycle(updated, by_id)
        if cycle:
            return {"schedule": updated, "critical": [], "critical_count": 0,
                    "project_finish": 0,
                    "warnings": [f"Критичен път не е смятан — кръгова "
                                 f"зависимост: {' → '.join(cycle)}."]}

        order = self._topological_order(updated, by_id)
        if order is None:
            return {"schedule": updated, "critical": [], "critical_count": 0,
                    "project_finish": 0,
                    "warnings": ["Критичен път не е смятан — графикът не може "
                                 "да се подреди топологично."]}

        # --- Ранни дати: както са в графика (форуърдът е `reschedule`) ---
        span: dict[str, int] = {}
        early_finish: dict[str, int] = {}
        early_start: dict[str, int] = {}
        for tid in order:
            task = by_id[tid]
            duration = self._as_int(task.get("duration"), 0)
            sp = max(duration, 1) - 1
            start = self._as_int(task.get("start_day"), 1)
            span[tid] = sp
            early_start[tid] = start
            early_finish[tid] = start if duration <= 0 else start + sp

        # --- Наследници по ребра (типът и лагът са на страната на наследника) ---
        successors: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
        for task in updated:
            tid = _task_key(task)
            for link in dependency_links(task):
                if link.predecessor_id in by_id:
                    successors[link.predecessor_id].append(
                        (tid, link.type, link.lag_days))

        finish = max(early_finish.values(), default=1)
        project_finish = int(deadline_day) if deadline_day else finish

        # --- Обратен ход ---
        late_finish: dict[str, int] = {}
        late_start: dict[str, int] = {}
        for tid in reversed(order):
            duration = self._as_int(by_id[tid].get("duration"), 0)
            sp = span[tid]
            candidates: list[int] = []
            for succ_id, link_type, lag in successors.get(tid, []):
                if succ_id not in late_start:
                    continue
                if link_type == "SS":
                    candidates.append(late_start[succ_id] - lag + sp)
                elif link_type == "FF":
                    candidates.append(late_finish[succ_id] - lag)
                elif link_type == "SF":
                    candidates.append(late_finish[succ_id] - lag + sp)
                else:  # FS
                    candidates.append(late_start[succ_id] - 1 - lag)

            lf = min(candidates) if candidates else project_finish
            late_finish[tid] = lf
            late_start[tid] = lf if duration <= 0 else lf - sp

        # --- Резерв и маркиране ---
        critical: list[str] = []
        floats: dict[str, int] = {}
        for task in updated:
            tid = _task_key(task)
            total_float = late_finish[tid] - early_finish[tid]
            floats[tid] = total_float
            task["total_float"] = total_float
            task["late_start"] = late_start[tid]
            task["late_finish"] = late_finish[tid]
            if not self._is_summary(task):
                task["is_critical"] = total_float <= 0
                if total_float <= 0:
                    critical.append(tid)

        # Обобщаващите се маркират по децата — те са сбор, не работа.
        for task in updated:
            if not self._is_summary(task):
                continue
            tid = _task_key(task)
            kids = [t for t in updated
                    if str(t.get("parent_id") or "").strip() == tid]
            task["is_critical"] = any(k.get("is_critical") for k in kids)

        return {
            "schedule": updated,
            "critical": critical,
            "critical_count": len(critical),
            "project_finish": project_finish,
            "total_float": floats,
            "warnings": [],
        }

    @staticmethod
    def _is_summary(task: dict) -> bool:
        """Дали задачата е обобщаваща (има деца) — не носи собствена работа."""
        return bool(
            task.get("is_summary") or task.get("_has_children")
            or task.get("sub_activities")
            or str(task.get("type", "")).lower() in ("summary", "wbs", "group")
        )

    # ------------------------------------------------------------------
    # Пространствен ремонт (2026-08) — сериализирай РЕАЛНИТЕ сблъсъци
    # ------------------------------------------------------------------

    def resolve_spatial_conflicts(
        self,
        schedule: list[dict],
        *,
        buffer_m: float = DEFAULT_CREW_BUFFER_M,
        max_rounds: int = 6,
    ) -> dict[str, Any]:
        """Разреши пространствените сблъсъци, вместо само да ги обявиш за грешка.

        ЗАЩО (2026-08, реален прогон): генерацията слагаше два екипа на едни и
        същи метри в застъпващи се дни.  Гейтът го хващаше правилно → графикът
        е `invalid` → няма изход.  Молбата към модела „не се застъпвай" не е
        проверима; преместването във времето е.

        Ремонтът е ДЕТЕРМИНИСТИЧЕН и КОНСЕРВАТИВЕН: за всяка двойка задачи,
        които реално делят метри в едни и същи дни, се добавя FS връзка
        (по-ранната → по-късната) и датите се преизчисляват.  Тоест втората
        бригада ИЗЧАКВА първата на същия участък — най-безобидното решение.

        Какво НЕ прави:
          - не пипа нарушения на БУФЕРА (`kind == "buffer"`) — те са
            технологично изискване, не физически сблъсък, и остават warning;
          - не свързва задача с неин родител/потомък (йерархия, не бригади);
          - не добавя връзка, която затваря цикъл — такава двойка се връща
            като `unresolved` и гейтът пак я обявява за грешка.

        Всяка добавена връзка се връща в `added_links` — промяната е авторска
        намеса в AI графика и трябва да се ПОКАЗВА, не да се случва тихо.

        Args:
            schedule: Списък задачи (не се мутира).
            buffer_m: Буферът за откриване (само за докладване на сблъсъка).
            max_rounds: Таван на итерациите — след преместване може да се
                появи нов сблъсък надолу по оста.

        Returns:
            {schedule, added_links, unresolved, rounds}
        """
        if not schedule:
            return {"schedule": [], "added_links": [], "unresolved": [], "rounds": 0}

        updated = copy.deepcopy(schedule)
        added_links: list[dict[str, Any]] = []
        handled: set[tuple[str, str]] = set()
        rounds = 0

        for _ in range(max_rounds):
            collisions = [c for c in find_crew_collisions(updated, buffer_m)
                          if c.get("kind") == "overlap"]
            if not collisions:
                break
            rounds += 1
            by_id = {_task_key(t): t for t in updated}
            progress = False

            for collision in collisions:
                first, second = self._serialization_order(
                    by_id.get(str(collision["task_a"])),
                    by_id.get(str(collision["task_b"])),
                )
                if first is None or second is None:
                    continue
                fid, sid = _task_key(first), _task_key(second)
                pair = (fid, sid)
                if pair in handled or (sid, fid) in handled:
                    continue
                if self._is_hierarchy_pair(first, second, by_id):
                    handled.add(pair)
                    continue
                if fid in dependency_ids(second):
                    # Вече са свързани и пак се застъпват — връзката е SS/FF
                    # или лагът е отрицателен.  Не я пренаписваме: това е
                    # умисъл на модела, гейтът ще го каже.
                    handled.add(pair)
                    continue

                deps = list(second.get("dependencies") or [])
                second["dependencies"] = deps + [
                    {"predecessor_id": fid, "type": "FS", "lag_days": 0}
                ]
                if self._detect_cycle(updated, {_task_key(t): t for t in updated}):
                    second["dependencies"] = deps
                    handled.add(pair)
                    continue

                handled.add(pair)
                progress = True
                added_links.append({
                    "predecessor": fid, "successor": sid,
                    "alignment": collision.get("alignment", ""),
                    "overlap_m": collision.get("overlap_m", 0.0),
                    "days": collision.get("days"),
                    "reason": "spatial_overlap",
                })

            if not progress:
                break

            result = self.reschedule(updated)
            if result["warnings"]:
                # Не могат да се преизчислят датите (цикъл/топология) — спираме
                # тук и оставяме гейта да се произнесе върху каквото има.
                break
            updated = result["schedule"]

        unresolved = [c for c in find_crew_collisions(updated, buffer_m)
                      if c.get("kind") == "overlap"]
        return {
            "schedule": updated,
            "added_links": added_links,
            "unresolved": unresolved,
            "rounds": rounds,
        }

    def link_networks(
        self,
        schedule: list[dict],
        networks: dict[str, list[str]],
        *,
        order: list[str] | None = None,
        lag_days: int = 12,
    ) -> dict[str, Any]:
        """Свържи ПАРАЛЕЛНИТЕ мрежи по реда „вода → канал → пътни".

        ЖИВ ПРОГОН 2026-08-06: частите се генерират независимо и всяка започва
        от ден 1 — водопровод, канализация, ЕЛ и пътна тръгват в един и същи
        ден, а възстановяването на настилка предхожда изкопите под нея.
        Правило #74 (урок #11) казва точно обратното: Rolling Wave — вода →
        канал → пътни с 10-12 дни закъснение, а Правило #75 — пътните не
        завършват преди канализацията.

        Реализация (SS с лаг, не FS): следващата мрежа ТРЪГВА `lag_days` след
        началото на предната — вълните се застъпват, както е на терен, вместо
        да се редят една след друга.  Плюс FF връзка канал→пътни, за да не
        приключат пътните преди канализацията.

        Args:
            schedule: Списък задачи (не се мутира).
            networks: {ключ на мрежа: [id-та на задачи]}.
            order: Редът на мрежите; по подразбиране ["В", "К", "П"].
            lag_days: Закъснението на вълната в дни.

        Returns:
            {schedule, added_links, skipped}
        """
        order = order or ["В", "К", "П"]
        present = [key for key in order if networks.get(key)]
        if len(present) < 2:
            return {"schedule": list(schedule), "added_links": [], "skipped": []}

        updated = copy.deepcopy(schedule)
        by_id = {_task_key(t): t for t in updated}
        added: list[dict] = []
        skipped: list[dict] = []

        def _tasks_of(key: str) -> list[dict]:
            return [by_id[tid] for tid in networks.get(key, []) if tid in by_id]

        def _roots(tasks: list[dict]) -> list[dict]:
            ids = {_task_key(t) for t in tasks}
            return [t for t in tasks
                    if not any(d in ids for d in dependency_ids(t))]

        def _add(pred: dict, succ: dict, link_type: str, lag: int, why: str) -> None:
            pid, sid = _task_key(pred), _task_key(succ)
            if pid == sid or pid in dependency_ids(succ):
                return
            deps = list(succ.get("dependencies") or [])
            succ["dependencies"] = deps + [
                {"predecessor_id": pid, "type": link_type, "lag_days": lag}]
            if self._detect_cycle(updated, {_task_key(t): t for t in updated}):
                succ["dependencies"] = deps
                skipped.append({"predecessor": pid, "successor": sid, "reason": "cycle"})
                return
            added.append({"predecessor": pid, "successor": sid,
                          "type": link_type, "lag_days": lag, "reason": why})

        for earlier, later in zip(present, present[1:]):
            lead = _tasks_of(earlier)
            follow = _tasks_of(later)
            if not lead or not follow:
                continue
            anchor = min(lead, key=lambda t: (self._as_int(t.get("start_day"), 1),
                                              _task_key(t)))
            for root in _roots(follow):
                _add(anchor, root, "SS", lag_days, "rolling_wave")

        # Правило #75: пътните не завършват преди канализацията.
        if "К" in present and "П" in present:
            sewer, road = _tasks_of("К"), _tasks_of("П")
            if sewer and road:
                last_sewer = max(sewer, key=lambda t: (self._task_end(t), _task_key(t)))
                last_road = max(road, key=lambda t: (self._task_end(t), _task_key(t)))
                _add(last_sewer, last_road, "FF", 0, "road_not_before_sewer")

        if added:
            result = self.reschedule(updated)
            if not result["warnings"]:
                updated = result["schedule"]
        return {"schedule": updated, "added_links": added, "skipped": skipped}

    @classmethod
    def _serialization_order(
        cls, a: dict | None, b: dict | None
    ) -> tuple[dict | None, dict | None]:
        """Коя от двете задачи минава първа — детерминистично, без гадаене.

        По-ранното начало води; при равно начало — по-ранният край; при
        пълно равенство — по ID, за да е повторяемо.
        """
        if a is None or b is None:
            return (None, None)

        def key(task: dict) -> tuple[int, int, str]:
            return (cls._as_int(task.get("start_day"), 1),
                    cls._task_end(task), _task_key(task))

        return (a, b) if key(a) <= key(b) else (b, a)

    @staticmethod
    def _is_hierarchy_pair(a: dict, b: dict, by_id: dict[str, dict]) -> bool:
        """Дали двете задачи са в отношение родител↔потомък (по `parent_id`).

        Обобщаваща задача и нейна подзадача естествено делят метри и дни —
        това не е сблъсък на бригади и не бива да се сериализира.
        """
        def ancestors(task: dict) -> set[str]:
            seen: set[str] = set()
            current = task
            for _ in range(20):        # таван срещу счупена йерархия
                pid = current.get("parent_id")
                pid = str(pid).strip() if pid is not None else ""
                if not pid or pid in seen:
                    break
                seen.add(pid)
                current = by_id.get(pid, {})
            return seen

        return _task_key(a) in ancestors(b) or _task_key(b) in ancestors(a)

    # ------------------------------------------------------------------
    # Helpers for deterministic durations
    # ------------------------------------------------------------------

    @classmethod
    def _task_end(cls, task: dict) -> int:
        """Краен ден на задача — от end_day, иначе изведен от start+duration."""
        end = task.get("end_day")
        # NaN/Inf НЕ бива да гърми с int() (одит 2026-08 v30) — пада към
        # изчисление от продължителността; валидаторът вече е маркирал
        # не-крайната стойност като грешка fail-closed.
        if (isinstance(end, (int, float)) and not isinstance(end, bool)
                and math.isfinite(end)):
            return int(end)
        start = cls._as_int(task.get("start_day"), 0)
        return start + max(cls._as_int(task.get("duration"), 0), 1) - 1

    @staticmethod
    def _as_int(value: Any, default: int) -> int:
        """Cast to int; default за None/bool/нечислово/НЕ-крайно (NaN/Inf).

        Одит 2026-08 v30: без isfinite проверката `int(nan)`/`int(inf)` вдигаше
        ValueError и сваляше reschedule (а с него цялото преизчисляване) — вместо
        да остави валидатора да отхвърли стойността fail-closed.
        """
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return default
        if not math.isfinite(value):
            return default
        return int(value)

    @staticmethod
    def _topological_order(
        schedule: list[dict], task_by_id: dict[str, dict]
    ) -> list[str] | None:
        """Kahn topological sort over dependencies. None if not sortable."""
        indegree: dict[str, int] = {_task_key(t): 0 for t in schedule}
        successors: dict[str, list[str]] = defaultdict(list)

        for task in schedule:
            tid = _task_key(task)
            for dep_id in dependency_ids(task):
                if dep_id in indegree:
                    successors[dep_id].append(tid)
                    indegree[tid] += 1

        queue = [tid for tid, deg in indegree.items() if deg == 0]
        order: list[str] = []

        while queue:
            tid = queue.pop(0)
            order.append(tid)
            for succ in successors.get(tid, []):
                indegree[succ] -= 1
                if indegree[succ] == 0:
                    queue.append(succ)

        return order if len(order) == len(indegree) else None

    @classmethod
    def _total_duration(cls, schedule: list[dict]) -> int:
        """Span from earliest start to latest end, in days."""
        if not schedule:
            return 0
        min_start = None
        max_end = None
        for task in schedule:
            start = cls._as_int(task.get("start_day"), 1)
            end = task.get("end_day")
            if (isinstance(end, (int, float)) and not isinstance(end, bool)
                    and math.isfinite(end)):
                end = int(end)
            else:
                end = start + max(cls._as_int(task.get("duration"), 0), 1) - 1
            min_start = start if min_start is None else min(min_start, start)
            max_end = end if max_end is None else max(max_end, end)
        if min_start is None or max_end is None or max_end < min_start:
            return 0
        return max_end - min_start + 1

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_schedule(self, schedule: list[dict]) -> dict[str, Any]:
        """Validate a schedule for errors and warnings.

        Checks performed:
          ERRORS (make the schedule invalid):
            - Empty schedule
            - Missing task name
            - Duplicate task IDs
            - Negative start_day or duration
            - Duration / end_day mismatch
            - Dependency on non-existent ID
            - Circular dependencies (DFS, skipped if >1000 tasks)
            - Task starts before predecessor finishes (FS violation)
            - Sub-activity outside parent bounds
            - Total schedule duration <= 0

          WARNINGS (informational):
            - Task duration > 365 days
            - Team assigned to >2 overlapping tasks
            - Task with no dependency, no parent, and start_day > 1
            - water_pipe/sewer task without diameter
            - Gap > 30 days between predecessor and successor

        Args:
            schedule: List of task dicts.

        Returns:
            Dict with 'valid' bool, 'errors' list, 'warnings' list.
        """
        errors: list[str] = []
        warnings: list[str] = []

        if not schedule:
            errors.append("Графикът е празен.")
            return {"valid": False, "errors": errors, "warnings": warnings}

        # Build lookup maps
        task_by_id: dict[str, dict] = {}
        ids_seen: set[str] = set()

        for i, task in enumerate(schedule):
            # --- Basic checks ---
            if not task.get("name"):
                errors.append(f"Задача #{i + 1} няма име.")

            tid = _task_key(task)
            if tid in ids_seen:
                errors.append(f"Дублирано ID: {tid}")
            ids_seen.add(tid)
            task_by_id[tid] = task

            start = task.get("start_day", 0)
            duration = task.get("duration", 0)
            end = task.get("end_day")

            # Одит 2026-07-23: `start_day: "утре"` сваляше валидатора с
            # TypeError и с него ЦЯЛОТО генериране — вместо да върне грешка.
            # Типът се проверява ПРЕДИ всяко сравнение.
            # Числов fail-closed договор (одит 2026-08 v30): освен типа, се
            # отхвърлят НЕ-крайни (NaN/Inf) и ДРОБНИ start_day/end_day — иначе
            # exporter-ът ги int()-ва тихо и canonical ≠ export.
            for field, value in (("start_day", start), ("end_day", end)):
                if value is None:
                    continue
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    errors.append(
                        f"Задача '{task.get('name')}' ({tid}) има {field} "
                        f"от невалиден тип: {value!r}."
                    )
                elif not math.isfinite(value):
                    errors.append(
                        f"Задача '{task.get('name')}' ({tid}) има {field}, "
                        f"което не е крайно число ({value})."
                    )
                elif isinstance(value, float) and value != int(value):
                    errors.append(
                        f"Задача '{task.get('name')}' ({tid}) има ДРОБЕН {field} "
                        f"({value}); приемат се само цели работни дни."
                    )

            if isinstance(start, (int, float)) and not isinstance(start, bool):
                if start < 0:
                    errors.append(
                        f"Задача '{task.get('name')}' ({tid}) има отрицателен начален ден."
                    )

            # Празно ID е твърда грешка, не изчаква втора празна задача,
            # за да се появи като „дублирано ID".
            if not str(tid).strip():
                errors.append(
                    f"Задача '{task.get('name')}' няма ID."
                )
            # Одит 2026-07-23: отрицателната продължителност беше само
            # предупреждение, тоест график с duration=-5 получаваше valid=True.
            # Това е невъзможна стойност, не спорна — грешка е.
            if isinstance(duration, (int, float)) and not isinstance(duration, bool):
                if not math.isfinite(duration):
                    errors.append(
                        f"Задача '{task.get('name')}' ({tid}) има продължителност, "
                        f"която не е крайно число ({duration})."
                    )
                elif duration < 0:
                    errors.append(
                        f"Задача '{task.get('name')}' ({tid}) има отрицателна "
                        f"продължителност ({duration})."
                    )
                # Договор (одит 2026-08 v28→v29): моделът работи с ЦЕЛИ работни
                # дни.  recompute_durations закръгля ПОЛОЖИТЕЛНИ дробни ПРЕДИ тук.
                # Ако дробна стойност все пак стигне валидацията, тя е ТВЪРДА
                # ГРЕШКА (fail-closed) — иначе canonical ≠ експортиран XML.
                elif isinstance(duration, float) and duration != int(duration):
                    errors.append(
                        f"Задача '{task.get('name')}' ({tid}) има дробна "
                        f"продължителност ({duration}); моделът приема само цели "
                        f"работни дни (пусни recompute_durations преди валидация)."
                    )
            elif duration is not None:
                errors.append(
                    f"Задача '{task.get('name')}' ({tid}) има продължителност "
                    f"от невалиден тип: {duration!r}."
                )

            # --- Duration / end_day consistency ---
            # Сравненията стават само след като типовете са потвърдени —
            # иначе низ в което и да е от трите полета сваля валидатора и
            # заедно с него цялото генериране.
            numeric = (
                isinstance(duration, (int, float)) and not isinstance(duration, bool)
                and isinstance(start, (int, float)) and not isinstance(start, bool)
                and isinstance(end, (int, float)) and not isinstance(end, bool)
            )
            if numeric and duration > 0:
                expected_end = start + duration - 1
                if end != expected_end:
                    errors.append(
                        f"Задача '{task.get('name')}' ({tid}): "
                        f"end_day={end} ≠ start_day({start}) + duration({duration}) - 1 = {expected_end}."
                    )

        # --- Dependency existence ---
        for task in schedule:
            tid = _task_key(task)
            for dep_id in dependency_ids(task):
                if dep_id not in task_by_id:
                    errors.append(
                        f"Задача '{task.get('name')}' ({tid}) зависи от "
                        f"несъществуващо ID: {dep_id}."
                    )

        # --- Circular dependencies (DFS) ---
        #
        # Одит 2026-07-24: лимитът беше 1000 и над него проверката се
        # ПРОПУСКАШЕ с предупреждение — цикъл в 1001 задачи минаваше за
        # валиден (fail-OPEN).  DFS е O(V+E); 1000 не е мащаб, който оправдава
        # това.  Лимитът е вдигнат далеч над всеки реален график, а над него
        # графикът се ОТХВЪРЛЯ, не се одобрява (fail-CLOSED): непроверена
        # логика не е доказана логика.
        if len(schedule) <= _MAX_TASKS_FOR_CYCLE_CHECK:
            cycle = self._detect_cycle(schedule, task_by_id)
            if cycle:
                errors.append(f"Кръгова зависимост: {' → '.join(cycle)}.")
        else:
            errors.append(
                f"Графикът има {len(schedule)} задачи (над лимита "
                f"{_MAX_TASKS_FOR_CYCLE_CHECK}) — проверката за кръгови "
                "зависимости не може да се изпълни, затова графикът НЕ е "
                "потвърден. Разделете го на етапи."
            )

        # --- Повредени тип/лаг на зависимост → ТВЪРДА ГРЕШКА ---
        #
        # Одит 2026-07-24 v5, точка 9: `dependency_links` свежда непознат тип
        # (напр. "BAD") тихо до FS и нечислов лаг до 0.  Това е удобно за
        # робастен експорт, но за ВАЛИДАТОРА е мълчаливо превръщане на
        # повредени данни в различно инженерно решение.  Тук се хваща изрично:
        # повреденият вход прави графика невалиден, а не друг график.
        for task in schedule:
            tid = _task_key(task)
            for dep in task.get("dependencies") or []:
                if not isinstance(dep, dict):
                    continue
                raw_type = dep.get("type") or dep.get("dependency_type")
                if raw_type is not None and str(raw_type).upper() not in _VALID_LINK_TYPES:
                    errors.append(
                        f"Задача ({tid}): невалиден тип зависимост "
                        f"'{raw_type}' — допустими са FS, SS, FF, SF."
                    )
                raw_lag = dep.get("lag_days", dep.get("lag"))
                if raw_lag is not None and (
                    isinstance(raw_lag, bool)
                    or not isinstance(raw_lag, (int, float))
                ):
                    errors.append(
                        f"Задача ({tid}): нечислов лаг '{raw_lag}' в зависимост."
                    )
            # Нивото на задачата също носи dependency_type (стар формат).
            raw_task_type = task.get("dependency_type")
            if raw_task_type is not None and str(raw_task_type).upper() not in _VALID_LINK_TYPES:
                errors.append(
                    f"Задача ({tid}): невалиден dependency_type "
                    f"'{raw_task_type}' — допустими са FS, SS, FF, SF."
                )

        # --- Dependency violations, ПО ТИП на връзката ---
        #
        # Одит 2026-07-23: всички зависимости се проверяваха като FS.  Валидна
        # SS връзка (изкоп и полагане тръгват заедно — урок #15) се обявяваше
        # за грешка, а нарушения на SS/FF/SF минаваха незабелязано.
        for task in schedule:
            tid = _task_key(task)
            start = self._as_int(task.get("start_day"), 0)
            end = self._task_end(task)

            for link in dependency_links(task):
                pred = task_by_id.get(link.predecessor_id)
                if pred is None:
                    continue  # already reported above
                pred_start = self._as_int(pred.get("start_day"), 0)
                pred_end = self._task_end(pred)
                label = f"'{pred.get('name')}' ({link.predecessor_id})"
                lag = link.lag_days

                if link.type == "SS":
                    # Наследникът не бива да започва преди предшественика (+лаг)
                    if start < pred_start + lag:
                        errors.append(
                            f"Задача '{task.get('name')}' ({tid}) [SS] започва ден "
                            f"{start}, но предшественик {label} започва ден "
                            f"{pred_start}" + (f" + лаг {lag}д" if lag else "") + "."
                        )
                elif link.type == "FF":
                    if end < pred_end + lag:
                        errors.append(
                            f"Задача '{task.get('name')}' ({tid}) [FF] завършва ден "
                            f"{end}, но предшественик {label} завършва ден "
                            f"{pred_end}" + (f" + лаг {lag}д" if lag else "") + "."
                        )
                elif link.type == "SF":
                    if end < pred_start + lag:
                        errors.append(
                            f"Задача '{task.get('name')}' ({tid}) [SF] завършва ден "
                            f"{end}, но предшественик {label} започва ден "
                            f"{pred_start}" + (f" + лаг {lag}д" if lag else "") + "."
                        )
                else:  # FS
                    if start <= pred_end + lag:
                        errors.append(
                            f"Задача '{task.get('name')}' ({tid}) започва ден {start}, "
                            f"но предшественик {label} завършва ден {pred_end}"
                            + (f" + лаг {lag}д" if lag else "") + "."
                        )

        # --- Sub-activity bounds ---
        for task in schedule:
            tid = _task_key(task)
            parent_start = task.get("start_day", 0)
            parent_end = task.get("end_day")
            if parent_end is None:
                dur = task.get("duration", 0)
                parent_end = parent_start + max(dur, 1) - 1

            for sub in task.get("sub_activities", []):
                sub_start = sub.get("start_day", 0)
                sub_end = sub.get("end_day")
                if sub_end is None:
                    sub_dur = sub.get("duration", 0)
                    sub_end = sub_start + max(sub_dur, 1) - 1

                if sub_start < parent_start or sub_end > parent_end:
                    errors.append(
                        f"Поддейност '{sub.get('name')}' на задача ({tid}) "
                        f"[{sub_start}–{sub_end}] излиза извън обхвата на "
                        f"родителя [{parent_start}–{parent_end}]."
                    )

        # Also check parent_id references at top level
        for task in schedule:
            pid = task.get("parent_id")
            if pid and pid not in task_by_id:
                warnings.append(
                    f"Задача '{task.get('name')}' ({task.get('id')}) "
                    f"сочи към несъществуващ parent_id: {pid}."
                )

        # --- Total duration ---
        if schedule:
            max_end = 0
            min_start = float("inf")
            for task in schedule:
                s = task.get("start_day", 0)
                e = task.get("end_day")
                if e is None:
                    dur = task.get("duration", 0)
                    e = s + max(dur, 1) - 1
                if s < min_start:
                    min_start = s
                if e > max_end:
                    max_end = e
            total_dur = max_end - min_start + 1 if max_end >= min_start else 0
            if total_dur <= 0:
                errors.append("Общата продължителност на графика е ≤ 0.")

        # ===============================================================
        # WARNINGS
        # ===============================================================

        for task in schedule:
            tid = _task_key(task)
            name = task.get("name", "?")
            duration = self._as_int(task.get("duration"), 0)

            # --- Suspiciously long task ---
            # Обобщаващата задача е СБОР, не работа: тя трае колкото децата си.
            # Жив прогон 14.08.2026: 2346 предупреждения, повечето „СТРОИТЕЛСТВО
            # трае 989 дни" — вярно и безполезно, а истинските предупреждения се
            # давеха в него.
            if duration > 365 and not self._is_summary(task):
                warnings.append(
                    f"Задача '{name}' ({tid}) има продължителност {duration} дни (>365)."
                )

            # --- Missing dependency for non-first task ---
            if (
                not task.get("dependencies")
                and not task.get("parent_id")
                and task.get("start_day", 0) > 1
            ):
                warnings.append(
                    f"Задача '{name}' ({tid}) няма предшественици и не е поддейност, "
                    f"но започва ден {task.get('start_day')}."
                )

            # --- Pipe/sewer without diameter ---
            if task.get("type") in ("water_pipe", "sewer") and not task.get("diameter"):
                warnings.append(
                    f"Задача '{name}' ({tid}) е тип '{task.get('type')}', "
                    f"но няма зададен DN (diameter)."
                )

        # --- Team overlap (>2 simultaneous tasks) ---
        team_intervals: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
        for task in schedule:
            team = task.get("team")
            if not team:
                continue
            s = task.get("start_day", 0)
            e = task.get("end_day")
            if e is None:
                dur = task.get("duration", 0)
                e = s + max(dur, 1) - 1
            team_intervals[team].append((s, e, _task_key(task) or "?"))

        for team, intervals in team_intervals.items():
            intervals.sort()
            for i, (s1, e1, id1) in enumerate(intervals):
                overlap_count = 0
                overlap_ids = []
                for j, (s2, e2, id2) in enumerate(intervals):
                    if i == j:
                        continue
                    if s2 <= e1 and e2 >= s1:
                        overlap_count += 1
                        overlap_ids.append(id2)
                # Одит 2026-07-23: прагът беше `>= 2`, тоест нужни бяха ТРИ
                # застъпени задачи.  За неделим екип конфликтът започва още
                # при втория едновременен ангажимент.
                if overlap_count >= 1:
                    warnings.append(
                        f"Екип '{team}' е назначен на {overlap_count + 1} задачи "
                        f"едновременно (вкл. {id1} и "
                        f"{', '.join(str(i) for i in overlap_ids[:3])})."
                    )
                    break  # one warning per team is enough

        # --- Large gap between predecessor and successor ---
        for task in schedule:
            tid = _task_key(task)
            start = task.get("start_day", 0)
            for dep_id in dependency_ids(task):
                pred = task_by_id.get(dep_id)
                if pred is None:
                    continue
                pred_end = pred.get("end_day")
                if pred_end is None:
                    pred_dur = pred.get("duration", 0)
                    pred_end = pred.get("start_day", 0) + max(pred_dur, 1) - 1
                gap = start - pred_end - 1
                if gap > 30:
                    warnings.append(
                        f"Между '{pred.get('name')}' ({dep_id}) и "
                        f"'{task.get('name')}' ({tid}) има празнина от {gap} дни."
                    )

        # --- ПРОСТРАНСТВЕНИ проверки (одит 2026-07-23, точка 3) ---
        #
        # Мрежовият график не може да ги направи: две задачи може да са
        # напълно коректни по зависимости и пак да изпращат два екипа на един
        # и същи метър в един и същи ден.
        #
        # Проверките са ДОБАВЪЧНИ — задачи без пикетаж просто не участват.
        spatial = spatial_report(schedule)

        for collision in spatial["collisions"]:
            crews = ""
            if collision["crew_a"] and collision["crew_b"]:
                crews = f" ({collision['crew_a']} и {collision['crew_b']})"
            pair = (
                f"'{collision['name_a']}' ({collision['task_a']}) и "
                f"'{collision['name_b']}' ({collision['task_b']})"
            )
            days = f"дни {collision['days'][0]}–{collision['days'][1]}"

            if collision["kind"] == "overlap":
                errors.append(
                    f"Пространствен конфликт по '{collision['alignment']}': {pair} "
                    f"работят на едни и същи {collision['overlap_m']:.0f}м през "
                    f"{days}{crews}."
                )
            else:
                # Допират се или са по-близо от изисквания буфер — технологично
                # изискване, не физически сблъсък.
                warnings.append(
                    f"Недостатъчно изоставане по '{collision['alignment']}': {pair} "
                    f"работят на по-малко от {collision['buffer_m']:.0f}м един от "
                    f"друг през {days}{crews}."
                )

        for violation in spatial["open_trench"]:
            warnings.append(
                f"Открит изкоп по '{violation['alignment']}' достига "
                f"{violation['open_m']:.0f}м на ден {violation['day']} "
                f"(лимит {violation['limit_m']:.0f}м) — задачи: "
                f"{', '.join(violation['tasks'][:4])}."
            )

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "spatial": {
                "covered": spatial["covered"],
                "total": spatial["total"],
                "alignments": spatial["alignments"],
            },
        }

    # ------------------------------------------------------------------
    # Cycle detection (DFS)
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_cycle(
        schedule: list[dict], task_by_id: dict[str, dict]
    ) -> list[str] | None:
        """Detect circular dependencies using DFS.

        Returns the cycle path (list of IDs) or None.
        """
        # Одит 2026-07-24: рекурсивният DFS блъскаше стека при над ~1000
        # задачи — точно затова лимитът беше нисък и цикълът минаваше за
        # валиден над него.  Итеративна реализация с явен стек няма това
        # ограничение и позволява fail-closed при голям график.
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {_task_key(t): WHITE for t in schedule}
        parent: dict[str, str | None] = {}

        def _cycle_path(start: str, back_to: str) -> list[str]:
            path = [back_to, start]
            node = start
            while node != back_to:
                node = parent.get(node, "")
                if not node or node == back_to:
                    break
                path.insert(1, node)
            path.append(back_to)
            return path

        for root in list(color.keys()):
            if color.get(root) != WHITE:
                continue
            # Стек от (tid, итератор по зависимостите).
            stack: list[tuple[str, Any]] = [
                (root, iter(dependency_ids(task_by_id.get(root, {}))))
            ]
            color[root] = GRAY

            while stack:
                tid, deps = stack[-1]
                advanced = False
                for dep_id in deps:
                    if dep_id not in color:
                        continue
                    if color[dep_id] == GRAY:
                        return _cycle_path(tid, dep_id)
                    if color[dep_id] == WHITE:
                        color[dep_id] = GRAY
                        parent[dep_id] = tid
                        stack.append(
                            (dep_id, iter(dependency_ids(task_by_id.get(dep_id, {}))))
                        )
                        advanced = True
                        break
                if not advanced:
                    color[tid] = BLACK
                    stack.pop()

        return None

    # ------------------------------------------------------------------
    # Adjust schedule (cascade)
    # ------------------------------------------------------------------

    def adjust_schedule(
        self, schedule: list[dict], changes: dict[str, Any]
    ) -> dict[str, Any]:
        """Apply adjustments to an existing schedule with optional cascading.

        Args:
            schedule: Current task list.
            changes: Dict with keys:
                task_id (str): ID of the task to change.
                field (str): Which field to modify.
                new_value: The new value for the field.
                cascade (bool): Whether to shift dependent tasks.

        Returns:
            Dict with keys:
                schedule: Updated task list.
                warnings: List of warning strings.
                affected_count: Number of tasks affected by cascade.
                error: Optional error string (only if task not found).
        """
        task_id = changes.get("task_id", "")
        field = changes.get("field", "")
        new_value = changes.get("new_value")
        cascade = changes.get("cascade", False)

        warnings: list[str] = []

        # Deep copy to avoid mutating the original
        updated = copy.deepcopy(schedule)

        # Build lookup
        task_by_id: dict[str, dict] = {}
        for task in updated:
            task_by_id[_task_key(task)] = task

        target = task_by_id.get(str(task_id).strip() if task_id is not None else "")
        if target is None:
            return {
                "schedule": schedule,
                "warnings": [],
                "affected_count": 0,
                "error": f"Задача с ID '{task_id}' не е намерена.",
            }

        old_end = target.get("end_day")
        if old_end is None:
            dur = target.get("duration", 0)
            old_end = target.get("start_day", 0) + max(dur, 1) - 1

        # Apply the field change
        target[field] = new_value

        affected_count = 1  # the target itself

        # Special handling for duration changes
        if field == "duration" and isinstance(new_value, (int, float)):
            new_duration = int(new_value)
            target["duration"] = new_duration
            target["end_day"] = target.get("start_day", 0) + new_duration - 1
            new_end = target["end_day"]

            # Check sub-activities
            for sub in target.get("sub_activities", []):
                sub_end = sub.get("end_day")
                if sub_end is None:
                    sub_dur = sub.get("duration", 0)
                    sub_end = sub.get("start_day", 0) + max(sub_dur, 1) - 1
                if sub_end > new_end:
                    warnings.append(
                        f"Поддейностите на {task_id} излизат извън новия обхват."
                    )
                    break

            # Cascade dependent tasks
            if cascade:
                delta = new_end - old_end
                if delta != 0:
                    cascaded = self._cascade_shift(
                        task_id, delta, task_by_id, updated
                    )
                    affected_count += len(cascaded)

                    if len(cascaded) > _MAX_CASCADE_TASKS:
                        warnings.append(
                            f"Каскадната промяна засяга {len(cascaded)} задачи. "
                            f"Потвърдете с 'Да, приложи каскадата'."
                        )
                        # Return schedule with only the target changed, no cascade
                        reverted = copy.deepcopy(schedule)
                        for t in reverted:
                            if t.get("id") == task_id:
                                t[field] = new_value
                                t["duration"] = new_duration
                                t["end_day"] = target.get("start_day", 0) + new_duration - 1
                                break
                        return {
                            "schedule": reverted,
                            "warnings": warnings,
                            "affected_count": 1,
                        }

        return {
            "schedule": updated,
            "warnings": warnings,
            "affected_count": affected_count,
        }

    @staticmethod
    def _cascade_shift(
        source_id: str,
        delta: int,
        task_by_id: dict[str, dict],
        schedule: list[dict],
    ) -> list[str]:
        """Shift all tasks that depend (transitively) on source_id by delta days.

        Returns list of shifted task IDs.
        """
        # Build reverse dependency map: task_id → list of successors
        successors: dict[str, list[str]] = defaultdict(list)
        for task in schedule:
            tid = _task_key(task)
            for dep_id in dependency_ids(task):
                successors[dep_id].append(tid)

        # BFS from source_id
        shifted: list[str] = []
        queue = list(successors.get(source_id, []))
        visited: set[str] = set()

        while queue:
            tid = queue.pop(0)
            if tid in visited:
                continue
            visited.add(tid)

            task = task_by_id.get(tid)
            if not task:
                continue

            task["start_day"] = task.get("start_day", 0) + delta
            if task.get("end_day") is not None:
                task["end_day"] = task["end_day"] + delta

            # Shift sub-activities too
            for sub in task.get("sub_activities", []):
                sub["start_day"] = sub.get("start_day", 0) + delta
                if sub.get("end_day") is not None:
                    sub["end_day"] = sub["end_day"] + delta

            shifted.append(tid)
            queue.extend(successors.get(tid, []))

        return shifted

    # ------------------------------------------------------------------
    # Modification diff (before vs after)
    # ------------------------------------------------------------------

    def validate_modification(
        self,
        before: list[dict],
        after: list[dict],
        requested_change: str,
    ) -> dict[str, Any]:
        """Compare a schedule before and after an AI modification.

        Detects unintended changes, missing/new tasks, and structural issues.

        Args:
            before: Schedule before modification.
            after: Schedule after modification.
            requested_change: The user's modification request text.

        Returns:
            Dict with:
                valid (bool), task_count_match (bool), ids_match (bool),
                unintended_changes (list), missing_tasks (list),
                new_tasks (list), warnings (list).
        """
        warnings: list[str] = []

        before_by_id = {t.get("id", f"__idx_{i}"): t for i, t in enumerate(before)}
        after_by_id = {t.get("id", f"__idx_{i}"): t for i, t in enumerate(after)}

        before_ids = set(before_by_id.keys())
        after_ids = set(after_by_id.keys())

        task_count_match = len(before) == len(after)
        ids_match = before_ids == after_ids

        missing_tasks = sorted(before_ids - after_ids)
        new_tasks = sorted(after_ids - before_ids)

        # Extract mentioned task IDs from the user request
        mentioned_ids = set(_TASK_ID_RE.findall(requested_change))

        # Build set of cascade-reachable IDs from mentioned tasks
        allowed_ids = set(mentioned_ids)
        self._expand_cascade_ids(allowed_ids, after)

        # Find unintended changes
        unintended_changes: list[dict[str, Any]] = []
        common_ids = before_ids & after_ids

        for tid in common_ids:
            old = before_by_id[tid]
            new = after_by_id[tid]
            changed_fields = self._diff_task(old, new)
            if changed_fields and tid not in allowed_ids:
                unintended_changes.append({
                    "id": tid,
                    "name": old.get("name", "?"),
                    "changed_fields": changed_fields,
                })

        if missing_tasks:
            warnings.append(
                f"AI-ят е премахнал {len(missing_tasks)} задачи: "
                f"{', '.join(missing_tasks[:5])}"
                + (f" (+{len(missing_tasks) - 5})" if len(missing_tasks) > 5 else "")
            )

        if new_tasks:
            warnings.append(
                f"AI-ят е добавил {len(new_tasks)} нови задачи: "
                f"{', '.join(new_tasks[:5])}"
                + (f" (+{len(new_tasks) - 5})" if len(new_tasks) > 5 else "")
            )

        if unintended_changes:
            ids_str = ", ".join(c["id"] for c in unintended_changes[:5])
            extra = len(unintended_changes) - 5
            warnings.append(
                f"Непредвидени промени в {len(unintended_changes)} задачи: {ids_str}"
                + (f" (+{extra})" if extra > 0 else "")
            )

        valid = not missing_tasks and not new_tasks and len(unintended_changes) == 0

        return {
            "valid": valid,
            "task_count_match": task_count_match,
            "ids_match": ids_match,
            "unintended_changes": unintended_changes,
            "missing_tasks": missing_tasks,
            "new_tasks": new_tasks,
            "warnings": warnings,
        }

    @staticmethod
    def _expand_cascade_ids(allowed_ids: set[str], schedule: list[dict]) -> None:
        """Expand allowed_ids to include all transitive dependents (cascade successors)."""
        successors: dict[str, list[str]] = defaultdict(list)
        for task in schedule:
            tid = _task_key(task)
            for dep_id in dependency_ids(task):
                successors[dep_id].append(tid)

        queue = list(allowed_ids)
        while queue:
            tid = queue.pop(0)
            for succ_id in successors.get(tid, []):
                if succ_id not in allowed_ids:
                    allowed_ids.add(succ_id)
                    queue.append(succ_id)

    @staticmethod
    def _diff_task(old: dict, new: dict) -> list[str]:
        """Return list of field names that differ between two task dicts.

        Ignores sub_activities for simplicity (compared structurally elsewhere).
        """
        fields_to_compare = (
            "name", "type", "phase", "start_day", "end_day", "duration",
            "team", "diameter", "length_m", "parent_id", "is_critical",
        )
        changed = []
        for f in fields_to_compare:
            if old.get(f) != new.get(f):
                changed.append(f)

        # Compare dependencies as sets
        old_deps = set(dependency_ids(old))
        new_deps = set(dependency_ids(new))
        if old_deps != new_deps:
            changed.append("dependencies")

        return changed

    # ------------------------------------------------------------------
    # DataFrame conversion
    # ------------------------------------------------------------------

    def to_dataframe(
        self, schedule: list[dict], start_date: str
    ) -> pd.DataFrame:
        """Convert schedule to a pandas DataFrame for table display.

        Args:
            schedule: List of task dicts.
            start_date: Project start date (ISO format).

        Returns:
            DataFrame with columns: №, Дейност, Тип, DN, L(м), Екип,
            Начало, Край, Дни, Критичен.
        """
        rows: list[dict[str, Any]] = []
        for i, task in enumerate(schedule):
            duration = task.get("duration", 0)
            start_day = task.get("start_day", 1)
            end_day = task.get("end_day", start_day + max(duration, 1) - 1)
            # Проба 2026-07-24: DN смесва int (500) и низ („Ф90; РЕ") в една
            # колона → Streamlit/pyarrow гърми при показване.  DN легитимно е
            # текст (съдържа диаметър+материал), затова се нормализира до низ;
            # L(м) остава числово или None, за да е чиста числова колона.
            _dn = task.get("diameter")
            _len = task.get("length_m")
            rows.append({
                "№": i + 1,
                "Дейност": task.get("name", "Без име"),
                "Тип": get_type_label(task.get("type", "")),
                "DN": "—" if _dn in (None, "") else str(_dn),
                "L(м)": f"{_len:g}" if isinstance(_len, (int, float)) else "—",
                "Екип": task.get("team", "—"),
                "Начало": day_to_date(start_day, start_date),
                "Край": day_to_date(end_day, start_date),
                "Дни": duration,
                "Критичен": "🔴" if task.get("is_critical") else "",
            })
        return pd.DataFrame(rows)
