"""Чат-сесії, повідомлення, облік токенів, ліміти та аудит."""
from datetime import datetime
from app.extensions import db


def _now():
    return datetime.utcnow()


class ChatSession(db.Model):
    __tablename__ = "chat_sessions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"))
    model_id = db.Column(db.Integer, db.ForeignKey("models.id"))  # обрана модель чату
    skill_id = db.Column(db.Integer, db.ForeignKey("skills.id"))
    title = db.Column(db.String)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=_now, onupdate=_now)

    messages = db.relationship(
        "ChatMessage", backref="session", lazy="select",
        cascade="all, delete-orphan", order_by="ChatMessage.created_at",
    )
    model = db.relationship("Model", lazy="joined")

    @property
    def total_tokens_sum(self):
        return sum(m.total_tokens or 0 for m in self.messages)

    def to_dict(self, include_messages=False):
        data = {
            "id": self.id,
            "user_id": self.user_id,
            "model_id": self.model_id,
            "model_name": self.model.name if self.model else None,
            "skill_id": self.skill_id,
            "title": self.title,
            "total_tokens": self.total_tokens_sum,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_messages:
            data["messages"] = [m.to_dict() for m in self.messages]
        return data


class ChatMessage(db.Model):
    __tablename__ = "chat_messages"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
                           nullable=False)
    role = db.Column(db.String, nullable=False)  # system | user | assistant
    content = db.Column(db.Text)
    skill_id = db.Column(db.Integer, db.ForeignKey("skills.id"))  # застосований скіл (опц.)
    msg_metadata = db.Column("metadata", db.Text)  # JSON
    prompt_tokens = db.Column(db.Integer, default=0)
    completion_tokens = db.Column(db.Integer, default=0)
    total_tokens = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)

    def to_dict(self):
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "skill_id": self.skill_id,
            "prompt_tokens": self.prompt_tokens or 0,
            "completion_tokens": self.completion_tokens or 0,
            "total_tokens": self.total_tokens or 0,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class TokenUsageLog(db.Model):
    __tablename__ = "token_usage_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"))
    skill_id = db.Column(db.Integer, db.ForeignKey("skills.id"))
    model_id = db.Column(db.Integer, db.ForeignKey("models.id"))
    session_id = db.Column(db.Integer, db.ForeignKey("chat_sessions.id"))
    prompt_tokens = db.Column(db.Integer, nullable=False, default=0)
    completion_tokens = db.Column(db.Integer, nullable=False, default=0)
    total_tokens = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)


class TokenLimit(db.Model):
    """Детальні ліміти токенів (Етап 2, схема закладена наперед)."""
    __tablename__ = "token_limits"

    id = db.Column(db.Integer, primary_key=True)
    scope_type = db.Column(db.String, nullable=False)  # global | group | user
    scope_id = db.Column(db.Integer)
    period = db.Column(db.String, nullable=False, default="monthly")
    limit_tokens = db.Column(db.Integer, nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=_now, onupdate=_now)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    action = db.Column(db.String, nullable=False)
    entity_type = db.Column(db.String)
    entity_id = db.Column(db.Integer)
    details = db.Column(db.Text)  # JSON
    ip_address = db.Column(db.String)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)
