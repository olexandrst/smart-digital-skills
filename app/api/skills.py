"""Навички: каталог, публікація (Admin/Skill Manager), активація користувачами.

Керування каталогом: навички лише завантажуються пакетами (upload) —
ручного створення через форму немає. Завантаження без контексту створює
нову навичку; завантаження в контексті наявної — її нову версію.
"""
import json
import os
import re
from datetime import datetime
from flask import Blueprint, request, jsonify, send_file
from app.extensions import db
from app.core.permissions import require_auth, require_global_role
from app.core.security import current_user
from app.core.errors import ApiError
from app.models import (
    Skill, SkillInput, SkillFeedback, UserSkill, GroupSkill, Group,
    CatalogFavorite, CatalogSection, ChatSession, ChatMessage, TokenUsageLog,
    UserFile, ReviewLog,
)
from app.services import skill_service, chat_service, package_service

bp = Blueprint("skills", __name__)

# Статуси навички: 'draft' = «Не опублікована», 'published' = «Опублікована».
VALID_STATUS = {"draft", "published"}
ALLOWED_PACKAGE_EXT = (".zip", ".skill")
DEFAULT_CATEGORY = "Загальне"


@bp.get("")
@require_auth
def list_skills():
    """Admin/Skill Manager бачать усі навички; решта — лише опубліковані."""
    user = current_user()
    if user.has_global_role("admin") or user.has_global_role("skill_manager"):
        skills = Skill.query.order_by(Skill.id).all()
    else:
        skills = Skill.query.filter_by(status="published").order_by(Skill.id).all()
    return jsonify([s.to_dict() for s in skills])


@bp.get("/mine")
@require_auth
def my_skills():
    """Навички, доступні користувачу: власні активації ∪ навички його груп."""
    user = current_user()
    skill_ids = skill_service.effective_skill_ids(user.id)
    skills = (Skill.query.filter(Skill.id.in_(skill_ids))
              .filter_by(status="published").all()) if skill_ids else []
    return jsonify([s.to_dict() for s in skills])


@bp.get("/settings")
@require_auth
def skills_settings():
    """Налаштування доступу до навичок (для UI: Каталог та самоактивація)."""
    return jsonify({"individual_access": skill_service.individual_access_enabled()})


@bp.get("/access-matrix")
@require_global_role("admin")
def access_matrix():
    """Матриця доступів: групи (рядки) × опубліковані навички (колонки)."""
    groups = Group.query.order_by(Group.name).all()
    skills = Skill.query.filter_by(status="published").order_by(Skill.name).all()
    grants = [f"{gs.group_id}:{gs.skill_id}"
              for gs in GroupSkill.query.filter_by(is_active=True).all()]
    return jsonify({
        "groups": [{"id": g.id, "name": g.name} for g in groups],
        "skills": [{"id": s.id, "name": s.name} for s in skills],
        "grants": grants,
        "individual_access": skill_service.individual_access_enabled(),
    })


@bp.post("/access")
@require_global_role("admin")
def set_access():
    """Вмикає/вимикає доступ групи до навички (чекбокс матриці)."""
    data = request.get_json(silent=True) or {}
    gid, sid = data.get("group_id"), data.get("skill_id")
    if not gid or not sid:
        raise ApiError("Вкажіть group_id та skill_id", 400, "validation_error")
    Group.query.get_or_404(gid)
    granted = bool(data.get("granted"))
    if granted:
        skill_service.assign_to_group(gid, sid, current_user().id)
    else:
        skill_service.remove_from_group(gid, sid)
    return jsonify({"group_id": gid, "skill_id": sid, "granted": granted})


@bp.post("/access-settings")
@require_global_role("admin")
def set_access_settings():
    """Перемикач «Дозволити індивідуальний доступ» (Каталог + самоактивація)."""
    data = request.get_json(silent=True) or {}
    if "individual_access" not in data:
        raise ApiError("Вкажіть individual_access", 400, "validation_error")
    value = skill_service.set_individual_access(bool(data["individual_access"]))
    return jsonify({"individual_access": value})


@bp.get("/<int:skill_id>")
@require_auth
def get_skill(skill_id):
    skill = Skill.query.get_or_404(skill_id)
    return jsonify(skill.to_dict())


