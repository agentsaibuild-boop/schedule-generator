"""Овърхедът на веригата не расте, когато я разделим на повече пакети.

FAILURE означава: src/segment_scale.py е счупен — или задължителните стъпки без
количество пак взимат медианата за ЕДИН еталонен участък на всеки пакет (тогава
разделянето дублира работа), или мащабирането е плъзнало върху вериги, където
продължителностите идват от договора, не от размера на обекта.

Одит 07.08.2026, P1: „`PhysicalSegment` ≠ `ExecutionBatch`".  Измерено
18.08.2026: 8 → 14 участъка даваше +252 задача-дни само от този механизъм.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.segment_scale import scale_segment_overhead  # noqa: E402

ВЕРИГИ = {
    "chains": {
        "sewer_section": {
            "wbs_root": "construction",
            "observed_count": 46,
            "steps": [
                {"key": "survey", "median_days": 1.0, "min_days": 1.0,
                 "max_days": 4.0, "covers": []},
                {"key": "laying", "median_days": 3.0, "min_days": 1.0,
                 "max_days": 36.0, "covers": ["laying"]},
                {"key": "cctv", "median_days": 1.0, "min_days": 1.0,
                 "max_days": 3.0, "covers": []},
            ],
        },
        "pavement_section": {
            "wbs_root": "construction",
            "observed_count": 0,
            "steps": [{"key": "asphalt", "median_days": 2.0,
                       "covers": ["pavement"]}],
        },
        "design": {
            "wbs_root": "design",
            "observed_count": 1,
            "steps": [{"key": "water", "median_days": 50.0, "covers": []}],
        },
    }
}


class Пакет:
    def __init__(self, pid: str, chain: str) -> None:
        self.id = pid
        self.chain = chain


def _участък(pid: str, сметнати_дни: float) -> list[dict]:
    """Един пакет: доказано полагане + две задължителни стъпки без количество."""
    return [
        {"id": f"{pid}_survey", "parent_id": pid, "chain_step": "survey",
         "duration": 1.0, "duration_source": "chain_template"},
        {"id": f"{pid}_laying", "parent_id": pid, "chain_step": "laying",
         "duration": сметнати_дни, "duration_source": "calculated"},
        {"id": f"{pid}_cctv", "parent_id": pid, "chain_step": "cctv",
         "duration": 1.0, "duration_source": "chain_template"},
    ]


def _прогон(брой_пакети: int, общо_работа: float = 120.0) -> list[dict]:
    """Една и съща работа, разделена на N пакета."""
    задачи: list[dict] = []
    пакети = []
    for i in range(брой_пакети):
        pid = f"P{i}"
        задачи += _участък(pid, общо_работа / брой_пакети)
        пакети.append(Пакет(pid, "sewer_section"))
    scale_segment_overhead(задачи, пакети, ВЕРИГИ)
    return задачи


def _овърхед(задачи: list[dict]) -> float:
    return sum(float(t["duration"]) for t in задачи
               if t["duration_source"] == "chain_template")


# ---------------------------------------------------------------------------
# Същината
# ---------------------------------------------------------------------------


def test_overhead_does_not_grow_with_the_number_of_packages():
    """Разделянето е решение за организация, не нова работа."""
    осем = _овърхед(_прогон(8))
    четиринайсет = _овърхед(_прогон(14))

    # ТОЧНО равни: разпределението е по най-голям остатък, не със закръгляне
    # нагоре на всеки пакет поотделно.  Допуск няма — той би скрил точно
    # дефекта, заради който този файл съществува.
    assert осем == четиринайсет, (
        f"овърхедът расте с разделянето: {осем} → {четиринайсет} дни — "
        "PhysicalSegment пак е същото нещо като ExecutionBatch")


def test_overhead_matches_what_the_human_actually_scheduled():
    """Анкерът е еталонът: 46 участъка × 1 ден survey + 46 × 1 ден CCTV."""
    задачи = _прогон(8)

    очаквано = 46 * 1.0 + 46 * 1.0
    получено = _овърхед(задачи)

    assert получено == очаквано, (
        f"{получено} дни овърхед срещу {очаквано} в еталонния график")


def test_the_old_behaviour_would_fail_this():
    """Пазач: медианата на пакет дава 8×2 = 16 дни вместо 92."""
    assert _овърхед(_прогон(8)) > 8 * 2.0


# ---------------------------------------------------------------------------
# Какво НЕ бива да се пипа
# ---------------------------------------------------------------------------


def test_calculated_durations_are_never_touched():
    """Нормите са единственият източник — мащабирането не ги надписва."""
    задачи = _прогон(4, общо_работа=100.0)

    сметнати = [t for t in задачи if t["duration_source"] == "calculated"]
    assert сметнати
    assert all(t["duration"] == 25.0 for t in сметнати)


def test_contract_chains_keep_their_durations():
    """Проектирането трае колкото договорът казва, не колкото е обектът."""
    задачи = [{"id": "D_water", "parent_id": "D", "chain_step": "water",
               "duration": 50.0, "duration_source": "chain_template"}]

    scale_segment_overhead(задачи, [Пакет("D", "design")], ВЕРИГИ)

    assert задачи[0]["duration"] == 50.0
    assert "segment_share" not in задачи[0]


def test_chain_without_a_reference_count_keeps_the_template():
    """`pavement_section` не е извлечена от еталона — няма спрямо какво."""
    задачи = [{"id": "A_asphalt", "parent_id": "A", "chain_step": "asphalt",
               "duration": 2.0, "duration_source": "chain_template"}]

    _, бележки = scale_segment_overhead(
        задачи, [Пакет("A", "pavement_section")], ВЕРИГИ)

    assert задачи[0]["duration"] == 2.0
    assert any("pavement_section" in b for b in бележки)


def test_no_step_falls_below_one_day():
    """Задача от нула дни е milestone, а стъпката се извършва."""
    задачи = _прогон(200)

    assert all(float(t["duration"]) >= 1
               for t in задачи if t["duration_source"] == "chain_template")


def test_the_number_says_where_it_came_from():
    """Без произход числото е неразличимо от гадаене."""
    пипната = [t for t in _прогон(8)
               if t["duration_source"] == "chain_template"][0]

    assert пипната["template_days"] == 1.0
    assert 0 < пипната["segment_share"] <= 1
    assert пипната["duration_source"] == "chain_template", (
        "произходът трябва да остане `chain_template` — числото пак НЕ е "
        "от нормите и не бива да се чете като доказано")


def test_a_package_without_any_proven_step_still_gets_a_share():
    """Пакет без нито една сметната стъпка се дели поравно, а не изчезва."""
    задачи = [
        {"id": "P0_survey", "parent_id": "P0", "chain_step": "survey",
         "duration": 1.0, "duration_source": "chain_template"},
        {"id": "P1_survey", "parent_id": "P1", "chain_step": "survey",
         "duration": 1.0, "duration_source": "chain_template"},
    ]

    scale_segment_overhead(
        задачи, [Пакет("P0", "sewer_section"), Пакет("P1", "sewer_section")],
        ВЕРИГИ)

    assert [t["duration"] for t in задачи] == [23, 23]


# ---------------------------------------------------------------------------
# Анкерът е еталонът НА МЕТЪР (24.08.2026)
#
# Дотук общата работа по стъпка беше `median × observed_count` — абсолютният
# сбор на ЕДИН обект.  Тоест размерът на НАШИЯ търг не значеше нищо: обект с
# 4000 м и обект с 20 000 м канал получаваха еднакъв овърхед, само нарязан на
# повече парчета.  Еталонът беше зашит в срока на всеки график.
# ---------------------------------------------------------------------------

#: Същата верига, но с измерената дължина на еталона: 4075 м на 46 участъка,
#: тоест 88.6 м на участък.
ВЕРИГИ_С_ДЪЛЖИНА = {
    "chains": {
        "sewer_section": dict(ВЕРИГИ["chains"]["sewer_section"],
                              observed_length_m=4075.0),
    }
}


class Ред:
    def __init__(self, клас: str, количество: float, мярка: str = "м") -> None:
        self.activity_class = клас
        self.quantity = количество
        self.unit = мярка


class ПакетСМетри(Пакет):
    def __init__(self, pid: str, chain: str, items) -> None:
        super().__init__(pid, chain)
        self.items = tuple(items)


def _прогон_с_метри(брой_пакети: int, общо_метри: float,
                    общо_работа: float = 120.0) -> list[dict]:
    задачи: list[dict] = []
    пакети = []
    for i in range(брой_пакети):
        pid = f"M{i}"
        задачи += _участък(pid, общо_работа / брой_пакети)
        пакети.append(ПакетСМетри(pid, "sewer_section",
                                  [Ред("laying", общо_метри / брой_пакети)]))
    scale_segment_overhead(задачи, пакети, ВЕРИГИ_С_ДЪЛЖИНА)
    return задачи


def test_obekt_s_razmera_na_etalona_poluchava_etalonniya_overhed():
    """4075 м = 46 еталонни участъка → 46 дни survey + 46 дни CCTV."""
    получено = _овърхед(_прогон_с_метри(8, 4075.0))

    assert получено == 92.0, (
        f"{получено} дни при обект с точно размера на еталона — очаквани 92")


def test_dvoyno_po_dulug_obekt_poluchava_dvoyno_overhed():
    """Размерът на търга мени срока — това е цялата поправка."""
    еталонен = _овърхед(_прогон_с_метри(8, 4075.0))
    двоен = _овърхед(_прогон_с_метри(8, 8150.0))

    assert двоен == 2 * еталонен, (
        f"{еталонен} → {двоен} дни при двойно повече метри — анкерът пак е "
        "закован за размера на еталона")


def test_polovin_obekt_poluchava_polovin_overhed():
    половин = _овърхед(_прогон_с_метри(8, 2037.5))

    assert половин == 46.0, (
        f"{половин} дни при половин обект — очаквани 46")


def test_razdelyaneto_pak_ne_mie_overheda():
    """Свойството, което пазехме досега, оцелява: метрите не се менят."""
    осем = _овърхед(_прогон_с_метри(8, 6000.0))
    четиринайсет = _овърхед(_прогон_с_метри(14, 6000.0))

    assert осем == четиринайсет, (
        f"овърхедът расте с разделянето: {осем} → {четиринайсет}")


def test_broyat_metri_samo_redovete_za_polagane():
    """Изкопът също е в метри, но е друга работа върху същото трасе."""
    задачи = []
    пакети = []
    for i in range(4):
        pid = f"X{i}"
        задачи += _участък(pid, 30.0)
        пакети.append(ПакетСМетри(pid, "sewer_section", [
            Ред("laying", 1000.0),
            Ред("excavation", 1000.0),     # същите метри, друга дейност
            Ред("backfill", 1000.0),
        ]))
    scale_segment_overhead(задачи, пакети, ВЕРИГИ_С_ДЪЛЖИНА)

    # 4000 м полагане ≈ 45.1 еталонни участъка → 45 + 45 дни.
    assert _овърхед(задачи) == 90.0, (
        f"{_овърхед(задачи)} дни — изкопът и засипката са влезли в дължината")


def test_bez_metri_anker_pada_kum_stariya_i_go_kazva():
    """Верига без линейни редове не може да бъде измерена на метър."""
    задачи = []
    пакети = []
    for i in range(4):
        pid = f"B{i}"
        задачи += _участък(pid, 30.0)
        пакети.append(ПакетСМетри(pid, "sewer_section",
                                  [Ред("manhole", 12.0, "бр")]))

    _, бележки = scale_segment_overhead(задачи, пакети, ВЕРИГИ_С_ДЪЛЖИНА)

    assert _овърхед(задачи) == 92.0, "падането към стария анкер не е запазено"
    assert not any("еталонни участъка" in b for b in бележки), (
        "обявен е мащаб по метри там, където метри няма")


def test_mashtabut_se_obyavyava():
    """Числото, което мени срока, не бива да е невидимо."""
    задачи = []
    пакети = []
    for i in range(4):
        pid = f"S{i}"
        задачи += _участък(pid, 30.0)
        пакети.append(ПакетСМетри(pid, "sewer_section", [Ред("laying", 2000.0)]))

    _, бележки = scale_segment_overhead(задачи, пакети, ВЕРИГИ_С_ДЪЛЖИНА)

    assert any("еталонни участъка" in b and "8000" in b for b in бележки), (
        f"мащабът не е обявен: {бележки}")
