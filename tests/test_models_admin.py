"""Тести керування каталогом моделей (Admin): активація та видалення."""
from app.extensions import db
from app.models import Model, Skill
from tests.conftest import login, auth


def test_activate_endpoint(client):
    token = login(client, "admin", "Admin123!")
    mid = Model.query.filter_by(name="gpt-4o").first().id
    res = client.post(f"/api/models/{mid}/activate", headers=auth(token),
                      json={"is_active": False})
    assert res.status_code == 200
    assert res.get_json()["is_active"] is False


def test_delete_blocked_when_skill_uses_model(client):
    # gpt-4o використовується скілом Summarizer → видалення заборонено.
    token = login(client, "admin", "Admin123!")
    mid = Model.query.filter_by(name="gpt-4o").first().id
    res = client.delete(f"/api/models/{mid}", headers=auth(token))
    assert res.status_code == 409


def test_delete_unused_model(client, app):
    token = login(client, "admin", "Admin123!")
    res = client.post("/api/models", headers=auth(token), json={
        "name": "Temp", "provider": "openai",
        "model_type": "llm", "deployment_name": "temp"})
    mid = res.get_json()["id"]
    res = client.delete(f"/api/models/{mid}", headers=auth(token))
    assert res.status_code == 200
    assert Model.query.get(mid) is None


def test_non_admin_cannot_delete(client):
    token = login(client, "u1", "pass")
    mid = Model.query.filter_by(name="gpt-4o").first().id
    res = client.delete(f"/api/models/{mid}", headers=auth(token))
    assert res.status_code == 403
