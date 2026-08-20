# Real Data Validation

Дата составления: 2026-08-20 (окружение: изолированная разработческая
sandbox-сессия, не Render, не Mac пользователя).

**Главный принцип раздела 34 требований**: "если система не может показать
URL реального источника, из которого взялся вывод, этот вывод считается
неподтверждённым". Этот документ честно разделяет: (a) что было реально
проверено живым запросом в этой сессии, (b) что реализовано и покрыто
mocked-тестами, но НЕ проверено живой сетью здесь, и (c) что принципиально
не может быть проверено в этой конкретной sandbox-среде.

## Важная оговорка про окружение этой сессии

Эта сессия работает в контейнере с сетевым proxy (allowlist только
pypi.org/npm/GitHub/anthropic.com). Прямые вызовы `httpx`/собственного кода
проекта (`app/ingestion/live_youtube.py`, `app/article_parser.py`,
`app/providers/openrouter.py`, `app/search_client.py`) к googleapis.com,
openrouter.ai, серпапи и произвольным сайтам **заблокированы** на уровне
сетевого proxy этой sandbox (подтверждено: `curl` к googleapis.com/
openrouter.ai/rbc.ru возвращает `CONNECT tunnel failed, response 403`).

Отдельно от этого, у ассистента есть инструменты WebSearch/WebFetch - они
идут через ДРУГОЙ, Anthropic-хостируемый маршрут и реально работают из этой
сессии. Ими подтверждено, что живые Nike-related YouTube-видео и статьи
существуют и доступны по указанным ниже URL - **но это подтверждение сделано
инструментами ассистента, а не кодом приложения** (не через
`YouTubeAdapter`/`ArticlesPlatformAdapter`/OpenRouter). Реальная сквозная
проверка через сам код приложения возможна только на Render (или локально с
обычным сетевым доступом) - это описано в разделах ниже как "NOT TESTED (в
этой сессии)".

---

## YOUTUBE LIVE

**Статус: NOT TESTED через код приложения в этой сессии** (нет доступа к
googleapis.com из sandbox). Реализация (`app/ingestion/live_youtube.py`,
`app/platforms/youtube.py`) не менялась в рамках real-data upgrade (раздел
22 - "не переписывать существующую аналитику") и уже была рабочей до этого
задания; она честно возвращает `status="unavailable"`, если
`YOUTUBE_API_KEY` не задан - это подтверждено тестом
`tests/test_analyze_api.py::test_analyze_returns_analysis_id_and_get_resolves_it`.

Подтверждено НЕ через код проекта, а через WebSearch (реальные существующие
публичные YouTube-видео о Nike, найденные вживую в момент составления этого
документа):

