"""Схема та довідкові дані.

**Схема** від хвилі 6 ведеться версійними міграціями Alembic:
`alembic upgrade head`. Функція `sync_schema()` лишається запасним шляхом для
дрібних інсталяцій (AUTO_MIGRATE=1) і для оновлення баз, створених до переходу
на міграції; вона вміє лише безпечні additive-зміни й написана під SQLite.

**Довідкові дані** (`sync_reference_data`) — окрема річ: набір значень
довідників і словник формулювань не є схемою, тож синхронізуються при кожному
старті незалежно від того, чим накатувалася схема. Усі операції ідемпотентні.
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
    # SQLite-специфічно: на PostgreSQL цю зміну робить Alembic-міграція.
    if engine.dialect.name == "sqlite":
        relax_not_null("skills", "model_id")


def sync_reference_data():
    """Довідники й словники — не схема, тому синхронізуються завжди.

    Викликається при старті незалежно від AUTO_MIGRATE: після
    `alembic upgrade head` таблиці вже є, але порожні, а застосунок без
    довідника видів матеріалів не працює.
    """
    seed_catalog_terms()
    seed_search_synonyms()
    migrate_legacy_tags()


def seed_search_synonyms():
    """Початковий словник формулювань задач (BR-07), ідемпотентно.

    Наявні записи не чіпаємо: те, що менеджер відредагував чи вимкнув, має
    переживати перезапуск.
    """
    from app.models import SearchSynonym, DEFAULT_SYNONYMS

    existing = {s.phrase.casefold() for s in SearchSynonym.query.all()}
    added = False
    for phrase, terms in DEFAULT_SYNONYMS:
        if phrase.casefold() in existing:
            continue
        db.session.add(SearchSynonym(phrase=phrase, terms=terms))
        added = True
    if added:
        db.session.commit()


def seed_catalog_terms():
    """Наповнює керовані довідники метаданих значеннями за замовчуванням (FR-04).

    Ідемпотентно: наявні значення не чіпаються, тож перейменування, зроблені
    менеджером, переживають перезапуск застосунку.
    """
    from app.models import (
        CatalogTerm, DEFAULT_TERMS, DEFAULT_MATERIAL_TYPES,
        DEFAULT_STATUS_TERMS, DEFAULT_REUSE_TERMS,
    )

    existing = {(t.kind, t.name_norm) for t in CatalogTerm.query.all()}
    added = False
    for kind, values in DEFAULT_TERMS.items():
        for position, (name, description) in enumerate(values):
            key = (kind, CatalogTerm.normalize(name))
            if key in existing:
                continue
            db.session.add(CatalogTerm(
                kind=kind, name=name, name_norm=key[1],
                description=description, position=position))
            existing.add(key)
            added = True

    # Види матеріалів: запис довідника з поведінкою та оформленням. Саме ці
    # поля роблять вид даними, а не константою — новий вид додається з
    # інтерфейсу й одразу працює в каталозі, фільтрах і редакторі.
    have_codes = {t.code for t in CatalogTerm.query.filter_by(kind="material_type")}
    for position, spec in enumerate(DEFAULT_MATERIAL_TYPES):
        code, name, icon, color, needs_url, needs_body, scope, url_label, body_label = spec
        if code in have_codes:
            continue
        db.session.add(CatalogTerm(
            kind="material_type", code=code, name=name,
            name_norm=CatalogTerm.normalize(name), position=position,
            icon_emoji=icon, color=color, requires_url=needs_url,
            requires_body=needs_body, has_scope=scope,
            url_label=url_label, body_label=body_label))
        added = True

    # Статуси життєвого циклу й акценти оформлення — теж записи довідника:
    # назву, іконку та колір змінює адміністратор, коди лишаються за кодом.
    have_status = {t.code for t in CatalogTerm.query.filter_by(kind="status")}
    for position, (code, name, icon, color) in enumerate(DEFAULT_STATUS_TERMS):
        if code in have_status:
            continue
        db.session.add(CatalogTerm(
            kind="status", code=code, name=name,
            name_norm=CatalogTerm.normalize(name), position=position,
            icon_emoji=icon, color=color))
        added = True

    have_reuse = {t.code for t in CatalogTerm.query.filter_by(kind="reuse_level")}
    for position, (code, name, description) in enumerate(DEFAULT_REUSE_TERMS):
        if code in have_reuse:
            continue
        db.session.add(CatalogTerm(
            kind="reuse_level", code=code, name=name,
            name_norm=CatalogTerm.normalize(name), position=position,
            description=description))
        added = True

    have_accent = {t.code for t in CatalogTerm.query.filter_by(kind="accent")}
    accent_names = {"red": "Червоний", "ink": "Графітовий", "steel": "Сталевий",
                    "amber": "Бурштиновий", "green": "Зелений"}
    from app.models import ACCENTS
    for position, code in enumerate(ACCENTS):
        if code in have_accent:
            continue
        name = accent_names.get(code, code)
        db.session.add(CatalogTerm(
            kind="accent", code=code, name=name,
            name_norm=CatalogTerm.normalize(name), position=position, color=code))
        added = True

    if added:
        db.session.commit()


def migrate_legacy_tags():
    """Переносить теги зі старого рядка через кому в керований довідник (FR-02).

    Одноразова операція: матеріал із уже створеними зв'язками пропускається,
    тому повторний запуск нічого не змінює. Значення, що відрізняються лише
    регістром чи пробілами, зводяться до одного терміна.
    """
    from app.models import CatalogTerm, CatalogResource, CatalogResourceTag

    pending = (CatalogResource.query
               .filter(CatalogResource.tags.isnot(None))
               .filter(CatalogResource.tags != "").all())
    if not pending:
        return
    linked = {row.resource_id for row in
              db.session.query(CatalogResourceTag.resource_id).distinct()}
    known = {t.name_norm: t for t in CatalogTerm.query.filter_by(kind="tag")}

    changed = False
    for res in pending:
        if res.id in linked:
            continue
        seen = set()
        for raw in res.tags.split(","):
            name = " ".join(raw.split())
            norm = CatalogTerm.normalize(name)
            if not name or norm in seen:
                continue
            seen.add(norm)
            term = known.get(norm)
            if term is None:
                term = CatalogTerm(kind="tag", name=name, name_norm=norm)
                db.session.add(term)
                db.session.flush()
                known[norm] = term
            db.session.add(CatalogResourceTag(resource_id=res.id, term_id=term.id))
            changed = True
    if changed:
        db.session.commit()


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
