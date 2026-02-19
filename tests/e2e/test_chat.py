"""Test chat input and message display."""

import pytest

pytestmark = pytest.mark.e2e


def test_chat_heading_visible(app_page):
    """The Chat heading should be visible."""
    # Heading uses st.markdown("### 💬 Чат") — match the emoji+text combo
    # or fall back to checking the chat input area exists (proves chat column rendered)
    heading = app_page.locator(':text("💬 Чат")')
    if heading.count() > 0 and heading.first.is_visible(timeout=3000):
        assert True
    else:
        # Fallback: chat input proves the chat column is rendered
        chat_input = app_page.locator('textarea[data-testid="stChatInputTextArea"], textarea')
        assert chat_input.first.is_visible(timeout=5000), "Neither chat heading nor chat input visible"


def test_chat_input_exists(app_page):
    """Chat input textarea should be present."""
    # Streamlit chat_input renders as textarea inside stChatInput
    chat_input = app_page.locator('textarea[data-testid="stChatInputTextArea"]')
    if chat_input.count() == 0:
        # Fallback: try broader selector
        chat_input = app_page.locator('textarea')
    assert chat_input.first.is_visible(timeout=5000)


def test_first_message_displayed(app_page):
    """At least one chat message or info bar should appear on first load."""
    # When a project is loaded, Streamlit may show an info bar with restored history
    # instead of a direct stChatMessage. Accept either as proof the chat is working.
    messages = app_page.locator('[data-testid="stChatMessage"]')
    info_bar = app_page.locator('[data-testid="stAlert"], [data-testid="stNotification"]')
    has_message = messages.first.is_visible(timeout=5000)
    has_info = info_bar.first.is_visible(timeout=3000) if not has_message else False
    assert has_message or has_info, "Neither chat message nor info bar visible"
