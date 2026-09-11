"""Середовище Alembic: метадані беремо з застосунку, URL — з конфігурації.

Так міграції й моделі не можуть розійтися: `--autogenerate` бачить рівно ті
таблиці, які оголошені в `app/models`.
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app import create_app
from app.extensions import db
import app.models  # noqa: F401  — імпорт реєструє всі таблиці в metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Застосунок створюємо без автоміграції та планувальника: Alembic лише
# читає конфігурацію, запускати фонові процеси йому не потрібно.
flask_app = create_app()
flask_app.config.update(AUTO_MIGRATE=False, ENABLE_SCHEDULER=False)
config.set_main_option("sqlalchemy.url",
                       flask_app.config["SQLALCHEMY_DATABASE_URI"].replace("%", "%%"))
target_metadata = db.metadata


def run_migrations_offline():
    context.configure(url=config.get_main_option("sqlalchemy.url"),
                      target_metadata=target_metadata, literal_binds=True,
                      compare_type=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(config.get_section(config.config_ini_section),
                                     prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        # render_as_batch — щоб ALTER працював і на SQLite, і на PostgreSQL
        # однаково: без нього SQLite не вміє змінювати колонки.
        context.configure(connection=connection, target_metadata=target_metadata,
                          compare_type=True, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
