"""Фабрика застосунку Smart-ProfiHub."""
import os
from flask import Flask, jsonify, send_from_directory

from app.config import get_config, INSTANCE_DIR
from app.extensions import db, jwt, cors
from app.core.errors import register_error_handlers


def create_app(config_object=None):
    app = Flask(__name__, static_folder="static", static_url_path="")
    app.config.from_object(config_object or get_config())

    os.makedirs(INSTANCE_DIR, exist_ok=True)

    db.init_app(app)
    jwt.init_app(app)
    cors.init_app(app, resources={r"/api/*": {"origins": "*"}})

    # Імпорт моделей реєструє таблиці в metadata.
    from app import models  # noqa: F401

    from app.api import register_blueprints
    register_blueprints(app)
    register_error_handlers(app)
    _register_jwt_handlers()

    # Additive-автоміграція схеми (SQLite, MVP без Alembic).
    if app.config.get("AUTO_MIGRATE", True):
        from app.core.schema import sync_schema
        with app.app_context():
            sync_schema()

    # Планувальник тижневого скидання квот (понеділок 00:05 UTC).
    if app.config.get("ENABLE_SCHEDULER", True):
        _start_scheduler(app)

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "service": "smart-profihub"})

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/files/<guid>/<path:filename>")
    def public_user_file(guid, filename):
        """Пряме клікабельне посилання на файл користувача: /files/<GUID>/<FILE>.

        GUID (storage_uid) виступає як неперебірний капабіліті-токен. Каталог —
        усередині застосунку (instance/user_files). send_from_directory захищає
        від виходу за межі теки.
        """
        base = os.path.join(app.config["USER_FILES_DIR"], guid)
        return send_from_directory(base, filename)

    return app


_scheduler = None


def _start_scheduler(app):
    """Запускає APScheduler з тижневим скиданням лічильників квот."""
    global _scheduler
    if _scheduler is not None:
        return
    # У dev із reloader стартуємо лише в робочому процесі (уникаємо подвоєння).
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        app.logger.warning("APScheduler не встановлено — тижневе скидання працює "
                           "лише «ліниво» за зсувом тижневого вікна.")
        return

    from app.services import quota_service

    def _job():
        with app.app_context():
            quota_service.reset_all()

    _scheduler = BackgroundScheduler(daemon=True, timezone="UTC")
    _scheduler.add_job(_job, "cron", day_of_week="mon", hour=0, minute=5,
                       id="weekly_quota_reset", replace_existing=True)
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
