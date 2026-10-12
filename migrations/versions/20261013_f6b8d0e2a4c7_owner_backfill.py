"""власник опублікованого — той, хто опублікував

Правило «власником опублікованого матеріалу стає той, хто його опублікував»
тепер діє для всіх ролей. Уже опубліковані матеріали й навички без власника
отримують його за тим самим правилом: хто останнім опублікував (журнал
`review_logs`), а якщо запису немає — хто створив, і зрештою — автор.

Схема не змінюється, лише дані; відкат нічого не повертає (власника,
заповненого за правилом, від уведеного вручну вже не відрізнити).

Revision ID: f6b8d0e2a4c7
Revises: e5a7c9d1f3b6
Create Date: 2026-10-13 12:00:00
"""
from alembic import op


revision = 'f6b8d0e2a4c7'
down_revision = 'e5a7c9d1f3b6'
branch_labels = None
depends_on = None

_EMPTY = "(owner IS NULL OR TRIM(owner) = '')"


def _backfill(table, item_type, public_statuses):
    where = f"status IN ({public_statuses}) AND {_EMPTY}"
    op.execute(f"""
        UPDATE {table} SET owner = (
            SELECT rl.actor_name FROM review_logs rl
            WHERE rl.item_type = '{item_type}' AND rl.item_id = {table}.id
              AND rl.to_status = 'published' AND rl.actor_name IS NOT NULL
              AND TRIM(rl.actor_name) <> ''
            ORDER BY rl.created_at DESC, rl.id DESC LIMIT 1)
        WHERE {where}""")
    op.execute(f"""
        UPDATE {table} SET owner = (
            SELECT COALESCE(NULLIF(TRIM(u.full_name), ''), u.username)
            FROM users u WHERE u.id = {table}.created_by)
        WHERE {where}""")
    op.execute(f"""
        UPDATE {table} SET owner = author
        WHERE {where} AND author IS NOT NULL AND TRIM(author) <> ''""")


def upgrade():
    _backfill("catalog_resources", "resource", "'published', 'needs_update'")
    _backfill("skills", "skill", "'published'")


def downgrade():
    pass
