"""Навички: хостинг пакетів у каталозі.

Навичку створює завантаження пакета (.zip / .skill зі skill.md): без
контексту — нова навичка, у контексті наявної — її нова версія.

Попередня модерація, як і для матеріалів (див. moderation_service):
будь-який користувач завантажує навичку й подає її на розгляд; у каталог
вона потрапляє після схвалення менеджером свого розділу. Зміни автора до
опублікованої навички (атрибути чи новий пакет) чекають схвалення, не
ховаючи опубліковану версію. Менеджер розділу може з навичками своїх
розділів усе — автор дізнається про його рішення. Опубліковані навички
бачать і завантажують усі.
"""
import os
import re
from datetime import datetime
from flask import Blueprint, request, jsonify, send_file
from app.extensions import db
from app.core import access
from app.core.permissions import require_auth, require_moderator
from app.core.security import current_user
from app.core.errors import ApiError
from app.models import Skill, SkillFeedback, CatalogFavorite, Notification, User
from app.services import (package_service, review_service, comment_service,
                          moderation_service)

bp = Blueprint("skills", __name__)

# Статуси навички: 'draft' = «Чернетка», 'review' = «На розгляді»,
# 'published' = «Опублікована». published від того, хто не модерує розділ,
# означає подання на розгляд.
VALID_STATUS = {"draft", "review", "published"}
PUBLIC = ("published",)
ALLOWED_PACKAGE_EXT = (".zip", ".skill")
DEFAULT_CATEGORY = "Загальне"
EDITABLE_FIELDS = ("name", "description", "version", "author", "category", "owner",
                   "input_spec", "output_spec", "starter_prompt")
# Що може прийти в пропозиції змін (крім нового пакета).
CHANGE_FIELDS = EDITABLE_FIELDS + ("instructions", "section_id", "folder_id")


def _is_author(skill, user):
    return moderation_service.is_author(skill, user)


def _visible_skill(skill_id):
    """Навичка, яку поточний користувач має право бачити.

    Неопубліковану бачать лише автор і менеджери розділу: для решти її не
    існує (404, а не 403 — щоб не підтверджувати сам факт чернетки).
    """
    skill = Skill.query.get_or_404(skill_id)
    if not moderation_service.can_see(skill, current_user(), PUBLIC):
        raise ApiError("Навичку не знайдено", 404, "not_found")
    return skill


def _editable_skill(skill_id):
    """Навичка, якою поточний користувач може керувати: автор або менеджер розділу."""
    skill = _visible_skill(skill_id)
    user = current_user()
    if not (access.can_moderate(user, skill) or _is_author(skill, user)):
        raise ApiError("Керувати навичкою може її автор або менеджер розділу",
                       403, "forbidden")
    return skill


def skill_payload(skill, user):
    """Навичка для відповіді: з пропозицією змін (автору й модератору)."""
    out = skill.to_dict()
    pending = moderation_service.pending_payload(skill, user)
    if pending and "package" in pending["data"]:
        pkg = pending["data"]["package"]
        # Шлях до файлу — справа сервера; клієнту досить назви й метаданих.
        pending["data"]["package"] = {"filename": pkg.get("filename"),
                                      "meta": pkg.get("meta")}
    out["pending"] = pending
    out["can_moderate"] = access.can_moderate(user, skill)
    return out


def _publish(skill, user):
    """Позначає навичку опублікованою; власником стає автор (якщо власника
    ще не вказано), а не менеджер, що її схвалив."""
    skill.status = "published"
    if skill.published_at is None:
        skill.published_at = datetime.utcnow()
    if not (skill.owner or "").strip():
        author = db.session.get(User, skill.created_by) if skill.created_by else None
        owner = author or user
        skill.owner = owner.full_name or owner.username
    skill.review_note = None


def _apply_attrs(skill, data):
    """Описові атрибути й місце в каталозі (власне збереження або схвалення)."""
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


def _check_attrs(skill, data):
    """Перевіряє зміни на копії, не чіпаючи навичку."""
    copy = Skill(**{c.name: getattr(skill, c.name) for c in Skill.__table__.columns
                    if c.name != "id"})
    _apply_attrs(copy, data)


