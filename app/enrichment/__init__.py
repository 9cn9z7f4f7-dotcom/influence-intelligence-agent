"""Vision/screenshot enrichment layer (раздел 2-3 real-data требований)."""
from __future__ import annotations

from app.enrichment.screenshot import ScreenshotCache, capture_screenshot
from app.enrichment.visual_evidence import VisualEvidenceEnricher, VisualEvidenceResult

__all__ = ["ScreenshotCache", "capture_screenshot", "VisualEvidenceEnricher", "VisualEvidenceResult"]
