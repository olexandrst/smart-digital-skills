"""знайомство з інтерфейсом: коли користувач його пройшов

Нова колонка `users.tour_completed_at`. Порожня — під час наступного входу
хаб покаже інтерактивне знайомство з інтерфейсом. Уже наявним користувачам
теж: інтерфейс після прибирання чату й нової навігації для них новий, а
пропустити знайомство можна одним кліком.

Revision ID: a8d0c2e4f6b9
Revises: f6b8d0e2a4c7
Create Date: 2026-10-14 10:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = 'a8d0c2e4f6b9'
down_revision = 'f6b8d0e2a4c7'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column('tour_completed_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('users') as batch:
        batch.drop_column('tour_completed_at')
