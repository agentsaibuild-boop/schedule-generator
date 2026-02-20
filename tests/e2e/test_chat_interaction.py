"""Тест: Чатът приема съобщения и показва AI отговор."""
# -*- coding: utf-8 -*-

import pytest

pytestmark = pytest.mark.e2e

AI_RESPONSE_TIMEOUT = 90000


def test_chat_accepts_message_and_responds(app_page):
    """
    Пишем съобщение в чата и проверяваме че AI отговаря.
    FAILURE означава: chat_handler.py, ai_router.py или ai_processor.py са счупени.
    """
    chat_input = app_page.locator('[data-testid="stChatInputTextArea"]')
    chat_input.wait_for(state="visible", timeout=10000)

    messages_before = app_page.locator('[data-testid="stChatMessage"]').count()

    chat_input.click()
    chat_input.fill("Здравей, можеш ли да ми кажеш колко урока имаш в базата знания?")
    chat_input.press("Enter")

    app_page.wait_for_function(
        f"document.querySelectorAll('[data-testid=\"stChatMessage\"]').length > {messages_before}",
        timeout=AI_RESPONSE_TIMEOUT,
    )

    messages_after = app_page.locator('[data-testid="stChatMessage"]').count()
    assert messages_after > messages_before, (
        "AI не отговори на съобщението в чата след 90 секунди"
    )


def test_chat_input_clears_after_send(app_page):
    """
    След изпращане полето трябва да се изчисти.
    FAILURE означава: chat input не се reset-ва правилно.
    """
    chat_input = app_page.locator('[data-testid="stChatInputTextArea"]')
    chat_input.wait_for(state="visible", timeout=10000)

    chat_input.click()
    chat_input.fill("тест")
    chat_input.press("Enter")

    app_page.wait_for_function(
        'document.querySelector(\'[data-testid="stChatInputTextArea"]\')?.value === ""',
        timeout=10000,
    )
    assert chat_input.input_value() == "", "Chat input не се изчисти след изпращане"
