"""Спільне для браузерних тестів: пароль демо-користувачів і короткі дії."""
import pytest

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

PASSWORD = "Passw0rd!"


def login(page, base, username="tester"):
    page.goto(base)
    page.fill("#login-username", username)
    page.fill("#login-password", PASSWORD)
    page.click("#login-form button[type=submit]")
    page.wait_for_selector("#sidebar .sb-item")


def open_wizard_at_describe(page, kind, text):
    page.click("#cat-add")
    page.click(f'.wiz-kind[data-kind="{kind}"]')
    page.fill("#wiz-editor .rte-area", text)
    page.click("#wiz-next")
    page.wait_for_selector("#wiz-name")


def settings(page, **values):
    """Налаштування хабу від імені поточного користувача (менеджера)."""
    return page.evaluate("(body) => api('/catalog/settings', { method: 'POST', body })", values)
