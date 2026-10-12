"""Фабрика застосунку AI Knowledge Hub."""
import os
import sqlite3

from flask import Flask, jsonify, send_from_directory
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.config import get_config, INSTANCE_DIR
from app.extensions import db, jwt, cors
from app.core.errors import register_error_handlers

# Каталоги стану, які мають існувати ще до першого запиту. Створюємо їх при
# старті, а не ліниво: якщо /home/data недоступний для запису, краще впасти
# одразу з видимою помилкою, ніж через тиждень на завантаженні файлу.
_STATE_DIRS = ("SKILL_PACKAGES_DIR", "SKILL_ICONS_DIR", "USER_FILES_DIR")


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record):
    """Налаштування кожного з'єднання SQLite.

    Викликається для будь-якого движка, тому спершу перевіряємо, що це справді
    SQLite: на PostgreSQL ці PRAGMA не існують.
    """
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cfg = _sqlite_pragmas.settings
    cur = dbapi_connection.cursor()
    try:
        # Чекати на зайняту базу, а не падати одразу. На мережевій шарі
        # блокування знімається не миттєво.
        cur.execute("PRAGMA busy_timeout = %d" % (cfg["busy_timeout"] * 1000))
        cur.execute("PRAGMA journal_mode = %s" % cfg["journal_mode"])
        cur.execute("PRAGMA synchronous = %s" % cfg["synchronous"])
        # Див. коментар до SQLITE_FOREIGN_KEYS у app/config.py: поки в схемі
        # є зв'язки без ON DELETE, увімкнення ламає видалення матеріалів.
        cur.execute("PRAGMA foreign_keys = %s"
                    % ("ON" if cfg["foreign_keys"] else "OFF"))
    finally:
        cur.close()


# Значення за замовчуванням для випадку, коли з'єднання створюється поза
# застосунком (наприклад, у скриптах Alembic).
_sqlite_pragmas.settings = {"busy_timeout": 30, "journal_mode": "DELETE",
                            "synchronous": "FULL", "foreign_keys": False}


def create_app(config_object=None):
    app = Flask(__name__, static_folder="static", static_url_path="")
    app.config.from_object(config_object or get_config())

    os.makedirs(INSTANCE_DIR, exist_ok=True)
    for key in _STATE_DIRS:
        path = app.config.get(key)
        if path:
            os.makedirs(path, exist_ok=True)

    _sqlite_pragmas.settings = {
        "busy_timeout": app.config.get("SQLITE_BUSY_TIMEOUT", 30),
        "journal_mode": app.config.get("SQLITE_JOURNAL_MODE", "DELETE"),
        "synchronous": app.config.get("SQLITE_SYNCHRONOUS", "FULL"),
        "foreign_keys": app.config.get("SQLITE_FOREIGN_KEYS", False),
    }
    _configure_sqlite_pool(app)

    if app.config.get("TRUST_PROXY", False):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    jwt.init_app(app)
    cors.init_app(app, resources={r"/api/*": {"origins": "*"}})

    # Імпорт моделей реєструє таблиці в metadata.
    from app import models  # noqa: F401

    from app.api import register_blueprints
    register_blueprints(app)
    register_error_handlers(app)
    _register_jwt_handlers()

    # Схема ведеться Alembic (`alembic upgrade head`). AUTO_MIGRATE=1 лишає
    # запасне additive-доповнення для дрібних інсталяцій і для баз, створених
    # до переходу на версійні міграції.
    if app.config.get("AUTO_MIGRATE", False):
        from app.core.schema import sync_schema
        with app.app_context():
            sync_schema()

    # Довідкові дані — не схема: синхронізуються завжди, бо після міграції
    # таблиці довідників існують, але порожні.
    if app.config.get("SYNC_REFERENCE_DATA", True):
        from sqlalchemy import inspect as sa_inspect
        from app.core.schema import sync_reference_data
        with app.app_context():
            if not sa_inspect(db.engine).has_table("catalog_terms"):
                # Перший запуск до міграцій: очікуваний стан, не помилка.
                app.logger.info("База ще не мігрована — довідники "
                                "синхронізуються після `alembic upgrade head`.")
            else:
                try:
                    sync_reference_data()
                except Exception as exc:   # не валимо старт через довідники
                    app.logger.warning("Довідники не синхронізовано: %s", exc)

    # Планувальник щоденних нагадувань власникам про перегляд матеріалів.
    if app.config.get("ENABLE_SCHEDULER", True):
        _start_scheduler(app)

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "service": "smart-profihub"})

    @app.get("/")
    def index():
        # index.html не кешуємо навіть у production: саме він посилається на
        # style.css?v=NN та app.js?v=NN. Закешований index віддавав би старі
        # версії ще довго після релізу.
        resp = send_from_directory(app.static_folder, "index.html")
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp

    @app.get("/guide/")
    def guide():
        # Інструкція для користувачів — статична сторінка поруч із
        # застосунком (значок «?» і останній пункт бічної панелі). Як і
        # index.html, не кешується, щоб оновлена інструкція доходила одразу.
        resp = send_from_directory(os.path.join(app.static_folder, "guide"), "index.html")
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp

    return app


