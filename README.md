# Influence Intelligence Agent

Хакатонный MVP: превращает массив публичных influencer-интеграций конкурентов
в стратегическую картину рынка — Market Map → Competitor DNA → Next Move →
White Space → Our Move.

## Быстрый старт (demo-режим, без интернета)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python scripts/generate_demo_data.py   # один раз - сгенерировать demo dataset
python manage.py demo-run              # прогнать весь аналитический pipeline
python manage.py serve                 # запустить API + UI на http://localhost:8000
```

Откройте `http://localhost:8000` — дашборд со всеми пятью слоями и кнопкой
**▶ Demo Flow** для гостевого прогона по 5 шагам.

## Структура проекта

```
app/
  models.py            - единая модель данных (Creator, Competitor, Integration, Evidence, ...)
  storage.py            - SQLite storage layer
  evidence.py            - FACT / COMPUTED / AI_INFERENCE evidence-система
  health.py             - health/degraded-mode registry для источников
  pipeline.py            - оркестрация всего pipeline
  ingestion/            - YouTube adapter, generic web adapter, demo loader, optional adapters
  analytics/             - Market Map, Competitor DNA, Next Move, White Space, Our Move, LLM-обёртка, guardrails
  api/server.py          - FastAPI приложение (все /api/* эндпоинты + статический UI)
config/settings.py       - все "магические числа" (buckets, windows, weights) в одном месте
data/demo/                - synthetic demo dataset (генерируется scripts/generate_demo_data.py)
static/                   - UI-дашборд (index.html, app.js, styles.css)
tests/                    - pytest suite
scripts/generate_demo_data.py - генератор demo dataset
manage.py                 - CLI: demo-reset / demo-run / serve
```

## Команды

| Команда | Что делает |
|---|---|
| `python scripts/generate_demo_data.py` | Сгенерировать/пересоздать demo dataset в `data/demo/*.json` |
| `python manage.py demo-reset` | Очистить SQLite + `output/*.json` |
| `python manage.py demo-run` | Полный прогон pipeline, печатает `DEMO READY` со статистикой |
| `python manage.py serve` | Запустить API + UI (`--port`, `--host`, `--reload`) |
| `pytest -q` | Прогнать тесты |

## Режимы

- **demo** (по умолчанию, `APP_MODE=demo`) — работает полностью без интернета на
  synthetic dataset (`is_synthetic=true` во всех записях, явно показано в UI).
- **live** (`APP_MODE=live`) — дополнительно пытается опросить YouTube Data API v3
  и generic web adapter для health-статуса; при отсутствии `YOUTUBE_API_KEY`
  источник помечается `unavailable`, а не роняет pipeline. Аналитический "скелет"
  остаётся demo dataset (см. `FINAL_READINESS_REPORT.md`, раздел LIMITATIONS).

## Конфигурация

Все buckets/windows/weights — в `config/settings.py`. Локальный override без
изменения кода: создайте `config/config.local.json` с нужными ключами (см.
`Settings.__init__` за списком поддерживаемых полей).

## Evidence / Why

Каждый computed или ai_inference вывод несёт `evidence_ids`. Разрешить в
человекочитаемый вид: `GET /api/evidence/{evidence_id}`, либо кнопка
"why / evidence" в UI.
