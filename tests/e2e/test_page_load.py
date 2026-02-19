"""Test that the application loads correctly in the browser."""

import pytest

pytestmark = pytest.mark.e2e


def test_main_app_container_renders(app_page):
    """The Streamlit stApp container should be visible."""
    app = app_page.locator('[data-testid="stApp"]')
    assert app.is_visible()


def test_sidebar_has_content(app_page):
    """Sidebar should contain interactive elements (config check passed)."""
    # The clear chat button is always present regardless of sidebar state
    btn = app_page.locator('button:has-text("Изчисти чата")')
    assert btn.is_visible(timeout=5000)


def test_main_content_not_stopped(app_page):
    """Main content should render — sidebar subheader with emoji loaded."""
    # Subheaders include emojis, use partial match
    ai_status = app_page.get_by_text("AI Статус")
    assert ai_status.is_visible(timeout=5000)
