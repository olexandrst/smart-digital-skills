"""Попередня модерація в браузері (фідбек тестування, п. 11–12): автор подає,
менеджер розділу схвалює в черзі, зміни до опублікованого чекають схвалення."""
from tests.e2e.helpers import expect, login, open_wizard_at_describe


def _relogin(page, base, username):
    page.evaluate("() => { localStorage.clear(); sessionStorage.clear(); }")
    login(page, base, username)


def test_submission_approval_and_changes(page, live_url):
    login(page, live_url, "author")
    open_wizard_at_describe(page, "instruction", "Як перевірити постачальника перед договором.")
    page.fill("#wiz-name", "Перевірка постачальника")
    page.fill("#wiz-desc", "Чек-лист перевірки.")
    page.select_option("#wiz-section", label="Матеріали для тестів")
    expect(page.locator("#wiz-next")).to_have_text("Подати на розгляд")
    page.click("#wiz-next")
    page.wait_for_selector(".md-modal")
    expect(page.locator(".md-modal .sm-manage")).to_contain_text("Забрати з розгляду")
    rid = page.evaluate("async () => (await api('/catalog/resources?mine=1'))"
                        ".find(r => r.name === 'Перевірка постачальника').id")

    # Менеджер розділу: «Керування» відкривається на черзі, структури й користувачів немає.
    _relogin(page, live_url, "manager")
    # Інші тести ділять із цим базу, тож звіряємо лічильник із чергою, а не з «1».
    queued = page.evaluate("async () => (await api('/catalog/moderation')).count")
    assert queued >= 1
    expect(page.locator('#mode-seg [data-mode="admin"] .seg-badge')).to_have_text(str(queued))
    page.click('#mode-seg [data-mode="admin"]')
    expect(page.locator(".page-h1")).to_have_text("Модерація")
    sidebar = page.locator("#sidebar .sb-label")
    expect(sidebar).to_have_text(["Модерація", "KPI хабу", "Використання", "Інструкція"])
    row = page.locator(f'.content-queue .res-row[data-id="{rid}"]')
    row.locator('[data-act="approve"]').click()
    expect(row).to_have_count(0)
    status = page.evaluate("async (id) => (await api(`/catalog/resources/${id}`)).status", rid)
    assert status == "published"

    # Автор змінює опубліковане — у каталозі лишається попередня версія.
    _relogin(page, live_url, "author")
    page.evaluate("(id) => openResourceModal(id)", rid)
    page.click('.sm-manage [data-m="edit"]')
    page.wait_for_selector("#wiz-editor")
    page.click("#wiz-next")
    page.fill("#wiz-name", "Перевірка постачальника: чек-лист")
    page.click("#wiz-next")
    page.wait_for_selector(".md-modal")
    expect(page.locator(".md-modal h2")).to_have_text("Перевірка постачальника")
    expect(page.locator(".md-modal")).to_contain_text("Ваші зміни на розгляді")

    _relogin(page, live_url, "manager")
    page.click('#mode-seg [data-mode="admin"]')
    page.locator(f'.content-queue .res-row[data-id="{rid}"] [data-act="changes"]').click()
    modal = page.locator(".chg-modal")
    expect(modal).to_contain_text("Перевірка постачальника: чек-лист")
    page.click("#chg-ok")
    expect(modal).to_have_count(0)
    name = page.evaluate("async (id) => (await api(`/catalog/resources/${id}`)).name", rid)
    assert name == "Перевірка постачальника: чек-лист"


def test_admin_assigns_sections_to_manager(page, live_url):
    login(page, live_url, "admin")
    page.click('#mode-seg [data-mode="admin"]')
    page.click('#sidebar [data-dest="admin:users"]')
    page.locator('#u-body tr:has-text("tester") [data-act="edit"]').click()
    expect(page.locator("#ue-sections")).to_be_hidden()
    page.select_option("#ue-role", "section_manager")
    expect(page.locator("#ue-sections")).to_be_visible()
    page.locator('#ue-sections label:has-text("Матеріали для тестів") input').check()
    page.check("#ue-expert")
    page.click("#ue-save")
    expect(page.locator('#u-body tr:has-text("tester")')).to_contain_text("Матеріали для тестів")
    expect(page.locator('#u-body tr:has-text("tester")')).to_contain_text("Експерт")
    # Повертаємо роль, щоб інші тести бачили звичайного користувача.
    uid = page.evaluate("async () => (await api('/users')).find(u => u.username === 'tester').id")
    page.evaluate("""(id) => api(`/users/${id}`, { method: 'PATCH',
        body: { roles: [], is_expert: false } })""", uid)
