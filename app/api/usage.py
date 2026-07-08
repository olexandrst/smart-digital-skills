"""Облік токенів і грошей: власні (усі), групи, глобально (Admin), аналітика."""
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request
from sqlalchemy import func
from app.extensions import db
from app.core.permissions import require_auth, require_global_role, require_group_membership
from app.core.security import current_user
from app.core.errors import ApiError
from app.models import TokenUsageLog, User, Model, Skill, Group
from app.services import quota_service

bp = Blueprint("usage", __name__)


def _aggregate(query):
    row = query.with_entities(
        func.coalesce(func.sum(TokenUsageLog.prompt_tokens), 0),
        func.coalesce(func.sum(TokenUsageLog.completion_tokens), 0),
        func.coalesce(func.sum(TokenUsageLog.total_tokens), 0),
        func.coalesce(func.sum(TokenUsageLog.cost_in), 0.0),
        func.coalesce(func.sum(TokenUsageLog.cost_out), 0.0),
        func.coalesce(func.sum(TokenUsageLog.cost_total), 0.0),
        func.count(TokenUsageLog.id),
    ).one()
    return {
        "prompt_tokens": int(row[0]),
        "completion_tokens": int(row[1]),
        "total_tokens": int(row[2]),
        "cost_in": float(row[3]),
        "cost_out": float(row[4]),
        "cost_total": float(row[5]),
        "requests": int(row[6]),
    }


def _parse_dt(value, end=False):
    """Парсить дату 'YYYY-MM-DD' (або ISO). end=True → кінець дня."""
    if not value:
        return None
    try:
        if len(value) == 10:
            d = datetime.strptime(value, "%Y-%m-%d")
            return d + timedelta(days=1) - timedelta(microseconds=1) if end else d
        return datetime.fromisoformat(value.replace("Z", ""))
    except ValueError:
        raise ApiError("Невірний формат дати (очікується YYYY-MM-DD)",
                       400, "validation_error")


def _user_logs(user_id):
    return TokenUsageLog.query.filter_by(user_id=user_id, is_system=False)


def _scope_money_logs(user):
    """Базова вибірка логів для «Гроші» + опис області.

    Звичайний користувач бачить лише власну статистику. Admin може дивитись
    статистику будь-якого користувача (`?user_id=`) або цілої групи
    (`?group_id=` — сума по активних учасниках). За замовчуванням — сам
    поточний користувач.
    """
    is_admin = user.has_global_role("admin")
    uid = request.args.get("user_id", type=int)
    gid = request.args.get("group_id", type=int)

    if not is_admin:
        if gid or (uid and uid != user.id):
            raise ApiError("Недостатньо прав для перегляду чужої статистики",
                           403, "forbidden")
        return (_user_logs(user.id),
                {"type": "user", "user_id": user.id, "username": user.username})

    if gid:
        group = Group.query.get(gid)
        if group is None:
            raise ApiError("Групу не знайдено", 404, "not_found")
        member_ids = [m.user_id for m in group.memberships if m.status == "active"]
        base = TokenUsageLog.query.filter(
            TokenUsageLog.is_system == False,  # noqa: E712
            TokenUsageLog.user_id.in_(member_ids))
        return base, {"type": "group", "group_id": group.id, "group": group.name,
                      "members": len(member_ids)}

    target = User.query.get(uid) if uid else user
    if target is None:
        raise ApiError("Користувача не знайдено", 404, "not_found")
    return (_user_logs(target.id),
            {"type": "user", "user_id": target.id, "username": target.username})


@bp.get("/scope-options")
@require_global_role("admin")
def scope_options():
    """Списки користувачів і груп для перемикача статистики «Гроші» (Admin)."""
    users = User.query.order_by(User.username).all()
    groups = Group.query.order_by(Group.name).all()
    return jsonify({
        "users": [{"id": u.id, "username": u.username, "full_name": u.full_name}
                  for u in users],
        "groups": [{"id": g.id, "name": g.name} for g in groups],
    })


@bp.get("/me")
@require_auth
def my_usage():
    user = current_user()
    data = _aggregate(_user_logs(user.id))
    data["quota"] = quota_service.status(user.id)
    return jsonify(data)


