"""Фідбек колег від 24.11.2026 у браузері: головна «спочатку цінність і вибір
маршруту», плитки-сценарії, компактні «Нещодавно додані», «Мої матеріали» й
«Зв'язатися з адміністратором», підказка на екрані входу, теги й дії на плитках."""
import io
import zipfile

from tests.e2e.helpers import expect, login

ADMIN_EMAIL = "olga.starodubtseva@metinvest.digital"
SCENARIOS = ["Навчатися та розвиватися", "Знайти ідеї та практичний досвід",
             "Застосувати AI у роботі"]


def _relogin(page, base, username):
    page.evaluate("() => { localStorage.clear(); sessionStorage.clear(); }")
    login(page, base, username)


def _set_scenario(page, scenario):
    """Прив'язати тестовий розділ до плитки-сценарію (адміністратор)."""
    page.evaluate("""async (scenario) => {
        const sec = (await api('/catalog/sections')).find(s => s.name === 'Матеріали для тестів');
        await api(`/catalog/sections/${sec.id}`, { method: 'PATCH', body: { scenario } });
        await ensureHubData(true);
        await go('hub:home');
    }""", scenario)


def test_home_starts_with_value_and_route_choice(page, live_url):
    login(page, live_url)
    # П. 1: що таке хаб — на першому екрані, з контактом адміністратора.
    expect(page.locator(".home-title")).to_have_text(
        "Усе необхідне для роботи з AI в одному просторі")
    expect(page.locator(".home-lead")).to_contain_text(
        "Навчайтеся, знаходьте практичні приклади використання AI")
    expect(page.locator(".home-path span")).to_have_text(
        ["Навчайтеся", "Надихайтеся досвідом колег", "Застосовуйте AI у своїх задачах"])
    expect(page.locator("#home-admin a")).to_have_attribute("href", f"mailto:{ADMIN_EMAIL}")
    # П. 2: три плитки-сценарії з тим, що всередині, і своєю дією.
    tiles = page.locator("#home-scenarios .scn-tile")
    expect(tiles.locator(".scn-title")).to_have_text(SCENARIOS)
    expect(tiles.locator(".scn-cta")).to_have_text(
        ["Перейти до навчання", "Переглянути матеріали", "Знайти рішення"])
    expect(tiles.nth(0).locator(".scn-inside li")).to_contain_text(["курси"])
    expect(tiles.nth(2).locator(".scn-inside li")).to_contain_text(["практичні шаблони"])
    # «Знайти рішення» над плитками не потрібна — лише в третій плитці.
    expect(page.get_by_text("Знайти рішення", exact=True)).to_have_count(1)
    # П. 5: кнопки «Мій контент» на головній немає.
    expect(page.locator("#cat-my-content")).to_have_count(0)
    expect(page.locator("#view").get_by_text("Мій контент")).to_have_count(0)
    # П. 6: «Нещодавно додані» — компактна плитка, а не розгорнутий список.
    expect(page.locator("#home-new")).to_contain_text("Нещодавно додані")
    expect(page.locator("#fresh-row")).to_have_count(0)
    assert set(page.locator("#cat-personal h2").all_text_contents()) <= {"Рекомендовано вам"}


def test_new_tile_opens_full_list_newest_first(page, live_url):
    login(page, live_url, "admin")
    page.evaluate("""() => api('/catalog/resources', { method: 'POST', body: {
        resource_type: 'instruction', name: 'Найновіша інструкція', description: 'Нове.',
        body: 'Текст.', status: 'published' } })""")
    _relogin(page, live_url, "tester")
    page.click("#home-new")
    expect(page.locator(".page-h1")).to_have_text("Нещодавно додані")
    expect(page.locator(".page-sub")).to_have_text("Нові матеріали каталогу — найсвіжіші першими.")
    expect(page.locator("#cat-grid .mdc-title").first).to_have_text("Найновіша інструкція")
    # Повний список: усі матеріали каталогу, а не перші вісім.
    total = page.evaluate("() => catalogState.items.length")
    expect(page.locator("#cat-grid article.mdc")).to_have_count(total)


def test_find_solution_opens_general_search(page, live_url):
    login(page, live_url)
    page.locator("#home-scenarios .scn-tile", has_text="Застосувати AI у роботі").click()
    expect(page.locator(".page-h1")).to_have_text("Матеріали")
    expect(page.locator("#global-q")).to_be_focused()
    total = page.evaluate("() => catalogState.items.length")
    expect(page.locator("#cat-grid article.mdc")).to_have_count(total)