@bp.get("")
@require_auth
def list_skills():
    """Опубліковані — усім; менеджер розділу бачить ще й усе в своїх розділах,
    адміністратор — усе. `mine=1` — лише власні навички в будь-якому статусі."""
    user = current_user()
    query = Skill.query
    if request.args.get("mine") in ("1", "true"):
        query = query.filter_by(created_by=user.id)
    else:
        query = query.filter(moderation_service.visible_filter(Skill, user, PUBLIC))
    return jsonify([skill_payload(s, user) for s in query.order_by(Skill.id).all()])


@bp.get("/<int:skill_id>")
@require_auth
def get_skill(skill_id):
    return jsonify(skill_payload(_visible_skill(skill_id), current_user()))


@bp.patch("/<int:skill_id>")
@require_auth
def update_skill(skill_id):
    """Редагування описових атрибутів навички та її місця в каталозі.
    Опубліковану навичку автор (якщо не модерує розділ) змінює через
    пропозицію змін — див. /changes."""
    skill = _editable_skill(skill_id)
    user = current_user()
    data = {k: v for k, v in (request.get_json(silent=True) or {}).items()
            if k in CHANGE_FIELDS}
    if moderation_service.needs_pending(skill, user):
        _check_attrs(skill, data)
        moderation_service.store_pending("skill", skill, user, data)
        db.session.commit()
        return jsonify(skill_payload(skill, user))
    from app.api.catalog import require_stays_moderated
    old_section = skill.section_id
    require_stays_moderated(user, skill, data)
    _apply_attrs(skill, data)
    if skill.status == "review" and skill.section_id != old_section:
        moderation_service.require_section(user, skill)
        review_service.notify_review_requested("skill", skill, user)
    db.session.commit()
    return jsonify(skill_payload(skill, user))


@bp.post("/<int:skill_id>/status")
@require_auth
def change_status(skill_id):
    """Подання на розгляд, схвалення, повернення, зняття з публікації.

    Автор подає навичку (`review` або `published`) чи забирає її (`draft`);
    менеджер розділу схвалює (`published`) або повертає (`draft`) з причиною
    (`note`) — автор отримає її сповіщенням.
    """
    skill = _editable_skill(skill_id)
    user = current_user()
    data = request.get_json(silent=True) or {}
    new_status = data.get("status")
    if new_status not in VALID_STATUS:
        raise ApiError("Статус має бути 'draft' (чернетка), 'review' (на розгляд) або "
                       "'published' (опублікована)", 400, "validation_error")
    note = moderation_service.clean_note(data)
    moderator = access.can_moderate(user, skill)
    author = _is_author(skill, user)
    old_status = skill.status
    if old_status == new_status:
        return jsonify(skill_payload(skill, user))
    if new_status in ("review", "published") and not moderator:
        moderation_service.submit("skill", skill, user, new_status,
                                  lambda item: _publish(item, user))
    elif new_status == "review":
        moderation_service.submit("skill", skill, user, "review", lambda item: None)
    elif new_status == "published":
        _publish(skill, user)
        moderation_service.log("skill", skill, user, old_status, "published", note)
        if not author:
            action = "approved" if old_status == "review" else "published"
            review_service.notify_moderated("skill", skill, user, action, note)
    else:
        if old_status == "published" and skill.pending_data:
            _merge_pending(skill)
        skill.status = new_status
        if moderator and not author:
            skill.review_note = note
            action = "returned" if old_status == "review" else "unpublished"
            review_service.notify_moderated("skill", skill, user, action, note)
        moderation_service.log("skill", skill, user, old_status, new_status, note)
    db.session.commit()
    return jsonify(skill_payload(skill, user))


def _apply_pending(skill, changes):
    """Застосовує пропозицію змін: атрибути й, якщо є, новий пакет."""
    pkg = changes.get("package")
    attrs = {k: v for k, v in changes.items() if k != "package"}
    if pkg:
        if pkg.get("path") and os.path.exists(pkg["path"]):
            skill.package_path = package_service.promote_package(skill.id, pkg["path"])
        _apply_meta_fields(skill, pkg.get("meta") or {}, pkg.get("filename"))
    _apply_attrs(skill, attrs)


