"""Приховане за рішенням колег (30.10.2026): «Поділитися ідеєю», «Мій розвиток»
і зовнішні розділи, які адміністратор вимкнув у «Структурі каталогу»."""
from tests.e2e.helpers import expect, login


def _relogin(page, base, username):
    page.evaluate("() => { localStorage.clear(); sessionStorage.clear(); }")
    login(page, base, username)


def test_ideas_and_development_are_hidden(page, live_url):
    login(page, live_url)
    labels = page.locator("#sidebar .sb-label").all_text_contents()
    assert "Мій розвиток" not in labels
    expect(page.locator("#sidebar .sb-group", has_text="Навчання")).to_have_count(0)
    expect(page.locator("#cat-idea")).to_have_count(0)
    expect(page.locator("#home-level")).to_be_hidden()
    # Пряма адреса «Мого розвитку» веде на головну.
    page.evaluate("() => go('hub:dev')")
    expect(page.locator("#cat-personal")).to_be_visible()
    # Меню профілю — без «Мої ідеї».
    page.click("#me-btn")
    expect(page.locator('#me-menu [data-act="my-ideas"]')).to_be_hidden()
    page.keyboard.press("Escape")
    # Картка матеріалу — без «Поділитися ідеєю».
    page.click('#sidebar [data-dest="hub:all"]')
    page.locator("#cat-grid article.mdc").first.click()
    expect(page.locator(".md-modal")).to_be_visible()
    expect(page.locator("#res-idea")).to_have_count(0)


def test_hidden_section_not_in_sidebar(page, live_url):
    login(page, live_url, "admin")
    sid = page.evaluate("""async () => (await api('/catalog/sections', { method: 'POST',
        body: { name: 'Зовнішній портал', url: 'https://example.org/portal', is_active: false } })).id""")
    try:
        page.evaluate("() => ensureHubData(true).then(() => go('hub:home'))")
        # Адміністратор бачить прихований розділ у «Структурі», але не в меню бази знань.
        expect(page.locator("#sidebar .sb-label", has_text="Зовнішній портал")).to_have_count(0)
        _relogin(page, live_url, "tester")
        expect(page.locator("#sidebar .sb-label", has_text="Зовнішній портал")).to_have_count(0)
    finally:
        _relogin(page, live_url, "admin")
        page.evaluate("(id) => api(`/catalog/sections/${id}`, { method: 'DELETE' })", sid)
