"""Фабрика LLM-клієнтів за провайдером моделі.

Підтримувані провайдери (поле models.provider):
  - azure_ai_foundry / azure_openai → Azure OpenAI
  - local (Ollama / LM Studio / OpenAI-сумісний сервер)

У режимі моку (LLM_MOCK=1) або за відсутності ключа повертається мок-клієнт,
тож застосунок працює без зовнішніх ключів.
"""
from flask import current_app

from app.integrations.azure_foundry import AzureFoundryClient
from app.integrations.openai_client import OpenAIClient

# Канонічні значення провайдерів та їх синоніми.
SUPPORTED_PROVIDERS = {
    "azure_ai_foundry": "azure_ai_foundry",
    "azure_openai": "azure_ai_foundry",
    "azure": "azure_ai_foundry",
    # Локальні / OpenAI-сумісні сервери (локально чи віддалено).
    "local": "local",
    "ollama": "local",
    "lmstudio": "local",
    "lm_studio": "local",
    "openai_compatible": "local",
}


def normalize_provider(provider):
    return SUPPORTED_PROVIDERS.get((provider or "azure_ai_foundry").lower().strip(),
                                   "azure_ai_foundry")


def get_client_for_provider(provider, base_url=None):
    """LLM-клієнт за провайдером. `base_url` перекриває дефолт (локальні/OpenAI)."""
    cfg = current_app.config
    mock = cfg.get("LLM_MOCK", True)
    canonical = normalize_provider(provider)

    if canonical == "local":
        return OpenAIClient(
            api_key=cfg.get("LOCAL_API_KEY", "local") or "local",
            base_url=(base_url or cfg.get("LOCAL_BASE_URL", "http://localhost:11434/v1")),
            use_mock=mock, label="Локальна модель",
        )
    return AzureFoundryClient(
        endpoint=cfg.get("AZURE_FOUNDRY_ENDPOINT", ""),
        api_key=cfg.get("AZURE_FOUNDRY_API_KEY", ""),
        api_version=cfg.get("AZURE_FOUNDRY_API_VERSION", "2024-02-15-preview"),
        use_mock=mock,
    )


def get_client_for_model(model):
    """Повертає LLM-клієнт для конкретної моделі (за її provider та base_url)."""
    return get_client_for_provider(getattr(model, "provider", None),
                                   base_url=getattr(model, "base_url", None) or None)
