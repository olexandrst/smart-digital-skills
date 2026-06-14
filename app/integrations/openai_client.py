"""Адаптер до OpenAI API (api.openai.com або сумісний base_url)."""
from app.integrations.base import LLMResponse, mock_response


class OpenAIClient:
    provider_label = "OpenAI"

    def __init__(self, api_key="", base_url="", use_mock=True):
        self.api_key = api_key
        self.base_url = base_url or None
        self.use_mock = use_mock or not api_key

    def complete(self, model_name, prompt, parameters=None):
        if self.use_mock:
            return mock_response(self.provider_label, model_name, prompt)
        return self._real_complete(model_name, prompt, parameters or {})

    def _real_complete(self, model_name, prompt, parameters):
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "Пакет openai не встановлено. Виконайте `pip install openai` "
                "або увімкніть LLM_MOCK=1."
            ) from exc

        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        resp = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=parameters.get("temperature", 0.7),
        )
        usage = resp.usage
        return LLMResponse(
            content=resp.choices[0].message.content,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
        )
