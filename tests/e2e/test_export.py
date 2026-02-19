"""Test export tab elements."""

import pytest

pytestmark = pytest.mark.e2e


def _navigate_to_export_tab(app_page):
    """Click the Export tab to reveal export controls."""
    export_tab = app_page.locator('button[role="tab"]:has-text("Експорт")')
    export_tab.click()
    app_page.wait_for_timeout(1500)


def test_export_tab_has_pdf_button(app_page):
    """Export tab should have a 'Generate PDF' button."""
    _navigate_to_export_tab(app_page)
    pdf_btn = app_page.locator('button:has-text("Генерирай PDF")')
    assert pdf_btn.is_visible(timeout=5000)


def test_export_tab_has_xml_button(app_page):
    """Export tab should have a 'Generate XML' button."""
    _navigate_to_export_tab(app_page)
    xml_btn = app_page.locator('button:has-text("Генерирай XML")')
    assert xml_btn.is_visible(timeout=5000)


def test_export_tab_has_json_download(app_page):
    """Export tab should have a 'Download JSON' button."""
    _navigate_to_export_tab(app_page)
    json_btn = app_page.locator('button:has-text("Свали JSON")')
    assert json_btn.is_visible(timeout=5000)
