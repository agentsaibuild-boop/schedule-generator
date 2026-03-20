"""Unit tests for keyword detection in _start_sequence_questionnaire.

Covers C3 fix ("тласкател" recognised as water keyword) and C7 fix
("в/к" decoupled from individual network lists via _COMBINED_KEYWORDS).

FAILURE означава: промяна в _WATER_KEYWORDS / _SEWER_KEYWORDS / _COMBINED_KEYWORDS
в chat_handler.py без да се отразят тук → регресия в определянето кога да се
задава въпросникът за последователност В+К.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.chat_handler import ChatHandler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _analysis_with_scope(scope: str) -> dict:
    return {
        "analysis": json.dumps({
            "project_type": "",
            "scope": scope,
            "quantities": {},
        })
    }


def _triggers(scope: str) -> bool:
    """Return True if the scope string triggers the sequence questionnaire."""
    return ChatHandler()._start_sequence_questionnaire(_analysis_with_scope(scope)) is not None


# ---------------------------------------------------------------------------
# C3 — "тласкател" must be recognised as a water keyword
# ---------------------------------------------------------------------------

def test_тласкател_alone_does_not_trigger():
    """тласкател is water — without sewer the questionnaire must NOT fire."""
    assert not _triggers("тласкател DN300")


def test_тласкател_with_sewer_triggers():
    """тласкател (water) + канализация → both networks present → questionnaire fires."""
    assert _triggers("тласкател и канализация")


def test_тласкател_and_фекална_triggers():
    """тласкател + фекална мрежа (sewer synonym) → questionnaire fires."""
    assert _triggers("тласкател и фекална мрежа")


# ---------------------------------------------------------------------------
# C7 — "в/к" must not create false-positive when only one network is present
# ---------------------------------------------------------------------------

def test_vk_abbreviation_alone_does_not_trigger():
    """Bare 'в/к' (without 'мрежа') must NOT fire the questionnaire.

    Before C7 fix, 'в/к' sat in BOTH keyword lists, so a project description
    mentioning it once would set both has_water and has_sewer to True.
    """
    assert not _triggers("в/к инсталация")


def test_vk_mreza_triggers():
    """'в/к мрежа' explicitly means combined water+sewer → questionnaire fires."""
    assert _triggers("в/к мрежа")


def test_vk_mreza_uppercase_triggers():
    """Keyword comparison is case-insensitive — 'В/К мрежа' must also fire."""
    assert _triggers("В/К мрежа")


def test_вк_mreza_without_slash_triggers():
    """'вк мрежа' (no slash variant) must also trigger the questionnaire."""
    assert _triggers("вк мрежа")


# ---------------------------------------------------------------------------
# Sanity — single-network projects must NOT trigger
# ---------------------------------------------------------------------------

def test_water_only_does_not_trigger():
    assert not _triggers("водопровод питейна вода DN90")


def test_sewer_only_does_not_trigger():
    assert not _triggers("канализация фекална DN315")


def test_питейна_alone_does_not_trigger():
    assert not _triggers("питейна мрежа")


def test_dual_explicit_triggers():
    """Explicit 'водопровод и канализация' is the baseline combined case."""
    assert _triggers("водопровод и канализация")


if __name__ == "__main__":
    tests = [
        test_тласкател_alone_does_not_trigger,
        test_тласкател_with_sewer_triggers,
        test_тласкател_and_фекална_triggers,
        test_vk_abbreviation_alone_does_not_trigger,
        test_vk_mreza_triggers,
        test_vk_mreza_uppercase_triggers,
        test_вк_mreza_without_slash_triggers,
        test_water_only_does_not_trigger,
        test_sewer_only_does_not_trigger,
        test_питейна_alone_does_not_trigger,
        test_dual_explicit_triggers,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as exc:
            print(f"  FAIL  {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
