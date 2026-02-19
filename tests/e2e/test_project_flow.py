"""Test project and visualization layout."""

import pytest

pytestmark = pytest.mark.e2e


def test_visualization_heading_visible(app_page):
    """Visualization heading should be visible in the right column."""
    heading = app_page.get_by_text("Визуализация")
    assert heading.first.is_visible(timeout=5000)


def test_page_has_schedule_content(app_page):
    """Page should show schedule-related content (demo or loaded project)."""
    # Either the Gantt chart, tabs, or status bar should be visible
    has_plotly = app_page.locator('.js-plotly-plot').first.is_visible(timeout=5000)
    has_tabs = app_page.locator('button[role="tab"]').first.is_visible(timeout=3000)
    has_status = app_page.get_by_text("проект", exact=False).first.is_visible(timeout=3000)
    assert has_plotly or has_tabs or has_status
