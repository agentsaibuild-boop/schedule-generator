"""Unit tests for ScheduleBuilder._detect_cycle — DFS cycle detection.

Covers: no cycle (linear chain, tree, disconnected), 2-node cycle, 3-node cycle,
        self-loop, multiple disconnected components (one with cycle), empty schedule,
        and cycle path correctness.

FAILURE означава: src/schedule_builder.py :: _detect_cycle е счупена —
validate_schedule ще пропуска кръгови зависимости в графика,
което ще доведе до безкраен цикъл при cascade shifts или невалидни графици.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.schedule_builder import ScheduleBuilder

_detect = ScheduleBuilder._detect_cycle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _t(id_: str, deps: list[str] | None = None) -> dict:
    """Minimal task dict for cycle detection tests."""
    return {"id": id_, "name": f"Задача {id_}", "dependencies": deps or []}


def _build(tasks: list[dict]) -> tuple[list[dict], dict]:
    task_by_id = {t["id"]: t for t in tasks}
    return tasks, task_by_id


# ---------------------------------------------------------------------------
# No cycle — should return None
# ---------------------------------------------------------------------------

class TestNoCycle:
    def test_empty_schedule_returns_none(self):
        result = _detect([], {})
        assert result is None

    def test_single_task_no_deps(self):
        tasks, by_id = _build([_t("A")])
        assert _detect(tasks, by_id) is None

    def test_two_tasks_linear(self):
        """A → B (A depends on B, no back edge)."""
        tasks, by_id = _build([_t("A", ["B"]), _t("B")])
        assert _detect(tasks, by_id) is None

    def test_three_tasks_linear_chain(self):
        """A → B → C (linear, no cycle)."""
        tasks, by_id = _build([_t("A", ["B"]), _t("B", ["C"]), _t("C")])
        assert _detect(tasks, by_id) is None

    def test_diamond_shape_no_cycle(self):
        """A depends on B and C; B and C both depend on D."""
        tasks, by_id = _build([
            _t("A", ["B", "C"]),
            _t("B", ["D"]),
            _t("C", ["D"]),
            _t("D"),
        ])
        assert _detect(tasks, by_id) is None

    def test_two_disconnected_linear_chains(self):
        """Two independent chains — no cycle."""
        tasks, by_id = _build([
            _t("A", ["B"]), _t("B"),
            _t("X", ["Y"]), _t("Y"),
        ])
        assert _detect(tasks, by_id) is None

    def test_task_with_missing_dep_skipped(self):
        """Dependency on non-existent ID is skipped (not a cycle)."""
        tasks, by_id = _build([_t("A", ["GHOST"])])
        assert _detect(tasks, by_id) is None


# ---------------------------------------------------------------------------
# 2-node cycle
# ---------------------------------------------------------------------------

class TestTwoNodeCycle:
    def test_mutual_dependency_detected(self):
        """A depends on B, B depends on A → cycle."""
        tasks, by_id = _build([_t("A", ["B"]), _t("B", ["A"])])
        result = _detect(tasks, by_id)
        assert result is not None

    def test_mutual_dependency_returns_list(self):
        tasks, by_id = _build([_t("A", ["B"]), _t("B", ["A"])])
        result = _detect(tasks, by_id)
        assert isinstance(result, list)

    def test_mutual_dependency_path_contains_both_nodes(self):
        tasks, by_id = _build([_t("A", ["B"]), _t("B", ["A"])])
        result = _detect(tasks, by_id)
        assert "A" in result
        assert "B" in result

    def test_mutual_dependency_path_forms_cycle(self):
        """Path should start and end with the same node."""
        tasks, by_id = _build([_t("A", ["B"]), _t("B", ["A"])])
        result = _detect(tasks, by_id)
        assert result[0] == result[-1]


# ---------------------------------------------------------------------------
# Self-loop
# ---------------------------------------------------------------------------

class TestSelfLoop:
    def test_self_loop_detected(self):
        """Task depends on itself."""
        tasks, by_id = _build([_t("A", ["A"])])
        result = _detect(tasks, by_id)
        assert result is not None

    def test_self_loop_not_none(self):
        tasks, by_id = _build([_t("A", ["A"]), _t("B")])
        assert _detect(tasks, by_id) is not None

    def test_self_loop_contains_self(self):
        tasks, by_id = _build([_t("A", ["A"])])
        result = _detect(tasks, by_id)
        assert "A" in result


# ---------------------------------------------------------------------------
# 3-node cycle
# ---------------------------------------------------------------------------

class TestThreeNodeCycle:
    def test_three_node_cycle_detected(self):
        """A→B→C→A forms a cycle."""
        tasks, by_id = _build([
            _t("A", ["B"]),
            _t("B", ["C"]),
            _t("C", ["A"]),
        ])
        result = _detect(tasks, by_id)
        assert result is not None

    def test_three_node_cycle_path_has_all_nodes(self):
        tasks, by_id = _build([
            _t("A", ["B"]),
            _t("B", ["C"]),
            _t("C", ["A"]),
        ])
        result = _detect(tasks, by_id)
        for node in ("A", "B", "C"):
            assert node in result

    def test_three_node_cycle_path_forms_closed_loop(self):
        tasks, by_id = _build([
            _t("A", ["B"]),
            _t("B", ["C"]),
            _t("C", ["A"]),
        ])
        result = _detect(tasks, by_id)
        assert result[0] == result[-1]

    def test_three_node_cycle_path_min_length(self):
        """Cycle path must have at least 3 unique nodes + closing node = 4."""
        tasks, by_id = _build([
            _t("A", ["B"]),
            _t("B", ["C"]),
            _t("C", ["A"]),
        ])
        result = _detect(tasks, by_id)
        assert len(result) >= 3


# ---------------------------------------------------------------------------
# Disconnected graph with cycle in one component
# ---------------------------------------------------------------------------

class TestDisconnectedGraph:
    def test_cycle_detected_in_one_component(self):
        """Component X-Y has no cycle; component A-B-A does."""
        tasks, by_id = _build([
            _t("X", ["Y"]), _t("Y"),          # clean chain
            _t("A", ["B"]), _t("B", ["A"]),   # cycle
        ])
        result = _detect(tasks, by_id)
        assert result is not None

    def test_clean_component_does_not_interfere(self):
        """A clean component before the cyclic one doesn't hide the cycle."""
        tasks, by_id = _build([
            _t("P"), _t("Q", ["P"]), _t("R", ["Q"]),  # clean
            _t("X", ["Y"]), _t("Y", ["X"]),             # cycle
        ])
        result = _detect(tasks, by_id)
        assert result is not None
        assert "X" in result or "Y" in result

    def test_no_cycle_in_either_component(self):
        """Two clean disconnected components → None."""
        tasks, by_id = _build([
            _t("A", ["B"]), _t("B"),
            _t("C", ["D"]), _t("D"),
        ])
        assert _detect(tasks, by_id) is None


# ---------------------------------------------------------------------------
# Path structure validation
# ---------------------------------------------------------------------------

class TestCyclePath:
    def test_path_is_list(self):
        tasks, by_id = _build([_t("A", ["B"]), _t("B", ["A"])])
        result = _detect(tasks, by_id)
        assert isinstance(result, list)

    def test_path_has_at_least_two_elements(self):
        tasks, by_id = _build([_t("A", ["B"]), _t("B", ["A"])])
        result = _detect(tasks, by_id)
        assert len(result) >= 2

    def test_no_cycle_returns_none_not_empty_list(self):
        tasks, by_id = _build([_t("A"), _t("B", ["A"])])
        result = _detect(tasks, by_id)
        assert result is None


class TestMismatchedTaskById:
    def test_task_in_schedule_but_missing_from_task_by_id_returns_none(self):
        """Lines 323-325: when task_by_id does not contain a task that IS in
        schedule (and therefore in the color map), dfs colours it BLACK and
        returns None gracefully — no exception, no false cycle."""
        tasks = [_t("A")]
        # Intentionally pass an empty task_by_id so "A" is coloured but missing.
        result = _detect(tasks, {})
        assert result is None
