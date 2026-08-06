"""Unit tests: анализът се чете и когато моделът е оградил JSON-а.

ЖИВ ПРОГОН 2026-08-06 (Sonnet през OpenRouter): анализът се върна като
```json … ``` — валиден JSON в ограда.  Всяко четене в ChatHandler ползваше
гол `json.loads` и гърмеше ТИХО.  Наведнъж падаха пет неща:
  - project_type → празен (значи и защитата „out_of_scope" е мъртва);
  - conflicts → моделът НАМЕРИ противоречие между файловете, а човекът
    никога не го видя;
  - specifics → празна причина при отказ;
  - участъци → празен списък;
  - въпросникът за последователността → проект с водопровод И канализация
    не питаше нищо.
Всичко това при напълно валиден отговор от модела.

FAILURE означава: смяна на модел (или на настроението му за форматиране)
пак изключва наведнъж класификацията, противоречията и въпросника — без
нито едно съобщение за грешка.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.chat_handler import ChatHandler  # noqa: E402

_ANALYSIS = {
    "project_type": "distribution",
    "scope": "водопровод и канализация",
    "quantities": {"Клон 1": {"dn": 110}, "Клон 2": {"dn": 160}},
    "conflicts": ["КСС дава 420 м, ситуацията — 380 м"],
    "specifics": "нестандартна доставка",
}


def _fenced(payload: dict, tag: str = "json") -> dict:
    return {"analysis": f"```{tag}\n{json.dumps(payload, ensure_ascii=False)}\n```"}


def _plain(payload: dict) -> dict:
    return {"analysis": json.dumps(payload, ensure_ascii=False)}


# ===================================================================
# _parsed_analysis
# ===================================================================

def test_fenced_json_is_parsed():
    assert ChatHandler._parsed_analysis(_fenced(_ANALYSIS)) == _ANALYSIS


def test_fence_without_language_tag_is_parsed():
    assert ChatHandler._parsed_analysis(_fenced(_ANALYSIS, tag="")) == _ANALYSIS


def test_json_surrounded_by_prose_is_recovered():
    raw = ("Ето анализа на документите:\n"
           + json.dumps(_ANALYSIS, ensure_ascii=False)
           + "\nАко имате въпроси, кажете.")
    assert ChatHandler._parsed_analysis({"analysis": raw}) == _ANALYSIS


def test_plain_json_still_works():
    assert ChatHandler._parsed_analysis(_plain(_ANALYSIS)) == _ANALYSIS


def test_dict_passes_through():
    assert ChatHandler._parsed_analysis({"analysis": _ANALYSIS}) == _ANALYSIS


def test_garbage_gives_empty_dict_not_exception():
    assert ChatHandler._parsed_analysis({"analysis": "съжалявам, не мога"}) == {}
    assert ChatHandler._parsed_analysis({"analysis": ""}) == {}
    assert ChatHandler._parsed_analysis({}) == {}


# ===================================================================
# Какво зависеше от това парсване
# ===================================================================

def test_project_type_survives_the_fence():
    assert ChatHandler._extract_project_type(_fenced(_ANALYSIS)) == "distribution"


def test_out_of_scope_guard_survives_the_fence():
    """Празен project_type изключваше отказа при проект извън обхвата."""
    fenced = _fenced({**_ANALYSIS, "project_type": "out_of_scope"})
    assert ChatHandler._extract_project_type(fenced) == "out_of_scope"


def test_sections_survive_the_fence():
    assert ChatHandler._extract_sections_from_analysis(_fenced(_ANALYSIS)) == [
        "Клон 1", "Клон 2"]


def test_sequence_questionnaire_survives_the_fence():
    """Проект с водопровод И канализация трябва да зададе въпроса."""
    state = ChatHandler()._start_sequence_questionnaire(_fenced(_ANALYSIS))
    assert state is not None
    assert state.get("question")


def test_manual_selection_still_wins_when_analysis_is_unreadable():
    assert ChatHandler._extract_project_type(
        {"analysis": "не мога"}, {"type": "single_section"}) == "single_section"
