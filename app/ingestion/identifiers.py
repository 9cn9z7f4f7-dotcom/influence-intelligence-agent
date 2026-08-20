"""
Стабильная генерация id из человекочитаемых имён (конкурент/креатор),
общая для live-ingestion и CSV/JSON import - чтобы одно и то же имя
конкурента, встреченное в разных источниках, схлопывалось в одну сущность.
"""
from __future__ import annotations

import hashlib
import re


def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^\w]+", "_", text, flags=re.UNICODE)
    return text.strip("_") or "unknown"


def stable_id(prefix: str, *parts: str) -> str:
    joined = "|".join(p or "" for p in parts)
    digest = hashlib.sha1(joined.encode("utf-8")).hexdigest()[:8]
    slug = slugify(parts[0]) if parts else "unknown"
    return f"{prefix}_{slug}_{digest}"
