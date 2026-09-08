"""Каталог AI Knowledge Hub: розділи, ресурси, обране, пошук та аналітика.

Навички лишаються у /api/skills — тут усе інше наповнення каталогу.
Керування ресурсами й розділами: Admin / Skill Manager. Перегляд — усі.
"""
from datetime import datetime
from flask import Blueprint, request, jsonify
from sqlalchemy import func
from app.extensions import db
from app.core.permissions import require_auth, require_global_role
from app.core.security import current_user
from app.core.errors import ApiError
from app.models import (
    CatalogSection, CatalogResource, CatalogFavorite, ReviewLog, SearchQueryLog,
    Skill, SkillFeedback, AppSetting,
    RESOURCE_TYPES, LINK_TYPES, BODY_TYPES, LINK_SCOPES, ACCENTS,
    RESOURCE_STATUSES, PUBLIC_STATUSES, REUSE_LEVELS,
)

bp = Blueprint("catalog", __name__)

FAVORITE_TYPES = {"skill", "resource"}
MAX_BODY = 100_000
MAX_QUERY = 200
# Ключ налаштування «сувора публікація» (BR-05): вимагати обов'язкові поля.
STRICT_PUBLISH_KEY = "catalog_publish_strict"
# Поля, без яких матеріал не публікується при увімкненій суворій публікації.
REQUIRED_ON_PUBLISH = (
    ("description", "короткий опис"),
    ("section_id", "розділ"),
    ("category", "категорія"),
    ("owner", "власник матеріалу"),
    ("next_review_at", "дата наступного перегляду"),
)


def _is_manager(user):
    return user.has_global_role("admin") or user.has_global_role("skill_manager")


def strict_publish_enabled():
    """Чи ввімкнено вимогу обов'язкових полів при публікації (governance)."""
    return AppSetting.get(STRICT_PUBLISH_KEY, "0") == "1"


def _clean_url(value, required, label="посилання"):
    """Валідує посилання: http(s)://… або внутрішній шлях /…"""
    url = (value or "").strip()
    if not url:
        if required:
            raise ApiError(f"Вкажіть {label}", 400, "validation_error")
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


def _parse_date(value, field):
    """Дата 'YYYY-MM-DD' або ISO-рядок; порожнє значення → None."""
    text = (value or "").strip() if isinstance(value, str) else value
    if not text:
        return None
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00").split("+")[0])
    except ValueError:
        raise ApiError(f"Некоректна дата у полі «{field}» (очікується РРРР-ММ-ДД)",
                       400, "validation_error")


def _resolve_section(section_id):
    if section_id in (None, "", 0):
        return None
    try:
        section_id = int(section_id)
    except (TypeError, ValueError):
        raise ApiError("Некоректний розділ", 400, "validation_error")
    if CatalogSection.query.get(section_id) is None:
        raise ApiError("Розділ не знайдено", 404, "not_found")
    return section_id


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

    for field in ("category", "author", "icon_emoji", "owner", "tools"):
        if field in data:
            value = (data.get(field) or "").strip()
            setattr(res, field, value or None)

    if "section_id" in data:
        res.section_id = _resolve_section(data.get("section_id"))

    if "tags" in data:
        res.tags = _clean_tags(data.get("tags"))

    if "reuse_level" in data:
        level = (data.get("reuse_level") or "").strip()
        res.reuse_level = level if level in REUSE_LEVELS else None

    for field, label in (("reviewed_at", "Дата перегляду"),
                         ("next_review_at", "Дата наступного перегляду")):
        if field in data:
            setattr(res, field, _parse_date(data.get(field), label))

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

    # Посилання обов'язкове для агентів, MCP-серверів і корисних посилань.
    needs_url = res.resource_type in LINK_TYPES
    url_label = "endpoint сервера" if res.resource_type == "mcp" else "посилання"
    if "url" in data or (creating and needs_url):
        res.url = _clean_url(data.get("url"), required=needs_url, label=url_label)
    if needs_url and not res.url:
        raise ApiError(f"Вкажіть {url_label}", 400, "validation_error")
    if needs_url and not res.link_scope:
        res.link_scope = "external"

    # Промпт, інструкція та кейс без тексту — беззмістовні.
    if res.resource_type in BODY_TYPES and creating and not res.body:
        raise ApiError("Додайте текст матеріалу", 400, "validation_error")