@bp.patch("/<int:skill_id>")
@require_global_role("admin", "skill_manager")
def update_skill(skill_id):
    """Редагування атрибутів навички: назва, версія, автор, опис, категорія."""
    skill = Skill.query.get_or_404(skill_id)
    data = request.get_json(silent=True) or {}
    for field in ("name", "description", "version", "author", "category", "owner",
                  "prompt_template", "input_spec", "output_spec", "starter_prompt"):
        if field in data:
            value = data[field]
            if field in ("name", "version") and not (value or "").strip():
                raise ApiError("Назва та версія не можуть бути порожніми",
                               400, "validation_error")
            setattr(skill, field, value)
    if "section_id" in data:
        section_id = data["section_id"]
        if section_id in (None, "", 0):
            skill.section_id = None
        elif CatalogSection.query.get(section_id) is None:
            raise ApiError("Розділ не знайдено", 404, "not_found")
        else:
            skill.section_id = section_id
    if "model_id" in data:
        skill.model_id = data["model_id"]
    if "parameters" in data:
        skill.parameters = json.dumps(data["parameters"])
    if "inputs" in data:
        _replace_inputs(skill.id, data["inputs"])
    db.session.commit()
    return jsonify(skill.to_dict())


@bp.post("/<int:skill_id>/status")
@require_global_role("admin", "skill_manager")
def change_status(skill_id):
    """Публікація / зняття з публікації."""
    skill = Skill.query.get_or_404(skill_id)
    data = request.get_json(silent=True) or {}
    new_status = data.get("status")
    if new_status not in VALID_STATUS:
        raise ApiError("Статус має бути 'draft' (не опублікована) або "
                       "'published' (опублікована)", 400, "validation_error")
    old_status = skill.status
    skill.status = new_status
    if new_status == "published" and skill.published_at is None:
        skill.published_at = datetime.utcnow()
    if old_status != new_status:
        user = current_user()
        db.session.add(ReviewLog(
            item_type="skill", item_id=skill.id, item_name=skill.name,
            actor_user_id=user.id if user else None,
            actor_name=(user.full_name or user.username) if user else None,
            from_status=old_status, to_status=new_status,
        ))
    db.session.commit()
    return jsonify(skill.to_dict())


def _require_individual_access(user):
    """Самоактивація дозволена, якщо ввімкнено індивідуальний доступ (або Admin)."""
    if not skill_service.individual_access_enabled() and not user.has_global_role("admin"):
        raise ApiError("Індивідуальний доступ до навичок вимкнено адміністратором",
                       403, "individual_access_disabled")


@bp.post("/<int:skill_id>/activate")
@require_auth
def activate(skill_id):
    """Самостійна активація (встановлення) опублікованої навички для себе."""
    user = current_user()
    _require_individual_access(user)
    count = skill_service.self_activate(user.id, skill_id)
    return jsonify({"message": "Навичку встановлено", "activations_count": count})


@bp.post("/<int:skill_id>/deactivate")
@require_auth
def deactivate(skill_id):
    """Самостійне вилучення навички (доступ за групою при цьому зберігається)."""
    user = current_user()
    _require_individual_access(user)
    count = skill_service.self_deactivate(user.id, skill_id)
    return jsonify({"message": "Навичку вилучено", "activations_count": count})


# ----------------------------- Зворотний зв'язок -----------------------------

@bp.post("/<int:skill_id>/feedback")
@require_auth
def submit_feedback(skill_id):
    """Користувач надсилає повідомлення (фідбек) щодо навички."""
    skill = Skill.query.get_or_404(skill_id)
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    rating = data.get("rating")
    if rating not in (None, ""):
        try:
            rating = int(rating)
        except (TypeError, ValueError):
            raise ApiError("Оцінка має бути числом від 1 до 5", 400, "validation_error")
        if not 1 <= rating <= 5:
            raise ApiError("Оцінка має бути числом від 1 до 5", 400, "validation_error")
    else:
        rating = None
    if not message and rating is None:
        raise ApiError("Повідомлення не може бути порожнім", 400, "validation_error")
    if len(message) > 5000:
        raise ApiError("Повідомлення завелике (максимум 5000 символів)",
                       400, "validation_error")
    user = current_user()
    fb = SkillFeedback(
        item_type="skill", skill_id=skill.id, skill_name=skill.name,
        skill_version=skill.version, rating=rating,
        user_id=user.id, username=user.full_name or user.username,
        message=message or "(без коментаря)",
    )
    db.session.add(fb)
    db.session.commit()
    return jsonify({"message": "Дякуємо! Повідомлення надіслано.", "id": fb.id}), 201


