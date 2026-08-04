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


# Чист citation report по подразбиране — количествата са ДОКАЗАНИ.  Тестовете,
# които не са за произхода, ползват това, за да изолират аспекта си (одит v8:
# без него strict блокира заради fail-closed „непроверени количества").
_CLEAN_CITE = {"checked": True, "mismatch": 0, "unknown_ref": 0,
               "uncited": 0, "verified": 0}


def _decide(status, validation, policy, unresolved=0, monkeypatch=None,
            citation=None):
    if monkeypatch is not None:
        monkeypatch.setenv("EXPORT_POLICY", policy)
    return AIProcessor._export_decision(
        status, validation, {}, _dur(unresolved),
        _CLEAN_CITE if citation is None else citation)


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
# lenient — по-меко, но статусът пак е allowlist
# ===================================================================

def test_lenient_does_not_export_needs_human_review(monkeypatch):
    """Одит v6, точка 1: статусът е allowlist при ВСЯКА политика.
    needs_human_review е валиден, но чака човек — не се експортира дори
    при lenient (само `approved` минава)."""
    result = _decide("needs_human_review", VALID, "lenient", monkeypatch=monkeypatch)
    assert result["exportable"] is False


def test_lenient_exports_unresolved(monkeypatch):
    result = _decide("approved", VALID, "lenient", unresolved=5, monkeypatch=monkeypatch)
    assert result["exportable"] is True


# ===================================================================
# Одит v6, точка 1 — статусът е ALLOWLIST (само approved експортира)
# ===================================================================

@pytest.mark.parametrize("policy", ["strict", "provisional", "lenient"])
@pytest.mark.parametrize("status", ["error", "stopped", "parse_error", "какъвто"])
def test_failed_status_never_exports_even_if_valid(status, policy, monkeypatch):
    """Сринат/спрян контрольор → графикът НЕ е готов за възложител,
    колкото и структурно валиден да е."""
    result = _decide(status, VALID, policy, monkeypatch=monkeypatch)
    assert result["exportable"] is False
    assert result["blockers"]


@pytest.mark.parametrize("policy", ["strict", "provisional", "lenient"])
def test_only_approved_is_exportable(policy, monkeypatch):
    assert _decide("approved", VALID, policy, monkeypatch=monkeypatch)["exportable"] is True


def test_error_status_blocker_names_the_status(monkeypatch):
    result = _decide("error", VALID, "provisional", monkeypatch=monkeypatch)
    assert any("error" in b for b in result["blockers"])


# ===================================================================
# Одит v7, точка 4 — произходът на КОЛИЧЕСТВАТА влиза в strict gate
# ===================================================================

def _cite(mismatch=0, unknown_ref=0, uncited=0, verified=0, checked=True):
    return {"mismatch": mismatch, "unknown_ref": unknown_ref,
            "uncited": uncited, "verified": verified, "checked": checked}


def test_strict_blocks_quantity_mismatch(monkeypatch):
    monkeypatch.setenv("EXPORT_POLICY", "strict")
    result = AIProcessor._export_decision(
        "approved", VALID, {}, _dur(0), _cite(mismatch=1))
    assert result["exportable"] is False
    assert any("mismatch" in b or "разминава" in b for b in result["blockers"])


def test_strict_blocks_invented_citation(monkeypatch):
    monkeypatch.setenv("EXPORT_POLICY", "strict")
    result = AIProcessor._export_decision(
        "approved", VALID, {}, _dur(0), _cite(unknown_ref=2))
    assert result["exportable"] is False


def test_strict_blocks_uncited_quantities(monkeypatch):
    """Одит v8, точка 5: strict = ДОКАЗАНИ количества, значи и uncited блокира."""
    monkeypatch.setenv("EXPORT_POLICY", "strict")
    result = AIProcessor._export_decision(
        "approved", VALID, {}, _dur(0), _cite(uncited=2))
    assert result["exportable"] is False
    assert any("цитат" in b for b in result["blockers"])


def test_strict_blocks_when_boq_index_missing(monkeypatch):
    """Одит v8, точка 4: липсващ КСС индекс → fail-closed при strict."""
    monkeypatch.setenv("EXPORT_POLICY", "strict")
    result = AIProcessor._export_decision(
        "approved", VALID, {}, _dur(0), {"checked": False, "reason": "no_boq_index"})
    assert result["exportable"] is False
    assert any("не е проверен" in b for b in result["blockers"])


def test_strict_blocks_on_provenance_exception(monkeypatch):
    """Одит v8, точка 6: грешка в provenance → fail-closed при strict."""
    monkeypatch.setenv("EXPORT_POLICY", "strict")
    result = AIProcessor._export_decision(
        "approved", VALID, {}, _dur(0), {"checked": False, "reason": "exception"})
    assert result["exportable"] is False


def test_strict_allows_clean_citations(monkeypatch):
    monkeypatch.setenv("EXPORT_POLICY", "strict")
    result = AIProcessor._export_decision(
        "approved", VALID, {}, _dur(0), _cite(verified=5))
    assert result["exportable"] is True


def test_provisional_exports_despite_unproven_quantities(monkeypatch):
    """provisional остава по-меко: недоказани количества са предупреждение."""
    monkeypatch.setenv("EXPORT_POLICY", "provisional")
    result = AIProcessor._export_decision(
        "approved", VALID, {}, _dur(0), {"checked": False, "reason": "no_boq_index"})
    assert result["exportable"] is True
    assert result["blockers"]           # но пак се докладва


def test_citation_report_is_optional_backward_compatible():
    """Старият 4-аргументен подпис не бива да гърми (provisional default)."""
    result = AIProcessor._export_decision("approved", VALID, {}, _dur(0))
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


def test_strict_blocks_uncovered_boq_rows(monkeypatch):
    """Одит v13 #1: общ coverage — непокрит КСС ред блокира strict дори
    извън staging (напр. еднолистов проект с пропуснат ред)."""
    monkeypatch.setenv("EXPORT_POLICY", "strict")
    cite = {"checked": True, "mismatch": 0, "unknown_ref": 0, "uncited": 0,
            "verified": 1, "uncovered": ["КСС.xlsx!A!3"]}
    result = AIProcessor._export_decision("approved", VALID, {}, _dur(0), cite)
    assert result["exportable"] is False
    assert any("не са ДОКАЗАНО покрити" in b for b in result["blockers"])


def test_provisional_reports_uncovered_but_still_exports(monkeypatch):
    monkeypatch.setenv("EXPORT_POLICY", "provisional")
    cite = {"checked": True, "mismatch": 0, "unknown_ref": 0, "uncited": 0,
            "verified": 1, "uncovered": ["КСС.xlsx!A!3"]}
    result = AIProcessor._export_decision("approved", VALID, {}, _dur(0), cite)
    assert result["exportable"] is True
    assert result["blockers"]
