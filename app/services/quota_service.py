"""QuotaService — ліміти токенів на користувача та статус використання.

Ліміт зберігається у token_limits (scope_type='user'); якщо явного ліміту
немає, береться DEFAULT_USER_TOKEN_LIMIT. Використання = сума total_tokens
з token_usage_logs користувача.
"""
from flask import current_app
from sqlalchemy import func

from app.extensions import db
from app.core.errors import ApiError
from app.models import TokenLimit, TokenUsageLog


def default_limit():
    return int(current_app.config.get("DEFAULT_USER_TOKEN_LIMIT", 2000))


def get_limit(user_id):
    tl = TokenLimit.query.filter_by(
        scope_type="user", scope_id=user_id, is_active=True).first()
    return int(tl.limit_tokens) if tl else default_limit()


def get_used(user_id):
    total = db.session.query(
        func.coalesce(func.sum(TokenUsageLog.total_tokens), 0)
    ).filter(TokenUsageLog.user_id == user_id).scalar()
    return int(total or 0)


def set_limit(user_id, limit):
    limit = int(limit)
    if limit < 0:
        raise ApiError("Ліміт не може бути відʼємним", 400, "validation_error")
    tl = TokenLimit.query.filter_by(scope_type="user", scope_id=user_id).first()
    if tl:
        tl.limit_tokens = limit
        tl.is_active = True
        tl.period = "total"
    else:
        db.session.add(TokenLimit(
            scope_type="user", scope_id=user_id, period="total",
            limit_tokens=limit, is_active=True))
    db.session.commit()
    return limit


def status(user_id):
    used = get_used(user_id)
    limit = get_limit(user_id)
    percent = round(min(100.0, used * 100.0 / limit), 1) if limit > 0 else 100.0
    return {
        "used": used,
        "limit": limit,
        "remaining": max(0, limit - used),
        "percent": percent,
    }


def ensure_within_limit(user):
    """Кидає 429, якщо користувач уже вичерпав ліміт токенів."""
    st = status(user.id)
    if st["limit"] > 0 and st["used"] >= st["limit"]:
        raise ApiError(
            f"Вичерпано ліміт токенів ({st['used']}/{st['limit']}). "
            "Зверніться до адміністратора, щоб збільшити квоту.",
            429, "quota_exceeded")
