"""Каталог: ресурси (промпти, інструкції, агенти, корисні посилання) та «Обране».

Навички лишаються у /api/skills — тут усе інше наповнення каталогу.
Керування ресурсами: Admin / Skill Manager. Перегляд опублікованого — усі.
"""
from datetime import datetime
from flask import Blueprint, request, jsonify
from app.extensions import db
from app.core.permissions import require_auth, require_global_role
from app.core.security import current_user
from app.core.errors import ApiError
from app.models import (
    CatalogResource, CatalogFavorite, Skill,
    RESOURCE_TYPES, LINK_TYPES, LINK_SCOPES, ACCENTS,
)

bp = Blueprint("catalog", __name__)

VALID_STATUS = {"draft", "published"}
FAVORITE_TYPES = {"skill", "resource"}
MAX_BODY = 100_000


def _is_manager(user):
    return user.has_global_role("admin") or user.has_global_role("skill_manager")


def _clean_url(value, required):
    """Валідує посилання: http(s)://… або внутрішній шлях /…"""
    url = (value or "").strip()
    if not url:
        if required:
            raise ApiError("Вкажіть посилання", 400, "validation_error")
        return None
    low = url.lower()
    if not (low.startswith("http://") or low.startswith("https://")
            or url.startswith("/")):
        raise ApiError("Посилання має починатися з http://, https:// або «/» "
                       "(внутрішній ресурс)", 400, "validation_error")
    if len(url) > 2000:
        raise ApiError("Посилання завелике", 400, "validation_error")
    return url


def _clean_tags(value):
    """Приймає список або рядок через кому; повертає нормалізований рядок."""
    if value is None:
        return None
    items = value.split(",") if isinstance(value, str) else list(value)
    tags, seen = [], set()
    for raw in items:
        tag = str(raw).strip()
        key = tag.casefold()
        if tag and key not in seen:
            seen.add(key)
            tags.append(tag)
    return ", ".join(tags[:8])


def _apply_fields(res, data, *, creating=False):
    """Переносить поля запиту в ресурс із валідацією (спільне для POST/PATCH)."""
    if creating or "resource_type" in data:
        rtype = (data.get("resource_type") or "").strip()
        if rtype not in RESOURCE_TYPES:
            raise ApiError(
                "Тип ресурсу має бути одним із: " + ", ".join(RESOURCE_TYPES),
                400, "validation_error")
        res.resource_type = rtype

    if creating or "name" in data:
        name = (data.get("name") or "").strip()
        if not name:
            raise ApiError("Назва не може бути порожньою", 400, "validation_error")
        res.name = name

    if creating or "description" in data:
        res.description = (data.get("description") or "").strip()

    for field in ("category", "author", "icon_emoji"):
        if field in data:
            value = (data.get(field) or "").strip()
            setattr(res, field, value or None)

    if "tags" in data:
        res.tags = _clean_tags(data.get("tags"))

    if "body" in data:
        body = data.get("body") or ""
        if len(body) > MAX_BODY:
            raise ApiError("Текст завеликий (максимум 100 000 символів)",
                           400, "validation_error")
        res.body = body.strip() or None

    if "accent" in data:
        accent = (data.get("accent") or "").strip()
        res.accent = accent if accent in ACCENTS else None  # None = типовий для виду

    if "is_featured" in data:
        res.is_featured = bool(data.get("is_featured"))

    if "version" in data:
        version = (data.get("version") or "").strip()
        if not version:
            raise ApiError("Версія не може бути порожньою", 400, "validation_error")
        res.version = version

    if "link_scope" in data:
        scope = (data.get("link_scope") or "").strip()
        res.link_scope = scope if scope in LINK_SCOPES else None

    # Посилання обов'язкове для агентів і корисних посилань.
    needs_url = res.resource_type in LINK_TYPES
    if "url" in data or (creating and needs_url):
        res.url = _clean_url(data.get("url"), required=needs_url)
    if needs_url and not res.url:
        raise ApiError("Вкажіть посилання", 400, "validation_error")
    if needs_url and not res.link_scope:
        res.link_scope = "external"

    # Промпт та інструкція без тексту — беззмістовні.
    if res.resource_type in ("prompt", "instruction") and creating and not res.body:
        raise ApiError("Додайте текст промпту або інструкції", 400, "validation_error")


# ------------------------------- Ресурси -------------------------------

