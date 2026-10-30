"""приховати зовнішні розділи «Про проєкт», «AI Partners Network», «Help & Feedback»

Колеги вирішили поки не показувати три розділи-посилання на зовнішні
портали (30.10.2026). Розділи не видаляються, лише стають прихованими
(`is_active = false`): адміністратор бачить їх у «Керування → Структура
каталогу» й може повернути одним перемикачем. Матеріали, що в них лежать,
лишаються в каталозі. Назву першого розділу звіряємо за початком: на різних
базах вона буває повною («…AI Culture Acceleration») і скороченою.

Revision ID: a9c1e3f5b7d0
Revises: f8b0d2e4a6c9
Create Date: 2026-10-30 10:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = 'a9c1e3f5b7d0'
down_revision = 'f8b0d2e4a6c9'
branch_labels = None
depends_on = None

_WHERE = ("name LIKE 'Про проєкт «AI Culture%' "
          "OR name IN ('AI Partners Network', 'Help & Feedback')")


def _set(active):
    op.get_bind().execute(sa.text(
        f"UPDATE catalog_sections SET is_active = :v WHERE {_WHERE}"), {"v": active})


def upgrade():
    _set(False)


def downgrade():
    _set(True)
