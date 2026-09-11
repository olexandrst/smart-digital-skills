"""Воронка ідей (BR-15, FR-10): подання, черга менеджера, життєвий цикл.

Подати ідею може будь-який авторизований користувач; свої ідеї він бачить
завжди. Черга всіх ідей і зміна статусу — Admin / Skill Manager.
"""
from flask import Blueprint, request, jsonify
from sqlalchemy import func
from app.extensions import db
from app.core.permissions import require_auth, require_global_role
from app.core.security import current_user
from app.core.errors import ApiError
from app.models import (
    Idea, CatalogResource, ReviewLog, IDEA_STATUSES, IDEA_CLOSED_STATUSES,
)

bp = Blueprint("ideas", __name__)

MAX_TITLE = 200
MAX_TEXT = 5000


def _is_manager(user):
    return user.has_global_role("admin") or user.has_global_role("skill_manager")


def _clean_text(value, label, *, required=False, limit=MAX_TEXT):
    text = (value or "").strip()
    if not text:
        if required:
            raise ApiError(f"Заповніть поле «{label}»", 400, "validation_error")
        return None
    if len(text) > limit:
        raise ApiError(f"Поле «{label}» завелике (максимум {limit} символів)",
                       400, "validation_error")
    return text


def _clean_url(value):
    url = (value or "").strip()
    if not url:
        return None
    low = url.lower()
    if not (low.startswith("http://") or low.startswith("https://")
            or url.startswith("/")):
        raise ApiError("Посилання має починатися з http://, https:// або «/»",
                       400, "validation_error")
    if len(url) > 2000:
        raise ApiError("Посилання завелике", 400, "validation_error")
    return url


def _resolve_resource(value, label):
    if value in (None, "", 0):
        return None
    try:
        resource_id = int(value)
    except (TypeError, ValueError):
        raise ApiError(f"Некоректне значення поля «{label}»", 400, "validation_error")
    if CatalogResource.query.get(resource_id) is None:
        raise ApiError("Матеріал каталогу не знайдено", 404, "not_found")
    return resource_id


@bp.get("")
@require_auth
def list_ideas():
    """Ідеї. Звичайний користувач бачить лише свої; менеджер — усі.

    `?status=` — фільтр за статусом, `?mine=1` — лише власні (для менеджера).
    """
    user = current_user()
    query = Idea.query
    manager = _is_manager(user)
    if not manager or request.args.get("mine") == "1":
        query = query.filter_by(author_id=user.id)

    status = request.args.get("status")
    if status == "open":
        query = query.filter(Idea.status.notin_(IDEA_CLOSED_STATUSES))
    elif status:
        if status not in IDEA_STATUSES:
            raise ApiError("Статус має бути одним із: " + ", ".join(IDEA_STATUSES),
                           400, "validation_error")
        query = query.filter_by(status=status)

    rows = query.order_by(Idea.created_at.desc()).all()
    return jsonify([i.to_dict() for i in rows])


@bp.get("/stats")
@require_global_role("admin", "skill_manager")
def idea_stats():
    """Скільки ідей у кожному статусі — для черги та аналітики воронки."""
    counts = dict(db.session.query(Idea.status, func.count(Idea.id))
                  .group_by(Idea.status).all())
    return jsonify({
        "by_status": {s: counts.get(s, 0) for s in IDEA_STATUSES},
        "total": sum(counts.values()),
        "open": sum(n for s, n in counts.items() if s not in IDEA_CLOSED_STATUSES),
    })


@bp.get("/<int:idea_id>")
@require_auth
def get_idea(idea_id):
    idea = Idea.query.get_or_404(idea_id)
    user = current_user()
    if idea.author_id != user.id and not _is_manager(user):
        raise ApiError("Чужа ідея", 403, "forbidden")
    return jsonify(idea.to_dict())


@bp.post("")
@require_auth
def create_idea():
    """Подання ідеї. Доступне будь-якому авторизованому користувачу."""
    data = request.get_json(silent=True) or {}
    user = current_user()
    idea = Idea(
        title=_clean_text(data.get("title"), "Суть ідеї", required=True,
                          limit=MAX_TITLE),
        body=_clean_text(data.get("body"), "Опис ідеї", required=True),
        problem=_clean_text(data.get("problem"), "Бізнес-задача"),
        expected_effect=_clean_text(data.get("expected_effect"), "Очікуваний ефект"),
        contact=_clean_text(data.get("contact"), "Контакт", limit=200),
        author_id=user.id,
        author_name=user.full_name or user.username,
        source_resource_id=_resolve_resource(data.get("source_resource_id"),
                                             "матеріал-джерело"),
    )
    db.session.add(idea)
    db.session.flush()
    _log_idea(idea, None, "submitted", None)
    db.session.commit()
    return jsonify(idea.to_dict()), 201


@bp.patch("/<int:idea_id>")
@require_global_role("admin", "skill_manager")
def update_idea(idea_id):
    """Маршрутизація ідеї: зовнішній процес оцінки та картка-результат."""
    idea = Idea.query.get_or_404(idea_id)
    data = request.get_json(silent=True) or {}
    if "external_url" in data:
        idea.external_url = _clean_url(data.get("external_url"))
    if "resource_id" in data:
        idea.resource_id = _resolve_resource(data.get("resource_id"),
                                             "картка-результат")
    db.session.commit()
    return jsonify(idea.to_dict())


@bp.post("/<int:idea_id>/status")
@require_global_role("admin", "skill_manager")
def change_idea_status(idea_id):
    """Перехід життєвим циклом із коментарем, який бачить автор ідеї."""
    idea = Idea.query.get_or_404(idea_id)
    data = request.get_json(silent=True) or {}
    new_status = data.get("status")
    if new_status not in IDEA_STATUSES:
        raise ApiError("Статус має бути одним із: " + ", ".join(IDEA_STATUSES),
                       400, "validation_error")
    note = _clean_text(data.get("note"), "Коментар")
    if new_status in IDEA_CLOSED_STATUSES and not note:
        raise ApiError("Для відхилення чи закриття ідеї потрібен коментар — "
                       "автор має розуміти рішення", 400, "note_required")
    if "resource_id" in data:
        idea.resource_id = _resolve_resource(data.get("resource_id"),
                                             "картка-результат")

    old_status = idea.status
    idea.status = new_status
    idea.status_note = note
    if old_status != new_status:
        _log_idea(idea, old_status, new_status, note)
    db.session.commit()
    return jsonify(idea.to_dict())


@bp.get("/<int:idea_id>/log")
@require_auth
def idea_log(idea_id):
    """Історія рішень за ідеєю — видима і авторові, щоб не питати «а що з нею»."""
    idea = Idea.query.get_or_404(idea_id)
    user = current_user()
    if idea.author_id != user.id and not _is_manager(user):
        raise ApiError("Чужа ідея", 403, "forbidden")
    rows = (ReviewLog.query.filter_by(item_type="idea", item_id=idea_id)
            .order_by(ReviewLog.created_at.desc()).all())
    return jsonify([r.to_dict() for r in rows])


def _log_idea(idea, from_status, to_status, note):
    """Запис у спільний журнал життєвого циклу (той самий, що й для матеріалів)."""
    user = current_user()
    db.session.add(ReviewLog(
        item_type="idea", item_id=idea.id, item_name=idea.title,
        actor_user_id=user.id if user else None,
        actor_name=(user.full_name or user.username) if user else None,
        from_status=from_status, to_status=to_status, note=note,
    ))
