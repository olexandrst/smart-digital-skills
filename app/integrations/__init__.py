"""Фабрика LLM-клієнтів за провайдером моделі.

Підтримувані провайдери (поле models.provider):
  - azure_ai_foundry / azure_openai → Azure OpenAI
  - openai                          → OpenAI API
  - gemini / google                 → Google Gemini API

У режимі моку (LLM_MOCK=1) або за відсутності ключа повертається мок-клієнт,
тож застосунок працює без зовнішніх ключів.
"""
from flask import current_app

from app.integrations.azure_foundry import AzureFoundryClient
from app.integrations.openai_client import OpenAIClient
from app.integrations.gemini_client import GeminiClient

# Канонічні значення провайдерів та їх синоніми.
SUPPORTED_PROVIDERS = {
    "azure_ai_foundry": "azure_ai_foundry",
    "azure_openai": "azure_ai_foundry",
    "azure": "azure_ai_foundry",
    "openai": "openai",
    "gemini": "gemini",
    "google": "gemini",
}


def normalize_provider(provider):
    return SUPPORTED_PROVIDERS.get((provider or "azure_ai_foundry").lower().strip(),
                                   "azure_ai_foundry")


def get_client_for_provider(provider):
    cfg = current_app.config
    mock = cfg.get("LLM_MOCK", True)
    canonical = normalize_provider(provider)

    if canonical == "openai":
        return OpenAIClient(
            api_key=cfg.get("OPENAI_API_KEY", ""),
            base_url=cfg.get("OPENAI_BASE_URL", ""),
            use_mock=mock,
        )
    if canonical == "gemini":
        return GeminiClient(
            api_key=cfg.get("GEMINI_API_KEY", ""),
            use_mock=mock,
        )
    return AzureFoundryClient(
        endpoint=cfg.get("AZURE_FOUNDRY_ENDPOINT", ""),
        api_key=cfg.get("AZURE_FOUNDRY_API_KEY", ""),
        api_version=cfg.get("AZURE_FOUNDRY_API_VERSION", "2024-02-15-preview"),
        use_mock=mock,
    )


def get_client_for_model(model):
    """Повертає LLM-клієнт для конкретної моделі (за її provider)."""
    return get_client_for_provider(getattr(model, "provider", None))
