"""лише каталог: прибрати чат, моделі, групи, квоти, доступи та запуск навичок

Застосунок перестає бути AI-чатом і лишається каталогом знань, навичок і
промптів. Тому видаляються:
  * чат і облік токенів: chat_sessions, chat_messages, token_usage_logs,
    token_limits (квоти), token_counters, audit_logs (не використовувався);
  * моделі та доступи до них: models, group_models;
  * групи й призначення навичок: groups, group_memberships, invitations,
    group_skills, user_skills;
  * запуск навичок: skill_inputs і колонки skills.model_id, parameters,
    runtime, entrypoint, activations_count, а також user_files.skill_id.

Замість лічильника активацій навичка отримує downloads_count — скільки разів
її пакет завантажили з каталогу.

⚠️ Міграція руйнівна: історія чатів, облік токенів, групи й квоти видаляються
безповоротно. Перед розгортанням зробіть резервну копію бази
(`python -m scripts.backup_db`). `downgrade` відтворює схему, але не дані.

Рядки user_files без resource_id (особисті файли й результати запуску
навичок) не чіпаємо: вони вже нікуди не ведуть з інтерфейсу, а фізичні файли
лишаються в теці USER_FILES_DIR — прибрати їх можна вручну.

Revision ID: b7e2d4f6a8c1
Revises: a1c3e5f7b9d2
Create Date: 2026-10-12 10:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = 'b7e2d4f6a8c1'
down_revision = 'a1c3e5f7b9d2'
branch_labels = None
depends_on = None


# Порядок важливий для баз із увімкненою перевіркою зовнішніх ключів:
# спершу ті, хто посилається, потім ті, на кого посилаються.
_DROPPED_TABLES = (
    "chat_messages", "token_usage_logs", "chat_sessions",
    "user_skills", "group_skills", "group_models",
    "invitations", "group_memberships",
    "token_counters", "token_limits", "audit_logs",
    "skill_inputs", "groups",
)
_DROPPED_SKILL_COLUMNS = ("model_id", "parameters", "runtime", "entrypoint",
                          "activations_count")


def upgrade():
    for table in _DROPPED_TABLES:
        op.drop_table(table)

    # SQLite не вміє DROP COLUMN зі зовнішнім ключем — batch-режим перебудовує
    # таблицю. Зв'язок skills.model_id → models знімається разом із колонкою,
    # тому таблицю models видаляємо вже після цього кроку.
    with op.batch_alter_table("skills") as batch:
        for column in _DROPPED_SKILL_COLUMNS:
            batch.drop_column(column)
        batch.add_column(sa.Column("downloads_count", sa.Integer(), nullable=False,
                                   server_default="0"))
    op.drop_table("models")

    with op.batch_alter_table("user_files") as batch:
        batch.drop_column("skill_id")

    # Перемикач «індивідуального доступу до навичок» більше нічого не вмикає.
    op.execute("DELETE FROM app_settings WHERE key = 'skills_individual_access'")


def downgrade():
    # Відтворює схему попередньої версії (порожні таблиці, без даних).
    op.create_table('models',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('model_type', sa.String(), nullable=False),
    sa.Column('provider', sa.String(), nullable=False),
    sa.Column('deployment_name', sa.String(), nullable=False),
    sa.Column('base_url', sa.String(), nullable=True),
    sa.Column('api_version', sa.String(), nullable=True),
    sa.Column('context_window', sa.Integer(), nullable=True),
    sa.Column('price_in', sa.Float(), nullable=True),
    sa.Column('price_out', sa.Float(), nullable=True),
    sa.Column('config', sa.Text(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('is_system', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )

    with op.batch_alter_table("user_files") as batch:
        batch.add_column(sa.Column('skill_id', sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_user_files_skill_id", "skills",
                                 ["skill_id"], ["id"])

    with op.batch_alter_table("skills") as batch:
        batch.drop_column("downloads_count")
        batch.add_column(sa.Column('model_id', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('parameters', sa.Text(), nullable=True))
        batch.add_column(sa.Column('runtime', sa.String(), nullable=True))
        batch.add_column(sa.Column('entrypoint', sa.String(), nullable=True))
        batch.add_column(sa.Column('activations_count', sa.Integer(), nullable=False,
                                   server_default="0"))
        batch.create_foreign_key("fk_skills_model_id", "models",
                                 ["model_id"], ["id"])

    op.create_table('token_limits',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('scope_type', sa.String(), nullable=False),
    sa.Column('scope_id', sa.Integer(), nullable=True),
    sa.Column('period', sa.String(), nullable=False),
    sa.Column('limit_tokens', sa.Integer(), nullable=False),
    sa.Column('limit_cost', sa.Float(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('audit_logs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('actor_user_id', sa.Integer(), nullable=True),
    sa.Column('action', sa.String(), nullable=False),
    sa.Column('entity_type', sa.String(), nullable=True),
    sa.Column('entity_id', sa.Integer(), nullable=True),
    sa.Column('details', sa.Text(), nullable=True),
    sa.Column('ip_address', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('groups',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('token_counters',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('used_tokens', sa.Integer(), nullable=False),
    sa.Column('used_cost', sa.Float(), nullable=False),
    sa.Column('period_start', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id')
    )
    op.create_table('chat_sessions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('group_id', sa.Integer(), nullable=True),
    sa.Column('model_id', sa.Integer(), nullable=True),
    sa.Column('skill_id', sa.Integer(), nullable=True),
    sa.Column('title', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ),
    sa.ForeignKeyConstraint(['model_id'], ['models.id'], ),
    sa.ForeignKeyConstraint(['skill_id'], ['skills.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('group_memberships',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('group_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('invited_by', sa.Integer(), nullable=True),
    sa.Column('joined_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['invited_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('group_id', 'user_id')
    )
    op.create_table('group_models',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('group_id', sa.Integer(), nullable=False),
    sa.Column('model_id', sa.Integer(), nullable=False),
    sa.Column('assigned_by', sa.Integer(), nullable=True),
    sa.Column('assigned_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['assigned_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['model_id'], ['models.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('group_id', 'model_id')
    )
    op.create_table('group_skills',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('group_id', sa.Integer(), nullable=False),
    sa.Column('skill_id', sa.Integer(), nullable=False),
    sa.Column('assigned_by', sa.Integer(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('assigned_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['assigned_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['skill_id'], ['skills.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('group_id', 'skill_id')
    )
    op.create_table('invitations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('group_id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(), nullable=False),
    sa.Column('role', sa.String(), nullable=False),
    sa.Column('token', sa.String(), nullable=False),
    sa.Column('invited_by', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('accepted_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['invited_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token')
    )
    op.create_table('skill_inputs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('skill_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('label', sa.String(), nullable=True),
    sa.Column('data_type', sa.String(), nullable=False),
    sa.Column('is_required', sa.Boolean(), nullable=False),
    sa.Column('default_value', sa.Text(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['skill_id'], ['skills.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('skill_id', 'name')
    )
    op.create_table('user_skills',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('skill_id', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(), nullable=False),
    sa.Column('group_id', sa.Integer(), nullable=True),
    sa.Column('assigned_by', sa.Integer(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('assigned_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['assigned_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ),
    sa.ForeignKeyConstraint(['skill_id'], ['skills.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'skill_id')
    )
    op.create_table('chat_messages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('session_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.String(), nullable=False),
    sa.Column('content', sa.Text(), nullable=True),
    sa.Column('skill_id', sa.Integer(), nullable=True),
    sa.Column('metadata', sa.Text(), nullable=True),
    sa.Column('prompt_tokens', sa.Integer(), nullable=True),
    sa.Column('completion_tokens', sa.Integer(), nullable=True),
    sa.Column('total_tokens', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['chat_sessions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['skill_id'], ['skills.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('token_usage_logs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('group_id', sa.Integer(), nullable=True),
    sa.Column('skill_id', sa.Integer(), nullable=True),
    sa.Column('model_id', sa.Integer(), nullable=True),
    sa.Column('session_id', sa.Integer(), nullable=True),
    sa.Column('prompt_tokens', sa.Integer(), nullable=False),
    sa.Column('completion_tokens', sa.Integer(), nullable=False),
    sa.Column('total_tokens', sa.Integer(), nullable=False),
    sa.Column('cost_in', sa.Float(), nullable=False),
    sa.Column('cost_out', sa.Float(), nullable=False),
    sa.Column('cost_total', sa.Float(), nullable=False),
    sa.Column('feature', sa.String(), nullable=False),
    sa.Column('mode', sa.String(), nullable=False),
    sa.Column('is_system', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ),
    sa.ForeignKeyConstraint(['model_id'], ['models.id'], ),
    sa.ForeignKeyConstraint(['session_id'], ['chat_sessions.id'], ),
    sa.ForeignKeyConstraint(['skill_id'], ['skills.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
