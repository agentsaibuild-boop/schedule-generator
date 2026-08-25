"""Производителността идва от СРОКА и ЕКИПИТЕ, а не от норми.

Изпълнителят, 24.08.2026: „От сега напред приемаш, че НЯМА НОРМИ за полагане на
тръби… приемаш крайния срок за изпълнение за даденост и задаваш въпрос: с колко
екипа ще се работи, и после: паралелно ли ще работят.  След като имаш тази
информация, изчисляваш на база срока и броя на екипите каква е
производителността и разпределяш по дните."

FAILURE означава: продължителностите пак ще идват от норми, които никой не е
потвърдил — 119 от 189 стойности са от ЕДИН обект, а за 29% от диаметрите в
реален търг норма изобщо няма и срокът става недоказуем.

Числото, което това правило произвежда, НЕ Е норма и графикът трябва да го
казва: то е следствие от обявения срок.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.deadline_pace import derive  # noqa: E402


class Ред:
    def __init__(self, метри: float) -> None:
        self.activity_class = "laying"
        self.quantity = метри
        self.unit = "м"
        #: Калибрирането брои метрите ПРЕЗ цитата към количествения ред.
        self.source_ref = "СПЕЦ!1"


class Пакет:
    def __init__(self, pid: str, верига: str, метри: float) -> None:
        self.id = pid
        self.chain = верига
        self.items = (Ред(метри),)


def _обект(верига: str = "water_section", метри: float = 1268.0):
    return [Пакет("P1", верига, метри / 2), Пакет("P2", верига, метри / 2)]


class TestИзвежданеОтСрока:
    def test_edin_ekip_e_metri_delено_na_dni(self):
        темпа, _ = derive(_обект(), days=180, crews=1, parallel=True)

        assert round(темпа["water_section"], 2) == round(1268 / 180, 2)

    def test_dva_ekipa_paralelno_udvoyavat_tempoto_na_obekta(self):
        # Два екипа в 180 дни значат 360 екипо-дни: всеки кара по-бавно, а
        # обектът се събира в същия срок.
        един, _ = derive(_обект(), days=180, crews=1, parallel=True)
        два, _ = derive(_обект(), days=180, crews=2, parallel=True)

        assert round(два["water_section"] * 2, 4) == round(един["water_section"], 4)

    def test_posledovatelno_broyat_ekipi_ne_menи_nishto(self):
        един, _ = derive(_обект(), days=180, crews=1, parallel=False)
        три, _ = derive(_обект(), days=180, crews=3, parallel=False)

        assert един == три, (
            "при последователна работа вторият екип чака първия — броят им "
            "не бива да мени темпото")

    def test_tempoto_e_po_veriga(self):
        пакети = _обект("sewer_section", 4075) + _обект("water_section", 3247)

        темпа, _ = derive(пакети, days=660, crews=2, parallel=True)

        assert set(темпа) == {"sewer_section", "water_section"}
        assert темпа["sewer_section"] > темпа["water_section"]


class TestЧислотоСеОбявява:
    def test_belezhkata_kazva_che_ne_e_norma(self):
        _, бележки = derive(_обект(), days=180, crews=2, parallel=True)

        текст = " ".join(бележки)
        assert "ИЗВЕДЕНО ОТ СРОКА" in текст and "не норма" in текст, бележки
        assert "1268" in текст and "180" in текст, бележки

    def test_posledovatelnoto_se_kazva_izrichno(self):
        _, бележки = derive(_обект(), days=180, crews=3, parallel=False)

        assert any("последователно" in б for б in бележки), бележки


class TestКогаНеСеПрилага:
    def test_bez_srok_nyama_tempo(self):
        темпа, бележки = derive(_обект(), days=0, crews=2)

        assert темпа == {} and бележки == []

    def test_bez_metri_nyama_tempo(self):
        темпа, _ = derive(_обект(метри=0.0), days=180, crews=2)

        assert темпа == {}

    def test_flagat_izklyuchva_praviloto(self, monkeypatch):
        monkeypatch.setenv("PACE_FROM_DEADLINE", "0")

        темпа, _ = derive(_обект(), days=180, crews=2)

        assert темпа == {}, "флагът не изключва — нормите няма как да се сверят"

    def test_ne_se_pipat_neлинейни_verigi(self):
        темпа, _ = derive(_обект("pavement_section", 5000), days=180, crews=2)

        assert темпа == {}, (
            "настилките не се мерят на метър тръба — темпото им не идва оттук")


class TestТемпотоРешаваПродължителностите:
    """Изведеното темпо трябва да МЕНИ веригата, не само да се обявява."""

    def test_tempoto_razpravya_verigata_nagore(self):
        from src.segment_scale import calibrate_to_declared_pace

        # 1268 м при 3.52 м/ден на екип искат 360 екипо-дни.  Веригата е 108 —
        # мерено на Русе, точно този случай оставаше непроменен.
        задачи = [{"id": f"t{i}", "parent_id": "P1", "chain_step": f"s{i}",
                   "duration": 12.0} for i in range(9)]
        пакети = [Пакет("P1", "water_section", 1268.0)]

        class Ред2:
            ref = "СПЕЦ!1"
            quantity = 1268.0
            unit = "м"

        задачи, бележки = calibrate_to_declared_pace(
            задачи, пакети, [Ред2()], overrides={"water_section": 3.52})

        сбор = sum(t["duration"] for t in задачи)
        assert сбор > 300, f"веригата остана {сбор} екипо-дни вместо ~360"
        assert any("искат 360" in б for б in бележки), бележки

    def test_smetnatoto_ostava_vidimo_i_pri_razpravyane(self):
        from src.segment_scale import calibrate_to_declared_pace

        задачи = [{"id": "t0", "parent_id": "P1", "chain_step": "s0",
                   "duration": 12.0}]
        пакети = [Пакет("P1", "water_section", 1268.0)]

        class Ред2:
            ref = "СПЕЦ!1"
            quantity = 1268.0
            unit = "м"

        задачи, _ = calibrate_to_declared_pace(
            задачи, пакети, [Ред2()], overrides={"water_section": 3.52})

        assert задачи[0]["computed_duration"] == 12.0
        assert задачи[0]["declared_pace"] == 3.52


class TestПоследователнатаРаботаМениГрафика:
    """Вторият въпрос мени ПОДРЕДБАТА, не само сметката за темпото."""

    ВЕРИГИ = {"chains": {"water_section": {"wbs_root": "construction", "steps": [
        {"key": "survey"}, {"key": "excavation"}, {"key": "laying"}]}}}

    def _обект(self):
        задачи, пакети = [], []
        for i in (1, 2):
            pid = f"P{i}"
            for стъпка in ("survey", "excavation", "laying"):
                задачи.append({"id": f"{pid}_{стъпка}", "parent_id": pid,
                               "chain_step": стъпка, "duration": 3,
                               "dependencies": []})
            пакети.append(Пакет(pid, "water_section", 500.0))
            пакети[-1].front = "Екип В1"
        return задачи, пакети

    def test_vtoriyat_uchastak_chaka_parviya(self, monkeypatch):
        from src.work_package import chain_sections_sequentially

        monkeypatch.setenv("TEAMS_PARALLEL", "0")
        задачи, пакети = self._обект()

        задачи, бележки = chain_sections_sequentially(задачи, пакети, self.ВЕРИГИ)

        първата_на_втория = [t for t in задачи
                             if t["id"] == "P2_survey"][0]
        връзки = [d["predecessor_id"] for d in първата_на_втория["dependencies"]]
        assert "P1_laying" in връзки, (
            "вторият участък не чака ПОСЛЕДНАТА стъпка на първия — "
            f"{връзки}")
        assert any("Последователна" in б for б in бележки), бележки

    def test_redat_e_na_verigata_a_ne_na_datite(self, monkeypatch):
        # Дати още няма, когато правилото се прилага.  Подреждането по
        # `start_day` връзваше произволни стъпки и участъците пак тръгваха
        # заедно — мерено на Русе.
        from src.work_package import chain_sections_sequentially

        monkeypatch.setenv("TEAMS_PARALLEL", "0")
        задачи, пакети = self._обект()
        for t in задачи:
            t.pop("start_day", None)

        задачи, _ = chain_sections_sequentially(задачи, пакети, self.ВЕРИГИ)

        втори = {t["id"]: t for t in задачи if t["parent_id"] == "P2"}
        assert not втори["P2_excavation"]["dependencies"], (
            "вързана е грешна стъпка — редицата е между участъци, не вътре")

    def test_paralelno_ne_vrazva_nishto(self, monkeypatch):
        from src.work_package import chain_sections_sequentially

        monkeypatch.setenv("TEAMS_PARALLEL", "1")
        задачи, пакети = self._обект()

        задачи, бележки = chain_sections_sequentially(задачи, пакети, self.ВЕРИГИ)

        assert not бележки
        assert all(not t["dependencies"] for t in задачи)
