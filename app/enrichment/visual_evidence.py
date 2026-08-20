"""
VisualEvidenceEnricher - раздел 2 real-data требований.

Получает source_url + screenshot + extracted DOM/text + brand name/aliases и
через OpenRouterProvider.analyze_text_and_image_json() просит vision-модель
найти визуальные коммерческие сигналы: brand logo, product visible, paid
partnership, sponsor disclosure, promo code, CTA, branded banner, product
placement, visual topic/content format.

ЖЁСТКИЕ ПРАВИЛА:
  - Vision result = AI_INFERENCE (конкретно EvidenceType.VISUAL_AI), НЕ FACT.
  - VisualEvidenceEnricher САМ НИКОГДА не создаёт Integration - он только
    возвращает структурированный сигнал; финальное решение принимает
    IntegrationDetector через app.detection.combine_dom_and_visual().
  - Если OpenRouter недоступен / вернул невалидный JSON / нет screenshot -
    возвращается VisualEvidenceResult(status="unavailable"/"degraded") с
    честными (не выдуманными) значениями по умолчанию - pipeline не падает.
"""
from __future__ import annotations

import hashlib
from typing import Optional

from pydantic import BaseModel, Field, ValidationError

from app.providers.openrouter import OpenRouterProvider

SYSTEM_PROMPT = (
    "Ты - визуальный аналитик influence-маркетинга. Тебе дают screenshot страницы/поста "
    "и извлечённый из DOM текст. Определи ТОЛЬКО то, что реально видно на картинке, и "
    "верни STRICT JSON (ничего кроме JSON, без markdown-обёртки) со схемой:\n"
    '{"brand_visible": bool, "commercial_signal_visible": bool, '
    '"signals": [строки из набора: "logo","product","paid_partnership","sponsor_disclosure",'
    '"promo_code","cta","branded_banner","product_placement","content_topic"], '
    '"content_topics": [строки - визуальный формат/тема контента, напр. "unboxing","tutorial"], '
    '"confidence": число 0..1, "evidence": [короткие текстовые описания того, что реально видно]}\n'
    "НЕ утверждай то, чего не видно на картинке, и не додумывай текст, которого нет на "
    "screenshot/в extracted_text. Если сомневаешься - confidence должен быть низким. "
    "Ты НЕ выносишь итоговый вердикт по интеграции - только описываешь визуальные сигналы."
)

ALLOWED_SIGNALS = {
    "logo", "product", "paid_partnership", "sponsor_disclosure", "promo_code",
    "cta", "branded_banner", "product_placement", "content_topic",
}


class VisualEvidenceResult(BaseModel):
    """Строгая схема ответа vision-модели (раздел 2 требований) + служебные
    поля, которые добавляет сам enricher (status/source_url) - НЕ часть
    ответа модели."""

    brand_visible: bool = False
    commercial_signal_visible: bool = False
    signals: list[str] = Field(default_factory=list)
    content_topics: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    evidence: list[str] = Field(default_factory=list)

    # ok - реальный ответ vision-модели; unavailable - нет ключа/screenshot;
    # degraded - ключ есть, но вызов/парсинг упал (раздел 24, тот же failsafe принцип).
    status: str = "ok"
    source_url: Optional[str] = None

    def is_usable(self) -> bool:
        return self.status == "ok"


def _cache_key(url: str, screenshot: bytes | str | None) -> str:
    """URL + screenshot hash - раздел 3 требований: одинаковый screenshot не
    должен отправляться повторно."""
    if isinstance(screenshot, bytes):
        digest = hashlib.sha256(screenshot).hexdigest()[:24]
    elif isinstance(screenshot, str):
        digest = hashlib.sha256(screenshot.encode("utf-8", errors="ignore")).hexdigest()[:24]
    else:
        digest = "no_screenshot"
    return f"{url}|{digest}"


class VisualEvidenceEnricher:
    """Оборачивает OpenRouterProvider + screenshot-hash cache (раздел 1-3)."""

    def __init__(self, provider: OpenRouterProvider | None = None,
                 cache: Optional[dict[str, VisualEvidenceResult]] = None) -> None:
        self.provider = provider or OpenRouterProvider()
        # Инъектируемый dict позволяет держать один shared cache на весь
        # analysis run и позволяет тестам проверить cache hit/miss явно.
        self._cache: dict[str, VisualEvidenceResult] = cache if cache is not None else {}

    def is_available(self) -> bool:
        return self.provider.is_available()

    def cache_stats(self) -> dict:
        return {"cached_entries": len(self._cache)}

    def enrich(self, source_url: str, screenshot: bytes | str | None, extracted_text: str,
               brand_name: str, brand_aliases: list[str] | None = None) -> VisualEvidenceResult:
        """Возвращает VisualEvidenceResult. НИКОГДА не бросает исключение -
        при недоступности/ошибке возвращает status='unavailable'/'degraded'
        с честными значениями по умолчанию (не выдуманными)."""
        cache_key = _cache_key(source_url, screenshot)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        if screenshot is None or not self.provider.is_available():
            result = VisualEvidenceResult(status="unavailable", source_url=source_url)
            self._cache[cache_key] = result
            return result

        image_data_url = self._to_data_url(screenshot)
        aliases_str = ", ".join(brand_aliases or [])
        user_text = (
            f"brand_name={brand_name!r}; brand_aliases=[{aliases_str}]; source_url={source_url!r}; "
            f"extracted_text (первые 2000 символов)={extracted_text[:2000]!r}"
        )

        raw = self.provider.analyze_text_and_image_json(SYSTEM_PROMPT, user_text, image_data_url)
        if raw is None:
            result = VisualEvidenceResult(status="degraded", source_url=source_url)
            self._cache[cache_key] = result
            return result

        try:
            raw_signals = raw.get("signals")
            if isinstance(raw_signals, list):
                raw = {**raw, "signals": [s for s in raw_signals if isinstance(s, str) and s in ALLOWED_SIGNALS]}
            parsed = VisualEvidenceResult.model_validate({**raw, "status": "ok", "source_url": source_url})
        except ValidationError:
            parsed = VisualEvidenceResult(status="degraded", source_url=source_url)

        self._cache[cache_key] = parsed
        return parsed

    @staticmethod
    def _to_data_url(screenshot: bytes | str) -> str:
        if isinstance(screenshot, str) and screenshot.startswith(("http://", "https://", "data:")):
            return screenshot
        import base64
        raw_bytes = screenshot if isinstance(screenshot, bytes) else screenshot.encode("utf-8")
        b64 = base64.b64encode(raw_bytes).decode("ascii")
        return f"data:image/png;base64,{b64}"
