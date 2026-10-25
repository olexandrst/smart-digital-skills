"""AI Coffee прибрано з бази знань

Фідбек тестування, п. 7: зустрічі AI Coffee — тимчасовий формат лише для
AI-партнерів, тож база знань його не згадує. На вже наповненій базі:

- матеріал «AI Coffee: …» іде в архів (його видно лише менеджерам, повернути
  можна в «Керуванні»), перехід записується в журнал життєвого циклу;
- крок навчального маршруту з цим матеріалом видаляється разом із позначками
  про його проходження — інакше маршрут вів би на картку, якої не видно;
- колекцію «AI Coffee» приховано; якщо в ній лишилися інші матеріали, вони
  переходять на рівень розділу;
- з опису розділу прибрано згадку «AI Coffee, ».

Нова база взагалі не отримує цих даних (scripts/seed.py). Міграція
ідемпотентна: повторний запуск нічого не змінює. Відкат не повертає
видалені кроки маршруту — матеріал і колекцію можна повернути вручну.

Revision ID: c4e6a8b0d2f5
Revises: b3d5f7a9c1e4
Create Date: 2026-10-25 09:10:00
"""
from datetime import datetime

import sqlalchemy as sa
from alembic import op


revision = 'c4e6a8b0d2f5'
down_revision = 'b3d5f7a9c1e4'
branch_labels = None
depends_on = None

NOTE = "Фідбек тестування, п. 7: AI Coffee прибрано з бази знань"


def _ids(conn, sql, **params):
    return [row[0] for row in conn.execute(sa.text(sql), params)]


def _in(ids):
    return ", ".join(str(int(i)) for i in ids)


def upgrade():
    conn = op.get_bind()
    now = datetime.utcnow()

    resources = conn.execute(sa.text(
        "SELECT id, name, status FROM catalog_resources "
        "WHERE name LIKE 'AI Coffee%'")).fetchall()
    for rid, name, status in resources:
        if status == "archived":
            continue
        conn.execute(sa.text(
            "UPDATE catalog_resources SET status = 'archived', is_featured = :off, "
            "updated_at = :now WHERE id = :id"), {"off": False, "now": now, "id": rid})
        conn.execute(sa.text(
            "INSERT INTO review_logs (item_type, item_id, item_name, actor_name, "
            "from_status, to_status, note, created_at) VALUES "
            "('resource', :id, :name, 'Система', :old, 'archived', :note, :now)"),
            {"id": rid, "name": name, "old": status, "note": NOTE, "now": now})

    if resources:
        steps = _ids(conn, "SELECT id FROM learning_path_steps "
                           f"WHERE resource_id IN ({_in(r[0] for r in resources)})")
        if steps:
            conn.execute(sa.text(f"DELETE FROM learning_progress WHERE step_id IN ({_in(steps)})"))
            conn.execute(sa.text(f"DELETE FROM learning_path_steps WHERE id IN ({_in(steps)})"))

    folders = _ids(conn, "SELECT id FROM catalog_folders WHERE name = 'AI Coffee'")
    if folders:
        conn.execute(sa.text(
            f"UPDATE catalog_folders SET is_active = :off WHERE id IN ({_in(folders)})"),
            {"off": False})
        conn.execute(sa.text(
            f"UPDATE catalog_resources SET folder_id = NULL WHERE folder_id IN ({_in(folders)}) "
            "AND status <> 'archived'"))

    conn.execute(sa.text(
        "UPDATE catalog_sections SET description = REPLACE(description, 'AI Coffee, ', '') "
        "WHERE description LIKE '%AI Coffee, %'"))


def downgrade():
    pass
