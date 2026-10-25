"""дата, коли користувач визначив свій рівень AI-зрілості

Нова колонка `users.maturity_assessed_at`: сторінка «Мій розвиток» показує
рівень разом із датою, коли його визначено (фідбек тестування, п. 4 і 5).
Для вже вказаних рівнів дата невідома й лишається порожньою — сторінка тоді
показує сам рівень.

Revision ID: d5f7b9c1e3a6
Revises: c4e6a8b0d2f5
Create Date: 2026-10-25 09:20:00
"""
from alembic import op
import sqlalchemy as sa


revision = 'd5f7b9c1e3a6'
down_revision = 'c4e6a8b0d2f5'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column('maturity_assessed_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('users') as batch:
        batch.drop_column('maturity_assessed_at')
