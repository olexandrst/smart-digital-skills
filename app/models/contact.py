"""Зворотний зв'язок: повідомлення адміністратору хабу (фідбек тестування, п. 21).

Нетехнічні питання до адміністратора. Технічні йдуть до AI SD агента в Teams,
тут їх не збираємо. Повідомлення зберігається в базі, а не лише в листі:
лист може загубитися, запис — ні. Ім'я й пошту автора знімаємо на момент
надсилання, тож повідомлення лишається зрозумілим і після видалення
користувача.
"""
from datetime import datetime
from app.extensions import db


def _now():
    return datetime.utcnow()


def _iso(value):
    return value.isoformat() if value else None


# new — щойно надіслане; done — адміністратор опрацював.
CONTACT_STATUSES = ("new", "done")


class ContactMessage(db.Model):
    __tablename__ = "contact_messages"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    author_name = db.Column(db.String)
    author_email = db.Column(db.String)
    message = db.Column(db.Text, nullable=False)
    # Сторінка хабу, з якої надіслано: що людина дивилася, коли питала.
    page = db.Column(db.String)
    status = db.Column(db.String, nullable=False, default="new")
    created_at = db.Column(db.DateTime, nullable=False, default=_now, index=True)
    handled_at = db.Column(db.DateTime)
    handled_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    handler = db.relationship("User", lazy="joined", foreign_keys=[handled_by])

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "author_name": self.author_name,
            "author_email": self.author_email,
            "message": self.message,
            "page": self.page,
            "status": self.status,
            "created_at": _iso(self.created_at),
            "handled_at": _iso(self.handled_at),
            "handled_by_name": ((self.handler.full_name or self.handler.username)
                                if self.handler else None),
        }