def _drop_pending_package(skill):
    changes = moderation_service.get_pending(skill) or {}
    package_service.remove_file((changes.get("package") or {}).get("path"))


def _merge_pending(skill):
    """Навичка повертається в чернетки — неcхвалені зміни автора стають чернеткою."""
    changes = moderation_service.get_pending(skill)
    moderation_service.clear_pending(skill)
    if changes:
        _apply_pending(skill, changes)


@bp.post("/<int:skill_id>/changes")
@require_auth
def skill_changes(skill_id):
    """Рішення щодо пропозиції змін до опублікованої навички: `approve` і
    `reject` (менеджер розділу, з причиною `note`), `withdraw` (автор)."""
    skill = _editable_skill(skill_id)
    user = current_user()
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    if action not in ("approve", "reject", "withdraw"):
        raise ApiError("action має бути approve, reject або withdraw",
                       400, "validation_error")
    if action != "withdraw" and not access.can_moderate(user, skill):
        raise ApiError("Рішення щодо змін ухвалює менеджер розділу", 403, "forbidden")
    changes = moderation_service.get_pending(skill)
    if changes is None:
        raise ApiError("Змін, що чекають схвалення, немає", 400, "no_pending")
    note = moderation_service.clean_note(data)
    if action == "approve":
        moderation_service.clear_pending(skill)
        _apply_pending(skill, changes)
        moderation_service.log("skill", skill, user, skill.status, skill.status,
                               note or "Зміни схвалено")
        review_service.notify_moderated("skill", skill, user, "changes_approved", note)
    else:
        _drop_pending_package(skill)
        moderation_service.clear_pending(skill)
        moderation_service.log("skill", skill, user, skill.status, skill.status,
                               ("Зміни відхилено" + (f": {note}" if note else ""))
                               if action == "reject" else "Автор забрав зміни")
        if action == "reject":
            review_service.notify_moderated("skill", skill, user, "changes_rejected", note)
    db.session.commit()
    return jsonify(skill_payload(skill, user))


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
@require_moderator
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
@require_moderator
def feedback_unread_count():
    return jsonify({"unread": SkillFeedback.query.filter_by(is_read=False).count()})


@bp.post("/feedback/<int:feedback_id>/read")
@require_moderator
def mark_feedback_read(feedback_id):
    fb = SkillFeedback.query.get_or_404(feedback_id)
    fb.is_read = True
    db.session.commit()
    return jsonify(fb.to_dict())


@bp.post("/feedback/read-all")
@require_moderator
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


def _apply_meta_fields(skill, meta, filename):
    """Записує в навичку атрибути з пакета."""
    if meta.get("name"):
        skill.name = meta["name"]
    if meta.get("description"):
        skill.description = meta["description"]
    if meta.get("version"):
        skill.version = meta["version"]
    skill.prompt_template = meta.get("instructions")
    if filename:
        skill.package_filename = filename
    skill.skill_kind = "package"
    if meta.get("author"):
        skill.author = meta["author"]
    if meta.get("category"):
        skill.category = meta["category"]


def _apply_meta(skill, meta, filename, file_bytes):
    """Атрибути з пакета й сам архів."""
    _apply_meta_fields(skill, meta, filename)
    skill.package_path = package_service.store_package(skill.id, file_bytes)


@bp.post("/upload")
@require_auth
def upload_package():
    """Нова навичка з архіву зі skill.md — від будь-якого користувача.

    Поля форми: `status` — `draft` (типово), `review` чи `published`
    (подання; публікує одразу лише той, хто модерує розділ); `section_id`,
    `folder_id` — місце в каталозі, без розділу подати навичку не можна.
    """
    status = request.form.get("status") or "draft"
    if status not in VALID_STATUS:
        raise ApiError("Статус має бути 'draft', 'review' або 'published'",
                       400, "validation_error")
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
    placement = {k: request.form.get(k) for k in ("section_id", "folder_id")
                 if request.form.get(k)}
    if placement:
        from app.api.catalog import _apply_placement
        _apply_placement(skill, placement)
    if status != "draft":
        moderation_service.require_section(user, skill)
    db.session.add(skill)
    db.session.flush()
    _apply_meta(skill, meta, filename, file_bytes)
    if status != "draft":
        moderation_service.submit("skill", skill, user, status,
                                  lambda item: _publish(item, user))
    db.session.commit()
    return jsonify(skill_payload(skill, user)), 201


