"""Моделі Azure AI Foundry, скіли, вхідні параметри та призначення."""
from datetime import datetime
from app.extensions import db


def _now():
    return datetime.utcnow()


class Model(db.Model):
    __tablename__ = "models"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    model_type = db.Column(db.String, nullable=False)  # llm | cv
    provider = db.Column(db.String, nullable=False, default="azure_ai_foundry")
    deployment_name = db.Column(db.String, nullable=False)
    api_version = db.Column(db.String)
    context_window = db.Column(db.Integer)
    config = db.Column(db.Text)  # JSON
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=_now, onupdate=_now)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "model_type": self.model_type,
            "provider": self.provider,
            "deployment_name": self.deployment_name,
            "api_version": self.api_version,
            "context_window": self.context_window,
            "is_active": self.is_active,
        }


class Skill(db.Model):
    __tablename__ = "skills"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    description = db.Column(db.Text, nullable=False)
    # 'prompt' — LLM-скіл; 'package' — архів зі skill.md та кодом, що виконується.
    skill_kind = db.Column(db.String, nullable=False, default="prompt")
    model_id = db.Column(db.Integer, db.ForeignKey("models.id"))  # nullable: пакетам не потрібен
    prompt_template = db.Column(db.Text)
    parameters = db.Column(db.Text)  # JSON: temperature, top_p
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
        data = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "skill_kind": self.skill_kind or "prompt",
            "model_id": self.model_id,
            "model_name": self.model.name if self.model else None,
            "runtime": self.runtime,
            "entrypoint": self.entrypoint,
            "package_filename": self.package_filename,
            "has_package": bool(self.package_path),
            "status": self.status,
            "version": self.version,
            "activations_count": self.activations_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_inputs:
            data["inputs"] = [i.to_dict() for i in self.inputs]
        return data


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
