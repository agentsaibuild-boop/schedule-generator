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

from src.ai_router import AIRouter, PRICING, _MIN_SYSTEM_PROMPT_LEN


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
    cost = AIRouter._calculate_cost("deepseek-chat", 0, 0)
    assert cost == 0.0


def test_calculate_cost_deepseek_input_only():
    rate = PRICING["deepseek-chat"]["input"]
    cost = AIRouter._calculate_cost("deepseek-chat", 1_000, 0)
    assert abs(cost - rate * 1_000) < 1e-12


def test_calculate_cost_deepseek_output_only():
    rate = PRICING["deepseek-chat"]["output"]
    cost = AIRouter._calculate_cost("deepseek-chat", 0, 1_000)
    assert abs(cost - rate * 1_000) < 1e-12


def test_calculate_cost_deepseek_both():
    r = PRICING["deepseek-chat"]
    expected = 100 * r["input"] + 200 * r["output"]
    assert abs(AIRouter._calculate_cost("deepseek-chat", 100, 200) - expected) < 1e-12


def test_calculate_cost_anthropic():
    r = PRICING["claude-sonnet-4-6"]
    expected = 50 * r["input"] + 30 * r["output"]
    assert abs(AIRouter._calculate_cost("claude-sonnet-4-6", 50, 30) - expected) < 1e-12


def test_calculate_cost_unknown_model_falls_back_to_deepseek():
    """Unknown models fall back to deepseek-chat pricing (not KeyError)."""
    r = PRICING["deepseek-chat"]
    expected = 10 * r["input"] + 5 * r["output"]
    cost = AIRouter._calculate_cost("unknown-model-xyz", 10, 5)
    assert abs(cost - expected) < 1e-12


def test_calculate_cost_returns_float():
    cost = AIRouter._calculate_cost("deepseek-chat", 1000, 500)
    assert isinstance(cost, float)


def test_anthropic_output_rate_more_expensive_than_input():
    r = PRICING["claude-sonnet-4-6"]
    assert r["output"] > r["input"]


def test_deepseek_cheaper_than_anthropic_per_output_token():
    assert PRICING["deepseek-chat"]["output"] < PRICING["claude-sonnet-4-6"]["output"]


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
    router._log_usage("deepseek-chat", 100, 50, "chat")
    stats = router.get_usage_stats()
    assert stats["deepseek"]["calls"] == 1
    assert stats["deepseek"]["tokens_in"] == 100
    assert stats["deepseek"]["tokens_out"] == 50
    assert stats["total_calls"] == 1
    assert stats["total_cost_usd"] > 0


def test_log_usage_single_anthropic_call():
    router = make_router()
    router._log_usage("claude-sonnet-4-6", 200, 100, "verify")
    stats = router.get_usage_stats()
    assert stats["anthropic"]["calls"] == 1
    assert stats["anthropic"]["tokens_in"] == 200
    assert stats["anthropic"]["tokens_out"] == 100


def test_log_usage_multiple_calls_aggregate():
    router = make_router()
    router._log_usage("deepseek-chat", 100, 50, "chat")
    router._log_usage("deepseek-chat", 200, 80, "generate")
    stats = router.get_usage_stats()
    assert stats["deepseek"]["calls"] == 2
    assert stats["deepseek"]["tokens_in"] == 300
    assert stats["deepseek"]["tokens_out"] == 130


def test_log_usage_mixed_models():
    router = make_router()
    router._log_usage("deepseek-chat", 100, 50, "chat")
    router._log_usage("claude-sonnet-4-6", 200, 100, "verify")
    stats = router.get_usage_stats()
    assert stats["deepseek"]["calls"] == 1
    assert stats["anthropic"]["calls"] == 1
    assert stats["total_calls"] == 2


def test_log_usage_cost_accumulates_correctly():
    router = make_router()
    r_ds = PRICING["deepseek-chat"]
    expected_ds = 100 * r_ds["input"] + 50 * r_ds["output"]
    r_an = PRICING["claude-sonnet-4-6"]
    expected_an = 200 * r_an["input"] + 100 * r_an["output"]
    router._log_usage("deepseek-chat", 100, 50, "chat")
    router._log_usage("claude-sonnet-4-6", 200, 100, "verify")
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
    router._log_usage("deepseek-chat", 1000, 500, "chat")
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
    r1._log_usage("claude-sonnet-4-6", 500, 200, "verify")
    cost1 = r1.get_cumulative_stats()["anthropic"]
    # New router instance reads the same file
    r2 = make_router()
    r2.set_cumulative_path(str(tmp_path))
    assert r2.get_cumulative_stats()["anthropic"] == pytest.approx(cost1)