@bp.post("/<int:skill_id>/upload")
@require_auth
def upload_new_version(skill_id):
    """Нова версія наявної навички: ідентичність, розташування в каталозі,
    обране й відгуки зберігаються — оновлюються пакет і атрибути зі skill.md.
    Нова версія опублікованої навички від автора чекає схвалення."""
    skill = _editable_skill(skill_id)
    user = current_user()
    filename, file_bytes = _read_upload()
    meta = package_service.parse_package(file_bytes)
    if moderation_service.needs_pending(skill, user):
        _drop_pending_package(skill)
        path = package_service.store_package(skill.id, file_bytes, pending=True)
        moderation_service.store_pending("skill", skill, user, {
            "package": {"filename": filename, "meta": meta, "path": path}})
    else:
        _apply_meta(skill, meta, filename, file_bytes)
    db.session.commit()
    return jsonify(skill_payload(skill, user))


def _pending_package_path(skill):
    """Нова версія на розгляді — її бачать автор і менеджер розділу."""
    data = moderation_service.pending_payload(skill, current_user())
    if data is None:
        return None
    path = (moderation_service.get_pending(skill).get("package") or {}).get("path")
    return path if path and os.path.exists(path) else None


@bp.get("/<int:skill_id>/download")
@require_auth
def download_package(skill_id):
    """Завантаження пакета навички.

    Опубліковану навичку завантажує будь-хто; кожне таке завантаження
    рахується — це і є популярність навички в каталозі. Чернетку бачать і
    завантажують лише менеджери, і в лічильник ці перевірки не йдуть.
    """
    skill = _visible_skill(skill_id)
    if request.args.get("pending") in ("1", "true"):
        path = _pending_package_path(skill)
        if path is None:
            raise ApiError("Нової версії на розгляді немає", 404, "package_missing")
        safe = re.sub(r"[^\w.\-]+", "_", f"{skill.name}-нова-версія", flags=re.UNICODE)
        return send_file(path, as_attachment=True, download_name=f"{safe}.zip")
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
    """Перелік файлів у пакеті навички — щоб видно було, що завантажуєш.
    `?pending=1` — файли нової версії, що чекає схвалення."""
    skill = _visible_skill(skill_id)
    if request.args.get("pending") in ("1", "true"):
        path = _pending_package_path(skill)
        return jsonify(package_service.list_package_files(skill, path) if path else [])
    return jsonify(package_service.list_package_files(skill))


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
    return jsonify(skill_payload(skill, current_user()))


@bp.delete("/<int:skill_id>/icon")
@require_auth
def delete_icon(skill_id):
    """Видалення завантаженої іконки — навичка повертається до стандартної."""
    skill = _editable_skill(skill_id)
    package_service.delete_icon(skill)
    skill.icon_path = None
    skill.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(skill_payload(skill, current_user()))


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
    note = moderation_service.clean_note(request.get_json(silent=True) or {})
    package_service.delete_package_file(skill)
    _drop_pending_package(skill)
    package_service.delete_icon(skill)
    CatalogFavorite.query.filter_by(item_type="skill", item_id=skill_id)\
        .delete(synchronize_session=False)
    SkillFeedback.query.filter_by(skill_id=skill_id).update(
        {SkillFeedback.skill_id: None}, synchronize_session=False)
    # SQLite без перевірки зовнішніх ключів каскад сам не виконає.
    Notification.query.filter_by(skill_id=skill_id).delete(synchronize_session=False)
    if not _is_author(skill, user):
        review_service.notify_moderated("skill", skill, user, "deleted", note, link=False)
    db.session.delete(skill)
    db.session.commit()
    return jsonify({"message": "Навичку видалено"})
