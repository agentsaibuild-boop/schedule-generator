"""Unit tests: приложението довършва опита, не го оставя на потребителя.

СЕРИЯ 14.08.2026: 21 от 40 прогона са чисти — тоест ЕДИН опит е хвърляне на
монета.  `tools/build_audit_package.py` отдавна опитва до 10 пъти и надеждно
вади чист график, а приложението, което ползва потребителят, опитваше веднъж.

Оттам и усещането, че инструментът работи, а продуктът не: разликата не беше в
генерацията, а в това кой повтаря опита — програмата или човекът пред екрана.

FAILURE означава: всеки втори клик пак ще дава график за ръчен преглед.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.chat_handler import ChatHandler  # noqa: E402


def _хендлър(отговори):
    handler = ChatHandler.__new__(ChatHandler)
    handler.ai = MagicMock()
    handler.ai.generate_schedule_packaged.side_effect = отговори
    return handler


def _извикай(handler):
    return handler._try_package_generation(
        {"analysis": "x"}, [object()], num_teams=2, locations=[],
        progress=lambda *_a, **_k: None)


class TestRetryUntilExportable:
    def test_a_failed_attempt_is_repeated(self):
        резултат = _извикай(_хендлър([
            {"status": "needs_human_review", "exportable": False},
            {"status": "ok", "exportable": True, "schedule": {"tasks": []}},
        ]))
        assert резултат["exportable"] is True

    def test_an_exportable_result_is_not_second_guessed(self):
        """Не търсим по-хубав график — довършваме прекъснат опит."""
        handler = _хендлър([{"status": "ok", "exportable": True}])
        _извикай(handler)
        assert handler.ai.generate_schedule_packaged.call_count == 1

    def test_an_exception_does_not_end_the_whole_generation(self):
        резултат = _извикай(_хендлър([
            RuntimeError("засечка на доставчика"),
            {"status": "ok", "exportable": True},
        ]))
        assert резултат["exportable"] is True

    def test_the_last_result_is_returned_when_nothing_is_exportable(self):
        """Човекът трябва да види КАКВО пречи, не празен екран."""
        резултат = _извикай(_хендлър([
            {"status": "needs_human_review", "exportable": False, "n": 1},
            {"status": "needs_human_review", "exportable": False, "n": 2},
            {"status": "needs_human_review", "exportable": False, "n": 3},
            {"status": "needs_human_review", "exportable": False, "n": 4},
        ]))
        assert резултат is not None and резултат["n"] == 4

    def test_the_attempt_count_is_configurable(self, monkeypatch):
        monkeypatch.setenv("GENERATION_ATTEMPTS", "2")
        handler = _хендлър([{"status": "needs_human_review", "exportable": False}] * 2)
        _извикай(handler)
        assert handler.ai.generate_schedule_packaged.call_count == 2

    def test_a_result_without_the_flag_is_judged_by_status(self):
        """Липсващ флаг не значи неуспех — иначе готов график се плаща 4 пъти."""
        handler = _хендлър([{"status": "approved", "schedule": {"tasks": []}}])
        _извикай(handler)
        assert handler.ai.generate_schedule_packaged.call_count == 1
