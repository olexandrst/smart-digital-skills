"""розгляд поданих матеріалів: submitted_at, review_note

Статус «review» нової колонки не потребує — він лягає в наявну `status`.
Нові поля зберігають момент подання та коментар менеджера при поверненні:
автор має бачити причину в картці, а не шукати її в журналі.

Revision ID: a1c3e5f7b9d2
Revises: d7f976c479ef
Create Date: 2026-10-07 10:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = 'a1c3e5f7b9d2'
down_revision = 'd7f976c479ef'
branch_labels = None
depends_on = None


def upgrade():
    # Обидві колонки nullable — SQLite додає такі без перебудови таблиці.
    op.add_column('catalog_resources', sa.Column('submitted_at', sa.DateTime(), nullable=True))
    op.add_column('catalog_resources', sa.Column('review_note', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('catalog_resources') as batch_op:
        batch_op.drop_column('review_note')
        batch_op.drop_column('submitted_at')
