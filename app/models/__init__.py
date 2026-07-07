"""Реєстр SQLAlchemy-моделей. Імпорт тут гарантує реєстрацію всіх таблиць."""
from app.models.user import User, Role, UserRole, RefreshToken
from app.models.group import Group, GroupMembership, Invitation
from app.models.skill import (
    Model, Skill, SkillCategory, SkillInput, SkillFeedback, GroupSkill, UserSkill,
)
from app.models.chat import (
    ChatSession,
    ChatMessage,
    TokenUsageLog,
    TokenLimit,
    TokenCounter,
    AuditLog,
)
from app.models.file import UserFile

__all__ = [
    "User",
    "Role",
    "UserRole",
    "RefreshToken",
    "Group",
    "GroupMembership",
    "Invitation",
    "Model",
    "Skill",
    "SkillCategory",
    "SkillInput",
    "SkillFeedback",
    "GroupSkill",
    "UserSkill",
    "ChatSession",
    "ChatMessage",
    "TokenUsageLog",
    "TokenLimit",
    "TokenCounter",
    "AuditLog",
    "UserFile",
]