@bp.get("/timeline")
@require_auth
def timeline():
    """Часова шкала використання токенів (для діаграми).

    Період: `?from=&to=` (YYYY-MM-DD); за замовчуванням — від першого до
    останнього використання. Гранулярність масштабується автоматично.
    Повертає бакети {t, prompt_tokens, completion_tokens, total_tokens, cost_total}.
    """
    user = current_user()
    q = _user_logs(user.id)
    dfrom, dto = _parse_dt(request.args.get("from")), _parse_dt(request.args.get("to"), end=True)
    if dfrom:
        q = q.filter(TokenUsageLog.created_at >= dfrom)
    if dto:
        q = q.filter(TokenUsageLog.created_at <= dto)
    rows = q.order_by(TokenUsageLog.created_at).all()
    if not rows:
        return jsonify({"buckets": [], "granularity": None, "from": None, "to": None})

    first, last = rows[0].created_at, rows[-1].created_at
    gran = _pick_granularity(last - first)
    buckets = {}
    for r in rows:
        key = _bucket_key(r.created_at, gran)
        b = buckets.setdefault(key, {"prompt_tokens": 0, "completion_tokens": 0,
                                     "total_tokens": 0, "cost_total": 0.0})
        b["prompt_tokens"] += r.prompt_tokens or 0
        b["completion_tokens"] += r.completion_tokens or 0
        b["total_tokens"] += r.total_tokens or 0
        b["cost_total"] += r.cost_total or 0.0

    series = [dict(t=_iso(k), **v) for k, v in _fill_buckets(buckets, first, last, gran)]
    return jsonify({"buckets": series, "granularity": gran,
                    "from": _iso(first), "to": _iso(last)})


@bp.get("/money")
@require_auth
def money():
    """Витрати ($): усього, за період, у розрізі моделей і навичок.

    Період: `?from=&to=`; за замовчуванням — уся активність.
    Admin додатково: `?user_id=` (інший користувач) або `?group_id=`
    (сума по учасниках групи). Звичайний користувач — лише власні дані.
    """
    user = current_user()
    base, scope = _scope_money_logs(user)
    # Межі всієї активності (для дефолтного періоду в UI).
    span = base.with_entities(
        func.min(TokenUsageLog.created_at), func.max(TokenUsageLog.created_at)).one()

    q = base
    dfrom, dto = _parse_dt(request.args.get("from")), _parse_dt(request.args.get("to"), end=True)
    if dfrom:
        q = q.filter(TokenUsageLog.created_at >= dfrom)
    if dto:
        q = q.filter(TokenUsageLog.created_at <= dto)

    agg = _aggregate(q)
    rows = (q.with_entities(
                TokenUsageLog.model_id,
                func.coalesce(func.sum(TokenUsageLog.cost_total), 0.0),
                func.coalesce(func.sum(TokenUsageLog.total_tokens), 0),
                func.count(TokenUsageLog.id))
            .group_by(TokenUsageLog.model_id).all())
    model_names = {m.id: m.name for m in Model.query.all()}
    by_model = sorted(
        [{"model_id": r[0], "model": model_names.get(r[0], "—"),
          "cost": float(r[1]), "total_tokens": int(r[2]), "requests": int(r[3])}
         for r in rows],
        key=lambda x: x["cost"], reverse=True)

    # Розподіл вартості по НАВИЧКАХ (лише запуски з застосованою навичкою).
    srows = (q.filter(TokenUsageLog.skill_id.isnot(None))
             .with_entities(TokenUsageLog.skill_id,
                            func.coalesce(func.sum(TokenUsageLog.cost_total), 0.0),
                            func.count(TokenUsageLog.id))
             .group_by(TokenUsageLog.skill_id).all())
    skill_names = {s.id: s.name for s in Skill.query.all()}
    by_skill = sorted(
        [{"skill_id": r[0], "skill": skill_names.get(r[0], "—"),
          "cost": float(r[1]), "runs": int(r[2]),
          "avg": (float(r[1]) / r[2]) if r[2] else 0.0}
         for r in srows],
        key=lambda x: x["cost"], reverse=True)

    return jsonify({
        "cost_in": agg["cost_in"], "cost_out": agg["cost_out"],
        "cost_total": agg["cost_total"], "requests": agg["requests"],
        "total_tokens": agg["total_tokens"],
        "by_model": by_model,
        "by_skill": by_skill,
        "scope": scope,
        "activity_from": _iso(span[0]) if span[0] else None,
        "activity_to": _iso(span[1]) if span[1] else None,
    })


