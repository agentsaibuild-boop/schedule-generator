"""Шахтите, оттоците и сградните отклонения се БРОЯТ от чертежа.

Изпълнителят, 24.08.2026: количествата за график са „линейната дължина за В и К,
с всички диаметри на тръбите, както и СВО, СКО, шахти, УО от техническата
спецификация И ЧЕРТЕЖИТЕ".

FAILURE означава: точковите позиции пак ще идват САМО от таблица.  Техническа
спецификация с метри тръба и нито един брой — а такива са повечето — ще даде
график без шахти, без оттоци и без сградни отклонения, при чертеж, на който
всички те са изписани.

Двете правила, които тези тестове пазят:
    ОБХВАТ      възел до линия „следващ етап" не е наш, точно както отсечките;
    ТАБЛИЦАТА   надделява — преброеното допълва, не замества договорното.

Чертежите тук се СЪЗДАВАТ от теста; клиентски файлове в репото не влизат.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

fitz = pytest.importorskip("fitz", reason="PyMuPDF липсва — четенето на чертежи")

from src.provenance import QuantityRow, SourceRef, activity_class  # noqa: E402
from src.situation_reader import (  # noqa: E402
    count_nodes,
    merge_node_rows,
    nodes_as_quantity_rows,
    read_sewer_nodes,
)

ЗЕЛЕН = (0.75, 1.0, 0.0)      # инвестиционна програма → ВЛИЗА
РОЗОВ = (1.0, 0.64, 0.73)     # следващ етап → не влиза

_ШРИФТ = str(Path(__file__).parent.parent / "fonts" / "DejaVuSans.ttf")


def _пиши(page, x: float, y: float, текст: str, размер: float = 7) -> None:
    page.insert_text(fitz.Point(x, y), текст, fontsize=размер,
                     fontfile=_ШРИФТ, fontname="deja")


def _чертеж(път: Path) -> Path:
    """Две трасета: нашето (зелено) и на следващия етап (розово), с етикети."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=400)

    for i, (цвят, текст) in enumerate((
        (ЗЕЛЕН, "Инвестиционна програма за смесен канал"),
        (РОЗОВ, "Инвестиционна програма за битов канал - следващ етап"),
    )):
        y = 40 + i * 20
        page.draw_line(fitz.Point(30, y), fitz.Point(60, y), color=цвят, width=2)
        _пиши(page, 70, y + 3, текст)

    page.draw_line(fitz.Point(60, 200), fitz.Point(560, 200), color=ЗЕЛЕН, width=2)
    page.draw_line(fitz.Point(60, 340), fitz.Point(560, 340), color=РОЗОВ, width=2)

    _пиши(page, 300, 190, "ул.Грозден")

    # НАШИТЕ: три шахти, два оттока, едно сградно отклонение.
    for i, етикет in enumerate(("РШ 1", "РШ 2", "РШ 3", "ОТ 7", "ОТ 8", "СКО 4")):
        _пиши(page, 90 + i * 70, 196, етикет)

    # ЧУЖДИТЕ: на трасето от следващия етап.
    for i, етикет in enumerate(("РШ 40", "ОТ 41")):
        _пиши(page, 90 + i * 70, 336, етикет)

    doc.save(str(път))
    doc.close()
    return път


@pytest.fixture
def чертеж(tmp_path: Path) -> Path:
    return _чертеж(tmp_path / "СИТ_КАНАЛИЗАЦИЯ.pdf")


@pytest.fixture
def възли(чертеж: Path):
    return read_sewer_nodes(чертеж)


class TestЧетене:
    def test_всеки_етикет_се_разпознава(self, възли):
        assert len(възли) == 8, (
            f"прочетени са {len(възли)} точкови позиции вместо 8 — "
            f"{[в.label for в in възли]}")

    def test_видът_идва_от_етикета(self, възли):
        по_вид = {в.label: в.kind for в in възли}
        assert по_вид["РШ 1"] == "РШ"
        assert по_вид["ОТ 7"] == "ОТ"
        assert по_вид["СКО 4"] == "СКО"

    def test_позицията_казва_от_кой_документ_е(self, възли):
        assert {в.source for в in възли} == {"СИТ_КАНАЛИЗАЦИЯ.pdf"}

    def test_улицата_се_закача_за_позицията(self, възли):
        наши = [в for в in възли if в.in_scope]
        assert all(в.street == "ул.Грозден" for в in наши), (
            f"{[(в.label, в.street) for в in наши]}")


