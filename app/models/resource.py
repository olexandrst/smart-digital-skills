"""Наповнення каталогу AI Knowledge Hub поза навичками.

Каталог більше не обмежується навичками (Skill). Усе інше — однотипні
«ресурси» з єдиною моделлю: різниця лише в `resource_type` та наборі
заповнених полів (текст промпту/інструкції або посилання на агента/сервіс).

Тут же — друга вісь навігації (`CatalogSection`), журнал перегляду матеріалів
та лог пошукових запитів для аналітики хабу.
"""
from datetime import datetime
from app.extensions import db


def _now():
    return datetime.utcnow()


def _iso(value):
    return value.isoformat() if value else None


# Типи ресурсів каталогу (навички живуть окремою моделлю Skill).
RESOURCE_TYPES = ("prompt", "instruction", "case", "agent", "link")

# Типи, для яких обов'язкове посилання.
LINK_TYPES = ("agent", "link")

# Область посилання: зовнішній сервіс чи внутрішній ресурс компанії.
LINK_SCOPES = ("external", "internal")

# Статуси життєвого циклу матеріалу (BR-12).
# draft — чернетка; published — опублікований; needs_update — потребує оновлення
# (видимий користувачам із застереженням); archived — знятий, видимий лише менеджерам.
RESOURCE_STATUSES = ("draft", "published", "needs_update", "archived")

# Статуси, які бачать звичайні користувачі.
PUBLIC_STATUSES = ("published", "needs_update")

# Рівень повторного використання рішення (BR-14).
REUSE_LEVELS = ("ready", "adaptable", "reference")

# Акцент кольорової плитки-іконки у стилі Metinvest Digital.
ACCENTS = ("red", "ink", "steel", "amber", "green")


class CatalogSection(db.Model):
    """Розділ каталогу («Business Box» із BRD) — друга вісь навігації.

    Дзеркалить розділи корпоративного AI Knowledge Hub («Toolbox», «Power Links»,
    «Learning Space» тощо). Якщо розділ поки живе у зовнішньому порталі, у нього
    заповнене `url` — плитка веде туди замість фільтрації каталогу. Коли контент
    переїде до нас, `url` очищується, і розділ починає показувати власні картки.
    """
    __tablename__ = "catalog_sections"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False, unique=True)
    description = db.Column(db.Text)
    icon_emoji = db.Column(db.String)
    accent = db.Column(db.String)        # колір плитки; None = типовий
    url = db.Column(db.String)           # зовнішній розділ (гібридний режим)
    position = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)

    def to_dict(self, counts=None):
        data = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "icon_emoji": self.icon_emoji,
            "accent": self.accent,
            "url": self.url,
            "position": self.position,
            "is_active": self.is_active,
        }
        if counts is not None:
            data["items_count"] = counts.get(self.id, 0)
        return data


class CatalogResource(db.Model):
    __tablename__ = "catalog_resources"

    id = db.Column(db.Integer, primary_key=True)
    resource_type = db.Column(db.String, nullable=False, default="prompt")
    name = db.Column(db.String, nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    category = db.Column(db.String)
    author = db.Column(db.String)
    tags = db.Column(db.String)          # через кому: "RAG, аналітика"
    body = db.Column(db.Text)            # текст промпту або інструкції (Markdown)
    url = db.Column(db.String)           # посилання (агенти, корисні посилання)
    link_scope = db.Column(db.String)    # external | internal (для посилань)
    section_id = db.Column(db.Integer, db.ForeignKey("catalog_sections.id"))
    owner = db.Column(db.String)         # відповідальний за матеріал (BR-11)
    tools = db.Column(db.String)         # інструменти та платформи
    reuse_level = db.Column(db.String)   # ready | adaptable | reference (BR-14)
    reviewed_at = db.Column(db.DateTime)      # дата останнього перегляду
    next_review_at = db.Column(db.DateTime)   # дата наступного перегляду
    icon_emoji = db.Column(db.String)    # емодзі-іконка у плитці
    accent = db.Column(db.String)        # колір плитки; None = типовий для виду
    is_featured = db.Column(db.Boolean, nullable=False, default=False)
    status = db.Column(db.String, nullable=False, default="draft")  # RESOURCE_STATUSES
    version = db.Column(db.String, nullable=False, default="1.0.0")
    opens_count = db.Column(db.Integer, nullable=False, default=0)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=_now, onupdate=_now)
    published_at = db.Column(db.DateTime)

    section = db.relationship("CatalogSection", lazy="joined")

    def tag_list(self):
        return [t.strip() for t in (self.tags or "").split(",") if t.strip()]

    def is_review_overdue(self):
        """Чи минула дата наступного перегляду (для звіту про актуальність)."""
        return bool(self.next_review_at and self.next_review_at < datetime.utcnow())

    def to_dict(self):
        return {
            "id": self.id,
            "item_type": "resource",
            "resource_type": self.resource_type,
            "name": self.name,
            "description": self.description or "",
            "category": self.category,
            "section_id": self.section_id,
            "section_name": self.section.name if self.section else None,
            "author": self.author,
            "owner": self.owner,
            "tools": self.tools,
            "reuse_level": self.reuse_level,
            "reviewed_at": _iso(self.reviewed_at),
            "next_review_at": _iso(self.next_review_at),
            "review_overdue": self.is_review_overdue(),
            "tags": self.tag_list(),
            "body": self.body,
            "url": self.url,
            "link_scope": self.link_scope,
            "icon_emoji": self.icon_emoji,
            "accent": self.accent,
            "is_featured": self.is_featured,
            "status": self.status,
            "version": self.version,
            "opens_count": self.opens_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "published_at": self.published_at.isoformat() if self.published_at else None,
        }


