"""Тести тижневих квот: системний дефолт, персональна квота (пріоритет),
лічильник, енфорсмент і скидання."""
from datetime import datetime
from app.models import Model, User, TokenCounter
from app.services import quota_service
from tests.conftest import login, auth


def _uid(username):
    return User.query.filter_by(username=username).first().id


# ---------- Дефолт і статус ----------

def test_default_weekly_limit(client):
    token = login(client, "u1", "pass")
    q = client.get("/api/usage/me", headers=auth(token)).get_json()["quota"]
    assert q["limit"] == 2000        # DEFAULT_WEEKLY_TOKEN_LIMIT
    assert q["period"] == "weekly"
    assert q["custom"] is False
    assert q["used"] == 0
    assert "resets_at" in q


# ---------- Системна квота (Admin) ----------

def test_admin_sets_system_default(client):
    admin = login(client, "admin", "Admin123!")
    res = client.post("/api/usage/default-limit", headers=auth(admin), json={"limit": 5000})
    assert res.status_code == 200 and res.get_json()["limit"] == 5000

    # Користувач без персональної квоти бачить нову системну.
    token = login(client, "u1", "pass")
    assert client.get("/api/usage/me", headers=auth(token)).get_json()["quota"]["limit"] == 5000


def test_non_admin_cannot_set_system_default(client):
    token = login(client, "u1", "pass")
    assert client.post("/api/usage/default-limit", headers=auth(token),
                       json={"limit": 9}).status_code == 403


# ---------- Персональна квота: пріоритет / оновлення / видалення ----------

def test_custom_limit_overrides_system(client):
    admin = login(client, "admin", "Admin123!")
    uid = _uid("u1")
    client.post("/api/usage/default-limit", headers=auth(admin), json={"limit": 5000})
    res = client.post(f"/api/users/{uid}/token-limit", headers=auth(admin), json={"limit": 300})
    assert res.get_json()["custom_limit"] == 300

    token = login(client, "u1", "pass")
    q = client.get("/api/usage/me", headers=auth(token)).get_json()["quota"]
    assert q["limit"] == 300 and q["custom"] is True


def test_delete_custom_limit_falls_back(client):
    admin = login(client, "admin", "Admin123!")
    uid = _uid("u1")
    client.post(f"/api/users/{uid}/token-limit", headers=auth(admin), json={"limit": 300})
    res = client.delete(f"/api/users/{uid}/token-limit", headers=auth(admin))
    assert res.status_code == 200

    token = login(client, "u1", "pass")
    q = client.get("/api/usage/me", headers=auth(token)).get_json()["quota"]
    assert q["custom"] is False and q["limit"] == 2000


def test_users_list_shows_quota_fields(client):
    admin = login(client, "admin", "Admin123!")
    users = client.get("/api/users", headers=auth(admin)).get_json()
    u1 = next(u for u in users if u["username"] == "u1")
    assert u1["custom_limit"] is None
    assert u1["effective_limit"] == 2000
    assert "token_used" in u1


# ---------- Лічильник і енфорсмент ----------

def test_counter_grows_and_enforces(client):
    admin = login(client, "admin", "Admin123!")
    uid = _uid("u1")
    client.post(f"/api/users/{uid}/token-limit", headers=auth(admin), json={"limit": 1})

    token = login(client, "u1", "pass")
    mid = Model.query.filter_by(name="gpt-4o").first().id
    sid = client.post("/api/chat/sessions", headers=auth(token),
                      json={"model_id": mid}).get_json()["id"]
    # Перше — проходить і вичерпує квоту…
    assert client.post(f"/api/chat/sessions/{sid}/messages", headers=auth(token),
                       json={"content": "привіт"}).status_code == 200
    q = client.get("/api/usage/me", headers=auth(token)).get_json()["quota"]
    assert q["used"] > 0
    # …наступне блокується (429).
    assert client.post(f"/api/chat/sessions/{sid}/messages", headers=auth(token),
                       json={"content": "ще"}).status_code == 429


# ---------- Скидання ----------

def test_reset_all_zeroes_counter(app):
    uid = _uid("u1")
    quota_service.record_usage(uid, 500)
    assert quota_service.get_used(uid) == 500
    quota_service.reset_all()
    assert quota_service.get_used(uid) == 0


def test_lazy_reset_on_stale_period(app):
    uid = _uid("u1")
    quota_service.record_usage(uid, 500)
    # Імітуємо застарілий період (минулий тиждень) → лічильник має вважатись 0.
    c = TokenCounter.query.get(uid)
    c.period_start = datetime(2000, 1, 3, 0, 5)  # давній понеділок
    from app.extensions import db
    db.session.commit()
    assert quota_service.get_used(uid) == 0


def test_period_start_is_monday_0005(app):
    ps = quota_service.current_period_start(datetime(2026, 6, 14, 12, 0))  # неділя
    assert ps.weekday() == 0 and ps.hour == 0 and ps.minute == 5
    nxt = quota_service.next_reset_at(datetime(2026, 6, 14, 12, 0))
    assert nxt.weekday() == 0
