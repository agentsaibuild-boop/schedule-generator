"""Anthropic API communication handler for AI-powered schedule generation.

Provides chat, document analysis, schedule generation, and OCR via vision.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class AIProcessor:
    """Handles communication with the Anthropic API for AI responses."""

    def __init__(self, api_key: str | None = None, skills_path: str = "") -> None:
        """Initialize the AI processor.

        Reads ANTHROPIC_API_KEY from the environment (via .env) if not provided.

        Args:
            api_key: Anthropic API key. Falls back to env var.
            skills_path: Path to the skills directory for system prompts.
        """
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.skills_path = skills_path
        self.model = "claude-sonnet-4-5-20250929"
        self.ocr_model = "claude-sonnet-4-5-20250929"
        self._client = None

    # ------------------------------------------------------------------
    # Lazy client initialization
    # ------------------------------------------------------------------

    def _get_client(self):
        """Return an Anthropic client, creating one on first call."""
        if self._client is not None:
            return self._client

        if not self.api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. "
                "Add it to the .env file or pass it directly."
            )

        import anthropic

        self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    @property
    def is_configured(self) -> bool:
        """Check whether an API key is available."""
        return bool(self.api_key)

    # ------------------------------------------------------------------
    # OCR via vision
    # ------------------------------------------------------------------

    def ocr_pdf(self, filepath: str) -> dict:
        """OCR a scanned PDF using Anthropic vision API.

        Each page is rendered as an image and sent to the API for text
        extraction. Results are collected into the standard converted-JSON
        format used by FileManager.

        Args:
            filepath: Absolute path to the PDF file.

        Returns:
            Dict with 'status' and 'data' keys matching the conversion format.
        """
        try:
            import fitz  # PyMuPDF
        except ImportError:
            return {
                "status": "error",
                "error": "PyMuPDF (fitz) is required for OCR. Run: pip install PyMuPDF",
            }

        client = self._get_client()
        source_name = Path(filepath).name

        doc = fitz.open(filepath)
        pages_text: list[dict] = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            # Render at 200 DPI for good OCR quality
            pix = page.get_pixmap(dpi=200)
            img_bytes = pix.tobytes("png")
            b64_image = base64.b64encode(img_bytes).decode("ascii")

            # Call the vision API
            try:
                message = client.messages.create(
                    model=self.ocr_model,
                    max_tokens=4096,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/png",
                                        "data": b64_image,
                                    },
                                },
                                {
                                    "type": "text",
                                    "text": (
                                        "Извлечи ЦЕЛИЯ текст от това изображение. "
                                        "Запази структурата — заглавия, параграфи, таблици. "
                                        "Текстът е на български език. "
                                        "Отговори САМО с извлечения текст, без коментари."
                                    ),
                                },
                            ],
                        }
                    ],
                )
                extracted = message.content[0].text
            except Exception as exc:
                logger.warning(
                    "OCR API error on page %d of %s: %s",
                    page_num + 1,
                    source_name,
                    exc,
                )
                # Rate limit back-off
                if "rate" in str(exc).lower():
                    time.sleep(5)
                extracted = f"[OCR ГРЕШКА стр. {page_num + 1}: {exc}]"

            pages_text.append({"page": page_num + 1, "text": extracted})
            logger.info(
                "OCR page %d/%d of %s: %d chars",
                page_num + 1,
                len(doc),
                source_name,
                len(extracted),
            )

        doc.close()

        full_text = "\n\n".join(p["text"] for p in pages_text if p["text"])

        data = {
            "source_file": source_name,
            "type": "pdf",
            "extraction_method": "ocr_vision",
            "pages": len(pages_text),
            "content": pages_text,
            "full_text": full_text,
        }
        return {"status": "ok", "data": data}

    # ------------------------------------------------------------------
    # Document analysis (placeholder)
    # ------------------------------------------------------------------

    def process_documents(self, files: list[dict], project_type: str) -> dict:
        """Analyze project documents and extract schedule parameters.

        Args:
            files: List of file info dicts.
            project_type: Type of construction project.

        Returns:
            Analysis dict with extracted parameters.
        """
        return {
            "status": "placeholder",
            "message": "Document processing will be implemented with Anthropic API.",
            "project_type": project_type,
            "files_count": len(files),
        }

    # ------------------------------------------------------------------
    # Schedule generation (placeholder)
    # ------------------------------------------------------------------

    def generate_schedule(self, analysis: dict, config: dict) -> dict:
        """Generate a schedule from analysis results.

        Args:
            analysis: Analysis dict from process_documents.
            config: Schedule configuration parameters.

        Returns:
            Schedule dict with tasks, durations, dependencies.
        """
        return {
            "status": "placeholder",
            "message": "Schedule generation will be implemented with Anthropic API.",
            "tasks": [],
        }

    # ------------------------------------------------------------------
    # Clarification (placeholder)
    # ------------------------------------------------------------------

    def ask_clarification(self, context: str, question: str) -> str:
        """Ask the user a clarification question based on context.

        Args:
            context: Current conversation/project context.
            question: The clarification question to ask.

        Returns:
            Formatted question string.
        """
        return f"Уточняващ въпрос: {question}"

    # ------------------------------------------------------------------
    # Chat (placeholder)
    # ------------------------------------------------------------------

    def chat(self, messages: list[dict], system_prompt: str) -> str:
        """Send a chat message and get a response.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            system_prompt: System prompt with knowledge context.

        Returns:
            AI response string.
        """
        return (
            "Тази функция ще бъде свързана с Anthropic API в следваща стъпка. "
            "Засега работя в режим на placeholder."
        )
