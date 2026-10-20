"""Браузерні перевірки сценаріїв із фідбеку тестування (пункти 1, 2, 3, 5, 9, 13).

П. 3 — візуальний редактор тексту (app/static/editor.js): він зберігає той самий
Markdown, тож перевіряємо, що оформлення доходить до картки без змін.

Логіка цих сценаріїв живе у фронтенді (app/static/app.js), тож перевірити її
можна лише в браузері. Тести піднімають застосунок на тимчасовій базі й
відкривають його в Chromium через Playwright.

Запуск:
    pip install -r requirements-dev.txt
    python -m playwright install chromium   # якщо браузера ще немає
    pytest tests/e2e

Без Playwright чи браузера модуль пропускається, решта набору не страждає.
"""
import os
import tempfile
import threading
from datetime import datetime

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

from werkzeug.serving import make_server  # noqa: E402

from app import create_app  # noqa: E402
from app.config import BaseConfig  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Role, User  # noqa: E402

PASSWORD = "Passw0rd!"
_TMP = tempfile.mkdtemp(prefix="hub_e2e_")


class E2EConfig(BaseConfig):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(_TMP, "e2e.db")
    JWT_SECRET_KEY = "e2e"
    SECRET_KEY = "e2e"
    AUTO_MIGRATE = False
    SYNC_REFERENCE_DATA = False
    ENABLE_SCHEDULER = False
    # Лише вхід за паролем — незалежно від того, що налаштовано в оточенні.
    ENTRA_TENANT_ID = None
    ENTRA_CLIENT_ID = None
    ENTRA_ALLOW_PASSWORD_LOGIN = True
    SKILL_PACKAGES_DIR = os.path.join(_TMP, "skill_packages")
    SKILL_ICONS_DIR = os.path.join(_TMP, "skill_icons")
    USER_FILES_DIR = os.path.join(_TMP, "user_files")


def _seed(app):
    """Двоє користувачів, чотири опубліковані матеріали, двоє з них в обраному."""
    from app.core.schema import seed_catalog_terms
    with app.app_context():
        db.create_all()
        seed_catalog_terms()
        db.session.add_all([Role(code="admin", name="Адміністратор"),
                            Role(code="skill_manager", name="Менеджер каталогу")])
        # Знайомство з інтерфейсом уже пройдене — інакше воно перекрило б екран.
        done = datetime.utcnow()
        for username, name in (("author", "Автор Матеріалів"), ("tester", "Тестувальник")):
            db.session.add(User(username=username, full_name=name, is_active=True,
                                password_hash=hash_password(PASSWORD),
                                tour_completed_at=done))
        db.session.commit()

    client = app.test_client()

    def token(username):
        res = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
        return {"Authorization": f"Bearer {res.get_json()['access_token']}"}

    author = token("author")
    ids = {}
    for kind, name, extra in (
        ("instruction", "Питання та відповіді (FAQ)", {"body": "## Питання\n\nВідповіді."}),
        ("instruction", "Регламент закупівель", {"body": "Кроки закупівлі."}),
        ("link", "Корисний сайт", {"url": "https://example.com"}),
        ("prompt", "Промпт для звіту", {"body": "Склади звіт: {дані}"}),
    ):
        res = client.post("/api/catalog/resources", headers=author, json={
            "resource_type": kind, "name": name, "description": "Опис для тесту.",
            "status": "published", **extra})
        assert res.status_code == 201, res.get_json()
        ids[name] = res.get_json()["id"]

    tester = token("tester")
    for name in ("Питання та відповіді (FAQ)", "Корисний сайт"):
        res = client.post("/api/catalog/favorites", headers=tester,
                          json={"item_type": "resource", "item_id": ids[name]})
        assert res.status_code == 200, res.get_json()


@pytest.fixture(scope="module")
def live_url():
    app = create_app(E2EConfig)
    _seed(app)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as pw:
        try:
            chromium = pw.chromium.launch()
        except Exception as exc:   # браузер не встановлено
            pytest.skip(f"Chromium недоступний: {exc}")
        yield chromium
        chromium.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1280, "height": 860}, locale="uk-UA")
    pg = context.new_page()
    errors = []
    pg.on("pageerror", lambda exc: errors.append(str(exc)))
    yield pg
    context.close()
    assert not errors, f"Помилки JavaScript: {errors}"


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
    expect(page.locator("#wiz-next")).to_have_text("Опублікувати")

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
    page.click("#wiz-next")
    page.wait_for_selector(".md-modal")

    statuses = dict(page.evaluate(
        "async () => (await api('/catalog/resources?mine=1')).map(r => [r.name, r.status])"))
    assert statuses["Тижневий звіт"] == "draft"
    assert statuses["Місячний звіт"] == "published"


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


# ---- П. 5: спершу рівень і наступний крок, потім навчальні маршрути ----

def test_home_shows_level_and_next_step_before_paths(page, live_url):
    login(page, live_url)
    page.evaluate("""async () => {
        const levels = await api('/catalog/terms?kind=maturity');
        await api('/catalog/profile', { method: 'PATCH',
                                        body: { maturity_level_id: levels[0].id } });
    }""")
    page.reload()
    page.wait_for_selector("#cat-personal .lvl-next")
    order = page.evaluate("""() => [...document.querySelectorAll(
        '#cat-personal h2, #cat-personal .lvl-next, #cat-personal .nudge-txt b')]
        .map(e => e.textContent.trim())""")
    level = next(i for i, t in enumerate(order) if t.startswith("Ваш рівень"))
    step = next(i for i, t in enumerate(order) if t.startswith("Наступний крок"))
    paths = next(i for i, t in enumerate(order) if "маршрут" in t.lower())
    assert level < step < paths, order


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
