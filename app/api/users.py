"""Менеджмент користувачів (тільки Admin): CRUD, скидання пароля, ролі."""
import os
import shutil
from flask import Blueprint, request, jsonify, current_app
from app.extensions import db
from app.core.permissions import require_global_role
from app.core.security import hash_password, current_user
from app.core.errors import ApiError
from app.models import (
    User, Role, UserRole, RefreshToken, UserFile, Notification, CatalogFavorite,
    LearningProgress, HiddenRecommendation, SurveyPrompt, UserSession,
    Idea, ReviewLog, SearchQueryLog, SkillFeedback, SurveyResponse, ResourceView,
)

bp = Blueprint("users", __name__)

# Записи, що належать користувачеві й без нього не мають сенсу. Ролі сюди не
# входять: це колекція User.roles, і зв'язки з нею знімає сама ORM.
_OWNED_BY_USER = (RefreshToken, UserFile, Notification, CatalogFavorite,
                  LearningProgress, HiddenRecommendation, SurveyPrompt,
                  UserSession)
# Записи, що мають пережити користувача (історія, аналітика): лише
# відв'язуються. (колонка, модель)
_REFERENCES_TO_USER = ((Idea, "author_id"), (ReviewLog, "actor_user_id"),
                       (SearchQueryLog, "user_id"), (SkillFeedback, "user_id"),
                       (SurveyResponse, "user_id"), (ResourceView, "user_id"))


@bp.get("")
@require_global_role("admin")
def list_users():
    users = User.query.order_by(User.id).all()
    return jsonify([u.to_dict() for u in users])


@bp.post("")
@require_global_role("admin")
def create_user():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        raise ApiError("Вкажіть логін і пароль", 400, "validation_error")
    # Перевіряємо без урахування регістру: вхід теж його ігнорує, тож
    # «Admin» поруч із «admin» створив би два записи з одним логіном.
    if User.by_login(username) is not None:
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
    """Повне видалення користувача та його даних.

    Каскад робимо явно, а не покладаємося на ON DELETE у схемі: у SQLite
    перевірка зовнішніх ключів типово вимкнена (див. SQLITE_FOREIGN_KEYS),
    і без цього лишилися б осиротілі рядки. Заборонено видаляти себе та
    системного адміністратора.
    """
    user = User.query.get_or_404(user_id)
    if user.id == current_user().id:
        raise ApiError("Не можна видалити власний обліковий запис", 400, "self_delete")
    if user.is_system_admin:
        raise ApiError("Не можна видалити системного адміністратора", 400, "protected_user")

    storage_uid = user.storage_uid
    user.roles = []
    for model in _OWNED_BY_USER:
        model.query.filter_by(user_id=user_id).delete(synchronize_session=False)
    for model, column in _REFERENCES_TO_USER:
        col = getattr(model, column)
        model.query.filter(col == user_id).update({col: None}, synchronize_session=False)

    db.session.delete(user)
    db.session.commit()

    # Тека з вкладеннями, які завантажував користувач (не критично, якщо не вдалося).
    if storage_uid:
        path = os.path.join(current_app.config.get("USER_FILES_DIR", ""), storage_uid)
        if path and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)

    return jsonify({"message": "Користувача та його дані видалено"})


def _set_roles(user, role_codes):
    if role_codes is None:
        return
    UserRole.query.filter_by(user_id=user.id).delete()
    for code in role_codes:
        role = Role.query.filter_by(code=code).first()
        if role:
            db.session.add(UserRole(user_id=user.id, role_id=role.id))
