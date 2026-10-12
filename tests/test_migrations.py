"""Міграції Alembic і моделі мають описувати одну й ту саму схему.

Тести застосунку створюють таблиці через create_all, тож розбіжність між
моделями й міграціями вони не помічають. А production-база будується саме
міграціями (startup.sh → `alembic upgrade head`): так одного разу таблиця
notifications існувала в коді, але не в базі на Azure.

Alembic запускається окремим процесом: його env.py бере адресу бази з
конфігурації застосунку, а та читається з оточення під час імпорту. У
процесі тестів конфігурацію вже імпортовано, і міграції пішли б у робочу
базу, а не в тимчасову.
"""
import os
import subprocess
import sys

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app.extensions import db

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))

REMOVED_TABLES = {
    "models", "groups", "group_memberships", "invitations", "group_models",
    "group_skills", "user_skills", "skill_inputs", "chat_sessions",
    "chat_messages", "token_usage_logs", "token_limits", "token_counters",
    "audit_logs",
}


def _alembic(db_path, *args, tmp_path):
    env = dict(os.environ,
               DATABASE_URL=f"sqlite:///{db_path}",
               INSTANCE_DIR=str(tmp_path / "instance"),
               SYNC_REFERENCE_DATA="0", ENABLE_SCHEDULER="0", AUTO_MIGRATE="0")
    proc = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT,
                          env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]


@pytest.fixture
def migrated_db(tmp_path):
    path = tmp_path / "migrated.db"
    _alembic(path, "upgrade", "head", tmp_path=tmp_path)
    return path


def test_migrations_match_models(app, migrated_db):
    engine = create_engine(f"sqlite:///{migrated_db}")
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), db.metadata)
    assert diff == [], f"Схема після міграцій розходиться з моделями: {diff}"


def test_removed_features_have_no_tables(migrated_db):
    tables = set(inspect(create_engine(f"sqlite:///{migrated_db}")).get_table_names())
    assert not tables & REMOVED_TABLES
    assert "notifications" in tables


def test_downgrade_and_upgrade_round_trip(tmp_path, migrated_db):
    """Ревізію «лише каталог» можна відкотити й накотити знову без помилок."""
    _alembic(migrated_db, "downgrade", "a1c3e5f7b9d2", tmp_path=tmp_path)
    tables = set(inspect(create_engine(f"sqlite:///{migrated_db}")).get_table_names())
    assert {"groups", "models", "chat_sessions"} <= tables
    _alembic(migrated_db, "upgrade", "head", tmp_path=tmp_path)
    tables = set(inspect(create_engine(f"sqlite:///{migrated_db}")).get_table_names())
    assert not tables & REMOVED_TABLES


def _insert(conn, table, **values):
    """Вставляє рядок, заповнюючи решту обов'язкових колонок нейтральними
    значеннями: тесту важливі лише поля, які перевіряє міграція."""
    from sqlalchemy import text
    for col in inspect(conn).get_columns(table):
        if col["name"] in values or col["nullable"] or col["name"] == "id":
            continue
        kind = str(col["type"]).upper()
        values[col["name"]] = (0 if any(k in kind for k in ("INT", "BOOL"))
                               else "2026-01-01 00:00:00" if "DATE" in kind else "x")
    names = ", ".join(values)
    marks = ", ".join(f":{k}" for k in values)
    conn.execute(text(f"INSERT INTO {table} ({names}) VALUES ({marks})"), values)


def test_post_moderation_migration_converts_data(tmp_path):
    """Матеріали «на розгляді» публікуються, старі відгуки лишаються
    приватними, роль менеджера отримує нову назву."""
    from sqlalchemy import text
    path = tmp_path / "data.db"
    _alembic(path, "upgrade", "c4f1a9e3d2b7", tmp_path=tmp_path)
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        _insert(conn, "roles", code="skill_manager", name="Skill-менеджер")
        _insert(conn, "catalog_resources", name="На розгляді", status="review",
                submitted_at="2026-09-01 10:00:00", published_at=None)
        _insert(conn, "catalog_resources", name="Чернетка", status="draft")
        _insert(conn, "skill_feedback", item_type="resource", skill_name="x",
                message="приватний відгук")
    _alembic(path, "upgrade", "head", tmp_path=tmp_path)
    with engine.connect() as conn:
        rows = dict(conn.execute(text("SELECT name, status FROM catalog_resources")).all())
        assert rows == {"На розгляді": "published", "Чернетка": "draft"}
        published_at = conn.execute(text(
            "SELECT published_at FROM catalog_resources WHERE name = 'На розгляді'")).scalar()
        assert str(published_at).startswith("2026-09-01")
        assert conn.execute(text("SELECT is_public FROM skill_feedback")).scalar() == 0
        assert conn.execute(text(
            "SELECT name FROM roles WHERE code = 'skill_manager'")).scalar() == "Менеджер каталогу"


def test_owner_backfill_takes_whoever_published(tmp_path):
    """Опубліковане без власника отримує його: хто опублікував за журналом,
    інакше хто створив, інакше автор. Чернетки й заповнених не чіпаємо."""
    from sqlalchemy import text
    path = tmp_path / "owners.db"
    _alembic(path, "upgrade", "e5a7c9d1f3b6", tmp_path=tmp_path)
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        _insert(conn, "users", id=7, username="olena", full_name="Олена Коваль")
        _insert(conn, "catalog_resources", id=1, name="За журналом", status="published", owner=None)
        _insert(conn, "review_logs", item_type="resource", item_id=1, item_name="За журналом",
                to_status="published", actor_name="Менеджер Петро")
        _insert(conn, "catalog_resources", id=2, name="За творцем", status="needs_update",
                owner="", created_by=7)
        _insert(conn, "catalog_resources", id=3, name="За автором", status="published",
                owner=None, author="Іван")
        _insert(conn, "catalog_resources", id=4, name="Чернетка", status="draft", owner=None,
                created_by=7)
        _insert(conn, "catalog_resources", id=5, name="Свій власник", status="published",
                owner="Марія", created_by=7)
        _insert(conn, "skills", id=1, name="Навичка", status="published", owner=None,
                created_by=7)
    _alembic(path, "upgrade", "head", tmp_path=tmp_path)
    with engine.connect() as conn:
        owners = dict(conn.execute(text("SELECT id, owner FROM catalog_resources")).all())
        assert owners == {1: "Менеджер Петро", 2: "Олена Коваль", 3: "Іван",
                          4: None, 5: "Марія"}
        assert conn.execute(text("SELECT owner FROM skills WHERE id = 1")).scalar() == "Олена Коваль"
