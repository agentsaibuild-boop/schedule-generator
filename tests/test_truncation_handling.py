"""Unit tests: отрязан отговор спира генерирането (BACKLOG т.6).

Одитът посочи: `ai_router` задаваше `truncated: True` при
`finish_reason=length`, тестовете дори проверяваха, че съдържанието НЕ се
изхвърля — „извикващият решава".  Но извикващият не решаваше нищо: полето
висеше неизползвано, съдържанието продължаваше по веригата и се проявяваше
чак при парсването като „невалиден JSON".

Отрязан график не е частичен график — той е счупен.  Половин JSON не е
50% график.

Освен това Anthropic пътят нямаше еквивалент изобщо (`stop_reason ==
"max_tokens"`).

FAILURE означава: моделът пак може да върне половин график и това да се
представи като проблем с данните вместо с бюджета за токени.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai_processor import AIProcessor, gen_max_tokens  # noqa: E402
from src.ai_router import AIRouter, worker_max_tokens  # noqa: E402


# ===================================================================
# Разпознаване — двата доставчика
# ===================================================================

def _deepseek_router(finish_reason: str) -> AIRouter:
    router = AIRouter()
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(
            message=MagicMock(content='{"tasks": [{"id": "A"'),
            finish_reason=finish_reason,
        )],
        usage=MagicMock(prompt_tokens=1000, completion_tokens=4096),
    )
    router._deepseek_client = client
    return router


def _anthropic_router(stop_reason: str) -> AIRouter:
    router = AIRouter()
    client = MagicMock()
    response = MagicMock(
        content=[MagicMock(text='{"tasks": [{"id": "A"')],
        usage=MagicMock(input_tokens=1000, output_tokens=4096),
    )
    response.stop_reason = stop_reason
    client.messages.create.return_value = response
    router._anthropic_client = client
    return router


def test_deepseek_length_finish_reason_is_truncation():
    result = _deepseek_router("length")._chat_deepseek([{"role": "user", "content": "x"}], "s" * 200)
    assert result["truncated"] is True


def test_deepseek_stop_is_not_truncation():
    result = _deepseek_router("stop")._chat_deepseek([{"role": "user", "content": "x"}], "s" * 200)
    assert result["truncated"] is False


def test_anthropic_max_tokens_is_truncation():
    """Одит: Anthropic пътят нямаше никаква проверка."""
    result = _anthropic_router("max_tokens")._chat_anthropic(
        [{"role": "user", "content": "x"}], "s" * 200)
    assert result["truncated"] is True


def test_anthropic_end_turn_is_not_truncation():
    result = _anthropic_router("end_turn")._chat_anthropic(
        [{"role": "user", "content": "x"}], "s" * 200)
    assert result["truncated"] is False


def test_both_providers_report_the_same_field():
    """Извикващият не бива да пита различно според доставчика."""
    a = _deepseek_router("stop")._chat_deepseek([{"role": "user", "content": "x"}], "s" * 200)
    b = _anthropic_router("end_turn")._chat_anthropic(
        [{"role": "user", "content": "x"}], "s" * 200)
    assert "truncated" in a and "truncated" in b


# ===================================================================
# Флагът СЕ ЧЕТЕ — това е същината
# ===================================================================

class _KnowledgeStub:
    """Минимален заместител на KnowledgeManager.

    MagicMock не става: методите му връщат MagicMock, а
    `build_verification_prompt` прави `"\\n".join(parts)` и `skills_path /
    "references"` — тоест гърми преди да стигне до тестваното поведение.
    """

    def __init__(self) -> None:
        # Несъществуваща папка → блокът с референции се пропуска чисто.
        self.skills_path = Path("/несъществуваща/папка")

    def __getattr__(self, name):
        return lambda *args, **kwargs: "знание"


def _processor(chat_result: dict) -> AIProcessor:
    router = MagicMock()
    router.chat.return_value = chat_result
    router.deepseek_available = True
    router.anthropic_available = False
    return AIProcessor(router=router, knowledge_manager=_KnowledgeStub())


TRUNCATED = {
    "content": '{"tasks": [{"id": "A", "name": "Полаг',
    "model": "test", "cost": 0.01, "truncated": True,
    "usage": {"input_tokens": 1000, "output_tokens": 4096},
}


def test_truncated_generation_returns_error():
    processor = _processor(TRUNCATED)
    result = processor.generate_schedule(
        analysis={"analysis": "тест"}, project_type="довеждащ")
    assert result["status"] == "error"


def test_truncated_generation_is_flagged_as_such():
    processor = _processor(TRUNCATED)
    result = processor.generate_schedule(
        analysis={"analysis": "тест"}, project_type="довеждащ")
    assert result.get("truncated") is True


def test_error_message_names_the_real_cause():
    """Не 'невалиден JSON', а 'отговорът беше отрязан'."""
    processor = _processor(TRUNCATED)
    result = processor.generate_schedule(
        analysis={"analysis": "тест"}, project_type="довеждащ")
    assert "отрязан" in result["message"]


def test_error_message_reports_the_token_count():
    processor = _processor(TRUNCATED)
    result = processor.generate_schedule(
        analysis={"analysis": "тест"}, project_type="довеждащ")
    assert "4096" in result["message"]


def test_error_message_suggests_what_to_do():
    processor = _processor(TRUNCATED)
    result = processor.generate_schedule(
        analysis={"analysis": "тест"}, project_type="довеждащ")
    assert "етапи" in result["message"] or "MAX_TOKENS" in result["message"]


def test_truncated_content_never_reaches_the_pipeline():
    """Половин JSON не бива да мине за график."""
    processor = _processor(TRUNCATED)
    result = processor.generate_schedule(
        analysis={"analysis": "тест"}, project_type="довеждащ")
    assert "schedule" not in result


def test_untruncated_generation_proceeds():
    """Нормалният път не бива да се засегне."""
    processor = _processor({
        "content": '{"tasks": [], "total_duration": 0}',
        "model": "test", "cost": 0.01, "truncated": False,
        "usage": {"input_tokens": 100, "output_tokens": 50},
    })
    processor.router.run_correction_cycle.return_value = {
        "status": "approved", "schedule": {"tasks": []}, "cycles": 1,
        "total_cost": 0.0,
    }
    result = processor.generate_schedule(
        analysis={"analysis": "тест"}, project_type="довеждащ")
    assert result["status"] != "error"


def test_missing_truncated_key_is_treated_as_fine():
    """Стар router без полето не бива да блокира всичко."""
    processor = _processor({
        "content": '{"tasks": [], "total_duration": 0}',
        "model": "test", "cost": 0.01,
        "usage": {"input_tokens": 100, "output_tokens": 50},
    })
    processor.router.run_correction_cycle.return_value = {
        "status": "approved", "schedule": {"tasks": []}, "cycles": 1,
        "total_cost": 0.0,
    }
    result = processor.generate_schedule(
        analysis={"analysis": "тест"}, project_type="довеждащ")
    assert result["status"] != "error"


# ===================================================================
# Регресия за формулировката на стария тест
# ===================================================================

def test_the_old_test_claim_is_no_longer_true():
    """Старият тест твърдеше: „извикващият решава".  Сега наистина решава."""
    source = (Path(__file__).parent.parent / "src" / "ai_processor.py").read_text(
        encoding="utf-8")
    assert 'gen_result.get("truncated")' in source


