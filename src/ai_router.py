"""Dual AI routing — DeepSeek (worker) + Anthropic Claude (controller).

DeepSeek handles: chat, document analysis, schedule generation, OCR, corrections.
Anthropic handles: schedule verification, lesson validation, quality control.
Both directions have automatic fallback if one API is unavailable.

CRITICAL: DeepSeek NEVER receives a request without knowledge context in system_prompt.
Every function that calls DeepSeek MUST include a knowledge-aware system prompt.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.json_contract import (
    CORRECTION_SPEC,
    LESSON_SPEC,
    VERIFICATION_SPEC,
    JSONContractError,
    coerce,
    parse_contract,
    parse_json_strict,
)

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model selection — SINGLE SOURCE OF TRUTH. Change models here only.
# ---------------------------------------------------------------------------
# Worker: generation, chat, OCR, corrections (kept on a DIFFERENT provider than
# the controller so verification is genuinely independent). MODEL_WORKER
# tracks DeepSeek's latest stable model.
# Overridable via .env so the worker can be reached either directly
# (api.deepseek.com, model "deepseek-chat") or through an OpenAI-compatible
# gateway like OpenRouter (model "deepseek/deepseek-chat").
MODEL_WORKER = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
# OCR: работникът е text-only (DeepSeek няма GA vision), затова OCR минава
# през отделен vision модел на същия OpenAI-съвместим endpoint.  Празно =
# ползвай работника, т.е. досегашното поведение.  Виж P1 в REVISION_2026-07.md.
MODEL_OCR = os.getenv("OCR_MODEL", "") or MODEL_WORKER
# Controller: schedule verification, lesson validation, reasoning-heavy checks.
# Upgraded 2026-07-22 from claude-sonnet-4-6 → Opus 4.8 (reasoning verifier).
MODEL_CONTROLLER = "claude-opus-4-8"

# ОПЦИОНАЛЕН Claude РАБОТНИК (проба 2026-08-04): DeepSeek V3 има ~8192 таван на
# изхода → отрязва големи графици и налага под-разбиване на партиди.  Claude дава
# 128K изход + по-добро следване на инструкции.  Зададен `WORKER_MODEL=claude-…`
# насочва работника към този модел (висок max_tokens, без отрязване).  Празно =
# досегашният DeepSeek работник.
WORKER_MODEL_OVERRIDE = os.getenv("WORKER_MODEL", "").strip()

# Таван на изхода, който МОЖЕ да поеме безусловно всеки работник — под него
# заявката не се отхвърля от нито един доставчик.  Ползва се само като
# СТЪПАЛО НАДОЛУ, когато доставчикът откаже по-високия таван.
_SAFE_OUTPUT_TOKENS = 8192

# Колко пъти да се повтори ПРАЗЕН отговор от доставчика, преди да се обяви за
# провал.  Признакът е празно СЪДЪРЖАНИЕ, не малък брой токени: кратък отговор
# може да е напълно законен и не бива да се преповтаря.
_EMPTY_RETRIES = int(os.getenv("EMPTY_RESPONSE_RETRIES", "2"))

# След колко ПОРЕДНИ провала работникът се изключва за цялата сесия.  Виж
# `chat()` — една засечка не бива да прехвърля целия прогон на контрольора.
_WORKER_FAILURES_BEFORE_DISABLE = int(
    os.getenv("WORKER_FAILURES_BEFORE_DISABLE", "3"))


def worker_max_tokens() -> int:
    """Таванът на изхода на РАБОТНИКА — един източник за ДВАТА клона.

    ПРОГОНИ 10.08.2026: 11 отговора излязоха отрязани „при таван 8192", а
    предупреждението съветваше да се вдигне `WORKER_MAX_TOKENS`.  Съветът беше
    празен: променливата се прилагаше САМО по Claude-клона (`chat()` →
    `_chat_worker_claude`), а по DeepSeek/OpenRouter минаваше подаденият от
    извикващия `GEN_MAX_TOKENS` (8192) и вдигането ѝ не променяше нищо.

    Чете се при всяко повикване, а не веднъж при import, за да важи и когато
    `.env` се зарежда след модула.

    СЕРИЯ 13.08.2026: подразбиращите се 16 000 не стигат за реален търг —
    отрязване имаше при три от шестте пробвани работника, а чистите прогони
    ползват до 28 000 изходни токена.  Стойността живееше само в `.env`, който
    НЕ е в git, тоест всяка друга машина работеше по старому.  Затова тук е
    32 000: за търг с 70 реда КСС това е таванът, при който отрязването спира
    да е водеща причина за провал.
    """
    return int(os.getenv("WORKER_MAX_TOKENS", "32000"))

# Над този таван на изхода Anthropic SDK иска STREAMING, иначе дълъг генериращ
# отговор рискува HTTP timeout (препоръка от проучването за модели, 2026-08).
# Стриймингът събира отговора на части и връща същия финален обект.
_STREAM_MIN_TOKENS = int(os.getenv("STREAM_MIN_TOKENS", "8000"))

# ---------------------------------------------------------------------------
# Pricing per token (USD) — fast-moving; verify before relying.
# ---------------------------------------------------------------------------
PRICING = {
    MODEL_WORKER: {"input": 0.28 / 1_000_000, "output": 0.42 / 1_000_000},      # DeepSeek (latest)
    MODEL_CONTROLLER: {"input": 5.0 / 1_000_000, "output": 25.0 / 1_000_000},   # Opus 4.8
    # Claude-работник опции (проба 2026-08-04).  Sonnet 5 промо $2/$10 до 31.08.
    "claude-sonnet-5": {"input": 2.0 / 1_000_000, "output": 10.0 / 1_000_000},
    "claude-opus-5": {"input": 5.0 / 1_000_000, "output": 25.0 / 1_000_000},
}
# OCR моделът се задава от .env и цената му не е известна предварително.
# Без запис тук `_calculate_cost` пада към тарифата на работника и отчита
# грешна цена мълчаливо — затова задай OCR_PRICE_IN/OUT (USD за 1M токена),
# ако OCR_MODEL е различен от работника.
if MODEL_OCR != MODEL_WORKER:
    PRICING[MODEL_OCR] = {
        "input": float(os.getenv("OCR_PRICE_IN", "0.10")) / 1_000_000,
        "output": float(os.getenv("OCR_PRICE_OUT", "0.40")) / 1_000_000,
    }

# ---------------------------------------------------------------------------
# Token limits per call type
# ---------------------------------------------------------------------------
_MAX_TOKENS_CHAT = 4096        # regular chat, analysis, verification

# OCR има СВОЙ таван, по-висок от чата.
#
# ОДИТ 07.08.2026, точка 3: „спрямо човешкия еталон с около 46 канализационни и
# 23 водопроводни физически участъка това още е сериозно under-segmentation."
#
# Причината се оказа тук, а не в четенето.  Списъкът с отсечки от ситуационния
# чертеж се РЕЖЕШЕ на 4096 токена — при ~45 токена на отсечка това е таван от
# около 80 отсечки за целия отговор, а с полетата и форматирането реално към
# 15-20.  Оттам „извличаме 6-16 отсечки от файл, което е далеч от пълнотата" и
# „четенето е нестабилно между опити": спасителният парсер вадеше толкова цели
# обекта, колкото са се побрали, и броят им зависеше от дължината на имената.
#
# Чертежът се чете веднъж на проект, тоест по-високият таван не се плаща на
# всяка генерация.
_OCR_MAX_TOKENS = int(os.getenv("OCR_MAX_TOKENS", "16000"))
_MAX_TOKENS_CORRECTION = 8192  # schedule correction (larger output needed)
_MAX_TOKENS_LESSON = 1024      # lesson verification (short JSON response)
_MIN_SYSTEM_PROMPT_LEN = 100   # minimum viable knowledge-aware system prompt
_API_TIMEOUT_SECONDS = 120     # timeout for all AI API calls (prevents Streamlit freeze)

# ---------------------------------------------------------------------------
# Verification system prompt template
# ---------------------------------------------------------------------------
VERIFICATION_SYSTEM_PROMPT = """\
Ти си контрольор на строителни графици за ВиК проекти.
Проверяваш дали графикът спазва следните правила:

