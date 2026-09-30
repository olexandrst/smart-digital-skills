"""Резервна копія бази SQLite — безпечна на працюючому застосунку.

Запуск:  python -m scripts.backup_db [шлях або каталог]

Чому не `cp`. Звичайне копіювання файлу бази під навантаженням дає копію в
довільний момент транзакції: якщо в цю мить хтось писав, у копії буде
пошкоджена сторінка, і про це стане відомо лише тоді, коли копія знадобиться.
Онлайн-бекап SQLite (`Connection.backup`) копіює узгоджений знімок і сам
перезапускається, якщо база змінилася під час копіювання.

За замовчуванням копія лягає в `<INSTANCE_DIR>/backups/profihub-РРРР-ММ-ДД.db`.
Старі копії не видаляються — ротацію робить той, хто планує запуск.
"""
import os
import sqlite3
import sys
from datetime import date

from sqlalchemy.engine import make_url

from app import create_app
from app.config import INSTANCE_DIR


def main(argv):
    app = create_app()
    url = make_url(app.config["SQLALCHEMY_DATABASE_URI"])
    if url.get_backend_name() != "sqlite":
        raise SystemExit("Скрипт для SQLite; поточна база: " + url.get_backend_name())

    src = url.database
    if not src or not os.path.exists(src):
        raise SystemExit("Файл бази не знайдено: %s" % src)

    target = argv[1] if len(argv) > 1 else os.path.join(INSTANCE_DIR, "backups")
    if target.endswith(".db"):
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    else:
        os.makedirs(target, exist_ok=True)
        target = os.path.join(target, "profihub-%s.db" % date.today().isoformat())

    # mode=ro — читаємо, нічого не змінюючи навіть випадково.
    source = sqlite3.connect("file:%s?mode=ro" % src, uri=True)
    dest = sqlite3.connect(target)
    try:
        with dest:
            source.backup(dest)
    finally:
        dest.close()
        source.close()

    print("✓ Копію збережено: %s (%.1f МБ)"
          % (target, os.path.getsize(target) / 1024 / 1024))


if __name__ == "__main__":
    main(sys.argv)
