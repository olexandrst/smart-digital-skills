"""Моделі Azure AI Foundry, скіли, вхідні параметри та призначення."""
from datetime import datetime
from app.extensions import db


def _now():
    return datetime.utcnow()


class Model(db.Model):
    __tablename__ = "models"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    model_type = db.Column(db.String, nullable=False, default="llm")  # завжди llm
    provider = db.Column(db.String, nullable=False, default="azure_ai_foundry")
    deployment_name = db.Column(db.String, nullable=False)
    # Базовий URL API (для локальних моделей Ollama/LM Studio та OpenAI-сумісних).
    base_url = db.Column(db.String)
    api_version = db.Column(db.String)
    context_window = db.Column(db.Integer)
    config = db.Column(db.Text)  # JSON
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    is_system = db.Column(db.Boolean, nullable=False, default=False)  # системна модель
    created_at = db.Column(db.DateTime, nullable=False, default=_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=_now, onupdate=_now)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "model_type": self.model_type,
            "provider": self.provider,
            "deployment_name": self.deployment_name,
            "base_url": self.base_url,
            "is_system": self.is_system,
            "api_version": self.api_version,
            "context_window": self.context_window,
            "is_active": self.is_active,
        }


class Skill(db.Model):
    __tablename__ = "skills"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    description = db.Column(db.Text, nullable=False)
    author = db.Column(db.String)        # автор навички (зі skill.md або редагується)
    category = db.Column(db.String)      # категорія навички
    icon_path = db.Column(db.String)     # шлях до завантаженої PNG-іконки (nullable)
    # 'prompt' — LLM-навичка; 'package' — архів зі skill.md та кодом, що виконується.
    skill_kind = db.Column(db.String, nullable=False, default="prompt")
    model_id = db.Column(db.Integer, db.ForeignKey("models.id"))  # nullable: пакетам не потрібен
    prompt_template = db.Column(db.Text)
    parameters = db.Column(db.Text)  # JSON: temperature, top_p
    # Описові атрибути для каталогу (показуються у детальній картці):
    input_spec = db.Column(db.Text)      # «Вхідні дані»
    output_spec = db.Column(db.Text)     # «Результат роботи»
    starter_prompt = db.Column(db.Text)  # «Стартовий промпт»
    # Поля для скілів-пакетів:
    runtime = db.Column(db.String)          # напр. 'python'
    entrypoint = db.Column(db.String)       # шлях до файлу запуску відносно кореня пакета
    package_filename = db.Column(db.String) # оригінальна назва завантаженого архіву
    package_path = db.Column(db.String)     # шлях до збереженого архіву
    status = db.Column(db.String, nullable=False, default="draft")  # draft|testing|published|delisted
    version = db.Column(db.String, nullable=False, default="1.0.0")
    activations_count = db.Column(db.Integer, nullable=False, default=0)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=_now, onupdate=_now)
    published_at = db.Column(db.DateTime)

    model = db.relationship("Model", lazy="joined")
    inputs = db.relationship(
        "SkillInput", backref="skill", lazy="select",
        cascade="all, delete-orphan", order_by="SkillInput.position",
    )

    def to_dict(self, include_inputs=True):
        icon_ver = int(self.updated_at.timestamp()) if self.updated_at else 0
        data = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "author": self.author,
            "category": self.category,
            "skill_kind": self.skill_kind or "prompt",
            "model_id": self.model_id,
            "model_name": self.model.name if self.model else None,
            "runtime": self.runtime,
            "entrypoint": self.entrypoint,
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
            "activations_count": self.activations_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_inputs:
            data["inputs"] = [i.to_dict() for i in self.inputs]
        return data


class SkillCategory(db.Model):
    """Керований довідник категорій навичок (Admin/Skill Manager)."""
    __tablename__ = "skill_categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False, unique=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)

    def to_dict(self):
        return {"id": self.id, "name": self.name}


class SkillFeedback(db.Model):
    """Зворотний зв'язок користувача щодо навички (надходить у «Управління навичками»).

    Назву навички, версію та логін зберігаємо знімком, щоб повідомлення лишалось
    читабельним навіть після зміни/видалення навички чи користувача.
    """
    __tablename__ = "skill_feedback"

    id = db.Column(db.Integer, primary_key=True)
    skill_id = db.Column(db.Integer, db.ForeignKey("skills.id", ondelete="SET NULL"))
    skill_name = db.Column(db.String, nullable=False)
    skill_version = db.Column(db.String)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    username = db.Column(db.String)
    message = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)

    def to_dict(self):
        return {
            "id": self.id,
            "skill_id": self.skill_id,
            "skill_name": self.skill_name,
            "skill_version": self.skill_version,
            "user_id": self.user_id,
            "username": self.username,
            "message": self.message,
            "is_read": self.is_read,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class SkillInput(db.Model):
    __tablename__ = "skill_inputs"
    __table_args__ = (db.UniqueConstraint("skill_id", "name"),)

    id = db.Column(db.Integer, primary_key=True)
    skill_id = db.Column(db.Integer, db.ForeignKey("skills.id", ondelete="CASCADE"),
                         nullable=False)
    name = db.Column(db.String, nullable=False)
    label = db.Column(db.String)
    data_type = db.Column(db.String, nullable=False, default="string")
    is_required = db.Column(db.Boolean, nullable=False, default=True)
    default_value = db.Column(db.Text)
    description = db.Column(db.Text)
    position = db.Column(db.Integer, nullable=False, default=0)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "label": self.label,
            "data_type": self.data_type,
            "is_required": self.is_required,
            "default_value": self.default_value,
            "description": self.description,
            "position": self.position,
        }


class GroupSkill(db.Model):
    __tablename__ = "group_skills"
    __table_args__ = (db.UniqueConstraint("group_id", "skill_id"),)

    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id", ondelete="CASCADE"),
                         nullable=False)
    skill_id = db.Column(db.Integer, db.ForeignKey("skills.id", ondelete="CASCADE"),
                         nullable=False)
    assigned_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    assigned_at = db.Column(db.DateTime, nullable=False, default=_now)


class UserSkill(db.Model):
    """Матеріалізований ефективний доступ користувача до скіла."""
    __tablename__ = "user_skills"
    __table_args__ = (db.UniqueConstraint("user_id", "skill_id"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False)
    skill_id = db.Column(db.Integer, db.ForeignKey("skills.id", ondelete="CASCADE"),
                         nullable=False)
    source = db.Column(db.String, nullable=False, default="self")  # self | group
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"))
    assigned_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    assigned_at = db.Column(db.DateTime, nullable=False, default=_now)
