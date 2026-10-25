"""колір картки — лише за видом матеріалу

Фідбек тестування, п. 2.2: ручний вибір кольору плитки прибрано, щоб
однаковий вид завжди виглядав однаково. Кольори, задані раніше вручну
(наприклад, помаранчевий FAQ серед фіолетових інструкцій), скидаються.
Колонка `catalog_resources.accent` лишається для сумісності й надалі порожня.

Схема не змінюється, лише дані; відкат нічого не повертає (ручний колір
більше ніде не показується).

Revision ID: b3d5f7a9c1e4
Revises: a8d0c2e4f6b9
Create Date: 2026-10-25 09:00:00
"""
from alembic import op


revision = 'b3d5f7a9c1e4'
down_revision = 'a8d0c2e4f6b9'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE catalog_resources SET accent = NULL WHERE accent IS NOT NULL")


def downgrade():
    pass
