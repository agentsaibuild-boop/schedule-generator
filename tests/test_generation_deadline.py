"""Unit tests: един опит за генерация има ТВЪРД срок.

ЖИВ ПРОГОН 14.08.2026: приложението увисна с часове.  Всяко HTTP извикване си
има таван, но един опит е десетки извиквания, а стрийминг, който капе по един
токен, не гърми никога — тоест таванът на извикването не е таван на опита.
Докато опитът виси, интерфейсът е заключен и човекът няма какво да натисне.

FAILURE означава: увиснал доставчик пак ще държи приложението часове.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.chat_handler import ChatHandler  # noqa: E402
from src.deadline import (  # noqa: E402
    DeadlineExceeded,
    attempt_timeout,
    run_with_deadline,
)


class TestRunWithDeadline:
    def test_the_caller_is_released_on_time(self):
        начало = time.monotonic()
        with pytest.raises(DeadlineExceeded):
            run_with_deadline(lambda _p: time.sleep(30), 0.5, name="висящо")
        assert time.monotonic() - начало < 5, "чакащият не беше освободен"

    def test_the_result_comes_back_when_the_work_finishes(self):
        assert run_with_deadline(lambda _p: "готово", 5) == "готово"

    def test_progress_is_relayed_to_the_waiting_thread(self):
        """Streamlit не вижда `st.*` от чужда нишка — затова напредъкът се
        предава през опашка и се показва от ЧАКАЩАТА нишка."""
        видяно: list[str] = []
        нишки: list[str] = []

        def работа(напредък):
            import threading
            нишки.append(threading.current_thread().name)
            напредък("първо")
            напредък("второ")
            return None

        run_with_deadline(работа, 5, progress=lambda m: видяно.append(m))
        assert видяно == ["първо", "второ"]
        assert нишки and нишки[0] != "MainThread", "работата не е в своя нишка"

    def test_an_error_in_the_work_is_not_swallowed(self):
        def работа(_напредък):
            raise RuntimeError("засечка на доставчика")

        with pytest.raises(RuntimeError, match="засечка"):
            run_with_deadline(работа, 5)

    def test_no_deadline_runs_inline(self):
        import threading
        нишки: list[str] = []
        run_with_deadline(
            lambda _p: нишки.append(threading.current_thread().name), 0)
        assert нишки == ["MainThread"]


class TestAttemptTimeout:
    def test_the_default_is_generous_but_finite(self):
        assert 60 <= attempt_timeout() <= 3600

    def test_the_environment_wins(self, monkeypatch):
        monkeypatch.setenv("GENERATION_ATTEMPT_TIMEOUT", "42")
        assert attempt_timeout() == 42

    def test_zero_switches_the_deadline_off(self, monkeypatch):
        monkeypatch.setenv("GENERATION_ATTEMPT_TIMEOUT", "0")
        assert attempt_timeout() == 0

    def test_nonsense_does_not_break_generation(self, monkeypatch):
        monkeypatch.setenv("GENERATION_ATTEMPT_TIMEOUT", "скоро")
        assert attempt_timeout() >= 60


class TestAHungAttemptDoesNotLockTheApp:
    """Висящият опит се изоставя и редът идва на следващия."""

    @staticmethod
    def _хендлър(отговори):
        """Списък от отговори; извикуемото се ИЗПЪЛНЯВА (за да може да виси)."""
        стъпки = iter(отговори)
        handler = ChatHandler.__new__(ChatHandler)
        handler.ai = MagicMock()

        def _отговор(*a, **k):
            стъпка = next(стъпки)
            return стъпка(*a, **k) if callable(стъпка) else стъпка

        handler.ai.generate_schedule_packaged.side_effect = _отговор
        return handler

    def test_the_next_attempt_gets_its_turn(self, monkeypatch):
        monkeypatch.setenv("GENERATION_ATTEMPT_TIMEOUT", "1")

        def увисва(*_a, **_k):
            time.sleep(30)                      # доставчикът не отговаря

        handler = self._хендлър([увисва, {"status": "ok", "exportable": True}])
        начало = time.monotonic()
        резултат = handler._try_package_generation(
            {"analysis": "x"}, [object()], num_teams=2, locations=[],
            progress=lambda *_a, **_k: None)

        assert резултат["exportable"] is True
        assert time.monotonic() - начало < 10, "чакахме колкото висящия опит"

    def test_the_user_is_told_why_it_was_cut(self, monkeypatch):
        monkeypatch.setenv("GENERATION_ATTEMPT_TIMEOUT", "1")
        съобщения: list[str] = []
        handler = self._хендлър([
            lambda *_a, **_k: time.sleep(30),
            {"status": "ok", "exportable": True},
        ])
        handler._try_package_generation(
            {"analysis": "x"}, [object()], num_teams=2, locations=[],
            progress=lambda m, *_a, **_k: съобщения.append(m))

        assert any("няма отговор" in m for m in съобщения), съобщения