class CatalogFavorite(db.Model):
    """«Обране» користувача — спільне для навичок і ресурсів каталогу.

    item_type: 'skill' | 'resource'. Зв'язок навмисно без ForeignKey на дві
    різні таблиці; осиротілі рядки прибираються при видаленні елемента.
    """
    __tablename__ = "catalog_favorites"
    __table_args__ = (db.UniqueConstraint("user_id", "item_type", "item_id"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False)
    item_type = db.Column(db.String, nullable=False, default="skill")
    item_id = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)

    def to_dict(self):
        return {"item_type": self.item_type, "item_id": self.item_id}


class ReviewLog(db.Model):
    """Журнал життєвого циклу матеріалу (FR-08).

    Пишеться при кожній зміні статусу: хто, коли і з якого статусу в який
    перевів матеріал. Дає відповідь «чому цей матеріал досі опублікований».
    """
    __tablename__ = "review_logs"

    id = db.Column(db.Integer, primary_key=True)
    item_type = db.Column(db.String, nullable=False, default="resource")  # resource|skill
    item_id = db.Column(db.Integer, nullable=False)
    item_name = db.Column(db.String)          # знімок назви на момент запису
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    actor_name = db.Column(db.String)
    from_status = db.Column(db.String)
    to_status = db.Column(db.String, nullable=False)
    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)

    def to_dict(self):
        return {
            "id": self.id,
            "item_type": self.item_type,
            "item_id": self.item_id,
            "item_name": self.item_name,
            "actor_name": self.actor_name,
            "from_status": self.from_status,
            "to_status": self.to_status,
            "note": self.note,
            "created_at": _iso(self.created_at),
        }


class SearchQueryLog(db.Model):
    """Пошуковий запит користувача в каталозі (BR-08, FR-09).

    Дає Knowledge Manager відповідь на два питання: що шукають найчастіше і
    які запити не мають результатів (тобто якого контенту бракує). Заразом це
    єдине джерело даних для майбутнього AI-помічника пошуку.
    """
    __tablename__ = "search_query_logs"

    id = db.Column(db.Integer, primary_key=True)
    # Увага: атрибут НЕ можна називати `query` — він перекриє Model.query.
    query_text = db.Column("query", db.String, nullable=False)
    query_norm = db.Column(db.String, nullable=False)  # нижній регістр — для групування
    results_count = db.Column(db.Integer, nullable=False, default=0)
    section_id = db.Column(db.Integer, db.ForeignKey("catalog_sections.id"))
    kind = db.Column(db.String)               # активний фільтр за видом наповнення
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    # Заповнюється, якщо після запиту користувач відкрив картку (Search Success Rate).
    opened_item_type = db.Column(db.String)
    opened_item_id = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)

    def to_dict(self):
        return {
            "id": self.id,
            "query": self.query_text,
            "results_count": self.results_count,
            "kind": self.kind,
            "opened_item_type": self.opened_item_type,
            "created_at": _iso(self.created_at),
        }