def _require_publish_ready(res):
    """BR-05: не публікувати матеріал без обов'язкових полів (якщо ввімкнено)."""
    if not strict_publish_enabled():
        return
    missing = [label for field, label in REQUIRED_ON_PUBLISH if not getattr(res, field)]
    if missing:
        raise ApiError("Для публікації заповніть: " + ", ".join(missing),
                       400, "publish_requirements")


def _log_review(item_type, item, from_status, to_status, note=None):
    """Запис у журнал життєвого циклу (FR-08)."""
    user = current_user()
    db.session.add(ReviewLog(
        item_type=item_type, item_id=item.id, item_name=item.name,
        actor_user_id=user.id if user else None,
        actor_name=(user.full_name or user.username) if user else None,
        from_status=from_status, to_status=to_status, note=note,
    ))


# ------------------------------- Розділи -------------------------------

@bp.get("/sections")
@require_auth
def list_sections():
    """Розділи каталогу з кількістю матеріалів у кожному."""
    manager = _is_manager(current_user())
    query = CatalogSection.query
    if not manager:
        query = query.filter_by(is_active=True)
    sections = query.order_by(CatalogSection.position, CatalogSection.id).all()

    statuses = RESOURCE_STATUSES if manager else PUBLIC_STATUSES
    counts = dict(
        db.session.query(CatalogResource.section_id, func.count(CatalogResource.id))
        .filter(CatalogResource.section_id.isnot(None))
        .filter(CatalogResource.status.in_(statuses))
        .group_by(CatalogResource.section_id).all())
    skill_statuses = ("published",) if not manager else ("draft", "published")
    for section_id, n in (db.session.query(Skill.section_id, func.count(Skill.id))
                          .filter(Skill.section_id.isnot(None))
                          .filter(Skill.status.in_(skill_statuses))
                          .group_by(Skill.section_id).all()):
        counts[section_id] = counts.get(section_id, 0) + n
    return jsonify([s.to_dict(counts) for s in sections])


@bp.post("/sections")
@require_global_role("admin", "skill_manager")
def create_section():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        raise ApiError("Назва розділу не може бути порожньою", 400, "validation_error")
    # Порівняння без урахування регістру в Python (SQLite lower() лишає кирилицю).
    if any(s.name.casefold() == name.casefold() for s in CatalogSection.query.all()):
        raise ApiError("Такий розділ уже існує", 409, "section_exists")
    section = CatalogSection(name=name)
    _apply_section_fields(section, data)
    db.session.add(section)
    db.session.commit()
    return jsonify(section.to_dict()), 201


def _apply_section_fields(section, data):
    for field in ("description", "icon_emoji"):
        if field in data:
            value = (data.get(field) or "").strip()
            setattr(section, field, value or None)
    if "accent" in data:
        accent = (data.get("accent") or "").strip()
        section.accent = accent if accent in ACCENTS else None
    if "url" in data:
        section.url = _clean_url(data.get("url"), required=False)
    if "position" in data:
        try:
            section.position = int(data.get("position") or 0)
        except (TypeError, ValueError):
            raise ApiError("Позиція має бути числом", 400, "validation_error")
    if "is_active" in data:
        section.is_active = bool(data.get("is_active"))


@bp.patch("/sections/<int:section_id>")
@require_global_role("admin", "skill_manager")
def update_section(section_id):
    section = CatalogSection.query.get_or_404(section_id)
    data = request.get_json(silent=True) or {}
    if "name" in data:
        name = (data.get("name") or "").strip()
        if not name:
            raise ApiError("Назва розділу не може бути порожньою",
                           400, "validation_error")
        if any(s.name.casefold() == name.casefold() and s.id != section_id
               for s in CatalogSection.query.all()):
            raise ApiError("Такий розділ уже існує", 409, "section_exists")
        section.name = name
    _apply_section_fields(section, data)
    db.session.commit()
    return jsonify(section.to_dict())


