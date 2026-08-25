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
from app.models.resource import (
    CatalogSection, CatalogResource, CatalogFavorite, ReviewLog, SearchQueryLog,
    RESOURCE_TYPES, LINK_TYPES, LINK_SCOPES, ACCENTS,
    RESOURCE_STATUSES, PUBLIC_STATUSES, REUSE_LEVELS,
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
    "CatalogSection",
    "CatalogResource",
    "CatalogFavorite",
    "ReviewLog",
    "SearchQueryLog",
    "RESOURCE_STATUSES",
    "PUBLIC_STATUSES",
    "REUSE_LEVELS",
    "RESOURCE_TYPES",
    "LINK_TYPES",
    "LINK_SCOPES",
    "ACCENTS",
    "UserFile",
    "AppSetting",
]
