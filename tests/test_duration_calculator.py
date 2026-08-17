"""Unit tests for src/duration_calculator.py — детерминистични продължителности.

Covers: нормализация на DN/материал/метод, търсене на производителност,
        параметрична продължителност по дължина, бройки (СРС/РШ),
        дезинфекция (урок #33), Долноград tier lookup (урок #45),
        и golden values от ACCURACY.md (Среднево, Горноград, Опитно, Долноград).

FAILURE означава: src/duration_calculator.py е счупен — продължителностите
в генерирания график ще се върнат към стойностите, които LLM-ът си измисля
(P2 от REVISION_2026-07.md).  Това е точно причината за отклоненията в
точността: чугун, сметнат по тарифа за PE, дава занижен график (урок #35).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.duration_calculator import (
    COUNT_RATES,
    DEFAULT_MIN_DAYS,
    WATER_TEST_DAYS,
    calculate_task_duration,
    count_duration,
    detect_dn,
    detect_length_m,
    detect_material,
    detect_method,
    detect_terrain,
    disinfection_days,
    is_pipe_task,
    load_productivities,
    normalize_dn,
    normalize_homoglyphs,
    pipe_duration,
    resolve_rate,
    terrain_factor,
    vratsa_tier_days,
)


@pytest.fixture()
def config() -> dict:
    """Реалният config/productivities.json — тестваме срещу верифицираните данни."""
    return load_productivities()


# ===================================================================
# normalize_dn / detect_dn
# ===================================================================

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (300, 300),
        (300.0, 300),
        ("300", 300),
        ("DN300", 300),
        ("DN 300", 300),
        ("dn-300", 300),
        # „Ф" е стандартната българска нотация за диаметър в КСС (жив тест 2026-08)
        ("Ф300", 300),
        ("Ф 400", 400),
        ("Ф1000, РP", 1000),
        ("Ф90; РЕ", 90),
        ("ф110", 110),
        ("Ø200", 200),
        ("Полагане DN110 PE — ул. Витоша", 110),
        (None, None),
        ("", None),
        ("без диаметър", None),
        (0, None),
        (True, None),  # bool не е DN
    ],
)
def test_normalize_dn(value, expected):
    assert normalize_dn(value) == expected


def test_detect_dn_prefers_diameter_field():
    assert detect_dn({"diameter": 500, "name": "Полагане DN110"}) == 500


def test_detect_dn_accepts_dn_field():
    """Промптът иска 'dn', schedule_builder чете 'diameter' — приемаме и двете."""
    assert detect_dn({"dn": "DN160", "name": "Полагане"}) == 160


def test_detect_dn_falls_back_to_name():
    assert detect_dn({"name": "Полагане DN315 PVC — Клон 4"}) == 315


def test_detect_dn_missing_returns_none():
    assert detect_dn({"name": "Мобилизация"}) is None


# ===================================================================
# detect_material — урок #35 (CI ≠ PE)
# ===================================================================

@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Полагане DN300 CI — горски участък", "CI"),
        ("Полагане тръби чугун DN300", "CI"),
        ("Полагане ковък чугун DN300", "CI"),
        ("Полагане DN110 PE 100 RC", "PE"),
        ("Полагане полиетилен DN90", "PE"),
        ("Полагане DN315 PVC", "PVC"),
        ("Полагане DN315 ПВЦ", "PVC"),
        ("Полагане азбестоциментова тръба", "AC"),
        # PP (полипропилен) — стандартен за канализация (жив тест 2026-08)
        ("Полагане DN300 PP", "PP"),
        ("Полагане тръби полипропилен DN500", "PP"),
        ("Полагане DN500", None),
        ("Изкоп за траншея", None),
    ],
)
def test_detect_material(name, expected):
    assert detect_material({"name": name}) == expected


def test_detect_material_pp_from_cyrillic_diameter_cell():
    """КСС слага материала в диаметърната клетка: „Ф300, РP" (кирилско Р).

    normalize_homoglyphs → „PP"; detect_material гледа и полето diameter.
    Без това цялата канализационна мрежа оставаше MISSING_MATERIAL.
    """
    assert detect_material({"name": "Изграждане на канализация",
                            "diameter": "Ф300, РP"}) == "PP"


def test_detect_material_pe_from_diameter_cell():
    assert detect_material({"name": "Водопроводна мрежа",
                            "diameter": "Ф90; РЕ"}) == "PE"


def test_detect_material_explicit_field_wins_over_silence():
    assert detect_material({"material": "CI", "name": "Полагане DN300"}) == "CI"


def test_detect_material_never_guesses():
    """Без маркер за материал → None, не 'PE по подразбиране' (урок #35)."""
    assert detect_material({"name": "Полагане тръби DN300 — ул. Х"}) is None


# --- OCR homoglyph устойчивост (наблюдавано при жив тест 2026-07-22) ---

def test_detect_material_survives_cyrillic_pe_from_ocr():
    """OCR връща „РЕ" с кирилски букви — материалът пак трябва да е PE.

    Без това една сгрешена буква изключва детерминистичното изчисление.
    """
    assert detect_material({"name": "Доставка и полагане РЕ 100 RC DN110"}) == "PE"


def test_detect_material_survives_cyrillic_ci_from_ocr():
    assert detect_material({"name": "Полагане СI тръби DN300"}) == "CI"


def test_normalize_homoglyphs_converts_pure_lookalike_tokens():
    assert normalize_homoglyphs("РЕ 100 RC") == "PE 100 RC"


def test_normalize_homoglyphs_leaves_real_bulgarian_words_alone():
    """Думи с небуквени двойници не се пипат — иначе се чупи текстът."""
    text = "Разваляне на асфалтова настилка"
    assert normalize_homoglyphs(text) == text


def test_normalize_homoglyphs_does_not_break_material_free_names():
    assert detect_material({"name": "Разваляне на асфалтова настилка"}) is None


def test_homoglyph_normalization_does_not_invent_material():
    """СРС е изцяло от двойници (→ CPC), но не бива да стане материал."""
    assert detect_material({"name": "Монтаж СРС"}) is None


# ===================================================================
# detect_method / detect_terrain
# ===================================================================

@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Безизкопно полагане DN90", "HDD"),
        ("HDD сондаж под пътя", "HDD"),
        ("Сондиране DN110", "HDD"),
        ("Полагане DN110 открит изкоп", "open"),
        ("Полагане DN110", "open"),
    ],
)
def test_detect_method(name, expected):
    assert detect_method({"name": name}) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Полагане DN300 — горски участък", "forest"),
        ("Полагане DN300 — скален терен", "forest"),
        ("Възстановяване на асфалтова настилка", "asphalt"),
        ("Полагане DN110 — черен път", "dirt_road"),
    ],
)
def test_detect_terrain(name, expected):
    assert detect_terrain({"name": name}) == expected


