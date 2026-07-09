"""Реєстр SQLAlchemy-моделей. Імпорт тут гарантує реєстрацію всіх таблиць."""
from app.models.user import User, Role, UserRole, RefreshToken
from app.models.group import Group, GroupMembership, Invitation
from app.models.skill import (
    Model, Skill, SkillCategory, SkillInput, SkillFeedback,
    GroupSkill, GroupModel, UserSkill,
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
from app.models.setting import AppSetting

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
    "GroupModel",
    "UserSkill",
    "ChatSession",
    "ChatMessage",
    "TokenUsageLog",
    "TokenLimit",
    "TokenCounter",
    "AuditLog",
    "UserFile",
    "AppSetting",
]
