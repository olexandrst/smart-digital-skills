"""Браузерні перевірки хвилі А з фідбеку тестування.

- п. 2.2 — колір картки лише за видом;
- п. 3   — заголовки з жирних рядків Word і зображення в тексті;
- п. 5 і 8 — нова головна, сторінка «Мій розвиток», теги на плитках;
- п. 6   — опитування під час виходу й на початку наступного сеансу.
"""

from tests.e2e.helpers import expect, login, settings
from tests.test_files import PNG


def _relogin(page, base, username):
    page.evaluate("() => { localStorage.clear(); sessionStorage.clear(); }")
    login(page, base, username)


# ---- П. 5 і 8: головна — привітання з рівнем і два блоки ----

def test_home_has_level_line_and_two_blocks(page, live_url):
    # Свіжий матеріал публікує адміністратор: у користувача він пройшов би розгляд.
    login(page, live_url, "admin")
    page.evaluate("""() => api('/catalog/resources', { method: 'POST', body: {
        resource_type: 'instruction', name: 'Свіжа інструкція', description: 'Нове.',
        body: 'Текст.', status: 'published' } })""")
    _relogin(page, live_url, "tester")
    level = page.locator("#home-level")
    expect(level).to_be_visible()
    expect(level).to_contain_text("Рівень роботи з AI ще не визначено")
    expect(level.locator("#home-dev")).to_have_text("Мій розвиток →")
    titles = page.locator("#cat-personal h2")
    expect(titles.last).to_have_text("Нещодавно додані")
    assert set(titles.all_text_contents()) <= {"Рекомендовано вам", "Нещодавно додані"}
    # Каталогу з фільтрами на головній більше немає — він у «Матеріалах».
    expect(page.locator("#cat-grid")).to_have_count(0)
    expect(page.locator("#cat-kinds")).to_have_count(0)
    fresh = page.locator("#fresh-row article.mdc")
    assert fresh.count() <= 8
    if not fresh.count():   # малий каталог: усе нове вже серед рекомендацій
        expect(page.locator("#cat-personal")).to_contain_text("вже серед рекомендованих")
    # Те, що вже є серед рекомендацій, у новинках не повторюється.
    recs = set(page.locator("#rec-row .mdc-title").all_text_contents())
    assert not recs & set(page.locator("#fresh-row .mdc-title").all_text_contents())
    # Щойно опублікований матеріал одразу видно на головній.
    expect(page.locator("#cat-personal")).to_contain_text("Свіжа інструкція")

    # Пошук із головної веде в «Матеріали» й одразу звужує каталог.
    page.fill("#global-q", "регламент")
    expect(page.locator(".page-h1")).to_have_text("Матеріали")
    expect(page.locator("#cat-grid article.mdc")).to_have_count(1)
    page.fill("#global-q", "")

    page.click('#sidebar [data-dest="hub:home"]')
    page.click("#home-all")
    expect(page.locator(".page-h1")).to_have_text("Матеріали")


def _set_tester_level(page, level_name, date=None):
    """Рівень вносить адміністратор у редакторі користувача (фідбек, п. 4)."""
    page.evaluate("""async ([name, date]) => {
        const uid = (await api('/users')).find(u => u.username === 'tester').id;
        const lv = name ? (await api('/catalog/terms?kind=maturity')).find(t => t.name === name).id : null;
        await api(`/users/${uid}`, { method: 'PATCH', body: Object.assign(
            { maturity_level_id: lv }, date ? { maturity_assessed_at: date } : {}) });
    }""", [level_name, date])


def test_my_development_shows_level_date_next_step_and_paths(page, live_url):
    login(page, live_url)
    page.click('#sidebar [data-dest="hub:dev"]')
    expect(page.locator(".page-h1")).to_have_text("Мій розвиток")
    expect(page.locator(".dev-level-name")).to_have_text("Ще не визначено")
    expect(page.locator(".dev-level")).to_contain_text(
        "Рівень визначається за результатами корпоративного тестування")
    # Сам користувач рівень не змінює, а тесту в хабі поки немає — кнопок теж.
    expect(page.locator(".dev-level button, .dev-level a")).to_have_count(0)

    _relogin(page, live_url, "admin")
    _set_tester_level(page, "User", "2026-09-15")
    try:
        _relogin(page, live_url, "tester")
        page.click('#sidebar [data-dest="hub:dev"]')
        expect(page.locator(".dev-level-name")).to_have_text("User")
        expect(page.locator(".dev-date")).to_have_text("Визначено 15.09.2026")
        expect(page.locator(".dev-ladder li.on")).to_have_text("User")
        expect(page.locator(".dev-ladder li.done")).to_have_text("Aware")
        expect(page.locator(".dev-next")).to_contain_text("Наступний крок — Integrator")
        expect(page.locator(".dev h2").last).to_have_text("Навчальні маршрути")

        page.click('#sidebar [data-dest="hub:home"]')
        expect(page.locator("#home-level")).to_contain_text("Ваш рівень: User")
    finally:
        _relogin(page, live_url, "admin")
        _set_tester_level(page, None)


