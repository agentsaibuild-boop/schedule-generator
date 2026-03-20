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

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pricing per token (USD)
# ---------------------------------------------------------------------------
PRICING = {
    "deepseek-chat": {"input": 0.28 / 1_000_000, "output": 0.42 / 1_000_000},  # V3.2, Feb 2026
    "claude-sonnet-4-6": {"input": 3.0 / 1_000_000, "output": 15.0 / 1_000_000},
}

# ---------------------------------------------------------------------------
# Token limits per call type
# ---------------------------------------------------------------------------
_MAX_TOKENS_CHAT = 4096        # regular chat, analysis, OCR, verification
_MAX_TOKENS_CORRECTION = 8192  # schedule correction (larger output needed)
_MAX_TOKENS_LESSON = 1024      # lesson verification (short JSON response)
_MIN_SYSTEM_PROMPT_LEN = 100   # minimum viable knowledge-aware system prompt

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
            base_url="https://api.deepseek.com",
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

        self._anthropic_client = anthropic.Anthropic(api_key=self._anthropic_key)
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
                model="deepseek-chat",
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
                model="claude-sonnet-4-6",
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

    # ------------------------------------------------------------------
    # Chat (Worker = DeepSeek, fallback = Anthropic)
    # ------------------------------------------------------------------

    def chat(self, messages: list[dict], system_prompt: str) -> dict:
        """Send a chat message to the worker (DeepSeek). Falls back to Anthropic.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            system_prompt: System prompt with knowledge context.

        Returns:
            Dict with content, model, usage, cost, fallback.
        """
        self._warn_empty_prompt(system_prompt, "chat")

        # Try DeepSeek first
        if self.deepseek_available:
            try:
                return self._chat_deepseek(messages, system_prompt)
            except Exception as exc:
                logger.warning("DeepSeek chat failed, trying fallback: %s", exc)
                self.deepseek_available = False
                self._update_fallback_state()

        # Fallback to Anthropic
        if self.anthropic_available:
            try:
                return self._chat_anthropic(messages, system_prompt, is_fallback=True)
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

    def _chat_deepseek(self, messages: list[dict], system_prompt: str) -> dict:
        """Send chat to DeepSeek via OpenAI-compatible API."""
        client = self._get_deepseek()
        full_messages = [{"role": "system", "content": system_prompt}] + messages

        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=full_messages,
            max_tokens=_MAX_TOKENS_CHAT,
            temperature=0.3,
            timeout=120,
        )

        content = response.choices[0].message.content or ""
        usage = response.usage
        tokens_in = usage.prompt_tokens if usage else 0
        tokens_out = usage.completion_tokens if usage else 0

        self._log_usage("deepseek-chat", tokens_in, tokens_out, "chat")

        return {
            "content": content,
            "model": "deepseek-chat",
            "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out},
            "cost": self._calculate_cost("deepseek-chat", tokens_in, tokens_out),
            "fallback": False,
        }

    def _chat_anthropic(
        self, messages: list[dict], system_prompt: str, *, is_fallback: bool = False,
        max_tokens: int = _MAX_TOKENS_CHAT,
    ) -> dict:
        """Send chat to Anthropic Claude."""
        client = self._get_anthropic()

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            system=system_prompt,
            messages=messages,
            timeout=120,
        )

        content = response.content[0].text if response.content else ""
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens

        self._log_usage("claude-sonnet-4-6", tokens_in, tokens_out, "chat")

        return {
            "content": content,
            "model": "claude-sonnet-4-6",
            "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out},
            "cost": self._calculate_cost("claude-sonnet-4-6", tokens_in, tokens_out),
            "fallback": is_fallback,
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

        # Try Anthropic first
        if self.anthropic_available:
            try:
                return self._verify_with_model(
                    "anthropic", system_prompt, user_message
                )
            except Exception as exc:
                logger.warning("Anthropic verify failed, trying fallback: %s", exc)
                self.anthropic_available = False
                self._update_fallback_state()

        # Fallback to DeepSeek
        if self.deepseek_available:
            try:
                return self._verify_with_model(
                    "deepseek", system_prompt, user_message
                )
            except Exception as exc:
                logger.error("DeepSeek fallback verify also failed: %s", exc)
                self.deepseek_available = False
                self._update_fallback_state()

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
                model="claude-sonnet-4-6",
                max_tokens=_MAX_TOKENS_CHAT,
                system=system_prompt,
                messages=messages,
                temperature=0.1,
                timeout=120,
            )
            raw = response.content[0].text if response.content else "{}"
            tokens_in = response.usage.input_tokens
            tokens_out = response.usage.output_tokens
            model = "claude-sonnet-4-6"
        else:
            client = self._get_deepseek()
            full_msgs = [{"role": "system", "content": system_prompt}] + messages
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=full_msgs,
                max_tokens=_MAX_TOKENS_CHAT,
                temperature=0.1,
                timeout=120,
            )
            raw = response.choices[0].message.content or "{}"
            usage = response.usage
            tokens_in = usage.prompt_tokens if usage else 0
            tokens_out = usage.completion_tokens if usage else 0
            model = "deepseek-chat"

        self._log_usage(model, tokens_in, tokens_out, "verify")
        cost = self._calculate_cost(model, tokens_in, tokens_out)

        # Parse JSON response
        parsed = self._parse_json_response(raw)

        return {
            "approved": parsed.get("approved", False),
            "issues": parsed.get("issues", []),
            "corrections": parsed.get("corrections", []),
            "summary": parsed.get("summary", ""),
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
                return self._apply_with_model("deepseek", messages, full_system, schedule_json)
            except Exception as exc:
                logger.warning("DeepSeek corrections failed: %s", exc)
                self.deepseek_available = False
                self._update_fallback_state()

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
                model="deepseek-chat",
                messages=full_msgs,
                max_tokens=_MAX_TOKENS_CORRECTION,
                temperature=0.1,
                timeout=120,
            )
            raw = response.choices[0].message.content or "{}"
            usage = response.usage
            tokens_in = usage.prompt_tokens if usage else 0
            tokens_out = usage.completion_tokens if usage else 0
            model = "deepseek-chat"
        else:
            client = self._get_anthropic()
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=_MAX_TOKENS_CORRECTION,
                system=system_prompt,
                messages=messages,
                temperature=0.1,
                timeout=120,
            )
            raw = response.content[0].text if response.content else "{}"
            tokens_in = response.usage.input_tokens
            tokens_out = response.usage.output_tokens
            model = "claude-sonnet-4-6"

        self._log_usage(model, tokens_in, tokens_out, "correct")
        cost = self._calculate_cost(model, tokens_in, tokens_out)

        parsed = self._parse_json_response(raw)

        return {
            "corrected_schedule": parsed.get("schedule", schedule_json),
            "applied": parsed.get("applied", []),
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
                    "error": "AI models are unavailable.",
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
        self, image_base64: str, system_prompt: str = "", media_type: str = "image/png"
    ) -> str:
        """OCR a single page image via DeepSeek vision. Falls back to Anthropic.

        Args:
            image_base64: Base64-encoded image.
            system_prompt: Optional knowledge context for OCR guidance.
            media_type: MIME type of the image ("image/png" or "image/jpeg").

        Returns:
            Extracted text string.
        """
        # Build OCR prompt with optional additional context
        additional = system_prompt if system_prompt else ""
        full_ocr_system = OCR_SYSTEM_PROMPT.format(additional_context=additional)

        ocr_user_prompt = (
            "Извлечи ЦЕЛИЯ текст от това изображение. "
            "Текстът е на български. Запази структурата — заглавия, параграфи, таблици. "
            "Отговори САМО с извлечения текст, без коментари."
        )

        # Try DeepSeek first
        if self.deepseek_available:
            try:
                return self._ocr_deepseek(
                    image_base64, ocr_user_prompt, full_ocr_system, media_type=media_type
                )
            except Exception as exc:
                logger.warning("DeepSeek OCR failed: %s", exc)
                self.deepseek_available = False
                self._update_fallback_state()

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
            model="deepseek-chat",
            messages=messages,
            max_tokens=_MAX_TOKENS_CHAT,
            timeout=120,
        )
        text = response.choices[0].message.content or ""
        usage = response.usage
        tokens_in = usage.prompt_tokens if usage else 0
        tokens_out = usage.completion_tokens if usage else 0
        self._log_usage("deepseek-chat", tokens_in, tokens_out, "ocr")
        return text

    def _ocr_anthropic(
        self, image_base64: str, prompt: str, system_prompt: str, media_type: str = "image/png"
    ) -> str:
        """OCR via Anthropic vision."""
        client = self._get_anthropic()
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=_MAX_TOKENS_CHAT,
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
            timeout=120,
        )
        text = response.content[0].text if response.content else ""
        self._log_usage(
            "claude-sonnet-4-6",
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
                    model="claude-sonnet-4-6",
                    max_tokens=_MAX_TOKENS_LESSON,
                    system=system_prompt,
                    messages=messages,
                    temperature=0.1,
                )
                raw = response.content[0].text if response.content else "{}"
                self._log_usage(
                    "claude-sonnet-4-6",
                    response.usage.input_tokens,
                    response.usage.output_tokens,
                    "lesson",
                )
                parsed = self._parse_json_response(raw)
                return {
                    "approved": parsed.get("approved", False),
                    "formatted_lesson": parsed.get("formatted_lesson", lesson_text),
                    "reason": parsed.get("reason", ""),
                    "model": "claude-sonnet-4-6",
                }
            except Exception as exc:
                logger.warning("Anthropic lesson check failed: %s", exc)

        # Fallback: DeepSeek
        if self.deepseek_available:
            try:
                client = self._get_deepseek()
                full_msgs = [{"role": "system", "content": system_prompt}] + messages
                response = client.chat.completions.create(
                    model="deepseek-chat",
                    messages=full_msgs,
                    max_tokens=_MAX_TOKENS_LESSON,
                    temperature=0.1,
                )
                raw = response.choices[0].message.content or "{}"
                usage = response.usage
                self._log_usage(
                    "deepseek-chat",
                    usage.prompt_tokens if usage else 0,
                    usage.completion_tokens if usage else 0,
                    "lesson",
                )
                parsed = self._parse_json_response(raw)
                return {
                    "approved": parsed.get("approved", False),
                    "formatted_lesson": parsed.get("formatted_lesson", lesson_text),
                    "reason": parsed.get("reason", ""),
                    "model": "deepseek-chat",
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
            if model == "deepseek-chat":
                target = deepseek_stats
            else:
                target = anthropic_stats

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
        key = "deepseek" if model == "deepseek-chat" else "anthropic"
        self._cumulative[key] = self._cumulative.get(key, 0.0) + cost
        self._cumulative["total"] = self._cumulative.get("total", 0.0) + cost
        self._cumulative["total_calls"] = self._cumulative.get("total_calls", 0) + 1
        self._save_cumulative()

    @staticmethod
    def _calculate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
        """Calculate cost for a specific API call."""
        rate = PRICING.get(model, PRICING["deepseek-chat"])
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
        """Parse a JSON response from AI, handling common formatting issues."""
        text = raw.strip()

        # Remove markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last lines (``` markers)
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to find JSON object in the text
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass

        logger.warning("Failed to parse JSON response: %.200s", text)
        return {"approved": False, "issues": ["Invalid JSON response from AI"], "corrections": []}
