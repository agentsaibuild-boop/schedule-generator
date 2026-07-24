"""Прозрачност за AI-генерирано съдържание — EU AI Act, чл. 50.

ЕДИНСТВЕН източник на текстовете и маркерите за разкриване.  Разпръснати
низове из UI-а и експортите се разминават с времето; тук са на едно място,
за да може юрист да ги прегледне наведнъж.

Приложими срокове (проверени на 2026-07-22):
  - чл. 50(1) — потребителят трябва да знае, че взаимодейства с AI система:
    прилага се от **2 август 2026**.  Digital Omnibus отложи задълженията
    за високорискови системи към 2027/2028, но чл. 50 остава непроменен.
  - чл. 50(2) — генерираното съдържание да е маркирано в машинно четим
    формат: за системи, пуснати на пазара ПРЕДИ 2 август 2026, важи
    гратисен период до **2 декември 2026**.

ВАЖНО: това е инженерна имплементация, не правен съвет.  Дали конкретното
внедряване попада в обхвата и дали текстовете са достатъчни, се преценява
от юрист.  Кодът тук осигурява механизма — маркирането да съществува,
да е машинно четимо и да е видимо.
"""

from __future__ import annotations

from datetime import datetime

# Идентификатор на системата в машинно четимите маркери.
SYSTEM_NAME = "ВиК Schedule Generator"

# ---------------------------------------------------------------------------
# чл. 50(1) — взаимодействие с AI система
# ---------------------------------------------------------------------------

CHAT_DISCLOSURE_BG = (
    "Разговаряте с AI система. Графиците се генерират автоматично и "
    "изискват проверка от правоспособен инженер преди употреба."
)

CHAT_DISCLOSURE_EN = (
    "You are interacting with an AI system. Schedules are generated "
    "automatically and require review by a qualified engineer before use."
)

# ---------------------------------------------------------------------------
# чл. 50(2) — маркиране на генерираното съдържание
# ---------------------------------------------------------------------------

CONTENT_DISCLOSURE_BG = (
    "Генерирано с изкуствен интелект. Подлежи на инженерна проверка."
)

CONTENT_DISCLOSURE_EN = "AI-generated content. Subject to engineering review."

# Дълъг вариант за бележки в MS Project и подобни полета.
CONTENT_NOTICE_LONG_BG = (
    "ВНИМАНИЕ: Този график е генериран автоматично от AI система "
    f"({SYSTEM_NAME}). Продължителностите са изчислени по нормите в "
    "config/productivities.json. Документът НЕ замества преценката на "
    "правоспособен проектант и подлежи на проверка преди подаване към "
    "възложител."
)


def machine_readable_marker(generated_at: datetime | None = None) -> dict:
    """Машинно четим маркер за вграждане в JSON/XML/метаданни.

    Args:
        generated_at: Момент на генериране.  По подразбиране — сега.

    Returns:
        Плосък dict, годен за JSON сериализация и за PDF метаданни.
    """
    stamp = (generated_at or datetime.now()).isoformat(timespec="seconds")
    return {
        "ai_generated": True,
        "ai_system": SYSTEM_NAME,
        "ai_disclosure": CONTENT_DISCLOSURE_EN,
        "ai_disclosure_bg": CONTENT_DISCLOSURE_BG,
        "generated_at": stamp,
        "requires_human_review": True,
    }


def pdf_metadata_keywords(generated_at: datetime | None = None) -> str:
    """Ключови думи за PDF метаданните (машинно четимо поле)."""
    marker = machine_readable_marker(generated_at)
    return (
        f"ai-generated=true; ai-system={marker['ai_system']}; "
        f"generated-at={marker['generated_at']}; requires-human-review=true"
    )


def stamp_schedule(schedule_data: dict, generated_at: datetime | None = None) -> dict:
    """Върни копие на данните за графика с добавен маркер.

    Не мутира входа — извикващият решава дали да замени оригинала.
    """
    stamped = dict(schedule_data or {})
    stamped["_ai_disclosure"] = machine_readable_marker(generated_at)
    return stamped
