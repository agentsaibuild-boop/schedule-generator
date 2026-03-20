"""Unit tests for ScheduleBuilder.to_dataframe.

FAILURE означава: таблицата за преглед на графика в UI-а не се показва
правилно — липсват колони, дати са грешни, или функцията се срива при
непълни данни от AI.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.schedule_builder import ScheduleBuilder


def _builder() -> ScheduleBuilder:
    return ScheduleBuilder()


def _task(**kwargs) -> dict:
    base = {
        "id": "T1",
        "name": "Водопровод DN90",
        "type": "water_pipe",
        "start_day": 1,
        "duration": 10,
        "end_day": 10,
        "team": "Бригада 1",
        "diameter": "DN90",
        "length_m": 500,
        "is_critical": False,
    }
    base.update(kwargs)
    return base


START_DATE = "2026-06-01"


# ---------------------------------------------------------------------------
# Column presence
# ---------------------------------------------------------------------------

class TestColumns:
    def test_expected_columns_present(self):
        sb = _builder()
        df = sb.to_dataframe([_task()], START_DATE)
        expected = {"№", "Дейност", "Тип", "DN", "L(м)", "Екип", "Начало", "Край", "Дни", "Критичен"}
        assert expected.issubset(set(df.columns))

    def test_row_count_matches_task_count(self):
        sb = _builder()
        tasks = [_task(id=f"T{i}", name=f"Задача {i}") for i in range(5)]
        df = sb.to_dataframe(tasks, START_DATE)
        assert len(df) == 5

    def test_empty_schedule_returns_empty_dataframe(self):
        sb = _builder()
        df = sb.to_dataframe([], START_DATE)
        assert len(df) == 0


# ---------------------------------------------------------------------------
# Correct values
# ---------------------------------------------------------------------------

class TestValues:
    def test_number_column_sequential(self):
        sb = _builder()
        tasks = [_task(id=f"T{i}", name=f"Задача {i}") for i in range(3)]
        df = sb.to_dataframe(tasks, START_DATE)
        assert list(df["№"]) == [1, 2, 3]

    def test_name_column(self):
        sb = _builder()
        df = sb.to_dataframe([_task(name="Тест задача")], START_DATE)
        assert df.iloc[0]["Дейност"] == "Тест задача"

    def test_duration_column(self):
        sb = _builder()
        df = sb.to_dataframe([_task(duration=15)], START_DATE)
        assert df.iloc[0]["Дни"] == 15

    def test_start_date_day1(self):
        """Day 1 should map to the project start date."""
        sb = _builder()
        df = sb.to_dataframe([_task(start_day=1)], START_DATE)
        assert df.iloc[0]["Начало"] == "01.06.2026"

    def test_end_date_day10(self):
        """Day 10 with start 2026-06-01 → 2026-06-10."""
        sb = _builder()
        df = sb.to_dataframe([_task(start_day=1, end_day=10)], START_DATE)
        assert df.iloc[0]["Край"] == "10.06.2026"

    def test_critical_task_red_dot(self):
        sb = _builder()
        df = sb.to_dataframe([_task(is_critical=True)], START_DATE)
        assert df.iloc[0]["Критичен"] == "🔴"

    def test_non_critical_task_empty(self):
        sb = _builder()
        df = sb.to_dataframe([_task(is_critical=False)], START_DATE)
        assert df.iloc[0]["Критичен"] == ""

    def test_team_column(self):
        sb = _builder()
        df = sb.to_dataframe([_task(team="Сондажен екип")], START_DATE)
        assert df.iloc[0]["Екип"] == "Сондажен екип"

    def test_diameter_column(self):
        sb = _builder()
        df = sb.to_dataframe([_task(diameter="DN300")], START_DATE)
        assert df.iloc[0]["DN"] == "DN300"

    def test_length_column(self):
        sb = _builder()
        df = sb.to_dataframe([_task(length_m=673)], START_DATE)
        assert df.iloc[0]["L(м)"] == 673


# ---------------------------------------------------------------------------
# Robustness — missing fields must not raise KeyError
# ---------------------------------------------------------------------------

class TestRobustness:
    def test_missing_name_uses_fallback(self):
        """Task without 'name' key must not raise KeyError."""
        sb = _builder()
        task = _task()
        del task["name"]
        df = sb.to_dataframe([task], START_DATE)
        assert df.iloc[0]["Дейност"] == "Без име"

    def test_missing_start_day_uses_fallback(self):
        """Task without 'start_day' must not raise KeyError."""
        sb = _builder()
        task = _task()
        del task["start_day"]
        df = sb.to_dataframe([task], START_DATE)
        # start_day defaults to 1 → 01.06.2026
        assert df.iloc[0]["Начало"] == "01.06.2026"

    def test_missing_end_day_computed_from_duration(self):
        """end_day absent: should be derived as start_day + duration - 1."""
        sb = _builder()
        task = _task(start_day=1, duration=5)
        del task["end_day"]
        df = sb.to_dataframe([task], START_DATE)
        # day 5 from 2026-06-01 → 2026-06-05
        assert df.iloc[0]["Край"] == "05.06.2026"

    def test_missing_team_shows_dash(self):
        sb = _builder()
        task = _task()
        del task["team"]
        df = sb.to_dataframe([task], START_DATE)
        assert df.iloc[0]["Екип"] == "—"

    def test_missing_diameter_shows_dash(self):
        sb = _builder()
        task = _task()
        del task["diameter"]
        df = sb.to_dataframe([task], START_DATE)
        assert df.iloc[0]["DN"] == "—"

    def test_missing_length_shows_dash(self):
        sb = _builder()
        task = _task()
        del task["length_m"]
        df = sb.to_dataframe([task], START_DATE)
        assert df.iloc[0]["L(м)"] == "—"

    def test_duration_zero_end_day_computed_as_start(self):
        """duration=0 → end_day = start_day (same day, max(0,1)-1=0 → start+0=start)."""
        sb = _builder()
        task = _task(start_day=5, duration=0)
        del task["end_day"]
        df = sb.to_dataframe([task], START_DATE)
        # day 5 → 05.06.2026
        assert df.iloc[0]["Начало"] == df.iloc[0]["Край"]

    def test_missing_is_critical_defaults_to_empty(self):
        sb = _builder()
        task = _task()
        del task["is_critical"]
        df = sb.to_dataframe([task], START_DATE)
        assert df.iloc[0]["Критичен"] == ""
