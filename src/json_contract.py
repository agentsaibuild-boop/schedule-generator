"""Договор за JSON отговорите от AI моделите (P7).

ЗАЩО: `_parse_json_response` връщаше при провал
`{"approved": False, "issues": ["Invalid JSON response from AI"]}` —
структура, НЕРАЗЛИЧИМА от легитимно отхвърляне на график.  Счупен JSON
изглеждаше като „графикът има проблеми", задействаше корекционни цикли и
накрая излизаше „needs_human_review".  Потребителят виждаше проблем с
графика си там, където всъщност се беше счупило парсването.

Освен това нищо не проверяваше СТРУКТУРАТА: валиден JSON с липсващи или
грешно типизирани полета минаваше нататък и се спъваше по-късно, далеч от
мястото на грешката.

Тук са три неща:
1. `parse_json_strict` — разграничава „няма JSON" от „ето JSON".
2. `coerce` — привежда към очакваната форма и КАЗВА какво е липсвало.
3. Спецификации за трите отговора, които приложението очаква.
"""

from __future__ import annotations

import json
import logging
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)


class JSONContractError(ValueError):
    """Отговорът на модела не може да се приведе към очаквания договор.

    Отделен тип, за да може извикващият да разграничи „моделът отговори
    нещо неизползваемо" от „API-то не работи" — двете искат различна
    реакция (повторен опит срещу превключване към друг доставчик).
    """


class ParseResult(NamedTuple):
    """Резултат от парсване на отговор."""

    data: dict | None       # None = не е намерен обект
    error: str              # празно, ако всичко е наред
    recovered: bool = False # True = извлечен от заобикалящ текст


def _strip_fences(text: str) -> str:
    """Махни ```json ... ``` ограждане, ако има."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = [ln for ln in stripped.split("\n") if not ln.strip().startswith("```")]
    return "\n".join(lines).strip()


def parse_json_strict(raw: str) -> ParseResult:
    """Извлечи JSON обект от отговор на модел.

    За разлика от стария парсер НЕ измисля резултат при провал — връща
    `data=None` и причина, за да може извикващият да реши.

    Args:
        raw: Суровият текст на отговора.

    Returns:
        ParseResult.  `recovered=True` означава, че JSON-ът е бил ограден с
        друг текст — това е сигнал за небрежен отговор, не е грешка.
    """
    if not raw or not raw.strip():
        return ParseResult(None, "празен отговор")

    text = _strip_fences(raw)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        first_error = str(exc)
    else:
        if isinstance(data, dict):
            return ParseResult(data, "")
        return ParseResult(None, f"очакван обект, получен {type(data).__name__}")

    # Втори опит: изкопай най-външния обект от заобикалящ текст.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(data, dict):
                return ParseResult(data, "", recovered=True)

    return ParseResult(None, f"невалиден JSON: {first_error}")


def coerce(data: dict, spec: dict[str, tuple[type, Any]]) -> tuple[dict, list[str]]:
    """Приведи dict към очакваната форма.

    Args:
        data: Парснатият отговор.
        spec: {поле: (очакван_тип, стойност_по_подразбиране)}.

    Returns:
        (приведен dict, списък с бележки какво е било поправено).
        Бележките се логват — тихото попълване на стойности по подразбиране
        крие точно това, което трябва да се види при отстраняване на грешка.
    """
    result: dict[str, Any] = {}
    problems: list[str] = []

    for field, (expected, default) in spec.items():
        if field not in data:
            result[field] = default() if callable(default) else default
            problems.append(f"липсва '{field}'")
            continue

        value = data[field]

        # bool е подтип на int — не бива да минава за число и обратно.
        if expected is bool:
            if isinstance(value, bool):
                result[field] = value
                continue
            problems.append(f"'{field}' не е булево ({type(value).__name__})")
            result[field] = default() if callable(default) else default
            continue

        if isinstance(value, bool) and expected in (int, float):
            problems.append(f"'{field}' е булево, а се очаква число")
            result[field] = default() if callable(default) else default
            continue

        if isinstance(value, expected):
            result[field] = value
            continue

        # Тесни, безопасни превръщания.
        if expected is list and value is None:
            result[field] = []
            problems.append(f"'{field}' е null, прието като празен списък")
            continue
        if expected is str and isinstance(value, (int, float)):
            result[field] = str(value)
            continue

        problems.append(
            f"'{field}' е {type(value).__name__}, а се очаква {expected.__name__}"
        )
        result[field] = default() if callable(default) else default

    return result, problems


# ---------------------------------------------------------------------------
# Спецификации на очакваните отговори
# ---------------------------------------------------------------------------

VERIFICATION_SPEC: dict[str, tuple[type, Any]] = {
    "approved": (bool, False),
    "issues": (list, list),
    "corrections": (list, list),
    "summary": (str, ""),
}

CORRECTION_SPEC: dict[str, tuple[type, Any]] = {
    "schedule": (dict, dict),
    "applied": (list, list),
}

LESSON_SPEC: dict[str, tuple[type, Any]] = {
    "approved": (bool, False),
    "formatted_lesson": (str, ""),
    "reason": (str, ""),
}


def parse_contract(raw: str, spec: dict[str, tuple[type, Any]], what: str) -> dict:
    """Парсни и приведи наведнъж; хвърли, ако е неизползваемо.

    Args:
        raw: Суровият отговор.
        spec: Очакваната форма.
        what: Име на операцията за съобщенията ('верификация', 'корекция'...).

    Returns:
        Приведеният dict.

    Raises:
        JSONContractError: ако в отговора няма обект.
    """
    parsed = parse_json_strict(raw)
    if parsed.data is None:
        raise JSONContractError(f"{what}: {parsed.error}")

    if parsed.recovered:
        logger.info("%s: JSON беше ограден с друг текст — извлечен успешно.", what)

    result, problems = coerce(parsed.data, spec)
    if problems:
        logger.warning("%s: отговорът не спазва формата — %s", what, "; ".join(problems))
    return result
