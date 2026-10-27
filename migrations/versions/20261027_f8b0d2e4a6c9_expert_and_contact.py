"""позначка «Експерт» на матеріалах і зворотний зв'язок

Позначка «Експерт» біля автора (фідбек тестування, п. 11) — ознака автора
на момент першої публікації: `author_is_expert` у матеріалів і навичок.
Для вже опублікованого її беремо з теперішньої ознаки автора: галочки
з'явилися щойно, тож іншого джерела немає.

«Зворотний зв'язок» (п. 21): повідомлення адміністратору хабу зберігаються в
`contact_messages` зі статусом «нове» / «опрацьовано».

Revision ID: f8b0d2e4a6c9
Revises: e7a9c1d3f5b8
Create Date: 2026-10-27 16:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = 'f8b0d2e4a6c9'
down_revision = 'e7a9c1d3f5b8'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    for table in ("catalog_resources", "skills"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("author_is_expert", sa.Boolean(), nullable=False,
                                       server_default=sa.false()))
        conn.execute(sa.text(
            f"UPDATE {table} SET author_is_expert = :on "
            f"WHERE published_at IS NOT NULL AND created_by IN "
            f"(SELECT id FROM users WHERE is_expert = :on)"), {"on": True})

    op.create_table(
        "contact_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("author_name", sa.String(), nullable=True),
        sa.Column("author_email", sa.String(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("page", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="new"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("handled_at", sa.DateTime(), nullable=True),
        sa.Column("handled_by", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_contact_messages_created_at", "contact_messages", ["created_at"])


def downgrade():
    op.drop_index("ix_contact_messages_created_at", table_name="contact_messages")
    op.drop_table("contact_messages")
    conn = op.get_bind()
    conn.execute(sa.text("DELETE FROM notifications WHERE kind = 'contact_message'"))
    for table in ("skills", "catalog_resources"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("author_is_expert")