# ===================================================================
# БЮДЖЕТЪТ ЗА ТОКЕНИ — настройката трябва да върши работа
#
# ПРОГОНИ 10.08.2026: 11 отговора излязоха отрязани „при таван 8192" и убиха
# 14 от 40 прогона.  Предупреждението съветваше да се вдигне
# `WORKER_MAX_TOKENS`, но тя се прилагаше САМО по Claude-клона — по
# DeepSeek/OpenRouter минаваше `GEN_MAX_TOKENS` с твърд default 8192.
# Тоест съветът в лога беше неизпълним: нямаше настройка, която да помогне.
# ===================================================================

def _recording_deepseek_router(fail_above: int | None = None):
    """Router, който ЗАПИСВА всяка заявка; по избор отказва висок таван.

    `fail_above` играе работник с ТВЪРД таван на изхода (DeepSeek V3 директно):
    заявка над него не се отрязва, а се отхвърля.
    """
    seen: list[dict] = []

    def create(**kwargs):
        seen.append(kwargs)
        if fail_above is not None and kwargs["max_tokens"] > fail_above:
            raise ValueError(
                f"max_tokens {kwargs['max_tokens']} над тавана на модела")
        if kwargs.get("stream"):
            return iter([MagicMock(
                choices=[MagicMock(delta=MagicMock(content="{}"),
                                   finish_reason="stop")],
                usage=MagicMock(prompt_tokens=10, completion_tokens=5))])
        return MagicMock(
            choices=[MagicMock(message=MagicMock(content="{}"),
                               finish_reason="stop")],
            usage=MagicMock(prompt_tokens=10, completion_tokens=5))

    router = AIRouter()
    client = MagicMock()
    client.chat.completions.create.side_effect = create
    router._deepseek_client = client
    router.deepseek_available = True
    router.anthropic_available = False
    return router, seen


