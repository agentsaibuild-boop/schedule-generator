"""Unit tests: срокът е въпрос на едновременност, не само на брой задачи.

ОДИТ 13.08.2026, P0.3: „Броят leaf задачи вече е почти същият (486 срещу 513 в
човешкия еталон), но span-ът е 1.7× по-дълъг.  Човекът държи медиана 7 активни
задачи и пик 10; ние — 2 и 2.  Не е нужно просто ‚още задачи'."

Това коригира и нашия собствен извод от раздел 11.3 на брийфа.  Мярката за
успех вече не е броят задачи, а колко от тях вървят едновременно — и затова се
мери на всеки прогон, вместо да се гадае от срока.

FAILURE означава: пак ще гоним брой задачи, без да виждаме, че ги редим една
след друга.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.schedule_diagnostics import (  # noqa: E402
    concurrency_bottlenecks,
    concurrency_report,
    widest_join,
)


def _задача(ид, старт, край, зависи=()):
    return {"id": ид, "name": f"задача {ид}", "start_day": старт,
            "end_day": край, "dependencies": list(зависи)}


class TestConcurrencyReport:
    def test_sequential_work_has_concurrency_of_one(self):
        """Пет задачи една след друга: пик 1, колкото и да са."""
        задачи = [_задача(f"T{i}", i * 10 + 1, i * 10 + 9) for i in range(5)]
        отчет = concurrency_report(задачи)

        assert отчет["construction_leaf_count"] == 5
        assert отчет["peak_active_leaf_tasks"] == 1
        assert отчет["median_active_leaf_tasks"] == 1

    def test_parallel_work_is_visible(self):
        """Същите пет задачи, но едновременно: пик 5, срок 9 дни вместо 49."""
        задачи = [_задача(f"T{i}", 1, 9) for i in range(5)]
        отчет = concurrency_report(задачи)

        assert отчет["peak_active_leaf_tasks"] == 5
        assert отчет["construction_span_days"] == 9

    def test_the_same_task_count_can_give_different_spans(self):
        """Същината на находката: броят задачи не казва нищо за срока."""
        последователно = concurrency_report(
            [_задача(f"T{i}", i * 10 + 1, i * 10 + 9) for i in range(5)])
        успоредно = concurrency_report([_задача(f"T{i}", 1, 9) for i in range(5)])

        assert (последователно["construction_leaf_count"]
                == успоредно["construction_leaf_count"])
        assert последователно["construction_span_days"] > \
            успоредно["construction_span_days"] * 4

    def test_an_empty_schedule_says_it_was_not_evaluated(self):
        assert concurrency_report([])["evaluated"] is False


class TestBottlenecks:
    def test_it_names_the_task_that_holds_the_most_work(self):
        """Одиторът намери пътна основа с 41 предшественика — глобална бариера."""
        задачи = [_задача("BAR", 1, 2)]
        задачи += [_задача(f"T{i}", 3, 4, зависи=["BAR"]) for i in range(7)]

        (най,) = concurrency_bottlenecks(задачи, top=1)
        assert най["task_id"] == "BAR"
        assert най["successors"] == 7

    def test_the_widest_join_is_reported(self):
        задачи = [_задача(f"T{i}", 1, 2) for i in range(6)]
        задачи.append(_задача("КРАЙ", 3, 4, зависи=[f"T{i}" for i in range(6)]))

        най = widest_join(задачи)
        assert най["task_id"] == "КРАЙ"
        assert най["predecessors"] == 6
