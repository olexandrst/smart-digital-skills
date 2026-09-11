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
    CatalogSection, CatalogFolder, CatalogTerm, CatalogResourceTag,
    CatalogResource, CatalogFavorite, ReviewLog, SearchQueryLog, SearchSynonym,
    RESOURCE_TYPES, LINK_TYPES, BODY_TYPES, LINK_SCOPES, ACCENTS,
    RESOURCE_STATUSES, PUBLIC_STATUSES, REUSE_LEVELS,
    TERM_KINDS, SINGLE_VALUE_TERM_KINDS, DEFAULT_TERMS, DEFAULT_FOLDERS,
    DEFAULT_SYNONYMS,
)
from app.models.idea import Idea, IDEA_STATUSES, IDEA_CLOSED_STATUSES
from app.models.usage import (
    UserSession, ResourceView, SESSION_IDLE_MINUTES, VIEW_TARGETS,
)
from app.models.survey import (
    SurveyResponse, SurveyPrompt, SURVEY_KINDS, SURVEY_SCALES,
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
    "CatalogFolder",
    "CatalogTerm",
    "CatalogResourceTag",
    "CatalogResource",
    "CatalogFavorite",
    "ReviewLog",
    "SearchQueryLog",
    "SearchSynonym",
    "DEFAULT_SYNONYMS",
    "TERM_KINDS",
    "SINGLE_VALUE_TERM_KINDS",
    "DEFAULT_TERMS",
    "DEFAULT_FOLDERS",
    "RESOURCE_STATUSES",
    "PUBLIC_STATUSES",
    "REUSE_LEVELS",
    "RESOURCE_TYPES",
    "LINK_TYPES",
    "BODY_TYPES",
    "LINK_SCOPES",
    "ACCENTS",
    "Idea",
    "IDEA_STATUSES",
    "IDEA_CLOSED_STATUSES",
    "UserSession",
    "ResourceView",
    "SESSION_IDLE_MINUTES",
    "VIEW_TARGETS",
    "SurveyResponse",
    "SurveyPrompt",
    "SURVEY_KINDS",
    "SURVEY_SCALES",
    "UserFile",
    "AppSetting",
]
