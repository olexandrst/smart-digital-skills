"""Облік токенів: власні (усі), групи (менеджер), глобально (Admin)."""
from flask import Blueprint, jsonify
from sqlalchemy import func
from app.extensions import db
from app.core.permissions import require_auth, require_global_role, require_group_role
from app.core.security import current_user
from app.models import TokenUsageLog, User
from app.services import quota_service

bp = Blueprint("usage", __name__)


def _aggregate(query):
    row = query.with_entities(
        func.coalesce(func.sum(TokenUsageLog.prompt_tokens), 0),
        func.coalesce(func.sum(TokenUsageLog.completion_tokens), 0),
        func.coalesce(func.sum(TokenUsageLog.total_tokens), 0),
        func.count(TokenUsageLog.id),
    ).one()
    return {
        "prompt_tokens": int(row[0]),
        "completion_tokens": int(row[1]),
        "total_tokens": int(row[2]),
        "requests": int(row[3]),
    }


@bp.get("/me")
@require_auth
def my_usage():
    user = current_user()
    data = _aggregate(TokenUsageLog.query.filter_by(user_id=user.id))
    data["quota"] = quota_service.status(user.id)
    return jsonify(data)


@bp.get("/group/<int:group_id>")
@require_group_role("manager")
def group_usage(group_id):
    q = TokenUsageLog.query.filter_by(group_id=group_id)
    return jsonify(_aggregate(q))


@bp.get("/global")
@require_global_role("admin")
def global_usage():
    total = _aggregate(TokenUsageLog.query)
    # Розбивка по користувачах для адмін-панелі.
    rows = (
        db.session.query(
            User.username,
            func.coalesce(func.sum(TokenUsageLog.total_tokens), 0),
            func.count(TokenUsageLog.id),
        )
        .join(TokenUsageLog, TokenUsageLog.user_id == User.id)
        .group_by(User.id)
        .all()
    )
    total["by_user"] = [
        {"username": r[0], "total_tokens": int(r[1]), "requests": int(r[2])}
        for r in rows
    ]
    return jsonify(total)
