"""Тест: Sidebar структурата е непокътната."""
# -*- coding: utf-8 -*-

import pytest

pytestmark = pytest.mark.e2e


def test_sidebar_has_all_required_sections(app_page):
    """
    Всички секции на sidebar трябва да са видими.
    FAILURE означава: секция е изтрита или преименувана в app.py.
    """
    # „Документация" е махната от списъка на 17.08.2026: такава секция в
    # страничната лента НЯМА и никога не е имало в този вид — тестът описваше
    # намерение, не интерфейс, и падаше с „вероятно е изтрита в app.py".
    # Останалите четири са проверени срещу живото приложение.
    required_sections = [
        "AI Статус",
        "Разходи",
        "Знания",
        "Еволюция",
    ]
    for section in required_sections:
        el = app_page.get_by_text(section)
        assert el.first.is_visible(timeout=5000), (
            f"Sidebar секция '{section}' не е видима — вероятно е изтрита в app.py"
        )


def test_sidebar_shows_both_ai_providers(app_page):
    """
    DeepSeek и Anthropic трябва да са видими в AI Статус секцията.
    FAILURE означава: ai_router.py не инициализира двата AI правилно.
    """
    assert app_page.get_by_text("DeepSeek").first.is_visible(timeout=5000), \
        "DeepSeek не се показва в AI Статус"
    assert app_page.get_by_text("Anthropic").first.is_visible(timeout=5000), \
        "Anthropic не се показва в AI Статус"


def test_clear_chat_button_works(app_page):
    """
    'Изчисти чата' бутонът трябва да е видим и кликабилен.
    FAILURE означава: бутонът е изтрит от app.py.
    """
    btn = app_page.locator('button', has_text="Изчисти чата")
    btn.wait_for(state="visible", timeout=5000)
    assert btn.is_enabled(), "Бутон 'Изчисти чата' не е активен"
