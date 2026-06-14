"""Адаптер до Google Gemini API (google-generativeai)."""
from app.integrations.base import LLMResponse, mock_response, estimate_tokens


class GeminiClient:
    provider_label = "Gemini"

    def __init__(self, api_key="", use_mock=True):
        self.api_key = api_key
        self.use_mock = use_mock or not api_key

    def complete(self, model_name, prompt, parameters=None):
        if self.use_mock:
            return mock_response(self.provider_label, model_name, prompt)
        return self._real_complete(model_name, prompt, parameters or {})

    def _real_complete(self, model_name, prompt, parameters):
        try:
            import google.generativeai as genai
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "Пакет google-generativeai не встановлено. Виконайте "
                "`pip install google-generativeai` або увімкніть LLM_MOCK=1."
            ) from exc

        genai.configure(api_key=self.api_key)
        model = genai.GenerativeModel(model_name)
        resp = model.generate_content(
            prompt,
            generation_config={"temperature": parameters.get("temperature", 0.7)},
        )

        usage = getattr(resp, "usage_metadata", None)
        prompt_tokens = getattr(usage, "prompt_token_count", None)
        completion_tokens = getattr(usage, "candidates_token_count", None)
        content = resp.text
        return LLMResponse(
            content=content,
            prompt_tokens=prompt_tokens if prompt_tokens is not None else estimate_tokens(prompt),
            completion_tokens=(completion_tokens if completion_tokens is not None
                               else estimate_tokens(content)),
        )