@bp.delete("/sections/<int:section_id>")
@require_global_role("admin", "skill_manager")
def delete_section(section_id):
    """Видаляє розділ. Матеріали зберігаються — у них знімається прив'язка."""
    section = CatalogSection.query.get_or_404(section_id)
    CatalogResource.query.filter_by(section_id=section_id).update(
        {CatalogResource.section_id: None}, synchronize_session=False)
    Skill.query.filter_by(section_id=section_id).update(
        {Skill.section_id: None}, synchronize_session=False)
    db.session.delete(section)
    db.session.commit()
    return jsonify({"message": "Розділ видалено"})


# ------------------------------- Ресурси -------------------------------

@bp.get("/resources")
@require_auth
def list_resources():
    """Перелік ресурсів. `?type=` — фільтр за типом, `?section_id=` — за розділом.

    Admin/Skill Manager бачать усе; решта — опубліковані та ті, що потребують
    оновлення (чернетки й архів приховані).
    """
    query = CatalogResource.query
    rtype = request.args.get("type")
    if rtype:
        if rtype not in RESOURCE_TYPES:
            raise ApiError("Невідомий тип ресурсу", 400, "validation_error")
        query = query.filter_by(resource_type=rtype)
    section_id = request.args.get("section_id")
    if section_id:
        query = query.filter_by(section_id=_resolve_section(section_id))
    if not _is_manager(current_user()):
        query = query.filter(CatalogResource.status.in_(PUBLIC_STATUSES))
    items = query.order_by(CatalogResource.is_featured.desc(),
                           CatalogResource.id.desc()).all()
    ratings = _rating_map("resource")
    return jsonify([dict(r.to_dict(), **ratings.get(r.id, _EMPTY_RATING))
                    for r in items])


_EMPTY_RATING = {"rating_avg": None, "rating_count": 0}


def _rating_map(item_type):
    """Середній рейтинг і кількість оцінок за матеріалами одного типу."""
    id_col = SkillFeedback.resource_id if item_type == "resource" else SkillFeedback.skill_id
    rows = (db.session.query(id_col, func.avg(SkillFeedback.rating),
                             func.count(SkillFeedback.rating))
            .filter(id_col.isnot(None), SkillFeedback.rating.isnot(None))
            .group_by(id_col).all())
    return {row[0]: {"rating_avg": round(float(row[1]), 1), "rating_count": int(row[2])}
            for row in rows}


@bp.get("/resources/<int:resource_id>")
@require_auth
def get_resource(resource_id):
    res = CatalogResource.query.get_or_404(resource_id)
    if res.status not in PUBLIC_STATUSES and not _is_manager(current_user()):
        raise ApiError("Ресурс недоступний", 403, "forbidden")
    ratings = _rating_map("resource")
    return jsonify(dict(res.to_dict(), **ratings.get(res.id, _EMPTY_RATING)))


@bp.post("/resources")
@require_global_role("admin", "skill_manager")
def create_resource():
    data = request.get_json(silent=True) or {}
    user = current_user()
    res = CatalogResource(created_by=user.id)
    res.author = user.full_name or user.username
    _apply_fields(res, data, creating=True)
    publish = (data.get("status") or "draft") == "published"
    if publish:
        _require_publish_ready(res)
        res.status = "published"
        res.published_at = datetime.utcnow()
    db.session.add(res)
    db.session.flush()
    if publish:
        _log_review("resource", res, None, "published")
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
    """Перехід життєвим циклом: draft → published → needs_update → archived."""
    res = CatalogResource.query.get_or_404(resource_id)
    data = request.get_json(silent=True) or {}
    new_status = data.get("status")
    if new_status not in RESOURCE_STATUSES:
        raise ApiError("Статус має бути одним із: " + ", ".join(RESOURCE_STATUSES),
                       400, "validation_error")
    if new_status == "published":
        _require_publish_ready(res)

    old_status = res.status
    res.status = new_status
    if new_status == "published":
        if res.published_at is None:
            res.published_at = datetime.utcnow()
        res.reviewed_at = datetime.utcnow()
    if old_status != new_status:
        _log_review("resource", res, old_status, new_status,
                    (data.get("note") or "").strip() or None)
    db.session.commit()
    return jsonify(res.to_dict())


@bp.get("/resources/<int:resource_id>/review-log")
@require_global_role("admin", "skill_manager")
def resource_review_log(resource_id):
    CatalogResource.query.get_or_404(resource_id)
    rows = (ReviewLog.query.filter_by(item_type="resource", item_id=resource_id)
            .order_by(ReviewLog.created_at.desc()).all())
    return jsonify([r.to_dict() for r in rows])


