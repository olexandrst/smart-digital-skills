"""Реєстр моделей (Azure OpenAI / локальні). Реєструвати — лише Admin."""
import json
from flask import Blueprint, request, jsonify
from app.extensions import db
from app.core.permissions import require_auth, require_global_role
from app.core.errors import ApiError
from app.models import Model, Skill
from app.integrations import SUPPORTED_PROVIDERS, normalize_provider
from app.integrations import discovery

bp = Blueprint("models", __name__)


def _truthy(value):
    return str(value).lower() in ("1", "true", "yes")


def _parse_price(value):
    """Ціна у USD за 1M токенів: приймає число/рядок ('10.58'); '' → None."""
    if value is None or value == "":
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        raise ApiError("Ціна має бути числом (напр. 10.58)", 400, "validation_error")
    if price < 0:
        raise ApiError("Ціна не може бути відʼємною", 400, "validation_error")
    return price


@bp.get("")
@require_auth
def list_models():
    """Список моделей. `?active=1` — лише активні (для вибору в чаті)."""
    query = Model.query
    if _truthy(request.args.get("active", "")):
        query = query.filter_by(is_active=True)
    models = query.order_by(Model.id).all()
    return jsonify([m.to_dict() for m in models])


@bp.get("/providers")
@require_auth
def list_providers():
    """Канонічні провайдери для UI (без дублювання синонімів)."""
    canonical = sorted(set(SUPPORTED_PROVIDERS.values()))
    return jsonify(canonical)


@bp.get("/available")
@require_global_role("admin")
def available_models():
    """Динамічний список моделей, доступних у провайдера.

    `?provider=` — провайдер (за замовч. azure_ai_foundry),
    `?base_url=` — для локальних/OpenAI-сумісних серверів (Ollama, LM Studio).
    Повертає {"provider", "source": "api|fallback", "models": [...]}.
    """
    provider = request.args.get("provider", "azure_ai_foundry")
    if (provider or "").lower().strip() not in SUPPORTED_PROVIDERS:
        raise ApiError("Невідомий provider", 400, "validation_error")
    base_url = (request.args.get("base_url") or "").strip() or None
    try:
        models, source = discovery.list_available_models(provider, base_url=base_url)
    except Exception as exc:  # мережеві/авторизаційні помилки провайдера
        raise ApiError(f"Не вдалося отримати список моделей: {exc}",
                       502, "discovery_failed")
    return jsonify({
        "provider": normalize_provider(provider),
        "source": source,
        "models": models,
    })


@bp.post("")
@require_global_role("admin")
def create_model():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    deployment_name = (data.get("deployment_name") or "").strip()
    provider = data.get("provider", "azure_ai_foundry")
    if not name or not deployment_name:
        raise ApiError("Вкажіть name та deployment_name", 400, "validation_error")
    if (provider or "").lower().strip() not in SUPPORTED_PROVIDERS:
        raise ApiError(
            "Невідомий provider. Дозволені: " + ", ".join(sorted(SUPPORTED_PROVIDERS)),
            400, "validation_error")

    model = Model(
        name=name,
        model_type="llm",  # тип моделей прибрано — усі llm
        provider=normalize_provider(provider),
        deployment_name=deployment_name,
        base_url=(data.get("base_url") or "").strip() or None,
        api_version=data.get("api_version"),
        context_window=data.get("context_window"),
        price_in=_parse_price(data.get("price_in")),
        price_out=_parse_price(data.get("price_out")),
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
    for field in ("name", "deployment_name", "base_url", "api_version", "context_window"):
        if field in data:
            setattr(model, field, data[field])
    for field in ("price_in", "price_out"):
        if field in data:
            setattr(model, field, _parse_price(data[field]))
    if "provider" in data:
        if (data["provider"] or "").lower().strip() not in SUPPORTED_PROVIDERS:
            raise ApiError("Невідомий provider", 400, "validation_error")
        model.provider = normalize_provider(data["provider"])
    if "is_active" in data:
        model.is_active = bool(data["is_active"])
    db.session.commit()
    return jsonify(model.to_dict())


@bp.post("/<int:model_id>/system")
@require_global_role("admin")
def set_system(model_id):
    """Призначає модель системною (для службових задач, напр. іменування чатів).

    Системна модель — лише одна; призначення скидає прапор з інших.
    """
    model = Model.query.get_or_404(model_id)
    data = request.get_json(silent=True) or {}
    make = bool(data.get("is_system", True))
    if make:
        if model.model_type != "llm":
            raise ApiError("Системною може бути лише LLM-модель", 400, "validation_error")
        Model.query.filter(Model.id != model.id).update({Model.is_system: False})
        model.is_system = True
    else:
        model.is_system = False
    db.session.commit()
    return jsonify(model.to_dict())


@bp.post("/<int:model_id>/activate")
@require_global_role("admin")
def set_active(model_id):
    """Активація/деактивація моделі. Лише активні доступні користувачам у чаті."""
    model = Model.query.get_or_404(model_id)
    data = request.get_json(silent=True) or {}
    model.is_active = bool(data.get("is_active", True))
    db.session.commit()
    return jsonify(model.to_dict())


@bp.delete("/<int:model_id>")
@require_global_role("admin")
def delete_model(model_id):
    model = Model.query.get_or_404(model_id)
    used_by = Skill.query.filter_by(model_id=model_id).count()
    if used_by:
        raise ApiError(
            f"Модель використовується у {used_by} скіл(ах). Спочатку видаліть "
            "або переналаштуйте ці скіли, або деактивуйте модель.",
            409, "model_in_use")
    db.session.delete(model)
    db.session.commit()
    return jsonify({"message": "Модель видалено"})
