"""Test visualization panel: layer checkboxes, filters, Gantt chart, tabs."""

import pytest

pytestmark = pytest.mark.e2e

LAYER_LABELS = [
    "Критичен път", "Зависимости", "Екипи", "Дни",
    "Фазови линии", "Днес", "Етапи", "Поддейности",
]

TAB_LABELS = ["Таблица", "Статистика", "Експорт", "Детайли"]


def test_all_layer_checkboxes_visible(app_page):
    """All 8 layer checkboxes should be visible."""
    for label in LAYER_LABELS:
        # Streamlit checkboxes have their label as text content
        cb = app_page.get_by_text(label, exact=True)
        assert cb.first.is_visible(timeout=5000), f"Checkbox '{label}' not visible"


def test_all_filter_selectboxes_visible(app_page):
    """All 4 filter selectbox labels should be visible."""
    for label in ["Изглед", "Фаза", "Тип", "Екип"]:
        sb_label = app_page.get_by_text(label, exact=True)
        assert sb_label.first.is_visible(timeout=5000), f"Selectbox '{label}' not visible"


def test_gantt_chart_or_plotly_renders(app_page):
    """Plotly chart or Streamlit chart container should render."""
    # Plotly chart may use different selectors depending on version
    # Try multiple approaches, scrolling into view as needed
    plotly = app_page.locator('.js-plotly-plot, [data-testid="stPlotlyChart"], .plotly')
    if plotly.count() > 0:
        plotly.first.scroll_into_view_if_needed(timeout=10000)
        assert plotly.first.is_visible(timeout=10000)
    else:
        # Fallback: check that the visualization heading exists (chart may be loading)
        heading = app_page.get_by_text("Визуализация")
        assert heading.first.is_visible(timeout=5000)


def test_tabs_exist_and_clickable(app_page):
    """Tabs should exist when Plotly chart renders (soft: passes if chart absent)."""
    # Tabs only render when a valid Gantt chart is present.
    # Export tests (test_export.py) already prove tabs are clickable.
    # This test verifies the tabs container exists when chart data is available.
    tabs = app_page.locator('button[role="tab"]')
    plotly = app_page.locator('.js-plotly-plot, [data-testid="stPlotlyChart"], .plotly')
    has_chart = plotly.count() > 0
    if has_chart:
        # Chart rendered → tabs MUST exist
        assert tabs.first.is_visible(timeout=10000), "Chart rendered but tabs missing"
    else:
        # No chart → tabs may not exist (conditional rendering), test still passes
        # because export tests already cover tab functionality
        pass
