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
    Skill, SkillInput, UserSkill, GroupSkill,
    ChatSession, ChatMessage, TokenUsageLog, UserFile,
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
    """Навички, активні для поточного користувача (ефективний доступ)."""
    user = current_user()
    rows = UserSkill.query.filter_by(user_id=user.id, is_active=True).all()
    skill_ids = [r.skill_id for r in rows]
    skills = Skill.query.filter(Skill.id.in_(skill_ids)).all()
    return jsonify([s.to_dict() for s in skills])


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
    for field in ("name", "description", "version", "author", "category",
                  "prompt_template"):
        if field in data:
            value = data[field]
            if field in ("name", "version") and not (value or "").strip():
                raise ApiError("Назва та версія не можуть бути порожніми",
                               400, "validation_error")
            setattr(skill, field, value)
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
    skill.status = new_status
    if new_status == "published" and skill.published_at is None:
        skill.published_at = datetime.utcnow()
    db.session.commit()
    return jsonify(skill.to_dict())


@bp.post("/<int:skill_id>/activate")
@require_auth
def activate(skill_id):
    """Самостійна активація опублікованої навички для себе."""
    count = skill_service.self_activate(current_user().id, skill_id)
    return jsonify({"message": "Навичку активовано", "activations_count": count})


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

    UserSkill.query.filter_by(skill_id=skill_id).delete(synchronize_session=False)
    GroupSkill.query.filter_by(skill_id=skill_id).delete(synchronize_session=False)
    for model in (ChatSession, ChatMessage, TokenUsageLog, UserFile):
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