@bp.get("/feedback")
@require_global_role("admin", "skill_manager")
def list_feedback():
    """Перелік фідбеку. `?filter=new` — лише непрочитані."""
    query = SkillFeedback.query
    if request.args.get("filter") == "new":
        query = query.filter_by(is_read=False)
    items = query.order_by(SkillFeedback.created_at.desc()).all()
    unread = SkillFeedback.query.filter_by(is_read=False).count()
    return jsonify({"items": [f.to_dict() for f in items], "unread": unread})


@bp.get("/feedback/unread-count")
@require_global_role("admin", "skill_manager")
def feedback_unread_count():
    return jsonify({"unread": SkillFeedback.query.filter_by(is_read=False).count()})


@bp.post("/feedback/<int:feedback_id>/read")
@require_global_role("admin", "skill_manager")
def mark_feedback_read(feedback_id):
    fb = SkillFeedback.query.get_or_404(feedback_id)
    fb.is_read = True
    db.session.commit()
    return jsonify(fb.to_dict())


@bp.post("/feedback/read-all")
@require_global_role("admin", "skill_manager")
def mark_all_feedback_read():
    SkillFeedback.query.filter_by(is_read=False).update({SkillFeedback.is_read: True})
    db.session.commit()
    return jsonify({"message": "Усі повідомлення позначено прочитаними"})


@bp.post("/<int:skill_id>/run")
@require_auth
def run(skill_id):
    data = request.get_json(silent=True) or {}
    result = chat_service.run_skill(
        current_user(), skill_id,
        inputs=data.get("inputs", {}),
        session_id=data.get("session_id"),
    )
    return jsonify(result)


def _read_upload():
    file = request.files.get("file")
    if file is None or not file.filename:
        raise ApiError("Файл не надіслано (поле 'file')", 400, "validation_error")
    if not file.filename.lower().endswith(ALLOWED_PACKAGE_EXT):
        raise ApiError("Дозволені лише архіви .zip або .skill", 400, "validation_error")
    return file.filename, file.read()


def _apply_meta(skill, meta, filename, file_bytes):
    """Записує атрибути з пакета в навичку та зберігає архів."""
    skill.name = meta["name"]
    skill.description = meta["description"]
    skill.version = meta["version"]
    skill.runtime = meta["runtime"]
    skill.entrypoint = meta["entrypoint"]
    skill.prompt_template = meta.get("instructions")
    skill.package_filename = filename
    if meta.get("author"):
        skill.author = meta["author"]
    if meta.get("category"):
        skill.category = meta["category"]
    skill.package_path = package_service.store_package(skill.id, file_bytes, filename)
    _replace_inputs(skill.id, meta["inputs"])


@bp.post("/upload")
@require_global_role("admin", "skill_manager")
def upload_package():
    """Завантаження НОВОЇ навички (архів зі skill.md). Статус — «Не опублікована»."""
    filename, file_bytes = _read_upload()
    meta = package_service.parse_package(file_bytes)
    user = current_user()

    skill = Skill(
        name=meta["name"],
        description=meta["description"],
        author=meta.get("author") or user.full_name or user.username,
        category=meta.get("category") or DEFAULT_CATEGORY,
        skill_kind="package",
        status="draft",
        created_by=user.id,
    )
    db.session.add(skill)
    db.session.flush()
    _apply_meta(skill, meta, filename, file_bytes)
    db.session.commit()
    return jsonify(skill.to_dict()), 201


@bp.post("/<int:skill_id>/upload")
@require_global_role("admin", "skill_manager")
def upload_new_version(skill_id):
    """Завантаження НОВОЇ ВЕРСІЇ наявної навички (в її контексті).

    Ідентичність, активації та зв'язки з групами/користувачами зберігаються —
    оновлюються пакет і атрибути.
    """
    skill = Skill.query.get_or_404(skill_id)
    filename, file_bytes = _read_upload()
    meta = package_service.parse_package(file_bytes)
    skill.skill_kind = "package"
    _apply_meta(skill, meta, filename, file_bytes)
    db.session.commit()
    return jsonify(skill.to_dict())


