"""сценарії головної, порядок розділів і теги для видів без власного типу

Фідбек колег від 24.11.2026:

- `catalog_sections.scenario` — плитка-сценарій головної, що збирає
  матеріали розділу: «Навчатися та розвиватися» (`learn`) — AI Use Policy &
  Safety, Learning Space і FAQ; «Знайти ідеї та практичний досвід»
  (`ideas`) — Insight Center і Power Links. Прив'язку далі змінює
  адміністратор у «Структурі каталогу».
- Порядок розділів у бічній панелі — як у фідбеку: AI Use Policy & Safety,
  Learning Space, Toolbox, Insight Center, Power Links, FAQ. Решта розділів
  іде після них у попередньому порядку.
- «Курси», «добірки», «практичні шаблони» й «рекомендації щодо використання
  AI» — теги, а не нові види матеріалів: додаємо їх у довідник тегів, щоб
  автори обирали готові.

Revision ID: c3e5a7b9d1f3
Revises: b1d3f5a7c9e2
Create Date: 2026-11-24 12:00:00
"""
from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = 'c3e5a7b9d1f3'
down_revision = 'b1d3f5a7c9e2'
branch_labels = None
depends_on = None

SCENARIOS = {
    "learn": ("AI Use Policy & Safety", "Learning Space", "FAQ"),
    "ideas": ("Insight Center", "Power Links"),
}
ORDER = ("AI Use Policy & Safety", "Learning Space", "Toolbox",
         "Insight Center", "Power Links", "FAQ")
# Решта розділів — після шести названих, зі збереженням їхнього порядку.
OTHERS_SHIFT = 100
TAGS = ("курси", "добірки", "практичні шаблони", "рекомендації щодо використання AI")
# Опис доданих тегів: пояснює адміністраторові, звідки вони, і дає відкату
# змогу не зачепити однойменний тег, що існував раніше.
TAG_NOTE = "Вид матеріалу без окремого типу (фідбек колег, 24.11.2026)"


def _norm(name):
    # Те саме, що CatalogTerm.normalize: пробіли стиснуто, регістр не важить.
    return " ".join(name.split()).casefold()


def _in(names):
    return ", ".join(f":n{i}" for i in range(len(names))), \
        {f"n{i}": n for i, n in enumerate(names)}


def upgrade():
    with op.batch_alter_table("catalog_sections") as batch:
        batch.add_column(sa.Column("scenario", sa.String(), nullable=True))

    conn = op.get_bind()
    for code, names in SCENARIOS.items():
        marks, params = _in(names)
        conn.execute(sa.text(
            f"UPDATE catalog_sections SET scenario = :code WHERE name IN ({marks})"),
            {"code": code, **params})

    marks, params = _in(ORDER)
    conn.execute(sa.text(
        f"UPDATE catalog_sections SET position = position + {OTHERS_SHIFT} "
        f"WHERE name NOT IN ({marks})"), params)
    for position, name in enumerate(ORDER, start=1):
        conn.execute(sa.text(
            "UPDATE catalog_sections SET position = :p WHERE name = :n"),
            {"p": position, "n": name})

    have = {row[0] for row in conn.execute(sa.text(
        "SELECT name_norm FROM catalog_terms WHERE kind = 'tag'"))}
    top = conn.execute(sa.text(
        "SELECT COALESCE(MAX(position), 0) FROM catalog_terms WHERE kind = 'tag'")).scalar()
    for i, name in enumerate(TAGS, start=1):
        if _norm(name) in have:
            continue
        conn.execute(sa.text(
            "INSERT INTO catalog_terms (kind, name, name_norm, description, position, "
            "is_active, requires_url, requires_body, has_scope, created_at) "
            "VALUES ('tag', :name, :norm, :note, :pos, :on, :off, :off, :off, :now)"),
            {"name": name, "norm": _norm(name), "note": TAG_NOTE, "pos": (top or 0) + i,
             "on": True, "off": False, "now": datetime.utcnow()})


def downgrade():
    conn = op.get_bind()
    # Теги прибираємо лише додані цією міграцією й ще ніде не використані.
    conn.execute(sa.text(
        "DELETE FROM catalog_terms WHERE kind = 'tag' AND description = :note "
        "AND id NOT IN (SELECT term_id FROM catalog_resource_tags)"), {"note": TAG_NOTE})
    marks, params = _in(ORDER)
    conn.execute(sa.text(
        f"UPDATE catalog_sections SET position = position - {OTHERS_SHIFT} "
        f"WHERE name NOT IN ({marks}) AND position >= {OTHERS_SHIFT}"), params)
    with op.batch_alter_table("catalog_sections") as batch:
        batch.drop_column("scenario")
