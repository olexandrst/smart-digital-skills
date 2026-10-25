"""Браузерні тести: застосунок на тимчасовій базі й Chromium через Playwright.

Сервер і браузер — одні на весь прогін: піднімати їх для кожного модуля
довго, а дані тестів одне одному не заважають (кожен тест створює своє).

Запуск:
    pip install -r requirements-dev.txt
    python -m playwright install chromium   # якщо браузера ще немає
    pytest tests/e2e

Без Playwright чи браузера тести пропускаються, решта набору не страждає.
"""
import os
import tempfile
import threading
from datetime import datetime

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from werkzeug.serving import make_server  # noqa: E402

from app import create_app  # noqa: E402
from app.config import BaseConfig  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Role, User, UserRole  # noqa: E402
from tests.e2e.helpers import PASSWORD  # noqa: E402

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
    """Автор, тестувальник і менеджер; чотири опубліковані матеріали з тегами,
    двоє з них в обраному тестувальника."""
    from app.core.schema import seed_catalog_terms
    with app.app_context():
        db.create_all()
        seed_catalog_terms()
        manager_role = Role(code="skill_manager", name="Менеджер каталогу")
        db.session.add_all([Role(code="admin", name="Адміністратор"), manager_role])
        # Знайомство з інтерфейсом уже пройдене — інакше воно перекрило б екран.
        done = datetime.utcnow()
        for username, name in (("author", "Автор Матеріалів"), ("tester", "Тестувальник"),
                               ("manager", "Менеджер Каталогу")):
            db.session.add(User(username=username, full_name=name, is_active=True,
                                password_hash=hash_password(PASSWORD),
                                tour_completed_at=done))
        db.session.flush()
        manager = User.query.filter_by(username="manager").one()
        db.session.add(UserRole(user_id=manager.id, role_id=manager_role.id))
        db.session.commit()

    client = app.test_client()

    def token(username):
        res = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
        return {"Authorization": f"Bearer {res.get_json()['access_token']}"}

    author = token("author")
    ids = {}
    for kind, name, extra in (
        ("instruction", "Питання та відповіді (FAQ)",
         {"body": "## Питання\n\nВідповіді.", "tags": "довідка, хаб"}),
        ("instruction", "Регламент закупівель",
         {"body": "Кроки закупівлі.", "tags": "закупівлі, регламент"}),
        ("link", "Корисний сайт", {"url": "https://example.com"}),
        ("prompt", "Промпт для звіту", {"body": "Склади звіт: {дані}", "tags": "закупівлі, звіти"}),
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


@pytest.fixture(scope="session")
def live_app():
    app = create_app(E2EConfig)
    _seed(app)
    return app


@pytest.fixture(scope="session")
def live_url(live_app):
    app = live_app
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()


@pytest.fixture(scope="session")
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
