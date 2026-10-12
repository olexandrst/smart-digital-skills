"""Коментарі й оцінки під картками каталогу — спільні для матеріалів і навичок.

Коментар публічний: його бачать усі, хто бачить картку. Оцінка (1–5) —
одна на користувача: нова замінює попередню, тож середнє не можна
«накрутити» повторними відгуками. Оцінку можна поставити без тексту — тоді
вона рахується в середньому, але в стрічці коментарів не з'являється.

Зберігається все в таблиці `skill_feedback` (історична назва). Відгуки,
залишені до появи публічних коментарів, писалися лише для команди: вони
мають `is_public = False` і в стрічку не потрапляють.
"""
from app.extensions import db
from app.core.errors import ApiError
from app.models import SkillFeedback
from app.services import review_service

MAX_LENGTH = 5000
# Так раніше позначали відгук без тексту — у стрічці його не показуємо.
_LEGACY_EMPTY = "(без коментаря)"


def clean_rating(value):
    if value in (None, ""):
        return None
    try:
        rating = int(value)
    except (TypeError, ValueError):
        raise ApiError("Оцінка має бути числом від 1 до 5", 400, "validation_error")
    if not 1 <= rating <= 5:
        raise ApiError("Оцінка має бути числом від 1 до 5", 400, "validation_error")
    return rating


def _item_filter(item_type, item_id):
    column = SkillFeedback.skill_id if item_type == "skill" else SkillFeedback.resource_id
    return column == item_id


def add(item_type, item, user, message, rating):
    """Новий коментар та/або оцінка. Повертає запис (ще не зафіксований)."""
    message = (message or "").strip()
    rating = clean_rating(rating)
    if not message and rating is None:
        raise ApiError("Напишіть коментар або поставте оцінку", 400, "validation_error")
    if len(message) > MAX_LENGTH:
        raise ApiError(f"Коментар завеликий (максимум {MAX_LENGTH} символів)",
                       400, "validation_error")
    if rating is not None:
        # Одна оцінка на користувача: попередня перестає рахуватися, а
        # коментар, до якого вона була прив'язана, лишається.
        (SkillFeedback.query
         .filter(_item_filter(item_type, item.id), SkillFeedback.user_id == user.id,
                 SkillFeedback.rating.isnot(None))
         .update({SkillFeedback.rating: None}, synchronize_session=False))
    link = {"skill_id": item.id} if item_type == "skill" else {"resource_id": item.id}
    fb = SkillFeedback(
        item_type=item_type, skill_name=item.name, skill_version=item.version,
        rating=rating, user_id=user.id, username=user.full_name or user.username,
        message=message, is_public=True, **link)
    db.session.add(fb)
    if message:
        review_service.notify_comment(item_type, item, user, message)
    return fb


def public_dict(fb, user):
    """Коментар у стрічці: лише те, що бачать усі, плюс право видалити."""
    mine = fb.user_id is not None and fb.user_id == user.id
    return {
        "id": fb.id,
        "author": fb.username or "Користувач",
        "message": fb.message,
        "rating": fb.rating,
        "created_at": fb.created_at.isoformat() if fb.created_at else None,
        "mine": mine,
        "can_delete": mine or review_service.is_manager(user),
    }


def thread(item_type, item_id, user):
    """Стрічка коментарів картки, середня оцінка й оцінка поточного користувача."""
    base = SkillFeedback.query.filter(_item_filter(item_type, item_id))
    comments = (base.filter(SkillFeedback.is_public.is_(True),
                            SkillFeedback.message != "",
                            SkillFeedback.message != _LEGACY_EMPTY)
                .order_by(SkillFeedback.created_at.asc(), SkillFeedback.id.asc()).all())
    rated = base.filter(SkillFeedback.rating.isnot(None))
    avg, count = (db.session.query(db.func.avg(SkillFeedback.rating),
                                   db.func.count(SkillFeedback.rating))
                  .filter(_item_filter(item_type, item_id),
                          SkillFeedback.rating.isnot(None)).one())
    mine = (rated.filter(SkillFeedback.user_id == user.id)
            .order_by(SkillFeedback.created_at.desc()).first())
    return {
        "items": [public_dict(c, user) for c in comments],
        "rating_avg": round(float(avg), 1) if avg is not None else None,
        "rating_count": int(count),
        "my_rating": mine.rating if mine else None,
    }


def remove(fb, user):
    """Видалення коментаря: автор коментаря або менеджер каталогу.

    Якщо до коментаря прив'язана оцінка, зникає лише текст — видалити
    коментар не означає забрати свою оцінку.
    """
    if not (fb.user_id == user.id or review_service.is_manager(user)):
        raise ApiError("Видалити коментар може його автор або менеджер каталогу",
                       403, "forbidden")
    if fb.rating is not None:
        fb.message = ""
    else:
        db.session.delete(fb)
