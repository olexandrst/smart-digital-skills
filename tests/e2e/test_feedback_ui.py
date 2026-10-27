"""Браузерні перевірки сценаріїв із фідбеку тестування (пункти 1, 2, 3, 9, 13).

П. 3 — візуальний редактор тексту (app/static/editor.js): він зберігає той самий
Markdown, тож перевіряємо, що оформлення доходить до картки без змін.

Логіка цих сценаріїв живе у фронтенді (app/static/app.js), тож перевірити її
можна лише в браузері. Сервер, база й браузер — у tests/e2e/conftest.py.
"""
from tests.e2e.helpers import expect, login, open_wizard_at_describe


# ---- П. 2: вибір виду в «Обраному» не відкриває весь каталог ----

def test_kind_filter_stays_inside_favorites(page, live_url):
    login(page, live_url)
    page.click('#sidebar [data-dest="hub:fav"]')
    cards = page.locator("#cat-grid article.mdc")
    expect(cards).to_have_count(2)

    page.click('.cat-kind[data-kind="instruction"]')
    expect(cards).to_have_count(1)
    expect(cards.first).to_contain_text("Питання та відповіді (FAQ)")
    expect(page.locator(".page-h1")).to_have_text("Обране")
    # Лічильник «Усі» рахує обране, а не весь каталог.
    expect(page.locator('.cat-kind[data-kind="all"] .cat-kind-n')).to_have_text("2")

    page.click("#cat-reset")   # «Скинути всі» лишає в «Обраному»
    expect(cards).to_have_count(2)


# ---- П. 1: майстер завершують дві явні кнопки, перемикача режиму немає ----

def test_wizard_finishes_with_two_explicit_buttons(page, live_url):
    login(page, live_url)
    page.click("#cat-add")
    page.click('.wiz-kind[data-kind="prompt"]')
    page.fill("#wiz-editor .rte-area", "Склади тижневий звіт за даними: {дані}")
    expect(page.locator("#wiz-draft")).to_be_hidden()   # ще не останній крок
    page.click("#wiz-next")
    page.fill("#wiz-name", "Тижневий звіт")
    page.fill("#wiz-desc", "Звіт за тиждень у трьох абзацах.")
    expect(page.locator("#wiz-pub")).to_have_count(0)
    expect(page.locator("#wiz-draft")).to_have_text("Зберегти чернетку")
    # Попередня модерація: користувач подає матеріал менеджерові розділу.
    expect(page.locator("#wiz-next")).to_have_text("Подати на розгляд")

    page.click("#wiz-draft")
    # Після збереження хаб відкриває «Наповнення каталогу» й картку чернетки.
    page.wait_for_selector(".md-modal")
    expect(page.locator(".md-modal .sm-draft")).to_be_visible()
    page.keyboard.press("Escape")
    page.wait_for_selector(".md-modal", state="detached")

    page.click('#sidebar [data-dest="hub:home"]')
    open_wizard_at_describe(page, "prompt", "Склади місячний звіт за даними: {дані}")
    page.fill("#wiz-name", "Місячний звіт")
    page.fill("#wiz-desc", "Звіт за місяць.")
    # Без розділу подати нікому — майстер просить його обрати.
    page.click("#wiz-next")
    expect(page.locator("#wiz-err")).to_contain_text("Оберіть розділ")
    page.select_option("#wiz-section", label="Матеріали для тестів")
    page.click("#wiz-next")
    page.wait_for_selector(".md-modal")
    expect(page.locator(".md-modal .sm-draft.review")).to_contain_text("На розгляді")

    statuses = dict(page.evaluate(
        "async () => (await api('/catalog/resources?mine=1')).map(r => [r.name, r.status])"))
    assert statuses["Тижневий звіт"] == "draft"
    assert statuses["Місячний звіт"] == "review"


# ---- П. 2: попередження про матеріал із тією самою назвою ----

def test_wizard_warns_about_material_with_same_name(page, live_url):
    login(page, live_url)
    open_wizard_at_describe(page, "instruction", "Нова версія регламенту.")
    dup = page.locator("#wiz-dup")
    page.fill("#wiz-name", "регламент   ЗАКУПІВЕЛЬ")
    expect(dup).to_be_visible()
    expect(dup).to_contain_text("Регламент закупівель")
    page.fill("#wiz-name", "Регламент закупівель 2027")
    expect(dup).to_be_hidden()


# ---- П. 13: «Як увійти» — на сторінці входу ----

def test_login_page_explains_how_to_sign_in(page, live_url):
    page.goto(live_url)
    summary = page.locator("#login-help summary")
    expect(summary).to_have_text("Як увійти до «AI Knowledge Hub»")
    steps = page.locator("#login-help-steps")
    expect(steps).to_be_hidden()
    summary.click()
    expect(steps).to_be_visible()
    expect(steps.locator("li").first).to_contain_text("логін і пароль")
    # Кнопки корпоративного входу тут немає — і кроку про неї теж.
    expect(steps).not_to_contain_text("корпоративний акаунт")


# ---- П. 3: візуальний редактор зберігає оформлення як Markdown ----

