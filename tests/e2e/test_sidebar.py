"""Test all sidebar elements are visible and interactive."""

import pytest

pytestmark = pytest.mark.e2e


def test_ai_status_section(app_page):
    """AI Status section should show DeepSeek and Anthropic status."""
    assert app_page.get_by_text("AI Статус").is_visible(timeout=5000)
    assert app_page.get_by_text("DeepSeek").first.is_visible()
    assert app_page.get_by_text("Anthropic").first.is_visible()


def test_health_check_button(app_page):
    """Health check button should be visible."""
    btn = app_page.locator('button:has-text("Провери отново")')
    assert btn.is_visible(timeout=5000)


def test_cost_tracking_section(app_page):
    """Cost tracking section should be visible."""
    assert app_page.get_by_text("Разходи").is_visible(timeout=5000)


def test_project_section(app_page):
    """Project section should be visible (either current or load)."""
    # Either "Текущ проект" (project loaded) or "Зареди проект" (no project)
    has_current = app_page.get_by_text("Текущ проект").is_visible(timeout=3000)
    has_load = app_page.get_by_text("Зареди проект").first.is_visible(timeout=3000)
    assert has_current or has_load


def test_knowledge_section(app_page):
    """Knowledge stats section should be visible."""
    assert app_page.get_by_text("Знания").is_visible(timeout=5000)


def test_evolution_section(app_page):
    """Evolution section with History and Rollback buttons should be visible."""
    assert app_page.get_by_text("Еволюция").is_visible(timeout=10000)
    assert app_page.locator('button:has-text("История")').is_visible(timeout=5000)
    assert app_page.locator('button:has-text("Върни")').is_visible(timeout=5000)


def test_documentation_section(app_page):
    """Documentation section should be visible."""
    # Use heading role to avoid matching "Обнови документацията" button text
    heading = app_page.get_by_role("heading", name="Документация")
    assert heading.is_visible(timeout=5000)


def test_clear_chat_button(app_page):
    """Clear chat button should be visible."""
    btn = app_page.locator('button:has-text("Изчисти чата")')
    assert btn.is_visible(timeout=5000)
