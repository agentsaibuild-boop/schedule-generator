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

from src.ai_processor import AIProcessor  # noqa: E402
from src.ai_router import AIRouter  # noqa: E402


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
