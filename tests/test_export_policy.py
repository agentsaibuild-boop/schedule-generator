"""Unit tests: export gate различава ВАЛИДЕН от ГОТОВ ЗА ВЪЗЛОЖИТЕЛ.

Одит 2026-07-24, точки 3 и 4:
- `unresolved` продължителности (кодът не може да ги докаже) минаваха за
  експортируеми — provenance беше информация, не контрол;
- `needs_human_review` (AI сигнализира липсваща дейност) също беше
  експортируем — детерминистично валиден се приравняваше на готов.

Въведени са три политики (EXPORT_POLICY): strict / provisional / lenient.
Детерминистично валиден е ПРЕДПОСТАВКА за всички — невалиден график не се
експортира при никоя.

FAILURE означава: непотвърден или недоказан график пак може да излезе като
официален XML/PDF при възложителя.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai_processor import AIProcessor  # noqa: E402

VALID = {"valid": True, "checked": True, "task_count": 5, "errors": [], "warnings": []}
INVALID = {"valid": False, "checked": True, "errors": ["цикъл"], "warnings": []}


def _dur(unresolved: int = 0) -> dict:
    return {"summary": {"unresolved": unresolved}}


def _decide(status, validation, policy, unresolved=0, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setenv("EXPORT_POLICY", policy)
    return AIProcessor._export_decision(status, validation, {}, _dur(unresolved))


# ===================================================================
# Невалиден — блокиран винаги
# ===================================================================

@pytest.mark.parametrize("policy", ["strict", "provisional", "lenient"])
def test_invalid_never_exports(policy, monkeypatch):
    assert _decide("invalid", INVALID, policy, monkeypatch=monkeypatch)["exportable"] is False


def test_invalid_blocker_is_explained(monkeypatch):
    result = _decide("invalid", INVALID, "lenient", monkeypatch=monkeypatch)
    assert any("детерминистичната проверка" in b for b in result["blockers"])


# ===================================================================
# strict — само чист график
# ===================================================================

def test_strict_clean_schedule_exports(monkeypatch):
    assert _decide("approved", VALID, "strict", monkeypatch=monkeypatch)["exportable"] is True


def test_strict_blocks_unresolved(monkeypatch):
    result = _decide("approved", VALID, "strict", unresolved=3, monkeypatch=monkeypatch)
    assert result["exportable"] is False
    assert any("не са доказани" in b for b in result["blockers"])


def test_strict_blocks_needs_human_review(monkeypatch):
    result = _decide("needs_human_review", VALID, "strict", monkeypatch=monkeypatch)
    assert result["exportable"] is False


# ===================================================================
# provisional (по подразбиране)
# ===================================================================

def test_provisional_is_the_default(monkeypatch):
    monkeypatch.delenv("EXPORT_POLICY", raising=False)
    result = AIProcessor._export_decision("approved", VALID, {}, _dur(0))
    assert result["policy"] == "provisional"


def test_provisional_allows_unresolved_with_warning(monkeypatch):
    result = _decide("approved", VALID, "provisional", unresolved=3, monkeypatch=monkeypatch)
    assert result["exportable"] is True
    assert result["blockers"]           # но с предупреждение


def test_provisional_blocks_needs_human_review(monkeypatch):
    """Човешка нужда е по-силна от липса на доказан произход."""
    result = _decide("needs_human_review", VALID, "provisional", monkeypatch=monkeypatch)
    assert result["exportable"] is False


def test_provisional_clean_exports_without_warnings(monkeypatch):
    result = _decide("approved", VALID, "provisional", monkeypatch=monkeypatch)
    assert result["exportable"] is True
    assert result["blockers"] == []


# ===================================================================
# lenient — старото поведение
# ===================================================================

def test_lenient_exports_needs_human_review(monkeypatch):
    result = _decide("needs_human_review", VALID, "lenient", monkeypatch=monkeypatch)
    assert result["exportable"] is True


def test_lenient_exports_unresolved(monkeypatch):
    result = _decide("approved", VALID, "lenient", unresolved=5, monkeypatch=monkeypatch)
    assert result["exportable"] is True


# ===================================================================
# Устойчивост
# ===================================================================

def test_unknown_policy_falls_back_to_provisional(monkeypatch):
    result = _decide("needs_human_review", VALID, "глупост", monkeypatch=monkeypatch)
    assert result["policy"] == "provisional"
    assert result["exportable"] is False


def test_blockers_are_always_reported_even_when_exportable(monkeypatch):
    """provisional пуска, но пак казва защо графикът е предварителен."""
    result = _decide("approved", VALID, "provisional", unresolved=2, monkeypatch=monkeypatch)
    assert result["exportable"] is True
    assert any("не са доказани" in b for b in result["blockers"])


# ===================================================================
# Интеграция
# ===================================================================

def test_pipeline_exposes_export_decision():
    source = (Path(__file__).parent.parent / "src" / "ai_processor.py").read_text(
        encoding="utf-8")
    assert '"exportable": export["exportable"]' in source
    assert '"export_blockers"' in source


def test_app_reads_export_decision_not_just_validity():
    source = (Path(__file__).parent.parent / "app.py").read_text(encoding="utf-8")
    assert "last_export" in source
    assert 'not _export["exportable"]' in source


def test_chat_handler_propagates_export():
    source = (Path(__file__).parent.parent / "src" / "chat_handler.py").read_text(
        encoding="utf-8")
    assert '"export"' in source
