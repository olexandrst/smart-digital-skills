"""Адаптер до Azure OpenAI / Azure AI Foundry.

У MVP працює мок. Реальний клієнт підключається через openai SDK у режимі
Azure, коли задано endpoint/ключ та вимкнено мок.
"""
from app.integrations.base import LLMResponse, mock_chat_response

# Зворотна сумісність зі старим імпортом.
FoundryResponse = LLMResponse


class AzureFoundryClient:
    provider_label = "Azure OpenAI"

    def __init__(self, endpoint="", api_key="", api_version="2024-02-15-preview",
                 use_mock=True):
        self.endpoint = endpoint
        self.api_key = api_key
        self.api_version = api_version
        self.use_mock = use_mock or not (endpoint and api_key)

    def complete(self, model_name, prompt, parameters=None):
        return self.chat(model_name, [{"role": "user", "content": prompt}], parameters)

    def chat(self, model_name, messages, parameters=None):
        if self.use_mock:
            return mock_chat_response(self.provider_label, model_name, messages)
        return self._real_chat(model_name, messages, parameters or {})

    def _real_chat(self, model_name, messages, parameters):
        try:
            from openai import AzureOpenAI
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "Пакет openai не встановлено. Виконайте `pip install openai` "
                "або увімкніть LLM_MOCK=1."
            ) from exc

        client = AzureOpenAI(
            azure_endpoint=self.endpoint,
            api_key=self.api_key,
            api_version=parameters.get("api_version", self.api_version),
        )
        resp = client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=parameters.get("temperature", 0.7),
        )
        usage = resp.usage
        return LLMResponse(
            content=resp.choices[0].message.content,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
        )
