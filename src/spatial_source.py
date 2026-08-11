"""Откъде идва геометрията — и какво ѝ вярваме.

ОДИТ 10.08.2026, P1.1: „PDF Vision не трябва да е authoritative source за
node-to-node topology."

Съгласни сме, и измерването го показа: срещу 69 физически участъка в човешкия
еталон от PDF чертеж излизат 4 до 8, нестабилно, а понякога нула.  Преди това
слаб vision модел връщаше валиден JSON с преписани от промпта улици.

Проблемът не е, че четенето е слабо.  Проблемът е, че резултатът от четене на
картинка и резултатът от геометричен файл влизаха в програмата през един и същ
вход, без разлика в тежестта.  Затова тук източникът е ИЗРИЧЕН, а състоянието
на пространствения модел следва от него:

    DWG_DXF             геометрия от проекта            → resolved
    GIS                 GIS слой                        → resolved
    STRUCTURED_SEGMENTS таблица с участъци от човек      → resolved
    PDF_SUGGESTIONS     четене на чертеж                 → suggested
    NONE                няма нищо                        → unresolved

`suggested` значи: имената може да се ползват за КРЪЩАВАНЕ на участъци, но не
са доказателство за топология и не бива да излизат като геометрия.  Точно тази
разлика липсваше — и заради нея съчинени възли попаднаха в MS Project файл.
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "SpatialSource",
    "SpatialStatus",
    "status_for",
    "is_authoritative",
    "describe",
]


class SpatialSource(str, Enum):
    """Откъде са дошли участъците."""

    DWG_DXF = "dwg_dxf"
    GIS = "gis"
    STRUCTURED_SEGMENTS = "structured_segments"
    PDF_SUGGESTIONS_ONLY = "pdf_suggestions_only"
    NONE = "none"


class SpatialStatus(str, Enum):
    """Какво може да се твърди за пространствения модел."""

    RESOLVED = "resolved"        # геометрията е документ
    SUGGESTED = "suggested"      # има имена, няма доказана топология
    UNRESOLVED = "unresolved"    # няма пространствен модел


#: Кои източници са достатъчни, за да наричаме участъците геометрия.
_AUTHORITATIVE = frozenset({
    SpatialSource.DWG_DXF,
    SpatialSource.GIS,
    SpatialSource.STRUCTURED_SEGMENTS,
})

_STATUS = {
    SpatialSource.DWG_DXF: SpatialStatus.RESOLVED,
    SpatialSource.GIS: SpatialStatus.RESOLVED,
    SpatialSource.STRUCTURED_SEGMENTS: SpatialStatus.RESOLVED,
    SpatialSource.PDF_SUGGESTIONS_ONLY: SpatialStatus.SUGGESTED,
    SpatialSource.NONE: SpatialStatus.UNRESOLVED,
}

_DESCRIPTIONS = {
    SpatialStatus.RESOLVED: (
        "Участъците идват от геометричен източник — възлите и трасетата са "
        "документ и се изнасят като такива."
    ),
    SpatialStatus.SUGGESTED: (
        "Участъците са ПРОЧЕТЕНИ ОТ ЧЕРТЕЖ и служат само за наименуване.  "
        "Възлите не се изнасят като геометрия: четенето на PDF не е "
        "доказателство за топология."
    ),
    SpatialStatus.UNRESOLVED: (
        "Няма пространствен източник.  Участъците са групиране по количества, "
        "не физически трасета."
    ),
}


def status_for(source: SpatialSource | str) -> SpatialStatus:
    """Какво състояние следва от този източник."""
    return _STATUS[SpatialSource(source)]


def is_authoritative(source: SpatialSource | str) -> bool:
    """Дали от този източник възлите могат да напуснат програмата."""
    return SpatialSource(source) in _AUTHORITATIVE


def describe(source: SpatialSource | str) -> str:
    """Изречението, което да стои в отчета — за да не се подразбира."""
    return _DESCRIPTIONS[status_for(source)]