def test_tag_opens_all_materials_with_it(page, live_url):
    login(page, live_url)
    page.click('#sidebar [data-dest="hub:all"]')
    card = page.locator("#cat-grid article.mdc", has_text="Регламент закупівель")
    tags = card.locator("button.mdc-tag-link")
    expect(tags).to_have_text(["закупівлі", "регламент"])
    tags.first.click()
    expect(page.locator(".md-modal")).to_have_count(0)        # картка не відкрилась
    expect(page.locator("#cat-active")).to_contain_text("Тег: закупівлі")
    names = page.locator("#cat-grid .mdc-title")
    expect(names).to_have_count(2)
    assert set(names.all_text_contents()) == {"Регламент закупівель", "Промпт для звіту"}

    # Тег у відкритій картці робить те саме, а картку закриває.
    page.click("#cat-reset")
    page.locator("#cat-grid article.mdc", has_text="Питання та відповіді").click()
    page.locator('.md-modal button.mdc-tag-link:has-text("довідка")').click()
    expect(page.locator(".md-modal")).to_have_count(0)
    expect(page.locator("#cat-grid .mdc-title")).to_have_text(["Питання та відповіді (FAQ)"])


# ---- П. 2.2: колір картки лише за видом ----

def test_card_colour_follows_kind(page, live_url):
    login(page, live_url, "manager")
    page.click('#sidebar [data-dest="hub:all"]')
    tiles = page.locator("#cat-grid article.mdc", has_text="Опис для тесту").locator(".mdc-top .mdc-tile")
    classes = {}
    for i in range(tiles.count()):
        card = page.locator("#cat-grid article.mdc", has_text="Опис для тесту").nth(i)
        kind = card.locator(".mdc-tag").first.text_content()
        classes.setdefault(kind, set()).add(tiles.nth(i).get_attribute("class"))
    assert all(len(v) == 1 for v in classes.values()), classes
    # У редакторі матеріалу поля «Колір плитки» більше немає.
    rid = page.evaluate("() => catalogState.items.find(i => i.kind === 'instruction').id")
    page.evaluate("(id) => openResourceEditor(id)", rid)
    page.wait_for_selector("#re-type")
    expect(page.locator("#re-accent")).to_have_count(0)


# ---- П. 6: опитування, коли людина закінчує роботу ----

def _survey_always(page):
    settings(page, survey_min_views=0, survey_days_between=0,
             survey_days_after_answer=0, survey_days_after_dismiss=0)


def _survey_defaults(page):
    settings(page, survey_min_views=5, survey_days_between=30,
             survey_days_after_answer=90, survey_days_after_dismiss=14)


def test_survey_on_logout_and_at_next_session(page, live_url, live_app):
    from app.models import SurveyResponse
    login(page, live_url, "admin")
    _survey_always(page)
    try:
        _relogin(page, live_url, "tester")
        # «Вийти»: спершу одне питання, потім вихід — що б людина не обрала.
        page.click("#me-btn")
        page.click("#logout-btn")
        modal = page.locator(".sv-modal")
        expect(modal).to_contain_text("Перш ніж вийти")
        modal.locator('.sv-score[data-score="9"]').click()
        page.fill("#sv-text", "Зручна головна")
        page.click("#sv-send")
        expect(page.locator("#login-form")).to_be_visible()
        with live_app.app_context():
            row = SurveyResponse.query.filter_by(context="logout").one()
            assert (row.score, row.comment) == (9, "Зручна головна")

        # Вкладку закрили без «Вийти»: наступний сеанс питає про минулий.
        login(page, live_url, "tester")
        page.evaluate("""() => localStorage.setItem('hub-session', JSON.stringify(
            { user: state.user.id, at: Date.now() - 2 * 3600 * 1000 }))""")
        tab = page.context.new_page()
        tab.goto(live_url)
        bar = tab.locator(".survey-bar")
        expect(bar).to_contain_text("Як вам минулий сеанс?")
        bar.locator(".sv-close").click()
        expect(bar).to_have_count(0)

        # Свіжа ознака сеансу (сусідня вкладка) — не питаємо.
        tab.evaluate("() => sessionStorage.clear()")
        tab.reload()
        tab.wait_for_selector("#sidebar .sb-item")
        tab.wait_for_timeout(2200)
        expect(tab.locator(".survey-bar")).to_have_count(0)
        tab.close()
    finally:
        _relogin(page, live_url, "admin")
        _survey_defaults(page)


