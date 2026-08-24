"""Прочетеното от чертежа стига до генерацията, по която върви реалният проект.

FAILURE означава: чертежите пак се четат в единия клон на кода, а графикът се
прави в другия.  Точно това беше вярно до 24.08.2026 — `handle_generate` четеше
отсечки и точкови позиции, но при реален проект той връща управлението на
въпросника и генерацията минава през `_continue_generation`, който вадеше САМО
имената на улиците.  Тоест участъците от чертежа и преброените шахти се виждаха
единствено в `tools/offline_dry_run.py`, а човекът получаваше график без тях.

Тестът е за КАБЕЛА, не за четенето: че прочетеното е подадено нататък.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.chat_handler import ChatHandler  # noqa: E402


class _Дотук(Exception):
    """Спира изпълнението точно на извикването, което проверяваме."""


def _handler(възли=(), отсечки=()):
    h = ChatHandler()
    h.files = MagicMock()
    h.files.get_all_text.return_value = ""
    h.ai = MagicMock()
    h._progress = MagicMock()
    h._extract_project_type = MagicMock(return_value="водопровод")
    h._read_situation_files = MagicMock(
        return_value=(["ул.Грозден"], list(отсечки), list(възли)))
    h._boq_index = MagicMock(return_value=[])
    h._try_package_generation = MagicMock(side_effect=_Дотук)
    return h


class TestСледВъпросника:
    def test_отсечките_стигат_до_пакетната_генерация(self):
        h = _handler(отсечки=[{"network": "К", "branch": "Кл.48", "dn": 700,
                               "length_m": 618.74}])

        with pytest.raises(_Дотук):
            h._continue_generation({}, {}, None, num_teams=1)

        _, kwargs = h._try_package_generation.call_args
        assert kwargs.get("segments"), (
            "участъците от чертежа не се подават на генерацията — "
            "графикът пак ще ги измисля като етапи")

    def test_точковите_позиции_се_доливат_към_количествата(self):
        възел = MagicMock()
        h = _handler(възли=[възел])
        извикано = {}

        def _слей(boq, nodes, notes):
            извикано["възли"] = list(nodes)
            return boq

        h._with_drawing_counts = MagicMock(side_effect=_слей)

        with pytest.raises(_Дотук):
            h._continue_generation({}, {}, None, num_teams=1)

        assert извикано.get("възли") == [възел], (
            "преброените шахти и оттоци не стигат до количествата")

    def test_чертежите_се_четат_веднъж_за_прогон(self):
        h = _handler()

        with pytest.raises(_Дотук):
            h._continue_generation({}, {}, None, num_teams=1)

        assert h._read_situation_files.call_count == 1, (
            "чертежите се четат повече от веднъж — всяко четене отваря PDF-ите "
            "наново")


class TestОбщиятЧетец:
    def test_без_файлове_връща_три_празни(self):
        h = ChatHandler()
        h.files = None
        h.ai = None

        места, отсечки, възли = h._read_situation_files()

        assert (места, отсечки, възли) == ([], [], [])
