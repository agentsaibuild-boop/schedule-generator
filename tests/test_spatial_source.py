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
