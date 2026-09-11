"""Навчальні маршрути (BR-9): перегляд, проходження, керування.

Дивитися й проходити маршрути може будь-який авторизований користувач;
створювати й редагувати — Admin / Skill Manager.
"""
from flask import Blueprint, request, jsonify
from app.extensions import db
from app.core.permissions import require_auth, require_global_role
from app.core.security import current_user
from app.core.errors import ApiError
from app.models import (
    LearningPath, LearningPathStep, LearningProgress, CatalogResource, CatalogTerm,
)

bp = Blueprint("paths", __name__)

MAX_TEXT = 5000


def _is_manager(user):
    return user.has_global_role("admin") or user.has_global_role("skill_manager")


def _done_steps(user, path_id=None):
    """Ідентифікатори кроків, які користувач уже пройшов."""
    query = LearningProgress.query.filter_by(user_id=user.id)
    if path_id is not None:
        query = query.filter_by(path_id=path_id)
    return {row.step_id for row in query}


def _clean_url(value):
    url = (value or "").strip()
    if not url:
        return None
    low = url.lower()
    if not (low.startswith("http://") or low.startswith("https://")
            or url.startswith("/")):
        raise ApiError("Посилання має починатися з http://, https:// або «/»",
                       400, "validation_error")
    return url[:2000]


@bp.get("")
@require_auth
def list_paths():
    """Маршрути з прогресом поточного користувача.

    `?mine=1` — лише розпочаті; `?starter=1` — лише стартові.
    """
    user = current_user()
    query = LearningPath.query
    if not _is_manager(user):
        query = query.filter_by(is_active=True)
    if request.args.get("starter") == "1":
        query = query.filter_by(is_starter=True)

    done = _done_steps(user)
    paths = query.order_by(LearningPath.position, LearningPath.id).all()
    rows = [p.to_dict(progress=done) for p in paths]
    if request.args.get("mine") == "1":
        rows = [r for r in rows if r["steps_done"]]
    return jsonify(rows)


@bp.get("/<int:path_id>")
@require_auth
def get_path(path_id):
    path = LearningPath.query.get_or_404(path_id)
    user = current_user()
    if not path.is_active and not _is_manager(user):
        raise ApiError("Маршрут недоступний", 403, "forbidden")
    return jsonify(path.to_dict(progress=_done_steps(user, path_id)))


@bp.post("")
@require_global_role("admin", "skill_manager")
def create_path():
    data = request.get_json(silent=True) or {}
    name = " ".join((data.get("name") or "").split())
    if not name:
        raise ApiError("Назва маршруту не може бути порожньою",
                       400, "validation_error")
    if any(p.name.casefold() == name.casefold() for p in LearningPath.query.all()):
        raise ApiError("Такий маршрут уже існує", 409, "path_exists")
    path = LearningPath(name=name)
    _apply_path_fields(path, data)
    db.session.add(path)
    db.session.commit()
    return jsonify(path.to_dict()), 201


def _apply_path_fields(path, data):
    for field in ("description", "icon_emoji", "audience_role"):
        if field in data:
            value = (data.get(field) or "").strip()
            setattr(path, field, value or None)
    if "maturity_level_id" in data:
        value = data.get("maturity_level_id")
        if value in (None, "", 0):
            path.maturity_level_id = None
        else:
            term = CatalogTerm.query.get(value)
            if term is None or term.kind != "maturity":
                raise ApiError("Рівень зрілості відсутній у довіднику",
                               400, "validation_error")
            path.maturity_level_id = term.id
    if "is_starter" in data:
        path.is_starter = bool(data.get("is_starter"))
    if "is_active" in data:
        path.is_active = bool(data.get("is_active"))
    if "position" in data:
        try:
            path.position = int(data.get("position") or 0)
        except (TypeError, ValueError):
            raise ApiError("Позиція має бути числом", 400, "validation_error")


@bp.patch("/<int:path_id>")
@require_global_role("admin", "skill_manager")
def update_path(path_id):
    path = LearningPath.query.get_or_404(path_id)
    data = request.get_json(silent=True) or {}
    if "name" in data:
        name = " ".join((data.get("name") or "").split())
        if not name:
            raise ApiError("Назва маршруту не може бути порожньою",
                           400, "validation_error")
        if any(p.name.casefold() == name.casefold() and p.id != path_id
               for p in LearningPath.query.all()):
            raise ApiError("Такий маршрут уже існує", 409, "path_exists")
        path.name = name
    _apply_path_fields(path, data)
    db.session.commit()
    return jsonify(path.to_dict())


@bp.delete("/<int:path_id>")
@require_global_role("admin", "skill_manager")
def delete_path(path_id):
    path = LearningPath.query.get_or_404(path_id)
    LearningProgress.query.filter_by(path_id=path_id).delete(synchronize_session=False)
    db.session.delete(path)
    db.session.commit()
    return jsonify({"message": "Маршрут видалено"})