@bp.get("/resources")
@require_auth
def list_resources():
    """Перелік ресурсів. `?type=` — фільтр за типом.

    Admin/Skill Manager бачать усе; решта — лише опубліковані.
    """
    query = CatalogResource.query
    rtype = request.args.get("type")
    if rtype:
        if rtype not in RESOURCE_TYPES:
            raise ApiError("Невідомий тип ресурсу", 400, "validation_error")
        query = query.filter_by(resource_type=rtype)
    if not _is_manager(current_user()):
        query = query.filter_by(status="published")
    items = query.order_by(CatalogResource.is_featured.desc(),
                           CatalogResource.id.desc()).all()
    return jsonify([r.to_dict() for r in items])


@bp.get("/resources/<int:resource_id>")
@require_auth
def get_resource(resource_id):
    res = CatalogResource.query.get_or_404(resource_id)
    if res.status != "published" and not _is_manager(current_user()):
        raise ApiError("Ресурс недоступний", 403, "forbidden")
    return jsonify(res.to_dict())


@bp.post("/resources")
@require_global_role("admin", "skill_manager")
def create_resource():
    data = request.get_json(silent=True) or {}
    user = current_user()
    res = CatalogResource(created_by=user.id)
    res.author = user.full_name or user.username
    _apply_fields(res, data, creating=True)
    if (data.get("status") or "draft") == "published":
        res.status = "published"
        res.published_at = datetime.utcnow()
    db.session.add(res)
    db.session.commit()
    return jsonify(res.to_dict()), 201


@bp.patch("/resources/<int:resource_id>")
@require_global_role("admin", "skill_manager")
def update_resource(resource_id):
    res = CatalogResource.query.get_or_404(resource_id)
    _apply_fields(res, request.get_json(silent=True) or {})
    db.session.commit()
    return jsonify(res.to_dict())


@bp.post("/resources/<int:resource_id>/status")
@require_global_role("admin", "skill_manager")
def change_resource_status(resource_id):
    """Публікація / зняття з публікації ресурсу."""
    res = CatalogResource.query.get_or_404(resource_id)
    data = request.get_json(silent=True) or {}
    new_status = data.get("status")
    if new_status not in VALID_STATUS:
        raise ApiError("Статус має бути 'draft' або 'published'",
                       400, "validation_error")
    res.status = new_status
    if new_status == "published" and res.published_at is None:
        res.published_at = datetime.utcnow()
    db.session.commit()
    return jsonify(res.to_dict())


@bp.post("/resources/<int:resource_id>/open")
@require_auth
def register_open(resource_id):
    """Лічильник відкриттів/копіювань — для сортування «за популярністю»."""
    res = CatalogResource.query.get_or_404(resource_id)
    if res.status != "published" and not _is_manager(current_user()):
        raise ApiError("Ресурс недоступний", 403, "forbidden")
    res.opens_count = (res.opens_count or 0) + 1
    db.session.commit()
    return jsonify({"opens_count": res.opens_count})


@bp.delete("/resources/<int:resource_id>")
@require_global_role("admin", "skill_manager")
def delete_resource(resource_id):
    res = CatalogResource.query.get_or_404(resource_id)
    CatalogFavorite.query.filter_by(item_type="resource", item_id=resource_id)\
        .delete(synchronize_session=False)
    db.session.delete(res)
    db.session.commit()
    return jsonify({"message": "Ресурс видалено"})


# -------------------------------- Обране --------------------------------

@bp.get("/favorites")
@require_auth
def list_favorites():
    """Обране поточного користувача — окремо навички й ресурси."""
    rows = CatalogFavorite.query.filter_by(user_id=current_user().id).all()
    return jsonify({
        "skill": [r.item_id for r in rows if r.item_type == "skill"],
        "resource": [r.item_id for r in rows if r.item_type == "resource"],
    })


@bp.post("/favorites")
@require_auth
def toggle_favorite():
    """Перемикає «зірочку» для навички або ресурсу каталогу."""
    data = request.get_json(silent=True) or {}
    item_type = (data.get("item_type") or "").strip()
    item_id = data.get("item_id")
    if item_type not in FAVORITE_TYPES or not isinstance(item_id, int):
        raise ApiError("Вкажіть item_type ('skill' або 'resource') та item_id",
                       400, "validation_error")
    model = Skill if item_type == "skill" else CatalogResource
    if model.query.get(item_id) is None:
        raise ApiError("Елемент каталогу не знайдено", 404, "not_found")

    user_id = current_user().id
    row = CatalogFavorite.query.filter_by(
        user_id=user_id, item_type=item_type, item_id=item_id).first()
    if row is not None:
        db.session.delete(row)
        favorited = False
    else:
        db.session.add(CatalogFavorite(user_id=user_id, item_type=item_type,
                                       item_id=item_id))
        favorited = True
    db.session.commit()
    return jsonify({"item_type": item_type, "item_id": item_id,
                    "favorited": favorited})
