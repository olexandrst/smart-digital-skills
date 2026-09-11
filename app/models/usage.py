"""Облік роботи користувачів із базою знань (FR-11, BR-16).

Джерело даних для метрик розділу 11 BRD: DAU/WAU/MAU, тривалість і глибина
сесії, перегляди карток. Записи навмисно «плоскі» й нормалізовані за датою
(`day`), щоб агрегати за періодами рахувалися одним GROUP BY, а не перебором
історії в Python.
"""
from datetime import datetime, timedelta
from app.extensions import db


def _now():
    return datetime.utcnow()


def _iso(value):
    return value.isoformat() if value else None


# Сесія вважається завершеною після 30 хвилин без активності.
SESSION_IDLE_MINUTES = 30

# Що саме переглянули: картку каталогу, навичку, розділ або колекцію.
VIEW_TARGETS = ("resource", "skill", "section", "folder")


class UserSession(db.Model):
    """Сесія роботи в хабі: від першої дії до 30 хвилин бездіяльності.

    `ended_at` заповнюється не таймером, а лінивo — наступною дією користувача
    або запитом аналітики. Фонового процесу для цього не потрібно, а числа
    виходять ті самі.
    """
    __tablename__ = "user_sessions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    started_at = db.Column(db.DateTime, nullable=False, default=_now)
    last_seen_at = db.Column(db.DateTime, nullable=False, default=_now)
    ended_at = db.Column(db.DateTime)
    # Скільки матеріалів відкрито за сесію — глибина перегляду.
    views_count = db.Column(db.Integer, nullable=False, default=0)
    # Дата початку сесії окремою колонкою: агрегати за днями без функцій по даті.
    day = db.Column(db.Date, nullable=False, index=True)

    def is_expired(self, now=None):
        now = now or datetime.utcnow()
        return (now - self.last_seen_at) > timedelta(minutes=SESSION_IDLE_MINUTES)

    def duration_seconds(self):
        end = self.ended_at or self.last_seen_at
        return max(0, int((end - self.started_at).total_seconds()))

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "started_at": _iso(self.started_at),
            "ended_at": _iso(self.ended_at),
            "duration_seconds": self.duration_seconds(),
            "views_count": self.views_count,
        }


class ResourceView(db.Model):
    """Факт перегляду: хто, що і коли відкрив.

    Окремо від `CatalogResource.opens_count`: лічильник у картці лишається як
    був (він живить сортування «за популярністю»), а ця таблиця дає розрізи за
    користувачами й періодами, яких лічильник дати не може.
    """
    __tablename__ = "resource_views"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"),
                        index=True)
    target_type = db.Column(db.String, nullable=False, default="resource")  # VIEW_TARGETS
    target_id = db.Column(db.Integer, nullable=False)
    target_name = db.Column(db.String)        # знімок назви на момент перегляду
    session_id = db.Column(db.Integer, db.ForeignKey("user_sessions.id",
                                                     ondelete="SET NULL"))
    created_at = db.Column(db.DateTime, nullable=False, default=_now)
    day = db.Column(db.Date, nullable=False, index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "target_name": self.target_name,
            "created_at": _iso(self.created_at),
        }
