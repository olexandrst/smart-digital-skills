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
    _alembic(path, "upgrade", "e5a7c9d1f3b6", tmp_path=tmp_path)
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


def test_manual_card_colours_reset(tmp_path):
    """Фідбек, п. 2.2: колір картки — лише за видом, ручні кольори скидаються."""
    from sqlalchemy import text
    path = tmp_path / "accents.db"
    _alembic(path, "upgrade", "a8d0c2e4f6b9", tmp_path=tmp_path)
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        _insert(conn, "catalog_resources", name="Помаранчевий FAQ", status="published",
                accent="amber")
        _insert(conn, "catalog_resources", name="Типовий", status="published", accent=None)
    _alembic(path, "upgrade", "head", tmp_path=tmp_path)
    with engine.connect() as conn:
        assert conn.execute(text(
            "SELECT COUNT(*) FROM catalog_resources WHERE accent IS NOT NULL")).scalar() == 0


def test_ai_coffee_retired(tmp_path):
    """Фідбек, п. 7: матеріал AI Coffee — в архів із записом у журналі, крок
    маршруту й позначки його проходження — геть, колекцію приховано, згадку
    прибрано з опису розділу. Решту не чіпаємо; повторний запуск нічого не дублює."""
    from sqlalchemy import text
    path = tmp_path / "coffee.db"
    _alembic(path, "upgrade", "b3d5f7a9c1e4", tmp_path=tmp_path)
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        _insert(conn, "users", id=1, username="u1")
        _insert(conn, "catalog_sections", id=9, name="AI Community", is_active=1,
                description="AI Coffee, AI Hub Weekly та зустрічі спільноти.")
        _insert(conn, "catalog_folders", id=7, section_id=9, name="AI Coffee", is_active=1)
        _insert(conn, "catalog_folders", id=8, section_id=9, name="AI Hub Weekly", is_active=1)
        _insert(conn, "catalog_resources", id=1, name="AI Coffee: як приєднатися та що це дає",
                status="published", is_featured=1, section_id=9, folder_id=7)
        _insert(conn, "catalog_resources", id=2, name="Підсумки зустрічі", status="published",
                section_id=9, folder_id=7)
        _insert(conn, "catalog_resources", id=3, name="Як писати промпти", status="published")
        _insert(conn, "learning_paths", id=1, name="Старт з AI", is_active=1)
        _insert(conn, "learning_path_steps", id=10, path_id=1, position=0, resource_id=3)
        _insert(conn, "learning_path_steps", id=11, path_id=1, position=1, resource_id=1)
        _insert(conn, "learning_progress", user_id=1, path_id=1, step_id=10)
        _insert(conn, "learning_progress", user_id=1, path_id=1, step_id=11)
    _alembic(path, "upgrade", "head", tmp_path=tmp_path)
    with engine.connect() as conn:
        rows = {r[0]: r[1:] for r in conn.execute(text(
            "SELECT id, status, folder_id, is_featured FROM catalog_resources"))}
        assert rows[1] == ("archived", 7, 0)
        assert rows[2] == ("published", None, 0)       # з прихованої колекції — у розділ
        assert rows[3][0] == "published"
        assert conn.execute(text(
            "SELECT id FROM learning_path_steps ORDER BY id")).scalars().all() == [10]
        assert conn.execute(text(
            "SELECT step_id FROM learning_progress")).scalars().all() == [10]
        assert dict(conn.execute(text("SELECT id, is_active FROM catalog_folders")).all()) \
            == {7: 0, 8: 1}
        assert conn.execute(text("SELECT description FROM catalog_sections")).scalar() \
            == "AI Hub Weekly та зустрічі спільноти."
        log = conn.execute(text(
            "SELECT from_status, to_status, note FROM review_logs WHERE item_id = 1")).all()
        assert len(log) == 1 and log[0][:2] == ("published", "archived")
    _alembic(path, "downgrade", "b3d5f7a9c1e4", tmp_path=tmp_path)
    _alembic(path, "upgrade", "head", tmp_path=tmp_path)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM review_logs")).scalar() == 1


