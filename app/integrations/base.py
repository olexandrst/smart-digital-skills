"""Спільні примітиви для LLM-провайдерів."""
from dataclasses import dataclass


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


def build_http_client():
    """httpx-клієнт для openai SDK, що оминає несумісність із аргументом ``proxies``.

    У httpx>=0.28 параметр ``proxies`` прибрано, тоді як деякі версії openai SDK
    його ще передають — це спричиняє ``TypeError: got an unexpected keyword
    argument 'proxies'``. Передаючи власний ``http_client``, ми змушуємо SDK не
    будувати внутрішній клієнт із цим аргументом. Проксі з середовища
    (HTTP_PROXY/HTTPS_PROXY) підхоплюються автоматично через ``trust_env=True``.
    """
    try:
        import httpx
    except ImportError:  # pragma: no cover — httpx є залежністю openai
        return None
    return httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0), trust_env=True)


def estimate_tokens(text: str) -> int:
    """Груба оцінка: ~1.3 токена на слово (достатньо для базового обліку MVP/моку)."""
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
            last_user = m.get("content", "")
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