# ===================================================================
# resolve_rate — ЕФЕКТИВНА производителност (урок #16)
# ===================================================================

def test_resolve_rate_reads_effective_not_drill_rate(config):
    """Урок #16/#32: HDD DN90 = 12 м/д ефективна, НЕ 56 м/д пробивна."""
    lookup = resolve_rate(90, "PE", "HDD", config=config)
    assert lookup is not None
    assert lookup.rate == 12
    assert lookup.source == "config"
    assert config["productivities"]["DN90_PE_HDD"]["drill_rate"] == 56


def test_resolve_rate_ci_vs_pe_are_different_entries(config):
    """Урок #35: DN300 CI и DN500 PE не са взаимозаменяеми."""
    ci = resolve_rate(300, "CI", "open", config=config)
    pe = resolve_rate(500, "PE", "open", config=config)
    assert ci is not None and pe is not None
    assert ci.rate == 8
    assert pe.rate == 15


def test_resolve_rate_dn300_pe_has_no_entry(config):
    """DN300 PE няма норма → None, вместо да заеме тази на DN300 CI."""
    assert resolve_rate(300, "PE", "open", config=config) is None


def test_resolve_rate_materialless_key(config):
    """DN630_open няма материал в ключа — намира се и без материал."""
    lookup = resolve_rate(630, None, "open", config=config)
    assert lookup is not None
    assert lookup.rate == 9


@pytest.mark.parametrize(("dn", "expected"), [(90, 12), (110, 13), (160, 14)])
def test_resolve_rate_open_cut_pe_from_config(dn, expected, config):
    """Откритите PE норми са в конфига (слети от productivities.md, 2026-07-22)."""
    lookup = resolve_rate(dn, "PE", "open", config=config)
    assert lookup is not None
    assert lookup.rate == expected
    assert lookup.source == "config"


def test_resolve_rate_has_no_source_outside_config():
    """Единствен източник на истина: празен конфиг → нищо не се намира."""
    assert resolve_rate(90, "PE", "open", config={"productivities": {}}) is None