# ----------------------- Хелпери часової шкали -----------------------

def _iso(dt):
    return dt.isoformat() if dt else None


def _pick_granularity(span):
    if span <= timedelta(days=2):
        return "hour"
    if span <= timedelta(days=120):
        return "day"
    return "week"


def _bucket_key(dt, gran):
    if gran == "hour":
        return dt.replace(minute=0, second=0, microsecond=0)
    day = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if gran == "week":
        return day - timedelta(days=day.weekday())
    return day


def _bucket_step(gran):
    return {"hour": timedelta(hours=1), "day": timedelta(days=1),
            "week": timedelta(weeks=1)}[gran]


def _fill_buckets(buckets, first, last, gran):
    """Заповнює порожні бакети нулями від first до last (безперервна шкала)."""
    step = _bucket_step(gran)
    cur, end = _bucket_key(first, gran), _bucket_key(last, gran)
    empty = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_total": 0.0}
    out = []
    for _ in range(2000):  # запобіжник від нескінченного циклу
        out.append((cur, buckets.get(cur, dict(empty))))
        if cur >= end:
            break
        cur = cur + step
    return out


@bp.get("/default-limit")
@require_global_role("admin")
def get_default_limit():
    """Системна тижнева квота (USD) за замовчуванням."""
    return jsonify({"limit": quota_service.system_default_limit(),
                    "currency": "USD", "period": "weekly"})


@bp.post("/default-limit")
@require_global_role("admin")
def set_default_limit():
    data = request.get_json(silent=True) or {}
    if "limit" not in data:
        raise ApiError("Вкажіть limit", 400, "validation_error")
    limit = quota_service.set_system_default_limit(data["limit"])
    return jsonify({"limit": limit, "currency": "USD", "period": "weekly"})


@bp.get("/group/<int:group_id>")
@require_group_membership()
def group_usage(group_id):
    q = TokenUsageLog.query.filter_by(group_id=group_id, is_system=False)
    return jsonify(_aggregate(q))


@bp.get("/global")
@require_global_role("admin")
def global_usage():
    total = _aggregate(TokenUsageLog.query.filter_by(is_system=False))
    rows = (
        db.session.query(
            User.username,
            func.coalesce(func.sum(TokenUsageLog.total_tokens), 0),
            func.coalesce(func.sum(TokenUsageLog.cost_total), 0.0),
            func.count(TokenUsageLog.id),
        )
        .join(TokenUsageLog, TokenUsageLog.user_id == User.id)
        .filter(TokenUsageLog.is_system == False)  # noqa: E712
        .group_by(User.id)
        .all()
    )
    total["by_user"] = [
        {"username": r[0], "total_tokens": int(r[1]),
         "cost_total": float(r[2]), "requests": int(r[3])}
        for r in rows
    ]
    return jsonify(total)


@bp.get("/system")
@require_global_role("admin")
def system_usage():
    """Окремий облік СИСТЕМНОГО використання токенів: по моделях та фічах."""
    total = _aggregate(TokenUsageLog.query.filter_by(is_system=True))
    rows = (
        db.session.query(
            Model.name,
            TokenUsageLog.feature,
            func.coalesce(func.sum(TokenUsageLog.prompt_tokens), 0),
            func.coalesce(func.sum(TokenUsageLog.completion_tokens), 0),
            func.coalesce(func.sum(TokenUsageLog.total_tokens), 0),
            func.count(TokenUsageLog.id),
        )
        .outerjoin(Model, TokenUsageLog.model_id == Model.id)
        .filter(TokenUsageLog.is_system == True)  # noqa: E712
        .group_by(Model.name, TokenUsageLog.feature)
        .all()
    )
    total["breakdown"] = [
        {"model": r[0] or "—", "feature": r[1],
         "prompt_tokens": int(r[2]), "completion_tokens": int(r[3]),
         "total_tokens": int(r[4]), "requests": int(r[5])}
        for r in rows
    ]
    return jsonify(total)
