"""модератор і заступник розділу, ролі з груп Entra, погодження правок

- `section_managers.is_deputy` — у розділу модератор і заступник із
  однаковими правами (DEV-02). Наявні призначення: першого за id менеджера
  розділу вважаємо модератором, решту — заступниками.
- `review_logs.actor_role` — у якій ролі людина ухвалила рішення
  (модератор, заступник, адміністратор, автор).
- `catalog_resources` / `skills`: `mod_edit_by`, `mod_edit_at` — модератор
  змінив подане на розгляд і має погодити правки з автором (DEV-03).
- `user_roles.source`, `users.expert_source` — роль чи «Експерт» призначив
  адміністратор ('manual') або дала група корпоративного входу ('entra').

Revision ID: b1d3f5a7c9e2
Revises: a9c1e3f5b7d0
Create Date: 2026-11-02 12:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = 'b1d3f5a7c9e2'
down_revision = 'a9c1e3f5b7d0'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    with op.batch_alter_table("section_managers") as batch:
        batch.add_column(sa.Column("is_deputy", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))
    rows = conn.execute(sa.text(
        "SELECT section_id, user_id FROM section_managers ORDER BY section_id, user_id"
    )).fetchall()
    first = {}
    for section_id, user_id in rows:
        if section_id in first:
            conn.execute(sa.text(
                "UPDATE section_managers SET is_deputy = :on "
                "WHERE section_id = :s AND user_id = :u"),
                {"on": True, "s": section_id, "u": user_id})
        else:
            first[section_id] = user_id

    with op.batch_alter_table("review_logs") as batch:
        batch.add_column(sa.Column("actor_role", sa.String(), nullable=True))

    for table in ("catalog_resources", "skills"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("mod_edit_by", sa.Integer(), nullable=True))
            batch.add_column(sa.Column("mod_edit_at", sa.DateTime(), nullable=True))
            batch.create_foreign_key(f"fk_{table}_mod_edit_by", "users",
                                     ["mod_edit_by"], ["id"], ondelete="SET NULL")

    with op.batch_alter_table("user_roles") as batch:
        batch.add_column(sa.Column("source", sa.String(), nullable=False,
                                   server_default="manual"))
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("expert_source", sa.String(), nullable=True))
    conn.execute(sa.text("UPDATE users SET expert_source = 'manual' WHERE is_expert = :on"),
                 {"on": True})


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_column("expert_source")
    with op.batch_alter_table("user_roles") as batch:
        batch.drop_column("source")
    for table in ("skills", "catalog_resources"):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"fk_{table}_mod_edit_by", type_="foreignkey")
            batch.drop_column("mod_edit_at")
            batch.drop_column("mod_edit_by")
    with op.batch_alter_table("review_logs") as batch:
        batch.drop_column("actor_role")
    with op.batch_alter_table("section_managers") as batch:
        batch.drop_column("is_deputy")
