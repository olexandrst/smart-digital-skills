"""Адаптер до Google Gemini API (google-generativeai)."""
from app.integrations.base import LLMResponse, mock_chat_response, estimate_tokens


class GeminiClient:
    provider_label = "Gemini"

    def __init__(self, api_key="", use_mock=True):
        self.api_key = api_key
        self.use_mock = use_mock or not api_key

    def complete(self, model_name, prompt, parameters=None):
        return self.chat(model_name, [{"role": "user", "content": prompt}], parameters)

    def chat(self, model_name, messages, parameters=None):
        if self.use_mock:
            return mock_chat_response(self.provider_label, model_name, messages)
        return self._real_chat(model_name, messages, parameters or {})

    def _real_chat(self, model_name, messages, parameters):
        try:
            import google.generativeai as genai
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "Пакет google-generativeai не встановлено. Виконайте "
                "`pip install google-generativeai` або увімкніть LLM_MOCK=1."
            ) from exc

        genai.configure(api_key=self.api_key)
        model = genai.GenerativeModel(model_name)
        # Конвертуємо історію у формат Gemini (assistant → model).
        contents = [
            {"role": "model" if m.get("role") == "assistant" else "user",
             "parts": [m.get("content", "")]}
            for m in messages
        ]
        resp = model.generate_content(
            contents,
            generation_config={"temperature": parameters.get("temperature", 0.7)},
        )

        usage = getattr(resp, "usage_metadata", None)
        prompt_tokens = getattr(usage, "prompt_token_count", None)
        completion_tokens = getattr(usage, "candidates_token_count", None)
        content = resp.text
        prompt_fallback = sum(estimate_tokens(m.get("content", "")) for m in messages)
        return LLMResponse(
            content=content,
            prompt_tokens=prompt_tokens if prompt_tokens is not None else prompt_fallback,
            completion_tokens=(completion_tokens if completion_tokens is not None
                               else estimate_tokens(content)),
        )
