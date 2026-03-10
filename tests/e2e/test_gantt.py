"""Тест: Gantt chart се рендира с демо данни при стартиране."""
# -*- coding: utf-8 -*-

import pytest

pytestmark = pytest.mark.e2e


def test_gantt_renders_on_load(app_page):
    """
    Gantt chart трябва да се визуализира автоматично при зареждане
    с демо данни — без никакво действие от потребителя.
    FAILURE означава: schedule_builder.py или gantt_chart.py са счупени.
    """
    chart = app_page.locator('[data-testid="stPlotlyChart"]')
    chart.first.wait_for(state="visible", timeout=60000)
    assert chart.count() >= 1, "Gantt chart не се рендира при зареждане"


def test_gantt_has_layer_controls(app_page):
    """
    8-те слоя за управление на Gantt трябва да са видими.
    FAILURE означава: gantt_chart.py е изтрил или преименувал слоевете.
    """
    expected_layers = [
        "Критичен път",
        "Зависимости",
        "Екипи",
        "Дни",
        "Фазови линии",
        "Днес",
        "Етапи",
        "Поддейности",
    ]
    for layer in expected_layers:
        el = app_page.get_by_text(layer, exact=True)
        assert el.first.is_visible(timeout=5000), (
            f"Слой '{layer}' не е видим — вероятно е преименуван или изтрит в gantt_chart.py"
        )


def test_gantt_filter_controls_present(app_page):
    """
    4-те филтъра трябва да са видими.
    FAILURE означава: филтрите са премахнати от gantt_chart.py.
    """
    for label in ["Изглед", "Фаза", "Тип", "Екип"]:
        el = app_page.get_by_text(label, exact=True)
        assert el.first.is_visible(timeout=5000), f"Филтър '{label}' не е видим"
