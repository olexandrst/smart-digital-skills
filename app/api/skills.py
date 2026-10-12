"""Навички: хостинг пакетів у каталозі.

Навичку створює лише завантаження пакета (.zip / .skill зі skill.md):
без контексту — нова навичка, у контексті наявної — її нова версія.
Admin / Skill Manager керують атрибутами, публікацією та іконкою; будь-хто
бачить опубліковані навички й завантажує їхні пакети.
"""
import os
import re
from datetime import datetime
from flask import Blueprint, request, jsonify, send_file
from app.extensions import db
from app.core.permissions import require_auth, require_global_role
from app.core.security import current_user
from app.core.errors import ApiError
from app.models import Skill, SkillFeedback, CatalogFavorite, ReviewLog
from app.services import package_service

bp = Blueprint("skills", __name__)

# Статуси навички: 'draft' = «Не опублікована», 'published' = «Опублікована».
VALID_STATUS = {"draft", "published"}
ALLOWED_PACKAGE_EXT = (".zip", ".skill")
DEFAULT_CATEGORY = "Загальне"
EDITABLE_FIELDS = ("name", "description", "version", "author", "category", "owner",
                   "input_spec", "output_spec", "starter_prompt")


def _is_manager(user):
    return user.has_global_role("admin") or user.has_global_role("skill_manager")


def _visible_skill(skill_id):
    """Навичка, яку поточний користувач має право бачити.

    Неопубліковану бачать лише менеджери: для решти її не існує (404, а не
    403 — щоб не підтверджувати сам факт чернетки).
    """
    skill = Skill.query.get_or_404(skill_id)
    if skill.status != "published" and not _is_manager(current_user()):
        raise ApiError("Навичку не знайдено", 404, "not_found")
    return skill


@bp.get("")
@require_auth
def list_skills():
    """Admin/Skill Manager бачать усі навички; решта — лише опубліковані."""
    query = Skill.query
    if not _is_manager(current_user()):
        query = query.filter_by(status="published")
    return jsonify([s.to_dict() for s in query.order_by(Skill.id).all()])


@bp.get("/<int:skill_id>")
@require_auth
def get_skill(skill_id):
    return jsonify(_visible_skill(skill_id).to_dict())


@bp.patch("/<int:skill_id>")
@require_global_role("admin", "skill_manager")
def update_skill(skill_id):
    """Редагування описових атрибутів навички та її місця в каталозі."""
    skill = Skill.query.get_or_404(skill_id)
    data = request.get_json(silent=True) or {}
    for field in EDITABLE_FIELDS:
        if field in data:
            value = data[field]
            if field in ("name", "version") and not (value or "").strip():
                raise ApiError("Назва та версія не можуть бути порожніми",
                               400, "validation_error")
            setattr(skill, field, value)
    if "instructions" in data:
        skill.prompt_template = data["instructions"]
    if "section_id" in data or "folder_id" in data:
        # Розділ і колекція узгоджуються тим самим правилом, що й для матеріалів
        # каталогу: вибір колекції задає розділ (див. app/api/catalog.py).
        from app.api.catalog import _apply_placement
        _apply_placement(skill, data)
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


# ----------------------------- Зворотний зв'язок -----------------------------

@bp.post("/<int:skill_id>/feedback")
@require_auth
def submit_feedback(skill_id):
    """Користувач надсилає повідомлення (фідбек) щодо навички."""
    skill = _visible_skill(skill_id)
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


# ----------------------------- Пакет навички -----------------------------

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
    skill.prompt_template = meta.get("instructions")
    skill.package_filename = filename
    skill.skill_kind = "package"
    if meta.get("author"):
        skill.author = meta["author"]
    if meta.get("category"):
        skill.category = meta["category"]
    skill.package_path = package_service.store_package(skill.id, file_bytes)


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
    """Нова версія наявної навички: ідентичність, розташування в каталозі,
    обране й відгуки зберігаються — оновлюються пакет і атрибути зі skill.md."""
    skill = Skill.query.get_or_404(skill_id)
    filename, file_bytes = _read_upload()
    meta = package_service.parse_package(file_bytes)
    _apply_meta(skill, meta, filename, file_bytes)
    db.session.commit()
    return jsonify(skill.to_dict())


@bp.get("/<int:skill_id>/download")
@require_auth
def download_package(skill_id):
    """Завантаження пакета навички.

    Опубліковану навичку завантажує будь-хто; кожне таке завантаження
    рахується — це і є популярність навички в каталозі. Чернетку бачать і
    завантажують лише менеджери, і в лічильник ці перевірки не йдуть.
    """
    skill = _visible_skill(skill_id)
    if not skill.package_path or not os.path.exists(skill.package_path):
        raise ApiError("Пакет навички відсутній", 404, "package_missing")
    if skill.status == "published":
        # Атомарний інкремент у базі: паралельні завантаження не губляться.
        Skill.query.filter_by(id=skill.id).update(
            {Skill.downloads_count: Skill.downloads_count + 1},
            synchronize_session=False)
        db.session.commit()
    safe = re.sub(r"[^\w.\-]+", "_", f"{skill.name}-{skill.version}", flags=re.UNICODE)
    return send_file(skill.package_path, as_attachment=True,
                     download_name=f"{safe}.zip")


@bp.get("/<int:skill_id>/files")
@require_auth
def list_files(skill_id):
    """Перелік файлів у пакеті навички — щоб видно було, що завантажуєш."""
    return jsonify(package_service.list_package_files(_visible_skill(skill_id)))


# ----------------------------- Іконка -----------------------------

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


@bp.delete("/<int:skill_id>")
@require_global_role("admin", "skill_manager")
def delete_skill(skill_id):
    """Видалення навички разом із пакетом, іконкою та позначками «Обране».

    Відгуки зберігаються: вони тримають знімок назви й версії, тож лише
    відв'язуються від навички.
    """
    skill = Skill.query.get_or_404(skill_id)
    package_service.delete_package_file(skill)
    package_service.delete_icon(skill)
    CatalogFavorite.query.filter_by(item_type="skill", item_id=skill_id)\
        .delete(synchronize_session=False)
    SkillFeedback.query.filter_by(skill_id=skill_id).update(
        {SkillFeedback.skill_id: None}, synchronize_session=False)
    db.session.delete(skill)
    db.session.commit()
    return jsonify({"message": "Навичку видалено"})