def test_config_open_and_hdd_rates_are_separate_entries(config):
    """DN90 открит и DN90 HDD са различни ключове, макар нормите да съвпадат."""
    rates = config["productivities"]
    assert rates["DN90_PE_open"]["dig_rate"] == 57.6
    assert rates["DN90_PE_HDD"]["drill_rate"] == 56


def test_resolve_rate_unknown_dn_returns_none(config):
    assert resolve_rate(1200, "PE", "open", config=config) is None


def test_resolve_rate_no_silent_neighbour_approximation(config):
    """DN400 PE не заема нормата на DN400 PVC — тихото приближение е забранено."""
    assert resolve_rate(400, "PE", "open", config=config) is None


# --- PP канализация v0.5 (полево правило: Ф300-800=12, Ф>800=6, 2026-08) ---

@pytest.mark.parametrize("dn", [300, 400, 500, 600, 700, 800])
def test_resolve_rate_pp_small_dn_is_12(dn, config):
    """Ф300–Ф800 PP → 12 м/ден (полево правило на изпълнителя)."""
    lookup = resolve_rate(dn, "PP", "open", config=config)
    assert lookup is not None
    assert lookup.rate == 12
    assert lookup.key == f"DN{dn}_PP_open"


@pytest.mark.parametrize("dn", [1000, 1200])
def test_resolve_rate_pp_large_dn_is_6(dn, config):
    """Ф>800 PP → 6 м/ден (колекторите вървят по-бавно)."""
    lookup = resolve_rate(dn, "PP", "open", config=config)
    assert lookup is not None
    assert lookup.rate == 6


def test_resolve_rate_pp_does_not_borrow_pvc(config):
    """PP не заема PVC нормата и обратно — различни материали, различни ключове."""
    assert resolve_rate(315, "PP", "open", config=config) is None  # PP DN315 няма
    assert resolve_rate(300, "PVC", "open", config=config) is None  # PVC DN300 няма


@pytest.mark.parametrize("dn", [200, 225, 250, 280, 315, 350])
def test_resolve_rate_pe_mid_dn_is_40(dn, config):
    """PE Ф200–350 → 40 м/ден (полево правило на изпълнителя, 2026-08)."""
    lookup = resolve_rate(dn, "PE", "open", config=config)
    assert lookup is not None
    assert lookup.rate == 40
    assert lookup.key == f"DN{dn}_PE_open"


def test_calculate_pp_canalization_end_to_end():
    """Реален канализационен ред „Ф1000, РP" се смята детерминистично."""
    task = {"name": "Изграждане на смесена канализационна мрежа",
            "diameter": "Ф1000, РP", "length_m": 300, "unit": "m"}
    res = calculate_task_duration(task)
    assert res.code == "CALCULATED"
    assert res.days == 50  # 300м ÷ 6 м/ден


# --- Сградни отклонения СВО/СКО = 4 бр/ден (полево, 2026-08) ---

def test_svo_count_duration():
    task = {"name": "СВО", "quantity": 174, "unit": "бр"}
    res = calculate_task_duration(task)
    assert res.code == "CALCULATED"
    assert res.days == 44  # ceil(174 / 4)
    assert res.rate == 4


def test_sko_count_duration():
    task = {"name": "СКО смесена канализационна мрежа", "quantity": 180, "unit": "бр"}
    res = calculate_task_duration(task)
    assert res.code == "CALCULATED"
    assert res.days == 45  # 180 / 4


@pytest.mark.parametrize("unit", ["бр", "бр.", "брой", "броя", "бройки"])
def test_count_unit_variants_all_recognized(unit):
    """Реалните КСС ползват „брой" (не само „броя"/„бр") — трябва да минава."""
    task = {"name": "СКО", "quantity": 180, "unit": unit}
    res = calculate_task_duration(task)
    assert res.code == "CALCULATED", f"unit={unit!r} не се разпозна като бройка"


def test_unknown_count_still_has_no_rate():
    """Водомерна шахта не е СРС/РШ/СКО/СВО → остава COUNT_NO_RATE (не се гади)."""
    task = {"name": "Водомерна шахта", "quantity": 1, "unit": "бр"}
    res = calculate_task_duration(task)
    assert res.code == "COUNT_NO_RATE"
    assert res.days is None


# --- Площни настилки (кв.м) + линейни бордюри (v0.5, полево 2026-08) ---

