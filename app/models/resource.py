"""Наповнення каталогу AI Knowledge Hub поза навичками.

Каталог більше не обмежується навичками (Skill). Усе інше — однотипні
«ресурси» з єдиною моделлю: різниця лише в `resource_type` та наборі
заповнених полів (текст промпту/інструкції або посилання на агента/сервіс).

Тут же — навігація каталогу (`CatalogSection` → `CatalogFolder`), керовані
довідники метаданих (`CatalogTerm`), журнал перегляду матеріалів та лог
пошукових запитів для аналітики хабу.
"""
from datetime import datetime
from app.extensions import db


def _now():
    return datetime.utcnow()


def _iso(value):
    return value.isoformat() if value else None


# Типи ресурсів каталогу (навички живуть окремою моделлю Skill).
RESOURCE_TYPES = ("prompt", "instruction", "case", "agent", "mcp", "link")

# Типи, для яких обов'язкове посилання (для MCP це endpoint сервера).
LINK_TYPES = ("agent", "mcp", "link")

# Типи, які без тексту беззмістовні. MCP сюди не входить: інструкція
# підключення бажана, але endpoint самодостатній.
BODY_TYPES = ("prompt", "instruction", "case")

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

# Види керованих довідників метаданих (FR-04).
# tag            — теги матеріалів (багато на картку);
# complexity     — рівень складності (один на картку);
# business_value — бізнес-цінність (один на картку);
# material_type  — вид матеріалу; значення прив'язані до RESOURCE_TYPES кодом,
#                  через довідник керуються лише назва, порядок і видимість.
TERM_KINDS = ("tag", "complexity", "business_value", "material_type")

# Довідники, значення яких вибираються по одному на картку.
SINGLE_VALUE_TERM_KINDS = ("complexity", "business_value")

# Початкове наповнення довідників: (назва, опис). Порядок = порядок у списках.
DEFAULT_TERMS = {
    "complexity": [
        ("Базовий", "Застосовується без підготовки"),
        ("Середній", "Потрібне розуміння інструменту"),
        ("Просунутий", "Потрібні технічні навички або налаштування"),
    ],
    "business_value": [
        ("Економія часу", "Скорочує тривалість рутинної роботи"),
        ("Якість рішень", "Зменшує кількість помилок або підвищує точність"),
        ("Масштабування досвіду", "Дає змогу повторити рішення в інших підрозділах"),
        ("Нова можливість", "Робить здійсненним те, чого раніше не робили"),
    ],
}

# Типові колекції з BRD (BR-04). Ключ — назва розділу, у якому створюється
# колекція під час первинного наповнення; порядок збережено.
DEFAULT_FOLDERS = (
    ("Created Agents", "Готові агенти, створені в компанії", "🤖"),
    ("Practical Skills", "Навички та практики щоденної роботи з AI", "🎯"),
    ("AI Solutions", "Впроваджені AI-рішення та їхні результати", "⚙️"),
    ("Templates & Prompts", "Шаблони документів і перевірені промпти", "📝"),
    ("Use Cases", "Кейси застосування AI у бізнес-процесах", "💼"),
    ("Cataloging Knowledge", "Правила ведення бази знань і метадані", "🗂️"),
)


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


class CatalogFolder(db.Model):
    """Колекція матеріалів усередині розділу — третій рівень навігації (BR-03).

    Модель BRD: Warehouse → Boxes → Folders → Files. «Box» — це `CatalogSection`,
    «Folder» — ця колекція, «File» — картка матеріалу. Колекція завжди належить
    розділу: без нього шлях навігації обривається. Матеріал може бути без
    колекції — тоді він доступний на рівні розділу й у пошуку.
    """
    __tablename__ = "catalog_folders"
    __table_args__ = (db.UniqueConstraint("section_id", "name"),)

    id = db.Column(db.Integer, primary_key=True)
    section_id = db.Column(db.Integer, db.ForeignKey("catalog_sections.id"),
                           nullable=False)
    name = db.Column(db.String, nullable=False)
    description = db.Column(db.Text)      # призначення колекції (BR-04)
    icon_emoji = db.Column(db.String)
    position = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)

    section = db.relationship("CatalogSection", lazy="joined")

    def to_dict(self, counts=None):
        data = {
            "id": self.id,
            "section_id": self.section_id,
            "section_name": self.section.name if self.section else None,
            "name": self.name,
            "description": self.description,
            "icon_emoji": self.icon_emoji,
            "position": self.position,
            "is_active": self.is_active,
        }
        if counts is not None:
            data["items_count"] = counts.get(self.id, 0)
        return data


