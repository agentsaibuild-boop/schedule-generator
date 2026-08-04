"""Unit tests for per-page PDF classification and selective OCR (P1).

Covers: _image_coverage, _classify_pdf_pages, и решенията на _convert_pdf —
        сканирани страници отиват на OCR, рядък текст отива на reformat,
        смесен документ ползва хибриден път, а без API нищо не се преструва
        на прочетено.

FAILURE означава: src/file_manager.py :: класификацията на страници е счупена.
Последици: сканирани чертежи минават като празни (тиха загуба на данни), или
сканирана страница с тънък текстов слой отива на текстов reformat, където
vision не се вика и AI-ят преформатира боклук в убедителен боклук (P1).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

fitz = pytest.importorskip("fitz")

from src.file_manager import (  # noqa: E402
    _MIN_CHARS_PER_PAGE,
    _SCANNED_IMAGE_COVERAGE,
    _THIN_TEXT_CHARS,
    FileManager,
)

LOREM = (
    "Количествено-стойностна сметка за обект водопровод и канализация "
    "по улица Витоша, участък от ОТ 12 до ОТ 18, DN110 PE, дължина 420 метра."
)


# ---------------------------------------------------------------------------
# Helpers — построяваме реални PDF-и в паметта
# ---------------------------------------------------------------------------

def _png_bytes(width: int = 40, height: int = 40) -> bytes:
    """Малко PNG изображение (плътен правоъгълник)."""
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height))
    pix.set_rect(pix.irect, (200, 200, 200))
    return pix.tobytes("png")


# Вграденият 'helv' не рендира кирилица (излиза като '·'), а документите на
# този проект са на български — ползваме DejaVu, който и без това е в repo-то
# заради PDF експорта (урок #21).
_FONT_PATH = Path(__file__).parent.parent / "fonts" / "DejaVuSans.ttf"


def _make_pdf(tmp_path: Path, pages: list[dict], name: str = "test.pdf") -> str:
    """Построй PDF по спецификация.

    Всяка страница: {"text": str|None, "image": bool}
    `image=True` слага изображение върху ЦЯЛАТА страница (симулира скан).
    """
    doc = fitz.open()
    img = _png_bytes()
    has_font = _FONT_PATH.exists()

    for spec in pages:
        page = doc.new_page()
        if spec.get("image"):
            page.insert_image(page.rect, stream=img)
        text = spec.get("text")
        if text:
            if has_font:
                page.insert_text(
                    (50, 50), text, fontsize=9,
                    fontname="DejaVu", fontfile=str(_FONT_PATH),
                )
            else:
                page.insert_text((50, 50), text, fontsize=9, fontname="helv")

    path = tmp_path / name
    doc.save(str(path))
    doc.close()
    return str(path)


def _classify(tmp_path: Path, pages: list[dict]) -> list[dict]:
    path = _make_pdf(tmp_path, pages)
    doc = fitz.open(path)
    try:
        return FileManager._classify_pdf_pages(doc)
    finally:
        doc.close()


def _kinds(tmp_path: Path, pages: list[dict]) -> list[str]:
    return [p["kind"] for p in _classify(tmp_path, pages)]


# ---------------------------------------------------------------------------
# _image_coverage
# ---------------------------------------------------------------------------

def test_image_coverage_empty_page_is_zero(tmp_path):
    path = _make_pdf(tmp_path, [{"text": LOREM}])
    doc = fitz.open(path)
    try:
        assert FileManager._image_coverage(doc[0]) == 0.0
    finally:
        doc.close()


def test_image_coverage_full_page_image_is_one(tmp_path):
    path = _make_pdf(tmp_path, [{"image": True}])
    doc = fitz.open(path)
    try:
        assert FileManager._image_coverage(doc[0]) == pytest.approx(1.0, abs=0.05)
    finally:
        doc.close()


def test_image_coverage_never_exceeds_one(tmp_path):
    """Няколко припокриващи се изображения не бива да дават >1.0."""
    doc = fitz.open()
    page = doc.new_page()
    img = _png_bytes()
    for _ in range(3):
        page.insert_image(page.rect, stream=img)
    path = tmp_path / "overlap.pdf"
    doc.save(str(path))
    doc.close()

    doc = fitz.open(str(path))
    try:
        assert FileManager._image_coverage(doc[0]) <= 1.0
    finally:
        doc.close()


def test_image_coverage_survives_broken_page():
    """Грешка при измерване → 0.0, т.е. падаме към класификация по знаци."""
    broken = MagicMock()
    type(broken).rect = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    assert FileManager._image_coverage(broken) == 0.0


# ---------------------------------------------------------------------------
# _classify_pdf_pages
# ---------------------------------------------------------------------------

def test_text_page_is_classified_as_text(tmp_path):
    assert _kinds(tmp_path, [{"text": LOREM}]) == ["text"]


def test_full_page_image_without_text_is_scanned(tmp_path):
    assert _kinds(tmp_path, [{"image": True}]) == ["scanned"]


def test_scanned_page_with_thin_text_layer_is_scanned(tmp_path):
    """P1 ядрото: скан с печат/колонтитул НЕ бива да минава за 'рядък текст'.

    Точно този случай преди отиваше на текстов reformat и vision никога не
    се извикваше.
    """
    thin = "Обр. 12а"  # ~8 знака, под прага за text
    assert len(thin) < _MIN_CHARS_PER_PAGE
    assert _kinds(tmp_path, [{"image": True, "text": thin}]) == ["scanned"]


def test_sparse_text_page_without_image_is_thin_not_scanned(tmp_path):
    """Титулна страница с малко текст и без картинка — OCR няма какво да добави."""
    sparse = "Приложение 3"
    assert _THIN_TEXT_CHARS <= len(sparse) < _MIN_CHARS_PER_PAGE
    assert _kinds(tmp_path, [{"text": sparse}]) == ["thin"]


def test_blank_page_is_empty(tmp_path):
    assert _kinds(tmp_path, [{}]) == ["empty"]


def test_searchable_scan_counts_as_text(tmp_path):
    """Скан с пълен текстов слой (вече OCR-нат) не се праща пак на vision."""
    assert _kinds(tmp_path, [{"image": True, "text": LOREM}]) == ["text"]


def test_each_page_judged_independently(tmp_path):
    """Ядрото на поправката: решението е за страница, не средно за документа."""
    kinds = _kinds(tmp_path, [
        {"text": LOREM},
        {"text": LOREM},
        {"image": True},
        {"text": LOREM},
    ])
    assert kinds == ["text", "text", "scanned", "text"]


def test_classification_reports_chars_and_coverage(tmp_path):
    pages = _classify(tmp_path, [{"image": True}])
    assert pages[0]["chars"] == 0
    assert pages[0]["image_coverage"] >= _SCANNED_IMAGE_COVERAGE
    assert pages[0]["page"] == 1


# ---------------------------------------------------------------------------
# _convert_pdf — маршрутизиране
# ---------------------------------------------------------------------------

def _processor_with_ocr(text: str = "ИЗВЛЕЧЕН ТЕКСТ ОТ СКАН") -> MagicMock:
    proc = MagicMock()
    proc.ocr_pdf.return_value = {
        "status": "ok",
        "data": {"content": [{"page": 3, "text": text}]},
    }
    return proc


def test_all_text_document_uses_fitz_directly(tmp_path):
    path = _make_pdf(tmp_path, [{"text": LOREM}, {"text": LOREM}])
    result = FileManager()._convert_pdf(path, ai_processor=None)

    assert result["method"] == "fitz_text"
    assert LOREM[:20] in result["data"]["full_text"]


def test_scanned_pages_trigger_ocr_on_those_pages_only(tmp_path):
    """27 текстови + 1 сканирана → OCR само на сканираната, не на целия документ."""
    pages = [{"text": LOREM}, {"text": LOREM}, {"image": True}, {"text": LOREM}]
    path = _make_pdf(tmp_path, pages)
    proc = _processor_with_ocr()

    result = FileManager()._convert_pdf(path, ai_processor=proc)

    proc.ocr_pdf.assert_called_once()
    assert proc.ocr_pdf.call_args.kwargs["pages"] == [2]   # 0-based индекс на стр. 3
    assert result["method"] == "fitz_ocr_hybrid"
    assert result["data"]["ocr_pages"] == [3]


def test_ocr_text_replaces_only_the_scanned_page(tmp_path):
    pages = [{"text": LOREM}, {"text": LOREM}, {"image": True}]
    path = _make_pdf(tmp_path, pages)

    result = FileManager()._convert_pdf(path, ai_processor=_processor_with_ocr())
    by_page = {p["page"]: p["text"] for p in result["data"]["content"]}

    assert "ИЗВЛЕЧЕН ТЕКСТ ОТ СКАН" in by_page[3]
    assert LOREM[:20] in by_page[1]
    assert "ИЗВЛЕЧЕН" not in by_page[1]


def test_fully_scanned_document_reports_ocr_vision(tmp_path):
    path = _make_pdf(tmp_path, [{"image": True}])
    proc = MagicMock()
    proc.ocr_pdf.return_value = {
        "status": "ok",
        "data": {"content": [{"page": 1, "text": "СКАНИРАН ТЕКСТ"}]},
    }

    result = FileManager()._convert_pdf(path, ai_processor=proc)

    assert result["method"] == "ocr_vision"


def test_scanned_page_never_goes_to_text_reformat(tmp_path):
    """Регресия за P1: reformat_text не бива да се вика за сканирана страница."""
    path = _make_pdf(tmp_path, [{"image": True, "text": "Обр. 12а"}])
    proc = _processor_with_ocr()
    proc.reformat_text.return_value = {"status": "ok", "text": "боклук"}

    FileManager()._convert_pdf(path, ai_processor=proc)

    proc.reformat_text.assert_not_called()
    proc.ocr_pdf.assert_called_once()


def test_thin_text_document_uses_reformat(tmp_path):
    path = _make_pdf(tmp_path, [{"text": "Приложение 3"}])
    proc = MagicMock(spec=["reformat_text"])
    proc.reformat_text.return_value = {"status": "ok", "text": "Приложение 3 — оправено"}

    result = FileManager()._convert_pdf(path, ai_processor=proc)

    proc.reformat_text.assert_called_once()
    assert result["method"] == "fitz_reformat"


def test_scanned_without_api_is_reported_not_silently_empty(tmp_path):
    """Без OCR не се преструваме, че документът е прочетен."""
    path = _make_pdf(tmp_path, [{"text": LOREM}, {"image": True}])

    result = FileManager()._convert_pdf(path, ai_processor=None)

    assert result["method"] == "no_text"
    assert result["data"]["scanned_pages"] == [2]
    assert "сканирани" in result["detail"]
    # Текстът от четимата страница не се губи.
    assert LOREM[:20] in result["data"]["full_text"]


def test_ocr_failure_falls_back_without_crashing(tmp_path):
    path = _make_pdf(tmp_path, [{"text": LOREM}, {"image": True}])
    proc = MagicMock()
    proc.ocr_pdf.return_value = {"status": "error", "error": "няма кредит"}

    result = FileManager()._convert_pdf(path, ai_processor=proc)

    assert result["status"] == "ok"
    assert result["method"] == "no_text"
    assert result["data"]["scanned_pages"] == [2]


def test_ocr_exception_falls_back_without_crashing(tmp_path):
    path = _make_pdf(tmp_path, [{"text": LOREM}, {"image": True}])
    proc = MagicMock()
    proc.ocr_pdf.side_effect = RuntimeError("boom")

    result = FileManager()._convert_pdf(path, ai_processor=proc)

    assert result["status"] == "ok"
    assert result["method"] == "no_text"


def test_legacy_processor_without_pages_param_still_works(tmp_path):
    """Стар ai_processor без `pages` — падаме към OCR на целия документ."""
    path = _make_pdf(tmp_path, [{"text": LOREM}, {"image": True}])

    calls: list[tuple] = []

    class LegacyProcessor:
        def ocr_pdf(self, filepath):          # noqa: D102 — без параметър pages
            calls.append((filepath,))
            return {"status": "ok", "data": {"content": [{"page": 2, "text": "СТАР OCR"}]}}

    result = FileManager()._convert_pdf(path, ai_processor=LegacyProcessor())

    assert len(calls) == 1
    assert "СТАР OCR" in result["data"]["full_text"]
