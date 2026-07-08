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


def test_azure_resource_base_normalizes_full_path():
    """Повний URL запиту зводиться до кореня ресурсу (щоб не подвоювати шлях)."""
    from app.integrations.base import azure_resource_base as f
    root = "https://mih-we-cnt-iuic-d-oai-01.openai.azure.com"
    assert f(root + "/openai/v1/chat/completions") == root
    assert f(root + "/") == root
    assert f(root) == root
    assert f("mih.openai.azure.com/openai/v1") == "https://mih.openai.azure.com"
    assert f("") == ""


# ---------- Локальні моделі (Ollama / LM Studio) + прибраний тип ----------

def test_local_provider_available_and_synonyms(client):
    token = login(client, "admin", "Admin123!")
    for prov in ("local", "ollama", "lmstudio"):
        res = client.get(f"/api/models/available?provider={prov}", headers=auth(token))
        assert res.status_code == 200
        d = res.get_json()
        assert d["provider"] == "local"
        assert isinstance(d["models"], list) and d["models"]


def test_create_local_model_with_base_url(client):
    token = login(client, "admin", "Admin123!")
    res = client.post("/api/models", headers=auth(token), json={
        "name": "Local Llama", "provider": "ollama",
        "deployment_name": "llama3.1", "base_url": "http://localhost:11434/v1"})
    assert res.status_code == 201
    d = res.get_json()
    assert d["provider"] == "local"
    assert d["base_url"] == "http://localhost:11434/v1"
    assert d["model_type"] == "llm"

    mdl = Model.query.get(d["id"])
    from app.integrations import get_client_for_model
    client_obj = get_client_for_model(mdl)  # має піднятися без помилок
    assert client_obj is not None


def test_create_model_without_type_defaults_llm(client):
    token = login(client, "admin", "Admin123!")
    res = client.post("/api/models", headers=auth(token), json={
        "name": "NoType", "provider": "openai", "deployment_name": "gpt-4o"})
    assert res.status_code == 201
    assert res.get_json()["model_type"] == "llm"


def test_providers_include_local(client):
    token = login(client, "admin", "Admin123!")
    provs = client.get("/api/models/providers", headers=auth(token)).get_json()
    assert "local" in provs


def test_azure_v1_base_builds_v1_surface():
    """Azure v1 (New Foundry): базовий URL із трейлінг-слешем для OpenAI SDK."""
    from app.integrations.base import azure_v1_base
    host = "https://mih-we-cnt-iuic-d-oai-01.openai.azure.com"
    assert azure_v1_base(host + "/openai/v1") == host + "/openai/v1/"
    assert azure_v1_base(host + "/openai/v1/chat/completions") == host + "/openai/v1/"
    assert azure_v1_base(host) == host + "/openai/v1/"
    assert azure_v1_base("") == ""


def test_azure_chat_uses_v1_url(app):
    """Реальний Azure-чат б'є саме у v1 URL (перевіряємо побудову запиту)."""
    from openai import OpenAI
    from openai._models import FinalRequestOptions
    from app.integrations.base import azure_v1_base, build_http_client
    with app.app_context():
        base = azure_v1_base("https://res.openai.azure.com/openai/v1")
        c = OpenAI(base_url=base, api_key="k", http_client=build_http_client())
        req = c._build_request(FinalRequestOptions.construct(
            method="post", url="/chat/completions", json_data={"model": "gpt-4o"}))
        assert str(req.url) == "https://res.openai.azure.com/openai/v1/chat/completions"