def test_asphalt_restoration_area():
    task = {"name": "Пътна - възстановяване на пътна настилка (асфалтова настилка)",
            "unit": "кв. м", "quantity": 500}
    res = calculate_task_duration(task)
    assert res.code == "CALCULATED"
    assert res.days == 4  # ceil(500 / 150)
    assert res.rate == 150


def test_pavers_area_distinct_from_asphalt():
    """Тротоарни плочи (унипаваж) → 80 м²/д, НЕ асфалтовата норма."""
    task = {"name": "Доставка и полагане на тротоарни плочи (унипаваж)",
            "unit": "кв. м", "quantity": 240}
    res = calculate_task_duration(task)
    assert res.code == "CALCULATED"
    assert res.days == 3  # ceil(240 / 80)
    assert res.rate == 80
    assert res.rate_key == "pavers_unipavage"


def test_road_kerb_linear():
    task = {"name": "Доставка и полагане на средни бетонови бордюри С18 15/25/50 см",
            "unit": "м", "quantity": 300}
    res = calculate_task_duration(task)
    assert res.code == "CALCULATED"
    assert res.days == 15  # 300 / 20
    assert res.rate == 20
    assert res.rate_key == "kerb_road"


def test_kerb_is_not_treated_as_pipe():
    """Бордюр е линеен, но НЕ тръба — не бива да търси DN/материал."""
    task = {"name": "Бетонови бордюри", "unit": "м", "quantity": 100}
    res = calculate_task_duration(task)
    assert res.rate_key == "kerb_road"  # хванат от линейния клон, не тръбния


def test_area_without_norm_is_not_parametric():
    """Непозната площна дейност (нито асфалт, нито плочи) → NOT_PARAMETRIC."""
    task = {"name": "Затревяване на площи", "unit": "кв. м", "quantity": 200}
    res = calculate_task_duration(task)
    assert res.code == "NOT_PARAMETRIC"
    assert res.days is None


# ===================================================================
# terrain_factor (урок #17)
# ===================================================================

@pytest.mark.parametrize(
    ("terrain", "expected"),
    [("asphalt", 0.75), ("dirt_road", 1.0), ("forest", 0.6), ("непознат", 1.0)],
)
def test_terrain_factor(terrain, expected, config):
    assert terrain_factor(terrain, config=config) == expected


# ===================================================================
# pipe_duration
# ===================================================================

def test_pipe_duration_ceils():
    assert pipe_duration(100, 12, min_days=1) == 9  # 8.33 → 9


def test_pipe_duration_exact_division_does_not_round_up():
    assert pipe_duration(120, 12, min_days=1) == 10


def test_pipe_duration_applies_minimum():
    assert pipe_duration(10, 12, min_days=DEFAULT_MIN_DAYS) == DEFAULT_MIN_DAYS


def test_pipe_duration_terrain_factor_slows_down():
    """Коефициент < 1 намалява скоростта → повече дни."""
    assert pipe_duration(600, 10, factor=0.6, min_days=1) == 100


def test_pipe_duration_default_does_not_apply_terrain():
    assert pipe_duration(600, 10, min_days=1) == 60


@pytest.mark.parametrize(("length", "rate"), [(0, 12), (-5, 12), (100, 0), (100, -1)])
def test_pipe_duration_rejects_non_positive(length, rate):
    with pytest.raises(ValueError):
        pipe_duration(length, rate)


# ===================================================================
# count_duration — СРС / РШ
# ===================================================================

def test_count_duration_srs_prompt_example():
    """Примерът от промпта: 526 бр. СРС ÷ 5 бр./ден = 106 дни."""
    assert count_duration(526, COUNT_RATES["srs"]) == 106


def test_count_duration_rsh_is_slower_than_srs():
    assert count_duration(100, COUNT_RATES["rsh"]) == 50
    assert count_duration(100, COUNT_RATES["srs"]) == 20


def test_count_duration_rejects_non_positive():
    with pytest.raises(ValueError):
        count_duration(0, 5)


# ===================================================================
# disinfection_days — урок #33
# ===================================================================

def test_disinfection_dn300_ci_forest_is_six_days(config):
    result = disinfection_days(300, "CI", terrain="forest", config=config)
    assert result.days == 6


def test_disinfection_dn500_pe_is_four_days(config):
    assert disinfection_days(500, "PE", config=config).days == 4


