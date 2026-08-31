"""Доказаната продължителност и офертната се пазят ПООТДЕЛНО.

FAILURE означава: src/duration_evidence.py е счупен — или калибрирането до
договорния срок пак затрива с какво е било обосновано времето (тогава изходът
не може да каже „по доказателства 13 дни, в офертата 10.5"), или медианата от
корпуса пак се брои като „няма доказателство" и отчетът обявява 821 недоказани
задачи там, където доказани са 862 от 895.

Стълбицата е на одитора (31.08.2026): норма → корпус → изпълнител → договорен
срок → подразбиране → нищо.  Само последното значи „нямаме основание“.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.duration_evidence import (  # noqa: E402
    ДОКАЗАНИ, СТЪЛБИЦА, describe, grade_of, report, stamp_base, stamp_bid)


def _задача(**полета) -> dict:
    основа = {"id": "T1", "name": "изкоп", "duration": 4}
    основа.update(полета)
    return основа


# ---------------------------------------------------------------------------
# Разпознаване на основанието
# ---------------------------------------------------------------------------

def test_нормата_е_най_силното_основание():
    т = _задача(duration_source="calculated", duration_status="CALCULATED")
    assert grade_of(т) == "PARAMETRIC_NORM"


def test_медианата_от_корпуса_е_основание_а_не_липса():
    """Точно това се четеше като NOT_PARAMETRIC и вдигаше 821 недоказани."""
    т = _задача(duration_source="chain_template",
                duration_status="NOT_PARAMETRIC")
    assert grade_of(т) == "HISTORICAL_CORPUS_MEDIAN"
    assert grade_of(т) in ДОКАЗАНИ


def test_темпото_на_стъпката_е_основание_от_корпуса():
    """`step_rate` идва от еталонния график — 33 задачи бяха обявени за без
    основание само защото стълбицата не знаеше този произход."""
    т = _задача(duration_source="step_rate", step_rate=25.3,
                duration_status="NOT_PARAMETRIC")
    assert grade_of(т) == "HISTORICAL_CORPUS_MEDIAN"
    assert grade_of(т) in ДОКАЗАНИ


def test_разтеглената_върху_фазата_не_е_доказана():
    т = _задача(duration_source="construction_span", duration=660)
    assert grade_of(т) == "CONTRACT_SPAN_CALIBRATED"
    assert grade_of(т) not in ДОКАЗАНИ


def test_без_нищо_зад_себе_си_е_UNSUPPORTED():
    assert grade_of(_задача(duration_source="suggested")) == "UNSUPPORTED"


def test_стълбицата_е_подредена_от_силно_към_слабо():
    assert СТЪЛБИЦА[0] == "PARAMETRIC_NORM"
    assert СТЪЛБИЦА[-1] == "UNSUPPORTED"


# ---------------------------------------------------------------------------
# Базата се пази ПРЕДИ калибрирането
# ---------------------------------------------------------------------------

def test_базата_се_записва_веднъж_и_не_се_презаписва():
    задачи = [_задача(duration=13, duration_source="chain_template")]
    stamp_base(задачи)
    assert задачи[0]["base_duration"] == 13

    задачи[0]["duration"] = 10          # калибрирането свива
    stamp_base(задачи)                  # втори опит не бива да я мени
    assert задачи[0]["base_duration"] == 13


def test_обобщаващите_не_влизат():
    задачи = [_задача(is_summary=True, duration=100)]
    stamp_base(задачи)
    assert "base_duration" not in задачи[0]


# ---------------------------------------------------------------------------
# Офертата казва КОЛКО се различава и ЗАЩО
# ---------------------------------------------------------------------------

def test_свитата_задача_носи_множител_и_причина():
    задачи = [_задача(duration=13, duration_source="chain_template")]
    stamp_base(задачи)
    задачи[0].update({"duration": 10, "declared_pace": 6.64,
                      "pace_origin": "deadline"})
    stamp_bid(задачи)

    т = задачи[0]
    assert т["base_duration"] == 13          # базата оцелява свиването
    assert т["bid_duration"] == 10
    assert 0.76 < т["calibration_factor"] < 0.78
    assert "договорния срок" in т["calibration_reason"]
    assert т["duration_evidence"] == "CONTRACT_SPAN_CALIBRATED"


def test_обявеното_от_изпълнителя_не_е_същото_като_изведеното_от_срока():
    задачи = [_задача(duration=13, duration_source="chain_template")]
    stamp_base(задачи)
    задачи[0].update({"duration": 10, "declared_pace": 8.6,
                      "pace_origin": "declared"})
    stamp_bid(задачи)
    assert задачи[0]["duration_evidence"] == "CONTRACTOR_INPUT"
    assert "изпълнителя" in задачи[0]["calibration_reason"]


def test_непипнатата_задача_няма_причина_за_калибриране():
    задачи = [_задача(duration=5, duration_source="calculated",
                      duration_status="CALCULATED")]
    stamp_base(задачи)
    stamp_bid(задачи)
    assert задачи[0]["calibration_factor"] == 1.0
    assert "calibration_reason" not in задачи[0]


# ---------------------------------------------------------------------------
# Отчетът разделя двата въпроса
# ---------------------------------------------------------------------------

def test_отчетът_брои_основанието_а_калибрирането_отделно():
    задачи = [
        _задача(id="A", duration=5, duration_source="calculated",
                duration_status="CALCULATED"),
        _задача(id="B", duration=3, duration_source="chain_template"),
        _задача(id="C", duration=2, duration_source="suggested"),
    ]
    stamp_base(задачи)
    задачи[1].update({"duration": 2, "declared_pace": 7.0,
                      "pace_origin": "deadline"})
    stamp_bid(задачи)

    отчет = report(задачи)
    assert отчет["задачи"] == 3
    # Задача B е КАЛИБРИРАНА, но основанието ѝ си остава корпусът.
    assert отчет["по_основание"]["HISTORICAL_CORPUS_MEDIAN"]["задачи"] == 1
    assert отчет["доказани"] == 2
    assert отчет["калибрирани"] == 1


def test_описанието_казва_и_сбора():
    задачи = [_задача(duration=10, duration_source="chain_template")]
    stamp_base(задачи)
    задачи[0]["duration"] = 8
    задачи[0]["declared_pace"] = 6.0
    задачи[0]["pace_origin"] = "deadline"
    stamp_bid(задачи)
    редове = describe(report(задачи))
    сборът = [р for р in редове if "сбор:" in р]
    assert сборът and "×0.80" in сборът[0]
