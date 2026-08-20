"""
OpenRouterProvider - backend-only text/vision enrichment через OpenRouter
OpenAI-compatible Chat Completions API (раздел 1 требований real-data update).

Жёсткие правила:
  - ключ ТОЛЬКО из переменной окружения OPENROUTER_API_KEY (через
    config.settings, который сам делает os.getenv) - никогда не хардкодится
    и не принимается откуда-либо ещё (UI/DB/файл);
  - используется backend-side only: ключ никогда не отправляется во
    frontend, не возвращается через API, не логируется (даже в виде
    Authorization header), не сохраняется в SQLite, не пишется в README,
    не коммитится в git (см. app/providers/openrouter.py::_post - все
    исключения гасятся без логирования payload/headers);
  - если OPENROUTER_API_KEY отсутствует, сеть недоступна, ответ невалиден
    или таймаут - НИКОГДА не бросает исключение наружу. Вызывающий код
    (VisualEvidenceEnricher и т.п.) получает None/False и обязан
    переключиться на deterministic fallback
    (visual_ai_status = "unavailable"/"degraded").
"""
from __future__ import annotations

import json
from typing import Optional

import httpx

from config.settings import settings as default_settings

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_TEXT_MODEL = "anthropic/claude-3.5-haiku"
DEFAULT_VISION_MODEL = "anthropic/claude-3.5-sonnet"
REQUEST_TIMEOUT_SECONDS = 20.0


class OpenRouterProvider:
    """Тонкая, отказоустойчивая обёртка над OpenRouter chat/completions.

    Никогда не бросает исключение наружу вызывающему коду - любая ошибка
    (нет ключа, сеть, таймаут, HTTP-ошибка, невалидный JSON) превращается
    в None, чтобы pipeline мог применить deterministic fallback.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 vision_model: str | None = None, timeout: float = REQUEST_TIMEOUT_SECONDS,
                 settings=None) -> None:
        cfg = settings or default_settings
        # api_key-параметр существует только для тестов (явный override) -
        # в production коде всегда читается из settings/env, никогда не хардкодится.
        self.api_key = api_key if api_key is not None else cfg.openrouter_api_key
        self.model = model or cfg.openrouter_model or DEFAULT_TEXT_MODEL
        self.vision_model = vision_model or cfg.openrouter_vision_model or DEFAULT_VISION_MODEL
        self.timeout = timeout

    def is_available(self) -> bool:
        return bool(self.api_key)

    # ------------------------------------------------------------------
    # Низкоуровневый вызов - НИКОГДА не логирует payload/headers (там ключ).
    # ------------------------------------------------------------------
    def _post(self, payload: dict) -> Optional[dict]:
        if not self.is_available():
            return None
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            # Рекомендованные OpenRouter заголовки для атрибуции в их дашборде -
            # это не секреты, безопасно задавать статично.
            "HTTP-Referer": "https://influence-intelligence-agent.local",
            "X-Title": "Influence Intelligence Agent",
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(OPENROUTER_API_URL, headers=headers, json=payload)
            if resp.status_code == 429:
                return None  # rate limit - failsafe, без агрессивных ретраев
            resp.raise_for_status()
            return resp.json()
        except Exception:  # noqa: BLE001 - любая сетевая/HTTP/парсинг ошибка => failsafe None.
            # ВАЖНО: не логировать exc с payload/headers - там Authorization/ключ (раздел 25).
            return None

    @staticmethod
    def _extract_text(response: Optional[dict]) -> Optional[str]:
        if not response:
            return None
        try:
            choices = response.get("choices") or []
            if not choices:
                return None
            message = choices[0].get("message") or {}
            content = message.get("content")
            if isinstance(content, list):
                text_parts = [b.get("text", "") for b in content if isinstance(b, dict)]
                joined = "".join(text_parts).strip()
                return joined or None
            if isinstance(content, str):
                stripped = content.strip()
                return stripped or None
            return None
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _parse_json_text(raw: Optional[str]) -> Optional[dict]:
        if raw is None:
            return None
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None

    # ------------------------------------------------------------------
    # Публичный интерфейс (раздел 1 требований)
    # ------------------------------------------------------------------
    def analyze_text(self, system_prompt: str, user_text: str, model: str | None = None,
                      max_tokens: int = 1024) -> Optional[str]:
        """Text-only enrichment. None при недоступности/ошибке (failsafe)."""
        payload = {
            "model": model or self.model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
        }
        return self._extract_text(self._post(payload))

    def analyze_image(self, system_prompt: str, image_data_url: str, user_text: str = "",
                       model: str | None = None, max_tokens: int = 1024) -> Optional[str]:
        """Vision-only (+ опциональный текст) enrichment. image_data_url - data: URI или
        публичный https URL картинки/скриншота (OpenAI-compatible image_url content block)."""
        content: list[dict] = [{"type": "image_url", "image_url": {"url": image_data_url}}]
        if user_text:
            content.insert(0, {"type": "text", "text": user_text})
        payload = {
            "model": model or self.vision_model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
        }
        return self._extract_text(self._post(payload))

    def analyze_text_and_image(self, system_prompt: str, user_text: str, image_data_url: str,
                                model: str | None = None, max_tokens: int = 1024) -> Optional[str]:
        """Комбинированный text+vision запрос (основной способ вызова
        VisualEvidenceEnricher - контекст интеграции + screenshot)."""
        payload = {
            "model": model or self.vision_model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ]},
            ],
        }
        return self._extract_text(self._post(payload))

    # --- JSON-strict варианты (для VisualEvidenceEnricher/ArticleClassifier) -------
    def analyze_text_json(self, system_prompt: str, user_payload: dict,
                           model: str | None = None, max_tokens: int = 1024) -> Optional[dict]:
        raw = self.analyze_text(system_prompt, json.dumps(user_payload, ensure_ascii=False), model, max_tokens)
        return self._parse_json_text(raw)

    def analyze_text_and_image_json(self, system_prompt: str, user_text: str, image_data_url: str,
                                     model: str | None = None, max_tokens: int = 1024) -> Optional[dict]:
        raw = self.analyze_text_and_image(system_prompt, user_text, image_data_url, model, max_tokens)
        return self._parse_json_text(raw)
