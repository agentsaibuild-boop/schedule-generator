"""File manager for accessing, converting, and caching local project files.

Implements Rule #0: Convert 100% of documentation BEFORE analysis.
Supports PDF (text + OCR), Excel, DOCX, CSV, JSON, TXT.
Converted files are stored in a ``converted/`` subfolder inside the
project directory together with a ``_manifest.json`` cache.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".xlsx", ".xls", ".csv", ".json", ".txt", ".docx"}

# Minimum average characters per page to consider a PDF "text-based"
_MIN_CHARS_PER_PAGE = 50

APP_VERSION = "0.1"


class FileManager:
    """Manages access to local project files and their conversion to JSON."""

    def __init__(self, base_path: str | None = None) -> None:
        """Initialize the file manager.

        Args:
            base_path: Optional base path to the project directory.
        """
        self.base_path: Path | None = Path(base_path) if base_path else None
        self._manifest: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Project path management
    # ------------------------------------------------------------------

    def set_project_path(self, path: str) -> dict:
        """Validate and set the project directory, scanning for files.

        Args:
            path: Path to the project directory.

        Returns:
            Dict with keys: valid, files_count, converted_count, needs_conversion.
        """
        p = Path(path)
        if not (p.exists() and p.is_dir()):
            return {
                "valid": False,
                "files_count": 0,
                "converted_count": 0,
                "needs_conversion": 0,
            }

        self.base_path = p
        self._load_manifest()

        supported = self._list_supported_files()
        status = self.get_conversion_status()

        return {
            "valid": True,
            "files_count": len(supported),
            "converted_count": status["converted"],
            "needs_conversion": status["pending"] + status["changed"],
        }

    # ------------------------------------------------------------------
    # File listing helpers
    # ------------------------------------------------------------------

    def _list_supported_files(self) -> list[Path]:
        """List supported files in project dir (max 1 level of recursion).

        Excludes files inside the ``converted/`` subfolder.
        """
        if not self.base_path:
            return []

        files: list[Path] = []
        converted_dir = self.base_path / "converted"

        for f in sorted(self.base_path.iterdir()):
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS:
                files.append(f)
            elif f.is_dir() and f != converted_dir:
                for child in sorted(f.iterdir()):
                    if child.is_file() and child.suffix.lower() in SUPPORTED_EXTENSIONS:
                        files.append(child)
        return files

    def list_files(self) -> list[dict]:
        """List supported project files with metadata.

        Returns:
            List of dicts with name, path, size_kb, extension.
        """
        result = []
        for f in self._list_supported_files():
            stat = f.stat()
            result.append({
                "name": f.name,
                "path": str(f),
                "size_kb": round(stat.st_size / 1024, 1),
                "extension": f.suffix.lower(),
            })
        return result

    def get_supported_files(self) -> list[dict]:
        """Alias for list_files (backward compat)."""
        return self.list_files()

    def get_project_summary(self) -> dict:
        """Get a summary of the project directory.

        Returns:
            Dict with total_files, total_size_kb, by_type, supported_files.
        """
        files = self.list_files()
        if not files:
            return {
                "total_files": 0,
                "total_size_kb": 0,
                "by_type": {},
                "supported_files": 0,
            }

        by_type: dict[str, int] = {}
        total_size = 0.0
        for f in files:
            ext = f["extension"] or "(other)"
            by_type[ext] = by_type.get(ext, 0) + 1
            total_size += f["size_kb"]

        return {
            "total_files": len(files),
            "total_size_kb": round(total_size, 1),
            "by_type": by_type,
            "supported_files": len(files),
        }

    # ------------------------------------------------------------------
    # Manifest management
    # ------------------------------------------------------------------

    def _manifest_path(self) -> Path:
        assert self.base_path is not None
        return self.base_path / "converted" / "_manifest.json"

    def _load_manifest(self) -> None:
        mp = self._manifest_path()
        if mp.exists():
            try:
                self._manifest = json.loads(mp.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._manifest = {}
        else:
            self._manifest = {}

    def _save_manifest(self) -> None:
        assert self.base_path is not None
        converted_dir = self.base_path / "converted"
        converted_dir.mkdir(exist_ok=True)

        self._manifest.setdefault("project_path", str(self.base_path))
        self._manifest.setdefault("created", _now_iso())
        self._manifest["last_updated"] = _now_iso()
        self._manifest["app_version"] = APP_VERSION

        # Recompute stats
        files_info: dict = self._manifest.get("files", {})
        ok = sum(1 for v in files_info.values() if v.get("status") == "ok")
        ocr = sum(
            1
            for v in files_info.values()
            if v.get("conversion_method") == "ocr_vision"
        )
        failed = sum(1 for v in files_info.values() if v.get("status") == "error")
        self._manifest["stats"] = {
            "total_files": len(files_info),
            "converted_ok": ok,
            "converted_ocr": ocr,
            "failed": failed,
        }

        self._manifest_path().write_text(
            json.dumps(self._manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Conversion status
    # ------------------------------------------------------------------

    def is_conversion_needed(self) -> bool:
        """Check if there are unconverted or changed files.

        Returns:
            True if any files need conversion.
        """
        status = self.get_conversion_status()
        return (status["pending"] + status["changed"]) > 0

    def get_conversion_status(self) -> dict:
        """Compare original files against the manifest.

        Returns:
            Dict with total, converted, pending, changed, failed, method_summary, files (list of details).
        """
        supported = self._list_supported_files()
        manifest_files: dict = self._manifest.get("files", {})
        details: list[dict] = []
        converted = 0
        pending = 0
        changed = 0

        for fp in supported:
            name = fp.name
            stat = fp.stat()
            entry = manifest_files.get(name)

            if entry is None:
                details.append({"name": name, "status": "pending"})
                pending += 1
            elif entry.get("status") == "error":
                details.append({"name": name, "status": "pending"})
                pending += 1
            elif (
                entry.get("original_size") != stat.st_size
                or entry.get("original_modified")
                != datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                .replace(tzinfo=None)
                .isoformat(timespec="seconds")
            ):
                details.append({"name": name, "status": "changed"})
                changed += 1
            else:
                details.append({
                    "name": name,
                    "status": "converted",
                    "method": entry.get("conversion_method", ""),
                })
                converted += 1

        # Build method summary from manifest
        method_counts: dict[str, int] = {}
        for name, entry in manifest_files.items():
            if entry.get("status") == "ok":
                method = entry.get("conversion_method", "unknown")
                method_counts[method] = method_counts.get(method, 0) + 1

        return {
            "total": len(supported),
            "converted": converted,
            "pending": pending,
            "changed": changed,
            "method_summary": method_counts,
            "files": details,
        }

    # ------------------------------------------------------------------
    # File classification (pre-conversion check)
    # ------------------------------------------------------------------

    # Keywords that indicate a mandatory Bill-of-Quantities file (КСС)
    _REQUIRED_KEYWORDS: frozenset[str] = frozenset({
        "ксс", "кс ", "количествен", "сметка", "bill", "boq",
    })

    # Keywords that indicate useful-but-optional supporting documents
    _USEFUL_KEYWORDS: frozenset[str] = frozenset({
        "технич", "задание", "спецификац", "договор", "проект",
        "обяснителн", "записка", "пояснителн", "техническо",
    })

    # Keywords that indicate a situation / site-plan drawing (трасировъчен план)
    # These files contain street/quarter names as visual labels — ground-truth for locations.
    _SITUATION_KEYWORDS: frozenset[str] = frozenset({
        "ситуация", "ситуат", "трасе", "трасировъч", "situation", "site plan",
        "генерален план", "ген.план", "генплан",
    })

    def classify_files(self, ai_processor: Any | None = None) -> dict:
        """Classify project files as required, useful, situation, or unknown.

        Step 1: keyword match on filename (free, instant).
        Step 2: if no required file found and ai_processor available,
                ask DeepSeek to classify by filename (cheap fallback).

        Returns:
            Dict with keys:
                required        (list[str])   — КСС / bill-of-quantities files
                useful          (list[str])   — tech specs, contracts, etc.
                situation       (list[str])   — site plan / трасировъчен план filenames
                situation_paths (list[str])   — full absolute paths to situation files
                unknown         (list[str])   — unrecognised files
                can_proceed     (bool)        — True if at least one required file exists
                ai_used         (bool)        — True if AI fallback was triggered
        """
        files = self._list_supported_files()

        required: list[str] = []
        useful: list[str] = []
        situation: list[str] = []
        situation_paths: list[str] = []
        unknown: list[str] = []

        for fp in files:
            name = fp.name
            lower = name.lower()
            if any(kw in lower for kw in self._SITUATION_KEYWORDS):
                situation.append(name)
                situation_paths.append(str(fp))
            elif any(kw in lower for kw in self._REQUIRED_KEYWORDS):
                required.append(name)
            elif any(kw in lower for kw in self._USEFUL_KEYWORDS):
                useful.append(name)
            else:
                unknown.append(name)

        # If keyword match already found a required file — done.
        if required:
            return {
                "required": required,
                "useful": useful,
                "situation": situation,
                "situation_paths": situation_paths,
                "unknown": unknown,
                "can_proceed": True,
                "ai_used": False,
            }

        # Fallback: ask AI to classify by filename only.
        if ai_processor is not None and hasattr(ai_processor, "router") and ai_processor.router:
            try:
                names = [fp.name for fp in files]
                file_list = "\n".join(f"- {n}" for n in names)
                messages = [{
                    "role": "user",
                    "content": (
                        "Класифицирай следните файлове от строителен проект. "
                        "За всеки файл посочи категорията му:\n"
                        "  required  — КСС / количествено-стойностна сметка (задължителен)\n"
                        "  useful    — техническа спецификация, договор, проект, задание\n"
                        "  situation — ситуация / трасировъчен план / генерален план (чертеж)\n"
                        "  unknown   — всичко останало\n\n"
                        f"Файлове:\n{file_list}\n\n"
                        "Отговори само с валиден JSON:\n"
                        '{"required": [...], "useful": [...], "situation": [...], "unknown": [...]}'
                    ),
                }]
                file_class_prompt = (
                    "Ти си асистент за класификация на строителни документи. "
                    "Задачата ти е да разпознаеш ролята на всеки файл в тендерна "
                    "документация за ВиК инфраструктурен проект. "
                    "Отговаряй САМО с валиден JSON — без обяснения, без markdown."
                )
                result = ai_processor.router.chat(messages, file_class_prompt)
                classified = ai_processor.router.parse_json_response(result.get("content", "{}"))
                ai_required = classified.get("required", [])
                ai_useful = classified.get("useful", [])
                ai_situation = classified.get("situation", [])
                ai_unknown = classified.get("unknown", [])
                # Resolve full paths for AI-detected situation files
                name_to_path = {fp.name: str(fp) for fp in files}
                ai_situation_paths = [name_to_path[n] for n in ai_situation if n in name_to_path]
                return {
                    "required": ai_required,
                    "useful": ai_useful,
                    "situation": ai_situation,
                    "situation_paths": ai_situation_paths,
                    "unknown": ai_unknown,
                    "can_proceed": len(ai_required) > 0,
                    "ai_used": True,
                }
            except Exception:
                logger.warning("AI file classification failed, proceeding with unknown classification.")

        # No AI available and no keyword match — cannot determine required files.
        return {
            "required": [],
            "useful": useful,
            "situation": situation,
            "situation_paths": situation_paths,
            "unknown": unknown + required,  # required is empty here, unknown gets everything
            "can_proceed": False,
            "ai_used": False,
        }

    # ------------------------------------------------------------------
    # Batch conversion
    # ------------------------------------------------------------------

    def convert_all(
        self,
        ai_processor: Any | None = None,
        progress_callback: Callable[[int, int, str, str], None] | None = None,
        force: bool = False,
    ) -> dict:
        """Convert all pending/changed files.

        Args:
            ai_processor: Optional AIProcessor for OCR on scanned PDFs.
            progress_callback: Called with (current, total, filename, status_emoji).
            force: If True, re-convert ALL files regardless of cache.

        Returns:
            Dict with converted, skipped, failed counts and errors list.
        """
        supported = self._list_supported_files()
        status = self.get_conversion_status()
        file_statuses = {d["name"]: d["status"] for d in status["files"]}

        converted = 0
        skipped = 0
        failed = 0
        errors: list[str] = []
        results: list[dict] = []

        for i, fp in enumerate(supported):
            fname = fp.name
            needs_work = force or file_statuses.get(fname) != "converted"

            if not needs_work:
                skipped += 1
                if progress_callback:
                    progress_callback(i + 1, len(supported), fname, "skip")
                results.append({"file": fname, "action": "skipped"})
                continue

            if progress_callback:
                progress_callback(i + 1, len(supported), fname, "working")

            try:
                result = self.convert_single_file(str(fp), ai_processor)
                if result["status"] == "ok":
                    converted += 1
                    results.append({
                        "file": fname,
                        "action": "converted",
                        "method": result.get("method", ""),
                        "detail": result.get("detail", ""),
                    })
                    if progress_callback:
                        progress_callback(i + 1, len(supported), fname, "done")
                else:
                    failed += 1
                    errors.append(f"{fname}: {result.get('error', 'unknown')}")
                    results.append({"file": fname, "action": "failed", "error": result.get("error")})
                    if progress_callback:
                        progress_callback(i + 1, len(supported), fname, "error")
            except Exception as exc:
                failed += 1
                errors.append(f"{fname}: {exc}")
                results.append({"file": fname, "action": "failed", "error": str(exc)})
                logger.exception("Conversion failed for %s", fname)
                if progress_callback:
                    progress_callback(i + 1, len(supported), fname, "error")

        return {
            "converted": converted,
            "skipped": skipped,
            "failed": failed,
            "errors": errors,
            "results": results,
        }

    # ------------------------------------------------------------------
    # Single file conversion
    # ------------------------------------------------------------------

    def convert_single_file(
        self, filepath: str, ai_processor: Any | None = None
    ) -> dict:
        """Convert a single file based on its extension.

        Args:
            filepath: Absolute path to the file.
            ai_processor: Optional AIProcessor for OCR.

        Returns:
            Dict with status, output_file, method, detail.
        """
        fp = Path(filepath)
        ext = fp.suffix.lower()

        converters = {
            ".pdf": self._convert_pdf,
            ".xlsx": self._convert_excel,
            ".xls": self._convert_excel,
            ".docx": self._convert_docx,
            ".csv": self._convert_csv,
            ".json": self._copy_json_txt,
            ".txt": self._copy_json_txt,
        }

        converter = converters.get(ext)
        if not converter:
            return {"status": "error", "error": f"Unsupported extension: {ext}"}

        try:
            if ext == ".pdf":
                result = converter(str(fp), ai_processor)
            else:
                result = converter(str(fp))
        except Exception as exc:
            logger.exception("Converter error for %s", fp.name)
            return {"status": "error", "error": str(exc)}

        if result.get("status") != "ok":
            return result

        # Write converted JSON
        assert self.base_path is not None
        converted_dir = self.base_path / "converted"
        converted_dir.mkdir(exist_ok=True)
        out_name = fp.stem + ".json"
        out_path = converted_dir / out_name

        out_path.write_text(
            json.dumps(result["data"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Update manifest entry
        stat = fp.stat()
        mtime = (
            datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            .replace(tzinfo=None)
            .isoformat(timespec="seconds")
        )
        self._manifest.setdefault("files", {})[fp.name] = {
            "original_size": stat.st_size,
            "original_modified": mtime,
            "converted_file": f"converted/{out_name}",
            "converted_size": out_path.stat().st_size,
            "conversion_method": result.get("method", ""),
            "conversion_date": _now_iso(),
            "status": "ok",
            "pages_or_rows": result.get("pages_or_rows", 0),
        }
        self._save_manifest()

        logger.info(
            "Converted %s -> %s (%s)", fp.name, out_name, result.get("method")
        )

        return {
            "status": "ok",
            "output_file": str(out_path),
            "method": result.get("method", ""),
            "detail": result.get("detail", ""),
        }

    # ------------------------------------------------------------------
    # PDF conversion
    # ------------------------------------------------------------------

    def _convert_pdf(
        self, filepath: str, ai_processor: Any | None = None
    ) -> dict:
        """Convert a PDF file to structured JSON.

        Strategy (optimized for speed and cost):
        1. Extract text with PyMuPDF/fitz (best quality, local, free)
        2. If good text (>=50 avg chars/page) -> use directly
        3. If partial text (10-49 avg chars/page) -> send to DeepSeek
           for reformatting (cheap text task, no vision needed)
        4. If no text (<10 avg chars/page) -> truly scanned -> OCR via
           DeepSeek vision (last resort, expensive)
        """
        import fitz  # PyMuPDF — much better than PyPDF2

        doc = fitz.open(filepath)
        pages_text: list[dict] = []
        total_chars = 0

        for i, page in enumerate(doc):
            text = page.get_text().strip()
            total_chars += len(text)
            pages_text.append({"page": i + 1, "text": text})

        num_pages = len(doc) or 1
        avg_chars = total_chars / num_pages
        doc.close()

        source_name = Path(filepath).name

        # --- GOOD TEXT (>=50 chars/page avg) — use directly ---
        if avg_chars >= _MIN_CHARS_PER_PAGE:
            full_text = "\n\n".join(p["text"] for p in pages_text if p["text"])
            data = {
                "source_file": source_name,
                "type": "pdf",
                "extraction_method": "fitz_text",
                "pages": num_pages,
                "content": pages_text,
                "full_text": full_text,
            }
            return {
                "status": "ok",
                "data": data,
                "method": "fitz_text",
                "detail": f"{num_pages} стр., {avg_chars:.0f} симв/стр",
                "pages_or_rows": num_pages,
            }

        # --- PARTIAL TEXT (10-49 chars/page) — reformat via DeepSeek ---
        if avg_chars >= 10:
            raw_text = "\n\n".join(p["text"] for p in pages_text if p["text"])

            if ai_processor is not None and hasattr(ai_processor, "reformat_text"):
                try:
                    reformatted = ai_processor.reformat_text(raw_text, source_name)
                    if reformatted.get("status") == "ok":
                        data = {
                            "source_file": source_name,
                            "type": "pdf",
                            "extraction_method": "fitz_reformat",
                            "pages": num_pages,
                            "content": pages_text,
                            "full_text": reformatted["text"],
                        }
                        return {
                            "status": "ok",
                            "data": data,
                            "method": "fitz_reformat",
                            "detail": f"{num_pages} стр., преформатиран (DeepSeek)",
                            "pages_or_rows": num_pages,
                        }
                except Exception as exc:
                    logger.warning("Reformat failed for %s: %s", source_name, exc)

            # Reformat failed or no API — save raw partial text
            data = {
                "source_file": source_name,
                "type": "pdf",
                "extraction_method": "fitz_partial",
                "pages": num_pages,
                "content": pages_text,
                "full_text": raw_text,
            }
            return {
                "status": "ok",
                "data": data,
                "method": "fitz_partial",
                "detail": f"{num_pages} стр., частичен текст ({avg_chars:.0f} симв/стр)",
                "pages_or_rows": num_pages,
            }

        # --- NO TEXT (<10 chars/page) — truly scanned, try OCR ---
        if ai_processor is not None and hasattr(ai_processor, "ocr_pdf"):
            try:
                ocr_result = ai_processor.ocr_pdf(filepath)
                if ocr_result.get("status") == "ok":
                    return {
                        "status": "ok",
                        "data": ocr_result["data"],
                        "method": "ocr_vision",
                        "detail": f"OCR {num_pages} стр. (DeepSeek)",
                        "pages_or_rows": num_pages,
                    }
                else:
                    return {
                        "status": "error",
                        "error": ocr_result.get("error", "OCR failed"),
                    }
            except Exception as exc:
                logger.exception("OCR failed for %s", source_name)
                return {"status": "error", "error": f"OCR error: {exc}"}

        # No API available — save empty placeholder
        data = {
            "source_file": source_name,
            "type": "pdf",
            "extraction_method": "no_text",
            "pages": num_pages,
            "content": [],
            "full_text": "",
        }
        return {
            "status": "ok",
            "data": data,
            "method": "no_text",
            "detail": f"Сканиран, {num_pages} стр. (нужен API за OCR)",
            "pages_or_rows": num_pages,
        }

    # ------------------------------------------------------------------
    # Excel conversion
    # ------------------------------------------------------------------

    def _convert_excel(self, filepath: str) -> dict:
        """Convert an Excel file (.xlsx/.xls) to structured JSON.

        Handles merged cells by propagating the merged value.
        """
        from openpyxl import load_workbook

        wb = load_workbook(filepath, data_only=True)
        sheets: list[dict] = []
        total_rows = 0

        for ws in wb.worksheets:
            # Build a map of merged cells -> top-left value
            merged_vals: dict[tuple[int, int], Any] = {}
            for merge_range in ws.merged_cells.ranges:
                top_left_val = ws.cell(
                    merge_range.min_row, merge_range.min_col
                ).value
                for row in range(merge_range.min_row, merge_range.max_row + 1):
                    for col in range(merge_range.min_col, merge_range.max_col + 1):
                        merged_vals[(row, col)] = top_left_val

            def _cell_value(row: int, col: int) -> Any:
                if (row, col) in merged_vals:
                    return merged_vals[(row, col)]
                return ws.cell(row, col).value

            # Detect header row (first row with at least 2 non-empty cells)
            header_row_idx = 1
            for r in range(1, min(ws.max_row or 1, 20) + 1):
                vals = [_cell_value(r, c) for c in range(1, (ws.max_column or 1) + 1)]
                non_empty = sum(1 for v in vals if v is not None and str(v).strip())
                if non_empty >= 2:
                    header_row_idx = r
                    break

            headers = [
                str(_cell_value(header_row_idx, c) or f"Col{c}").strip()
                for c in range(1, (ws.max_column or 1) + 1)
            ]

            rows: list[dict] = []
            for r in range(header_row_idx + 1, (ws.max_row or 0) + 1):
                row_data: dict[str, Any] = {}
                all_empty = True
                for ci, h in enumerate(headers, start=1):
                    val = _cell_value(r, ci)
                    if val is not None:
                        all_empty = False
                    row_data[h] = _serialize_value(val)
                if not all_empty:
                    rows.append(row_data)

            total_rows += len(rows)
            sheets.append({
                "name": ws.title,
                "headers": headers,
                "rows": rows,
                "row_count": len(rows),
            })

        wb.close()

        data = {
            "source_file": Path(filepath).name,
            "type": "excel",
            "sheets": sheets,
        }

        sheet_summary = ", ".join(
            f"{s['name']}({s['row_count']})" for s in sheets
        )
        return {
            "status": "ok",
            "data": data,
            "method": "openpyxl",
            "detail": f"{len(sheets)} листа, {total_rows} реда",
            "pages_or_rows": total_rows,
        }

    # ------------------------------------------------------------------
    # DOCX conversion
    # ------------------------------------------------------------------

    def _convert_docx(self, filepath: str) -> dict:
        """Convert a DOCX file to structured JSON."""
        from docx import Document

        doc = Document(filepath)

        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]

        tables: list[dict] = []
        for table in doc.tables:
            rows_data: list[list[str]] = []
            for row in table.rows:
                rows_data.append([cell.text.strip() for cell in row.cells])

            if rows_data:
                headers = rows_data[0]
                data_rows = [
                    dict(zip(headers, r)) for r in rows_data[1:]
                ]
                tables.append({"headers": headers, "rows": data_rows})

        full_text = "\n".join(paragraphs)

        data = {
            "source_file": Path(filepath).name,
            "type": "docx",
            "paragraphs": paragraphs,
            "tables": tables,
            "full_text": full_text,
        }
        return {
            "status": "ok",
            "data": data,
            "method": "python-docx",
            "detail": f"{len(paragraphs)} параграфа, {len(tables)} таблици",
            "pages_or_rows": len(paragraphs),
        }

    # ------------------------------------------------------------------
    # CSV conversion
    # ------------------------------------------------------------------

    def _convert_csv(self, filepath: str) -> dict:
        """Convert a CSV file to JSON, auto-detecting delimiter and encoding."""
        raw = Path(filepath).read_bytes()

        # Try encodings in order of likelihood
        content: str | None = None
        used_encoding = ""
        for enc in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
            try:
                content = raw.decode(enc)
                used_encoding = enc
                break
            except (UnicodeDecodeError, ValueError):
                continue

        if content is None:
            return {"status": "error", "error": "Cannot detect file encoding."}

        # Detect delimiter
        sniffer = csv.Sniffer()
        try:
            sample = content[:4096]
            dialect = sniffer.sniff(sample, delimiters=",;\t|")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = "," if "," in content[:1000] else ";"

        reader = csv.reader(io.StringIO(content), delimiter=delimiter)
        all_rows = list(reader)

        if not all_rows:
            return {"status": "error", "error": "Empty CSV file."}

        headers = [h.strip() for h in all_rows[0]]
        rows = [dict(zip(headers, r)) for r in all_rows[1:] if any(c.strip() for c in r)]

        data = {
            "source_file": Path(filepath).name,
            "type": "csv",
            "encoding": used_encoding,
            "delimiter": repr(delimiter),
            "sheets": [{
                "name": "Sheet1",
                "headers": headers,
                "rows": rows,
                "row_count": len(rows),
            }],
        }
        return {
            "status": "ok",
            "data": data,
            "method": "csv",
            "detail": f"{len(rows)} реда ({used_encoding})",
            "pages_or_rows": len(rows),
        }

    # ------------------------------------------------------------------
    # JSON / TXT passthrough
    # ------------------------------------------------------------------

    def _copy_json_txt(self, filepath: str) -> dict:
        """Copy JSON (validated) or TXT files into the converted format."""
        fp = Path(filepath)
        raw = fp.read_bytes()

        # Try encodings
        content: str | None = None
        for enc in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
            try:
                content = raw.decode(enc)
                break
            except (UnicodeDecodeError, ValueError):
                continue

        if content is None:
            return {"status": "error", "error": "Cannot decode file."}

        if fp.suffix.lower() == ".json":
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as exc:
                return {"status": "error", "error": f"Invalid JSON: {exc}"}
            data = {
                "source_file": fp.name,
                "type": "json",
                "content": parsed,
            }
            method = "json_copy"
            detail = "JSON валидиран"
        else:
            data = {
                "source_file": fp.name,
                "type": "txt",
                "content": content,
                "full_text": content,
            }
            method = "txt_copy"
            detail = f"{len(content)} символа"

        return {
            "status": "ok",
            "data": data,
            "method": method,
            "detail": detail,
            "pages_or_rows": content.count("\n") + 1,
        }

    # ------------------------------------------------------------------
    # Reading converted files
    # ------------------------------------------------------------------

    def get_converted_files(self) -> list[dict]:
        """List all successfully converted files.

        Returns:
            List of dicts with original, converted, type, method, size.
        """
        manifest_files: dict = self._manifest.get("files", {})
        result = []
        for name, entry in manifest_files.items():
            if entry.get("status") != "ok":
                continue
            result.append({
                "original": name,
                "converted": entry.get("converted_file", ""),
                "type": Path(name).suffix.lower(),
                "method": entry.get("conversion_method", ""),
                "size": entry.get("converted_size", 0),
            })
        return result

    def read_converted(self, filename: str) -> dict:
        """Read a converted JSON file by original filename.

        Args:
            filename: Original filename (e.g. 'КСС.xlsx').

        Returns:
            Parsed JSON dict.

        Raises:
            FileNotFoundError: If the converted file doesn't exist.
        """
        assert self.base_path is not None
        out_name = Path(filename).stem + ".json"
        converted_path = self.base_path / "converted" / out_name

        if not converted_path.exists():
            raise FileNotFoundError(
                f"Converted file not found: {converted_path}"
            )

        return json.loads(converted_path.read_text(encoding="utf-8"))

    def get_all_text(self) -> str:
        """Combine text from ALL converted files into one large string.

        Useful for sending to AI for analysis.

        Returns:
            Combined text from all converted documents.
        """
        assert self.base_path is not None
        converted_dir = self.base_path / "converted"
        if not converted_dir.exists():
            return ""

        parts: list[str] = []
        for jf in sorted(converted_dir.glob("*.json")):
            if jf.name == "_manifest.json":
                continue
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            source = data.get("source_file", jf.stem)
            parts.append(f"=== {source} ===")

            if "full_text" in data and data["full_text"]:
                parts.append(data["full_text"])
            elif "sheets" in data:
                for sheet in data["sheets"]:
                    parts.append(f"--- {sheet.get('name', 'Sheet')} ---")
                    for row in sheet.get("rows", []):
                        parts.append(
                            " | ".join(str(v) for v in row.values())
                        )
            elif "content" in data:
                if isinstance(data["content"], str):
                    parts.append(data["content"])
                elif isinstance(data["content"], list):
                    for item in data["content"]:
                        if isinstance(item, dict) and "text" in item:
                            parts.append(item["text"])

            parts.append("")

        return "\n".join(parts)


# ------------------------------------------------------------------
# Utilities
# ------------------------------------------------------------------

def _now_iso() -> str:
    """Return current UTC time as ISO string (no timezone)."""
    return datetime.now(tz=timezone.utc).replace(tzinfo=None).isoformat(
        timespec="seconds"
    )


def _serialize_value(val: Any) -> Any:
    """Make a cell value JSON-serializable."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, (int, float, bool)):
        return val
    return str(val)
