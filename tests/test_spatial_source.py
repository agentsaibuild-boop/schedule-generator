"""Unit tests: източникът на геометрия е изричен, не подразбиращ се.

ОДИТ 10.08.2026, P1.1: „PDF Vision не трябва да е authoritative source за
node-to-node topology.  При PDF-only: spatial_status=unresolved."

Приемаме разграничението.  Правим го с една степен по-точно от поисканото:
PDF четенето не е `unresolved`, а `suggested` — имената от чертежа СА полезни
за кръщаване на участъците („кл. 1 от РШ 16 до РШ 17, ул. Петуния" вместо
описанието от КСС), просто не са доказателство за топология.  Разликата има
последствие: при `suggested` възлите не напускат програмата като геометрия.

Ако одиторът предпочита PDF да е направо `unresolved`, това е една дума в
`_STATUS` — но тогава губим и наименуването.

FAILURE означава: прочетено от картинка пак тежи колкото геометрия от проект.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.spatial_source import (  # noqa: E402
    SpatialSource,
    SpatialStatus,
    describe,
    is_authoritative,
    status_for,
)


@pytest.mark.parametrize("source", [
    SpatialSource.DWG_DXF,
    SpatialSource.GIS,
    SpatialSource.STRUCTURED_SEGMENTS,
])
def test_geometry_sources_are_authoritative(source):
    assert is_authoritative(source)
    assert status_for(source) is SpatialStatus.RESOLVED


def test_reading_a_pdf_is_not_authoritative():
    """Централната точка на находката."""
    assert not is_authoritative(SpatialSource.PDF_SUGGESTIONS_ONLY)


def test_reading_a_pdf_yields_suggestions_not_geometry():
    assert status_for(SpatialSource.PDF_SUGGESTIONS_ONLY) is SpatialStatus.SUGGESTED


def test_no_source_is_unresolved():
    assert status_for(SpatialSource.NONE) is SpatialStatus.UNRESOLVED
    assert not is_authoritative(SpatialSource.NONE)


def test_every_source_has_a_status():
    """Нов източник без решение за тежестта му е точно начинът да се промъкне."""
    for source in SpatialSource:
        assert isinstance(status_for(source), SpatialStatus)


def test_every_status_is_explained_in_words():
    """Ограничението трябва да се ЧЕТЕ в отчета, не да се подразбира."""
    for source in SpatialSource:
        assert len(describe(source)) > 40


def test_strings_are_accepted_as_well_as_enum_members():
    assert status_for("gis") is SpatialStatus.RESOLVED
    assert not is_authoritative("pdf_suggestions_only")


def test_an_unknown_source_is_refused():
    """Мълчаливо подразбиране е точно това, което премахваме."""
    with pytest.raises(ValueError):
        status_for("нещо ново")


# ===================================================================
# Vision моделът връща ПРИМЕРА от задачата (измерено 17.08.2026)
# ===================================================================
#
# Слаб OCR модел върна едни и същи ЧЕТИРИ „отсечки" за ДВА различни чертежа —
# дословно примерите от самата задача („кл. 48: РШ36→РШ37, РШ37→РШ38" и
# „КЛ. 25 - И: ОТ27→ОТ27А").  Улици нямаше, затова старата проверка за редицата
# „Първа, Втора, Трета" не се задейства и измислената геометрия мина нататък.
#
# Тя после кръщава участъци и гейтът за разделянето я брои като долна граница
# на това, което СЪЩЕСТВУВА на обекта.  Точно затова серията С отсечки излизаше
# по-слаба от серията без тях.
#
# FAILURE означава: измислена геометрия пак ще изглежда като прочетен чертеж.


from src.ai_processor import AIProcessor  # noqa: E402


def _отсечка(branch, start, end, network="К", street=""):
    return {"branch": branch, "start_node": start, "end_node": end,
            "network": network, "street": street}


def test_segments_copied_from_the_prompt_are_rejected():
    измислени = [
        _отсечка("кл. 48", "РШ36", "РШ37"),
        _отсечка("кл. 48", "РШ37", "РШ38"),
        _отсечка("КЛ. 25 - И", "ОТ27", "ОТ27А", network="В"),
    ]

    assert AIProcessor._situation_segments_are_schematic(измислени),         "примерът от задачата мина за прочетен чертеж"


def test_a_real_drawing_survives():
    """Еталонът СЪДЪРЖА „кл. 48" и „РШ 36" — по клон или възел не се съди."""
    истински = [
        _отсечка("Кл. 1", "РШ 1", "РШ 2"),
        _отсечка("Кл. 1", "РШ 2", "РШ 3"),
        _отсечка("кл. 48", "РШ 36", "Пр. Ш 1"),
    ]

    assert not AIProcessor._situation_segments_are_schematic(истински)


def test_the_old_street_check_still_works():
    схема = [_отсечка("кл. 1", "РШ 1", "РШ 2", street="ул. Първа"),
             _отсечка("кл. 2", "РШ 3", "РШ 4", street="ул. Втора")]

    assert AIProcessor._situation_segments_are_schematic(схема)


def test_one_copied_segment_among_many_real_ones_does_not_condemn_the_drawing():
    """Прагът е „повечето", не „поне една" — иначе истински чертеж отпада."""
    смесени = [_отсечка(f"Кл. {i}", f"РШ {i}", f"РШ {i + 1}") for i in range(1, 6)]
    смесени.append(_отсечка("кл. 48", "РШ36", "РШ37"))

    assert not AIProcessor._situation_segments_are_schematic(смесени)
