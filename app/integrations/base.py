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


def estimate_tokens(text: str) -> int:
    """Груба оцінка: ~1.3 токена на слово (достатньо для базового обліку MVP/моку)."""
    if not text:
        return 0
    return max(1, int(len(text.split()) * 1.3) + 1)


def mock_response(provider_label: str, model_name: str, prompt: str) -> LLMResponse:
    content = (
        f"[MOCK · {provider_label} · {model_name}] Отримано запит та опрацьовано його. "
        "Це демонстраційна відповідь у режимі моку. "
        "Підключіть реальний API-ключ у .env, щоб отримувати справжні відповіді."
    )
    return LLMResponse(
        content=content,
        prompt_tokens=estimate_tokens(prompt),
        completion_tokens=estimate_tokens(content),
    )
