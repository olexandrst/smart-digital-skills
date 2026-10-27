"""Конфігурація застосунку (Dev/Prod)."""
import os
from datetime import timedelta

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))

# Azure App Service задає WEBSITE_SITE_NAME сам — за нею й упізнаємо платформу.
ON_APP_SERVICE = bool(os.getenv("WEBSITE_SITE_NAME"))

# Кореневий каталог стану: база даних, пакети та іконки навичок, вкладення
# до карток каталогу.
#
# На App Service код у /home/site/wwwroot перезаписується кожним деплоєм, тому
# дані мають лежати поза ним — у /home/data. Раніше це доводилось задавати
# вручну через INSTANCE_DIR, і забута змінна означала втрату всього контенту
# при наступному деплої. Тепер шлях підставляється сам; явний INSTANCE_DIR
# і далі має пріоритет.
_DEFAULT_INSTANCE_DIR = ("/home/data" if ON_APP_SERVICE
                         else os.path.join(BASE_DIR, "instance"))
INSTANCE_DIR = os.getenv("INSTANCE_DIR", _DEFAULT_INSTANCE_DIR)


class BaseConfig:
    # Не кешувати статику (щоб уникати застарілих CSS/JS у браузері).
    SEND_FILE_MAX_AGE_DEFAULT = 0
    # Довіра до X-Forwarded-* вмикається лише за проксі (див. ProductionConfig).
    TRUST_PROXY = os.getenv("TRUST_PROXY", "0") == "1"
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

    # --- SQLite на мережевій файловій системі (Azure App Service /home) ---
    # /home в App Service — це SMB-шара Azure Files, а не локальний диск.
    # Через це:
    #   * WAL там НЕ працює (потребує спільної пам'яті, якої CIFS не дає) —
    #     журнал має лишатися rollback-режимом;
    #   * synchronous=NORMAL безпечний лише разом із WAL, тому тут FULL;
    #   * блокування іноді «підвисає», тож потрібен великий busy_timeout,
    #     інакше запит падає з «database is locked» замість того, щоб зачекати.
    # Значення винесені у змінні, щоб на локальному диску можна було ввімкнути
    # WAL і отримати вищу пропускну здатність.
    SQLITE_BUSY_TIMEOUT = int(os.getenv("SQLITE_BUSY_TIMEOUT", "30"))       # секунди
    SQLITE_JOURNAL_MODE = os.getenv("SQLITE_JOURNAL_MODE", "DELETE").upper()
    SQLITE_SYNCHRONOUS = os.getenv("SQLITE_SYNCHRONOUS", "FULL").upper()
    # Перевірка зовнішніх ключів у SQLite вимкнена за замовчуванням — і це
    # не недогляд. Чотири зв'язки у схемі оголошені без правила ON DELETE:
    #   ideas.source_resource_id, ideas.resource_id,
    #   learning_path_steps.resource_id, user_files.resource_id
    # Поки перевірка вимкнена, SQLite їх просто ігнорує, і видалення
    # матеріалу працює. З SQLITE_FOREIGN_KEYS=1 те саме видалення
    # падає з «FOREIGN KEY constraint failed».
    # Це треба полагодити міграцією (SET NULL там, де запис має пережити
    # батька) ДО переїзду на PostgreSQL: там перевірка увімкнена завжди й
    # вимкнути її не можна.
    SQLITE_FOREIGN_KEYS = os.getenv("SQLITE_FOREIGN_KEYS", "0") == "1"
    # Небагато з'єднань: що менше паралельних писарів у SQLite, то менше
    # конфліктів блокування. Чотирьом потокам gunicorn цього вистачає.
    # Сам пул збирає фабрика застосунку: база в пам'яті (тести) використовує
    # інший клас пулу, який параметра pool_size не приймає.
    SQLITE_POOL_SIZE = int(os.getenv("SQLITE_POOL_SIZE", "5"))

    # --- Вхід через Microsoft Entra ID (NFR-04) ---
    # Порожній TENANT або CLIENT вимикає корпоративний вхід: застосунок
    # лишається на парольному, як у MVP.
    ENTRA_TENANT_ID = os.getenv("ENTRA_TENANT_ID", "")
    ENTRA_CLIENT_ID = os.getenv("ENTRA_CLIENT_ID", "")
    ENTRA_CLIENT_SECRET = os.getenv("ENTRA_CLIENT_SECRET", "")
    # URI повернення; має збігатися із зареєстрованим у застосунку Entra.
    ENTRA_REDIRECT_URI = os.getenv("ENTRA_REDIRECT_URI", "")
    # Ролі з груп каталогу не беруться: їх призначає адміністратор вручну.
    # Парольний вхід лишається для сервісних облікових записів; вимикається
    # ENTRA_ALLOW_PASSWORD_LOGIN=0, коли всі входять через каталог.
    ENTRA_ALLOW_PASSWORD_LOGIN = os.getenv("ENTRA_ALLOW_PASSWORD_LOGIN", "1") == "1"

    # Схема ведеться Alembic: `alembic upgrade head`. AUTO_MIGRATE=1 вмикає
    # запасне additive-доповнення схеми при старті (як було до хвилі 6).
    AUTO_MIGRATE = os.getenv("AUTO_MIGRATE", "0") == "1"
    # Синхронізація довідників і словників при старті (ідемпотентна).
    SYNC_REFERENCE_DATA = os.getenv("SYNC_REFERENCE_DATA", "1") == "1"

    # --- Пакети навичок (хостинг у каталозі, без виконання) ---
    SKILL_PACKAGES_DIR = os.getenv(
        "SKILL_PACKAGES_DIR", os.path.join(INSTANCE_DIR, "skill_packages"))
    # Захист від zip-bomb: максимальний розмір пакета в розпакованому вигляді.
    SKILL_PACKAGE_MAX_UNZIPPED = int(
        os.getenv("SKILL_PACKAGE_MAX_UNZIPPED", str(50 * 1024 * 1024)))
    # PNG-іконки навичок і їхній максимальний розмір (байти).
    SKILL_ICONS_DIR = os.getenv(
        "SKILL_ICONS_DIR", os.path.join(INSTANCE_DIR, "skill_icons"))
    SKILL_ICON_MAX_BYTES = int(os.getenv("SKILL_ICON_MAX_BYTES", str(2 * 1024 * 1024)))

    # --- Вкладення до карток каталогу ---
    # По підкаталогу-GUID на користувача, який завантажив файл.
    USER_FILES_DIR = os.getenv(
        "USER_FILES_DIR", os.path.join(INSTANCE_DIR, "user_files"))
    USER_FILE_MAX_BYTES = int(os.getenv("USER_FILE_MAX_BYTES", str(25 * 1024 * 1024)))

    # Фоновий планувальник щоденних нагадувань власникам про перегляд матеріалів.
    ENABLE_SCHEDULER = os.getenv("ENABLE_SCHEDULER", "1") == "1"