@bp.post("/resources/<int:resource_id>/open")
@require_auth
def register_open(resource_id):
    """Лічильник відкриттів/копіювань — для сортування «за популярністю»."""
    res = CatalogResource.query.get_or_404(resource_id)
    if res.status not in PUBLIC_STATUSES and not _is_manager(current_user()):
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
    SkillFeedback.query.filter_by(resource_id=resource_id).update(
        {SkillFeedback.resource_id: None}, synchronize_session=False)
    db.session.delete(res)
    db.session.commit()
    return jsonify({"message": "Ресурс видалено"})


# -------------------------- Зворотний зв'язок --------------------------

def _clean_rating(value):
    if value in (None, ""):
        return None
    try:
        rating = int(value)
    except (TypeError, ValueError):
        raise ApiError("Оцінка має бути числом від 1 до 5", 400, "validation_error")
    if not 1 <= rating <= 5:
        raise ApiError("Оцінка має бути числом від 1 до 5", 400, "validation_error")
    return rating


@bp.post("/resources/<int:resource_id>/feedback")
@require_auth
def submit_resource_feedback(resource_id):
    """Відгук та/або оцінка матеріалу каталогу (BR-13)."""
    res = CatalogResource.query.get_or_404(resource_id)
    if res.status not in PUBLIC_STATUSES and not _is_manager(current_user()):
        raise ApiError("Ресурс недоступний", 403, "forbidden")
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    rating = _clean_rating(data.get("rating"))
    if not message and rating is None:
        raise ApiError("Залиште оцінку або повідомлення", 400, "validation_error")
    if len(message) > 5000:
        raise ApiError("Повідомлення завелике (максимум 5000 символів)",
                       400, "validation_error")
    user = current_user()
    fb = SkillFeedback(
        item_type="resource", resource_id=res.id, skill_name=res.name,
        skill_version=res.version, rating=rating,
        user_id=user.id, username=user.full_name or user.username,
        message=message or "(без коментаря)",
    )
    db.session.add(fb)
    db.session.commit()
    return jsonify({"message": "Дякуємо! Відгук надіслано.", "id": fb.id}), 201


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


# ---------------------------- Пошукові запити ----------------------------

@bp.post("/search-log")
@require_auth
def log_search():
    """Реєструє пошуковий запит користувача (BR-08, FR-09).

    Повертає id запису — фронт передає його у /search-log/<id>/opened, якщо
    користувач після пошуку відкрив картку (це і є Search Success Rate).
    """
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    if not query:
        raise ApiError("Порожній запит", 400, "validation_error")
    row = SearchQueryLog(
        query_text=query[:MAX_QUERY],
        query_norm=query[:MAX_QUERY].casefold(),
        results_count=int(data.get("results_count") or 0),
        section_id=_resolve_section(data.get("section_id")),
        kind=(data.get("kind") or None),
        user_id=current_user().id,
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"id": row.id}), 201


@bp.post("/search-log/<int:log_id>/opened")
@require_auth
def log_search_opened(log_id):
    """Позначає, що після цього запиту користувач відкрив матеріал."""
    row = SearchQueryLog.query.get_or_404(log_id)
    if row.user_id != current_user().id:
        raise ApiError("Чужий запис пошуку", 403, "forbidden")
    data = request.get_json(silent=True) or {}
    item_type = (data.get("item_type") or "").strip()
    if item_type not in FAVORITE_TYPES:
        raise ApiError("Вкажіть item_type ('skill' або 'resource')",
                       400, "validation_error")
    row.opened_item_type = item_type
    row.opened_item_id = data.get("item_id")
    db.session.commit()
    return jsonify({"id": row.id, "opened_item_type": row.opened_item_type})


# ----------------------------- Аналітика хабу -----------------------------

@bp.get("/settings")
@require_auth
def catalog_settings():
    """Налаштування каталогу для UI."""
    return jsonify({"strict_publish": strict_publish_enabled()})


