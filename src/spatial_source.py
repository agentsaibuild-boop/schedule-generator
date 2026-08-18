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

import re
from enum import Enum

__all__ = [
    "SpatialSource",
    "SpatialStatus",
    "status_for",
    "is_authoritative",
    "describe",
    "strip_node_claim",
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
        "НЕ МОГА да определя node-to-node участъците от тези входни данни.  "
        "Участъците по-долу са ГРУПИРАНЕ ПО КОЛИЧЕСТВА (етапи на изпълнение), "
        "не физически трасета, и възли не се изнасят.  За физически участъци е "
        "нужен DWG/DXF, GIS слой или таблица с участъци — КСС не съдържа "
        "разчленяване."
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


#: Как изглежда възел в българската проектантска практика: РШ 36, КШ 1,
#: ВШ 2, ЕЛШ 8, ОТ 27А, Т.46, Пр. Ш 1, СРШ 53.
#:
#: Кирилица И латиница за буквите-двойници (Т/T, О/O, Р/P, Ш е само кирилско):
#: тръжните документи ги смесват в един и същи ред — „ГЛ.КЛ.II от Т.1 до T.46",
#: „КЛ. 18 - И от ОТ 78 до OT 88".  Съвпадение само по кирилица пропуска
#: половината твърдения и ги пуска навън.
_NODE = (r"(?:С?[РP]Ш|КШ|ВШ|ЕЛШ|ПШ|[ОO][ТT]|П[рp]\.?\s*Ш|[ТT])"
         r"\s*\.?\s*\d+\s*[А-Яа-яA-Za-z]?")

#: „… от РШ 36 до Пр. Ш 1" — ТВЪРДЕНИЕ ЗА ТОПОЛОГИЯ, не име.
_NODE_CLAIM = re.compile(
    rf"\s*[,;–—-]?\s*от\s+{_NODE}\s+до\s+{_NODE}\s*$", re.IGNORECASE)


def strip_node_claim(text: str) -> str:
    """Маха „от <възел> до <възел>" от име, което няма право да го твърди.

    ЗАЩО ТУК И ЗАЩО ИЗПЪЛНИМО.  Този модул от 10.08.2026 обещава с думи, че
    „непотвърдената идентичност не напуска програмата".  Обещанието не е било
    изпълнено: пакет със `spatial_verified=False` се изписваше ДОСЛОВНО като
    потвърден — „кл. 1 от КШ 1 до КШ 2" — и тези съчинени възли влизаха в
    имената на задачите, в WBS-а и в изнесения MS Project файл, където са
    неразличими от прочетена геометрия.  Проверено на 18.08.2026: `label`
    лепеше `start_node`/`end_node`, без изобщо да гледа `spatial_verified`, а
    свободното `name` идва право от модела.

    Маха се САМО твърдението за двойка възли.  Клонът и улицата остават: при
    `SUGGESTED` те са законно име, прочетено от чертеж.  Каквото не прилича на
    възел, не се пипа — по-добре да остане нещо излишно, отколкото да се
    отреже истинско име.
    """
    предишно = None
    текущо = str(text or "").strip()
    while текущо != предишно:
        предишно = текущо
        текущо = _NODE_CLAIM.sub("", текущо).strip(" ,;–—-")
    return текущо
