"""Количествата, въведени на ръка, се четат като всеки друг документ.

FAILURE означава: човек без четими тръжни документи пак няма как да влезе — или
въведеното се записва във формат, който индексът не разбира, или описанието на
реда не носи мрежата, диаметъра и материала и надолу по веригата редът излиза
без клас, без норма и без верига.

Описанието НЕ е свободен текст: то е единственото място, откъдето конвейерът
чете какво е това количество.  Затова тестовете тук проверяват точно него — с
истинските четци, не с преразказ.
"""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.duration_calculator import (  # noqa: E402
    detect_dn,
    detect_material,
    detect_method,
)
from src.manual_quantities import (  # noqa: E402
    ИМЕ_НА_ФАЙЛА,
    Тръба,
    as_csv,
    save,
)
from src.provenance import activity_class  # noqa: E402


def _редове(csv_текст: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(csv_текст), delimiter=";"))


class TestОписаниетоНосиВсичко:
    """Мрежата, диаметърът, материалът и методът се четат от описанието."""

    def test_канализационна_тръба(self):
        описание = Тръба("К", 300, "PP", 1182.0).описание()

        assert "канализационни" in описание, описание
        assert detect_dn({"name": описание}) == 300
        assert detect_material({"name": описание}) == "PP"
        assert detect_method({"name": описание}) == "open"
        assert activity_class(описание) == "laying"

    def test_водопроводна_тръба(self):
        описание = Тръба("В", 110, "PE", 540.0).описание()

        assert "водопроводни" in описание, описание
        assert detect_dn({"name": описание}) == 110
        assert detect_material({"name": описание}) == "PE"

    def test_безизкопното_се_вижда_в_описанието(self):
        сондаж = Тръба("В", 110, "PE", 540.0, method="HDD").описание()

        assert detect_method({"name": сондаж}) == "HDD", сондаж

    def test_точковите_позиции_хващат_клас(self):
        csv_текст = as_csv([], {"РШ": 46, "СКО": 180, "УО": 12}, {})

        for ред in _редове(csv_текст):
            assert activity_class(ред["Описание"]) == "manhole", ред["Описание"]

    def test_точковите_позиции_казват_и_мрежата_си(self):
        # Тук няма лист и няма име на файл, от които мрежата да се извади:
        # позиция без мрежа остава без верига и някой я разпределя наслуки.
        from src.execution_batches import _network_of_row
        from src.provenance import QuantityRow, SourceRef

        csv_текст = as_csv([], {"РШ": 46, "СРС": 8, "СКО": 180, "СВО": 174,
                                "УО": 12}, {})
        for ред in _редове(csv_текст):
            row = QuantityRow(description=ред["Описание"], quantity=1.0,
                              unit="бр", raw={},
                              source=SourceRef("Ръчно.csv", "Sheet1", 2))
            assert _network_of_row(row) in ("К", "В"), ред["Описание"]

    def test_възстановяването_хваща_клас(self):
        csv_текст = as_csv([], {}, {"асфалт": 10824, "унипаваж": 18671,
                                    "бордюри": 7761})

        for ред in _редове(csv_текст):
            assert activity_class(ред["Описание"]) == "pavement", ред["Описание"]


class TestТаблицата:
    def test_колоните_са_тези_които_индексът_чете(self):
        csv_текст = as_csv([Тръба("К", 300, "PP", 100.0)])

        заглавия = _редове(csv_текст)[0].keys()
        assert set(заглавия) == {"Описание", "Количество", "Мярка"}

    def test_мерките_са_по_вид_работа(self):
        csv_текст = as_csv([Тръба("К", 300, "PP", 100.0)],
                           {"РШ": 5}, {"асфалт": 200.0})
        мерки = {р["Описание"][:12]: р["Мярка"] for р in _редове(csv_текст)}

        assert set(мерки.values()) == {"м", "бр", "кв.м"}

    def test_nulite_ne_vlizat(self):
        """Нула шахти не е количество, а липса на такова."""
        csv_текст = as_csv([Тръба("К", 300, "PP", 0.0)],
                           {"РШ": 0, "СКО": 3}, {"асфалт": 0})

        редове = _редове(csv_текст)
        assert len(редове) == 1 and "СКО" in редове[0]["Описание"]

    def test_red_bez_diametur_ne_minava(self):
        assert not _редове(as_csv([Тръба("К", 0, "PP", 500.0)]))


class TestЗапис:
    def test_pishe_se_v_papkata_na_proekta(self, tmp_path: Path):
        път = save(tmp_path, [Тръба("К", 300, "PP", 1182.0)], {"РШ": 46})

        assert път == tmp_path / ИМЕ_НА_ФАЙЛА and път.exists()
        съдържание = път.read_text(encoding="utf-8-sig")
        assert "DN 300" in съдържание and "Ревизионни шахти" in съдържание

    def test_prezapisva_se_a_ne_trupa_versii(self, tmp_path: Path):
        save(tmp_path, [Тръба("К", 300, "PP", 100.0)])
        save(tmp_path, [Тръба("К", 400, "PP", 200.0)])

        файлове = list(tmp_path.glob("*.csv"))
        assert len(файлове) == 1
        assert "DN 400" in файлове[0].read_text(encoding="utf-8-sig")
        assert "DN 300" not in файлове[0].read_text(encoding="utf-8-sig")

    def test_prazen_vhod_grymva_yasno(self, tmp_path: Path):
        with pytest.raises(ValueError, match="нито едно количество"):
            save(tmp_path, [], {}, {})

        assert not list(tmp_path.glob("*.csv")), (
            "празен файл остана да минава за прочетен вход")
