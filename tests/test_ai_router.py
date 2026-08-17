"""Unit tests for AIRouter infrastructure methods — no API calls required.

Covers: _calculate_cost, _update_fallback_state, get_usage_stats, _log_usage,
        _warn_empty_prompt, get_cumulative_stats, set_cumulative_path,
        _load_cumulative, _save_cumulative, PRICING constants.

FAILURE означава: src/ai_router.py :: core infrastructure е счупена —
ценообразуването, fallback логиката или usage tracking дават грешни данни,
което нарушава cost reporting и failover поведението на dual-AI системата.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai_router import (
    AIRouter,
    PRICING,
    MODEL_WORKER,
    MODEL_CONTROLLER,
    _MIN_SYSTEM_PROMPT_LEN,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_router() -> AIRouter:
    """Create a fresh AIRouter without touching .env / API keys."""
    return AIRouter()


# ---------------------------------------------------------------------------
# _calculate_cost — static method, pure arithmetic
# ---------------------------------------------------------------------------

def test_calculate_cost_deepseek_zero_tokens():
    cost = AIRouter._calculate_cost(MODEL_WORKER, 0, 0)
    assert cost == 0.0


def test_calculate_cost_deepseek_input_only():
    rate = PRICING[MODEL_WORKER]["input"]
    cost = AIRouter._calculate_cost(MODEL_WORKER, 1_000, 0)
    assert abs(cost - rate * 1_000) < 1e-12


def test_calculate_cost_deepseek_output_only():
    rate = PRICING[MODEL_WORKER]["output"]
    cost = AIRouter._calculate_cost(MODEL_WORKER, 0, 1_000)
    assert abs(cost - rate * 1_000) < 1e-12


def test_calculate_cost_deepseek_both():
    r = PRICING[MODEL_WORKER]
    expected = 100 * r["input"] + 200 * r["output"]
    assert abs(AIRouter._calculate_cost(MODEL_WORKER, 100, 200) - expected) < 1e-12


def test_calculate_cost_anthropic():
    r = PRICING[MODEL_CONTROLLER]
    expected = 50 * r["input"] + 30 * r["output"]
    assert abs(AIRouter._calculate_cost(MODEL_CONTROLLER, 50, 30) - expected) < 1e-12


def test_calculate_cost_unknown_model_falls_back_to_deepseek():
    """Unknown models fall back to deepseek-chat pricing (not KeyError)."""
    r = PRICING[MODEL_WORKER]
    expected = 10 * r["input"] + 5 * r["output"]
    cost = AIRouter._calculate_cost("unknown-model-xyz", 10, 5)
    assert abs(cost - expected) < 1e-12


def test_calculate_cost_returns_float():
    cost = AIRouter._calculate_cost(MODEL_WORKER, 1000, 500)
    assert isinstance(cost, float)


def test_anthropic_output_rate_more_expensive_than_input():
    r = PRICING[MODEL_CONTROLLER]
    assert r["output"] > r["input"]


def test_deepseek_cheaper_than_anthropic_per_output_token():
    assert PRICING[MODEL_WORKER]["output"] < PRICING[MODEL_CONTROLLER]["output"]


# ---------------------------------------------------------------------------
# _update_fallback_state
# ---------------------------------------------------------------------------

def test_fallback_state_both_available():
    router = make_router()
    router.deepseek_available = True
    router.anthropic_available = True
    router._update_fallback_state()
    assert router.fallback_active is False
    assert router.fallback_source is None


def test_fallback_state_deepseek_down():
    router = make_router()
    router.deepseek_available = False
    router.anthropic_available = True
    router._update_fallback_state()
    assert router.fallback_active is True
    assert router.fallback_source == "deepseek"


def test_fallback_state_anthropic_down():
    router = make_router()
    router.deepseek_available = True
    router.anthropic_available = False
    router._update_fallback_state()
    assert router.fallback_active is True
    assert router.fallback_source == "anthropic"


def test_fallback_state_both_down():
    router = make_router()
    router.deepseek_available = False
    router.anthropic_available = False
    router._update_fallback_state()
    assert router.fallback_active is True
    assert router.fallback_source == "both"


def test_fallback_state_recovery():
    """Recovering from fallback resets flags correctly."""
    router = make_router()
    router.deepseek_available = False
    router.anthropic_available = True
    router._update_fallback_state()
    assert router.fallback_active is True
    # DeepSeek recovers
    router.deepseek_available = True
    router._update_fallback_state()
    assert router.fallback_active is False
    assert router.fallback_source is None


# ---------------------------------------------------------------------------
# _log_usage + get_usage_stats
# ---------------------------------------------------------------------------

def test_log_usage_empty_initially():
    router = make_router()
    stats = router.get_usage_stats()
    assert stats["total_calls"] == 0
    assert stats["total_cost_usd"] == 0.0
    assert stats["fallback_events"] == 0


def test_log_usage_single_deepseek_call():
    router = make_router()
    router._log_usage(MODEL_WORKER, 100, 50, "chat")
    stats = router.get_usage_stats()
    assert stats["deepseek"]["calls"] == 1
    assert stats["deepseek"]["tokens_in"] == 100
    assert stats["deepseek"]["tokens_out"] == 50
    assert stats["total_calls"] == 1
    assert stats["total_cost_usd"] > 0


def test_log_usage_single_anthropic_call():
    router = make_router()
    router._log_usage(MODEL_CONTROLLER, 200, 100, "verify")
    stats = router.get_usage_stats()
    assert stats["anthropic"]["calls"] == 1
    assert stats["anthropic"]["tokens_in"] == 200
    assert stats["anthropic"]["tokens_out"] == 100


def test_log_usage_multiple_calls_aggregate():
    router = make_router()
    router._log_usage(MODEL_WORKER, 100, 50, "chat")
    router._log_usage(MODEL_WORKER, 200, 80, "generate")
    stats = router.get_usage_stats()
    assert stats["deepseek"]["calls"] == 2
    assert stats["deepseek"]["tokens_in"] == 300
    assert stats["deepseek"]["tokens_out"] == 130


def test_log_usage_mixed_models():
    router = make_router()
    router._log_usage(MODEL_WORKER, 100, 50, "chat")
    router._log_usage(MODEL_CONTROLLER, 200, 100, "verify")
    stats = router.get_usage_stats()
    assert stats["deepseek"]["calls"] == 1
    assert stats["anthropic"]["calls"] == 1
    assert stats["total_calls"] == 2


def test_log_usage_cost_accumulates_correctly():
    router = make_router()
    r_ds = PRICING[MODEL_WORKER]
    expected_ds = 100 * r_ds["input"] + 50 * r_ds["output"]
    r_an = PRICING[MODEL_CONTROLLER]
    expected_an = 200 * r_an["input"] + 100 * r_an["output"]
    router._log_usage(MODEL_WORKER, 100, 50, "chat")
    router._log_usage(MODEL_CONTROLLER, 200, 100, "verify")
    stats = router.get_usage_stats()
    assert abs(stats["total_cost_usd"] - (expected_ds + expected_an)) < 1e-10


def test_get_usage_stats_returns_required_keys():
    router = make_router()
    stats = router.get_usage_stats()
    assert "deepseek" in stats
    assert "anthropic" in stats
    assert "total_cost_usd" in stats
    assert "fallback_events" in stats
    assert "total_calls" in stats


# ---------------------------------------------------------------------------
# _warn_empty_prompt
# ---------------------------------------------------------------------------

def test_warn_empty_prompt_empty_string(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="src.ai_router"):
        AIRouter._warn_empty_prompt("", "test_caller")
    assert any("test_caller" in r.message for r in caplog.records)


def test_warn_empty_prompt_short_string(caplog):
    import logging
    short = "x" * (_MIN_SYSTEM_PROMPT_LEN - 1)
    with caplog.at_level(logging.WARNING, logger="src.ai_router"):
        AIRouter._warn_empty_prompt(short, "test_caller")
    assert len(caplog.records) > 0


def test_warn_empty_prompt_adequate_length(caplog):
    import logging
    adequate = "a" * _MIN_SYSTEM_PROMPT_LEN
    with caplog.at_level(logging.WARNING, logger="src.ai_router"):
        AIRouter._warn_empty_prompt(adequate, "test_caller")
    assert len(caplog.records) == 0


def test_warn_empty_prompt_none_value(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="src.ai_router"):
        AIRouter._warn_empty_prompt(None, "test_caller")  # type: ignore
    assert len(caplog.records) > 0


# ---------------------------------------------------------------------------
# get_cumulative_stats
# ---------------------------------------------------------------------------

def test_get_cumulative_stats_returns_copy():
    router = make_router()
    stats = router.get_cumulative_stats()
    stats["injected"] = True
    # Modifying the returned dict must not affect the internal state
    assert "injected" not in router._cumulative


def test_get_cumulative_stats_initial_zeros():
    router = make_router()
    stats = router.get_cumulative_stats()
    assert stats["deepseek"] == 0.0
    assert stats["anthropic"] == 0.0
    assert stats["total"] == 0.0
    assert stats["total_calls"] == 0


# ---------------------------------------------------------------------------
# set_cumulative_path / _save_cumulative / _load_cumulative (disk I/O)
# ---------------------------------------------------------------------------

def test_save_and_load_cumulative(tmp_path):
    router = make_router()
    router.set_cumulative_path(str(tmp_path))
    router._log_usage(MODEL_WORKER, 1000, 500, "chat")
    # After log_usage, cumulative should be saved to disk
    saved_file = tmp_path / "cumulative_usage.json"
    assert saved_file.exists()
    data = json.loads(saved_file.read_text())
    assert data["deepseek"] > 0
    assert data["total_calls"] == 1


def test_load_cumulative_existing_data(tmp_path):
    saved = {"deepseek": 1.23, "anthropic": 4.56, "total": 5.79, "total_calls": 10}
    (tmp_path / "cumulative_usage.json").write_text(json.dumps(saved))
    router = make_router()
    router.set_cumulative_path(str(tmp_path))
    stats = router.get_cumulative_stats()
    assert stats["deepseek"] == pytest.approx(1.23)
    assert stats["total_calls"] == 10


def test_load_cumulative_invalid_json(tmp_path):
    (tmp_path / "cumulative_usage.json").write_text("NOT_JSON{{{{")
    router = make_router()
    # Must not raise — gracefully falls back to defaults
    router.set_cumulative_path(str(tmp_path))
    stats = router.get_cumulative_stats()
    assert stats["total_calls"] == 0


def test_save_cumulative_no_path_is_noop():
    """_save_cumulative without a path should silently do nothing."""
    router = make_router()
    router._cumulative_path = None
    router._save_cumulative()  # must not raise


def test_cumulative_persists_across_router_instances(tmp_path):
    r1 = make_router()
    r1.set_cumulative_path(str(tmp_path))
    r1._log_usage(MODEL_CONTROLLER, 500, 200, "verify")
    cost1 = r1.get_cumulative_stats()["anthropic"]
    # New router instance reads the same file
    r2 = make_router()
    r2.set_cumulative_path(str(tmp_path))
    assert r2.get_cumulative_stats()["anthropic"] == pytest.approx(cost1)


# ---------------------------------------------------------------------------
# Truncation detection (Етап 4 находка, 2026-07-22)
# ---------------------------------------------------------------------------

class TestTruncationDetection:
    """Отрязан отговор трябва да се съобщава, не да минава тихо.

    FAILURE означава: reasoning worker модел, чието разсъждение изяжда
    бюджета от токени, ще връща счупен JSON, а логът ще показва само
    „невалиден JSON" — без да се разбере, че причината е таванът.
    """

    @staticmethod
    def _router_with_finish_reason(reason: str):
        from unittest.mock import MagicMock
        from src.ai_router import AIRouter

        r = AIRouter()
        client = MagicMock()
        client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(
                message=MagicMock(content='{"tasks": []}'),
                finish_reason=reason,
            )],
            usage=MagicMock(prompt_tokens=100, completion_tokens=4096),
        )
        r._deepseek_client = client
        return r

    def test_length_finish_reason_flags_truncation(self):
        r = self._router_with_finish_reason("length")
        result = r._chat_deepseek([{"role": "user", "content": "x"}], "system" * 40)
        assert result["truncated"] is True

    def test_normal_finish_reason_is_not_truncated(self):
        r = self._router_with_finish_reason("stop")
        result = r._chat_deepseek([{"role": "user", "content": "x"}], "system" * 40)
        assert result["truncated"] is False

    def test_truncation_is_logged(self, caplog):
        import logging
        r = self._router_with_finish_reason("length")
        with caplog.at_level(logging.WARNING):
            r._chat_deepseek([{"role": "user", "content": "x"}], "system" * 40)
        assert any("ОТРЯЗАН" in rec.message for rec in caplog.records)

    def test_content_still_returned_when_truncated(self):
        """Отрязаното съдържание не се изхвърля — извикващият решава."""
        r = self._router_with_finish_reason("length")
        result = r._chat_deepseek([{"role": "user", "content": "x"}], "system" * 40)
        assert result["content"] == '{"tasks": []}'


# ---------------------------------------------------------------------------
# Празен отговор от доставчика (серия 13.08.2026)
# ---------------------------------------------------------------------------

class TestEmptyResponseRetry:
    """Празен отговор е засечка на доставчика, не резултат на модела.

    FAILURE означава: 2-секунден отговор със седем изходни токена пак ще се
    брои за провалена генерация.  В серията от 13.08.2026 това бяха 5 от 40
    прогона — цели пет процента от статистиката за качеството на модела бяха
    чужда инфраструктура.
    """

    @staticmethod
    def _router(responses: list[tuple[str, int]]):
        """responses: списък от (съдържание, изходни токени) по реда на опитите."""
        from unittest.mock import MagicMock
        from src.ai_router import AIRouter

        r = AIRouter()
        client = MagicMock()
        client.chat.completions.create.side_effect = [
            MagicMock(
                choices=[MagicMock(message=MagicMock(content=content),
                                   finish_reason="stop")],
                usage=MagicMock(prompt_tokens=12000, completion_tokens=out),
            )
            for content, out in responses
        ]
        r._deepseek_client = client
        return r

    def test_empty_response_is_retried_and_recovers(self, monkeypatch):
        monkeypatch.setattr("src.ai_router.time.sleep", lambda _s: None)
        r = self._router([("", 7), ('{"tasks": []}', 900)])
        result = r._chat_deepseek([{"role": "user", "content": "x"}], "system" * 40)
        assert result["content"] == '{"tasks": []}'

    def test_persistent_empty_response_raises(self, monkeypatch):
        """След изчерпване на опитите се вдига грешка, не се връща празно.

        Празен низ, върнат тихо, се превръща в „невалиден JSON" няколко слоя
        по-нагоре — точно заблудата, която тази поправка премахва.
        """
        import pytest
        monkeypatch.setattr("src.ai_router.time.sleep", lambda _s: None)
        r = self._router([("", 7)] * 12)
        with pytest.raises(Exception, match="празен отговор"):
            r._chat_deepseek([{"role": "user", "content": "x"}], "system" * 40)

    def test_empty_attempt_is_still_billed(self, monkeypatch):
        """Входът на изхвърления опит е платен и трябва да личи в сметката."""
        monkeypatch.setattr("src.ai_router.time.sleep", lambda _s: None)
        r = self._router([("", 7), ('{"tasks": []}', 900)])
        r._chat_deepseek([{"role": "user", "content": "x"}], "system" * 40)
        assert any(entry["task_type"] == "chat-празен" for entry in r.usage_log)

    def test_worker_failure_does_not_disable_the_whole_session(self):
        """Една засечка НЕ прехвърля цялата сесия на скъпия контрольор.

        FAILURE означава: първият отказ на работника пак ще изключва пътя му
        до края на живота на инстанцията.  На 12.08.2026 това струваше $3.92 —
        18 прогона, поискани от шест евтини работника, бяха обслужени от Opus,
        а резултатът беше представен като сравнение между модели.
        """
        from unittest.mock import MagicMock
        from src.ai_router import AIRouter

        r = AIRouter()
        r.anthropic_available = True
        r._chat_deepseek = MagicMock(side_effect=RuntimeError("засечка"))
        r._chat_anthropic = MagicMock(return_value={"content": "{}", "cost": 0.0})

        r.chat([{"role": "user", "content": "x"}], "system")

        assert r.deepseek_available is True, "работникът е изключен след ЕДИН провал"
        assert r._chat_anthropic.called, "резервният път не се е задействал"

    def test_worker_disabled_only_after_three_consecutive_failures(self):
        from unittest.mock import MagicMock
        from src.ai_router import AIRouter

        r = AIRouter()
        r.anthropic_available = True
        r._chat_deepseek = MagicMock(side_effect=RuntimeError("засечка"))
        r._chat_anthropic = MagicMock(return_value={"content": "{}", "cost": 0.0})

        for _ in range(2):
            r.chat([{"role": "user", "content": "x"}], "system")
        assert r.deepseek_available is True

        r.chat([{"role": "user", "content": "x"}], "system")
        assert r.deepseek_available is False, "системната засечка трябва да го изключи"

    def test_success_resets_the_failure_counter(self):
        """Разпръснати засечки не бива да се натрупват до изключване."""
        from unittest.mock import MagicMock
        from src.ai_router import AIRouter

        r = AIRouter()
        r.anthropic_available = True
        r._chat_anthropic = MagicMock(return_value={"content": "{}", "cost": 0.0})
        r._chat_deepseek = MagicMock(side_effect=[
            RuntimeError("засечка"), {"content": "{}", "cost": 0.0},
            RuntimeError("засечка"), RuntimeError("засечка"),
        ])

        for _ in range(4):
            r.chat([{"role": "user", "content": "x"}], "system")

        assert r.deepseek_available is True, "успехът по средата не е нулирал брояча"

    def test_fallback_says_the_controller_took_over(self, caplog):
        import logging
        from unittest.mock import MagicMock
        from src.ai_router import AIRouter

        r = AIRouter()
        r.anthropic_available = True
        r._chat_deepseek = MagicMock(side_effect=RuntimeError("засечка"))
        r._chat_anthropic = MagicMock(return_value={"content": "{}", "cost": 0.0})

        with caplog.at_level(logging.WARNING):
            r.chat([{"role": "user", "content": "x"}], "system")

        assert any("контрольора" in rec.message for rec in caplog.records), \
            "прехвърлянето към по-скъпия модел минава мълчаливо"

    def test_whitespace_only_counts_as_empty(self, monkeypatch):
        monkeypatch.setattr("src.ai_router.time.sleep", lambda _s: None)
        r = self._router([("   \n  ", 40), ('{"tasks": []}', 900)])
        result = r._chat_deepseek([{"role": "user", "content": "x"}], "system" * 40)
        assert result["content"] == '{"tasks": []}'


# ---------------------------------------------------------------------------
# Claude worker: streaming при голям изход + best-effort JSON schema (2026-08)
# ---------------------------------------------------------------------------

class _FakeUsage:
    input_tokens = 10
    output_tokens = 20


class _FakeBlock:
    type = "text"
    text = '{"tasks": []}'


class _FakeMessage:
    content = [_FakeBlock()]
    usage = _FakeUsage()
    stop_reason = "end_turn"


class _FakeStreamCtx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return _FakeMessage()


class _FakeMessages:
    def __init__(self):
        self.create_calls = []
        self.stream_calls = []

    def create(self, **kw):
        self.create_calls.append(kw)
        # Симулирай доставчик, който отхвърля structured output.
        if "output_config" in kw:
            raise TypeError("output_config не се поддържа")
        return _FakeMessage()

    def stream(self, **kw):
        self.stream_calls.append(kw)
        if "output_config" in kw:
            raise TypeError("output_config не се поддържа")
        return _FakeStreamCtx()


class _FakeClient:
    def __init__(self):
        self.messages = _FakeMessages()


def _worker_router(monkeypatch) -> tuple[AIRouter, _FakeClient]:
    r = make_router()
    client = _FakeClient()
    monkeypatch.setattr(r, "_get_anthropic", lambda: client)
    return r, client


def test_worker_claude_streams_for_large_max_tokens(monkeypatch):
    """max_tokens ≥ прага → стрийминг (иначе дълъг отговор рискува timeout)."""
    r, client = _worker_router(monkeypatch)
    out = r._chat_worker_claude([{"role": "user", "content": "hi"}], "sys",
                                model="claude-sonnet-5", max_tokens=16000)
    assert client.messages.stream_calls, "трябваше да ползва stream()"
    assert not client.messages.create_calls, "не биваше да ползва create()"
    assert out["content"] == '{"tasks": []}'


def test_worker_claude_creates_for_small_max_tokens(monkeypatch):
    """Малък изход → обикновен create (без стрийминг)."""
    r, client = _worker_router(monkeypatch)
    r._chat_worker_claude([{"role": "user", "content": "hi"}], "sys",
                          model="claude-sonnet-5", max_tokens=4000)
    assert client.messages.create_calls and not client.messages.stream_calls


def test_worker_claude_does_not_send_structured_output(monkeypatch):
    """Жив тест 2026-08: строгата schema триеше полета / удряше union-лимит.
    Затова Claude worker-ът НЕ праща output_config — само 1 заявка, без него
    (валидният JSON и материалният enum се налагат през промпта)."""
    r, client = _worker_router(monkeypatch)
    schema = {"type": "object", "properties": {"tasks": {"type": "array"}}}
    out = r._chat_worker_claude([{"role": "user", "content": "hi"}], "sys",
                                model="claude-sonnet-5", max_tokens=4000,
                                response_schema=schema)
    assert len(client.messages.create_calls) == 1
    assert "output_config" not in client.messages.create_calls[0]
    assert out["content"] == '{"tasks": []}'


# ===================================================================
# Cost accounting при Claude през OpenRouter (одит 2026-08)
# ===================================================================

def test_cost_sonnet_via_openrouter_not_priced_as_deepseek():
    """DEEPSEEK_MODEL=anthropic/claude-sonnet-5 → Sonnet тарифа (~$12/2M),
    НЕ DeepSeek ($0.70/2M).  Семейството се разпознава по име."""
    r = make_router()
    c = r._calculate_cost("anthropic/claude-sonnet-5", 1_000_000, 1_000_000)
    assert abs(c - 12.0) < 0.01, c


def test_cost_opus_recognized():
    r = make_router()
    c = r._calculate_cost("anthropic/claude-opus-5", 1_000_000, 1_000_000)
    assert abs(c - 30.0) < 0.01, c


def test_cost_deepseek_still_cheap():
    r = make_router()
    c = r._calculate_cost("deepseek-chat", 1_000_000, 1_000_000)
    assert abs(c - 0.70) < 0.01, c


# ===================================================================
# Работникът се изключва за сесията само при СИСТЕМНА засечка —
# по ВСИЧКИ пътища, не само по генериращия (одит 17.08.2026)
# ===================================================================
#
# FAILURE означава: src/ai_router.py :: една преходна грешка при проверката,
# при прилагането на корекции или при OCR пак ще прехвърля цялата останала
# сесия — включително генерирането — на контрольора, който е 5–25 пъти
# по-скъп, без това да личи в изхода.

def test_verify_failure_does_not_disable_the_worker():
    """Проверката е РЕЗЕРВНИЯТ път на работника — една засечка не го изключва."""
    from unittest.mock import MagicMock

    r = make_router()
    r.anthropic_available = False       # контрольорът е извън строя
    r.deepseek_available = True
    r._verify_with_model = MagicMock(side_effect=RuntimeError("засечка"))

    r.verify_schedule("{}", "правила")

    assert r.deepseek_available is True, "работникът е изключен след ЕДИН провал"


def test_verify_failures_disable_the_worker_only_when_systemic():
    from unittest.mock import MagicMock

    r = make_router()
    r.anthropic_available = False
    r.deepseek_available = True
    r._verify_with_model = MagicMock(side_effect=RuntimeError("засечка"))

    for _ in range(2):
        r.verify_schedule("{}", "правила")
    assert r.deepseek_available is True

    r.verify_schedule("{}", "правила")
    assert r.deepseek_available is False, "системната засечка трябва да го изключи"


def test_corrections_failure_does_not_disable_the_worker():
    from unittest.mock import MagicMock

    r = make_router()
    r.deepseek_available = True
    r.anthropic_available = True
    r._apply_with_model = MagicMock(side_effect=RuntimeError("засечка"))

    r.apply_corrections("{}", [{"task_id": 1, "fix": "x"}])

    assert r.deepseek_available is True, "работникът е изключен след ЕДИН провал"


def test_ocr_failure_of_a_separate_vision_model_spares_the_worker(monkeypatch):
    """`OCR_MODEL` без vision достъп проваля ВСЯКА страница.

    Това не казва нищо за работника, който с текста се справя — а преди
    поправката отнасяше и генерирането със себе си още на първата страница.
    """
    from unittest.mock import MagicMock

    monkeypatch.setattr("src.ai_router.MODEL_OCR", "vision/друг-модел")
    r = make_router()
    r.deepseek_available = True
    r.anthropic_available = True
    r._ocr_deepseek = MagicMock(side_effect=RuntimeError("няма vision"))
    r._ocr_anthropic = MagicMock(return_value="текст")

    for _ in range(5):
        assert r.ocr_pdf_page("base64") == "текст"

    assert r.deepseek_available is True, "OCR моделът е изключил работника"
    assert r.ocr_available is False, "провалящият се OCR модел се пробва вечно"
    assert r._ocr_anthropic.call_count == 5, "резервният път не се е задействал"


def test_ocr_failure_counts_for_the_worker_when_it_is_the_same_model(monkeypatch):
    """Без зададен `OCR_MODEL` OCR-ът върви през самия работник — тогава
    провалите му са негови и след третия го изключват."""
    from unittest.mock import MagicMock
    from src.ai_router import MODEL_WORKER as _worker

    monkeypatch.setattr("src.ai_router.MODEL_OCR", _worker)
    r = make_router()
    r.deepseek_available = True
    r.anthropic_available = True
    r._ocr_deepseek = MagicMock(side_effect=RuntimeError("засечка"))
    r._ocr_anthropic = MagicMock(return_value="текст")

    for _ in range(2):
        r.ocr_pdf_page("base64")
    assert r.deepseek_available is True

    r.ocr_pdf_page("base64")
    assert r.deepseek_available is False, "системната засечка трябва да го изключи"
