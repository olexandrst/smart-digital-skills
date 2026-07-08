"""Динамічне отримання списку доступних моделей у провайдера (для Admin).

Дозволяє в UI обирати модель/деплоймент зі списку, який реально доступний за
заданим ключем/endpoint, а не вводити назву вручну.

У режимі моку (LLM_MOCK=1) або за відсутності ключа повертається курований
fallback-список, щоб функція була корисною й без реальних ключів.
"""
from flask import current_app

from app.integrations import normalize_provider
from app.integrations.base import build_http_client, azure_resource_base

# Курований запасний список (мок / відсутній ключ / недоступний сервер).
_FALLBACK = {
    "azure_ai_foundry": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini",
                          "o4-mini", "o3-mini"],
    "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini",
               "o4-mini", "o3-mini"],
    "gemini": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash",
               "gemini-1.5-pro", "gemini-1.5-flash"],
    "local": ["llama3.1", "llama3.2", "qwen2.5", "mistral", "phi3", "gemma2"],
}


def list_available_models(provider, model_type="llm", base_url=None):
    """Повертає (models, source): список назв моделей та джерело ('api'|'fallback').

    `base_url` — для локальних/OpenAI-сумісних серверів (Ollama, LM Studio).
    """
    cfg = current_app.config
    canonical = normalize_provider(provider)
    mock = cfg.get("LLM_MOCK", True)

    def fallback():
        return _FALLBACK.get(canonical, []), "fallback"

    if canonical == "local":
        url = base_url or cfg.get("LOCAL_BASE_URL", "")
        if mock or not url:
            return fallback()
        return _openai_models(cfg.get("LOCAL_API_KEY", "local") or "local", url), "api"

    if canonical == "openai":
        key = cfg.get("OPENAI_API_KEY", "")
        if mock or not key:
            return fallback()
        return _openai_models(key, base_url or cfg.get("OPENAI_BASE_URL", "") or None), "api"

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


# Авторинг-версії data-plane, що повертають список ДЕПЛОЙМЕНТІВ (не весь каталог).
_AZURE_DEPLOYMENT_API_VERSIONS = [
    "2024-05-01-preview", "2023-10-01-preview", "2023-03-15-preview", "2022-12-01",
]


def _azure_models(endpoint, api_key, api_version):
    """Список ДЕПЛОЙМЕНТІВ ресурсу (саме їх треба у чаті), а НЕ весь каталог.

    `/openai/v1/models` та `/openai/models` повертають десятки базових моделей
    регіону — це не те, що розгорнув користувач. Деплойменти віддає авторинг-
    ендпоінт `/openai/deployments`; пробуємо кілька сумісних api-version.
    Якщо не вдалося — повертаємо порожньо (користувач введе назву вручну), а не
    сотні зайвих моделей.
    """
    base = azure_resource_base(endpoint)
    headers = {"api-key": api_key}
    versions = ([api_version] if api_version else []) + _AZURE_DEPLOYMENT_API_VERSIONS
    seen = set()
    client = build_http_client()
    if client is None:
        return []
    try:
        for ver in versions:
            if not ver or ver in seen:
                continue
            seen.add(ver)
            url = f"{base}/openai/deployments?api-version={ver}"
            try:
                resp = client.get(url, headers=headers)
                if resp.status_code != 200:
                    continue
                data = resp.json().get("data", [])
                ids = sorted({d.get("id") for d in data if d.get("id")})
                if ids:
                    return ids
            except Exception:
                continue
    finally:
        client.close()
    return []


def _gemini_models(api_key):
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    out = []
    for m in genai.list_models():
        methods = getattr(m, "supported_generation_methods", []) or []
        if "generateContent" in methods:
            out.append(m.name.split("/")[-1])
    return sorted(set(out))
