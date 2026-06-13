"""Створення схеми бази даних (db.create_all).

Запуск: python -m scripts.init_db
"""
from app import create_app
from app.extensions import db


def main():
    app = create_app()
    with app.app_context():
        db.create_all()
        print("✓ Схему бази даних створено:", app.config["SQLALCHEMY_DATABASE_URI"])


if __name__ == "__main__":
    main()