class TestОбхват:
    def test_чуждата_мрежа_не_се_брои(self, възли):
        assert count_nodes(възли) == {"РШ": 3, "ОТ": 2, "СКО": 1}, (
            "позиции от следващия етап влязоха в нашите количества")

    def test_отпадналите_казват_защо(self, възли):
        чужди = [в for в in възли if not в.in_scope]
        assert len(чужди) == 2
        assert all("следващ етап" in в.scope_reason for в in чужди), (
            f"{[(в.label, в.scope_reason) for в in чужди]}")


class TestКоличества:
    def test_редовете_носят_брой_мярка_и_чертежа(self, възли):
        редове = nodes_as_quantity_rows(възли)
        по_вид = {р.raw["вид"]: р for р in редове}

        assert set(по_вид) == {"РШ", "ОТ", "СКО"}
        assert по_вид["РШ"].quantity == 3
        assert по_вид["РШ"].unit == "бр"
        assert по_вид["РШ"].source.document == "СИТ_КАНАЛИЗАЦИЯ.pdf"
        assert "чертеж" in по_вид["РШ"].description, по_вид["РШ"].description

    def test_описанието_хваща_клас_и_норма(self, възли):
        редове = nodes_as_quantity_rows(възли)

        for ред in редове:
            assert activity_class(ред.description) == "manhole", (
                f"„{ред.description}" + "“ остава без клас — работата няма да "
                "попадне в нито една стъпка от веригата")

    def test_уличният_отток_вече_има_клас(self):
        # Причината шаблонът да се разшири: ред „Улични оттоци" не съвпадаше с
        # нито един клас и работата оставаше непланирана.
        assert activity_class("Улични оттоци (УО)") == "manhole"
        assert activity_class("Дъждоприемни шахти") == "manhole"


class TestСливане:
    def _ред(self, описание: str, количество: float, мярка: str = "бр"):
        return QuantityRow(description=описание, quantity=количество,
                           unit=мярка, source=SourceRef(document="СПЕЦ.docx",
                                                        sheet="таблица 1", row=3),
                           raw={})

    def test_таблицата_надделява_над_преброеното(self, възли):
        таблица = [self._ред("Ревизионни шахти РШ", 46)]

        редове, бележки = merge_node_rows(таблица,
                                          nodes_as_quantity_rows(възли))

        шахти = [р for р in редове if "шахт" in р.description.lower()]
        assert len(шахти) == 1 and шахти[0].quantity == 46, (
            "преброените шахти се добавиха върху договорните — броят се удвоява")
        assert any("важи таблицата" in б for б in бележки), бележки

    def test_каквото_таблицата_мълчи_идва_от_чертежа(self, възли):
        таблица = [self._ред("Ревизионни шахти РШ", 46)]

        редове, бележки = merge_node_rows(таблица,
                                          nodes_as_quantity_rows(възли))

        добавени = {р.raw.get("вид") for р in редове if р.raw.get("вид")}
        assert добавени == {"ОТ", "СКО"}, (
            f"от чертежа влязоха {добавени} — оттоците и СКО липсват")
        assert any("ПРЕБРОЕНИ ОТ ЧЕРТЕЖА" in б for б in бележки), бележки

    def test_ред_в_метри_не_минава_за_брой(self, възли):
        # „Изграждане на канализация, вкл. ревизионни шахти — 1182 m'“ НЕ е
        # бройка: шахтите там са казано какво влиза в цената, не колко са.
        таблица = [self._ред("Изграждане на клонове, вкл. ревизионни шахти",
                             1182.0, "m")]

        редове, _ = merge_node_rows(таблица, nodes_as_quantity_rows(възли))

        добавени = {р.raw.get("вид") for р in редове if р.raw.get("вид")}
        assert "РШ" in добавени, (
            "линеен ред премина за бройка и шахтите отпаднаха от графика")

    def test_без_чертеж_нищо_не_се_мени(self):
        таблица = [self._ред("Ревизионни шахти РШ", 46)]

        редове, бележки = merge_node_rows(таблица, [])

        assert редове == таблица and not бележки
