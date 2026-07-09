"""Спільні примітиви для LLM-провайдерів."""
import os
from dataclasses import dataclass
from urllib.parse import urlsplit

# Таймаут очікування відповіді моделі (сек). За замовчуванням 30 хв — деякі
# локальні моделі (Ollama/LM Studio) відповідають довго; користувач може
# перервати запит кнопкою «Стоп». Налаштовується через LLM_TIMEOUT.
DEFAULT_LLM_TIMEOUT = 1800.0


def llm_timeout():
    try:
        return float(os.getenv("LLM_TIMEOUT", DEFAULT_LLM_TIMEOUT))
    except (TypeError, ValueError):
        return DEFAULT_LLM_TIMEOUT


def azure_resource_base(endpoint):
    """Зводить Azure endpoint до кореня ресурсу `https://<host>` (без шляху).

    Користувачі часто вставляють повний URL запиту (напр.
    `…/openai/v1/chat/completions`). Для openai SDK потрібен ЛИШЕ корінь ресурсу,
    інакше шлях подвоюється й повертається 404 «Resource not found».
    """
    if not endpoint:
        return endpoint
    parts = urlsplit(endpoint if "://" in endpoint else "https://" + endpoint)
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return endpoint.rstrip("/")


def azure_v1_base(endpoint):
    """Базовий URL v1 API Azure OpenAI: `https://<host>/openai/v1/`.

    v1 — OpenAI-сумісна поверхня (New Foundry): чат викликається як звичайний
    OpenAI (`model=<деплоймент>`), БЕЗ api-version. Це знімає прив'язку до
    конкретної версії API й уникає 404 на класичному шляху `/openai/deployments`.
    """
    root = azure_resource_base(endpoint)
    return f"{root}/openai/v1/" if root else root


@dataclass
class LLMResponse:
    content: str
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


# Зворотна сумісність: стара назва відповіді Azure-клієнта.
FoundryResponse = LLMResponse


def build_http_client(timeout=None):
    """httpx-клієнт для openai SDK, що оминає несумісність із аргументом ``proxies``.

    У httpx>=0.28 параметр ``proxies`` прибрано, тоді як деякі версії openai SDK
    його ще передають — це спричиняє ``TypeError: got an unexpected keyword
    argument 'proxies'``. Передаючи власний ``http_client``, ми змушуємо SDK не
    будувати внутрішній клієнт із цим аргументом. Проксі з середовища
    (HTTP_PROXY/HTTPS_PROXY) підхоплюються автоматично через ``trust_env=True``.

    ``timeout`` — читання відповіді (сек); за замовчуванням — довгий таймаут для
    чату (LLM_TIMEOUT). Для швидких операцій (перелік моделей) передають менший.
    """
    try:
        import httpx
    except ImportError:  # pragma: no cover — httpx є залежністю openai
        return None
    read = llm_timeout() if timeout is None else timeout
    return httpx.Client(timeout=httpx.Timeout(read, connect=15.0), trust_env=True)


def content_to_text(content) -> str:
    """Зводить вміст повідомлення до тексту.

    Вміст може бути рядком або мультимодальним списком частин (текст +
    зображення/файли) — для оцінки токенів і моку беремо текст, а вкладення
    позначаємо коротким плейсхолдером.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype == "text":
                out.append(part.get("text", ""))
            elif ptype == "image_url":
                out.append("[зображення]")
            elif ptype == "file":
                out.append(f"[файл: {(part.get('file') or {}).get('filename', 'PDF')}]")
        return " ".join(out)
    return str(content or "")


def estimate_tokens(text) -> int:
    """Груба оцінка: ~1.3 токена на слово (достатньо для базового обліку MVP/моку)."""
    text = content_to_text(text)
    if not text:
        return 0
    return max(1, int(len(text.split()) * 1.3) + 1)


def mock_response(provider_label: str, model_name: str, prompt: str) -> LLMResponse:
    return mock_chat_response(provider_label, model_name,
                              [{"role": "user", "content": prompt}])


def mock_chat_response(provider_label: str, model_name: str, messages) -> LLMResponse:
    """Детермінований мок для багатоходового чату: відповідає на останнє повідомлення."""
    last_user = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user = content_to_text(m.get("content", ""))
            break
    snippet = (last_user[:80] + "…") if len(last_user) > 80 else last_user
    content = (
        f"[MOCK · {provider_label} · {model_name}] Відповідь на ваше повідомлення: "
        f"«{snippet}». Це демонстраційна відповідь у режимі моку — підключіть "
        "реальний API-ключ у .env, щоб отримувати справжні відповіді."
    )
    prompt_tokens = sum(estimate_tokens(m.get("content", "")) for m in messages)
    return LLMResponse(
        content=content,
        prompt_tokens=prompt_tokens,
        completion_tokens=estimate_tokens(content),
    )
