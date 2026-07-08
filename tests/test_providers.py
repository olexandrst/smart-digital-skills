"""Тести фабрики LLM-провайдерів та реєстрації моделей з provider.

Підтримуються лише Azure OpenAI та локальні (Ollama/LM Studio) моделі.
"""
from app.integrations import (
    normalize_provider, get_client_for_provider, get_client_for_model,
)
from app.integrations.azure_foundry import AzureFoundryClient
from app.integrations.openai_client import OpenAIClient
from app.models import Model
from tests.conftest import login, auth


def test_normalize_provider_aliases(app):
    assert normalize_provider("azure_openai") == "azure_ai_foundry"
    assert normalize_provider("AZURE") == "azure_ai_foundry"
    assert normalize_provider("ollama") == "local"
    assert normalize_provider("lm_studio") == "local"
    assert normalize_provider(None) == "azure_ai_foundry"
    assert normalize_provider("unknown") == "azure_ai_foundry"
    # Прибрані провайдери більше не розпізнаються → дефолт Azure.
    assert normalize_provider("openai") == "azure_ai_foundry"
    assert normalize_provider("gemini") == "azure_ai_foundry"


def test_factory_returns_correct_client(app):
    assert isinstance(get_client_for_provider("local"), OpenAIClient)
    assert isinstance(get_client_for_provider("azure_openai"), AzureFoundryClient)


def test_clients_mock_when_enabled(app):
    # TestConfig успадковує LLM_MOCK=1 → усі клієнти у режимі моку.
    for provider in ("local", "azure_ai_foundry"):
        client = get_client_for_provider(provider)
        resp = client.complete("some-model", "привіт світ")
        assert "MOCK" in resp.content
        assert resp.total_tokens > 0


def test_get_client_for_model_uses_model_provider(app):
    m = Model(name="l", model_type="llm", provider="local", deployment_name="llama3.1")
    assert isinstance(get_client_for_model(m), OpenAIClient)


def test_create_model_with_provider(client):
    token = login(client, "admin", "Admin123!")
    res = client.post("/api/models", headers=auth(token), json={
        "name": "Azure GPT", "provider": "azure",
        "model_type": "llm", "deployment_name": "gpt-4o"})
    assert res.status_code == 201
    assert res.get_json()["provider"] == "azure_ai_foundry"  # синонім нормалізовано


def test_create_model_rejects_removed_providers(client):
    token = login(client, "admin", "Admin123!")
    for prov in ("openai", "gemini", "google", "llama"):
        res = client.post("/api/models", headers=auth(token), json={
            "name": "X", "provider": prov, "model_type": "llm",
            "deployment_name": "x"})
        assert res.status_code == 400, prov


def test_providers_endpoint(client):
    token = login(client, "admin", "Admin123!")
    res = client.get("/api/models/providers", headers=auth(token))
    assert res.status_code == 200
    assert set(res.get_json()) == {"azure_ai_foundry", "local"}


def test_llm_timeout_default_and_override(monkeypatch):
    from app.integrations import base
    monkeypatch.delenv("LLM_TIMEOUT", raising=False)
    assert base.llm_timeout() == 1800.0            # 30 хв за замовчуванням
    monkeypatch.setenv("LLM_TIMEOUT", "45")
    assert base.llm_timeout() == 45.0
    monkeypatch.setenv("LLM_TIMEOUT", "bad")        # некоректне → дефолт
    assert base.llm_timeout() == 1800.0


def test_http_client_uses_long_timeout(monkeypatch):
    from app.integrations import base
    monkeypatch.delenv("LLM_TIMEOUT", raising=False)
    client = base.build_http_client()
    assert client is not None
    assert client.timeout.read == 1800.0
    # Перелік моделей — короткий таймаут.
    assert base.build_http_client(timeout=20.0).timeout.read == 20.0
