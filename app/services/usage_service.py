"""Облік переглядів і сесій (FR-11) та агрегати для KPI (BR-16).

Сесія ведеться ліниво: кожна зафіксована дія або продовжує поточну сесію, або
(якщо минуло понад SESSION_IDLE_MINUTES) закриває її та відкриває нову. Фонового
процесу для закриття сесій не потрібно — «висяча» сесія однаково закривається
тією ж межею бездіяльності під час підрахунку тривалості.

Усі агрегати рахуються запитами з GROUP BY по колонці `day`, а не перебором
історії в Python: набір переглядів зростає лінійно з використанням хабу.
"""
from datetime import datetime, timedelta, date

from sqlalchemy import func, distinct

from app.extensions import db
from app.models import UserSession, ResourceView, SESSION_IDLE_MINUTES


def _today(now=None):
    return (now or datetime.utcnow()).date()


def touch_session(user, now=None):
    """Повертає активну сесію користувача, створюючи чи продовжуючи її.

    Сесія рветься після 30 хвилин бездіяльності: стару закриваємо моментом
    останньої активності, а не «зараз», щоб пауза не потрапила в тривалість.
    """
    now = now or datetime.utcnow()
    session = (UserSession.query
               .filter_by(user_id=user.id, ended_at=None)
               .order_by(UserSession.last_seen_at.desc()).first())

    if session is not None and session.is_expired(now):
        session.ended_at = session.last_seen_at
        session = None

    if session is None:
        session = UserSession(user_id=user.id, started_at=now, last_seen_at=now,
                              day=now.date())
        db.session.add(session)
        db.session.flush()
    else:
        session.last_seen_at = now
    return session


def record_view(user, target_type, target_id, target_name=None, now=None):
    """Фіксує перегляд і повертає запис (або None без користувача).

    Один матеріал за сесію рахується один раз. Інакше «відкрив картку й
    скопіював промпт» дало б два перегляди, а глибина перегляду за сесію
    рахувала б кліки замість матеріалів. Лічильник `opens_count` у картці
    така дедуплікація не зачіпає — він рахує саме дії.
    """
    if user is None:
        return None
    now = now or datetime.utcnow()
    session = touch_session(user, now)

    seen = ResourceView.query.filter_by(session_id=session.id,
                                        target_type=target_type,
                                        target_id=target_id).first()
    if seen is not None:
        return seen

    view = ResourceView(user_id=user.id, target_type=target_type,
                        target_id=target_id, target_name=target_name,
                        session_id=session.id, created_at=now, day=now.date())
    session.views_count = (session.views_count or 0) + 1
    db.session.add(view)
    return view


def close_stale_sessions(now=None):
    """Закриває сесії, які вже прострочені, щоб тривалість була чесною."""
    now = now or datetime.utcnow()
    cutoff = now - timedelta(minutes=SESSION_IDLE_MINUTES)
    stale = UserSession.query.filter(UserSession.ended_at.is_(None),
                                     UserSession.last_seen_at < cutoff).all()
    for session in stale:
        session.ended_at = session.last_seen_at
    if stale:
        db.session.commit()
    return len(stale)


# ------------------------------ Агрегати ------------------------------

def active_users(since, until=None):
    """Скільки різних користувачів мали активність у проміжку дат."""
    query = db.session.query(func.count(distinct(ResourceView.user_id))) \
        .filter(ResourceView.day >= since)
    if until is not None:
        query = query.filter(ResourceView.day <= until)
    return int(query.scalar() or 0)


def views_count(since, until=None):
    query = db.session.query(func.count(ResourceView.id)) \
        .filter(ResourceView.day >= since)
    if until is not None:
        query = query.filter(ResourceView.day <= until)
    return int(query.scalar() or 0)


def session_metrics(since, until=None):
    """Середня тривалість сесії (секунди) та глибина перегляду за сесію."""
    query = UserSession.query.filter(UserSession.day >= since)
    if until is not None:
        query = query.filter(UserSession.day <= until)
    sessions = query.all()
    if not sessions:
        return {"sessions": 0, "avg_duration_seconds": None, "avg_depth": None}
    durations = [s.duration_seconds() for s in sessions]
    depths = [s.views_count or 0 for s in sessions]
    return {
        "sessions": len(sessions),
        "avg_duration_seconds": round(sum(durations) / len(durations)),
        "avg_depth": round(sum(depths) / len(depths), 1),
    }


def returning_gap(days, now=None):
    """Скільки користувачів не мали активності понад `days` днів.

    Рахуємо за останнім переглядом кожного користувача: тих, хто взагалі
    ніколи нічого не відкривав, тут немає — вони не «перестали повертатися».
    """
    now = now or datetime.utcnow()
    cutoff = now.date() - timedelta(days=days)
    rows = (db.session.query(ResourceView.user_id, func.max(ResourceView.day))
            .filter(ResourceView.user_id.isnot(None))
            .group_by(ResourceView.user_id).all())
    return sum(1 for _uid, last_day in rows if last_day and last_day < cutoff)


def top_viewed(since, until=None, limit=10):
    """Топ матеріалів за кількістю переглядів у проміжку."""
    query = (db.session.query(ResourceView.target_type, ResourceView.target_id,
                              func.max(ResourceView.target_name),
                              func.count(ResourceView.id))
             .filter(ResourceView.day >= since))
    if until is not None:
        query = query.filter(ResourceView.day <= until)
    rows = (query.group_by(ResourceView.target_type, ResourceView.target_id)
            .order_by(func.count(ResourceView.id).desc()).limit(limit).all())
    return [{"target_type": r[0], "target_id": r[1], "name": r[2], "views": int(r[3])}
            for r in rows]


def daily_series(since, until=None):
    """Перегляди та унікальні користувачі по днях — для динаміки на дашборді."""
    query = (db.session.query(ResourceView.day, func.count(ResourceView.id),
                              func.count(distinct(ResourceView.user_id)))
             .filter(ResourceView.day >= since))
    if until is not None:
        query = query.filter(ResourceView.day <= until)
    rows = query.group_by(ResourceView.day).order_by(ResourceView.day).all()
    return [{"day": r[0].isoformat() if isinstance(r[0], date) else str(r[0]),
             "views": int(r[1]), "users": int(r[2])} for r in rows]