def test_editor_toolbar_formatting_reaches_the_card(page, live_url):
    login(page, live_url)
    page.click("#cat-add")
    page.click('.wiz-kind[data-kind="instruction"]')
    area = page.locator("#wiz-editor .rte-area")
    area.click()
    page.click('#wiz-editor [data-cmd="h2"]')
    page.keyboard.type("Як подати заявку")
    page.keyboard.press("Enter")
    page.keyboard.type("Заповніть форму ")
    page.click('#wiz-editor [data-cmd="bold"]')
    page.keyboard.type("до п’ятниці")
    page.click('#wiz-editor [data-cmd="bold"]')
    page.keyboard.type(".")
    page.keyboard.press("Enter")
    page.click('#wiz-editor [data-cmd="ol"]')
    page.keyboard.type("Відкрийте портал")
    page.keyboard.press("Enter")
    page.keyboard.type("Натисніть «Нова заявка»")

    # Вкладка Markdown показує те, що буде збережено, і правки в ній видно в оформленні.
    page.click('#wiz-editor [data-mode="source"]')
    src = page.locator("#wiz-editor .rte-src")
    assert src.input_value() == (
        "# Як подати заявку\n\nЗаповніть форму **до п’ятниці**.\n\n"
        "1. Відкрийте портал\n2. Натисніть «Нова заявка»")
    src.fill(src.input_value() + "\n\n> Строк — 5 днів")
    page.click('#wiz-editor [data-mode="visual"]')
    expect(area.locator("blockquote")).to_have_text("Строк — 5 днів")

    page.click("#wiz-next")
    page.fill("#wiz-name", "Заявка через портал")
    page.fill("#wiz-desc", "Покроково, як подати заявку.")
    page.click("#wiz-draft")
    doc = page.locator(".md-modal .md-doc").first
    expect(doc.locator("h2")).to_have_text("Як подати заявку")
    expect(doc.locator("strong")).to_have_text("до п’ятниці")
    expect(doc.locator("ol li")).to_have_count(2)
    expect(doc.locator("blockquote")).to_be_visible()


def test_editor_cleans_text_pasted_from_word(page, live_url):
    login(page, live_url)
    page.click("#cat-add")
    page.click('.wiz-kind[data-kind="instruction"]')
    page.click("#wiz-editor .rte-area")
    page.evaluate("""() => {
        const html = `<p class=MsoNormal><b>Важливо:</b> перевірте <i>кошторис</i>.<o:p></o:p></p>
          <p style='mso-list:l0 level1 lfo1'><span style='mso-list:Ignore'>·<span>&nbsp;</span></span>Перший</p>
          <p style='mso-list:l0 level1 lfo1'><span style='mso-list:Ignore'>·<span>&nbsp;</span></span>Другий</p>
          <p><span style="color:red;font-family:Calibri">Звичайний</span> <img src="data:image/png;base64,AAAA"></p>
          <script>window.__pasted = 1</script>`;
        const dt = new DataTransfer();
        dt.setData("text/html", html);
        dt.setData("text/plain", "Важливо: перевірте кошторис.");
        document.querySelector("#wiz-editor .rte-area").dispatchEvent(
            new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
    }""")
    page.click('#wiz-editor [data-mode="source"]')
    assert page.locator("#wiz-editor .rte-src").input_value() == (
        "**Важливо:** перевірте *кошторис*.\n\n- Перший\n- Другий\n\nЗвичайний")
    assert page.evaluate("() => window.__pasted") is None
    expect(page.locator(".toast")).to_contain_text("Зображ")


def test_prompt_keeps_angle_brackets_and_copies_raw_text(page, live_url):
    login(page, live_url)
    text = "Ти аналітик.\nДокумент: <документ>{текст}</документ>\n\n1. Вимоги\n2. Ризики"
    page.click("#cat-add")
    page.click('.wiz-kind[data-kind="prompt"]')
    page.click('#wiz-editor [data-mode="source"]')
    page.fill("#wiz-editor .rte-src", text)
    page.click('#wiz-editor [data-mode="visual"]')
    expect(page.locator("#wiz-editor .rte-area")).to_contain_text("<документ>{текст}</документ>")
    page.click("#wiz-next")
    page.fill("#wiz-name", "Аналіз документа")
    page.fill("#wiz-desc", "Вимоги й ризики з документа.")
    page.click("#wiz-draft")
    prompt = page.locator(".md-modal .md-prompt")
    expect(prompt).to_contain_text("<документ>{текст}</документ>")
    expect(prompt.locator("ol li")).to_have_count(2)
    saved = page.evaluate(
        "async () => (await api('/catalog/resources?mine=1')).find(r => r.name === 'Аналіз документа').id")
    body = page.evaluate(f"async () => (await api('/catalog/resources/{saved}')).body")
    assert body == text


# ---- П. 9: замість прибраних написів на екрані входу — «Знання · Співпраця · Розвиток» ----

def test_login_badges_fit_between_text_and_photo(page, live_url):
    badges = page.locator("#auth-badges")
    titles = badges.locator(".ab b")
    for width, height, compact in ((1920, 1080, False), (1280, 720, True)):
        page.set_viewport_size({"width": width, "height": height})
        page.goto(live_url)
        expect(badges).to_be_visible()
        expect(titles).to_have_text(["Знання", "Співпраця", "Розвиток"])
        assert ("compact" in badges.get_attribute("class")) is compact
        # Не наїжджають на форму входу й тексти ліворуч.
        card = page.locator(".login-card").bounding_box()
        sub = page.evaluate("""() => { const r = document.createRange();
            r.selectNodeContents(document.querySelector('.auth-sub'));
            return r.getBoundingClientRect().right; }""")
        first = min(badges.locator(".ab").nth(i).bounding_box()["x"] for i in range(3))
        assert first > max(card["x"] + card["width"], sub), (width, first)
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(live_url)
    expect(badges).to_be_hidden()
