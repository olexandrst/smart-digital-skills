"""менеджери розділів і попередня модерація

Ролі (фідбек тестування, п. 11). Глобального «Менеджера каталогу» більше
немає: роль `skill_manager` стає `section_manager` — «Менеджер розділу», що
модерує лише призначені йому розділи (таблиця `section_managers`). Щоб у день
оновлення ніхто не втратив доступ, кожен колишній менеджер каталогу
отримує всі наявні розділи; зайві адміністратор прибере в «Користувачах».
Ознаки `users.is_expert` і `users.is_ideas_owner` ставить адміністратор.

Модерація стає попередньою (п. 12): повертається статус `review`
(«На розгляді»), час подання `submitted_at` і зміни до опублікованого, що
чекають схвалення (`pending_data`, `pending_by`, `pending_at`) — у матеріалів
і навичок. У навичок з'являється й `review_note` — причина повернення.
Опубліковане лишається опублікованим.

Revision ID: e7a9c1d3f5b8
Revises: d5f7b9c1e3a6
Create Date: 2026-10-27 10:00:00
"""
from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = 'e7a9c1d3f5b8'
down_revision = 'd5f7b9c1e3a6'
branch_labels = None
depends_on = None

MANAGER_NAME = "Менеджер розділу"
MANAGER_DESC = "Модерація матеріалів і навичок призначених розділів"


def _pending_columns(batch, prefix):
    batch.add_column(sa.Column("submitted_at", sa.DateTime(), nullable=True))
    batch.add_column(sa.Column("pending_data", sa.Text(), nullable=True))
    batch.add_column(sa.Column("pending_by", sa.Integer(), nullable=True))
    batch.add_column(sa.Column("pending_at", sa.DateTime(), nullable=True))
    batch.create_foreign_key(f"fk_{prefix}_pending_by", "users", ["pending_by"], ["id"])


def upgrade():
    conn = op.get_bind()

    # --- Роль менеджера розділу ---
    has_old = conn.execute(sa.text(
        "SELECT id FROM roles WHERE code = 'skill_manager'")).scalar()
    has_new = conn.execute(sa.text(
        "SELECT id FROM roles WHERE code = 'section_manager'")).scalar()
    if has_old and not has_new:
        conn.execute(sa.text(
            "UPDATE roles SET code = 'section_manager', name = :n, description = :d "
            "WHERE id = :id"), {"n": MANAGER_NAME, "d": MANAGER_DESC, "id": has_old})
    elif not has_new:
        conn.execute(sa.text(
            "INSERT INTO roles (code, name, description) "
            "VALUES ('section_manager', :n, :d)"), {"n": MANAGER_NAME, "d": MANAGER_DESC})

    op.create_table(
        "section_managers",
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("section_id", sa.Integer(),
                  sa.ForeignKey("catalog_sections.id", ondelete="CASCADE"),
                  primary_key=True),
    )
    role_id = conn.execute(sa.text(
        "SELECT id FROM roles WHERE code = 'section_manager'")).scalar()
    managers = [r[0] for r in conn.execute(sa.text(
        "SELECT user_id FROM user_roles WHERE role_id = :r"), {"r": role_id})]
    sections = [r[0] for r in conn.execute(sa.text("SELECT id FROM catalog_sections"))]
    for user_id in managers:
        for section_id in sections:
            conn.execute(sa.text(
                "INSERT INTO section_managers (user_id, section_id) VALUES (:u, :s)"),
                {"u": user_id, "s": section_id})

    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("is_expert", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))
        batch.add_column(sa.Column("is_ideas_owner", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))

    # --- Попередня модерація ---
    with op.batch_alter_table("catalog_resources") as batch:
        _pending_columns(batch, "catalog_resources")
    with op.batch_alter_table("skills") as batch:
        batch.add_column(sa.Column("review_note", sa.Text(), nullable=True))
        _pending_columns(batch, "skills")

    have = conn.execute(sa.text(
        "SELECT id FROM catalog_terms WHERE kind = 'status' AND code = 'review'")).scalar()
    if not have:
        conn.execute(sa.text(
            "INSERT INTO catalog_terms (kind, code, name, name_norm, position, "
            "icon_emoji, color, is_active, requires_url, requires_body, has_scope, "
            "created_at) VALUES ('status', 'review', 'На розгляді', 'на розгляді', 1, "
            "'🕓', 'amber', :on, :off, :off, :off, :now)"),
            {"on": True, "off": False, "now": datetime.utcnow()})


def downgrade():
    conn = op.get_bind()
    # Матеріали на розгляді повертаються авторам чернетками: старої версії
    # статусу review не знає.
    conn.execute(sa.text("UPDATE catalog_resources SET status = 'draft' "
                         "WHERE status = 'review'"))
    conn.execute(sa.text("UPDATE skills SET status = 'draft' WHERE status = 'review'"))
    conn.execute(sa.text("DELETE FROM catalog_terms WHERE kind = 'status' AND code = 'review'"))
    with op.batch_alter_table("skills") as batch:
        batch.drop_constraint("fk_skills_pending_by", type_="foreignkey")
        for col in ("pending_at", "pending_by", "pending_data", "submitted_at",
                    "review_note"):
            batch.drop_column(col)
    with op.batch_alter_table("catalog_resources") as batch:
        batch.drop_constraint("fk_catalog_resources_pending_by", type_="foreignkey")
        for col in ("pending_at", "pending_by", "pending_data", "submitted_at"):
            batch.drop_column(col)
    with op.batch_alter_table("users") as batch:
        batch.drop_column("is_ideas_owner")
        batch.drop_column("is_expert")
    op.drop_table("section_managers")
    conn.execute(sa.text(
        "UPDATE roles SET code = 'skill_manager', name = 'Менеджер каталогу', "
        "description = 'Модерація й керування всім контентом каталогу' "
        "WHERE code = 'section_manager'"))
