"""Unit tests for AI transparency marking — EU AI Act чл. 50.

Covers: машинно четимите маркери, единният източник на текстовете, и
        реалното им присъствие в трите изхода — PDF (метаданни + видим ред),
        MSPDI XML (полета, оцеляващи в .mpp) и JSON данните на графика.

FAILURE означава: src/ai_disclosure.py или закачането му в експортите е
счупено — генерираните графици излизат при възложителя без обозначение, че
са произведени от AI система.  Чл. 50(1) се прилага от 2 август 2026;
чл. 50(2) — с гратисен период до 2 декември 2026 за системи, пуснати на
пазара преди това.

Тестовете проверяват МЕХАНИЗМА (маркерът съществува, машинно четим е и е
видим), не правната достатъчност на текстовете — тя се преценява от юрист.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai_disclosure import (  # noqa: E402
    CHAT_DISCLOSURE_BG,
    CHAT_DISCLOSURE_EN,
    CONTENT_DISCLOSURE_BG,
    CONTENT_NOTICE_LONG_BG,
    SYSTEM_NAME,
    machine_readable_marker,
    pdf_metadata_keywords,
    stamp_schedule,
)
from src.export_xml import export_to_mspdi_xml  # noqa: E402

SCHEDULE = [
    {"id": "В01", "name": "Полагане DN110 PE", "start_day": 1, "end_day": 30,
     "duration": 30, "type": "water_pipe", "dependencies": []},
    {"id": "В02", "name": "Хидравлично изпитване", "start_day": 31, "end_day": 32,
     "duration": 2, "type": "test", "dependencies": ["В01"]},
]


# ===================================================================
# machine_readable_marker
# ===================================================================

def test_marker_declares_ai_generated():
    marker = machine_readable_marker()
    assert marker["ai_generated"] is True
    assert marker["requires_human_review"] is True


def test_marker_names_the_system():
    assert machine_readable_marker()["ai_system"] == SYSTEM_NAME


def test_marker_timestamp_is_iso():
    stamp = machine_readable_marker(datetime(2026, 8, 2, 9, 30, 0))["generated_at"]
    assert stamp == "2026-08-02T09:30:00"


def test_marker_is_json_serialisable():
    import json
    assert json.loads(json.dumps(machine_readable_marker()))["ai_generated"] is True


def test_marker_carries_both_languages():
    marker = machine_readable_marker()
    assert marker["ai_disclosure"]
    assert marker["ai_disclosure_bg"]


# ===================================================================
# stamp_schedule
# ===================================================================

def test_stamp_adds_marker():
    stamped = stamp_schedule({"tasks": []})
    assert stamped["_ai_disclosure"]["ai_generated"] is True


def test_stamp_preserves_original_data():
    stamped = stamp_schedule({"tasks": [1, 2], "total_duration": 30})
    assert stamped["tasks"] == [1, 2]
    assert stamped["total_duration"] == 30


def test_stamp_does_not_mutate_input():
    original = {"tasks": []}
    stamp_schedule(original)
    assert "_ai_disclosure" not in original


def test_stamp_handles_none():
    assert stamp_schedule(None)["_ai_disclosure"]["ai_generated"] is True


# ===================================================================
# Текстове — единен източник
# ===================================================================

def test_chat_disclosure_states_it_is_ai():
    assert "AI" in CHAT_DISCLOSURE_BG
    assert "AI" in CHAT_DISCLOSURE_EN


def test_content_disclosure_mentions_review():
    """Разкриването трябва да казва и че е нужна човешка проверка."""
    assert "проверка" in CONTENT_DISCLOSURE_BG


def test_long_notice_names_the_system():
    assert SYSTEM_NAME in CONTENT_NOTICE_LONG_BG


def test_pdf_keywords_are_machine_parseable():
    keywords = pdf_metadata_keywords(datetime(2026, 8, 2, 9, 0, 0))
    pairs = dict(
        part.strip().split("=", 1) for part in keywords.split(";") if "=" in part
    )
    assert pairs["ai-generated"] == "true"
    assert pairs["requires-human-review"] == "true"
    assert pairs["generated-at"] == "2026-08-02T09:00:00"


# ===================================================================
# MSPDI XML — маркерът пътува с файла
# ===================================================================

@pytest.fixture()
def xml_root() -> ET.Element:
    xml_bytes = export_to_mspdi_xml(SCHEDULE, "Тестов проект", "2026-08-03")
    if isinstance(xml_bytes, bytes):
        return ET.fromstring(xml_bytes)
    return ET.fromstring(str(xml_bytes))


def _text(root: ET.Element, tag: str) -> str:
    ns = "{http://schemas.microsoft.com/project}"
    node = root.find(f"{ns}{tag}")
    if node is None:
        node = root.find(tag)
    return node.text if node is not None and node.text else ""


def test_xml_has_ai_author(xml_root):
    assert _text(xml_root, "Author") == SYSTEM_NAME


def test_xml_subject_carries_disclosure(xml_root):
    assert _text(xml_root, "Subject") == CONTENT_DISCLOSURE_BG


def test_xml_comments_carry_long_notice(xml_root):
    assert SYSTEM_NAME in _text(xml_root, "Comments")


def test_xml_category_is_machine_readable(xml_root):
    assert _text(xml_root, "Category") == "ai-generated"


def test_xml_still_has_critical_duration_format(xml_root):
    """Урок #19: добавянето на метаданни не бива да чупи DurationFormat=5."""
    assert _text(xml_root, "DurationFormat") == "5"


def test_xml_still_lists_the_tasks(xml_root):
    ns = "{http://schemas.microsoft.com/project}"
    tasks = xml_root.find(f"{ns}Tasks")
    if tasks is None:
        tasks = xml_root.find("Tasks")
    assert tasks is not None
    assert len(list(tasks)) >= len(SCHEDULE)


# ===================================================================
# PDF — метаданни + видим ред
# ===================================================================

def test_pdf_contains_disclosure_metadata(tmp_path):
    fitz = pytest.importorskip("fitz")
    from src.export_pdf import export_to_pdf

    out = tmp_path / "graph.pdf"
    export_to_pdf(
        SCHEDULE, "Тестов проект", {}, start_date="2026-08-03", filename=str(out)
    )

    doc = fitz.open(str(out))
    try:
        meta = doc.metadata
        assert SYSTEM_NAME in (meta.get("creator") or "")
        assert "ai-generated=true" in (meta.get("keywords") or "")
    finally:
        doc.close()


def test_pdf_shows_disclosure_on_the_page(tmp_path):
    """Получателят трябва да го види, без да рови в метаданните."""
    fitz = pytest.importorskip("fitz")
    from src.export_pdf import export_to_pdf

    out = tmp_path / "graph.pdf"
    export_to_pdf(
        SCHEDULE, "Тестов проект", {}, start_date="2026-08-03", filename=str(out)
    )

    doc = fitz.open(str(out))
    try:
        text = doc[0].get_text()
    finally:
        doc.close()

    assert "изкуствен интелект" in text.lower()
