"""Заклещен поток се прекъсва по стенен часовник, не се чака безкрайно.

FAILURE означава: пазачът в src/ai_router._openai_request пак го няма и една
мъртва заявка може да държи прогона колкото си иска.

ИЗМЕРЕНО 18.08.2026, серия 4 прогон 1: заявка виси ШЕЙСЕТ МИНУТИ и чак тогава
излиза „Upstream error from DigitalOcean: stream failed".  HTTP timeout-ът от
600 s не я хвана и не е сгрешен: доставчикът праща keepalive-и, всеки е
получени байтове и нулира read timeout-а, а изходен текст не идва.  Тоест
по-дълъг read timeout НЕ е решението — таван върху целия поток е.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import ai_router  # noqa: E402
from src.ai_router import AIRouter, StreamStalled  # noqa: E402


class _Delta:
    def __init__(self, content: str | None = None) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str | None = None,
                 finish_reason: str | None = None) -> None:
        self.delta = _Delta(content)
        self.finish_reason = finish_reason


class _Chunk:
    """Парче от стрийма.  Без `choices` = keepalive: байтове, но не текст."""

    def __init__(self, content: str | None = None,
                 finish_reason: str | None = None,
                 keepalive: bool = False) -> None:
        self.choices = [] if keepalive else [_Choice(content, finish_reason)]
        self.usage = None


class _Stream:
    def __init__(self, chunks, пауза: float = 0.0) -> None:
        self._chunks = chunks
        self._пауза = пауза
        self.closed = False

    def __iter__(self):
        for c in self._chunks:
            if self._пауза:
                time.sleep(self._пауза)
            yield c

    def close(self) -> None:
        self.closed = True


class _Отговор:
    """Обикновен (не-streaming) отговор — какъвто SDK-ът връща под прага."""

    def __init__(self, съдържание: str) -> None:
        съобщение = type("msg", (), {"content": съдържание})()
        self.choices = [type("ch", (), {"message": съобщение,
                                        "finish_reason": "stop"})()]
        self.usage = type("u", (), {"prompt_tokens": 7,
                                    "completion_tokens": 3})()


class _Completions:
    def __init__(self, връща) -> None:
        self._връща = връща
        self.kwargs: dict = {}

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self._връща


class _Client:
    def __init__(self, връща) -> None:
        self.chat = type("chat", (), {"completions": _Completions(връща)})()


@pytest.fixture()
def кратък_таван(monkeypatch):
    """Таванът е в секунди — тестът не бива да чака двайсет минути."""
    monkeypatch.setattr(ai_router, "_STREAM_MAX_SECONDS", 1)


def _kwargs(max_tokens: int = 48000) -> dict:
    return {"model": "тест/модел", "messages": [], "max_tokens": max_tokens}


def test_a_stream_that_only_keepalives_is_cut(кратък_таван):
    """Сто keepalive-а без нито един знак текст — точно наблюдаваният случай."""
    поток = _Stream([_Chunk(keepalive=True) for _ in range(100)], пауза=0.05)
    клиент = _Client(поток)

    with pytest.raises(StreamStalled) as exc:
        AIRouter._openai_request(None, клиент, _kwargs(), 48000)

    assert exc.value.seconds >= 1
    assert поток.closed, "потокът остава отворен — сокетът виси и след отказа"


def test_the_partial_text_survives_for_diagnosis(кратък_таван):
    """Каквото е дошло, се вижда в изключението — иначе отказът е сляп."""
    парчета = [_Chunk("част "), _Chunk("от JSON")]
    парчета += [_Chunk(keepalive=True) for _ in range(100)]
    клиент = _Client(_Stream(парчета, пауза=0.05))

    with pytest.raises(StreamStalled) as exc:
        AIRouter._openai_request(None, клиент, _kwargs(), 48000)

    assert exc.value.partial == "част от JSON"


def test_a_healthy_stream_is_not_touched():
    """Пазачът не бива да реже нормална генерация."""
    клиент = _Client(_Stream([_Chunk("здрав "), _Chunk("отговор"),
                              _Chunk(None, finish_reason="stop")]))

    съдържание, _, _, finish = AIRouter._openai_request(
        None, клиент, _kwargs(), 48000)

    assert съдържание == "здрав отговор"
    assert finish == "stop"


def test_small_requests_do_not_stream_at_all():
    """Под прага заявката е обикновена — пазачът няма какво да пази.

    Проверява се ЯВНО, че `stream` не е поискан: ако прагът някога падне и
    малките заявки тръгнат през стрийма, таванът от 1200 s би започнал да
    важи и за тях.
    """
    клиент = _Client(_Отговор("кратко"))

    съдържание, вход, изход, finish = AIRouter._openai_request(
        None, клиент, _kwargs(max_tokens=100), 100)

    assert съдържание == "кратко"
    assert (вход, изход, finish) == (7, 3, "stop")
    assert "stream" not in клиент.chat.completions.kwargs