def test_section_moderation_migration_keeps_managers_access(tmp_path):
    """Колишній менеджер каталогу стає менеджером усіх наявних розділів;
    з'являються статус «На розгляді» й поля пропозиції змін. Опубліковане
    лишається опублікованим."""
    from sqlalchemy import text
    path = tmp_path / "sections.db"
    _alembic(path, "upgrade", "d5f7b9c1e3a6", tmp_path=tmp_path)
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        _insert(conn, "roles", id=1, code="admin", name="Адміністратор")
        _insert(conn, "roles", id=2, code="skill_manager", name="Менеджер каталогу")
        _insert(conn, "users", id=5, username="mgr")
        _insert(conn, "users", id=6, username="plain")
        _insert(conn, "user_roles", user_id=5, role_id=2)
        _insert(conn, "catalog_sections", id=10, name="Toolbox")
        _insert(conn, "catalog_sections", id=11, name="Insight Center")
        _insert(conn, "catalog_resources", name="Опублікований", status="published")
    _alembic(path, "upgrade", "e7a9c1d3f5b8", tmp_path=tmp_path)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT code, name FROM roles WHERE id = 2")).one() == \
            ("section_manager", "Менеджер розділу")
        pairs = set(conn.execute(text("SELECT user_id, section_id FROM section_managers")))
        assert pairs == {(5, 10), (5, 11)}
        assert conn.execute(text("SELECT is_expert, is_ideas_owner FROM users "
                                 "WHERE id = 6")).one() == (0, 0)
        assert conn.execute(text("SELECT status FROM catalog_resources")).scalar() == "published"
        assert conn.execute(text("SELECT name FROM catalog_terms WHERE kind = 'status' "
                                 "AND code = 'review'")).scalar() == "На розгляді"
        conn.execute(text("SELECT pending_data, pending_by, pending_at, submitted_at "
                          "FROM skills"))
    # Відкат повертає стару роль, а поданий матеріал — авторові чернеткою.
    with engine.begin() as conn:
        conn.execute(text("UPDATE catalog_resources SET status = 'review'"))
    _alembic(path, "downgrade", "d5f7b9c1e3a6", tmp_path=tmp_path)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT code FROM roles WHERE id = 2")).scalar() == \
            "skill_manager"
        assert conn.execute(text("SELECT status FROM catalog_resources")).scalar() == "draft"


def test_expert_and_contact_migration(tmp_path):
    """Позначку «Експерт» отримує опубліковане авторів, що вже мають ознаку;
    чернетки й матеріали інших авторів — ні. З'являється таблиця повідомлень
    адміністратору, відкат її прибирає."""
    from sqlalchemy import text
    path = tmp_path / "expert.db"
    _alembic(path, "upgrade", "e7a9c1d3f5b8", tmp_path=tmp_path)
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        _insert(conn, "users", id=1, username="expert", is_expert=1)
        _insert(conn, "users", id=2, username="plain", is_expert=0)
        _insert(conn, "catalog_resources", id=1, name="Від експерта", status="published",
                published_at="2026-10-01 10:00:00", created_by=1)
        _insert(conn, "catalog_resources", id=2, name="Чернетка експерта", status="draft",
                published_at=None, created_by=1)
        _insert(conn, "catalog_resources", id=3, name="Від користувача", status="published",
                published_at="2026-10-01 10:00:00", created_by=2)
        _insert(conn, "skills", id=1, name="Навичка експерта", status="published",
                published_at="2026-10-01 10:00:00", created_by=1)
    _alembic(path, "upgrade", "f8b0d2e4a6c9", tmp_path=tmp_path)
    with engine.connect() as conn:
        flags = dict(conn.execute(text(
            "SELECT id, author_is_expert FROM catalog_resources")).all())
        assert flags == {1: 1, 2: 0, 3: 0}
        assert conn.execute(text("SELECT author_is_expert FROM skills")).scalar() == 1
        assert "contact_messages" in inspect(conn).get_table_names()
    _alembic(path, "downgrade", "e7a9c1d3f5b8", tmp_path=tmp_path)
    tables = set(inspect(create_engine(f"sqlite:///{path}")).get_table_names())
    assert "contact_messages" not in tables
