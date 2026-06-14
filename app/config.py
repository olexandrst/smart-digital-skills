"""Конфігурація застосунку (Dev/Prod)."""
import os
from datetime import timedelta

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")


class BaseConfig:
    # Не кешувати статику (щоб уникати застарілих CSS/JS у браузері).
    SEND_FILE_MAX_AGE_DEFAULT = 0
    # Дефолти ≥32 байти (для dev). У production обов'язково задайте власні у .env.
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-me-in-production-0001")
    JWT_SECRET_KEY = os.getenv(
        "JWT_SECRET_KEY", "dev-jwt-secret-key-change-me-in-production-0001")
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=1)
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=30)

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        "sqlite:///" + os.path.join(INSTANCE_DIR, "profihub.db"),
    )

    # Additive-автоміграція схеми при старті (SQLite, MVP без Alembic).
    AUTO_MIGRATE = os.getenv("AUTO_MIGRATE", "1") == "1"

    # Глобальний перемикач моку для всіх LLM-провайдерів.
    # За замовчуванням увімкнено (MVP працює без зовнішніх ключів).
    # AZURE_FOUNDRY_MOCK лишено для зворотної сумісності.
    LLM_MOCK = os.getenv("LLM_MOCK", os.getenv("AZURE_FOUNDRY_MOCK", "1")) == "1"

    # Azure OpenAI / Azure AI Foundry
    AZURE_FOUNDRY_ENDPOINT = os.getenv("AZURE_FOUNDRY_ENDPOINT", "")
    AZURE_FOUNDRY_API_KEY = os.getenv("AZURE_FOUNDRY_API_KEY", "")
    AZURE_FOUNDRY_API_VERSION = os.getenv("AZURE_FOUNDRY_API_VERSION",
                                          "2024-02-15-preview")

    # OpenAI API
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")

    # Google Gemini API
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

    # --- Скіли-пакети (виконання коду) ---
    # Каталог зі збереженими архівами скілів.
    SKILL_PACKAGES_DIR = os.getenv(
        "SKILL_PACKAGES_DIR", os.path.join(INSTANCE_DIR, "skill_packages"))
    # Каталог файлів користувачів (по підкаталогу-GUID на користувача).
    USER_FILES_DIR = os.getenv(
        "USER_FILES_DIR", os.path.join(INSTANCE_DIR, "user_files"))
    # Максимальний розмір одного файлу користувача (байти).
    USER_FILE_MAX_BYTES = int(os.getenv("USER_FILE_MAX_BYTES", str(25 * 1024 * 1024)))
    # Тимчасова тека для виконання скілів — усередині застосунку (не /tmp).
    SKILL_RUN_DIR = os.getenv("SKILL_RUN_DIR", os.path.join(INSTANCE_DIR, "run_tmp"))

    # Системна ТИЖНЕВА квота токенів за замовчуванням (Admin може змінити).
    DEFAULT_WEEKLY_TOKEN_LIMIT = int(
        os.getenv("DEFAULT_WEEKLY_TOKEN_LIMIT",
                  os.getenv("DEFAULT_USER_TOKEN_LIMIT", "2000")))
    # Планувальник тижневого скидання лічильників (понеділок 00:05 UTC).
    ENABLE_SCHEDULER = os.getenv("ENABLE_SCHEDULER", "1") == "1"
    # Чи дозволено реально виконувати код зі скілів-пакетів.
    SKILL_EXEC_ENABLED = os.getenv("SKILL_EXEC_ENABLED", "1") == "1"
    # Таймаут одного виконання (секунди).
    SKILL_EXEC_TIMEOUT = int(os.getenv("SKILL_EXEC_TIMEOUT", "120"))
    # Ліміт пам'яті процесу (МБ; 0 = без ліміту). За замовчуванням вимкнено,
    # бо RLIMIT_AS на деяких системах заважає старту Python.
    SKILL_EXEC_MEMORY_MB = int(os.getenv("SKILL_EXEC_MEMORY_MB", "0"))
    # Обмеження розміру виводу (символи) та розміру розпакованого архіву (байти).
    SKILL_EXEC_MAX_OUTPUT = int(os.getenv("SKILL_EXEC_MAX_OUTPUT", "100000"))
    SKILL_PACKAGE_MAX_UNZIPPED = int(
        os.getenv("SKILL_PACKAGE_MAX_UNZIPPED", str(50 * 1024 * 1024)))


class DevelopmentConfig(BaseConfig):
    DEBUG = True


class ProductionConfig(BaseConfig):
    DEBUG = False


def get_config():
    env = os.getenv("FLASK_ENV", "development").lower()
    return ProductionConfig if env == "production" else DevelopmentConfig
