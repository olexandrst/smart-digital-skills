"""Адаптер до OpenAI API (api.openai.com або сумісний base_url)."""
from app.integrations.base import LLMResponse, mock_chat_response, build_http_client


class OpenAIClient:
    provider_label = "OpenAI"

    def __init__(self, api_key="", base_url="", use_mock=True):
        self.api_key = api_key
        self.base_url = base_url or None
        self.use_mock = use_mock or not api_key

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

        client = OpenAI(api_key=self.api_key, base_url=self.base_url,
                        http_client=build_http_client())
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
