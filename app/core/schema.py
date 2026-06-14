"""Легка additive-автоміграція схеми для SQLite (MVP без Alembic).

Додає відсутні nullable-колонки до існуючих таблиць (`ALTER TABLE ADD COLUMN`),
щоб після оновлення коду не доводилось вручну перестворювати базу. Покриває
лише безпечні additive-зміни; складні міграції — через Alembic на наступних етапах.
"""
import sqlalchemy as sa
from app.extensions import db


def _column_ddl(col, dialect):
    # SQLite ALTER TABLE ADD COLUMN не дозволяє NOT NULL без значення за замовчуванням.
    if not col.nullable and col.default is None and col.server_default is None:
        return None
    coltype = col.type.compile(dialect=dialect)
    return f'"{col.name}" {coltype}'


def sync_schema():
    """Створює відсутні таблиці та дописує відсутні nullable-колонки."""
    db.create_all()

    engine = db.engine
    inspector = sa.inspect(engine)
    existing_tables = set(inspector.get_table_names())

    for table in db.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        existing_cols = {c["name"] for c in inspector.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing_cols or col.primary_key:
                continue
            ddl = _column_ddl(col, engine.dialect)
            if ddl is None:
                continue
            with engine.begin() as conn:
                conn.execute(sa.text(f'ALTER TABLE "{table.name}" ADD COLUMN {ddl}'))
