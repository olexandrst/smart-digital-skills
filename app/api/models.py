"""Реєстр моделей Azure AI Foundry. Підключати/реєструвати — лише Admin."""
import json
from flask import Blueprint, request, jsonify
from app.extensions import db
from app.core.permissions import require_auth, require_global_role
from app.core.errors import ApiError
from app.models import Model

bp = Blueprint("models", __name__)


@bp.get("")
@require_auth
def list_models():
    models = Model.query.order_by(Model.id).all()
    return jsonify([m.to_dict() for m in models])


@bp.post("")
@require_global_role("admin")
def create_model():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    model_type = data.get("model_type")
    deployment_name = (data.get("deployment_name") or "").strip()
    if not name or not deployment_name:
        raise ApiError("Вкажіть name та deployment_name", 400, "validation_error")
    if model_type not in ("llm", "cv"):
        raise ApiError("model_type має бути 'llm' або 'cv'", 400, "validation_error")

    model = Model(
        name=name,
        model_type=model_type,
        deployment_name=deployment_name,
        api_version=data.get("api_version"),
        context_window=data.get("context_window"),
        config=json.dumps(data.get("config", {})),
    )
    db.session.add(model)
    db.session.commit()
    return jsonify(model.to_dict()), 201


@bp.patch("/<int:model_id>")
@require_global_role("admin")
def update_model(model_id):
    model = Model.query.get_or_404(model_id)
    data = request.get_json(silent=True) or {}
    for field in ("name", "deployment_name", "api_version", "context_window"):
        if field in data:
            setattr(model, field, data[field])
    if "is_active" in data:
        model.is_active = bool(data["is_active"])
    db.session.commit()
    return jsonify(model.to_dict())
