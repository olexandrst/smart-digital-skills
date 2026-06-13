"""Скіли: життєвий цикл (Admin/Skill Manager), каталог і самостійна активація."""
import json
from datetime import datetime
from flask import Blueprint, request, jsonify
from app.extensions import db
from app.core.permissions import require_auth, require_global_role
from app.core.security import current_user
from app.core.errors import ApiError
from app.models import Skill, SkillInput, UserSkill, Model
from app.services import skill_service, chat_service

bp = Blueprint("skills", __name__)

VALID_STATUS = {"draft", "testing", "published", "delisted"}


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