def test_disinfection_short_pe_branch_is_two_days(config):
    assert disinfection_days(90, "PE", length_m=350, config=config).days == 2


def test_disinfection_long_pe_branch_escalates(config):
    """Над 500м/клон вече не е 'къс клон' — минава на голяма мрежа."""
    assert disinfection_days(110, "PE", length_m=900, config=config).days == 4


def test_disinfection_mixed_dn_large_network_is_four_days(config):
    assert disinfection_days(None, None, mixed_dn=True, config=config).days == 4


def test_disinfection_unknown_combination_returns_none(config):
    result = disinfection_days(630, None, config=config)
    assert result.days is None
    assert "няма правило" in result.reason


def test_disinfection_reads_days_from_config():
    custom = {"disinfection_days": {"DN500_PE": 9}}
    assert disinfection_days(500, "PE", config=custom).days == 9


# ===================================================================
# vratsa_tier_days — урок #45
# ===================================================================

@pytest.mark.parametrize(
    ("act2_act3", "expected"),
    [(0.5, 6), (1.0, 6), (1.5, 7), (2.0, 7), (3.0, 9), (3.5, 9), (3.6, 10), (10.0, 10)],
)
def test_vratsa_tier_ladder(act2_act3, expected):
    assert vratsa_tier_days(act2_act3) == expected


@pytest.mark.parametrize(
    ("act2_act3", "expected"),
    [(1.0, 7), (2.0, 9), (3.0, 10), (3.6, 10)],
)
def test_vratsa_tier_svo_escalates_one_level(act2_act3, expected):
    """Участък с много сградни отклонения → +1 ниво (9д→10д)."""
    assert vratsa_tier_days(act2_act3, many_svo=True) == expected


def test_vratsa_tier_svo_cannot_exceed_ladder_top():
    assert vratsa_tier_days(99.0, many_svo=True) == 10


# ===================================================================
# is_pipe_task / detect_length_m
# ===================================================================

@pytest.mark.parametrize(
    "name",
    ["Полагане DN110 PE", "Водопровод ул. Х", "Канализация клон 4", "Тласкател към КПС"],
)
def test_is_pipe_task_true(name):
    assert is_pipe_task({"name": name}) is True


@pytest.mark.parametrize(
    "name",
    ["Изкоп за траншея", "Извозване земни маси", "Асфалтиране ул. Х", "Мобилизация"],
)
def test_is_pipe_task_false(name):
    assert is_pipe_task({"name": name}) is False


def test_is_pipe_task_by_type():
    assert is_pipe_task({"type": "water_pipe", "name": "Мобилизация"}) is True


def test_detect_length_m_from_field():
    assert detect_length_m({"length_m": 720}) == 720


def test_detect_length_m_from_quantity_when_unit_is_metres():
    assert detect_length_m({"quantity": 500, "unit": "м"}) == 500


def test_detect_length_m_ignores_quantity_in_other_units():
    assert detect_length_m({"quantity": 720, "unit": "м3"}) is None


def test_detect_length_m_missing():
    assert detect_length_m({"name": "Полагане"}) is None


# ===================================================================
# calculate_task_duration — интеграция
# ===================================================================

def test_calculate_milestone_is_zero():
    result = calculate_task_duration({"name": "ФИНАЛ: Приемане", "milestone": True})
    assert result.days == 0


def test_calculate_skips_non_pipe_task():
    result = calculate_task_duration({"name": "Изкоп за траншея", "quantity": 720, "unit": "м3"})
    assert result.days is None
    assert "не е тръбна дейност" in result.reason


def test_calculate_skips_pipe_task_without_material():
    """Липсващ материал → пропускаме, вместо да гадаем (урок #35)."""
    result = calculate_task_duration({"name": "Полагане DN300 — ул. Х", "length_m": 500})
    assert result.days is None
    assert "материалът не е указан" in result.reason


def test_calculate_skips_pipe_task_without_length():
    result = calculate_task_duration({"name": "Полагане DN110 PE", "diameter": 110})
    assert result.days is None
    assert "липсва length_m" in result.reason


def test_calculate_reports_rate_and_key():
    result = calculate_task_duration(
        {"name": "Полагане DN500 PE", "length_m": 720, "diameter": 500}
    )
    assert result.rate == 15
    assert result.rate_key == "DN500_PE_open"
    assert "720м ÷ 15 м/ден" in result.reason


