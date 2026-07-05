"""Менеджмент користувачів (тільки Admin): CRUD, скидання пароля, ролі, групи."""
import os
import shutil
from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import or_
from app.extensions import db
from app.core.permissions import require_global_role
from app.core.security import hash_password, current_user
from app.core.errors import ApiError
from app.models import (
    User, Role, UserRole, Group, GroupMembership, UserSkill,
    ChatSession, ChatMessage, TokenUsageLog, TokenCounter, TokenLimit,
    RefreshToken, UserFile,
)
from app.services import quota_service, group_service

bp = Blueprint("users", __name__)


@bp.get("/search")
@require_global_role("admin")
def search_users():
    """Пошук користувачів за логіном, іменем або email (для додавання в групи)."""
    q = (request.args.get("q") or "").strip()
    query = User.query
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            User.username.ilike(like),
            User.full_name.ilike(like),
            User.email.ilike(like),
        ))
    users = query.order_by(User.username).limit(20).all()
    return jsonify([
        {"id": u.id, "username": u.username, "full_name": u.full_name,
         "email": u.email, "is_active": u.is_active}
        for u in users
    ])


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


@bp.delete("/<int:user_id>")
@require_global_role("admin")
def delete_user(user_id):
    """Повне видалення користувача та всіх його даних.

    Заборонено видаляти себе та системного адміністратора.
    """
    user = User.query.get_or_404(user_id)
    if user.id == current_user().id:
        raise ApiError("Не можна видалити власний обліковий запис", 400, "self_delete")
    if user.is_system_admin:
        raise ApiError("Не можна видалити системного адміністратора", 400, "protected_user")

    storage_uid = user.storage_uid

    # Видаляємо повідомлення чатів (за сесіями користувача), потім сесії.
    session_ids = [s.id for s in ChatSession.query.filter_by(user_id=user_id).all()]
    if session_ids:
        ChatMessage.query.filter(ChatMessage.session_id.in_(session_ids)).delete(
            synchronize_session=False)
    for model in (ChatSession, UserSkill, TokenCounter, TokenUsageLog,
                  RefreshToken, UserFile, GroupMembership, UserRole):
        model.query.filter_by(user_id=user_id).delete(synchronize_session=False)
    TokenLimit.query.filter_by(scope_type="user", scope_id=user_id).delete(
        synchronize_session=False)

    db.session.delete(user)
    db.session.commit()

    # Прибираємо теку з файлами користувача (не критично, якщо не вдалося).
    if storage_uid:
        path = os.path.join(current_app.config.get("USER_FILES_DIR", ""), storage_uid)
        if path and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)

    return jsonify({"message": "Користувача та його дані видалено"})


@bp.get("/<int:user_id>/groups")
@require_global_role("admin")
def user_groups(user_id):
    """Групи, у яких користувач є активним членом."""
    User.query.get_or_404(user_id)
    rows = (db.session.query(Group)
            .join(GroupMembership, GroupMembership.group_id == Group.id)
            .filter(GroupMembership.user_id == user_id,
                    GroupMembership.status == "active")
            .order_by(Group.name)
            .all())
    return jsonify([{"id": g.id, "name": g.name, "description": g.description}
                    for g in rows])


@bp.delete("/<int:user_id>/groups/<int:group_id>")
@require_global_role("admin")
def remove_user_from_group(user_id, group_id):
    """Вилучає користувача з конкретної групи."""
    group_service.remove_member(group_id, user_id)
    return jsonify({"message": "Користувача вилучено з групи"})


def _set_roles(user, role_codes):
    if role_codes is None:
        return
    UserRole.query.filter_by(user_id=user.id).delete()
    for code in role_codes:
        role = Role.query.filter_by(code=code).first()
        if role:
            db.session.add(UserRole(user_id=user.id, role_id=role.id))
