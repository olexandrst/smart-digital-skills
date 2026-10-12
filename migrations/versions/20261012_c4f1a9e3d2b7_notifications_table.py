"""таблиця notifications, якої бракувало в базовій міграції

Сповіщення (нагадування про перегляд, подання на розгляд і рішення за ним)
з'явилися після базової міграції, але окремої ревізії для них не було.
Бази, створені через create_all чи AUTO_MIGRATE, таблицю мають, а бази,
розгорнуті «з нуля» через `alembic upgrade head` (зокрема Azure App Service
через startup.sh), — ні, і там кожен запит до сповіщень падав.

Тому таблиця створюється лише тоді, коли її немає: наявні дані не чіпаємо.

Revision ID: c4f1a9e3d2b7
Revises: b7e2d4f6a8c1
Create Date: 2026-10-12 11:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = 'c4f1a9e3d2b7'
down_revision = 'b7e2d4f6a8c1'
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table("notifications"):
        return
    op.create_table('notifications',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('body', sa.Text(), nullable=True),
    sa.Column('resource_id', sa.Integer(), nullable=True),
    sa.Column('dedupe_key', sa.String(), nullable=False),
    sa.Column('is_read', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['resource_id'], ['catalog_resources.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'dedupe_key')
    )
    op.create_index('ix_notifications_user_id', 'notifications', ['user_id'])


def downgrade():
    # Не видаляємо: на базах, де таблиця існувала до цієї ревізії, downgrade
    # знищив би сповіщення, яких ця ревізія не створювала.
    pass
