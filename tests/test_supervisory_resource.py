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

# ЗАЩО НЕ БАГЕР.  Дотук багерът беше примерът за машина, която може да е
# само на едно място.  Изпълнителят обаче каза на 31.08.2026, че багери,
# бетоновози и самосвали се наемат колкото трябва — и те излязоха от
# твърдото изравняване (`hired` в resource_capacity.json).  Механизмът,
# който тези тестове проверяват, е СЪЩИЯТ; сменен е само примерът, за да е
# ресурс, който наистина ограничава.  Заваръчната машина за ПЕ е такъв:
# 2 налични, не е надзорна, не е на екипа, не се наема.


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
        """Производственият ресурс си остава ограничение."""
        резултат = ScheduleBuilder().level_resources(
            _задачи(6, ["Заваръчна машина за ПЕ"]), capacity={"Заваръчна машина за ПЕ": 2})
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
        assert _is_leveling_resource("Заваръчна машина за ПЕ") is True

    def test_the_manager_is_still_assigned_to_the_task(self):
        """Изключен от изравняването НЕ значи изтрит от графика."""
        резултат = ScheduleBuilder().level_resources(
            _задачи(2, ["Ръководител работна група", "Заваръчна машина за ПЕ"]))

        assert all("Ръководител работна група" in t["resources"]
                   for t in резултат["schedule"])


class TestTheGateCountsByTheSameRule:
    """Проверката и изравняването не бива да броят по различни правила.

    ИЗМЕРЕНО 17.08.2026 на детерминистичния прогон: при ТРИ фронта графикът
    излизаше „претоварен" по „Ръководител работна група" — ограничение, което
    планировчикът нарочно не спазва от 14.08.  Тоест гейтът отхвърляше работа
    заради правило, което кодът е решил да няма.  При два фронта числото
    случайно оставаше под тавана и разминаването не се виждаше.
    """

    def test_a_supervisory_overload_is_not_reported(self):
        from src.schedule_diagnostics import _capacity_overloads

        задачи = _задачи(9, ["Ръководител работна група"])
        assert _capacity_overloads(задачи) == [], \
            "гейтът брои ресурс, който изравняването нарочно не ограничава"

    def test_a_production_overload_is_still_reported(self):
        from src.schedule_diagnostics import _capacity_overloads

        задачи = _задачи(9, ["Заваръчна машина за ПЕ"])
        претоварени = _capacity_overloads(задачи)

        assert претоварени, "истинско претоварване вече не се вижда"
        assert претоварени[0]["resource"] == "Заваръчна машина за ПЕ"

    def test_the_two_sides_agree_on_a_levelled_schedule(self):
        """Каквото изравняването е пуснало, гейтът не бива да отхвърля."""
        from src.schedule_diagnostics import _capacity_overloads

        изравнен = ScheduleBuilder().level_resources(
            _задачи(9, ["Ръководител работна група", "Заваръчна машина за ПЕ"]))

        assert _capacity_overloads(изравнен["schedule"]) == []
