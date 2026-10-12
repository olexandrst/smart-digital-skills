"""Навички: хостинг пакетів у каталозі.

Навичку створює завантаження пакета (.zip / .skill зі skill.md): без
контексту — нова навичка, у контексті наявної — її нова версія.

Пост-модерація, як і для матеріалів: будь-який користувач публікує навичку
сам і керує своєю (атрибути, нова версія, іконка, публікація, видалення);
менеджери каталогу отримують сповіщення про публікації користувачів і
можуть усе з будь-якою навичкою — автор дізнається про їхнє рішення.
Опубліковані навички бачать і завантажують усі.
"""
import os
import re
from datetime import datetime
from flask import Blueprint, request, jsonify, send_file
from app.extensions import db
from app.core.permissions import require_auth, require_global_role
from app.core.security import current_user
from app.core.errors import ApiError
from app.models import Skill, SkillFeedback, CatalogFavorite, ReviewLog, Notification
from app.services import package_service, review_service, comment_service

bp = Blueprint("skills", __name__)

# Статуси навички: 'draft' = «Не опублікована», 'published' = «Опублікована».
VALID_STATUS = {"draft", "published"}
ALLOWED_PACKAGE_EXT = (".zip", ".skill")
DEFAULT_CATEGORY = "Загальне"
EDITABLE_FIELDS = ("name", "description", "version", "author", "category", "owner",
                   "input_spec", "output_spec", "starter_prompt")


def _is_manager(user):
    return user.has_global_role("admin") or user.has_global_role("skill_manager")


def _is_author(skill, user):
    return skill.created_by is not None and skill.created_by == user.id


def _visible_skill(skill_id):
    """Навичка, яку поточний користувач має право бачити.

    Неопубліковану бачать лише автор і менеджери: для решти її не існує
    (404, а не 403 — щоб не підтверджувати сам факт чернетки).
    """
    skill = Skill.query.get_or_404(skill_id)
    user = current_user()
    if skill.status != "published" and not (_is_manager(user) or _is_author(skill, user)):
        raise ApiError("Навичку не знайдено", 404, "not_found")
    return skill


def _editable_skill(skill_id):
    """Навичка, якою поточний користувач може керувати: автор або менеджер."""
    skill = _visible_skill(skill_id)
    user = current_user()
    if not (_is_manager(user) or _is_author(skill, user)):
        raise ApiError("Керувати навичкою може її автор або менеджер каталогу",
                       403, "forbidden")
    return skill


def _log(skill, user, from_status, to_status, note=None):
    db.session.add(ReviewLog(
        item_type="skill", item_id=skill.id, item_name=skill.name,
        actor_user_id=user.id, actor_name=user.full_name or user.username,
        from_status=from_status, to_status=to_status, note=note,
    ))


def _clean_note(data):
    note = " ".join((data.get("note") or "").split()) or None
    if note and len(note) > 2000:
        raise ApiError("Причина завелика (максимум 2000 символів)", 400, "validation_error")
    return note


def _publish(skill, user):
    """Позначає навичку опублікованою; користувач-автор стає відповідальним."""
    skill.status = "published"
    if skill.published_at is None:
        skill.published_at = datetime.utcnow()
    if not _is_manager(user) and not (skill.owner or "").strip():
        skill.owner = user.full_name or user.username


@bp.get("")
@require_auth
def list_skills():
    """Менеджери бачать усі навички; решта — опубліковані. `mine=1` — лише
    власні навички користувача в будь-якому статусі."""
    user = current_user()
    query = Skill.query
    if request.args.get("mine") in ("1", "true"):
        query = query.filter_by(created_by=user.id)
    elif not _is_manager(user):
        query = query.filter_by(status="published")
    return jsonify([s.to_dict() for s in query.order_by(Skill.id).all()])


@bp.get("/<int:skill_id>")
@require_auth
def get_skill(skill_id):
    return jsonify(_visible_skill(skill_id).to_dict())


@bp.patch("/<int:skill_id>")
@require_auth
def update_skill(skill_id):
    """Редагування описових атрибутів навички та її місця в каталозі."""
    skill = _editable_skill(skill_id)
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
    if skill.status == "published":
        review_service.notify_published("skill", skill, current_user(), updated=True)
    db.session.commit()
    return jsonify(skill.to_dict())


@bp.post("/<int:skill_id>/status")
@require_auth
def change_status(skill_id):
    """Публікація / зняття з публікації — автор або менеджер.

    Менеджер, змінюючи стан чужої навички, може вказати причину (`note`) —
    автор отримає її сповіщенням. Публікацію користувача бачать менеджери.
    """
    skill = _editable_skill(skill_id)
    user = current_user()
    data = request.get_json(silent=True) or {}
    new_status = data.get("status")
    if new_status not in VALID_STATUS:
        raise ApiError("Статус має бути 'draft' (не опублікована) або "
                       "'published' (опублікована)", 400, "validation_error")
    note = _clean_note(data)
    old_status = skill.status
    if old_status == new_status:
        return jsonify(skill.to_dict())
    if new_status == "published":
        _publish(skill, user)
    else:
        skill.status = new_status
    _log(skill, user, old_status, new_status, note)
    if _is_manager(user):
        action = "published" if new_status == "published" else "unpublished"
        review_service.notify_moderated("skill", skill, user, action, note)
    elif new_status == "published":
        review_service.notify_published("skill", skill, user)
    db.session.commit()
    return jsonify(skill.to_dict())