{rules}

Отговори САМО с валиден JSON (без markdown, без ```):
{{
  "approved": true/false,
  "issues": ["проблем 1", "проблем 2"],
  "corrections": [
    {{"task_id": "XX", "field": "duration", "current": 10, "suggested": 15, "reason": "..."}}
  ],
  "summary": "Кратко обобщение"
}}

Ако графикът е коректен, approved=true и corrections=[].\
"""

CORRECTION_SYSTEM_PROMPT = """\
Ти си строителен инженер, специалист по ВиК графици.
Получаваш график в JSON формат и списък с корекции.
Приложи ВСИЧКИ корекции и върни коригирания график.

{knowledge_context}

Отговори САМО с валиден JSON (без markdown, без ```):
{{
  "schedule": <коригираният график>,
  "applied": ["описание на корекция 1", "описание на корекция 2"]
}}
"""

LESSON_VERIFICATION_PROMPT = """\
Ти си контрольор на база знания за строителни графици.
Проверяваш дали новият урок е коректно формулиран и не противоречи на съществуващите.

Съществуващи уроци:
{existing_lessons}

Нов урок за проверка:
{new_lesson}

Контекст: {context}

Отговори САМО с валиден JSON (без markdown, без ```):
{{
  "approved": true/false,
  "formatted_lesson": "Форматиран текст на урока",
  "reason": "Защо е одобрен/отхвърлен"
}}
"""

# Minimal OCR system prompt — includes domain-specific guidance
OCR_SYSTEM_PROMPT = """\
Ти си OCR асистент за строителни документи на български език.
Извличаш текст от сканирани документи за ВиК (водоснабдяване и канализация) проекти.

Правила:
- Запази структурата на документа (заглавия, параграфи, таблици)
- Българските букви трябва да са правилни (не ги заменяй с латиница)
- Числата и мерните единици трябва да са точни (м, м², м³, бр., кг, т, DN)
- Таблиците подреди с разделители | или табулации
- Ако текстът е нечетлив, отбележи с [нечетливо]

{additional_context}\
"""


class AIRouter:
    """Routes AI requests to DeepSeek (worker) or Anthropic (controller)."""

    def __init__(self) -> None:
        self._deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
        self._anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")

        self._deepseek_client: Any | None = None
        self._anthropic_client: Any | None = None

        self.deepseek_available: bool = True
        self.anthropic_available: bool = True
        self.fallback_active: bool = False
        self.fallback_source: str | None = None  # which API is down
        # Проба 2026-08-04: работникът е силен Claude модел (Sonnet 5 / Opus).
        # Тогава вторият AI корекционен цикъл е излишен и е бутилково гърло
        # (отряза се на голям график) — пропуска се, gate-ът остава авторитет.
        # Claude работник — и през директния WORKER_MODEL, И през OpenRouter
        # (DEEPSEEK_MODEL=anthropic/claude-…).  Одит 2026-08: досега се гледаше
        # само WORKER_MODEL → Sonnet през OpenRouter се третираше като DeepSeek
        # (грешно batching/skip + грешна цена).
        self.worker_is_claude: bool = (
            WORKER_MODEL_OVERRIDE.startswith("claude")
            or "claude" in MODEL_WORKER.lower()
        )

        # ПОРЕДНИ провали на работника.  Виж `chat()`: една засечка НЕ изключва
        # работника за цялата сесия — само този брояч решава кога е системно.
        self._worker_failures: int = 0

        # OCR моделът е ОТДЕЛЕН от работника, когато е зададен `OCR_MODEL`
        # (работникът е text-only, vision минава през друг модел).  Затова и
        # достъпността му се брои отделно: модел без реален vision достъп се
        # проваля при ВСЯКА страница, а това не бива да изключва работника,
        # който с текста се справя.
        self._ocr_failures: int = 0
        self.ocr_available: bool = True

        self.usage_log: list[dict] = []

        # Cumulative usage persistence (across sessions)
        self._cumulative_path: Path | None = None
        self._cumulative: dict = {
            "deepseek": 0.0, "anthropic": 0.0,
            "total": 0.0, "total_calls": 0,
        }

        # Stop flag for cancelling multi-step operations
        self.stop_requested: bool = False

    # ------------------------------------------------------------------
    # Lazy client initialization
    # ------------------------------------------------------------------

    def _get_deepseek(self):
        """Return the DeepSeek (OpenAI-compatible) client."""
        if self._deepseek_client is not None:
            return self._deepseek_client

        if not self._deepseek_key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY is not set. Add it to .env."
            )

        from openai import OpenAI

        self._deepseek_client = OpenAI(
            api_key=self._deepseek_key,
            base_url=DEEPSEEK_BASE_URL,
            timeout=_API_TIMEOUT_SECONDS,
        )
        return self._deepseek_client

    def _get_anthropic(self):
        """Return the Anthropic client."""
        if self._anthropic_client is not None:
            return self._anthropic_client

        if not self._anthropic_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Add it to .env."
            )

        import anthropic

        self._anthropic_client = anthropic.Anthropic(
            api_key=self._anthropic_key,
            timeout=_API_TIMEOUT_SECONDS,
        )
        return self._anthropic_client

    def get_anthropic_client(self):
        """Public accessor for the Anthropic client (for use by external modules)."""
        return self._get_anthropic()

    def log_usage(self, model: str, tokens_in: int, tokens_out: int, task_type: str) -> None:
        """Public accessor for usage logging (for use by external modules)."""
        self._log_usage(model, tokens_in, tokens_out, task_type)

    @staticmethod
    def parse_json_response(raw: str) -> dict:
        """Public accessor for JSON response parsing (for use by external modules)."""
        return AIRouter._parse_json_response(raw)

    # ------------------------------------------------------------------
    # System prompt validation
    # ------------------------------------------------------------------

    @staticmethod
    def _warn_empty_prompt(system_prompt: str, caller: str) -> None:
        """Log a warning if system_prompt is empty or suspiciously short.

        DeepSeek is a 'clean' model — without knowledge context it doesn't
        know the rules for ViK schedules, productivities, lessons, etc.
        """
        if not system_prompt or len(system_prompt) < _MIN_SYSTEM_PROMPT_LEN:
            logger.warning(
                "DeepSeek called with empty/short system prompt in %s! "
                "Knowledge context may be missing. Prompt length: %d chars.",
                caller, len(system_prompt) if system_prompt else 0,
            )

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def check_health(self) -> dict:
        """Check if both APIs are reachable. Updates availability flags.

        Returns:
            Dict with deepseek, anthropic booleans and fallback info.
        """
        # DeepSeek
        try:
            client = self._get_deepseek()
            client.chat.completions.create(
                model=MODEL_WORKER,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
                timeout=15,
            )
            self.deepseek_available = True
        except Exception as exc:
            logger.warning("DeepSeek health check failed: %s", exc)
            self.deepseek_available = False

        # Anthropic
        try:
            client = self._get_anthropic()
            client.messages.create(
                model=MODEL_CONTROLLER,
                max_tokens=5,
                messages=[{"role": "user", "content": "ping"}],
                timeout=15,
            )
            self.anthropic_available = True
        except Exception as exc:
            logger.warning("Anthropic health check failed: %s", exc)
            self.anthropic_available = False

        # Update fallback state
        self._update_fallback_state()

        return {
            "deepseek": self.deepseek_available,
            "anthropic": self.anthropic_available,
            "fallback_active": self.fallback_active,
            "fallback_source": self.fallback_source,
        }

    def _update_fallback_state(self) -> None:
        """Update fallback flags based on current availability."""
        if self.deepseek_available and self.anthropic_available:
            self.fallback_active = False
            self.fallback_source = None
        elif not self.deepseek_available and self.anthropic_available:
            self.fallback_active = True
            self.fallback_source = "deepseek"
        elif self.deepseek_available and not self.anthropic_available:
            self.fallback_active = True
            self.fallback_source = "anthropic"
        else:
            self.fallback_active = True
            self.fallback_source = "both"

    def _note_worker_failure(self, where: str, exc: BaseException,
                             *, to_controller: bool = True) -> None:
        """Отчети ЕДИН провал на работника; изключи го само ако е системен.

        Правилото беше въведено в `chat()` (12.08.2026), но останалите три пътя
        — проверка, прилагане на корекции и OCR — още вдигаха
        `deepseek_available = False` при първата засечка.  Ефектът е същият и
        оттам: една преходна грешка в който и да е от тях прехвърля ЦЯЛАТА
        по-нататъшна сесия, включително генерирането, на контрольора, който е
        5–25 пъти по-скъп, без това да личи в изхода.

        Затова правилото е едно и живее на едно място.

        `to_controller` казва дали тази заявка наистина се поема от контрольора.
        При проверката на графика работникът е РЕЗЕРВАТА (контрольорът е пробван
        пръв), тоест няма къде да се падне — там записът не бива да твърди
        прехвърляне, което не се е случило.
        """
        self._worker_failures += 1
        logger.warning(
            "Работникът %s се провали при %s (%s) — %s (%d-и пореден провал "
            "от %d, след които работникът се изключва за сесията).",
            MODEL_WORKER, where, exc,
            "заявката минава през контрольора, който е чувствително по-скъп"
            if to_controller else "резервен път за тази заявка няма",
            self._worker_failures, _WORKER_FAILURES_BEFORE_DISABLE)
        if self._worker_failures >= _WORKER_FAILURES_BEFORE_DISABLE:
            logger.error(
                "Работникът %s се изключва за сесията след %d поредни провала "
                "— ОТСЕГА ВСИЧКО минава през контрольора, който е чувствително "
                "по-скъп.", MODEL_WORKER, self._worker_failures)
            self.deepseek_available = False
            self._update_fallback_state()

    def _note_worker_success(self) -> None:
        """Нулирай брояча — провалите се броят само ПОРЕДНИ."""
        self._worker_failures = 0

    # ------------------------------------------------------------------
    # Chat (Worker = DeepSeek, fallback = Anthropic)
    # ------------------------------------------------------------------

    def chat(self, messages: list[dict], system_prompt: str,
             max_tokens: int = _MAX_TOKENS_CHAT,
             response_schema: dict | None = None) -> dict:
        """Send a chat message to the worker (DeepSeek). Falls back to Anthropic.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            system_prompt: System prompt with knowledge context.
            max_tokens: Таван на изходните токени.  Проба 2026-07-24: генери-
                рането на реален график иска повече от default 4096 — подава
                8192, за да не се отрязва JSON-ът.

        Returns:
            Dict with content, model, usage, cost, fallback.
        """
        self._warn_empty_prompt(system_prompt, "chat")

        # Проба 2026-08-04: ако е зададен Claude работник, той поема генерирането
        # (128K изход → без отрязване).  При провал пада към обичайния път.
        if WORKER_MODEL_OVERRIDE.startswith("claude") and self.anthropic_available:
            try:
                return self._chat_worker_claude(
                    messages, system_prompt, model=WORKER_MODEL_OVERRIDE,
                    max_tokens=max(max_tokens, worker_max_tokens()),
                    response_schema=response_schema)
            except Exception as exc:
                logger.warning("Claude работник (%s) се провали, fallback: %s",
                               WORKER_MODEL_OVERRIDE, exc)

        # Try DeepSeek first
        if self.deepseek_available:
            try:
                result = self._chat_deepseek(messages, system_prompt, max_tokens=max_tokens,
                                             response_schema=response_schema)
                self._note_worker_success()
                return result
            except Exception as exc:
                # ЕДНА ЗАСЕЧКА НЕ ИЗКЛЮЧВА РАБОТНИКА ЗА ЦЯЛАТА СЕСИЯ.
                #
                # Досега първият отказ вдигаше `deepseek_available = False` до
                # края на живота на инстанцията и всичко следващо мълчаливо
                # минаваше през контрольора — 5 до 25 пъти по-скъп модел, без
                # това да личи никъде в изхода.  Проба 12.08.2026: OpenRouter
                # беше без кредит и 18 прогона, поискани от шест различни
                # евтини работника, ги обслужи Opus.  $3.92 и шест еднакви
                # резултата, представени като сравнение между модели.
                #
                # Резервният път остава — но за ЕДНА заявка.  Работникът се
                # изключва чак след `_WORKER_FAILURES_BEFORE_DISABLE` поредни
                # провала, тоест когато засечката наистина е системна (изтекъл
                # ключ, свършил кредит), а не преходна.
                self._note_worker_failure("генериране", exc)

        # Fallback to Anthropic
        if self.anthropic_available:
            try:
                return self._chat_anthropic(messages, system_prompt, is_fallback=True,
                                            max_tokens=max_tokens)
            except Exception as exc:
                logger.error("Anthropic fallback also failed: %s", exc)
                self.anthropic_available = False
                self._update_fallback_state()

        return {
            "content": "Грешка: И двата AI модела са недостъпни. Проверете API ключовете и интернет връзката.",
            "model": "none",
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "cost": 0.0,
            "fallback": False,
            "error": True,
        }

    def _openai_request(self, client: Any, kwargs: dict, max_tokens: int):
        """OpenAI-съвместима заявка — STREAMING при голям изход.

        При max_tokens ≥ _STREAM_MIN_TOKENS non-streaming рискува HTTP timeout за
        дълъг генериращ отговор (наблюдавано със Sonnet през OpenRouter, 2026-08:
        пълен график надхвърля 8192 и 120с таван).  Стриймингът събира отговора на
        части и връща (content, tokens_in, tokens_out, finish_reason).
        """
        if max_tokens >= _STREAM_MIN_TOKENS:
            kw = {**kwargs, "stream": True,
                  "stream_options": {"include_usage": True},
                  "timeout": max(_API_TIMEOUT_SECONDS, 600)}
            parts: list[str] = []
            tin = tout = 0
            finish = None
            for chunk in client.chat.completions.create(**kw):
                if getattr(chunk, "choices", None):
                    ch0 = chunk.choices[0]
                    delta = getattr(ch0, "delta", None)
                    if delta and getattr(delta, "content", None):
                        parts.append(delta.content)
                    if getattr(ch0, "finish_reason", None):
                        finish = ch0.finish_reason
                usage = getattr(chunk, "usage", None)
                if usage:
                    tin = usage.prompt_tokens or tin
                    tout = usage.completion_tokens or tout
            return "".join(parts), tin, tout, finish
        # Малък изход — обикновена (не-streaming) заявка.
        resp = client.chat.completions.create(**kwargs, timeout=_API_TIMEOUT_SECONDS)
        ch = resp.choices[0]
        u = resp.usage
        return (ch.message.content or "",
                u.prompt_tokens if u else 0, u.completion_tokens if u else 0,
                getattr(ch, "finish_reason", None))

    def _request_with_empty_retry(self, client: Any, kw: dict) -> tuple:
        """Заявка, която повтаря ПРАЗЕН отговор — това е засечка, не резултат.

        Серията от 13.08.2026: 5 от 40 прогона се върнаха за 2 секунди със
        СЕДЕМ изходни токена.  Доставчикът отговаря „успешно" с празно тяло,
        затова нищо не гърми — парсването после се проваля и прогонът се
        отчита като лоша генерация.  Пет процента загубени прогони, дължащи се
        на чужда инфраструктура, влизаха в статистиката за качеството на
        модела.

        Токените на изхвърлените опити СЕ ЗАПИСВАТ: входът е платен, дори
        отговорът да е празен, и сметката трябва да го показва.
        """
        last: tuple | None = None
        for attempt in range(1, _EMPTY_RETRIES + 2):
            content, tokens_in, tokens_out, finish_reason = self._openai_request(
                client, kw, kw["max_tokens"])
            last = (content, tokens_in, tokens_out, finish_reason)
            if (content or "").strip():
                return last

            self._log_usage(MODEL_WORKER, tokens_in, tokens_out, "chat-празен")
            if attempt > _EMPTY_RETRIES:
                raise RuntimeError(
                    f"{MODEL_WORKER} върна празен отговор {attempt} пъти "
                    f"({tokens_out} изходни токена, finish_reason="
                    f"{finish_reason}) — засечка на доставчика, не резултат.")
            logger.warning(
                "Отговорът на %s е ПРАЗЕН (%d изходни токена, "
                "finish_reason=%s) — опит %d от %d.",
                MODEL_WORKER, tokens_out, finish_reason, attempt,
                _EMPTY_RETRIES + 1)
            time.sleep(2 * attempt)
        return last                                     # pragma: no cover

    def _chat_deepseek(self, messages: list[dict], system_prompt: str,
                       max_tokens: int = _MAX_TOKENS_CHAT,
                       response_schema: dict | None = None) -> dict:
        """Send chat to DeepSeek via OpenAI-compatible API.

        `response_schema` (2026-08): BEST-EFFORT structured output — материалът е
        enum.  Не всеки OpenAI-съвместим gateway поддържа `json_schema`; при
        отказ повтаряме заявката БЕЗ него (никога не чупим генерацията).
        """
        client = self._get_deepseek()
        full_messages = [{"role": "system", "content": system_prompt}] + messages
        base_kwargs = dict(
            model=MODEL_WORKER, messages=full_messages, temperature=0.3)

        # СТЪПАЛА НАДОЛУ, в реда на пробване.  Две измерения:
        #
        # 1. ТАВАН.  Част от работниците (DeepSeek V3 директно) имат ТВЪРД таван
        #    на изхода и ОТХВЪРЛЯТ заявка над него.  След като генерирането вече
        #    иска пълния таван на работника, липсата на стъпало надолу би
        #    превърнала една настройка в отказ на всеки прогон.  По-нисък таван
        #    е по-добре от загубен прогон — отрязването после си личи в
        #    `truncated`.
        # 2. JSON MODE вместо строга json_schema (жив тест Sonnet/OpenRouter,
        #    2026-08): строгата schema или ИЗТРИВАШЕ недекларирани полета, или
        #    се отхвърляше (union-type лимит на Anthropic).
        #    `{"type":"json_object"}` гарантира ВАЛИДЕН JSON без да ограничава
        #    полетата и работи при всички provider-и.  Материалният enum се
        #    налага през промпта.
        caps = [max_tokens]
        if max_tokens > _SAFE_OUTPUT_TOKENS:
            caps.append(_SAFE_OUTPUT_TOKENS)

        attempts: list[dict] = []
        for cap in caps:
            kw = {**base_kwargs, "max_tokens": cap}
            if response_schema is not None:
                attempts.append({**kw, "response_format": {"type": "json_object"}})
            attempts.append(kw)

        result, used_cap = None, max_tokens
        for i, kw in enumerate(attempts):
            try:
                result = self._request_with_empty_retry(client, kw)
                used_cap = kw["max_tokens"]
                break
            except Exception as exc:
                if i < len(attempts) - 1:
                    nxt = attempts[i + 1]
                    logger.warning(
                        "Заявката към %s се провали (%s) → следващо стъпало: "
                        "таван %d, %s json mode.", MODEL_WORKER, exc,
                        nxt["max_tokens"],
                        "със" if "response_format" in nxt else "без")
                else:
                    raise
        content, tokens_in, tokens_out, finish_reason = result

        self._log_usage(MODEL_WORKER, tokens_in, tokens_out, "chat")

        # Отрязан отговор досега минаваше тихо: JSON-ът излиза невалиден и
        # проблемът се появяваше чак при парсването, като „моделът се обърка".
        truncated = finish_reason == "length"
        if truncated:
            # Съветът сочи РАБОТЕЩАТА променлива: `used_cap` е таванът, който
            # доставчикът наистина прие, а той може да е стъпало НАДОЛУ от
            # поискания — тогава вдигането на настройката няма да помогне и
            # проектът трябва да се раздели на етапи.
            hit_ceiling = used_cap < max_tokens
            logger.warning(
                "Отговорът на %s е ОТРЯЗАН (finish_reason=length, %d изходни "
                "токена при таван %d). %s",
                MODEL_WORKER, tokens_out, used_cap,
                "Работникът отказа по-висок таван — раздели проекта на етапи "
                "или смени модела." if hit_ceiling
                else "Вдигни WORKER_MAX_TOKENS (или GEN_MAX_TOKENS).",
            )

        return {
            "content": content,
            "model": MODEL_WORKER,
            "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out},
            "cost": self._calculate_cost(MODEL_WORKER, tokens_in, tokens_out),
            "fallback": False,
            "truncated": truncated,
        }

    def _anthropic_worker_request(self, client: Any, kwargs: dict, max_tokens: int) -> Any:
        """Изпълни заявката към Claude worker-а — стрийминг при голям изход.

        При max_tokens ≥ _STREAM_MIN_TOKENS non-streaming рискува HTTP timeout
        за дълъг генериращ отговор.  `messages.stream()` събира отговора на части
        и `get_final_message()` връща същия финален Message обект.
        """
        if max_tokens >= _STREAM_MIN_TOKENS:
            with client.messages.stream(**kwargs) as stream:
                return stream.get_final_message()
        return client.messages.create(**kwargs)

    def _chat_worker_claude(
        self, messages: list[dict], system_prompt: str, *, model: str,
        max_tokens: int, response_schema: dict | None = None,
    ) -> dict:
        """Claude като РАБОТНИК (проба 2026-08-04) — висок max_tokens, без отрязване.

        NB (claude-api): Claude 5 моделите имат thinking ВКЛЮЧЕН по подразбиране →
        `content[0]` е ThinkingBlock, не текст.  За структуриран JSON thinking не
        трябва (само бави и харчи токени), затова е ИЗКЛЮЧЕН, а текстът се вади от
        текстовия блок, не от `content[0]`.  По-дълъг timeout — генерацията е
        по-голяма от обикновен chat.

        `response_schema` (2026-08): JSON schema със `material` като enum → моделът
        не може да върне невалиден материал (напр. PP липсваше в позволените и
        моделът пишеше PE).  BEST-EFFORT: ако SDK/моделът не приема structured
        output → повтаряме заявката БЕЗ schema (никога не чупим генерацията).
        """
        # `response_schema` СЪЗНАТЕЛНО не се подава като structured output тук
        # (жив тест 2026-08): строгата schema или трие полета, или удря union-лимит
        # на провайдъра.  Материалният enum се налага през промпта, а валидният
        # JSON — чрез инструкция.  Streaming при голям max_tokens (виж по-долу).
        _ = response_schema  # (запазено за съвместимост на подписа)
        client = self._get_anthropic()
        kwargs = dict(
            model=model, max_tokens=max_tokens,
            thinking={"type": "disabled"},
            system=system_prompt, messages=messages,
            timeout=max(_API_TIMEOUT_SECONDS, 300),
        )
        response = self._anthropic_worker_request(client, kwargs, max_tokens)
        content = next((getattr(b, "text", "") for b in (response.content or [])
                        if getattr(b, "type", None) == "text"), "")
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
        self._log_usage(model, tokens_in, tokens_out, "chat")
        truncated = getattr(response, "stop_reason", None) == "max_tokens"
        if truncated:
            logger.warning("Claude работник %s ОТРЯЗАН (max_tokens=%d).", model, max_tokens)
        return {
            "content": content, "model": model,
            "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out},
            "cost": self._calculate_cost(model, tokens_in, tokens_out),
            "fallback": False, "truncated": truncated,
        }

    def _chat_anthropic(
        self, messages: list[dict], system_prompt: str, *, is_fallback: bool = False,
        max_tokens: int = _MAX_TOKENS_CHAT,
    ) -> dict:
        """Send chat to Anthropic Claude."""
        client = self._get_anthropic()

        response = client.messages.create(
            model=MODEL_CONTROLLER,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=messages,
            timeout=_API_TIMEOUT_SECONDS,
        )

        content = response.content[0].text if response.content else ""
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens

        self._log_usage(MODEL_CONTROLLER, tokens_in, tokens_out, "chat")

        # Одит: DeepSeek пътят разпознаваше отрязан отговор, Anthropic — не.
        # Тук еквивалентът е stop_reason == "max_tokens".
        truncated = getattr(response, "stop_reason", None) == "max_tokens"
        if truncated:
            logger.warning(
                "Отговорът на %s е ОТРЯЗАН (stop_reason=max_tokens, %d изходни "
                "токена при таван %d).",
                MODEL_CONTROLLER, tokens_out, max_tokens,
            )

        return {
            "content": content,
            "model": MODEL_CONTROLLER,
            "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out},
            "cost": self._calculate_cost(MODEL_CONTROLLER, tokens_in, tokens_out),
            "fallback": is_fallback,
            "truncated": truncated,
        }

    def chat_anthropic_direct(
        self, messages: list[dict], system_prompt: str, max_tokens: int = _MAX_TOKENS_CHAT
    ) -> dict:
        """Send a chat request directly to Anthropic (no fallback to DeepSeek).

        Used for tasks that require structured expert reasoning — e.g. MS Project
        enrichment — where only the controller model is appropriate.

        Args:
            messages: Chat messages list.
            system_prompt: System prompt string.
            max_tokens: Max output tokens (default 4096; use 8192+ for large schedules).

        Returns same structure as _chat_anthropic: content, model, cost, usage.
        """
        return self._chat_anthropic(
            messages, system_prompt, is_fallback=False, max_tokens=max_tokens
        )

    # ------------------------------------------------------------------
    # Schedule verification (Controller = Anthropic, fallback = DeepSeek)
    # ------------------------------------------------------------------

    def verify_schedule(self, schedule_json: str, rules: str, project_type: str = "") -> dict:
        """Send schedule to the controller (Anthropic) for verification.

        Args:
            schedule_json: The schedule as a JSON string.
            rules: Verification rules from knowledge/skills.
            project_type: Project type for methodology-specific validation.

        Returns:
            Dict with approved, issues, corrections, model, cost.
        """
        type_context = f"Тип проект: {project_type}\n\n" if project_type else ""
        system_prompt = VERIFICATION_SYSTEM_PROMPT.format(rules=f"{type_context}{rules}")
        user_message = f"Провери следния график:\n\n{schedule_json}"

        parse_failures: list[str] = []

        # Try Anthropic first
        if self.anthropic_available:
            try:
                return self._verify_with_model(
                    "anthropic", system_prompt, user_message
                )
            except JSONContractError as exc:
                # Моделът отговори, но неизползваемо — API-то РАБОТИ.
                # Не го маркирай като недостъпен, само пробвай другия.
                logger.warning("Anthropic verify: неизползваем отговор — %s", exc)
                parse_failures.append(f"Anthropic: {exc}")
            except Exception as exc:
                logger.warning("Anthropic verify failed, trying fallback: %s", exc)
                self.anthropic_available = False
                self._update_fallback_state()

        # Fallback to DeepSeek
        if self.deepseek_available:
            try:
                verdict = self._verify_with_model(
                    "deepseek", system_prompt, user_message
                )
                self._note_worker_success()
                return verdict
            except JSONContractError as exc:
                # Моделът отговори — API-то работи.  Не е провал на работника.
                logger.error("DeepSeek verify: неизползваем отговор — %s", exc)
                parse_failures.append(f"DeepSeek: {exc}")
            except Exception as exc:
                self._note_worker_failure("проверка на графика", exc,
                                          to_controller=False)

        if parse_failures:
            # Разграничено от „моделите са недостъпни": тук те отговарят,
            # но не спазват формата.  Различна причина → различно решение.
            return {
                "approved": False,
                "issues": [],
                "corrections": [],
                "summary": "Отговорът на контрольора не можа да бъде разчетен.",
                "model": "none",
                "cost": 0.0,
                "error": True,
                "parse_error": True,
                "details": parse_failures,
            }

        return {
            "approved": False,
            "issues": ["AI models are unavailable — cannot verify."],
            "corrections": [],
            "summary": "Verification error.",
            "model": "none",
            "cost": 0.0,
            "error": True,
        }

    def _verify_with_model(
        self, provider: str, system_prompt: str, user_message: str
    ) -> dict:
        """Run verification with a specific provider."""
        messages = [{"role": "user", "content": user_message}]

        if provider == "anthropic":
            client = self._get_anthropic()
            response = client.messages.create(
                model=MODEL_CONTROLLER,
                max_tokens=_MAX_TOKENS_CHAT,
                system=system_prompt,
                messages=messages,
                # Проба 2026-08-03: Opus 4.8 отхвърля `temperature` (deprecated
                # for this model) → корекцията гърмеше и падаше на DeepSeek.
                timeout=_API_TIMEOUT_SECONDS,
            )
            raw = response.content[0].text if response.content else "{}"
            tokens_in = response.usage.input_tokens
            tokens_out = response.usage.output_tokens
            model = MODEL_CONTROLLER
        else:
            client = self._get_deepseek()
            full_msgs = [{"role": "system", "content": system_prompt}] + messages
            response = client.chat.completions.create(
                model=MODEL_WORKER,
                messages=full_msgs,
                max_tokens=_MAX_TOKENS_CHAT,
                temperature=0.1,
                timeout=_API_TIMEOUT_SECONDS,
            )
            raw = response.choices[0].message.content or "{}"
            usage = response.usage
            tokens_in = usage.prompt_tokens if usage else 0
            tokens_out = usage.completion_tokens if usage else 0
            model = MODEL_WORKER

        self._log_usage(model, tokens_in, tokens_out, "verify")
        cost = self._calculate_cost(model, tokens_in, tokens_out)

        # P7: неизползваем отговор хвърля, вместо да се маскира като
        # „графикът не е одобрен" — иначе счупен JSON задейства корекционни
        # цикли за несъществуващ проблем.
        parsed = parse_contract(raw, VERIFICATION_SPEC, "верификация")

        return {
            "approved": parsed["approved"],
            "issues": parsed["issues"],
            "corrections": parsed["corrections"],
            "summary": parsed["summary"],
            "model": model,
            "cost": cost,
        }

    # ------------------------------------------------------------------
    # Apply corrections (Worker = DeepSeek, fallback = Anthropic)
    # ------------------------------------------------------------------

    def apply_corrections(
        self, schedule_json: str, corrections: list[dict],
        system_prompt: str = "",
    ) -> dict:
        """Send corrections to the worker (DeepSeek) for application.

        Args:
            schedule_json: Current schedule JSON string.
            corrections: List of correction dicts from verification.
            system_prompt: Knowledge context for the AI. If empty, uses
                a basic correction prompt (with warning).

        Returns:
            Dict with corrected_schedule, applied, model, cost.
        """
        # Build the correction system prompt with knowledge context
        knowledge_ctx = system_prompt if system_prompt else ""
        full_system = CORRECTION_SYSTEM_PROMPT.format(
            knowledge_context=knowledge_ctx
        )

        self._warn_empty_prompt(knowledge_ctx, "apply_corrections")

        user_message = (
            f"Ето текущият график:\n{schedule_json}\n\n"
            f"Приложи следните корекции:\n{json.dumps(corrections, ensure_ascii=False, indent=2)}"
        )
        messages = [{"role": "user", "content": user_message}]

        # Try DeepSeek first
        if self.deepseek_available:
            try:
                applied = self._apply_with_model(
                    "deepseek", messages, full_system, schedule_json)
                self._note_worker_success()
                return applied
            except Exception as exc:
                self._note_worker_failure("прилагане на корекции", exc)

        # Fallback to Anthropic
        if self.anthropic_available:
            try:
                return self._apply_with_model("anthropic", messages, full_system, schedule_json)
            except Exception as exc:
                logger.error("Anthropic corrections fallback failed: %s", exc)

        return {
            "corrected_schedule": schedule_json,
            "applied": [],
            "model": "none",
            "cost": 0.0,
            "error": True,
        }

    def _apply_with_model(
        self, provider: str, messages: list[dict], system_prompt: str,
        schedule_json: str = "{}",
    ) -> dict:
        """Apply corrections using a specific provider."""
        if provider == "deepseek":
            client = self._get_deepseek()
            full_msgs = [
                {"role": "system", "content": system_prompt}
            ] + messages
            response = client.chat.completions.create(
                model=MODEL_WORKER,
                messages=full_msgs,
                max_tokens=_MAX_TOKENS_CORRECTION,
                temperature=0.1,
                timeout=_API_TIMEOUT_SECONDS,
            )
            raw = response.choices[0].message.content or "{}"
            usage = response.usage
            tokens_in = usage.prompt_tokens if usage else 0
            tokens_out = usage.completion_tokens if usage else 0
            model = MODEL_WORKER
        else:
            client = self._get_anthropic()
            response = client.messages.create(
                model=MODEL_CONTROLLER,
                max_tokens=_MAX_TOKENS_CORRECTION,
                system=system_prompt,
                messages=messages,
                # Opus 4.8 не приема `temperature` (проба 2026-08-03).
                timeout=_API_TIMEOUT_SECONDS,
            )
            raw = response.content[0].text if response.content else "{}"
            tokens_in = response.usage.input_tokens
            tokens_out = response.usage.output_tokens
            model = MODEL_CONTROLLER

        self._log_usage(model, tokens_in, tokens_out, "correct")
        cost = self._calculate_cost(model, tokens_in, tokens_out)

        parsed = parse_json_strict(raw)
        if parsed.data is None:
            # Нечетим отговор → връщаме графика НЕПРОМЕНЕН, вместо да
            # запишем празен dict върху него.
            raise JSONContractError(f"корекция: {parsed.error}")

        corrected, problems = coerce(parsed.data, CORRECTION_SPEC)
        if problems:
            logger.warning("Корекция: отговорът не спазва формата — %s", "; ".join(problems))

        return {
            "corrected_schedule": corrected["schedule"] or schedule_json,
            "applied": corrected["applied"],
            "model": model,
            "cost": cost,
        }

    # ------------------------------------------------------------------
    # Correction cycle (auto: generate -> verify -> correct -> verify...)
    # ------------------------------------------------------------------

    def run_correction_cycle(
        self,
        schedule_json: str,
        rules: str,
        max_cycles: int = 3,
        progress_callback: Any | None = None,
        knowledge_prompt: str = "",
        project_type: str = "",
    ) -> dict:
        """Automatic correction cycle: verify -> correct -> verify (max N times).

        Args:
            schedule_json: Initial schedule JSON string.
            rules: Verification rules.
            max_cycles: Maximum correction attempts.
            progress_callback: Optional callable(message: str) for progress updates.
            knowledge_prompt: Knowledge context to pass to apply_corrections.

        Returns:
            Dict with status, schedule, cycles, total_cost, history.
        """
        cycle = 0
        current_schedule = schedule_json
        all_issues: list[dict] = []
        total_cost = 0.0
        verification: dict = {}

        while cycle < max_cycles:
            # Check stop flag between steps
            if self.stop_requested:
                return {
                    "status": "stopped",
                    "schedule": current_schedule,
                    "cycles": cycle,
                    "total_cost": total_cost,
                    "history": all_issues,
                }

            # Verify
            if progress_callback:
                model_label = "Anthropic" if self.anthropic_available else "DeepSeek"
                progress_callback(
                    f"Проверявам правилата... ({model_label}) [опит {cycle + 1}]"
                )

            verification = self.verify_schedule(current_schedule, rules, project_type=project_type)
            total_cost += verification.get("cost", 0.0)

            if verification.get("error"):
                return {
                    "status": "error",
                    "schedule": current_schedule,
                    "cycles": cycle + 1,
                    "total_cost": total_cost,
                    "history": all_issues,
                    "error": (
                        "Контрольорът върна нечетим отговор — графикът НЕ е "
                        "проверен. Опитайте отново."
                        if verification.get("parse_error")
                        else "AI models are unavailable."
                    ),
                    "parse_error": verification.get("parse_error", False),
                }

            if verification["approved"]:
                return {
                    "status": "approved",
                    "schedule": current_schedule,
                    "cycles": cycle + 1,
                    "total_cost": total_cost,
                    "history": all_issues,
                    "summary": verification.get("summary", ""),
                }

            # Has issues — log them
            all_issues.append({
                "cycle": cycle + 1,
                "issues": verification["issues"],
                "corrections_count": len(verification["corrections"]),
            })

            # Check stop flag before correction step
            if self.stop_requested:
                return {
                    "status": "stopped",
                    "schedule": current_schedule,
                    "cycles": cycle + 1,
                    "total_cost": total_cost,
                    "history": all_issues,
                }

            if progress_callback:
                issues_str = ", ".join(verification["issues"][:3])
                progress_callback(
                    f"Коригирам: {issues_str}..."
                )

            # Apply corrections (with knowledge context)
            result = self.apply_corrections(
                current_schedule, verification["corrections"],
                system_prompt=knowledge_prompt,
            )
            total_cost += result.get("cost", 0.0)

            if result.get("error"):
                return {
                    "status": "error",
                    "schedule": current_schedule,
                    "cycles": cycle + 1,
                    "total_cost": total_cost,
                    "history": all_issues,
                    "error": "Error applying corrections.",
                }

            current_schedule = (
                json.dumps(result["corrected_schedule"], ensure_ascii=False)
                if isinstance(result["corrected_schedule"], dict)
                else result["corrected_schedule"]
            )
            cycle += 1

        # Exhausted attempts
        return {
            "status": "needs_human_review",
            "schedule": current_schedule,
            "cycles": max_cycles,
            "total_cost": total_cost,
            "remaining_issues": verification.get("issues", []),
            "history": all_issues,
        }

    # ------------------------------------------------------------------
    # Text reformatting (Worker = DeepSeek, text-only, cheap)
    # ------------------------------------------------------------------

    def reformat_text(self, raw_text: str, source_name: str = "") -> dict:
        """Reformat messy PDF text via DeepSeek (text-only, no vision).

        Used when fitz extracts partial text. Much cheaper than OCR.

        Args:
            raw_text: Raw extracted text from PDF.
            source_name: Original filename for context.

        Returns:
            Dict with 'status' and 'text' keys.
        """
        system_prompt = (
            "Ти си асистент за преформатиране на текст от PDF документи "
            "за ВиК (водоснабдяване и канализация) проекти на български.\n\n"
            "Правила:\n"
            "- Оправи структурата: заглавия, параграфи, таблици\n"
            "- Запази ТОЧНО числата, мерните единици (м, м², DN, бр.)\n"
            "- Не добавяй информация — само преформатирай\n"
            "- Ако има таблици, подреди ги с | разделители\n"
            "- Отговори САМО с преформатирания текст"
        )

        context = f" от файл '{source_name}'" if source_name else ""
        user_msg = (
            f"Преформатирай следния текст{context}. "
            "Запази цялата информация, оправи структурата:\n\n"
            f"{raw_text[:8000]}"  # Limit to ~8K chars to save tokens
        )

        messages = [{"role": "user", "content": user_msg}]

        # DeepSeek only — this is a cheap text task
        if self.deepseek_available:
            try:
                result = self._chat_deepseek(messages, system_prompt)
                return {"status": "ok", "text": result["content"]}
            except Exception as exc:
                logger.warning("DeepSeek reformat failed: %s", exc)

        # Fallback to Anthropic if DeepSeek is down
        if self.anthropic_available:
            try:
                result = self._chat_anthropic(
                    messages, system_prompt, is_fallback=True
                )
                return {"status": "ok", "text": result["content"]}
            except Exception as exc:
                logger.warning("Anthropic reformat fallback failed: %s", exc)

        return {"status": "error", "error": "AI models unavailable for reformatting."}

    # ------------------------------------------------------------------
    # OCR (Worker = DeepSeek vision, fallback = Anthropic vision)
    # ------------------------------------------------------------------

    def ocr_pdf_page(
        self, image_base64: str, system_prompt: str = "", media_type: str = "image/png",
        user_prompt: str = "",
    ) -> str:
        """OCR a single page image via DeepSeek vision. Falls back to Anthropic.

        Args:
            image_base64: Base64-encoded image.
            system_prompt: Optional knowledge context for OCR guidance.
            media_type: MIME type of the image ("image/png" or "image/jpeg").
            user_prompt: Заменя стандартната заявка „извлечи целия текст".

        Returns:
            Extracted text string.
        """
        # Build OCR prompt with optional additional context
        additional = system_prompt if system_prompt else ""
        full_ocr_system = OCR_SYSTEM_PROMPT.format(additional_context=additional)

        # ДВЕ ПРОТИВОРЕЧАЩИ СИ ИНСТРУКЦИИ (жив прогон 2026-08-07).
        #
        # Заявката тук беше закована на „Отговори САМО с извлечения текст, без
        # коментари", а извикващият слагаше своята задача в СИСТЕМНИЯ промпт.
        # Когато `extract_situation_segments` поиска JSON с отсечките, моделът
        # виждаше едновременно „върни JSON" (система) и „върни само текст"
        # (потребител) — и се подчиняваше на второто, защото е по-конкретно и
        # по-близо до изображението.
        #
        # Следствието: отговорът е свободен текст, `parse_json_strict` пада с
        # „Expecting value: line 1 column 1", и от чертежа излизат НУЛА
        # отсечки.  Оттам идва и „четенето е нестабилно между опити" — понякога
        # моделът все пак връщаше JSON, понякога не.
        ocr_user_prompt = user_prompt.strip() or (
            "Извлечи ЦЕЛИЯ текст от това изображение. "
            "Текстът е на български. Запази структурата — заглавия, параграфи, таблици. "
            "Отговори САМО с извлечения текст, без коментари."
        )

        # Try DeepSeek (или зададения OCR модел) first
        ocr_is_worker = MODEL_OCR == MODEL_WORKER
        if self.ocr_available and (self.deepseek_available or not ocr_is_worker):
            try:
                text = self._ocr_deepseek(
                    image_base64, ocr_user_prompt, full_ocr_system, media_type=media_type
                )
                self._ocr_failures = 0
                if ocr_is_worker:
                    self._note_worker_success()
                return text
            except Exception as exc:
                if ocr_is_worker:
                    self._note_worker_failure("OCR", exc)
                else:
                    # Отделен vision модел: провалът му НЕ казва нищо за
                    # работника.  Типичната причина е точно тази — `OCR_MODEL`
                    # без реален vision достъп; тогава пада всяка страница и
                    # преди тази промяна отнасяше и генерирането със себе си.
                    self._ocr_failures += 1
                    logger.warning(
                        "OCR моделът %s се провали (%s) — страницата минава "
                        "през контрольора (%d-и пореден провал от %d).",
                        MODEL_OCR, exc, self._ocr_failures,
                        _WORKER_FAILURES_BEFORE_DISABLE)
                    if self._ocr_failures >= _WORKER_FAILURES_BEFORE_DISABLE:
                        logger.error(
                            "OCR моделът %s се изключва за сесията след %d "
                            "поредни провала — провери дали има vision достъп. "
                            "ОТСЕГА OCR минава през контрольора.",
                            MODEL_OCR, self._ocr_failures)
                        self.ocr_available = False

        # Fallback to Anthropic
        if self.anthropic_available:
            try:
                return self._ocr_anthropic(
                    image_base64, ocr_user_prompt, full_ocr_system, media_type=media_type
                )
            except Exception as exc:
                logger.error("Anthropic OCR fallback failed: %s", exc)

        return "[OCR ERROR: Both AI models are unavailable]"

    def _ocr_deepseek(
        self, image_base64: str, prompt: str, system_prompt: str, media_type: str = "image/png"
    ) -> str:
        """OCR via DeepSeek vision."""
        client = self._get_deepseek()
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{media_type};base64,{image_base64}",
                    },
                },
                {"type": "text", "text": prompt},
            ],
        })

        response = client.chat.completions.create(
            model=MODEL_OCR,
            messages=messages,
            max_tokens=_OCR_MAX_TOKENS,
            timeout=_API_TIMEOUT_SECONDS,
        )
        text = response.choices[0].message.content or ""
        usage = response.usage
        tokens_in = usage.prompt_tokens if usage else 0
        tokens_out = usage.completion_tokens if usage else 0
        self._log_usage(MODEL_OCR, tokens_in, tokens_out, "ocr")
        return text

    def _ocr_anthropic(
        self, image_base64: str, prompt: str, system_prompt: str, media_type: str = "image/png"
    ) -> str:
        """OCR via Anthropic vision."""
        client = self._get_anthropic()
        response = client.messages.create(
            model=MODEL_CONTROLLER,
            max_tokens=_OCR_MAX_TOKENS,
            system=system_prompt if system_prompt else "",
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_base64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
            timeout=_API_TIMEOUT_SECONDS,
        )
        text = response.content[0].text if response.content else ""
        self._log_usage(
            MODEL_CONTROLLER,
            response.usage.input_tokens,
            response.usage.output_tokens,
            "ocr",
        )
        return text

    # ------------------------------------------------------------------
    # Lesson verification (Controller = Anthropic)
    # ------------------------------------------------------------------

    def save_lesson(
        self, lesson_text: str, context: str, existing_lessons: str = ""
    ) -> dict:
        """Validate a lesson via the controller before saving.

        Args:
            lesson_text: The new lesson to validate.
            context: Context about when/why this lesson was learned.
            existing_lessons: Summary of existing lessons.

        Returns:
            Dict with approved, formatted_lesson, reason.
        """
        system_prompt = LESSON_VERIFICATION_PROMPT.format(
            existing_lessons=existing_lessons or "(none)",
            new_lesson=lesson_text,
            context=context,
        )
        messages = [{"role": "user", "content": f"Провери този урок: {lesson_text}"}]

        # Try Anthropic first (controller)
        if self.anthropic_available:
            try:
                client = self._get_anthropic()
                response = client.messages.create(
                    model=MODEL_CONTROLLER,
                    max_tokens=_MAX_TOKENS_LESSON,
                    system=system_prompt,
                    messages=messages,
                    # Opus 4.8 не приема `temperature` (проба 2026-08-03).
                )
                raw = response.content[0].text if response.content else "{}"
                self._log_usage(
                    MODEL_CONTROLLER,
                    response.usage.input_tokens,
                    response.usage.output_tokens,
                    "lesson",
                )
                parsed = parse_contract(raw, LESSON_SPEC, "валидиране на урок")
                return {
                    "approved": parsed["approved"],
                    "formatted_lesson": parsed["formatted_lesson"] or lesson_text,
                    "reason": parsed["reason"],
                    "model": MODEL_CONTROLLER,
                }
            except Exception as exc:
                logger.warning("Anthropic lesson check failed: %s", exc)

        # Fallback: DeepSeek
        if self.deepseek_available:
            try:
                client = self._get_deepseek()
                full_msgs = [{"role": "system", "content": system_prompt}] + messages
                response = client.chat.completions.create(
                    model=MODEL_WORKER,
                    messages=full_msgs,
                    max_tokens=_MAX_TOKENS_LESSON,
                    temperature=0.1,
                )
                raw = response.choices[0].message.content or "{}"
                usage = response.usage
                self._log_usage(
                    MODEL_WORKER,
                    usage.prompt_tokens if usage else 0,
                    usage.completion_tokens if usage else 0,
                    "lesson",
                )
                parsed = parse_contract(raw, LESSON_SPEC, "валидиране на урок")
                return {
                    "approved": parsed["approved"],
                    "formatted_lesson": parsed["formatted_lesson"] or lesson_text,
                    "reason": parsed["reason"],
                    "model": MODEL_WORKER,
                }
            except Exception as exc:
                logger.error("DeepSeek lesson fallback failed: %s", exc)

        # Both failed — approve by default, let user review
        return {
            "approved": True,
            "formatted_lesson": lesson_text,
            "reason": "AI verification unavailable — lesson saved without validation.",
            "model": "none",
        }

    # ------------------------------------------------------------------
    # Usage tracking
    # ------------------------------------------------------------------

    def get_usage_stats(self) -> dict:
        """Get usage statistics grouped by model.

        Returns:
            Dict with per-model stats and totals.
        """
        deepseek_stats = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
        anthropic_stats = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
        fallback_events = 0

        for entry in self.usage_log:
            model = entry["model"]
            # Само контрольорът е Anthropic; всичко останало (работник, OCR
            # модел) минава през OpenAI-съвместимия endpoint.  Проверката е
            # по контрольора, а не по работника — иначе OCR модел, различен
            # от MODEL_WORKER, се брои погрешно като Anthropic разход.
            if model == MODEL_CONTROLLER:
                target = anthropic_stats
            else:
                target = deepseek_stats

            target["calls"] += 1
            target["tokens_in"] += entry["tokens_in"]
            target["tokens_out"] += entry["tokens_out"]
            target["cost_usd"] += entry["cost_usd"]

        # Count fallback events from log
        for entry in self.usage_log:
            if entry.get("is_fallback"):
                fallback_events += 1

        return {
            "deepseek": deepseek_stats,
            "anthropic": anthropic_stats,
            "total_cost_usd": deepseek_stats["cost_usd"] + anthropic_stats["cost_usd"],
            "fallback_events": fallback_events,
            "total_calls": deepseek_stats["calls"] + anthropic_stats["calls"],
        }

    def _log_usage(
        self, model: str, tokens_in: int, tokens_out: int, task_type: str
    ) -> None:
        """Log an API call for usage tracking (session + cumulative)."""
        cost = self._calculate_cost(model, tokens_in, tokens_out)
        self.usage_log.append({
            "timestamp": datetime.now().isoformat(),
            "model": model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost,
            "task_type": task_type,
        })

        # Update cumulative (persisted to disk)
        key = "anthropic" if model == MODEL_CONTROLLER else "deepseek"
        self._cumulative[key] = self._cumulative.get(key, 0.0) + cost
        self._cumulative["total"] = self._cumulative.get("total", 0.0) + cost
        self._cumulative["total_calls"] = self._cumulative.get("total_calls", 0) + 1
        self._save_cumulative()

    @staticmethod
    def _calculate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
        """Изчисли цена за извикване.

        Одит 2026-08: при `DEEPSEEK_MODEL=anthropic/claude-sonnet-5` точен ключ в
        PRICING няма → падаше към DeepSeek тарифата ($0.70/2M вместо ~$12/2M).
        Затова при липса на точен ключ разпознаваме семейството по име (sonnet/
        opus/claude).  NB: през OpenRouter реалната цена може да се различава
        (markup) — това е приблизителна оценка; OpenRouter е авторитетът за billing.
        """
        # Семейството се проверява ПРЕДИ точния ключ: PRICING[MODEL_WORKER] може
        # да е самият slug „anthropic/claude-sonnet-5" с DeepSeek тарифа (създаден
        # при import), затова точен match би дал грешна цена.
        m = (model or "").lower()
        if "sonnet" in m:
            rate = PRICING["claude-sonnet-5"]
        elif "opus" in m:
            rate = PRICING["claude-opus-5"]
        elif "claude" in m:
            rate = PRICING["claude-opus-5"]
        else:
            rate = PRICING.get(model, PRICING[MODEL_WORKER])
        return tokens_in * rate["input"] + tokens_out * rate["output"]

    # ------------------------------------------------------------------
    # Cumulative usage persistence
    # ------------------------------------------------------------------

    def set_cumulative_path(self, config_dir: str) -> None:
        """Set the path for cumulative usage file and load existing data."""
        self._cumulative_path = Path(config_dir) / "cumulative_usage.json"
        self._load_cumulative()

    def _load_cumulative(self) -> None:
        """Load cumulative usage from disk."""
        if self._cumulative_path and self._cumulative_path.exists():
            try:
                data = json.loads(self._cumulative_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._cumulative = data
            except (json.JSONDecodeError, OSError) as exc:
                logger.debug("Could not load cumulative usage cache (%s): %s", self._cumulative_path, exc)

    def _save_cumulative(self) -> None:
        """Save cumulative usage to disk."""
        if not self._cumulative_path:
            return
        try:
            self._cumulative_path.parent.mkdir(parents=True, exist_ok=True)
            self._cumulative_path.write_text(
                json.dumps(self._cumulative, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.debug("Could not save cumulative usage cache (%s): %s", self._cumulative_path, exc)

    def get_cumulative_stats(self) -> dict:
        """Get all-time cumulative usage stats (persisted across sessions)."""
        return dict(self._cumulative)

    # ------------------------------------------------------------------
    # JSON parsing helper
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_json_response(raw: str) -> dict:
        """Parse a JSON response from AI, handling common formatting issues.

        При провал връща ПРАЗЕН dict, не измислен резултат от верификация.
        Старото поведение връщаше `{"approved": False, "issues": [...]}` —
        което извикващите за класификация на файлове и разпознаване на
        намерение получаваха като „отговор" с напълно чужди полета, а
        верификацията четеше като „графикът има проблеми" (P7).

        За операции с известна форма ползвай `json_contract.parse_contract`,
        което хвърля вместо да гадае.
        """
        parsed = parse_json_strict(raw)
        if parsed.data is None:
            logger.warning(
                "Неуспешно парсване на JSON отговор (%s): %.200s", parsed.error, raw
            )
            return {}
        return parsed.data