- [COMERCIAL OFICIAL 2026 PARA EL MUNDIAL NIKE](https://www.youtube.com/watch?v=N7k74pu5kXM)
- [Nike - RIP The Script (2026 World Cup commercial)](https://www.youtube.com/watch?v=0ncJOic7o6U)
- [ALL NIKE SPONSORED TEAM FOR FIFA WORLD CUP 2026](https://www.youtube.com/watch?v=dBHXBra1slQ)

Это доказывает, что реальный публичный YouTube-контент про Nike существует
и находим прямо сейчас - но не то, что `YouTubeAdapter` конкретно его нашёл
и обработал (для этого нужен `YOUTUBE_API_KEY` и запуск на Render/локально с
обычной сетью).

## ARTICLES LIVE

**Статус: NOT TESTED через код приложения в этой сессии** (нет
`TAVILY_API_KEY`/`SERPAPI_KEY`, и прямой httpx-доступ к search API/сайтам
статей заблокирован proxy sandbox). Точечная доработка сделала Tavily
PRIMARY search provider для `ArticlesPlatformAdapter`, а SerpAPI - FALLBACK
(см. `app/search_client.py::SearchProviderRouter`,
`get_default_search_client()`): без обоих ключей `discover_brand_content()`
честно возвращает `status="unavailable"` (без demo/synthetic подмены) -
покрыто mocked-тестами (`test_18` в `tests/test_real_data_upgrade.py`, и
`tests/test_tavily_search_provider.py` - 12 тестов на primary/fallback/
normalization/dedup/coverage.search_provider/no-crash).

Подтверждено НЕ через код проекта, а через WebSearch/WebFetch:

- [5 самых необычных рекламных компаний Nike](https://sneakerhead.ru/blog/5-samyh-neobychnyh-reklamnyh-kompaniy-nike/) -
  реально существующая статья; WebFetch подтвердил заголовок и первое
  предложение текста живым запросом в момент составления этого документа.
- [Лучшие рекламные кампании Nike: как бренд ...](https://www.thepoizon.ru/article/best-nike-advertisements)
- [Nike: реклама, которая вдохновляет - Лайкни](https://www.likeni.ru/cases/nike-reklama-kotoraya-vdokhnovlyaet/)

`ArticleParser`/`ArticleClassifier` сами по себе (парсинг HTML, классификация
sponsored/organic) покрыты mocked HTTP-тестами (`test_5`, `test_6`, `test_7`
в `tests/test_real_data_upgrade.py`) - логика проверена, но не прогонялась
на реальном HTML вышеуказанных страниц в этой сессии.

## INSTAGRAM CONNECTOR

**Статус: NOT LIVE VALIDATED.** Instagram-авторизация (`local_connector/
instagram_connector.py`, `social_auth.py`) требует видимого браузерного окна
и живого пользовательского логина на Mac пользователя - физически
невозможно проверить из headless cloud sandbox. Реализация покрыта
mocked-тестами: job schema (`test_11`), DOM-based detect_integration/
extract_creator/build_social_integration (`test_14`), connector_offline/
manual_intervention_required статусы (`test_10`, `test_16`).

Формулировка по разделу 26 требований: "connector implementation ready / not
live validated" - именно этот случай.

## TIKTOK CONNECTOR

**Статус: NOT LIVE VALIDATED.** Симметрично Instagram - реализация
готова (`local_connector/tiktok_connector.py`), покрыта mocked-тестами
(`test_12`, `test_14`), но не проверена живым логином на Mac пользователя в
этой сессии.

## OPENROUTER VISION

**Статус: NOT LIVE VALIDATED** (нет `OPENROUTER_API_KEY`, и openrouter.ai
недоступен из sandbox proxy). `OpenRouterProvider`/`VisualEvidenceEnricher`
failsafe-поведение (нет ключа -> `unavailable`; сеть/HTTP ошибка -> `None`/
`degraded`; невалидный JSON -> `degraded`; whitelist сигналов) покрыто
mocked-тестами (`test_2`, `test_3`, `test_4` в
`tests/test_real_data_upgrade.py`) - логика проверена без реального вызова
OpenRouter API.

## TESTED

Через **mocked unit/integration-тесты** (`pytest`, вся команда
`python -m pytest -q` из корня проекта, виртуальное окружение `.venv`):

- Normal Analyze никогда не помечает данные `source_mode=demo` (тест 1).
- OpenRouter failsafe при отсутствии ключа/сети/невалидном JSON/429 (тест 2).
- OpenRouter JSON-strict парсинг ответа модели (тест 3).
- VisualEvidence строгая схема + signal whitelist + unavailable/degraded
  ветки (тест 4).
- Article parser: title/canonical_url/domain/author/published_at/main_text/
  outbound_links/image_urls + graceful degrade на HTTP-ошибке (тест 5).
- Sponsored/affiliate article classification (тест 6).
- Organic mention / editorial review НЕ считаются рекламой (тест 7).
- Publisher normalization, Publisher != Creator (тест 8).
- Local connector registration + отказ на неверный shared_secret (тест 9).
- Connector heartbeat -> online/connector_offline/manual_intervention_required
  переходы (тест 10).
- Instagram job schema, только фиксированные поля (тест 11).
- TikTok job schema, симметрично Instagram (тест 12).
- Connector НЕ может выполнить произвольную команду - ни в схемах, ни в коде
  `_dispatch()` (тест 13).
- Social connector result -> нормализованный Integration, недоступные поля -
  `null`, не выдуманы (тест 14).
- Evidence types разделены (FACT/COMPUTED/VISUAL_AI/AI_INFERENCE), visual
  escalation пишет именно VISUAL_AI-evidence (тест 15).
- Multi-platform routing: 4 запрошенные платформы -> 4 честные coverage-записи
  с правильными adapter-классами (тест 16).
- Screenshot cache не переснимает тот же URL повторно; VisualEvidenceEnricher
  кэширует по URL+screenshot hash, не шлёт одинаковый screenshot повторно в
  OpenRouter (тест 17).
- Полный mocked multi-source прогон orchestration pipeline (`_process_brand`
  -> analytical layers) без падений, с реальными (не synthetic) source_url в
  evidence confirmed-интеграции; сквозной `/api/analyze` по всем 4 платформам
  без падений (тест 18).
- Плюс весь существующий набор тестов проекта (142 теста, существовавших до
  этого задания) - не регрессировал.

Итог команды: **183 passed** (`python -m pytest -q`, см. TESTS в финальном
отчёте).

## NOT TESTED

- Реальный вызов YouTube Data API v3 через `YouTubeAdapter` с настоящим
  `YOUTUBE_API_KEY` (нет ключа + нет сетевого доступа к googleapis.com из
  этой sandbox).
- Реальный вызов Tavily Search API через `TavilySearchProvider` с настоящим
  `TAVILY_API_KEY`, и реальный вызов SerpAPI через `SerpApiSearchClient` с
  настоящим `SERPAPI_KEY` (оба недоступны из этой sandbox - см. выше).
- Реальный вызов OpenRouter Chat Completions API (text и vision) с настоящим
  `OPENROUTER_API_KEY`.
- Реальный Playwright-скриншот произвольной живой страницы + отправка в
  OpenRouter Vision (внутри самой app, не local connector).
- Реальный Instagram/TikTok логин, реальный сбор публичных постов через
  `local_connector/run.py` на живой машине пользователя.
- Реальный деплой на Render с новыми env-переменными (env-переменные
  добавлены в `render.yaml` как `sync: false`, но сам деплой/рестарт сервиса
  не выполнялся в рамках этой сессии).

## KNOWN LIMITATIONS

- Screenshot+vision эскалация работает только если на Render дополнительно
  один раз выполнить `playwright install chromium` (сознательно НЕ добавлено
  в `buildCommand` render.yaml, чтобы не рисковать существующим рабочим
  деплоем, раздел 31) - до этого шага она молча деградирует в
  `visual_ai_status=unavailable`, что не является ошибкой, а честным
  fallback.
- Articles/Web ограничен возможностями SerpAPI free/paid tier (лимиты
  запросов в минуту/день) - при превышении лимита конкретные запросы
  попадают в `queries_failed`, платформа переходит в `status="degraded"`,
  но не падает.
- Instagram/TikTok discovery внутри синхронного `/api/analyze` ждёт
  результата local connector не дольше `wait_seconds` (по умолчанию 6с) -
  если connector online, но не успел обработать job за это окно, платформа
  честно возвращает `status="degraded"` с `job_id` в `reason`; результат
  появится при следующем запуске Analyze (это осознанное ограничение
  синхронного MVP-эндпоинта, раздел 33 - "не добавлять отдельный scheduler").
- Instagram/TikTok DOM-selectors в `instagram_connector.py`/
  `tiktok_connector.py` рассчитаны на текущую (на момент разработки)
  структуру страниц этих платформ; при изменении разметки платформами
  selectors может понадобиться обновить - это ожидаемое поддерживающее
  обслуживание, а не архитектурный дефект.
- Данный документ и тесты честно отражают mocked/unit-уровень проверки;
  "PROOF OF REAL DATA" в финальном отчёте явно помечает, какие URL
  подтверждены WebSearch/WebFetch инструментами сессии (не кодом приложения).
