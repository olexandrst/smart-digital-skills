"""Тести фабрики LLM-провайдерів та реєстрації моделей з provider."""
from app.integrations import (
    normalize_provider, get_client_for_provider, get_client_for_model,
)
from app.integrations.azure_foundry import AzureFoundryClient
from app.integrations.openai_client import OpenAIClient
from app.integrations.gemini_client import GeminiClient
from app.models import Model
from tests.conftest import login, auth


def test_normalize_provider_aliases(app):
    assert normalize_provider("azure_openai") == "azure_ai_foundry"
    assert normalize_provider("AZURE") == "azure_ai_foundry"
    assert normalize_provider("google") == "gemini"
    assert normalize_provider("OpenAI") == "openai"
    assert normalize_provider(None) == "azure_ai_foundry"
    assert normalize_provider("unknown") == "azure_ai_foundry"


def test_factory_returns_correct_client(app):
    assert isinstance(get_client_for_provider("openai"), OpenAIClient)
    assert isinstance(get_client_for_provider("gemini"), GeminiClient)
    assert isinstance(get_client_for_provider("azure_openai"), AzureFoundryClient)


def test_clients_mock_when_enabled(app):
    # TestConfig успадковує LLM_MOCK=1 → усі клієнти у режимі моку.
    for provider in ("openai", "gemini", "azure_ai_foundry"):
        client = get_client_for_provider(provider)
        resp = client.complete("some-model", "привіт світ")
        assert "MOCK" in resp.content
        assert resp.total_tokens > 0


def test_get_client_for_model_uses_model_provider(app):
    m = Model(name="g", model_type="llm", provider="gemini", deployment_name="gemini-1.5-pro")
    assert isinstance(get_client_for_model(m), GeminiClient)


def test_create_model_with_provider(client):
    token = login(client, "admin", "Admin123!")
    res = client.post("/api/models", headers=auth(token), json={
        "name": "Gemini Pro", "provider": "google",
        "model_type": "llm", "deployment_name": "gemini-1.5-pro"})
    assert res.status_code == 201
    assert res.get_json()["provider"] == "gemini"  # синонім нормалізовано


def test_create_model_rejects_unknown_provider(client):
    token = login(client, "admin", "Admin123!")
    res = client.post("/api/models", headers=auth(token), json={
        "name": "X", "provider": "llama", "model_type": "llm",
        "deployment_name": "x"})
    assert res.status_code == 400


def test_providers_endpoint(client):
    token = login(client, "admin", "Admin123!")
    res = client.get("/api/models/providers", headers=auth(token))
    assert res.status_code == 200
    provs = res.get_json()
    assert set(provs) == {"azure_ai_foundry", "openai", "gemini"}
