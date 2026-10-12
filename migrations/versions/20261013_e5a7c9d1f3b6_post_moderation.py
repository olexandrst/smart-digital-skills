"""пост-модерація: користувачі публікують самі, коментарі публічні

- Статусу «На розгляді» більше немає: користувач публікує сам, а менеджер
  каталогу модерує вже опубліковане. Матеріали, що чекали розгляду,
  публікуються — їхні автори хотіли саме цього, а менеджери вже отримали
  сповіщення про них і можуть зняти будь-який з публікації. Колонка
  `submitted_at` більше не потрібна; `review_note` лишається — тепер це
  причина, з якою менеджер зняв матеріал з публікації.
- Сповіщення можуть посилатися на навичку (`notifications.skill_id`).
- Коментарі під картками стають публічними (`skill_feedback.is_public`).
  Відгуки, залишені раніше, писалися лише для команди, тому позначаються
  приватними й у стрічку не потрапляють.
- Роль `skill_manager` тепер називається «Менеджер каталогу».

Revision ID: e5a7c9d1f3b6
Revises: c4f1a9e3d2b7
Create Date: 2026-10-13 09:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = 'e5a7c9d1f3b6'
down_revision = 'c4f1a9e3d2b7'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE catalog_resources "
               "SET published_at = COALESCE(published_at, submitted_at, CURRENT_TIMESTAMP) "
               "WHERE status = 'review'")
    op.execute("UPDATE catalog_resources SET status = 'published' WHERE status = 'review'")
    with op.batch_alter_table("catalog_resources") as batch:
        batch.drop_column("submitted_at")
    op.execute("DELETE FROM catalog_terms WHERE kind = 'status' AND code = 'review'")

    with op.batch_alter_table("notifications") as batch:
        batch.add_column(sa.Column("skill_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_notifications_skill_id", "skills",
                                 ["skill_id"], ["id"], ondelete="CASCADE")

    with op.batch_alter_table("skill_feedback") as batch:
        batch.add_column(sa.Column("is_public", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))

    op.execute("UPDATE roles SET name = 'Менеджер каталогу', "
               "description = 'Модерація й керування всім контентом каталогу' "
               "WHERE code = 'skill_manager'")
    op.execute("UPDATE roles SET name = 'Адміністратор', "
               "description = 'Повний контроль над системою й користувачами' "
               "WHERE code = 'admin'")


def downgrade():
    op.execute("UPDATE roles SET name = 'Skill-менеджер', "
               "description = 'Керування каталогом і навичками' WHERE code = 'skill_manager'")
    op.execute("UPDATE roles SET name = 'Адміністратор системи', "
               "description = 'Повний контроль над системою' WHERE code = 'admin'")
    with op.batch_alter_table("skill_feedback") as batch:
        batch.drop_column("is_public")
    with op.batch_alter_table("notifications") as batch:
        batch.drop_constraint("fk_notifications_skill_id", type_="foreignkey")
        batch.drop_column("skill_id")
    with op.batch_alter_table("catalog_resources") as batch:
        batch.add_column(sa.Column("submitted_at", sa.DateTime(), nullable=True))
    # Статус «На розгляді» повертати нема куди: опубліковане лишається
    # опублікованим, а запис довідника стара версія відновить сама при старті.
