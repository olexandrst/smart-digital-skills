"""Легка additive-автоміграція схеми для SQLite (MVP без Alembic).

Створює відсутні таблиці, дописує відсутні колонки (з коректним DEFAULT/NOT NULL
для бекфілу наявних рядків) і за потреби знімає NOT NULL через перебудову
таблиці. Покриває лише безпечні additive-зміни; складні міграції — через Alembic.
"""
import sqlalchemy as sa
from app.extensions import db


def _default_literal(col):
    """SQL-літерал значення за замовчуванням колонки (або None)."""
    d = col.default
    if d is not None and getattr(d, "is_scalar", False):
        val = d.arg
    else:
        return None
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, (int, float)):
        return str(val)
    return "'" + str(val).replace("'", "''") + "'"


def _backfill_value(col):
    """SQL-вираз для заповнення NULL у NOT NULL-колонці при перебудові таблиці."""
    lit = _default_literal(col)
    if lit is not None:
        return lit
    t = col.type
    if isinstance(t, sa.DateTime):
        return "datetime('now')"
    if isinstance(t, (sa.Integer, sa.Numeric, sa.Float, sa.Boolean)):
        return "0"
    return "''"


def _column_ddl(col, dialect):
    """DDL для ADD COLUMN. Повертає None, якщо безпечно додати неможливо."""
    coltype = col.type.compile(dialect=dialect)
    lit = _default_literal(col)
    parts = [f'"{col.name}"', coltype]
    if lit is not None:
        parts.append(f"DEFAULT {lit}")
    if not col.nullable:
        # NOT NULL без значення за замовчуванням не можна додати до існуючих рядків.
        if lit is None and col.server_default is None:
            return None
        parts.append("NOT NULL")
    return " ".join(parts)


def sync_schema():
    """Створює відсутні таблиці та дописує відсутні колонки."""
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

    # Зняти NOT NULL зі skills.model_id (потрібно для скілів-пакетів).
    relax_not_null("skills", "model_id")


def relax_not_null(table_name, column_name):
    """Робить колонку nullable у SQLite через перебудову таблиці (якщо потрібно)."""
    engine = db.engine
    inspector = sa.inspect(engine)
    tables = set(inspector.get_table_names())
    if table_name not in tables or f"{table_name}_old" in tables:
        return
    col = next((c for c in inspector.get_columns(table_name)
                if c["name"] == column_name), None)
    if col is None or col.get("nullable", True):
        return  # уже nullable або колонки немає

    meta_table = db.metadata.tables.get(table_name)
    if meta_table is None:
        return
    old_cols = [c["name"] for c in inspector.get_columns(table_name)]
    common = [c for c in old_cols if c in meta_table.c]
    collist = ", ".join(f'"{c}"' for c in common)

    with engine.begin() as conn:
        conn.execute(sa.text(f'ALTER TABLE "{table_name}" RENAME TO "{table_name}_old"'))
    db.create_all()  # відтворює table_name з метаданих (model_id уже nullable)
    with engine.begin() as conn:
        # Бекфіл значень для NOT NULL-колонок, де у старих рядках можуть бути NULL.
        for cname in common:
            mcol = meta_table.c[cname]
            if not mcol.nullable:
                conn.execute(sa.text(
                    f'UPDATE "{table_name}_old" SET "{cname}"={_backfill_value(mcol)} '
                    f'WHERE "{cname}" IS NULL'))
        conn.execute(sa.text(
            f'INSERT INTO "{table_name}" ({collist}) SELECT {collist} FROM "{table_name}_old"'))
        conn.execute(sa.text(f'DROP TABLE "{table_name}_old"'))