class CatalogTerm(db.Model):
    """Значення керованого довідника метаданих (FR-04).

    Один довідник на кожен `kind` із TERM_KINDS. Картка посилається на термін
    за id, тому перейменування терміна одразу видно в усіх картках — у цьому й
    сенс довідника порівняно з вільним текстом.

    `code` заповнений лише для `material_type`: він прив'язує запис довідника до
    константи RESOURCE_TYPES, від якої залежить поведінка коду (обов'язковість
    посилання чи тексту). Додавання видів матеріалів із новою поведінкою —
    окрема задача; тут керуються назва, порядок і видимість.
    """
    __tablename__ = "catalog_terms"
    __table_args__ = (db.UniqueConstraint("kind", "name_norm"),)

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String, nullable=False)   # TERM_KINDS
    name = db.Column(db.String, nullable=False)
    # Нижній регістр без крайніх пробілів — щоб «RAG» і « rag » не роз'їхалися.
    name_norm = db.Column(db.String, nullable=False)
    code = db.Column(db.String)                   # лише для material_type
    description = db.Column(db.Text)
    position = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)

    @staticmethod
    def normalize(name):
        return " ".join(str(name or "").split()).casefold()

    def to_dict(self, counts=None):
        data = {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "code": self.code,
            "description": self.description,
            "position": self.position,
            "is_active": self.is_active,
        }
        if counts is not None:
            data["items_count"] = counts.get(self.id, 0)
        return data


class CatalogResourceTag(db.Model):
    """Зв'язок «картка ↔ тег» (FR-02): теги керовані, а не рядок через кому."""
    __tablename__ = "catalog_resource_tags"
    __table_args__ = (db.UniqueConstraint("resource_id", "term_id"),)

    id = db.Column(db.Integer, primary_key=True)
    resource_id = db.Column(db.Integer,
                            db.ForeignKey("catalog_resources.id", ondelete="CASCADE"),
                            nullable=False)
    term_id = db.Column(db.Integer,
                        db.ForeignKey("catalog_terms.id", ondelete="CASCADE"),
                        nullable=False)


class CatalogResource(db.Model):
    __tablename__ = "catalog_resources"

    id = db.Column(db.Integer, primary_key=True)
    resource_type = db.Column(db.String, nullable=False, default="prompt")
    name = db.Column(db.String, nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    category = db.Column(db.String)
    author = db.Column(db.String)
    # Спадщина: теги рядком через кому. Тепер джерело правди — catalog_resource_tags;
    # ця колонка лишена тільки як вхід для одноразового перенесення старих даних
    # (app/core/schema.py) і більше не пишеться.
    tags = db.Column(db.String)
    body = db.Column(db.Text)            # текст промпту або інструкції (Markdown)
    url = db.Column(db.String)           # посилання (агенти, корисні посилання)
    link_scope = db.Column(db.String)    # external | internal (для посилань)
    section_id = db.Column(db.Integer, db.ForeignKey("catalog_sections.id"))
    folder_id = db.Column(db.Integer, db.ForeignKey("catalog_folders.id"))
    complexity_id = db.Column(db.Integer, db.ForeignKey("catalog_terms.id"))
    business_value_id = db.Column(db.Integer, db.ForeignKey("catalog_terms.id"))
    owner = db.Column(db.String)         # відповідальний за матеріал (BR-11)
    owner_contact = db.Column(db.String)  # email або посилання для зв'язку з власником
    tools = db.Column(db.String)         # інструменти та платформи
    reuse_level = db.Column(db.String)   # ready | adaptable | reference (BR-14)
    # Покроковий сценарій «як повторити це рішення у себе» (BR-14).
    reuse_guidance = db.Column(db.Text)
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
    folder = db.relationship("CatalogFolder", lazy="joined")
    complexity = db.relationship("CatalogTerm", lazy="joined",
                                 foreign_keys=[complexity_id])
    business_value = db.relationship("CatalogTerm", lazy="joined",
                                     foreign_keys=[business_value_id])
    tag_terms = db.relationship(
        "CatalogTerm", lazy="selectin", order_by="CatalogTerm.name",
        secondary="catalog_resource_tags", viewonly=True)

    def tag_list(self):
        """Назви тегів матеріалу — з довідника, у стабільному порядку."""
        return [t.name for t in self.tag_terms]

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
            "folder_id": self.folder_id,
            "folder_name": self.folder.name if self.folder else None,
            "complexity_id": self.complexity_id,
            "complexity_name": self.complexity.name if self.complexity else None,
            "business_value_id": self.business_value_id,
            "business_value_name": (self.business_value.name
                                    if self.business_value else None),
            "author": self.author,
            "owner": self.owner,
            "owner_contact": self.owner_contact,
            "tools": self.tools,
            "reuse_level": self.reuse_level,
            "reuse_guidance": self.reuse_guidance,
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
            "created_by": self.created_by,
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
