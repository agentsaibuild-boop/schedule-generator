"""Ресурсът се брои в ХОРА, не в слотове за задачи.

FAILURE означава: таванът пак ограничава колко ЗАДАЧИ вървят наведнъж, а не
колко души има на обекта — и графикът чака за техника, която в действителност
стига.

ИЗМЕРЕНО 19.08.2026 от еталонния график, който записва Units на всяко
назначение:

    Каналджия             3 на задача, 14 на обекта   (стар таван: 6 задачи)
    Строителен работник   3 на задача, 11 на обекта   (стар таван: 8 задачи)
    Товарен автомобил     1 на задача,  9 на обекта   (стар таван: 6 задачи)

Обектът има четиринайсет каналджии, а допускахме шест задачи — всяка уж с
един каналджия.  Оттам идваше 82-процентното чакане на водопроводните пакети.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.schedule_builder import ScheduleBuilder, _headcount  # noqa: E402


def _задачи(брой: int, ресурси: list[str], дни: int = 5) -> list[dict]:
    return [{"id": f"T{i}", "name": f"задача {i}", "duration": дни,
             "start_day": 1, "end_day": дни, "dependencies": [],
             "resources": list(ресурси), "crew_id": f"екип {i}",
             "chain_step": "laying", "network": "К"}
            for i in range(1, брой + 1)]


# ---------------------------------------------------------------------------
# Числата идват от еталона
# ---------------------------------------------------------------------------


def test_the_reference_crew_composition_is_loaded():
    състав = _headcount()

    assert състав["Каналджия"] == {"на_задача": 3, "налични": 14}
    assert състав["Строителен работник"]["на_задача"] == 3
    assert състав["Ръководител работна група"]["налични"] == 6


def test_machines_are_counted_by_how_many_there_are():
    """Три багера, по един на задача → три едновременни, не „слот" за задача.

    Примерът беше „Каналджия", докато хората бяха общообектов ресурс.  От
    19.08.2026 те принадлежат на ЕКИПА и не ограничават обекта; машините
    остават общи и точно те са мерилото.
    """
    резултат = ScheduleBuilder().level_resources(_задачи(6, ["Багер ескаватор"]))

    едновременни = sum(1 for t in резултат["schedule"]
                       if int(t["start_day"]) == 1)
    assert едновременни == 3, [t["start_day"] for t in резултат["schedule"]]


def test_people_belong_to_the_crew_and_do_not_cap_the_site():
    """Изпълнителят: „всеки екип си има свои общи работници, не ги делят."

    Дотук `Общ работник` беше 8 души по 2 на задача — 4 едновременни задачи за
    ЦЕЛИЯ обект — и едновременността ни стоеше на 4.9 при 6.9 в еталона, на
    колкото и участъка да делим обекта.
    """
    резултат = ScheduleBuilder().level_resources(_задачи(6, ["Общ работник"]))

    едновременни = sum(1 for t in резултат["schedule"]
                       if int(t["start_day"]) == 1)
    assert едновременни == 6, [t["start_day"] for t in резултат["schedule"]]


def test_a_task_that_needs_nobody_special_is_not_capped():
    """Ресурс извън еталона пада към стария таван, не се блокира."""
    резултат = ScheduleBuilder().level_resources(
        _задачи(3, ["Нещо, което еталонът не познава"]))

    assert all(int(t["start_day"]) >= 1 for t in резултат["schedule"])


# ---------------------------------------------------------------------------
# Изричният таван си остава по-силен
# ---------------------------------------------------------------------------


def test_an_explicit_capacity_still_wins():
    """Който вика с `capacity={...}`, казва „толкова, точка"."""
    резултат = ScheduleBuilder().level_resources(
        _задачи(6, ["Багер ескаватор"]), capacity={"Багер ескаватор": 2})

    едновременни = sum(1 for t in резултат["schedule"]
                       if int(t["start_day"]) == 1)
    assert едновременни == 2


# ---------------------------------------------------------------------------
# Проверката брои по СЪЩОТО правило
# ---------------------------------------------------------------------------


def test_the_overload_check_counts_the_same_way():
    """Иначе гейтът отхвърля точно графиците, които изравняването е сметнало."""
    from src.schedule_diagnostics import _capacity_overloads

    изравнен = ScheduleBuilder().level_resources(_задачи(6, ["Багер ескаватор"]))

    assert not _capacity_overloads(изравнен["schedule"])


def test_the_check_ignores_crew_people_too():
    """Едно правило за двете страни: щом не ограничават, не могат и да претоварят."""
    from src.schedule_diagnostics import _capacity_overloads

    assert not _capacity_overloads(_задачи(9, ["Общ работник"]))


def test_the_check_still_catches_a_real_overload():
    """Гейт, който не може да падне, е безполезен."""
    from src.schedule_diagnostics import _capacity_overloads

    претоварен = _задачи(9, ["Багер ескаватор"])   # 9 наведнъж при 3 налични

    assert _capacity_overloads(претоварен)
