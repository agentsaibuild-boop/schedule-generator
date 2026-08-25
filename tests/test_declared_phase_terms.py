"""Обявеният в документацията срок се използва МАКСИМАЛНО.

Изпълнителят, 24.08.2026: „когато има зададени срокове за проектиране,
строителство и/или други, трябва да се използват максимално.  Ако проектирането
е записано 100 дни, ние в графика го записваме 100 дни."

FAILURE означава: графикът пак ще обещава срок, различен от обявения в
процедурата — по-дълъг (тогава офертата е неотговаряща) или по-кратък (тогава
поемаме риск, без да получаваме нищо срещу него).

Мерено на реален търг (ВиК Русе, тласкател Образцов Чифлик): нашето проектиране
излизаше 124 дни при обявени 30 + 40, а строителството 58 при обявени 180.
Човешкият график за същата поръчка пише точно 30, 40 и 180.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.segment_scale import (  # noqa: E402
    enforce_declared_phase_terms,
    verify_declared_terms,
)

ВЕРИГИ = {
    "chains": {
        "design": {"wbs_root": "design", "observed_count": 1, "steps": []},
        "water_section": {"wbs_root": "construction", "observed_count": 23,
                          "steps": []},
        "supervision": {"wbs_root": "supervision", "observed_count": 1,
                        "steps": []},
    }
}


class Пакет:
    def __init__(self, pid: str, chain: str) -> None:
        self.id = pid
        self.chain = chain


def _задача(pid: str, ключ: str, старт: int, дни: int) -> dict:
    return {"id": f"{pid}_{ключ}", "parent_id": pid, "chain_step": ключ,
            "duration": dни if (dни := дни) else 1,
            "start_day": старт, "end_day": старт + дни - 1}


def _верижка(pid: str, chain: str, старт: int, стъпки: list[int]) -> list[dict]:
    """Стъпки една след друга, без застъпване."""
    задачи, ден = [], старт
    for i, дни in enumerate(стъпки):
        задачи.append(_задача(pid, f"s{i}", ден, дни))
        ден += дни
    return задачи


def _преразпиши(задачи: list[dict]) -> list[dict]:
    """Просто пренареждане: стъпките на пакет вървят една след друга."""
    по_пакет: dict[str, list[dict]] = {}
    for t in задачи:
        по_пакет.setdefault(str(t["parent_id"]), []).append(t)
    for pid, листа in по_пакет.items():
        ден = min(int(t["start_day"]) for t in листа)
        for t in sorted(листа, key=lambda x: x["id"]):
            t["start_day"] = ден
            t["end_day"] = ден + int(t["duration"]) - 1
            ден = t["end_day"] + 1
    return задачи


def _обхват(задачи: list[dict], pid: str) -> int:
    свои = [t for t in задачи if t["parent_id"] == pid]
    return max(t["end_day"] for t in свои) - min(t["start_day"] for t in свои) + 1


class TestСвиване:
    def test_prekaleno_dulgata_faza_se_svива_do_obyavenoto(self, monkeypatch):
        monkeypatch.setenv("DESIGN_DAYS", "70")
        задачи = _верижка("D", "design", 1, [40, 40, 44])   # 124 дни

        задачи, бележки = enforce_declared_phase_terms(
            задачи, [Пакет("D", "design")], ВЕРИГИ, _преразпиши)

        assert _обхват(задачи, "D") == 70, f'{_обхват(задачи, "D")} дни'
        assert any("свита" in б and "70" in б for б in бележки), бележки


class TestРазтягане:
    def test_prekaleno_kratkata_faza_se_raztyaga(self, monkeypatch):
        monkeypatch.setenv("CONSTRUCTION_DAYS", "180")
        задачи = _верижка("В1", "water_section", 1, [20, 20, 18])  # 58 дни

        задачи, бележки = enforce_declared_phase_terms(
            задачи, [Пакет("В1", "water_section")], ВЕРИГИ, _преразпиши)

        assert abs(_обхват(задачи, "В1") - 180) <= 1, _обхват(задачи, "В1")
        assert any("разтеглена" in б for б in бележки), бележки

    def test_smetnatoto_ostava_vidimo(self, monkeypatch):
        monkeypatch.setenv("CONSTRUCTION_DAYS", "180")
        задачи = _верижка("В1", "water_section", 1, [20, 20, 18])

        задачи, _ = enforce_declared_phase_terms(
            задачи, [Пакет("В1", "water_section")], ВЕРИГИ, _преразпиши)

        пипната = задачи[0]
        assert пипната["computed_duration"] == 20, (
            "сметнатата продължителност е затрита — разликата става невидима")
        assert пипната["declared_term_days"] == 180


class TestКаквоНеСеПипа:
    def test_faza_bez_obyaven_srok_ne_se_pipa(self, monkeypatch):
        monkeypatch.delenv("DESIGN_DAYS", raising=False)
        monkeypatch.delenv("CONSTRUCTION_DAYS", raising=False)
        monkeypatch.delenv("CONTRACT_DAYS", raising=False)
        задачи = _верижка("D", "design", 1, [40, 40, 44])

        задачи, бележки = enforce_declared_phase_terms(
            задачи, [Пакет("D", "design")], ВЕРИГИ, _преразпиши)

        assert _обхват(задачи, "D") == 124 and not бележки

    def test_nadzorat_ne_se_pipa(self, monkeypatch):
        # Надзорът се котви за строителството (`enforce_construction_span`);
        # разтегли ли се и той сам, двете правила се бият.
        monkeypatch.setenv("SUPERVISION_DAYS", "500")
        задачи = _верижка("Н", "supervision", 1, [30])

        задачи, бележки = enforce_declared_phase_terms(
            задачи, [Пакет("Н", "supervision")], ВЕРИГИ, _преразпиши)

        assert _обхват(задачи, "Н") == 30 and not бележки

    def test_veche_spazeniyat_srok_ne_se_pipa(self, monkeypatch):
        monkeypatch.setenv("DESIGN_DAYS", "70")
        задачи = _верижка("D", "design", 1, [35, 35])

        задачи, бележки = enforce_declared_phase_terms(
            задачи, [Пакет("D", "design")], ВЕРИГИ, _преразпиши)

        assert _обхват(задачи, "D") == 70
        assert any("вече се спазва" in б for б in бележки), бележки

    def test_bez_prerazpisvane_ne_gurmi(self, monkeypatch):
        monkeypatch.setenv("DESIGN_DAYS", "70")
        задачи = _верижка("D", "design", 1, [40, 40, 44])

        задачи, бележки = enforce_declared_phase_terms(
            задачи, [Пакет("D", "design")], ВЕРИГИ, None)

        assert sum(int(t["duration"]) for t in задачи) < 124, (
            "без преразписване поне продължителностите трябва да са свити")


class TestДоговорниятСрокОстава:
    def test_contract_days_znachi_stroitelstvo(self, monkeypatch):
        # Четвъртият въпрос от въпросника пита точно това и не бива да се губи.
        monkeypatch.delenv("CONSTRUCTION_DAYS", raising=False)
        monkeypatch.setenv("CONTRACT_DAYS", "180")
        задачи = _верижка("В1", "water_section", 1, [20, 20, 18])

        задачи, бележки = enforce_declared_phase_terms(
            задачи, [Пакет("В1", "water_section")], ВЕРИГИ, _преразпиши)

        assert abs(_обхват(задачи, "В1") - 180) <= 1, бележки


class TestСрокътЕТаван:
    """Един ден НАД обявеното прави офертата неотговаряща — допуск няма."""

    @pytest.mark.parametrize("стъпки,цел", [
        ([40, 40, 44], 70),      # закръглянето даваше 71
        ([25, 25, 25], 70),      # излишък 5
        ([30, 30, 30], 89),      # излишък 1
        ([10, 10, 10, 10], 33),  # много кратки стъпки
    ])
    def test_fazata_nikoga_ne_nadhvurlya_obyavenoto(self, monkeypatch,
                                                    стъпки, цел):
        monkeypatch.setenv("DESIGN_DAYS", str(цел))
        задачи = _верижка("D", "design", 1, стъпки)

        задачи, _ = enforce_declared_phase_terms(
            задачи, [Пакет("D", "design")], ВЕРИГИ, _преразпиши)

        стана = _обхват(задачи, "D")
        assert стана <= цел, f"{стана} дни при обявени {цел} — над тавана"
        assert стана >= цел - 1, f"{стана} дни при обявени {цел} — срокът не се използва"

    def test_kogato_ne_moje_da_se_sberе_kazva_go(self, monkeypatch):
        # Пет стъпки по един ден не могат да станат три дни: стъпка, която се
        # извършва, не трае нула.
        monkeypatch.setenv("DESIGN_DAYS", "3")
        задачи = _верижка("D", "design", 1, [1, 1, 1, 1, 1])

        задачи, бележки = enforce_declared_phase_terms(
            задачи, [Пакет("D", "design")], ВЕРИГИ, _преразпиши)

        assert _обхват(задачи, "D") == 5
        assert any("събра точно" in б for б in бележки), бележки


class TestГаранцията:
    """Не късмет на последната итерация, а върнато най-добро съвместимо."""

    def test_koga_iteraciite_svarshvat_nad_tavana_se_vrushtame(self, monkeypatch):
        # Мерено на контролния прогон (Илиянци, 24.08.2026): строителството се
        # разтягаше 591 → 782 при обявени 780 и спираше ДВА ДНИ НАД тавана.
        monkeypatch.setenv("CONSTRUCTION_DAYS", "780")
        задачи = _верижка("В1", "water_section", 1, [200, 200, 191])  # 591

        задачи, бележки = enforce_declared_phase_terms(
            задачи, [Пакет("В1", "water_section")], ВЕРИГИ, _преразпиши,
            опити=3)

        стана = _обхват(задачи, "В1")
        assert стана <= 780, f"{стана} дни при обявени 780 — над тавана"
        assert стана >= 770, f"{стана} дни — срокът не се използва"

    def test_edin_opit_ne_ostavya_faza_nad_tavana(self, monkeypatch):
        monkeypatch.setenv("DESIGN_DAYS", "70")
        задачи = _верижка("D", "design", 1, [40, 40, 44])

        задачи, _ = enforce_declared_phase_terms(
            задачи, [Пакет("D", "design")], ВЕРИГИ, _преразпиши, опити=1)

        assert _обхват(задачи, "D") <= 70


class TestКаквоПокриваСрокътЗаСМР:
    """Мобилизацията и приемането са ВЪТРЕ в обявения строителен срок.

    ПРОВЕРЕНО В ДВАТА ЧОВЕШКИ ГРАФИКА (24.08.2026):
      Илиянци — „СТРОИТЕЛСТВО" (03.02.2026–24.11.2027) съдържа откриването на
        площадката, мобилизацията, участъците, окончателното приключване на
        СМР, демобилизацията и констативния акт;
      Русе — „ЕТАП 4" е точно обявените 180 дни и в него са мобилизацията
        (дни 72–76), участъците, изпитването, приключването и актът (ден 250).
    """

    ВЕРИГИ_С_ФАЗИ = {
        "chains": {
            "water_section": {"wbs_root": "construction", "steps": []},
            "mobilization": {"wbs_root": "mobilization", "steps": []},
            "acceptance": {"wbs_root": "acceptance", "steps": []},
        }
    }

    def _обект(self):
        задачи = (_верижка("М", "mobilization", 1, [10])
                  + _верижка("В1", "water_section", 11, [20, 20, 18])
                  + _верижка("П", "acceptance", 69, [12]))
        пакети = [Пакет("М", "mobilization"), Пакет("В1", "water_section"),
                  Пакет("П", "acceptance")]
        return задачи, пакети

    def _цял_обхват(self, задачи):
        return (max(t["end_day"] for t in задачи)
                - min(t["start_day"] for t in задачи) + 1)

    def test_srokat_pokriva_mobilizaciya_stroitelstvo_i_priemane(self, monkeypatch):
        monkeypatch.setenv("CONSTRUCTION_DAYS", "180")
        задачи, пакети = self._обект()
        assert self._цял_обхват(задачи) == 80

        задачи, бележки = enforce_declared_phase_terms(
            задачи, пакети, self.ВЕРИГИ_С_ФАЗИ, _преразпиши)

        стана = self._цял_обхват(задачи)
        assert 175 <= стана <= 180, (
            f"{стана} дни — мобилизацията и приемането пак стоят извън срока")
        assert any("construction" in б for б in бележки), бележки

    def test_faza_sas_svoy_srok_izliza_ot_grupata(self, monkeypatch):
        monkeypatch.setenv("CONSTRUCTION_DAYS", "180")
        monkeypatch.setenv("ACCEPTANCE_DAYS", "30")
        задачи, пакети = self._обект()

        задачи, _ = enforce_declared_phase_terms(
            задачи, пакети, self.ВЕРИГИ_С_ФАЗИ, _преразпиши)

        приемане = [t for t in задачи if t["parent_id"] == "П"]
        обхват = (max(t["end_day"] for t in приемане)
                  - min(t["start_day"] for t in приемане) + 1)
        assert 29 <= обхват <= 30, (
            f"приемането е {обхват} дни при свой обявен срок 30")


class TestНеотменимо:
    """График над обявения срок НЕ излиза мълчаливо."""

    def test_narushenieto_se_dokladva(self, monkeypatch):
        monkeypatch.setenv("DESIGN_DAYS", "70")
        задачи = _верижка("D", "design", 1, [40, 40, 44])   # 124 дни

        нарушения = verify_declared_terms(
            задачи, [Пакет("D", "design")], ВЕРИГИ)

        assert нарушения and "70" in нарушения[0] and "124" in нарушения[0]
        assert "МАКСИМУМ" in нарушения[0], нарушения

    def test_spazeniyat_srok_ne_se_doklad(self, monkeypatch):
        monkeypatch.setenv("DESIGN_DAYS", "70")
        задачи = _верижка("D", "design", 1, [35, 35])

        assert not verify_declared_terms(
            задачи, [Пакет("D", "design")], ВЕРИГИ)

    def test_milestone_ite_se_broyat_v_obhvata(self, monkeypatch):
        # Съгласувателните milestone-и стоят СЛЕД работната част и всеки заема
        # ден.  Мереше се само работата и графикът излизаше 124 при „спазени"
        # 120 — тоест правилото рапортуваше едно, а файлът показваше друго.
        monkeypatch.setenv("DESIGN_DAYS", "70")
        задачи = _верижка("D", "design", 1, [35, 34])
        задачи.append({"id": "D_m1", "parent_id": "D", "chain_step": "approve",
                       "duration": 0, "milestone": True,
                       "start_day": 70, "end_day": 70})
        задачи.append({"id": "D_m2", "parent_id": "D", "chain_step": "approve",
                       "duration": 0, "milestone": True,
                       "start_day": 71, "end_day": 71})

        нарушения = verify_declared_terms(
            задачи, [Пакет("D", "design")], ВЕРИГИ)

        assert нарушения, "milestone-ите извън тавана не се броят"

    def test_sboryat_na_srokovete_sasho_e_tavan(self, monkeypatch):
        monkeypatch.setenv("DESIGN_DAYS", "70")
        monkeypatch.setenv("CONSTRUCTION_DAYS", "100")
        задачи = (_верижка("D", "design", 1, [35, 35])
                  + _верижка("В1", "water_section", 71, [50, 49]))

        нарушения = verify_declared_terms(
            задачи, [Пакет("D", "design"), Пакет("В1", "water_section")],
            ВЕРИГИ)

        assert not нарушения, нарушения

        # Същите фази, но строителството е с 30 дни по-дълго.
        задачи = (_верижка("D", "design", 1, [35, 35])
                  + _верижка("В1", "water_section", 71, [65, 65]))
        нарушения = verify_declared_terms(
            задачи, [Пакет("D", "design"), Пакет("В1", "water_section")],
            ВЕРИГИ)
        assert any("Целият график" in н for н in нарушения), нарушения


class TestСвиванетоНеПрескача:
    """Свиването не бива да пада далеч ПОД тавана — това е другата половина.

    Закръглянето НАДОЛУ на всяка стъпка изглежда безопасно, но е тъпо острие:
    при множител 0.74 задача от 4 дни става 2.  Мерено на Русе — фаза от 243
    дни при таван 180 падаше на 155, тоест 25 дни неизползван срок.
    """

    def test_ne_pada_daleche_pod_tavana(self, monkeypatch):
        monkeypatch.setenv("DESIGN_DAYS", "180")
        задачи = _верижка("D", "design", 1, [80, 80, 84])   # 244 дни

        задачи, _ = enforce_declared_phase_terms(
            задачи, [Пакет("D", "design")], ВЕРИГИ, _преразпиши)

        стана = _обхват(задачи, "D")
        assert стана <= 180, f"{стана} дни — над тавана"
        assert стана >= 175, (
            f"{стана} дни при таван 180 — срокът не се използва, свиването "
            "прескача надолу")

    def test_i_pri_kratki_stapki_ne_preskacha(self, monkeypatch):
        monkeypatch.setenv("DESIGN_DAYS", "60")
        задачи = _верижка("D", "design", 1, [4] * 25)       # 100 дни

        задачи, _ = enforce_declared_phase_terms(
            задачи, [Пакет("D", "design")], ВЕРИГИ, _преразпиши)

        стана = _обхват(задачи, "D")
        assert 57 <= стана <= 60, f"{стана} дни при таван 60"


class TestРазтяганеПриЕдноденниСтъпки:
    """Умножението не мърда стъпки от по един ден — а след свиване те са точно такива.

    Мерено на Русе (25.08.2026): строителството оставаше 155 дни при таван 180,
    защото `1 × 1.16` се закръгля обратно на 1.  Двайсет и пет неизползвани дни
    при правило „обявеният срок се използва максимално".
    """

    def test_edinodenni_stapki_se_raztyagat(self, monkeypatch):
        monkeypatch.setenv("DESIGN_DAYS", "40")
        задачи = _верижка("D", "design", 1, [1] * 20)       # 20 дни

        задачи, _ = enforce_declared_phase_terms(
            задачи, [Пакет("D", "design")], ВЕРИГИ, _преразпиши)

        стана = _обхват(задачи, "D")
        assert 39 <= стана <= 40, (
            f"{стана} дни при обявени 40 — едноденните стъпки не се разтягат")

    def test_smesica_ot_kratki_i_dulgi(self, monkeypatch):
        monkeypatch.setenv("DESIGN_DAYS", "100")
        задачи = _верижка("D", "design", 1, [1, 1, 1, 12, 1, 1, 20, 1])  # 38

        задачи, _ = enforce_declared_phase_terms(
            задачи, [Пакет("D", "design")], ВЕРИГИ, _преразпиши)

        стана = _обхват(задачи, "D")
        assert 99 <= стана <= 100, f"{стана} дни при обявени 100"
