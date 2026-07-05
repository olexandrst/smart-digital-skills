"""Створення схеми бази даних (db.create_all).

Запуск: python -m scripts.init_db
"""
from app import create_app
from app.core.schema import sync_schema


def main():
    app = create_app()
    with app.app_context():
        sync_schema()  # create_all + дописування відсутніх колонок
        print("✓ Схему бази даних синхронізовано:", app.config["SQLALCHEMY_DATABASE_URI"])


if __name__ == "__main__":
    main()
