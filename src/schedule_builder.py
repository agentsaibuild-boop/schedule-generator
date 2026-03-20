"""Schedule builder — constructs, validates, adjusts, and diffs schedule data."""

from __future__ import annotations

import copy
import logging
import re
from collections import defaultdict
from typing import Any

import pandas as pd

from src.gantt_chart import day_to_date, generate_demo_schedule, get_type_label

logger = logging.getLogger(__name__)

# Maximum task count before skipping expensive checks (circular deps)
_MAX_TASKS_FOR_CYCLE_CHECK = 1000

# Cascade safety limit
_MAX_CASCADE_TASKS = 50

# Regex for extracting task IDs from Bulgarian text (e.g. В01, К03, МС01, П12)
_TASK_ID_RE = re.compile(r"[А-ЯA-Z]{1,3}\d{1,3}")


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

            if start < 0:
                errors.append(
                    f"Задача '{task.get('name')}' ({tid}) има отрицателен начален ден."
                )
            if duration < 0:
                warnings.append(
                    f"Задача '{task.get('name')}' ({tid}) има отрицателна продължителност."
                )

            # --- Duration / end_day consistency ---
            if duration > 0 and end is not None:
                expected_end = start + duration - 1
                if end != expected_end:
                    errors.append(
                        f"Задача '{task.get('name')}' ({tid}): "
                        f"end_day={end} ≠ start_day({start}) + duration({duration}) - 1 = {expected_end}."
                    )

        # --- Dependency existence ---
        for task in schedule:
            tid = task.get("id", "")
            for dep_id in task.get("dependencies", []):
                if dep_id not in task_by_id:
                    errors.append(
                        f"Задача '{task.get('name')}' ({tid}) зависи от "
                        f"несъществуващо ID: {dep_id}."
                    )

        # --- Circular dependencies (DFS) ---
        if len(schedule) <= _MAX_TASKS_FOR_CYCLE_CHECK:
            cycle = self._detect_cycle(schedule, task_by_id)
            if cycle:
                errors.append(f"Кръгова зависимост: {' → '.join(cycle)}.")
        else:
            warnings.append(
                f"Проверката за кръгови зависимости е пропусната "
                f"(графикът има {len(schedule)} задачи, лимит: {_MAX_TASKS_FOR_CYCLE_CHECK})."
            )

        # --- FS violation: task starts before predecessor ends ---
        for task in schedule:
            tid = task.get("id", "")
            start = task.get("start_day", 0)
            for dep_id in task.get("dependencies", []):
                pred = task_by_id.get(dep_id)
                if pred is None:
                    continue  # already reported above
                pred_end = pred.get("end_day")
                if pred_end is None:
                    pred_dur = pred.get("duration", 0)
                    pred_end = pred.get("start_day", 0) + max(pred_dur, 1) - 1
                if start <= pred_end:
                    errors.append(
                        f"Задача '{task.get('name')}' ({tid}) започва ден {start}, "
                        f"но предшественик '{pred.get('name')}' ({dep_id}) "
                        f"завършва ден {pred_end}."
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
            duration = task.get("duration", 0)

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
                if overlap_count >= 2:
                    warnings.append(
                        f"Екип '{team}' е назначен на повече от 2 задачи "
                        f"едновременно (вкл. {id1} и {', '.join(overlap_ids[:3])})."
                    )
                    break  # one warning per team is enough

        # --- Large gap between predecessor and successor ---
        for task in schedule:
            tid = task.get("id", "")
            start = task.get("start_day", 0)
            for dep_id in task.get("dependencies", []):
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

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
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
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {t.get("id", ""): WHITE for t in schedule}
        parent: dict[str, str | None] = {}

        def dfs(tid: str) -> list[str] | None:
            color[tid] = GRAY
            task = task_by_id.get(tid)
            if not task:
                color[tid] = BLACK
                return None
            for dep_id in task.get("dependencies", []):
                if dep_id not in color:
                    continue
                if color[dep_id] == GRAY:
                    # Found a cycle — reconstruct path
                    path = [dep_id, tid]
                    node = tid
                    while node != dep_id:
                        node = parent.get(node, "")
                        if not node or node == dep_id:
                            break
                        path.insert(1, node)
                    path.append(dep_id)
                    return path
                if color[dep_id] == WHITE:
                    parent[dep_id] = tid
                    result = dfs(dep_id)
                    if result:
                        return result
            color[tid] = BLACK
            return None

        for tid in list(color.keys()):
            if color.get(tid) == WHITE:
                result = dfs(tid)
                if result:
                    return result
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
            for dep_id in task.get("dependencies", []):
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
            for dep_id in task.get("dependencies", []):
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
        old_deps = set(old.get("dependencies") or [])
        new_deps = set(new.get("dependencies") or [])
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
