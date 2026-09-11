"""Нагадування власникам контенту про перегляд матеріалів (AKH-18).

Власник отримує сповіщення за тиждень до дати наступного перегляду і в день,
коли вона минула. Повторів немає навіть за кількох процесів: кожне сповіщення
має `dedupe_key`, а унікальний індекс у базі робить дублікат неможливим —
покладатися на «планувальник запущений лише в одному воркері» не можна.
"""
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Notification, CatalogResource, User, PUBLIC_STATUSES, AppSetting

# За скільки днів до дати перегляду попереджати.
WARN_DAYS_BEFORE = 7

# Ключ налаштування: чи слати щотижневий підсумок на пошту.
EMAIL_DIGEST_KEY = "review_email_digest"


def _owner_users(owner_name):
    """Користувачі, яких вважаємо власником матеріалу.

    Поле `owner` — вільний текст (прізвище відповідального), тому зіставляємо
    його з іменем або логіном. Це нагадування, а не права доступу: хибний збіг
    тут коштує зайвого сповіщення, а не витоку.
    """
    if not owner_name:
        return []
    needle = owner_name.strip().casefold()
    return [u for u in User.query.filter_by(is_active=True).all()
            if needle in {(u.full_name or "").casefold(), (u.username or "").casefold()}]


def due_materials(now=None):
    """Матеріали, за якими час нагадати: (матеріал, привід, дата-ключ)."""
    now = now or datetime.utcnow()
    today = now.date()
    warn_until = today + timedelta(days=WARN_DAYS_BEFORE)

    rows = (CatalogResource.query
            .filter(CatalogResource.status.in_(PUBLIC_STATUSES))
            .filter(CatalogResource.next_review_at.isnot(None))
            .filter(CatalogResource.owner.isnot(None)).all())

    out = []
    for res in rows:
        due = res.next_review_at.date()
        if due < today:
            out.append((res, "review_overdue", due))
        elif due <= warn_until:
            out.append((res, "review_due", due))
    return out


def create_reminders(now=None):
    """Створює сповіщення власникам. Повертає кількість нових."""
    now = now or datetime.utcnow()
    created = 0
    for res, kind, due in due_materials(now):
        owners = _owner_users(res.owner)
        if not owners:
            continue
        key = f"{kind}:{res.id}:{due.isoformat()}"
        title = ("Перегляд прострочено" if kind == "review_overdue"
                 else "Скоро перегляд матеріалу")
        body = (f"«{res.name}» — дата перегляду {due.strftime('%d.%m.%Y')}. "
                f"Підтвердьте актуальність, позначте як «потребує оновлення» "
                f"або віддайте в архів.")
        for user in owners:
            exists = Notification.query.filter_by(user_id=user.id,
                                                  dedupe_key=key).first()
            if exists is not None:
                continue
            db.session.add(Notification(
                user_id=user.id, kind=kind, title=title, body=body,
                resource_id=res.id, dedupe_key=key, created_at=now))
            try:
                db.session.commit()
                created += 1
            except IntegrityError:
                # Інший процес встиг створити те саме сповіщення — це нормально.
                db.session.rollback()
    return created


def email_digest_enabled(user):
    """Чи хоче користувач щотижневий підсумок на пошту (типово — так)."""
    return AppSetting.get(f"{EMAIL_DIGEST_KEY}:{user.id}", "1") == "1"


def set_email_digest(user, enabled):
    AppSetting.set(f"{EMAIL_DIGEST_KEY}:{user.id}", "1" if enabled else "0")
    db.session.commit()
    return enabled


def weekly_digest(user, now=None):
    """Текст щотижневого підсумку для власника (None — якщо нема про що)."""
    now = now or datetime.utcnow()
    items = [(res, kind, due) for res, kind, due in due_materials(now)
             if user in _owner_users(res.owner)]
    if not items:
        return None
    overdue = [x for x in items if x[1] == "review_overdue"]
    soon = [x for x in items if x[1] == "review_due"]
    lines = [f"Матеріали, що потребують вашої уваги ({len(items)}):"]
    if overdue:
        lines.append("\nПерегляд прострочено:")
        lines += [f"  • {r.name} — з {d.strftime('%d.%m.%Y')}" for r, _k, d in overdue]
    if soon:
        lines.append("\nПерегляд найближчим часом:")
        lines += [f"  • {r.name} — до {d.strftime('%d.%m.%Y')}" for r, _k, d in soon]
    return "\n".join(lines)


def send_weekly_digests(now=None):
    """Формує підсумки для всіх власників, що їх не вимкнули.

    Надсилання пошти залежить від інфраструктури замовника, тому тут лише
    формуємо повідомлення й віддаємо їх — підключити SMTP можна не чіпаючи
    логіку відбору.
    """
    now = now or datetime.utcnow()
    owners = {u.id: u for _res, _kind, _due in due_materials(now)
              for u in _owner_users(_res.owner)}
    out = []
    for user in owners.values():
        if not email_digest_enabled(user) or not user.email:
            continue
        text = weekly_digest(user, now)
        if text:
            out.append({"email": user.email, "subject": "AI Knowledge Hub: "
                                                        "матеріали на перегляд",
                        "body": text})
    return out
