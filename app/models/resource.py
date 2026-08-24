"""Ресурси каталогу: промпти, інструкції, агенти та корисні посилання.

Каталог більше не обмежується навичками (Skill). Усе інше — однотипні
«ресурси» з єдиною моделлю: різниця лише в `resource_type` та наборі
заповнених полів (текст промпту/інструкції або посилання на агента/сервіс).
"""
from datetime import datetime
from app.extensions import db


def _now():
    return datetime.utcnow()


# Типи ресурсів каталогу (навички живуть окремою моделлю Skill).
RESOURCE_TYPES = ("prompt", "instruction", "agent", "link")

# Типи, для яких обов'язкове посилання.
LINK_TYPES = ("agent", "link")

# Область посилання: зовнішній сервіс чи внутрішній ресурс компанії.
LINK_SCOPES = ("external", "internal")

# Акцент кольорової плитки-іконки у стилі Metinvest Digital.
ACCENTS = ("red", "ink", "steel", "amber", "green")


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
    icon_emoji = db.Column(db.String)    # емодзі-іконка у плитці
    accent = db.Column(db.String)        # колір плитки; None = типовий для виду
    is_featured = db.Column(db.Boolean, nullable=False, default=False)
    status = db.Column(db.String, nullable=False, default="draft")  # draft|published
    version = db.Column(db.String, nullable=False, default="1.0.0")
    opens_count = db.Column(db.Integer, nullable=False, default=0)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=_now, onupdate=_now)
    published_at = db.Column(db.DateTime)

    def tag_list(self):
        return [t.strip() for t in (self.tags or "").split(",") if t.strip()]

    def to_dict(self):
        return {
            "id": self.id,
            "item_type": "resource",
            "resource_type": self.resource_type,
            "name": self.name,
            "description": self.description or "",
            "category": self.category,
            "author": self.author,
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
