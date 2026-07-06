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


# ---------- Динамічний список доступних моделей ----------

def test_available_models_fallback_in_mock(client):
    token = login(client, "admin", "Admin123!")
    for provider in ("openai", "azure_ai_foundry", "gemini"):
        res = client.get(f"/api/models/available?provider={provider}", headers=auth(token))
        assert res.status_code == 200
        d = res.get_json()
        assert d["provider"] == ("azure_ai_foundry" if provider == "azure_ai_foundry" else provider)
        assert d["source"] == "fallback"  # мок → курований список
        assert isinstance(d["models"], list) and d["models"]


def test_available_models_synonym_provider(client):
    token = login(client, "admin", "Admin123!")
    res = client.get("/api/models/available?provider=azure", headers=auth(token))
    assert res.status_code == 200
    assert res.get_json()["provider"] == "azure_ai_foundry"


def test_available_models_unknown_provider(client):
    token = login(client, "admin", "Admin123!")
    res = client.get("/api/models/available?provider=bogus", headers=auth(token))
    assert res.status_code == 400


def test_available_models_requires_admin(client):
    token = login(client, "u1", "pass")
    res = client.get("/api/models/available?provider=openai", headers=auth(token))
    assert res.status_code == 403