def test_calculate_terrain_opt_in_changes_result():
    task = {"name": "Полагане DN300 CI — горски участък", "length_m": 673, "diameter": 300}
    without = calculate_task_duration(task, apply_terrain=False)
    with_terrain = calculate_task_duration(task, apply_terrain=True)
    assert with_terrain.days > without.days


# ===================================================================
# GOLDEN VALUES от ACCURACY.md — реални проекти
# ===================================================================

def test_golden_pernik_dn500_pe_720m():
    """Среднево: 720м DN500 PE ÷ 15 м/д = 48 раб. дни (диапазон 43–53)."""
    result = calculate_task_duration(
        {"name": "Полагане DN500 PE — довеждащ водопровод", "length_m": 720, "diameter": 500}
    )
    assert result.days == 48
    assert 43 <= result.days <= 53


def test_golden_harmanli_dn300_ci_673m():
    """Горноград: 673м DN300 CI ÷ 8 м/д (диапазон 75–93 раб. дни)."""
    result = calculate_task_duration(
        {"name": "Полагане DN300 CI — горски терен", "length_m": 673, "diameter": 300}
    )
    assert result.days == 85  # ceil(673/8) = 84.125 → 85; ACCURACY.md сочи ~84
    assert 75 <= result.days <= 93


def test_golden_harmanli_would_be_10x_wrong_as_pe():
    """Урок #35: ако DN300 CI мине като PE (DN500 норма) → 10× занижение.

    Модулът НЕ прави това — DN300 PE няма норма и задачата се пропуска.
    """
    as_pe = calculate_task_duration(
        {"name": "Полагане DN300 PE", "length_m": 673, "diameter": 300}
    )
    assert as_pe.days is None


def test_golden_ivanyane_branches_are_not_templated():
    """Урок #40: клонове с еднакъв DN, но различна дължина → различни дни."""
    kl16 = calculate_task_duration(
        {"name": "Кл.16 полагане DN90 PE", "length_m": 351, "diameter": 90}
    )
    kl20 = calculate_task_duration(
        {"name": "Кл.20 полагане DN90 PE", "length_m": 635, "diameter": 90}
    )
    kl22 = calculate_task_duration(
        {"name": "Кл.22 полагане DN90 PE", "length_m": 70, "diameter": 90}
    )
    assert kl16.days == 30   # ceil(351/12)
    assert kl20.days == 53   # ceil(635/12)
    assert kl22.days == 6    # ceil(70/12)
    assert len({kl16.days, kl20.days, kl22.days}) == 3


def test_golden_vratsa_tier_distribution():
    """Долноград: 49% от участъците са Tier 6д (Act2+Act3 ≈ 1.0д)."""
    assert vratsa_tier_days(1.0) == 6
    assert vratsa_tier_days(2.0) == 7
    assert vratsa_tier_days(3.5) == 9
    assert vratsa_tier_days(3.6, many_svo=True) == 10


def test_golden_hdd_uses_effective_not_drill_rate():
    """Урок #32: HDD DN90 = 12 м/д ефективна, не 56 м/д пробивна."""
    result = calculate_task_duration(
        {"name": "Безизкопно полагане DN90 PE", "length_m": 560, "diameter": 90}
    )
    assert result.rate == 12
    assert result.days == 47   # ceil(560/12); при 56 м/д би било 10 — 4.7× занижение


def test_water_test_days_constant():
    """Урок #34: изпитване = 2 дни (якост + спад на налягане)."""
    assert WATER_TEST_DAYS == 2


# ===================================================================
# Фактори на условията (ниво 5 — одит 2026-07-23, точка 4)
# ===================================================================

