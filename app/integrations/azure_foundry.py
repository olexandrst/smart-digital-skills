"""Адаптер до Azure AI Foundry.

У MVP працює мок: повертає детерміновану відповідь і рахує токени за
кількістю слів. Реальний клієнт підключається через openai SDK у режимі
Azure, коли задано AZURE_FOUNDRY_ENDPOINT/API_KEY та вимкнено мок.
"""
from dataclasses import dataclass
from flask import current_app


@dataclass
class FoundryResponse:
    content: str
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def _estimate_tokens(text: str) -> int:
    """Груба оцінка: ~1.3 токена на слово (достатньо для базового обліку MVP)."""
    if not text:
        return 0
    return max(1, int(len(text.split()) * 1.3) + 1)


class AzureFoundryClient:
    def __init__(self, endpoint="", api_key="", use_mock=True):
        self.endpoint = endpoint
        self.api_key = api_key
        self.use_mock = use_mock or not (endpoint and api_key)

    def complete(self, deployment_name, prompt, parameters=None):
        if self.use_mock:
            return self._mock_complete(deployment_name, prompt)
        return self._real_complete(deployment_name, prompt, parameters or {})

    def _mock_complete(self, deployment_name, prompt):
        content = (
            f"[MOCK · {deployment_name}] Отримано запит та опрацьовано його. "
            "Це демонстраційна відповідь Azure AI Foundry у режимі моку. "
            "Підключіть реальні ключі у .env, щоб отримувати справжні відповіді."
        )
        return FoundryResponse(
            content=content,
            prompt_tokens=_estimate_tokens(prompt),
            completion_tokens=_estimate_tokens(content),
        )

    def _real_complete(self, deployment_name, prompt, parameters):
        # Реальна інтеграція (Етап після MVP). Залишено як точку розширення.
        try:
            from openai import AzureOpenAI
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "Пакет openai не встановлено. Додайте openai у requirements "
                "або увімкніть AZURE_FOUNDRY_MOCK=1."
            ) from exc

        client = AzureOpenAI(
            azure_endpoint=self.endpoint,
            api_key=self.api_key,
            api_version=parameters.get("api_version", "2024-02-15-preview"),
        )
        resp = client.chat.completions.create(
            model=deployment_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=parameters.get("temperature", 0.7),
        )
        usage = resp.usage
        return FoundryResponse(
            content=resp.choices[0].message.content,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
        )


def get_client():
    cfg = current_app.config
    return AzureFoundryClient(
        endpoint=cfg.get("AZURE_FOUNDRY_ENDPOINT", ""),
        api_key=cfg.get("AZURE_FOUNDRY_API_KEY", ""),
        use_mock=cfg.get("AZURE_FOUNDRY_MOCK", True),
    )