@bp.get("/<int:skill_id>/download")
@require_global_role("admin", "skill_manager")
def download_package(skill_id):
    """Завантаження (скачування) пакета навички."""
    skill = Skill.query.get_or_404(skill_id)
    if not skill.package_path or not os.path.exists(skill.package_path):
        raise ApiError("Пакет навички відсутній", 404, "package_missing")
    safe = re.sub(r"[^\w.\-]+", "_", f"{skill.name}-{skill.version}", flags=re.UNICODE)
    return send_file(skill.package_path, as_attachment=True,
                     download_name=f"{safe}.zip")


@bp.post("/<int:skill_id>/icon")
@require_global_role("admin", "skill_manager")
def upload_icon(skill_id):
    """Завантаження PNG-іконки навички (замінює наявну)."""
    skill = Skill.query.get_or_404(skill_id)
    file = request.files.get("file")
    if file is None or not file.filename:
        raise ApiError("Файл не надіслано (поле 'file')", 400, "validation_error")
    skill.icon_path = package_service.store_icon(skill.id, file.read())
    skill.updated_at = datetime.utcnow()  # оновлюємо версію для скидання кешу іконки
    db.session.commit()
    return jsonify(skill.to_dict())


@bp.delete("/<int:skill_id>/icon")
@require_global_role("admin", "skill_manager")
def delete_icon(skill_id):
    """Видалення завантаженої іконки — навичка повертається до стандартної."""
    skill = Skill.query.get_or_404(skill_id)
    package_service.delete_icon(skill)
    skill.icon_path = None
    skill.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(skill.to_dict())


@bp.get("/<int:skill_id>/icon")
def get_icon(skill_id):
    """Віддає PNG-іконку навички. Публічний маршрут (для тегів <img>)."""
    skill = Skill.query.get_or_404(skill_id)
    if not skill.icon_path or not os.path.exists(skill.icon_path):
        raise ApiError("Іконку не задано", 404, "icon_missing")
    return send_file(skill.icon_path, mimetype="image/png")


@bp.get("/<int:skill_id>/files")
@require_auth
def list_files(skill_id):
    """Перелік файлів у пакеті навички."""
    skill = Skill.query.get_or_404(skill_id)
    if skill.skill_kind != "package":
        raise ApiError("Навичка не є пакетом", 400, "not_a_package")
    return jsonify(package_service.list_package_files(skill))


@bp.delete("/<int:skill_id>")
@require_global_role("admin", "skill_manager")
def delete_skill(skill_id):
    """Видалення навички + усіх її зв'язків з групами й користувачами.

    Після повторного встановлення навичку доведеться активувати заново.
    Історія чатів/токенів зберігається (посилання на навичку знімається).
    """
    skill = Skill.query.get_or_404(skill_id)
    if skill.skill_kind == "package":
        package_service.delete_package_file(skill)
    package_service.delete_icon(skill)

    UserSkill.query.filter_by(skill_id=skill_id).delete(synchronize_session=False)
    GroupSkill.query.filter_by(skill_id=skill_id).delete(synchronize_session=False)
    CatalogFavorite.query.filter_by(item_type="skill", item_id=skill_id)\
        .delete(synchronize_session=False)
    # Фідбек зберігаємо (знімок назви/версії), лише відв'язуємо від навички.
    for model in (ChatSession, ChatMessage, TokenUsageLog, UserFile, SkillFeedback):
        model.query.filter_by(skill_id=skill_id).update(
            {model.skill_id: None}, synchronize_session=False)

    db.session.delete(skill)
    db.session.commit()
    return jsonify({"message": "Навичку та всі її зв'язки видалено"})


def _replace_inputs(skill_id, inputs):
    SkillInput.query.filter_by(skill_id=skill_id).delete()
    for idx, spec in enumerate(inputs or []):
        name = (spec.get("name") or "").strip()
        if not name:
            continue
        db.session.add(SkillInput(
            skill_id=skill_id,
            name=name,
            label=spec.get("label"),
            data_type=spec.get("data_type", "string"),
            is_required=spec.get("is_required", True),
            default_value=spec.get("default_value"),
            description=spec.get("description"),
            position=spec.get("position", idx),
        ))