# -------------------------------- Кроки --------------------------------

@bp.post("/<int:path_id>/steps")
@require_global_role("admin", "skill_manager")
def add_step(path_id):
    """Крок маршруту: або картка каталогу, або зовнішнє посилання."""
    path = LearningPath.query.get_or_404(path_id)
    data = request.get_json(silent=True) or {}
    step = LearningPathStep(path_id=path.id,
                            position=len(path.steps))
    _apply_step_fields(step, data, creating=True)
    db.session.add(step)
    db.session.commit()
    return jsonify(step.to_dict()), 201


def _apply_step_fields(step, data, *, creating=False):
    if "resource_id" in data or creating:
        value = data.get("resource_id")
        if value in (None, "", 0):
            step.resource_id = None
        else:
            if CatalogResource.query.get(value) is None:
                raise ApiError("Матеріал каталогу не знайдено", 404, "not_found")
            step.resource_id = value
    if "external_url" in data or creating:
        step.external_url = _clean_url(data.get("external_url"))
    for field in ("title", "note"):
        if field in data or creating:
            value = (data.get(field) or "").strip()
            if len(value) > MAX_TEXT:
                raise ApiError(f"Поле «{field}» завелике", 400, "validation_error")
            setattr(step, field, value or None)
    if "position" in data:
        try:
            step.position = int(data.get("position") or 0)
        except (TypeError, ValueError):
            raise ApiError("Позиція має бути числом", 400, "validation_error")

    # Крок без матеріалу й без посилання нікуди не веде.
    if not step.resource_id and not step.external_url:
        raise ApiError("Крок має вести на матеріал каталогу або на посилання",
                       400, "validation_error")
    if step.external_url and not step.title:
        raise ApiError("Для зовнішнього посилання вкажіть назву кроку",
                       400, "validation_error")


@bp.patch("/<int:path_id>/steps/<int:step_id>")
@require_global_role("admin", "skill_manager")
def update_step(path_id, step_id):
    step = LearningPathStep.query.filter_by(id=step_id, path_id=path_id).first()
    if step is None:
        raise ApiError("Крок не знайдено", 404, "not_found")
    _apply_step_fields(step, request.get_json(silent=True) or {})
    db.session.commit()
    return jsonify(step.to_dict())


@bp.delete("/<int:path_id>/steps/<int:step_id>")
@require_global_role("admin", "skill_manager")
def delete_step(path_id, step_id):
    step = LearningPathStep.query.filter_by(id=step_id, path_id=path_id).first()
    if step is None:
        raise ApiError("Крок не знайдено", 404, "not_found")
    LearningProgress.query.filter_by(step_id=step_id).delete(synchronize_session=False)
    db.session.delete(step)
    db.session.commit()
    # Перенумеровуємо, щоб у позиціях не лишалося дірок.
    for index, row in enumerate(LearningPathStep.query.filter_by(path_id=path_id)
                                .order_by(LearningPathStep.position).all()):
        row.position = index
    db.session.commit()
    return jsonify({"message": "Крок видалено"})


@bp.post("/<int:path_id>/steps/reorder")
@require_global_role("admin", "skill_manager")
def reorder_steps(path_id):
    """Новий порядок кроків списком id."""
    LearningPath.query.get_or_404(path_id)
    data = request.get_json(silent=True) or {}
    order = data.get("order")
    if not isinstance(order, list) or not order:
        raise ApiError("Вкажіть order — список id кроків", 400, "validation_error")
    steps = {s.id: s for s in LearningPathStep.query.filter_by(path_id=path_id)}
    if set(order) != set(steps):
        raise ApiError("Список має містити рівно всі кроки маршруту",
                       400, "validation_error")
    for index, step_id in enumerate(order):
        steps[step_id].position = index
    db.session.commit()
    return jsonify(LearningPath.query.get(path_id).to_dict())


# ------------------------------- Прогрес -------------------------------

@bp.post("/<int:path_id>/steps/<int:step_id>/complete")
@require_auth
def complete_step(path_id, step_id):
    """Позначає крок пройденим або знімає позначку (`done: false`)."""
    path = LearningPath.query.get_or_404(path_id)
    user = current_user()
    if not path.is_active and not _is_manager(user):
        raise ApiError("Маршрут недоступний", 403, "forbidden")
    step = LearningPathStep.query.filter_by(id=step_id, path_id=path_id).first()
    if step is None:
        raise ApiError("Крок не знайдено", 404, "not_found")

    data = request.get_json(silent=True) or {}
    done = data.get("done", True)
    row = LearningProgress.query.filter_by(user_id=user.id, step_id=step_id).first()
    if done and row is None:
        db.session.add(LearningProgress(user_id=user.id, path_id=path_id,
                                        step_id=step_id))
    elif not done and row is not None:
        db.session.delete(row)
    db.session.commit()
    return jsonify(path.to_dict(progress=_done_steps(user, path_id)))
