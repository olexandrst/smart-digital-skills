"""Адаптер до Azure OpenAI / Azure AI Foundry.

Використовує v1 (New Foundry) OpenAI-сумісну поверхню: чат викликається як
звичайний OpenAI проти `https://<host>/openai/v1/` з `model=<деплоймент>`, БЕЗ
api-version. У MVP працює мок.
"""
from app.integrations.base import (
    LLMResponse, mock_chat_response, build_http_client, azure_v1_base, llm_timeout,
)

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
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "Пакет openai не встановлено. Виконайте `pip install openai` "
                "або увімкніть LLM_MOCK=1."
            ) from exc

        # v1 (New Foundry) — OpenAI-сумісна поверхня: model = назва деплойменту,
        # ключ передається як Bearer (Azure v1 це приймає), api-version не потрібна.
        client = OpenAI(
            base_url=azure_v1_base(self.endpoint),
            api_key=self.api_key,
            timeout=llm_timeout(),
            http_client=build_http_client(),
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
