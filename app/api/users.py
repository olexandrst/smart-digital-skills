"""Менеджмент користувачів (тільки адміністратор): CRUD, скидання пароля,
ролі, розділи менеджера, ознаки «Експерт» і «Відповідальний за ідеї» та
рівень AI-зрілості з датою визначення.

Ролі: адміністратор, менеджер розділу (модератор чи заступник у кожному
своєму розділі) або користувач — без ролі; «Експерт» — ознака поверх ролі.
Адміністратор призначає їх тут, а масовим користувачам їх дають групи
корпоративного входу (див. entra_service.sync_roles): групову роль позначено
джерелом 'entra', і вручну її тут не прибрати — лише вивівши людину з групи.
"""
import os
import shutil
from datetime import date, datetime
from flask import Blueprint, request, jsonify, current_app
from app.extensions import db
from app.core.permissions import require_global_role
from app.core.security import hash_password, current_user
from app.core.errors import ApiError
from app.models import (
    User, Role, UserRole, SectionManager, RefreshToken, UserFile, Notification, CatalogFavorite,
    CatalogSection, CatalogResource, CatalogTerm, Skill,
    LearningProgress, HiddenRecommendation, SurveyPrompt, UserSession,
    Idea, ReviewLog, SearchQueryLog, SkillFeedback, SurveyResponse, ResourceView,
    ContactMessage,
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
                       (SurveyResponse, "user_id"), (ResourceView, "user_id"),
                       (CatalogResource, "pending_by"), (Skill, "pending_by"),
                       (CatalogResource, "mod_edit_by"), (Skill, "mod_edit_by"),
                       (ContactMessage, "user_id"), (ContactMessage, "handled_by"))
# Ролі, які призначає адміністратор. Користувач — це відсутність ролі.
ASSIGNABLE_ROLES = ("admin", "section_manager")


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
    _apply_access(user, data)
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
        if user.id == current_user().id and not data["is_active"]:
            raise ApiError("Не можна деактивувати власний обліковий запис",
                           400, "self_deactivate")
        user.is_active = bool(data["is_active"])
    _apply_access(user, data)
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
    user.managed_sections = []
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


def _apply_access(user, data):
    """Роль, розділи менеджера й ознаки з запиту (лише передані поля)."""
    if "roles" in data:
        _set_roles(user, data["roles"])
    if "managed_section_ids" in data or "deputy_section_ids" in data:
        _set_sections(user, data.get("managed_section_ids",
                                     [s.id for s in user.managed_sections]),
                      data.get("deputy_section_ids", user.deputy_section_ids))
    # Розділи без ролі менеджера нічого не дають — не лишаємо їх висіти.
    if not user.has_global_role("section_manager"):
        user.managed_sections = []
    if "is_expert" in data:
        expert = bool(data["is_expert"])
        if expert != bool(user.is_expert):
            user.expert_source = "manual" if expert else None
        user.is_expert = expert
    if "is_ideas_owner" in data:
        user.is_ideas_owner = bool(data["is_ideas_owner"])
    _apply_level(user, data)


def _apply_level(user, data):
    """Рівень за результатами корпоративного тестування й дата, коли його
    визначено. Без дати береться сьогоднішня — якщо рівень справді змінився."""
    if "maturity_level_id" in data:
        raw = data.get("maturity_level_id")
        level = None
        if raw not in (None, ""):
            try:
                term = db.session.get(CatalogTerm, int(raw))
            except (TypeError, ValueError):
                term = None
            if term is None or term.kind != "maturity":
                raise ApiError("Рівень зрілості не знайдено", 400, "validation_error")
            level = term.id
        if level != user.maturity_level_id:
            user.maturity_assessed_at = datetime.utcnow() if level else None
        user.maturity_level_id = level
    if "maturity_assessed_at" in data and user.maturity_level_id:
        raw = (data.get("maturity_assessed_at") or "").strip()
        if raw:
            try:
                day = date.fromisoformat(raw[:10])
            except ValueError:
                raise ApiError("Дата визначення рівня має бути у форматі РРРР-ММ-ДД",
                               400, "validation_error")
            if day > date.today():
                raise ApiError("Дата визначення рівня не може бути в майбутньому",
                               400, "validation_error")
            user.maturity_assessed_at = datetime(day.year, day.month, day.day)
    if not user.maturity_level_id:
        user.maturity_assessed_at = None


def _set_roles(user, role_codes):
    if role_codes is None:
        return
    codes = [c for c in role_codes if c in ASSIGNABLE_ROLES]
    unknown = [c for c in role_codes if c not in ASSIGNABLE_ROLES]
    if unknown:
        raise ApiError("Невідома роль: " + ", ".join(map(str, unknown)),
                       400, "validation_error")
    me = current_user()
    if user.id == me.id and "admin" not in codes and not user.is_system_admin:
        raise ApiError("Не можна зняти з себе роль адміністратора — інакше хаб "
                       "може лишитися без адміністратора", 400, "self_demote")
    # Ролі, що лишаються, зберігають своє джерело (зокрема групу Entra).
    current = {r.code: r for r in user.roles}
    for code, role in current.items():
        if code not in codes:
            UserRole.query.filter_by(user_id=user.id, role_id=role.id).delete()
    for code in codes:
        if code in current:
            continue
        role = Role.query.filter_by(code=code).first()
        if role:
            db.session.add(UserRole(user_id=user.id, role_id=role.id, source="manual"))
    db.session.flush()
    db.session.expire(user, ["roles"])


def _id_set(values, field):
    if values is None:
        return set()
    if not isinstance(values, list):
        raise ApiError(f"{field} має бути списком", 400, "validation_error")
    try:
        return {int(x) for x in values}
    except (TypeError, ValueError):
        raise ApiError("Некоректний розділ", 400, "validation_error")


def _set_sections(user, section_ids, deputy_ids=None):
    """Розділи менеджера й у яких із них він заступник (решта — модератор).

    У розділу один модератор і один заступник (DEV-02): система має знати,
    хто відповідає за розділ. Зайняте місце треба спершу звільнити — тихо
    підміняти чуже призначення не можна."""
    ids = _id_set(section_ids, "managed_section_ids")
    deputies = _id_set(deputy_ids, "deputy_section_ids") & ids
    sections = CatalogSection.query.filter(CatalogSection.id.in_(ids)).all() if ids else []
    if len(sections) != len(ids):
        raise ApiError("Розділ не знайдено", 404, "not_found")
    if user.has_global_role("section_manager"):
        for section in sections:
            deputy = section.id in deputies
            taken = (SectionManager.query
                     .filter(SectionManager.section_id == section.id,
                             SectionManager.user_id != user.id,
                             SectionManager.is_deputy == deputy).all())
            for row in taken:
                other = db.session.get(User, row.user_id)
                if other is None or not other.is_active \
                        or not other.has_global_role("section_manager"):
                    continue
                slot = "заступник" if deputy else "модератор"
                raise ApiError(
                    f"У розділі «{section.name}» уже є {slot}: "
                    f"{other.full_name or other.username}. Спершу зніміть це "
                    f"призначення або оберіть іншу роль у розділі.",
                    409, "section_slot_taken")
    user.managed_sections = sections
    db.session.flush()
    for row in SectionManager.query.filter_by(user_id=user.id).all():
        row.is_deputy = row.section_id in deputies
