"""Підготовка бази: версійні міграції Alembic + довідкові дані.

Запуск: python -m scripts.init_db

Схему накатує Alembic (`alembic upgrade head`) — той самий шлях, що й у
production. База, створена до переходу на міграції, позначається штампом
поточної версії, щоб Alembic не намагався створити наявні таблиці повторно.
"""
import os

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app import create_app
from app.extensions import db
from app.core.schema import sync_reference_data

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _alembic_config():
    return Config(os.path.join(BASE_DIR, "alembic.ini"))


def main():
    app = create_app()
    cfg = _alembic_config()

    with app.app_context():
        inspector = inspect(db.engine)
        tables = set(inspector.get_table_names())

        if tables and "alembic_version" not in tables:
            # База створена до переходу на міграції: доганяємо відсутні колонки
            # старим additive-способом і позначаємо схему поточною версією.
            from app.core.schema import sync_schema
            sync_schema()
            command.stamp(cfg, "head")
            print("✓ Наявну базу оновлено й позначено поточною версією схеми")
        else:
            command.upgrade(cfg, "head")
            print("✓ Міграції застосовано")

        sync_reference_data()
        print("✓ Довідники синхронізовано:", app.config["SQLALCHEMY_DATABASE_URI"])


if __name__ == "__main__":
    main()