@bp.post("/settings")
@require_global_role("admin", "skill_manager")
def set_catalog_settings():
    data = request.get_json(silent=True) or {}
    if "strict_publish" not in data:
        raise ApiError("Вкажіть strict_publish", 400, "validation_error")
    AppSetting.set(STRICT_PUBLISH_KEY, "1" if data["strict_publish"] else "0")
    db.session.commit()
    return jsonify({"strict_publish": strict_publish_enabled()})


@bp.get("/analytics")
@require_global_role("admin", "skill_manager")
def analytics():
    """Базова аналітика наповнення хабу (BR-16, FR-11).

    Свідомо містить лише метрики, які чесно рахуються з наявних даних.
    MAU/DAU/NPS потребують посесійного трекінгу переглядів — його немає.
    """
    resources = CatalogResource.query.all()
    skills = Skill.query.all()
    now = datetime.utcnow()

    by_type = {t: 0 for t in RESOURCE_TYPES}
    by_status = {s: 0 for s in RESOURCE_STATUSES}
    for r in resources:
        by_type[r.resource_type] = by_type.get(r.resource_type, 0) + 1
        by_status[r.status] = by_status.get(r.status, 0) + 1
    by_type["skill"] = len(skills)

    sections = CatalogSection.query.order_by(CatalogSection.position,
                                             CatalogSection.id).all()
    by_section = []
    for s in sections:
        by_section.append({
            "id": s.id, "name": s.name,
            "resources": sum(1 for r in resources if r.section_id == s.id),
            "skills": sum(1 for k in skills if k.section_id == s.id),
        })
    unassigned = (sum(1 for r in resources if not r.section_id)
                  + sum(1 for k in skills if not k.section_id))

    published = [r for r in resources if r.status in PUBLIC_STATUSES]
    freshness = {
        "total": len(resources),
        "published": by_status.get("published", 0),
        "needs_update": by_status.get("needs_update", 0),
        "draft": by_status.get("draft", 0),
        "archived": by_status.get("archived", 0),
        "no_owner": sum(1 for r in published if not r.owner),
        "no_section": sum(1 for r in published if not r.section_id),
        "no_tags": sum(1 for r in published if not r.tags),
        "review_overdue": sum(1 for r in published
                              if r.next_review_at and r.next_review_at < now),
    }

    top_opened = sorted(resources, key=lambda r: r.opens_count or 0, reverse=True)[:10]
    top_items = [{"id": r.id, "name": r.name, "resource_type": r.resource_type,
                  "opens_count": r.opens_count or 0} for r in top_opened
                 if (r.opens_count or 0) > 0]

    # Пошукові запити: найпопулярніші та ті, що не дали результатів.
    def _queries(no_results):
        q = (db.session.query(SearchQueryLog.query_norm,
                              func.count(SearchQueryLog.id),
                              func.max(SearchQueryLog.query_text))
             .group_by(SearchQueryLog.query_norm))
        q = (q.having(func.max(SearchQueryLog.results_count) == 0) if no_results
             else q.having(func.max(SearchQueryLog.results_count) > 0))
        rows = q.order_by(func.count(SearchQueryLog.id).desc()).limit(10).all()
        return [{"query": row[2], "count": int(row[1])} for row in rows]

    total_searches = SearchQueryLog.query.count()
    successful = SearchQueryLog.query.filter(
        SearchQueryLog.opened_item_type.isnot(None)).count()
    zero_results = SearchQueryLog.query.filter_by(results_count=0).count()

    rating_row = (db.session.query(func.avg(SkillFeedback.rating),
                                   func.count(SkillFeedback.rating))
                  .filter(SkillFeedback.rating.isnot(None)).one())

    return jsonify({
        "by_type": by_type,
        "by_section": by_section,
        "unassigned": unassigned,
        "freshness": freshness,
        "top_opened": top_items,
        "search": {
            "total": total_searches,
            "success_rate": round(successful / total_searches * 100, 1) if total_searches else None,
            "no_results_rate": round(zero_results / total_searches * 100, 1) if total_searches else None,
            "top": _queries(no_results=False),
            "no_results": _queries(no_results=True),
        },
        "rating": {
            "avg": round(float(rating_row[0]), 2) if rating_row[0] is not None else None,
            "count": int(rating_row[1]),
        },
        "feedback_unread": SkillFeedback.query.filter_by(is_read=False).count(),
        "strict_publish": strict_publish_enabled(),
    })
