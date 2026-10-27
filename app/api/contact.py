"""Зворотний зв'язок з адміністратором хабу (фідбек тестування, п. 21).

Написати може будь-який користувач: автор, дата й сторінка підставляються
самі. Повідомлення зберігається в базі, адміністратори отримують сповіщення
в дзвіночку, а лист іде через app/services/mail_service.py (поки заглушка).
Список і статуси «нове» / «опрацьовано» бачить лише адміністратор.
"""
from datetime import datetime, timedelta

from flask import Blueprint, request, jsonify
from app.extensions import db
from app.core import access
from app.core.permissions import require_auth, require_admin
from app.core.security import current_user
from app.core.errors import ApiError
from app.models import ContactMessage, CONTACT_STATUSES, User
from app.services import mail_service, review_service

bp = Blueprint("contact", __name__)

MAX_MESSAGE = 5000
MAX_PAGE = 300
# Від однієї людини — не більше стількох повідомлень за годину: захист від
# випадкових повторних натискань і від засмічення скриньки.
HOURLY_LIMIT = 10
# Скільки символів повідомлення показувати в сповіщенні.
EXCERPT = 140


def _admins(except_user=None):
    return [u for u in User.query.filter_by(is_active=True).all()
            if access.is_admin(u) and (except_user is None or u.id != except_user.id)]


def _excerpt(text):
    text = " ".join(text.split())
    return text if len(text) <= EXCERPT else text[:EXCERPT].rstrip() + "…"


@bp.post("")
@require_auth
def send_message():
    data = request.get_json(silent=True) or {}
    user = current_user()
    text = (data.get("message") or "").strip()
    if not text:
        raise ApiError("Напишіть повідомлення", 400, "validation_error")
    if len(text) > MAX_MESSAGE:
        raise ApiError(f"Повідомлення завелике (максимум {MAX_MESSAGE} символів)",
                       400, "validation_error")
    page = " ".join((data.get("page") or "").split())[:MAX_PAGE] or None

    since = datetime.utcnow() - timedelta(hours=1)
    recent = (ContactMessage.query.filter_by(user_id=user.id)
              .filter(ContactMessage.created_at >= since).count())
    if recent >= HOURLY_LIMIT:
        raise ApiError("Забагато повідомлень за годину. Спробуйте пізніше",
                       429, "rate_limited")

    msg = ContactMessage(user_id=user.id, author_name=user.full_name or user.username,
                         author_email=user.email, message=text, page=page)
    db.session.add(msg)
    db.session.flush()

    who = msg.author_name
    admins = _admins(except_user=user)
    for admin in admins:
        review_service.push_notice(admin, "contact_message",
                                   "Нове повідомлення адміністратору",
                                   f"{who}: «{_excerpt(text)}»", f"contact:{msg.id}")
    db.session.commit()

    mail_service.send(
        [a.email for a in admins],
        f"AI Knowledge Hub: повідомлення від {who}",
        "\n".join(filter(None, [
            f"Від: {who}" + (f" <{msg.author_email}>" if msg.author_email else ""),
            f"Дата: {msg.created_at.strftime('%d.%m.%Y %H:%M')} (UTC)",
            f"Сторінка: {page}" if page else None,
            "",
            text,
        ])))
    return jsonify(msg.to_dict()), 201


@bp.get("")
@require_admin
def list_messages():
    """Повідомлення для адміністратора. `?status=new|done` — фільтр."""
    status = request.args.get("status")
    query = ContactMessage.query
    if status in CONTACT_STATUSES:
        query = query.filter_by(status=status)
    items = query.order_by(ContactMessage.created_at.desc(),
                           ContactMessage.id.desc()).limit(500).all()
    counts = dict(db.session.query(ContactMessage.status, db.func.count())
                  .group_by(ContactMessage.status).all())
    return jsonify({"items": [m.to_dict() for m in items],
                    "counts": {s: counts.get(s, 0) for s in CONTACT_STATUSES}})


@bp.patch("/<int:message_id>")
@require_admin
def update_message(message_id):
    """Позначити опрацьованим або повернути в нові."""
    msg = ContactMessage.query.get_or_404(message_id)
    data = request.get_json(silent=True) or {}
    status = data.get("status")
    if status not in CONTACT_STATUSES:
        raise ApiError("Статус має бути new або done", 400, "validation_error")
    if status != msg.status:
        msg.status = status
        if status == "done":
            msg.handled_at = datetime.utcnow()
            msg.handled_by = current_user().id
        else:
            msg.handled_at = None
            msg.handled_by = None
    db.session.commit()
    return jsonify(msg.to_dict())
