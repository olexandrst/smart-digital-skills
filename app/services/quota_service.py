"""QuotaService — тижневі квоти У ГРОШАХ (USD) та лічильники витрат.

Модель:
- Системна тижнева квота (scope_type='global') — діє для всіх; задає Admin.
  Якщо її немає в БД — береться DEFAULT_WEEKLY_MONEY_LIMIT ($1).
- Персональна квота користувача (scope_type='user') — опційна; якщо встановлена,
  має пріоритет над системною.
- Лічильник (token_counters.used_cost) — витрачені $ за поточний тиждень.
  Тиждень починається у понеділок 00:05 UTC; скидається щопонеділка.

Вартість запиту рахується з цін моделі (USD за 1M вхідних/вихідних токенів).
"""
from datetime import datetime, timedelta
from flask import current_app

from app.extensions import db
from app.core.errors import ApiError
from app.models import TokenLimit, TokenCounter

# Час тижневого скидання: понеділок 00:05 (UTC).
RESET_WEEKDAY = 0   # Monday
RESET_HOUR = 0
RESET_MINUTE = 5


def current_period_start(now=None):
    """Початок поточного тижневого періоду — найсвіжіший понеділок 00:05 (<= now)."""
    now = now or datetime.utcnow()
    monday = (now - timedelta(days=now.weekday())).replace(
        hour=RESET_HOUR, minute=RESET_MINUTE, second=0, microsecond=0)
    if now < monday:
        monday -= timedelta(days=7)
    return monday


def next_reset_at(now=None):
    return current_period_start(now) + timedelta(days=7)


# ----------------------------- Вартість -----------------------------

def compute_cost(model, prompt_tokens, completion_tokens):
    """Повертає (cost_in, cost_out, cost_total) у USD за цінами моделі."""
    pin = float(getattr(model, "price_in", None) or 0.0)
    pout = float(getattr(model, "price_out", None) or 0.0)
    cin = (int(prompt_tokens or 0) / 1_000_000.0) * pin
    cout = (int(completion_tokens or 0) / 1_000_000.0) * pout
    return cin, cout, cin + cout


# ----------------------------- Квоти -----------------------------

def system_default_limit():
    tl = TokenLimit.query.filter_by(scope_type="global", is_active=True).first()
    if tl and tl.limit_cost is not None:
        return float(tl.limit_cost)
    return float(current_app.config.get("DEFAULT_WEEKLY_MONEY_LIMIT", 1.0))


def set_system_default_limit(limit):
    limit = _validate_limit(limit)
    tl = TokenLimit.query.filter_by(scope_type="global").first()
    if tl:
        tl.limit_cost = limit
        tl.is_active = True
        tl.period = "weekly"
    else:
        db.session.add(TokenLimit(scope_type="global", scope_id=None, period="weekly",
                                  limit_tokens=0, limit_cost=limit, is_active=True))
    db.session.commit()
    return limit


def get_user_custom_limit(user_id):
    """Персональна квота ($) користувача або None."""
    tl = TokenLimit.query.filter_by(
        scope_type="user", scope_id=user_id, is_active=True).first()
    return float(tl.limit_cost) if tl and tl.limit_cost is not None else None


def set_user_limit(user_id, limit):
    limit = _validate_limit(limit)
    tl = TokenLimit.query.filter_by(scope_type="user", scope_id=user_id).first()
    if tl:
        tl.limit_cost = limit
        tl.is_active = True
        tl.period = "weekly"
    else:
        db.session.add(TokenLimit(scope_type="user", scope_id=user_id, period="weekly",
                                  limit_tokens=0, limit_cost=limit, is_active=True))
    db.session.commit()
    return limit


def delete_user_limit(user_id):
    """Видаляє персональну квоту — користувач повертається до системної."""
    TokenLimit.query.filter_by(scope_type="user", scope_id=user_id).delete()
    db.session.commit()


def effective_limit(user_id):
    custom = get_user_custom_limit(user_id)
    return custom if custom is not None else system_default_limit()


def _validate_limit(limit):
    try:
        limit = float(limit)
    except (TypeError, ValueError):
        raise ApiError("Ліміт має бути числом (USD)", 400, "validation_error")
    if limit < 0:
        raise ApiError("Ліміт не може бути відʼємним", 400, "validation_error")
    return limit


# ----------------------------- Лічильник -----------------------------

def _counter(user_id, create=False):
    c = TokenCounter.query.get(user_id)
    if c is None and create:
        c = TokenCounter(user_id=user_id, used_cost=0.0,
                         period_start=current_period_start())
        db.session.add(c)
    return c


def _apply_lazy_reset(counter, period_start):
    """Скидає лічильник, якщо його період застарів (зсунулось тижневе вікно)."""
    if counter.period_start is None or counter.period_start < period_start:
        counter.used_cost = 0.0
        counter.period_start = period_start


def get_used(user_id):
    """Витрачено $ за поточний тиждень."""
    c = _counter(user_id)
    if c is None:
        return 0.0
    if c.period_start is None or c.period_start < current_period_start():
        return 0.0  # період застарів → лічильник вважається обнуленим
    return float(c.used_cost or 0.0)


def record_usage(user_id, cost):
    """Додає витрачені $ до тижневого лічильника користувача."""
    cost = float(cost or 0.0)
    if cost <= 0:
        return
    period_start = current_period_start()
    c = _counter(user_id, create=True)
    _apply_lazy_reset(c, period_start)
    c.used_cost = float(c.used_cost or 0.0) + cost
    db.session.commit()


def reset_all():
    """Скидає лічильники всіх користувачів (виклик планувальником щопонеділка)."""
    period_start = current_period_start()
    TokenCounter.query.update(
        {TokenCounter.used_cost: 0.0, TokenCounter.period_start: period_start},
        synchronize_session=False)
    db.session.commit()


# ----------------------------- Статус / енфорсмент -----------------------------

def status(user_id):
    used = get_used(user_id)
    limit = effective_limit(user_id)
    custom = get_user_custom_limit(user_id)
    percent = round(min(100.0, used * 100.0 / limit), 1) if limit > 0 else 100.0
    return {
        "used": round(used, 6),
        "limit": limit,
        "remaining": max(0.0, round(limit - used, 6)),
        "percent": percent,
        "currency": "USD",
        "period": "weekly",
        "custom": custom is not None,
        "resets_at": next_reset_at().isoformat() + "Z",
    }


def ensure_within_limit(user):
    """Кидає 429, якщо тижневу квоту (у грошах) вичерпано."""
    used = get_used(user.id)
    limit = effective_limit(user.id)
    if limit > 0 and used >= limit:
        raise ApiError(
            f"Вичерпано тижневу квоту (${used:.4f} / ${limit:.2f}). "
            "Скидання — у понеділок. Зверніться до адміністратора для збільшення.",
            429, "quota_exceeded")