# ----------------------------- Зворотний зв'язок -----------------------------

@bp.get("/<int:skill_id>/feedback")
@require_auth
def skill_comments(skill_id):
    """Публічна стрічка коментарів навички, середня оцінка й власна оцінка."""
    skill = _visible_skill(skill_id)
    return jsonify(comment_service.thread("skill", skill.id, current_user()))


@bp.post("/<int:skill_id>/feedback")
@require_auth
def submit_feedback(skill_id):
    """Коментар та/або оцінка навички. Коментар бачать усі; автор навички
    отримує сповіщення."""
    skill = _visible_skill(skill_id)
    user = current_user()
    data = request.get_json(silent=True) or {}
    fb = comment_service.add("skill", skill, user, data.get("message"), data.get("rating"))
    db.session.commit()
    return jsonify(comment_service.public_dict(fb, user)), 201


@bp.get("/feedback")
@require_global_role("admin", "skill_manager")
def list_feedback():
    """Стрічка для команди: усі коментарі й оцінки (матеріали й навички),
    разом зі старими приватними відгуками. `?filter=new` — лише непрочитані."""
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
@require_auth
def upload_package():
    """Нова навичка з архіву зі skill.md — від будь-якого користувача.

    Поле форми `status`: `published` — одразу в каталог (менеджери отримають
    сповіщення, якщо публікує користувач), `draft` (типово) — чернетка.
    """
    status = request.form.get("status") or "draft"
    if status not in VALID_STATUS:
        raise ApiError("Статус має бути 'draft' або 'published'", 400, "validation_error")
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
    if status == "published":
        _publish(skill, user)
        _log(skill, user, None, "published")
        review_service.notify_published("skill", skill, user)
    db.session.commit()
    return jsonify(skill.to_dict()), 201


@bp.post("/<int:skill_id>/upload")
@require_auth
def upload_new_version(skill_id):
    """Нова версія наявної навички: ідентичність, розташування в каталозі,
    обране й відгуки зберігаються — оновлюються пакет і атрибути зі skill.md."""
    skill = _editable_skill(skill_id)
    filename, file_bytes = _read_upload()
    meta = package_service.parse_package(file_bytes)
    _apply_meta(skill, meta, filename, file_bytes)
    if skill.status == "published":
        review_service.notify_published("skill", skill, current_user(), updated=True)
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
@require_auth
def upload_icon(skill_id):
    """Завантаження PNG-іконки навички (замінює наявну)."""
    skill = _editable_skill(skill_id)
    file = request.files.get("file")
    if file is None or not file.filename:
        raise ApiError("Файл не надіслано (поле 'file')", 400, "validation_error")
    skill.icon_path = package_service.store_icon(skill.id, file.read())
    skill.updated_at = datetime.utcnow()  # оновлюємо версію для скидання кешу іконки
    db.session.commit()
    return jsonify(skill.to_dict())


@bp.delete("/<int:skill_id>/icon")
@require_auth
def delete_icon(skill_id):
    """Видалення завантаженої іконки — навичка повертається до стандартної."""
    skill = _editable_skill(skill_id)
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
@require_auth
def delete_skill(skill_id):
    """Видалення навички (автор або менеджер) разом із пакетом, іконкою,
    позначками «Обране» та сповіщеннями про неї.

    Коментарі зберігаються: вони тримають знімок назви й версії, тож лише
    відв'язуються від навички. Якщо видаляє менеджер чужу навичку, автор
    отримує сповіщення з причиною (`note`).
    """
    skill = _editable_skill(skill_id)
    user = current_user()
    note = _clean_note(request.get_json(silent=True) or {})
    package_service.delete_package_file(skill)
    package_service.delete_icon(skill)
    CatalogFavorite.query.filter_by(item_type="skill", item_id=skill_id)\
        .delete(synchronize_session=False)
    SkillFeedback.query.filter_by(skill_id=skill_id).update(
        {SkillFeedback.skill_id: None}, synchronize_session=False)
    # SQLite без перевірки зовнішніх ключів каскад сам не виконає.
    Notification.query.filter_by(skill_id=skill_id).delete(synchronize_session=False)
    if _is_manager(user):
        review_service.notify_moderated("skill", skill, user, "deleted", note, link=False)
    db.session.delete(skill)
    db.session.commit()
    return jsonify({"message": "Навичку видалено"})
