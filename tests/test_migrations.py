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
