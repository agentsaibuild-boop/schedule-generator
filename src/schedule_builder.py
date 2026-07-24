"""Schedule builder — constructs, validates, adjusts, and diffs schedule data."""

from __future__ import annotations

import copy
import logging
import re
from collections import defaultdict
from typing import Any, NamedTuple

import pandas as pd

from src.duration_calculator import (
    DEFAULT_MIN_DAYS,
    UNRESOLVED_CODES,
    calculate_task_duration,
)
from src.gantt_chart import day_to_date, generate_demo_schedule, get_type_label
from src.spatial import spatial_report

logger = logging.getLogger(__name__)

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
            pred, link_type, lag = dep, task_type, task_lag
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
                ids.append(dep)
        elif isinstance(dep, int) and not isinstance(dep, bool):
            ids.append(str(dep))
        elif isinstance(dep, dict):
            for key in ("predecessor_id", "id", "task_id", "uid"):
                value = dep.get(key)
                if isinstance(value, (str, int)) and not isinstance(value, bool):
                    ids.append(str(value))
                    break
    return ids


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
            tid = task.get("id", "?")
            name = task.get("name", "?")

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
                task["duration_source"] = "suggested"
                task["duration_status"] = result.code
                if old_duration is not None:
                    task["suggested_duration"] = old_duration
                task.pop("calculated_duration", None)

                skipped.append({
                    "id": tid, "name": name,
                    "reason": result.reason, "code": result.code,
                    "suggested_duration": old_duration,
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

        if changes and reschedule:
            resched = self.reschedule(updated)
            updated = resched["schedule"]
            warnings.extend(resched["warnings"])

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
            logger.warning(
                "%d задачи остават с НЕДОКАЗАНА продължителност (стойност от AI): %s",
                unresolved,
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
        task_by_id: dict[str, dict] = {t.get("id", ""): t for t in updated}

        cycle = self._detect_cycle(updated, task_by_id)
        if cycle:
            return {
                "schedule": updated,
                "warnings": [
                    f"Датите не са преизчислени — кръгова зависимост: {' → '.join(cycle)}."
                ],
                "shifted": [],
            }

        # --- Запомни оригиналните позиции и празнините по ребрата ---
        orig_start: dict[str, int] = {}
        orig_end: dict[str, int] = {}
        for task in updated:
            tid = task.get("id", "")
            start = self._as_int(task.get("start_day"), 1)
            orig_start[tid] = start
            end = task.get("end_day")
            if isinstance(end, (int, float)) and not isinstance(end, bool):
                orig_end[tid] = int(end)
            else:
                orig_end[tid] = start + max(self._as_int(task.get("duration"), 0), 1) - 1

        gaps: dict[tuple[str, str], int] = {}
        for task in updated:
            tid = task.get("id", "")
            for dep_id in dependency_ids(task):
                if dep_id in orig_end:
                    gaps[(dep_id, tid)] = orig_start[tid] - orig_end[dep_id] - 1

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
            deps = [d for d in dependency_ids(task) if d in new_end]

            if deps:
                start = max(new_end[d] + 1 + gaps.get((d, tid), 0) for d in deps)
            else:
                start = orig_start[tid]
            start = max(start, 1)

            duration = self._as_int(task.get("duration"), 0)
            end = start if duration <= 0 else start + duration - 1

            delta = start - orig_start[tid]
            if delta or end != orig_end[tid]:
                shifted.append(tid)

            # Поддейностите се местят със същата разлика.
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

    # ------------------------------------------------------------------
    # Helpers for deterministic durations
    # ------------------------------------------------------------------

    @classmethod
    def _task_end(cls, task: dict) -> int:
        """Краен ден на задача — от end_day, иначе изведен от start+duration."""
        end = task.get("end_day")
        if isinstance(end, (int, float)) and not isinstance(end, bool):
            return int(end)
        start = cls._as_int(task.get("start_day"), 0)
        return start + max(cls._as_int(task.get("duration"), 0), 1) - 1

    @staticmethod
    def _as_int(value: Any, default: int) -> int:
        """Cast to int, falling back to default for None/bool/non-numeric."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return default
        return int(value)

    @staticmethod
    def _topological_order(
        schedule: list[dict], task_by_id: dict[str, dict]
    ) -> list[str] | None:
        """Kahn topological sort over dependencies. None if not sortable."""
        indegree: dict[str, int] = {t.get("id", ""): 0 for t in schedule}
        successors: dict[str, list[str]] = defaultdict(list)

        for task in schedule:
            tid = task.get("id", "")
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
            if isinstance(end, (int, float)) and not isinstance(end, bool):
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

            tid = task.get("id", "")
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
            for field, value in (("start_day", start), ("end_day", end)):
                if value is None:
                    continue
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    errors.append(
                        f"Задача '{task.get('name')}' ({tid}) има {field} "
                        f"от невалиден тип: {value!r}."
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
                if duration < 0:
                    errors.append(
                        f"Задача '{task.get('name')}' ({tid}) има отрицателна "
                        f"продължителност ({duration})."
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
            tid = task.get("id", "")
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

        # --- Dependency violations, ПО ТИП на връзката ---
        #
        # Одит 2026-07-23: всички зависимости се проверяваха като FS.  Валидна
        # SS връзка (изкоп и полагане тръгват заедно — урок #15) се обявяваше
        # за грешка, а нарушения на SS/FF/SF минаваха незабелязано.
        for task in schedule:
            tid = task.get("id", "")
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
            tid = task.get("id", "")
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
            tid = task.get("id", "")
            name = task.get("name", "?")
            duration = self._as_int(task.get("duration"), 0)

            # --- Suspiciously long task ---
            if duration > 365:
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
            team_intervals[team].append((s, e, task.get("id", "?")))

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
                        f"едновременно (вкл. {id1} и {', '.join(overlap_ids[:3])})."
                    )
                    break  # one warning per team is enough

        # --- Large gap between predecessor and successor ---
        for task in schedule:
            tid = task.get("id", "")
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
        color: dict[str, int] = {t.get("id", ""): WHITE for t in schedule}
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
            task_by_id[task.get("id", "")] = task

        target = task_by_id.get(task_id)
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
            tid = task.get("id", "")
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
            tid = task.get("id", "")
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
            rows.append({
                "№": i + 1,
                "Дейност": task.get("name", "Без име"),
                "Тип": get_type_label(task.get("type", "")),
                "DN": task.get("diameter", "—"),
                "L(м)": task.get("length_m", "—"),
                "Екип": task.get("team", "—"),
                "Начало": day_to_date(start_day, start_date),
                "Край": day_to_date(end_day, start_date),
                "Дни": duration,
                "Критичен": "🔴" if task.get("is_critical") else "",
            })
        return pd.DataFrame(rows)
