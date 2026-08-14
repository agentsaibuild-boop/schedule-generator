"""Unit tests: надзорът не бива да е глобален семафор.

ОДИТ 14.08.2026, P0.3: „Ръководител работна група е hard-leveling ресурс върху
всичките 200 construction leaf tasks с MaxUnits=2, което превръща целия проект
в глобален semaphore с максимум две едновременни задачи.  Σ durations = 1672
task-days; при capacity 2 теоретичният минимум е 836 дни, а човешкият еталон е
660 — тоест benchmark-ът е математически недостижим при тази конфигурация."

Проверимо и точно: 201 назначения при таван 2, докато Фронт 1 и Фронт 2 имат по
3.  Ръководителят НАДЗИРАВА едновременна работа, не я изпълнява.

FAILURE означава: срокът пак ще се определя от надзорна роля, а не от бригадите
и техниката — и никаква работа по мрежата няма да го скъси.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.schedule_builder import ScheduleBuilder, _is_leveling_resource  # noqa: E402


def _задачи(брой: int, ресурси: list[str]):
    return [{"id": f"T{i}", "name": f"задача {i}", "duration": 5,
             "start_day": 1, "end_day": 5, "dependencies": [],
             "resources": list(ресурси)}
            for i in range(брой)]


class TestSupervisoryIsNotASemaphore:
    def test_the_manager_does_not_cap_concurrency(self):
        """Шест независими задачи с ръководител вървят едновременно."""
        резултат = ScheduleBuilder().level_resources(
            _задачи(6, ["Ръководител работна група"]))
        стартове = {t["start_day"] for t in резултат["schedule"]}

        assert стартове == {1}, (
            "надзорната роля пак разсрочва работата — "
            f"стартове: {sorted(стартове)}")

    def test_a_production_resource_still_caps_concurrency(self):
        """Багерът си остава ограничение — той наистина може да е на едно място."""
        резултат = ScheduleBuilder().level_resources(
            _задачи(6, ["Багер универсален"]), capacity={"Багер универсален": 2})
        стартове = sorted(t["start_day"] for t in резултат["schedule"])

        assert стартове[0] == 1 and стартове[-1] > 1, \
            "производствен ресурс трябва да ограничава едновременността"

    def test_the_front_still_caps_concurrency(self):
        резултат = ScheduleBuilder().level_resources(
            _задачи(6, ["Фронт 1"]), capacity={"Фронт 1": 3})
        едновременни = sum(1 for t in резултат["schedule"] if t["start_day"] == 1)

        assert едновременни <= 3

    def test_supervisory_roles_come_from_configuration(self):
        """Списъкът е конфигурация, а не име, зашито в кода."""
        assert _is_leveling_resource("Ръководител работна група") is False
        assert _is_leveling_resource("Багер универсален") is True

    def test_the_manager_is_still_assigned_to_the_task(self):
        """Изключен от изравняването НЕ значи изтрит от графика."""
        резултат = ScheduleBuilder().level_resources(
            _задачи(2, ["Ръководител работна група", "Багер универсален"]))

        assert all("Ръководител работна група" in t["resources"]
                   for t in резултат["schedule"])
