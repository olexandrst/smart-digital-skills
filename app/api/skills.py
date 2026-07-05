"""Скіли: життєвий цикл (Admin/Skill Manager), каталог і самостійна активація."""
import json
from datetime import datetime
from flask import Blueprint, request, jsonify
from app.extensions import db
from app.core.permissions import require_auth, require_global_role
from app.core.security import current_user
from app.core.errors import ApiError
from app.models import Skill, SkillInput, UserSkill, Model
from app.services import skill_service, chat_service, package_service

bp = Blueprint("skills", __name__)

VALID_STATUS = {"draft", "testing", "published", "delisted"}
ALLOWED_PACKAGE_EXT = (".zip", ".skill")


@bp.get("")
@require_auth
def list_skills():
    """Skill-менеджери/Admin бачать усі скіли; решта — лише published-каталог."""
    user = current_user()
    if user.has_global_role("admin") or user.has_global_role("skill_manager"):
        skills = Skill.query.order_by(Skill.id).all()
    else:
        skills = Skill.query.filter_by(status="published").order_by(Skill.id).all()
    return jsonify([s.to_dict() for s in skills])


@bp.get("/mine")
@require_auth
def my_skills():
    """Скіли, активні для поточного користувача (ефективний доступ)."""
    user = current_user()
    rows = UserSkill.query.filter_by(user_id=user.id, is_active=True).all()
    skill_ids = [r.skill_id for r in rows]
    skills = Skill.query.filter(Skill.id.in_(skill_ids)).all()
    return jsonify([s.to_dict() for s in skills])


@bp.post("")
@require_global_role("admin", "skill_manager")
def create_skill():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    description = (data.get("description") or "").strip()
    model_id = data.get("model_id")
    if not name or not description:
        raise ApiError("Вкажіть назву та опис скіла", 400, "validation_error")
    if not model_id or Model.query.get(model_id) is None:
        raise ApiError("Вкажіть існуючу модель (model_id)", 400, "validation_error")

    skill = Skill(
        name=name,
        description=description,
        model_id=model_id,
        prompt_template=data.get("prompt_template", "{text}"),
        parameters=json.dumps(data.get("parameters", {})),
        version=data.get("version", "1.0.0"),
        status="draft",
        created_by=current_user().id,
    )
    db.session.add(skill)
    db.session.flush()
    _replace_inputs(skill.id, data.get("inputs", []))
    db.session.commit()
    return jsonify(skill.to_dict()), 201


@bp.get("/<int:skill_id>")
@require_auth
def get_skill(skill_id):
    skill = Skill.query.get_or_404(skill_id)
    return jsonify(skill.to_dict())


@bp.patch("/<int:skill_id>")
@require_global_role("admin", "skill_manager")
def update_skill(skill_id):
    skill = Skill.query.get_or_404(skill_id)
    data = request.get_json(silent=True) or {}
    for field in ("name", "description", "prompt_template", "version"):
        if field in data:
            setattr(skill, field, data[field])
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
    skill = Skill.query.get_or_404(skill_id)
    data = request.get_json(silent=True) or {}
    new_status = data.get("status")
    if new_status not in VALID_STATUS:
        raise ApiError("Невідомий статус", 400, "validation_error")
    skill.status = new_status
    if new_status == "published" and skill.published_at is None:
        skill.published_at = datetime.utcnow()
    db.session.commit()
    return jsonify(skill.to_dict())


@bp.post("/<int:skill_id>/activate")
@require_auth
def activate(skill_id):
    """Самостійна активація published-скіла для себе."""
    count = skill_service.self_activate(current_user().id, skill_id)
    return jsonify({"message": "Скіл активовано", "activations_count": count})


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


@bp.post("/upload")
@require_global_role("admin", "skill_manager")
def upload_package():
    """Завантаження скіла-пакета (архів .zip або .skill зі skill.md та кодом)."""
    file = request.files.get("file")
    if file is None or not file.filename:
        raise ApiError("Файл не надіслано (поле 'file')", 400, "validation_error")
    if not file.filename.lower().endswith(ALLOWED_PACKAGE_EXT):
        raise ApiError("Дозволені лише архіви .zip або .skill", 400, "validation_error")

    file_bytes = file.read()
    meta = package_service.parse_package(file_bytes)

    skill = Skill(
        name=meta["name"],
        description=meta["description"],
        skill_kind="package",
        runtime=meta["runtime"],
        entrypoint=meta["entrypoint"],
        version=meta["version"],
        prompt_template=meta.get("instructions"),
        status="draft",
        package_filename=file.filename,
        created_by=current_user().id,
    )
    db.session.add(skill)
    db.session.flush()

    skill.package_path = package_service.store_package(skill.id, file_bytes, file.filename)
    _replace_inputs(skill.id, meta["inputs"])
    db.session.commit()
    return jsonify(skill.to_dict()), 201


@bp.get("/<int:skill_id>/files")
@require_auth
def list_files(skill_id):
    """Перелік файлів у пакеті скіла."""
    skill = Skill.query.get_or_404(skill_id)
    if skill.skill_kind != "package":
        raise ApiError("Скіл не є пакетом", 400, "not_a_package")
    return jsonify(package_service.list_package_files(skill))


@bp.delete("/<int:skill_id>")
@require_global_role("admin", "skill_manager")
def delete_skill(skill_id):
    skill = Skill.query.get_or_404(skill_id)
    if skill.skill_kind == "package":
        package_service.delete_package_file(skill)
    db.session.delete(skill)
    db.session.commit()
    return jsonify({"message": "Скіл видалено"})


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
