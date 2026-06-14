"""Тести лімітів токенів: дефолт, встановлення адміном, статус, енфорсмент."""
from app.models import Model
from tests.conftest import login, auth


def test_default_limit_in_usage(client):
    token = login(client, "u1", "pass")
    data = client.get("/api/usage/me", headers=auth(token)).get_json()
    assert "quota" in data
    assert data["quota"]["limit"] == 2000  # DEFAULT_USER_TOKEN_LIMIT
    assert data["quota"]["used"] == 0


def test_admin_sets_user_limit(client):
    admin = login(client, "admin", "Admin123!")
    # знайдемо id u1 зі списку
    users = client.get("/api/users", headers=auth(admin)).get_json()
    u1 = next(u for u in users if u["username"] == "u1")
    assert u1["token_limit"] == 2000

    res = client.post(f"/api/users/{u1['id']}/token-limit", headers=auth(admin),
                      json={"limit": 500})
    assert res.status_code == 200
    assert res.get_json()["token_limit"] == 500

    token = login(client, "u1", "pass")
    assert client.get("/api/usage/me", headers=auth(token)).get_json()["quota"]["limit"] == 500


def test_non_admin_cannot_set_limit(client):
    token = login(client, "u1", "pass")
    res = client.post("/api/users/1/token-limit", headers=auth(token), json={"limit": 10})
    assert res.status_code == 403


def test_quota_enforced_on_chat(client):
    admin = login(client, "admin", "Admin123!")
    users = client.get("/api/users", headers=auth(admin)).get_json()
    u1 = next(u for u in users if u["username"] == "u1")
    # дуже малий ліміт — наступний виклик моделі має блокуватись
    client.post(f"/api/users/{u1['id']}/token-limit", headers=auth(admin), json={"limit": 1})

    token = login(client, "u1", "pass")
    mid = Model.query.filter_by(name="gpt-4o").first().id
    sid = client.post("/api/chat/sessions", headers=auth(token),
                      json={"model_id": mid}).get_json()["id"]
    # Перше повідомлення проходить (used=0 < 1) і вичерпує квоту…
    first = client.post(f"/api/chat/sessions/{sid}/messages", headers=auth(token),
                        json={"content": "привіт"})
    assert first.status_code == 200
    # …наступне вже блокується.
    second = client.post(f"/api/chat/sessions/{sid}/messages", headers=auth(token),
                         json={"content": "ще раз"})
    assert second.status_code == 429


def test_quota_used_grows_after_use(client):
    token = login(client, "u1", "pass")
    mid = Model.query.filter_by(name="gpt-4o").first().id
    sid = client.post("/api/chat/sessions", headers=auth(token),
                      json={"model_id": mid}).get_json()["id"]
    client.post(f"/api/chat/sessions/{sid}/messages", headers=auth(token),
                json={"content": "порахуй токени"})
    q = client.get("/api/usage/me", headers=auth(token)).get_json()["quota"]
    assert q["used"] > 0
    assert q["percent"] > 0