def test_generation_asks_for_the_full_worker_ceiling(monkeypatch):
    """Без изрична настройка генерирането иска тавана на РАБОТНИКА, не 8192."""
    monkeypatch.delenv("GEN_MAX_TOKENS", raising=False)
    monkeypatch.setenv("WORKER_MAX_TOKENS", "16000")
    assert gen_max_tokens() == worker_max_tokens() == 16000


def test_raising_the_worker_ceiling_raises_the_generation_ceiling(monkeypatch):
    """Едно копче, не две, които тихо си противоречат."""
    monkeypatch.delenv("GEN_MAX_TOKENS", raising=False)
    monkeypatch.setenv("WORKER_MAX_TOKENS", "32000")
    assert gen_max_tokens() == 32000


def test_explicit_generation_ceiling_still_wins(monkeypatch):
    monkeypatch.setenv("WORKER_MAX_TOKENS", "16000")
    monkeypatch.setenv("GEN_MAX_TOKENS", "12000")
    assert gen_max_tokens() == 12000


def test_the_raised_ceiling_reaches_the_deepseek_request(monkeypatch):
    """Същината на дефекта: настройката трябва да стигне ДО заявката."""
    monkeypatch.delenv("GEN_MAX_TOKENS", raising=False)
    monkeypatch.setenv("WORKER_MAX_TOKENS", "16000")
    monkeypatch.setattr("src.ai_router.WORKER_MODEL_OVERRIDE", "")
    router, seen = _recording_deepseek_router()

    router.chat([{"role": "user", "content": "x"}], "s" * 200,
                max_tokens=gen_max_tokens())

    assert seen[0]["max_tokens"] == 16000


def test_provider_refusing_the_high_ceiling_falls_a_rung_down():
    """Работник с твърд таван не бива да превръща настройката в отказ."""
    router, seen = _recording_deepseek_router(fail_above=8192)

    result = router._chat_deepseek(
        [{"role": "user", "content": "x"}], "s" * 200, max_tokens=16000)

    assert [kw["max_tokens"] for kw in seen] == [16000, 8192]
    assert result["truncated"] is False


def test_no_downgrade_when_the_full_ceiling_is_accepted():
    """Стъпалото надолу е за отказ, не разход по подразбиране."""
    router, seen = _recording_deepseek_router()

    router._chat_deepseek(
        [{"role": "user", "content": "x"}], "s" * 200, max_tokens=16000)

    assert len(seen) == 1


def test_a_failure_on_every_rung_still_raises():
    """Слизането надолу не бива да ЗАМАЗВА истински провал на доставчика."""
    router, _ = _recording_deepseek_router(fail_above=0)

    with pytest.raises(ValueError):
        router._chat_deepseek(
            [{"role": "user", "content": "x"}], "s" * 200, max_tokens=16000)
