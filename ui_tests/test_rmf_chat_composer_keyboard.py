# test_rmf_chat_composer_keyboard.py
"""
UI test for RMF chat composer keyboard behavior.
Version: 0.250.076
Implemented in: 0.250.076

This test ensures Enter submits through the form, Shift+Enter inserts a newline,
IME composition does not submit, and existing empty/busy guards remain effective.
"""

from pathlib import Path

import pytest

playwright_sync_api = pytest.importorskip("playwright.sync_api")
expect = playwright_sync_api.expect


COMPOSER_SCRIPT = (
    Path(__file__).parents[1]
    / "application/single_app/static/js/rmf/rmf-chat-composer.js"
)


@pytest.mark.ui
def test_rmf_chat_composer_keyboard_behavior(playwright):
    """Validate keyboard submission and native multiline behavior."""
    browser = playwright.chromium.launch()
    page = browser.new_page()

    try:
        page.set_content(
            """
            <form id="chat-form">
                <label for="question">Question</label>
                <textarea id="question"></textarea>
                <button type="submit">Send</button>
            </form>
            """
        )
        page.add_script_tag(path=str(COMPOSER_SCRIPT))
        page.evaluate(
            """
            () => {
                window.submittedQuestions = [];
                window.chatBusy = false;
                const form = document.getElementById('chat-form');
                const question = document.getElementById('question');
                form.addEventListener('submit', (event) => {
                    event.preventDefault();
                    const value = question.value.trim();
                    if (!value || window.chatBusy) return;
                    window.submittedQuestions.push(value);
                });
                question.addEventListener('keydown', (event) => {
                    window.RmfChatComposer.handleKeydown(event, form);
                });
            }
            """
        )

        question = page.locator("#question")
        question.fill("What evidence supports AC-2?")
        question.press("Enter")
        assert page.evaluate("window.submittedQuestions") == [
            "What evidence supports AC-2?"
        ]
        expect(question).to_have_value("What evidence supports AC-2?")

        question.press("Shift+Enter")
        expect(question).to_have_value("What evidence supports AC-2?\n")
        assert len(page.evaluate("window.submittedQuestions")) == 1

        page.evaluate(
            """
            () => {
                document.getElementById('question').dispatchEvent(
                    new KeyboardEvent('keydown', {
                        key: 'Enter',
                        bubbles: true,
                        cancelable: true,
                        isComposing: true
                    })
                );
            }
            """
        )
        assert len(page.evaluate("window.submittedQuestions")) == 1

        question.fill("   ")
        question.press("Enter")
        assert len(page.evaluate("window.submittedQuestions")) == 1

        question.fill("Busy question")
        page.evaluate("window.chatBusy = true")
        question.press("Enter")
        assert len(page.evaluate("window.submittedQuestions")) == 1
    finally:
        browser.close()
