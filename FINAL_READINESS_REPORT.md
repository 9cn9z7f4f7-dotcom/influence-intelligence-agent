# FINAL READINESS REPORT — Influence Intelligence Agent

Дата: 2026-08-20

## WORKS

- Полный аналитический pipeline end-to-end: Market Map → Competitor DNA → Next Move → White Space → Our Move, работает на 199 интеграциях / 184 креаторах / 4 конкурентах synthetic demo dataset.
- `python manage.py demo-run` детерминированно проходит несколько раз подряд (проверено 3+ прогона, идентичные integrations_analyzed/creators_analyzed/competitors_analyzed).
- Все 7 API endpoints (`/api/overview`, `/api/market-map`, `/api/competitor-dna`, `/api/next-moves`, `/api/white-space`, `/api/our-move`, `/api/health`) отдают 200 и реальные посчитанные данные, а не заглушки.
- `GET /api/evidence/{id}` резолвит evidence по id; неизвестный id → 404 (проверено тестом).
- UI-дашборд (Overview, Market Map, Competitor DNA, Next Move, White Space, Our Move) целиком тянет данные с backend, включая кнопку **▶ Demo Flow** с 5 шагами живого демо. Проверено скриншотами headless Chromium — визуально консистентно, без "нарисованных" фейковых данных.
- Evidence-система (`FACT` / `COMPUTED` / `AI_INFERENCE`) работает по всем 5 слоям, включая raw FACT evidence на уровне каждой интеграции (не только агрегатов).
- Демо dataset специально сконструирован и провалидирован (assert'ами в генераторе) так, чтобы гарантировать: 4 конкурента, 184 креатора, 199 интеграций, 24 сегмента, явный перегретый сегмент (`fitness/youtube/mid`, saturation 100/100), явный white space (`medical_students/telegram/nano`, opportunity ≈83/100), явный recent strategic shift (NovaFit Media: `fitness/youtube/macro` → `coding/telegram/micro` за последние 30 дней), и десятки next-move кандидатов.
- Health/degraded-mode: если один источник недоступен (YouTube без ключа, Telegram/Instagram не реализованы), pipeline не падает и явно показывает статус в UI и `/api/health` — проверено тестами и живым прогоном.
- 46/46 pytest тестов зелёные (модели, нормализация, Market Map, DNA windows/shifts, similarity score, exclusion логика, White Space scoring + insufficient_data, Our Move evidence constraints, guardrails, degraded sources, полный demo pipeline, все API endpoints).
- Fresh install с нуля (новый venv, `pip install -r requirements.txt`, генерация dataset, `demo-run`, тесты) — проверено в изолированной директории, всё работает без правок.
- Demo работает без интернета — проверено принудительно (недостижимый HTTP(S)_PROXY), demo-режим не делает ни одного сетевого вызова.
- LLM guardrails: если ANTHROPIC_API_KEY задан, но модель вернула запрещённую формулировку ("конкурент точно сделает X") — детерминированный fallback подставляется автоматически (покрыто тестами `test_guardrails.py`).

## DEGRADED BUT SAFE

- **Telegram** — официального adapter нет, статус всегда `degraded`. Pipeline и UI показывают это явно, ничего не притворяется рабочим.
- **Instagram** — Graph API требует бизнес-верификации, статус всегда `unavailable`.
- **YouTube без ключа** — адаптер помечается `unavailable`, но live-режим и pipeline продолжают работать на demo dataset.
- **Anthropic без ключа** — Competitor DNA и Our Move возвращают те же цифры и evidence, но без LLM-переформулировки (`type: computed` вместо `ai_inference`, без "красивого текста" — ровно как требует мастер-промпт).

## REQUIRES CREDENTIALS

- `YOUTUBE_API_KEY` — для реального поиска видео/каналов в live-режиме (используется только для enrichment/health-проверки, не для построения конкурентной карты — см. LIMITATIONS).
- `ANTHROPIC_API_KEY` — для смысловой переформулировки паттернов Competitor DNA и summary Our Move.
- Ни один ключ не закоммичен в репозиторий (проверено grep по кодовой базе); `.env.example` содержит только пустые плейсхолдеры.

## DEMO ONLY

- **Весь конкурентный ландшафт (creators/competitors/integrations) в этой сборке — synthetic demo dataset**, явно помечен `is_synthetic: true` во всех записях и отдельным warning-баннером в UI. Реальный live-парсинг реальных конкурентных интеграций (кто с кем реально работал) не входит в объём этого MVP — see LIMITATIONS.
- Telegram/Instagram adapters — заглушки-стабы (`app/ingestion/optional_adapters.py`), существуют только для честного health-статуса, реальных данных не тянут.

## KNOWN LIMITATIONS

1. **Live-режим не строит конкурентную карту из реальных данных.** YouTube/web адаптеры в live-режиме используются только для health-проверки/enrichment отдельных сущностей (поиск каналов, парсинг страницы) — сопоставление "конкурент X интегрировался с креатором Y" на реальных данных требует отдельного NLP/detection слоя (детекция офера/меканики/CTA из текста поста), который сознательно не реализован в hackathon-объёме (см. раздел 20 мастер-промпта — "не превращай проект в универсальную платформу"). Все аналитические слои (Market Map/DNA/Next Move/White Space/Our Move) полностью рабочие и одинаково применимы к live-данным, если/когда появится реальный ingestion в модель `Integration`.
2. **Evidence-цепочка для FACT-уровня существует, но не всегда явно процитирована в каждой карточке UI.** Raw FACT evidence генерируется на каждую интеграцию при загрузке (см. `app/ingestion/demo_loader.py`) и доступен через `/api/evidence/{id}`, но большинство карточек UI ссылаются на COMPUTED/AI_INFERENCE evidence (агрегаты), а не на конкретные raw-факты по одной записи.
3. **White Space / Next Move формулы — прозрачные heuristics, не ML-модели.** Веса вынесены в `config/settings.py` и объясняются через `why`/`our_relevance_notes`, но это не заменяет реальную статистическую значимость при очень малых сегментах — поэтому сегменты с `available_creators < 5` помечаются `insufficient_data: true` и Our Move автоматически занижает confidence для них.
4. **Recent shift threshold (Δ ≥ 0.25) и MIN_SEGMENT_SAMPLE_SIZE (5)** — разумные дефолты, а не эмпирически откалиброванные на реальных данных константы (реальных данных пока нет). Легко перенастраиваемы.
5. **Однопроцессный health registry** — для hackathon-демо достаточно (единственный процесс uvicorn), но не переживёт горизонтальное масштабирование без доработки (не требуется для demo).

## COMMAND TO START

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/generate_demo_data.py   # один раз
python manage.py serve                 # http://localhost:8000
```

## COMMAND TO RUN DEMO

```bash
python manage.py demo-run
```
Выведет `DEMO READY` со сводкой (integrations/creators/competitors/white spaces/next targets analyzed). Затем откройте UI (`python manage.py serve`) и нажмите **▶ Demo Flow** для гостевого прогона по 5 шагам live-демо.

## ВЕРДИКТ

**READY FOR HACKATHON DEMO**
