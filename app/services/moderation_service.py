"""Попередня модерація матеріалів і навичок (фідбек тестування, п. 12).

Матеріал користувача з'являється в каталозі лише після схвалення:

    чернетка → на розгляді → опубліковано
                  ↘ повернуто в чернетку з причиною

Хто модерує розділ (його менеджери або адміністратор), той публікує одразу,
без розгляду. Решта, натиснувши «Опублікувати», подає матеріал на розгляд:
сервер сам перетворює published на review, тож один клієнт працює для всіх.

Зміни автора до вже опублікованого не ховають його: вони лягають у
`pending_data` («пропозиція змін») і замінюють опубліковану версію, лише
коли менеджер розділу їх схвалить. Тут — спільне для матеріалів і навичок;
як саме застосувати зміни, знає ендпоінт свого типу.
"""
import json
from datetime import datetime

from app.core import access
from app.core.errors import ApiError
from app.extensions import db
from app.models import ReviewLog, User
from app.services import review_service

PUBLIC = ("published", "needs_update")


def is_public(item):
    return item.status in PUBLIC


def is_author(item, user):
    return item.created_by is not None and user is not None and item.created_by == user.id


def require_section(user, item):
    """Без розділу нікому розглянути матеріал — подати його не можна.
    Адміністратор модерує й матеріали без розділу, тож для нього це не вимога."""
    if item.section_id is None and not access.is_admin(user):
        raise ApiError("Оберіть розділ — його менеджер розгляне матеріал",
                       400, "section_required")


def target_status(user, item, requested):
    """«Опублікувати» від того, хто не модерує розділ, означає «подати на розгляд»."""
    if requested == "published" and not access.can_moderate(user, item):
        return "review"
    return requested


def log(item_type, item, user, from_status, to_status, note=None):
    """Запис у журнал життєвого циклу (FR-08)."""
    db.session.add(ReviewLog(
        item_type=item_type, item_id=item.id, item_name=item.name,
        actor_user_id=user.id if user else None,
        actor_name=(user.full_name or user.username) if user else None,
        from_status=from_status, to_status=to_status, note=note,
    ))


def submit(item_type, item, user, requested, publish):
    """Подання автором: публікує одразу (якщо автор модерує розділ) або ставить
    на розгляд. `publish(item)` — що зробити з матеріалом при публікації
    (власник, дата, перевірка обов'язкових полів). Повертає новий статус."""
    require_section(user, item)
    status = target_status(user, item, requested)
    old = item.status if item.id is not None else None
    if status == "published":
        publish(item)
        item.status = "published"
    else:
        item.status = "review"
        item.submitted_at = datetime.utcnow()
    item.review_note = None
    log(item_type, item, user, old, item.status)
    if item.status == "review":
        review_service.notify_review_requested(item_type, item, user)
    return item.status


# --- Пропозиція змін до опублікованого ---

def get_pending(item):
    if not item.pending_data:
        return None
    try:
        data = json.loads(item.pending_data)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def store_pending(item_type, item, user, changes):
    """Додає зміни автора до пропозиції (поверх попередніх, ще не схвалених)."""
    data = get_pending(item) or {}
    data.update(changes)
    item.pending_data = json.dumps(data, ensure_ascii=False)
    item.pending_by = user.id
    item.pending_at = datetime.utcnow()
    review_service.notify_review_requested(item_type, item, user, changes=True)


def clear_pending(item):
    item.pending_data = None
    item.pending_by = None
    item.pending_at = None


def pending_payload(item, user):
    """Пропозицію змін бачать автор і ті, хто модерує розділ."""
    data = get_pending(item)
    if data is None or not (is_author(item, user) or access.can_moderate(user, item)):
        return None
    by = db.session.get(User, item.pending_by) if item.pending_by else None
    return {"data": data,
            "by": (by.full_name or by.username) if by else None,
            "at": item.pending_at.isoformat() if item.pending_at else None}


def needs_pending(item, user):
    """Чи йдуть зміни через схвалення: опубліковане правлять ті, хто не модерує."""
    return is_public(item) and not access.can_moderate(user, item)


def clean_note(data):
    note = " ".join(((data or {}).get("note") or "").split()) or None
    if note and len(note) > 2000:
        raise ApiError("Причина завелика (максимум 2000 символів)", 400, "validation_error")
    return note


def queue_filter(model, user):
    """Умова для черги модерації: подане на розгляд і зміни, що чекають
    схвалення, — у розділах, які людина модерує (адміністратор — усі)."""
    cond = db.or_(model.status == "review", model.pending_data.isnot(None))
    if access.is_admin(user):
        return cond
    managed = access.managed_section_ids(user)
    if not managed:
        return db.false()
    return db.and_(cond, model.section_id.in_(managed))


def visible_filter(model, user, public=PUBLIC):
    """Що з переліку бачить людина: опубліковане й усе в розділах, які вона
    модерує (адміністратор — усе). Власні чернетки автор бачить окремо
    (`mine=1`), а не в каталозі."""
    if access.is_admin(user):
        return db.true()
    conds = [model.status.in_(public)]
    managed = access.managed_section_ids(user)
    if managed:
        conds.append(model.section_id.in_(managed))
    return db.or_(*conds)


def can_see(item, user, public=PUBLIC):
    return (item.status in public or is_author(item, user)
            or access.can_moderate(user, item))