class DevelopmentConfig(BaseConfig):
    DEBUG = True


class ProductionConfig(BaseConfig):
    DEBUG = False
    # Статика роздається з тієї самої мережевої шари, що й база. Без кешу
    # кожен перехід сторінкою читає CSS, JS і шрифт по SMB заново. Версію
    # у посиланнях (?v=NN) міняє реліз, тож довгий кеш безпечний; сам
    # index.html позначається no-cache окремо у фабриці застосунку.
    SEND_FILE_MAX_AGE_DEFAULT = int(os.getenv("STATIC_MAX_AGE", str(30 * 24 * 3600)))
    # Azure термінує TLS на фронті й передає схему в X-Forwarded-Proto.
    # Без довіри до заголовка застосунок вважає з'єднання http-овим і
    # будує зовнішні посилання з неправильною схемою.
    TRUST_PROXY = os.getenv("TRUST_PROXY", "1") == "1"


def get_config():
    """Dev чи Prod. На App Service типово Prod — там development не має сенсу.

    Явний FLASK_ENV завжди сильніший: він потрібен, щоб можна було свідомо
    увімкнути налагодження на тестовому слоті.
    """
    default = "production" if ON_APP_SERVICE else "development"
    env = os.getenv("FLASK_ENV", default).lower()
    return ProductionConfig if env == "production" else DevelopmentConfig