def test_scenario_page_collects_materials_of_its_sections(page, live_url):
    login(page, live_url, "admin")
    _set_scenario(page, "learn")
    try:
        page.locator("#home-scenarios .scn-tile", has_text="Навчатися та розвиватися").click()
        expect(page.locator(".page-h1")).to_have_text("Навчатися та розвиватися")
        expect(page.locator(".page-sub")).to_have_text("Розвивайте свої знання та AI-навички")
        chips = page.locator(".scn-secs .scn-sec")
        expect(chips).to_have_text(["Матеріали для тестів"])
        expect(page.locator("#cat-grid article.mdc", has_text="Регламент закупівель")).to_have_count(1)
        # Розділ відкривається окремо просто зі сторінки сценарію.
        chips.first.click()
        expect(page.locator(".page-h1")).to_have_text("Матеріали для тестів")
        # Сценарій без розділів — пояснення, а не «нічого не знайдено».
        page.click('#sidebar [data-dest="hub:home"]')
        page.locator("#home-scenarios .scn-tile", has_text="Знайти ідеї").click()
        expect(page.locator(".page-h1")).to_have_text("Знайти ідеї та практичний досвід")
        expect(page.locator(".cat-empty")).to_contain_text("Тут поки немає матеріалів")
        # У «Структурі каталогу» видно, у якій плитці розділ.
        page.evaluate("() => go('admin:structure')")
        expect(page.locator(".res-row", has_text="Матеріали для тестів")).to_contain_text(
            "На головній: «Навчатися та розвиватися»")
        page.locator(".res-row", has_text="Матеріали для тестів").locator("[data-sec-edit]").click()
        expect(page.locator("#se-scenario")).to_have_value("learn")
        page.select_option("#se-scenario", "ideas")
        page.click("#se-save")
        expect(page.locator(".res-row", has_text="Матеріали для тестів")).to_contain_text(
            "На головній: «Знайти ідеї та практичний досвід»")
    finally:
        _set_scenario(page, "")


def test_sidebar_groups_my_materials_and_support_at_bottom(page, live_url):
    login(page, live_url)
    labels = page.locator("#sidebar .sb-label").all_text_contents()
    assert labels[:2] == ["Для вас", "Обране"]
    assert labels[-4:] == ["Мої матеріали", "AI SD агент", "Зв'язатися з адміністратором",
                           "Інструкція"]
    assert "Наповнення каталогу" not in labels
    page.click('#sidebar [data-dest="hub:contrib"]')
    expect(page.locator(".page-h1")).to_have_text("Мої матеріали")
    # Меню профілю: колишній «Мій контент» — «Перевірка актуальності».
    expect(page.locator('#me-menu [data-act="my-content"]')).to_contain_text(
        "Перевірка актуальності")


def _zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


def test_tile_button_differs_from_opening_the_card(page, live_url):
    login(page, live_url, "admin")
    package = list(_zip_bytes({"tile-skill/SKILL.md":
                               "---\nname: Навичка з плитки\ndescription: Опис\n---\n"}))
    page.evaluate("""async (bytes) => {
        const sec = (await api('/catalog/sections')).find(s => s.name === 'Матеріали для тестів');
        const file = new File([new Uint8Array(bytes)], 'tile-skill.zip', { type: 'application/zip' });
        await uploadSkillPackage(file, null, 'published', { section_id: sec.id });
        await ensureHubData(true);
        await go('hub:all');
    }""", package)
    skill = page.locator("#cat-grid article.mdc", has_text="Навичка з плитки")
    # Навичка: кнопка одразу завантажує пакет, картка при цьому не відкривається.
    expect(skill.locator(".mdc-act")).to_have_text("Завантажити")
    with page.expect_download() as download:
        skill.locator(".mdc-act").click()
    assert download.value.suggested_filename.endswith(".zip")
    expect(page.locator(".md-modal")).to_have_count(0)
    # Клік по самій плитці відкриває картку.
    skill.locator(".mdc-title").click()
    expect(page.locator(".md-modal")).to_be_visible()
    page.keyboard.press("Escape")
    # Інструкцію читають у картці, тож окремої кнопки «Читати» немає.
    expect(page.locator("#cat-grid article.mdc", has_text="Регламент закупівель")
           .locator(".mdc-act")).to_have_count(0)
    expect(page.locator("#cat-grid article.mdc", has_text="Промпт для звіту")
           .locator(".mdc-act")).to_have_text("Копіювати")


def test_login_page_names_admin_and_explains_password(page, live_url):
    page.goto(live_url)
    contact = page.locator("#login-contact")
    expect(contact).to_be_visible()
    expect(contact.locator("a")).to_have_attribute("href", f"mailto:{ADMIN_EMAIL}")
    expect(page.locator("#login-hint")).to_be_hidden()
    page.fill("#login-username", "olga@metinvestholding.com")
    page.fill("#login-password", "Corporate-Password-1")
    page.click("#login-form button[type=submit]")
    expect(page.locator("#login-error")).to_have_text("Невірний логін або пароль")
    expect(page.locator("#login-hint")).to_be_visible()
    expect(page.locator("#login-hint")).to_contain_text(
        "Пароль від корпоративної пошти тут не підходить")


def test_admin_changes_admin_email(page, live_url):
    login(page, live_url, "admin")
    page.click('#mode-seg [data-mode="admin"]')
    page.click('#sidebar [data-dest="admin:contact"]')
    field = page.locator("#admin-email")
    expect(field).to_have_value(ADMIN_EMAIL)
    try:
        field.fill("не-пошта")
        page.click("#admin-email-save")
        expect(page.locator("#toast")).to_contain_text("name@example.com")
        field.fill("hub.admin@example.com")
        page.click("#admin-email-save")
        page.click('#mode-seg [data-mode="hub"]')
        expect(page.locator("#home-admin a")).to_have_attribute(
            "href", "mailto:hub.admin@example.com")
        # Порожня адреса ховає рядок на головній.
        page.evaluate("() => api('/catalog/settings', { method: 'POST', "
                      "body: { admin_contact_email: '' } }).then(s => { catalogState.settings = s; "
                      "return go('hub:home'); })")
        expect(page.locator("#home-admin")).to_be_hidden()
    finally:
        page.evaluate("(email) => api('/catalog/settings', { method: 'POST', "
                      "body: { admin_contact_email: email } })", ADMIN_EMAIL)
