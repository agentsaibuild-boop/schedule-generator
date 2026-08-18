"""Съчинени възли не напускат програмата.

FAILURE означава: пакет без потвърдена геометрия пак се изписва като пакет с
прочетена такава, и съчинени възли влизат в имената на задачите, в WBS-а и в
изнесения MS Project файл, където никой отвън не може да ги различи от документ.

ОДИТ 10.08.2026, P1.1: „PDF Vision не трябва да е authoritative source за
node-to-node topology."  src/spatial_source.py го обеща с думи същия ден.
ПРОВЕРЕНО 18.08.2026: обещанието не беше изпълнено — `label` лепеше
`start_node`/`end_node`, без изобщо да гледа `spatial_verified`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.spatial_source import (  # noqa: E402
    SpatialSource, describe, is_authoritative, strip_node_claim)
from src.work_package import SpatialWorkPackage  # noqa: E402


def _пакет(**kw) -> SpatialWorkPackage:
    основа = {"id": "K1", "network": "К", "chain": "sewer_section"}
    return SpatialWorkPackage(**{**основа, **kw})


# ---------------------------------------------------------------------------
# Твърдението за възли
# ---------------------------------------------------------------------------


def test_unverified_nodes_do_not_reach_the_label():
    """Точният случай от прогона: „кл. 1 от КШ 1 до КШ 2" без нито един чертеж."""
    пакет = _пакет(branch="кл. 1", start_node="КШ 1", end_node="КШ 2",
                   spatial_verified=False)

    assert пакет.label == "кл. 1"
    assert "КШ" not in пакет.label


def test_the_claim_cannot_sneak_through_the_free_text_name():
    """`name` идва право от модела — гейт само върху възлите е заобиколим."""
    пакет = _пакет(name="кл. 5 от РШ 9 до РШ 10", spatial_verified=False)

    assert пакет.label == "кл. 5"


def test_verified_geometry_keeps_its_nodes():
    """Прочетената геометрия Е документ и трябва да се изнесе като такава."""
    пакет = _пакет(branch="кл. 2", start_node="РШ 7", end_node="РШ 8",
                   spatial_verified=True)

    assert пакет.label == "кл. 2 от РШ 7 до РШ 8"


def test_a_package_with_nothing_left_is_still_named():
    """Махането на твърдението не бива да оставя празно име."""
    пакет = _пакет(start_node="КШ 1", end_node="КШ 2", spatial_verified=False)

    assert пакет.label == "Участък K1"


# ---------------------------------------------------------------------------
# Самият разпознавач
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("вход,изход", [
    ("кл. 48 от РШ 36 до Пр. Ш 1", "кл. 48"),
    ("кл. 2,1 от РШ 3 до РШ 1", "кл. 2,1"),
    # Тръжните документи смесват кирилица и латиница в един и същи ред.
    ("ГЛ.КЛ.II от Т.1 до T.46", "ГЛ.КЛ.II"),
    ("КЛ. 18 - И от ОТ 78 до OT 88", "КЛ. 18 - И"),
])
def test_node_claims_are_recognised(вход, изход):
    assert strip_node_claim(вход) == изход


@pytest.mark.parametrize("текст", [
    "бул. Рожен",
    "Възстановяване на настилките",
    "кл. 12",
    # НЕ е твърдение за възли, макар да съдържа „от … до".
    "Изпитване от 1 до 5 бар",
])
def test_real_names_are_left_alone(текст):
    """По-добре да остане нещо излишно, отколкото да се отреже истинско име."""
    assert strip_node_claim(текст) == текст


# ---------------------------------------------------------------------------
# Приложението трябва да го КАЖЕ
# ---------------------------------------------------------------------------


def test_without_a_spatial_source_the_app_refuses_out_loud():
    """Мълчаливото знание е същото като липсващото."""
    изречение = describe(SpatialSource.NONE)

    assert "НЕ МОГА" in изречение
    assert "node-to-node" in изречение
    assert "DWG" in изречение, "не казва какво би поправило положението"


def test_a_drawing_is_not_authoritative_geometry():
    assert not is_authoritative(SpatialSource.PDF_SUGGESTIONS_ONLY)
    assert is_authoritative(SpatialSource.DWG_DXF)
    assert is_authoritative(SpatialSource.STRUCTURED_SEGMENTS)
