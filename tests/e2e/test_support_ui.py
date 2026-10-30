"""Хвилі 3 і 4 фідбеку тестування в браузері: позначка «Експерт», AI SD агент
і «Зворотний зв'язок» адміністратору."""
from tests.e2e.helpers import expect, login, open_wizard_at_describe

AGENT_PREFIX = "https://teams.microsoft.com/l/app/"


def _relogin(page, base, username):
    page.evaluate("() => { localStorage.clear(); sessionStorage.clear(); }")
    login(page, base, username)


def _set_expert(page, username, value):
    page.evaluate("""async ([name, value]) => {
        const uid = (await api('/users')).find(u => u.username === name).id;
        await api(`/users/${uid}`, { method: 'PATCH', body: { is_expert: value } });
    }""", [username, value])


def test_support_links_and_contact_message(page, live_url):
    login(page, live_url, "tester")
    # Низ бічної панелі: агент, «Зворотний зв'язок», інструкція.
    bottom = page.locator("#sidebar .sb-item .sb-label")
    labels = bottom.all_text_contents()[-3:]
    assert labels == ["AI SD агент", "Зворотний зв'язок", "Інструкція"]
    agent = page.locator('#sidebar [data-support="agent"]')
    expect(agent).to_have_attribute("title", "Для вирішення технічних питань")
    expect(agent).to_have_attribute("target", "_blank")
    assert agent.get_attribute("href").startswith(AGENT_PREFIX)

    # Той самий блок на головній.
    support = page.locator("#home-support")
    expect(support).to_contain_text("Потрібна допомога?")
    expect(support.locator('[data-support="agent"]')).to_have_attribute(
        "title", "Для вирішення технічних питань")
    expect(support.locator("#home-contact")).to_have_attribute(
        "title", "Нетехнічні питання адміністратору хабу")

    # Повідомлення: автор і сторінка підставляються самі.
    page.click('#sidebar [data-dest="hub:fav"]')
    page.click("#sidebar [data-contact]")
    expect(page.locator(".contact-meta")).to_contain_text("Тестувальник")
    expect(page.locator(".contact-meta")).to_contain_text("Обране")
    page.click("#ct-send")
    expect(page.locator("#ct-err")).to_have_text("Напишіть повідомлення")
    page.fill("#ct-msg", "Хочу стати менеджером розділу")
    page.click("#ct-send")
    expect(page.locator(".contact-modal")).to_have_count(0)

    # Адміністратор: сповіщення веде в «Керування → Зворотний зв'язок».
    _relogin(page, live_url, "admin")
    page.click("#notif-btn")
    page.locator(".notif-item", has_text="Хочу стати менеджером розділу").click()
    expect(page.locator(".page-h1")).to_have_text("Зворотний зв'язок")
    row = page.locator(".contact-row", has_text="Хочу стати менеджером розділу")
    expect(row).to_contain_text("Тестувальник")
    expect(row).to_contain_text("Обране (hub:fav)")
    row.locator('[data-cs="done"]').click()
    expect(page.locator(".contact-row", has_text="Хочу стати менеджером розділу")).to_have_count(0)
    page.click('[data-cf="done"]')
    expect(page.locator(".contact-row", has_text="Хочу стати менеджером розділу")).to_contain_text(
        "Опрацьовано")


def test_admin_changes_agent_link(page, live_url):
    login(page, live_url, "admin")
    page.click('#mode-seg [data-mode="admin"]')
    page.click('#sidebar [data-dest="admin:contact"]')
    field = page.locator("#agent-url")
    assert field.input_value().startswith(AGENT_PREFIX)
    original = field.input_value()
    try:
        field.fill("https://example.org/agent")
        page.click("#agent-url-save")
        expect(page.locator('#sidebar [data-support="agent"]')).to_have_attribute(
            "href", "https://example.org/agent")
        field.fill("")
        page.click("#agent-url-save")
        expect(page.locator('#sidebar [data-support="agent"]')).to_have_count(0)
    finally:
        page.evaluate("(url) => api('/catalog/settings', { method: 'POST', "
                      "body: { support_agent_url: url } })", original)


def test_section_manager_has_no_contact_page(page, live_url):
    login(page, live_url, "manager")
    page.click('#mode-seg [data-mode="admin"]')
    expect(page.locator('#sidebar [data-dest="admin:contact"]')).to_have_count(0)


def test_expert_label_on_published_material(page, live_url):
    login(page, live_url, "admin")
    _set_expert(page, "author", True)
    try:
        _relogin(page, live_url, "author")
        open_wizard_at_describe(page, "instruction", "Як вести реєстр ризиків.")
        page.fill("#wiz-name", "Реєстр ризиків")
        page.fill("#wiz-desc", "Шаблон і порядок ведення.")
        page.select_option("#wiz-section", label="Матеріали для тестів")
        page.click("#wiz-next")
        page.wait_for_selector(".md-modal")
        rid = page.evaluate("async () => (await api('/catalog/resources?mine=1'))"
                            ".find(r => r.name === 'Реєстр ризиків').id")

        _relogin(page, live_url, "manager")
        page.click('#mode-seg [data-mode="admin"]')
        page.locator(f'.content-queue .res-row[data-id="{rid}"] [data-act="approve"]').click()
        expect(page.locator(f'.content-queue .res-row[data-id="{rid}"]')).to_have_count(0)

        _relogin(page, live_url, "tester")
        page.click('#sidebar [data-dest="hub:all"]')
        card = page.locator("#cat-grid article.mdc", has_text="Реєстр ризиків")
        expect(card.locator(".exp-tag")).to_have_text("Експерт")
        expect(card.locator(".mdc-meta")).to_contain_text("Автор Матеріалів")
        # Інші матеріали того самого автора опубліковано до галочки — без позначки.
        expect(page.locator("#cat-grid article.mdc", has_text="Регламент закупівель")
               .locator(".exp-tag")).to_have_count(0)
        card.click()
        expect(page.locator(".md-modal .sm-meta .exp-tag")).to_have_text("Експерт")
    finally:
        _relogin(page, live_url, "admin")
        _set_expert(page, "author", False)