# ---- П. 3: заголовки з жирних рядків і зображення в тексті ----

def test_word_paste_bold_lines_become_headings(page, live_url):
    login(page, live_url)
    page.click("#cat-add")
    page.click('.wiz-kind[data-kind="instruction"]')
    page.click("#wiz-editor .rte-area")
    page.evaluate("""() => {
        const html = `<html xmlns:o="urn:schemas-microsoft-com:office:office"><body>
          <p class=MsoNormal><b>Питання та відповіді (FAQ)</b></p>
          <p class=MsoNormal>Відповіді на поширені запитання.</p>
          <p class=MsoNormal><b>Не можу знайти матеріал</b></p>
          <p class=MsoNormal>Спробуйте:</p>
          <p class=MsoListParagraph style='mso-list:l0 level1 lfo1'><span style='mso-list:Ignore'>1.<span>&nbsp;</span></span>Ключові слова</p>
          <p class=MsoListParagraph style='mso-list:l0 level1 lfo1'><span style='mso-list:Ignore'>2.<span>&nbsp;</span></span>Фільтри</p>
          <p class=MsoNormal><b>Важливо:</b> не додавайте паролі.</p>
          </body></html>`;
        const dt = new DataTransfer();
        dt.setData("text/html", html);
        dt.setData("text/plain", "FAQ");
        document.querySelector("#wiz-editor .rte-area").dispatchEvent(
            new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
    }""")
    page.click('#wiz-editor [data-mode="source"]')
    assert page.locator("#wiz-editor .rte-src").input_value() == (
        "# Питання та відповіді (FAQ)\n\nВідповіді на поширені запитання.\n\n"
        "## Не можу знайти матеріал\n\nСпробуйте:\n\n1. Ключові слова\n2. Фільтри\n\n"
        "**Важливо:** не додавайте паролі.")


def test_image_in_text_saved_and_shown_in_card(page, live_url):
    login(page, live_url)
    page.click("#cat-add")
    page.click('.wiz-kind[data-kind="prompt"]')
    # Промпт копіюють у модель як текст — картинок у ньому немає.
    expect(page.locator('#wiz-editor [data-cmd="image"]')).to_be_hidden()
    page.click("#wiz-rekind")
    page.click('.wiz-kind[data-kind="instruction"]')
    area = page.locator("#wiz-editor .rte-area")
    area.click()
    page.keyboard.type("Форма заявки:")
    page.locator("#wiz-editor .rte-file").set_input_files(
        files=[{"name": "Форма заявки.png", "mimeType": "image/png", "buffer": PNG}])
    expect(area.locator("img.md-img")).to_have_count(1)
    page.click('#wiz-editor [data-mode="source"]')
    md = page.locator("#wiz-editor .rte-src").input_value()
    assert md.startswith("Форма заявки:") and "![Форма заявки](/api/catalog/files/" in md
    page.click('#wiz-editor [data-mode="visual"]')

    page.click("#wiz-next")
    page.fill("#wiz-name", "Заявка з картинкою")
    page.fill("#wiz-desc", "Інструкція з картинкою в тексті.")
    page.click("#wiz-draft")
    img = page.locator(".md-modal .md-doc img.md-img")
    expect(img).to_have_count(1)
    page.wait_for_function("""() => {
        const i = document.querySelector('.md-modal .md-doc img.md-img');
        return i && i.complete && i.naturalWidth > 0; }""")
    # Картинка з тексту не дублюється серед вкладень картки.
    expect(page.locator(".md-modal .res-file-name")).to_have_count(0)
    img.click()
    expect(page.locator(".lightbox")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator(".lightbox")).to_have_count(0)