class TestConditionFactors:
    """Продължителността не е функция само на length + DN + material + method.

    FAILURE означава: почва, дълбочина, подземни води и градска среда пак не
    влияят на продължителността, а productivities.json дава фалшиво усещане
    за детерминизъм — формулата е детерминистична, входната норма не е пълна.
    """

    def test_no_conditions_means_no_change(self):
        from src.duration_calculator import condition_factors
        multiplier, applied = condition_factors({"name": "Полагане"})
        assert multiplier == 1.0
        assert applied == []

    def test_rocky_soil_slows_work_down(self):
        from src.duration_calculator import condition_factors
        multiplier, applied = condition_factors({"soil": "rocky"})
        assert multiplier > 1.0
        assert applied[0]["factor"] == "soil"

    def test_factors_multiply(self):
        from src.duration_calculator import CONDITION_FACTORS, condition_factors
        multiplier, applied = condition_factors({"soil": "rocky", "groundwater": "heavy"})
        expected = (CONDITION_FACTORS["soil"]["rocky"]
                    * CONDITION_FACTORS["groundwater"]["heavy"])
        assert multiplier == pytest.approx(expected)
        assert len(applied) == 2

    def test_unknown_value_is_ignored_not_guessed(self):
        from src.duration_calculator import condition_factors
        multiplier, applied = condition_factors({"soil": "нещо си"})
        assert multiplier == 1.0
        assert applied == []

    def test_each_factor_records_its_provenance(self):
        """Множител без обяснение е магическо число."""
        from src.duration_calculator import condition_factors
        _, applied = condition_factors({"depth": "deep"})
        assert applied[0] == {"factor": "depth", "value": "deep", "multiplier": 1.35}

    def test_conditions_are_off_by_default(self):
        """Стойностите не са верифицирани срещу проекти — не се прилагат сами."""
        task = {"name": "Полагане DN500 PE", "length_m": 720, "diameter": 500,
                "soil": "rocky", "groundwater": "heavy"}
        assert calculate_task_duration(task).days == 48

    def test_conditions_extend_duration_when_enabled(self):
        task = {"name": "Полагане DN500 PE", "length_m": 720, "diameter": 500,
                "soil": "rocky"}
        base = calculate_task_duration(task).days
        with_conditions = calculate_task_duration(task, apply_conditions=True).days
        assert with_conditions > base

    def test_reason_shows_which_conditions_applied(self):
        task = {"name": "Полагане DN500 PE", "length_m": 720, "diameter": 500,
                "soil": "rocky", "environment": "urban"}
        reason = calculate_task_duration(task, apply_conditions=True).reason
        assert "soil=rocky" in reason
        assert "environment=urban" in reason

    def test_favourable_conditions_can_shorten(self):
        task = {"name": "Полагане DN500 PE", "length_m": 720, "diameter": 500,
                "soil": "loose"}
        base = calculate_task_duration(task).days
        assert calculate_task_duration(task, apply_conditions=True).days <= base


# ===================================================================
# Бетонов кожух — обемна норма, изведена от еталона (17.08.2026)
# ===================================================================
#
# ИЗМЕРЕНО: трите реда „Бетонов кожух за тръба DN 500/700/1000" излизаха
# NOT_PARAMETRIC и получаваха медианата от шаблона — 3 дни.  Тоест 84.7 м³ и
# 6.93 м³ струваха еднакво време, а обемът не значеше нищо.
#
# FAILURE означава: обемните дейности пак ще получават продължителност от
# шаблона вместо от количеството си, и срокът няма да зависи от това колко
# бетон има за изливане.


def _кожух(quantity, unit="m3/m'", name="Полагане — Бетонов кожух за тръба DN 1000"):
    """Задачата, както я ражда веригата; `min_days=1` е както в конвейера."""
    return calculate_task_duration(
        {"name": name, "quantity": quantity, "unit": unit}, min_days=1)


def test_encasement_duration_follows_the_volume():
    малък = _кожух(6.93)
    голям = _кожух(84.7)

    assert малък.days and голям.days, "кожухът пак е без сметната норма"
    assert голям.days > малък.days, (
        f"84.7 м³ и 6.93 м³ дават еднакво време ({голям.days} срещу {малък.days})")


def test_encasement_uses_the_configured_rate():
    from src.duration_calculator import load_productivities

    норма = (load_productivities().get("volume_productivities", {})
             .get("concrete_encasement", {}).get("effective_rate"))
    резултат = _кожух(80.0)

    assert норма, "нормата липсва в config/productivities.json"
    assert резултат.rate == pytest.approx(float(норма))
    assert резултат.days == 5           # 80 ÷ 16.5 = 4.85 → 5


def test_encasement_is_marked_as_calculated_not_guessed():
    резултат = _кожух(84.7)

    assert резултат.code == "CALCULATED", f"кожухът се отчита като недоказан: {резултат.code}"
    assert "кожух" in (резултат.reason or "").lower()


def test_a_volume_row_that_is_not_encasement_is_left_alone():
    """Нормата важи за кожуха, не за всяка кубатура."""
    резултат = calculate_task_duration(
        {"name": "Изкоп на земни маси", "quantity": 500.0, "unit": "м3"},
        min_days=1)

    assert резултат.days is None
