"""Доступ користувачів до моделей.

Правила:
  * системна модель доступна всім і кожному;
  * решта моделей — лише групам, яким їх призначено (матриця «групи × моделі»);
  * ефективний доступ користувача обчислюється динамічно з його активних
    членств у групах (окремого обліку по користувачах не ведемо);
  * адміністратор бачить усі активні моделі.

Порядок сортування (для списку та вибору моделі за замовчуванням): спершу
системна, далі — alphanumeric (1-9, A-z).
"""
from app.extensions import db
from app.models import Model, GroupModel, GroupMembership


def _user_group_ids(user_id):
    return [m.group_id for m in GroupMembership.query.filter_by(
        user_id=user_id, status="active").all()]


def _sort_key(model):
    return (not model.is_system, (model.name or "").casefold())


def accessible_models(user):
    """Список активних моделей, доступних користувачу (відсортований)."""
    active = Model.query.filter_by(is_active=True).all()
    if user.is_system_admin:
        allowed = active
    else:
        gids = set(_user_group_ids(user.id))
        granted = set()
        if gids:
            granted = {gm.model_id for gm in GroupModel.query.filter(
                GroupModel.group_id.in_(gids)).all()}
        allowed = [m for m in active if m.is_system or m.id in granted]
    return sorted(allowed, key=_sort_key)


def default_model(user):
    """Модель за замовчуванням: одна → вона; кілька → системна має пріоритет,
    інакше — перша за alphanumeric-порядком."""
    models = accessible_models(user)
    return models[0] if models else None


def is_accessible(user, model_id):
    return any(m.id == model_id for m in accessible_models(user))


def set_group_model(group_id, model_id, granted, assigned_by=None):
    """Вмикає/вимикає доступ групи до моделі (ідемпотентно)."""
    link = GroupModel.query.filter_by(group_id=group_id, model_id=model_id).first()
    if granted and link is None:
        db.session.add(GroupModel(group_id=group_id, model_id=model_id,
                                  assigned_by=assigned_by))
    elif not granted and link is not None:
        db.session.delete(link)
    db.session.commit()
    return granted