def _configure_sqlite_pool(app):
    """Пул з'єднань для файлової бази SQLite.

    Тільки для файлової: `sqlite:///:memory:` (тести) працює на іншому класі
    пулу, який pool_size не приймає, а PostgreSQL має власні розумні дефолти.
    Явно задані SQLALCHEMY_ENGINE_OPTIONS не чіпаємо.
    """
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if app.config.get("SQLALCHEMY_ENGINE_OPTIONS"):
        return
    if not uri.startswith("sqlite:") or ":memory:" in uri:
        return
    size = app.config.get("SQLITE_POOL_SIZE", 5)
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_size": size,
        "max_overflow": size,
        "pool_recycle": 1800,
        "pool_pre_ping": True,
        # timeout у sqlite3 — це те саме очікування зайнятої бази, але вже
        # на рівні драйвера: діє ще до того, як виконається PRAGMA.
        "connect_args": {"timeout": app.config.get("SQLITE_BUSY_TIMEOUT", 30)},
    }


_scheduler = None


def _start_scheduler(app):
    """Запускає APScheduler зі щоденними нагадуваннями про перегляд матеріалів."""
    global _scheduler
    if _scheduler is not None:
        return
    # У dev із reloader стартуємо лише в робочому процесі (уникаємо подвоєння).
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        app.logger.warning("APScheduler не встановлено — нагадування про перегляд "
                           "матеріалів не надсилатимуться.")
        return

    from app.services import review_service

    def _review_job():
        """Нагадування власникам про перегляд матеріалів (AKH-18).

        Дублювання при кількох процесах неможливе не через «запускаємо в одному
        воркері», а через унікальний ключ сповіщення в базі: навіть якщо job
        відпрацює у трьох процесах, запис буде один.
        """
        with app.app_context():
            created = review_service.create_reminders()
            if created:
                app.logger.info("Нагадувань про перегляд створено: %s", created)

    _scheduler = BackgroundScheduler(daemon=True, timezone="UTC")
    _scheduler.add_job(_review_job, "cron", hour=7, minute=0,
                       id="daily_review_reminders", replace_existing=True)
    _scheduler.start()


def _register_jwt_handlers():
    @jwt.expired_token_loader
    def expired(_h, _p):
        return jsonify({"error": "token_expired",
                        "message": "Термін дії токена вичерпано"}), 401

    @jwt.invalid_token_loader
    def invalid(_e):
        return jsonify({"error": "invalid_token",
                        "message": "Недійсний токен"}), 401

    @jwt.unauthorized_loader
    def missing(_e):
        return jsonify({"error": "authorization_required",
                        "message": "Потрібна авторизація"}), 401
