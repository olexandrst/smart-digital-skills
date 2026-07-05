"""Динамічне отримання списку доступних моделей у провайдера (для Admin).

Дозволяє в UI обирати модель/деплоймент зі списку, який реально доступний за
заданим ключем/endpoint, а не вводити назву вручну.

У режимі моку (LLM_MOCK=1) або за відсутності ключа повертається курований
fallback-список, щоб функція була корисною й без реальних ключів.
"""
from flask import current_app

from app.integrations import normalize_provider
from app.integrations.base import build_http_client

# Курований запасний список (мок / відсутній ключ).
_FALLBACK = {
    "azure_ai_foundry": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini",
                          "o4-mini", "o3-mini"],
    "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini",
               "o4-mini", "o3-mini"],
    "gemini": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash",
               "gemini-1.5-pro", "gemini-1.5-flash"],
}
_CV_FALLBACK = {
    "azure_ai_foundry": ["gpt-4o", "gpt-4.1"],
    "openai": ["gpt-4o", "gpt-4.1"],
    "gemini": ["gemini-2.5-pro", "gemini-1.5-pro"],
}


def list_available_models(provider, model_type="llm"):
    """Повертає (models, source): список назв моделей та джерело ('api'|'fallback')."""
    cfg = current_app.config
    canonical = normalize_provider(provider)
    mock = cfg.get("LLM_MOCK", True)

    def fallback():
        table = _CV_FALLBACK if model_type == "cv" else _FALLBACK
        return table.get(canonical, []), "fallback"

    if canonical == "openai":
        key = cfg.get("OPENAI_API_KEY", "")
        if mock or not key:
            return fallback()
        return _openai_models(key, cfg.get("OPENAI_BASE_URL", "") or None), "api"

    if canonical == "gemini":
        key = cfg.get("GEMINI_API_KEY", "")
        if mock or not key:
            return fallback()
        return _gemini_models(key), "api"

    # Azure OpenAI
    endpoint = cfg.get("AZURE_FOUNDRY_ENDPOINT", "")
    key = cfg.get("AZURE_FOUNDRY_API_KEY", "")
    if mock or not (endpoint and key):
        return fallback()
    return _azure_models(endpoint, key,
                         cfg.get("AZURE_FOUNDRY_API_VERSION", "2024-02-15-preview")), "api"


def _openai_models(api_key, base_url):
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url, http_client=build_http_client())
    return sorted({m.id for m in client.models.list().data})


def _azure_models(endpoint, api_key, api_version):
    """Для Azure найкорисніші — назви деплойментів (саме їх треба у чаті).

    Спершу пробуємо data-plane список деплойментів, а якщо не вдалося —
    базові моделі ресурсу.
    """
    base = endpoint.rstrip("/")
    url = f"{base}/openai/deployments?api-version={api_version}"
    client = build_http_client()
    if client is not None:
        try:
            resp = client.get(url, headers={"api-key": api_key})
            resp.raise_for_status()
            data = resp.json().get("data", [])
            ids = sorted({d.get("id") for d in data if d.get("id")})
            if ids:
                return ids
        except Exception:
            pass  # тихий фолбек на базові моделі ресурсу
        finally:
            client.close()

    from openai import AzureOpenAI
    az = AzureOpenAI(azure_endpoint=endpoint, api_key=api_key,
                     api_version=api_version, http_client=build_http_client())
    return sorted({m.id for m in az.models.list().data})


def _gemini_models(api_key):
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    out = []
    for m in genai.list_models():
        methods = getattr(m, "supported_generation_methods", []) or []
        if "generateContent" in methods:
            out.append(m.name.split("/")[-1])
    return sorted(set(out))
