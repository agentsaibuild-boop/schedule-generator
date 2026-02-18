"""AI processor — orchestrates document analysis, schedule generation, and chat.

Uses AIRouter for all API calls (DeepSeek worker + Anthropic controller).
Enforces strict JSON pipeline: only converted .json files are accepted for analysis.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.ai_router import AIRouter
    from src.knowledge_manager import KnowledgeManager

logger = logging.getLogger(__name__)


class AIProcessor:
    """Orchestrates AI-powered schedule generation and document analysis."""

    def __init__(
        self,
        router: AIRouter | None = None,
        knowledge_manager: KnowledgeManager | None = None,
        api_key: str | None = None,
        skills_path: str = "",
    ) -> None:
        """Initialize the AI processor.

        Args:
            router: AIRouter instance for dual-AI calls.
            knowledge_manager: KnowledgeManager for building prompts.
            api_key: Legacy param (kept for backward compat during transition).
            skills_path: Legacy param.
        """
        self.router = router
        self.knowledge = knowledge_manager
        self._legacy_api_key = api_key or ""

    @property
    def is_configured(self) -> bool:
        """Check whether at least one AI model is available."""
        if self.router:
            return self.router.deepseek_available or self.router.anthropic_available
        return bool(self._legacy_api_key)

    # ------------------------------------------------------------------
    # System prompt builders
    # ------------------------------------------------------------------

    def build_system_prompt(self, project_type: str | None = None) -> str:
        """Build FULL system prompt for the worker (DeepSeek) from all knowledge tiers.

        Includes: SKILL.md + methodology + last 20 lessons + productivities + workflow.
        ~5000-8000 tokens.

        Args:
            project_type: Optional project type for specific methodology.

        Returns:
            Combined system prompt string.
        """
        if self.knowledge:
            return self.knowledge.get_all_knowledge_for_prompt(
                project_type=project_type, level="full"
            )

        return (
            "Ти си асистент за строителни графици за ВиК проекти в България. "
            "Отговаряй на български. Следвай правилата за генериране на линейни графици."
        )

    def build_minimal_prompt(self) -> str:
        """Build minimal system prompt for lightweight tasks (OCR, simple questions).

        Includes ONLY: core rules + productivities.
        ~1500-2000 tokens. Saves tokens for routine operations.

        Returns:
            Minimal system prompt string.
        """
        if self.knowledge:
            return self.knowledge.get_all_knowledge_for_prompt(level="minimal")

        return (
            "Ти си асистент за строителни графици за ВиК проекти в България. "
            "Отговаряй на български."
        )

    def build_verification_prompt(self) -> str:
        """Build strict verification rules for the controller (Anthropic).

        Returns:
            Verification rules string.
        """
        parts = ["Проверявай СТРИКТНО следните правила:\n"]

        if self.knowledge:
            # Include skills (core rules)
            skills = self.knowledge.get_skills()
            if skills:
                parts.append(skills)

            # Include verification checklist if available
            refs_path = self.knowledge.skills_path / "references"
            checklist_path = refs_path / "verification-checklist.md"
            if checklist_path.exists():
                parts.append(
                    "\n=== VERIFICATION CHECKLIST ===\n"
                    + checklist_path.read_text(encoding="utf-8")
                )

            # Include workflow rules
            workflow_path = refs_path / "workflow-rules.md"
            if workflow_path.exists():
                parts.append(
                    "\n=== WORKFLOW RULES ===\n"
                    + workflow_path.read_text(encoding="utf-8")
                )

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------

    def _validate_json_inputs(self, files: list[dict]) -> None:
        """Validate that all input files are converted .json files.

        Args:
            files: List of file info dicts from FileManager.

        Raises:
            ValueError: If any non-JSON files are detected.
        """
        non_json = [
            f.get("original", f.get("name", "unknown"))
            for f in files
            if f.get("converted") and not f["converted"].endswith(".json")
        ]
        if non_json:
            raise ValueError(
                f"Non-JSON files detected: {non_json}. "
                "Run file conversion first! (Rule #0)"
            )

    # ------------------------------------------------------------------
    # Document analysis
    # ------------------------------------------------------------------

    def analyze_documents(self, converted_files: list[dict]) -> dict:
        """Analyze converted documents via the worker (DeepSeek).

        IMPORTANT: Only accepts converted .json files (Rule #0).

        Args:
            converted_files: List of file info dicts from FileManager.get_converted_files().

        Returns:
            Analysis dict with project_type, scope, quantities, etc.
        """
        if not self.router:
            return {
                "status": "error",
                "message": "AI Router not initialized.",
            }

        # Validate: only JSON files allowed
        self._validate_json_inputs(converted_files)

        # Build a summary of all files
        file_summaries = []
        for f in converted_files:
            name = f.get("original", f.get("name", "unknown"))
            method = f.get("method", "")
            file_summaries.append(f"- {name} ({method})")

        files_text = "\n".join(file_summaries)

        system_prompt = self.build_system_prompt()
        messages = [{
            "role": "user",
            "content": (
                "Анализирай следните конвертирани документи от тендерна процедура за ВиК:\n\n"
                f"{files_text}\n\n"
                "Определи:\n"
                "1. Тип проект (разпределителна мрежа, довеждащ, единичен, инженеринг, mega)\n"
                "2. Обхват — какви мрежи се строят (водопровод, канализация, пътни)\n"
                "3. Количества — DN, дължини на клонове/участъци\n"
                "4. Срокове — ако са споменати\n"
                "5. Специфики — терен, материали, брой екипи\n\n"
                "Отговори в JSON формат."
            ),
        }]

        result = self.router.chat(messages, system_prompt)

        return {
            "status": "ok",
            "analysis": result["content"],
            "model": result["model"],
            "cost": result["cost"],
            "fallback": result.get("fallback", False),
        }

    # ------------------------------------------------------------------
    # Schedule generation with verification cycle
    # ------------------------------------------------------------------

    def generate_schedule(
        self,
        analysis: dict,
        project_type: str,
        progress_callback: Any | None = None,
    ) -> dict:
        """Generate a schedule via worker, then verify via controller.

        Args:
            analysis: Analysis dict from analyze_documents.
            project_type: Type of construction project.
            progress_callback: Optional callable(message: str) for progress.

        Returns:
            Dict with schedule, correction history, costs.
        """
        if not self.router:
            return {
                "status": "error",
                "message": "AI Router not initialized.",
            }

        # Step 1: Generate via DeepSeek
        if progress_callback:
            model_label = "DeepSeek" if self.router.deepseek_available else "Anthropic"
            progress_callback(f"Генерирам график... ({model_label})")

        system_prompt = self.build_system_prompt(project_type)
        analysis_text = (
            analysis.get("analysis", "")
            if isinstance(analysis.get("analysis"), str)
            else json.dumps(analysis, ensure_ascii=False)
        )

        messages = [{
            "role": "user",
            "content": (
                f"Генерирай строителен линеен график за следния проект:\n\n"
                f"{analysis_text}\n\n"
                f"Тип: {project_type}\n\n"
                "Отговори в JSON формат с:\n"
                "- tasks: масив от задачи с id, name, duration, start_day, "
                "dependencies, dn, length_m, team\n"
                "- total_duration: общ брой дни\n"
                "- teams: списък екипи\n"
                "- notes: допълнителни бележки"
            ),
        }]

        gen_result = self.router.chat(messages, system_prompt)

        if gen_result.get("error"):
            return {
                "status": "error",
                "message": gen_result["content"],
            }

        schedule_json = gen_result["content"]

        # Step 2: Verification cycle
        rules = self.build_verification_prompt()

        cycle_result = self.router.run_correction_cycle(
            schedule_json, rules, max_cycles=3, progress_callback=progress_callback
        )

        gen_cost = gen_result.get("cost", 0.0)
        cycle_cost = cycle_result.get("total_cost", 0.0)

        return {
            "status": cycle_result["status"],
            "schedule": cycle_result["schedule"],
            "cycles": cycle_result["cycles"],
            "total_cost": gen_cost + cycle_cost,
            "history": cycle_result.get("history", []),
            "remaining_issues": cycle_result.get("remaining_issues", []),
            "gen_model": gen_result["model"],
        }

    # ------------------------------------------------------------------
    # Chat response
    # ------------------------------------------------------------------

    def chat_response(
        self, messages: list[dict], project_context: dict | None = None
    ) -> dict:
        """Process a chat message via the worker.

        Args:
            messages: Chat history as list of dicts.
            project_context: Optional current project info.

        Returns:
            Dict with content, model, cost, fallback.
        """
        if not self.router:
            return {
                "content": "AI не е инициализиран. Проверете .env файла.",
                "model": "none",
                "cost": 0.0,
            }

        system_prompt = self.build_system_prompt()
        if project_context:
            ctx_str = json.dumps(project_context, ensure_ascii=False, default=str)
            system_prompt += f"\n\nТекущ проект: {ctx_str}"

        return self.router.chat(messages, system_prompt)

    # ------------------------------------------------------------------
    # OCR (delegates to router, which handles fallback)
    # ------------------------------------------------------------------

    def ocr_pdf(self, filepath: str) -> dict:
        """OCR a scanned PDF using AI vision (DeepSeek, fallback Anthropic).

        Args:
            filepath: Absolute path to the PDF file.

        Returns:
            Dict with 'status' and 'data' keys matching conversion format.
        """
        try:
            import fitz  # PyMuPDF
        except ImportError:
            return {
                "status": "error",
                "error": "PyMuPDF (fitz) is required for OCR. Run: pip install PyMuPDF",
            }

        if not self.router:
            return {"status": "error", "error": "AI Router not initialized."}

        # Build minimal prompt for OCR context
        ocr_system_prompt = self.build_minimal_prompt()

        source_name = Path(filepath).name
        doc = fitz.open(filepath)
        pages_text: list[dict] = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            pix = page.get_pixmap(dpi=200)
            img_bytes = pix.tobytes("png")
            b64_image = base64.b64encode(img_bytes).decode("ascii")

            try:
                extracted = self.router.ocr_pdf_page(
                    b64_image, system_prompt=ocr_system_prompt
                )
            except Exception as exc:
                logger.warning(
                    "OCR error on page %d of %s: %s", page_num + 1, source_name, exc
                )
                if "rate" in str(exc).lower():
                    time.sleep(5)
                extracted = f"[OCR ERROR page {page_num + 1}: {exc}]"

            pages_text.append({"page": page_num + 1, "text": extracted})
            logger.info(
                "OCR page %d/%d of %s: %d chars",
                page_num + 1, len(doc), source_name, len(extracted),
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
    # Legacy compatibility methods
    # ------------------------------------------------------------------

    def process_documents(self, files: list[dict], project_type: str) -> dict:
        """Legacy method — delegates to analyze_documents."""
        return self.analyze_documents(files)

    def generate_schedule_legacy(self, analysis: dict, config: dict) -> dict:
        """Legacy method — delegates to generate_schedule."""
        return self.generate_schedule(analysis, config.get("project_type", ""))

    def chat(self, messages: list[dict], system_prompt: str) -> str:
        """Legacy chat method — returns just the content string."""
        result = self.chat_response(messages)
        return result.get("content", "")

    def ask_clarification(self, context: str, question: str) -> str:
        """Legacy method for clarification questions."""
        return f"Уточняващ въпрос: {question}"
