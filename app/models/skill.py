"""Навички каталогу: пакет зі skill.md, категорії та зворотний зв'язок.

Навичка — це матеріал каталогу, який хостимо, а не запускаємо: пакет
(.zip / .skill) лежить у сховищі, користувач читає опис та інструкції й
завантажує пакет собі. Виконання коду, призначення моделям і групам, облік
активацій у застосунку більше немає.
"""
from datetime import datetime
from app.extensions import db


def _now():
    return datetime.utcnow()


class Skill(db.Model):
    __tablename__ = "skills"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    description = db.Column(db.Text, nullable=False)
    author = db.Column(db.String)        # автор навички (зі skill.md або редагується)
    category = db.Column(db.String)      # категорія навички
    section_id = db.Column(db.Integer, db.ForeignKey("catalog_sections.id"))
    folder_id = db.Column(db.Integer, db.ForeignKey("catalog_folders.id"))
    owner = db.Column(db.String)         # відповідальний за матеріал (BR-11)
    icon_path = db.Column(db.String)     # шлях до завантаженої PNG-іконки (nullable)
    # 'package' — архів зі skill.md і файлами; 'prompt' — навичка без пакета,
    # лише з текстом інструкцій (історичні записи до переходу на пакети).
    skill_kind = db.Column(db.String, nullable=False, default="prompt")
    # Тіло skill.md — інструкції навички. Показуються в картці.
    prompt_template = db.Column(db.Text)
    # Описові атрибути для каталогу (показуються у детальній картці):
    input_spec = db.Column(db.Text)      # «Вхідні дані»
    output_spec = db.Column(db.Text)     # «Результат роботи»
    starter_prompt = db.Column(db.Text)  # «Стартовий промпт»
    package_filename = db.Column(db.String)  # оригінальна назва завантаженого архіву
    package_path = db.Column(db.String)      # шлях до збереженого архіву
    status = db.Column(db.String, nullable=False, default="draft")  # draft|published
    version = db.Column(db.String, nullable=False, default="1.0.0")
    # Скільки разів пакет завантажили — міра популярності навички в каталозі
    # (замість колишнього лічильника активацій).
    downloads_count = db.Column(db.Integer, nullable=False, default=0)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=_now, onupdate=_now)
    published_at = db.Column(db.DateTime)

    def to_dict(self):
        icon_ver = int(self.updated_at.timestamp()) if self.updated_at else 0
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "author": self.author,
            "category": self.category,
            "section_id": self.section_id,
            "folder_id": self.folder_id,
            "owner": self.owner,
            "created_by": self.created_by,
            "skill_kind": self.skill_kind or "prompt",
            "instructions": self.prompt_template,
            "package_filename": self.package_filename,
            "has_package": bool(self.package_path),
            "has_icon": bool(self.icon_path),
            # URL іконки з версійним параметром для скидання кешу браузера.
            "icon_url": (f"/api/skills/{self.id}/icon?v={icon_ver}"
                         if self.icon_path else None),
            "input_spec": self.input_spec,
            "output_spec": self.output_spec,
            "starter_prompt": self.starter_prompt,
            "status": self.status,
            "version": self.version,
            "downloads_count": self.downloads_count or 0,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SkillCategory(db.Model):
    """Керований довідник категорій навичок (адміністратор і менеджер каталогу)."""
    __tablename__ = "skill_categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False, unique=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)

    def to_dict(self):
        return {"id": self.id, "name": self.name}


class SkillFeedback(db.Model):
    """Зворотний зв'язок користувача щодо матеріалу каталогу.

    Історично таблиця обслуговувала лише навички, тому й назва. Тепер сюди ж
    надходить фідбек про ресурси каталогу: `item_type` розрізняє їх, а
    `resource_id` посилається на ресурс (для навичок лишається `skill_id`).
    Оцінка `rating` (1–5) необов'язкова — можна лишити самий коментар.

    Назву матеріалу, версію та логін зберігаємо знімком, щоб повідомлення
    лишалось читабельним навіть після зміни/видалення матеріалу чи користувача.
    """
    __tablename__ = "skill_feedback"

    id = db.Column(db.Integer, primary_key=True)
    item_type = db.Column(db.String, nullable=False, default="skill")  # skill|resource
    skill_id = db.Column(db.Integer, db.ForeignKey("skills.id", ondelete="SET NULL"))
    resource_id = db.Column(db.Integer,
                            db.ForeignKey("catalog_resources.id", ondelete="SET NULL"))
    skill_name = db.Column(db.String, nullable=False)   # знімок назви матеріалу
    skill_version = db.Column(db.String)
    rating = db.Column(db.Integer)       # 1–5, необов'язково
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    username = db.Column(db.String)
    message = db.Column(db.Text, nullable=False)
    # Публічний коментар бачать усі під карткою. Відгуки, залишені до появи
    # коментарів, писалися лише для команди — вони лишаються приватними.
    is_public = db.Column(db.Boolean, nullable=False, default=True)
    is_read = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)

    def to_dict(self):
        return {
            "id": self.id,
            "item_type": self.item_type or "skill",
            "skill_id": self.skill_id,
            "resource_id": self.resource_id,
            "rating": self.rating,
            "skill_name": self.skill_name,
            "skill_version": self.skill_version,
            "user_id": self.user_id,
            "username": self.username,
            "message": self.message,
            "is_public": self.is_public,
            "is_read": self.is_read,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
