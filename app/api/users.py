"""Менеджмент користувачів (тільки Admin): CRUD, скидання пароля, ролі."""
from flask import Blueprint, request, jsonify
from app.extensions import db
from app.core.permissions import require_global_role
from app.core.security import hash_password
from app.core.errors import ApiError
from app.models import User, Role, UserRole
from app.services import quota_service

bp = Blueprint("users", __name__)


def _with_quota(user):
    d = user.to_dict()
    d["token_used"] = quota_service.get_used(user.id)            # за поточний тиждень
    d["custom_limit"] = quota_service.get_user_custom_limit(user.id)  # персональна або None
    d["effective_limit"] = quota_service.effective_limit(user.id)
    return d


@bp.get("")
@require_global_role("admin")
def list_users():
    users = User.query.order_by(User.id).all()
    return jsonify([_with_quota(u) for u in users])


@bp.post("/<int:user_id>/token-limit")
@require_global_role("admin")
def set_token_limit(user_id):
    """Встановити/оновити ПЕРСОНАЛЬНУ тижневу квоту (має пріоритет над системною)."""
    user = User.query.get_or_404(user_id)
    data = request.get_json(silent=True) or {}
    if "limit" not in data:
        raise ApiError("Вкажіть limit", 400, "validation_error")
    limit = quota_service.set_user_limit(user.id, data["limit"])
    return jsonify({"user_id": user.id, "custom_limit": limit})


@bp.delete("/<int:user_id>/token-limit")
@require_global_role("admin")
def delete_token_limit(user_id):
    """Видалити персональну квоту — користувач повертається до системної."""
    user = User.query.get_or_404(user_id)
    quota_service.delete_user_limit(user.id)
    return jsonify({"user_id": user.id,
                    "effective_limit": quota_service.effective_limit(user.id)})


@bp.post("")
@require_global_role("admin")
def create_user():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        raise ApiError("Вкажіть логін і пароль", 400, "validation_error")
    if User.query.filter_by(username=username).first():
        raise ApiError("Такий логін уже існує", 409, "conflict")

    user = User(
        username=username,
        email=(data.get("email") or None),
        full_name=data.get("full_name"),
        password_hash=hash_password(password),
        is_active=data.get("is_active", True),
    )
    db.session.add(user)
    db.session.flush()
    _set_roles(user, data.get("roles"))
    db.session.commit()
    return jsonify(user.to_dict()), 201


@bp.get("/<int:user_id>")
@require_global_role("admin")
def get_user(user_id):
    user = User.query.get_or_404(user_id)
    return jsonify(user.to_dict())


@bp.patch("/<int:user_id>")
@require_global_role("admin")
def update_user(user_id):
    user = User.query.get_or_404(user_id)
    data = request.get_json(silent=True) or {}
    if "email" in data:
        user.email = data["email"] or None
    if "full_name" in data:
        user.full_name = data["full_name"]
    if "is_active" in data:
        user.is_active = bool(data["is_active"])
    if "roles" in data:
        _set_roles(user, data["roles"])
    db.session.commit()
    return jsonify(user.to_dict())


@bp.post("/<int:user_id>/reset-password")
@require_global_role("admin")
def reset_password(user_id):
    user = User.query.get_or_404(user_id)
    data = request.get_json(silent=True) or {}
    new_password = data.get("password") or ""
    if len(new_password) < 6:
        raise ApiError("Пароль має містити щонайменше 6 символів",
                       400, "validation_error")
    user.password_hash = hash_password(new_password)
    db.session.commit()
    return jsonify({"message": "Пароль оновлено"})


def _set_roles(user, role_codes):
    if role_codes is None:
        return
    UserRole.query.filter_by(user_id=user.id).delete()
    for code in role_codes:
        role = Role.query.filter_by(code=code).first()
        if role:
            db.session.add(UserRole(user_id=user.id, role_id=role.id))
