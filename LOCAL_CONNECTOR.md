# Local Social Connector (Instagram / TikTok)

Instagram и TikTok требуют авторизованную сессию реального пользователя, а
Render (cloud) не может держать такую сессию честно и безопасно. Поэтому эти
две платформы работают через отдельный компонент - **Local Connector**, -
который ты запускаешь на своём собственном Mac (или любой другой машине с
графическим Chromium). Он забирает задания (jobs) у Render, собирает
публичные данные через твой собственный залогиненный браузер и отправляет
нормализованные результаты обратно.

YouTube и Articles/Web работают из облака (Render) и НЕ требуют local
connector - см. основной README.

---

## INSTALL

1. Клонируй/скачай тот же репозиторий проекта на свой Mac (тот же код, что
   задеплоен на Render).
2. Установи зависимости:

   ```bash
   pip install -r requirements.txt
   playwright install chromium
   ```

   (На Render `playwright install chromium` НЕ выполняется - см. `render.yaml` -
   поэтому screenshot/vision-эскалация на самом Render мягко деградирует.
   Local connector, наоборот, обязательно должен иметь установленный chromium -
   это его основной инструмент.)

3. Скопируй `.env.example` в `.env` (в корне проекта, там же, где запускаешь
   `local_connector/run.py`) и заполни:

   ```env
   RENDER_BASE_URL=https://<твой-render-домен>.onrender.com
   CONNECTOR_SHARED_SECRET=<то же значение, что задано в Render env CONNECTOR_SHARED_SECRET, если задано>
   ```

   `CONNECTOR_SHARED_SECRET` опционален: если на Render эта переменная не
   задана - можно оставить пустым и здесь.

## RUN

Из корня проекта:

```bash
python local_connector/run.py
```

При первом запуске connector сам зарегистрируется на Render (`POST
/api/connectors/register`) и сохранит `connector_id`/`connector_token` в
`.local_sessions/connector_credentials.json` (никогда не коммитится, см.
`.gitignore`). После этого он в бесконечном цикле:

1. раз в ~20 секунд отправляет heartbeat (`POST /api/connectors/heartbeat`) -
   именно по heartbeat Render понимает, что connector жив (UI показывает
   "Instagram/TikTok - Connected"); если heartbeat не приходит дольше
   `CONNECTOR_OFFLINE_AFTER_SECONDS` (по умолчанию 90с) - платформа честно
   помечается `connector_offline`;
2. раз в ~5 секунд опрашивает `GET /api/connectors/jobs` - если Render создал
   задание (потому что кто-то запустил Analyze с Instagram/TikTok) - забирает
   его;
3. выполняет job через `local_connector/instagram_connector.py` или
   `tiktok_connector.py` (авторизованный Playwright-браузер, публичная
   навигация/поиск, DOM extraction, опциональный screenshot);
4. отправляет результат обратно (`POST /api/connectors/results`).

Останавливается по Ctrl+C.

## INSTAGRAM LOGIN

При первом job на платформу `instagram` (или сразу при первом запуске, если
хочешь войти заранее) откроется **видимое** окно Chromium с формой входа
Instagram. Тебе нужно:

1. Вручную ввести свой логин/пароль в этом окне (код НИКОГДА не запрашивает и
   не хранит пароль - ты вводишь его сам, напрямую в браузер).
2. Пройти 2FA, если Instagram его запросит (SMS-код, приложение-аутентификатор
   и т.п.) - тоже вручную, в том же окне.
3. Когда лента/профиль полностью загрузились - вернуться в терминал и нажать
   Enter (там будет висеть `input()`, ждущий подтверждения).

После этого сессия (cookies/localStorage) сохраняется в
`.local_sessions/instagram_state.json`, и все последующие запуски connector-а
переиспользуют её headless-режимом - повторный ручной вход не нужен, пока
Instagram не сбросит сессию сам.

Если в любой момент Instagram показывает CAPTCHA/challenge/checkpoint -
connector НЕ пытается их обойти. Он останавливает job, отправляет на Render
`status="manual_intervention_required"`, и в UI это честно отображается как
статус, требующий твоего ручного участия (см. `PlatformSourceStatus`).
Тебе нужно самостоятельно открыть тот же браузерный профиль/сессию, пройти
challenge вручную, и в следующий job connector продолжит нормально.

## TIKTOK LOGIN

Симметрично Instagram: первый запуск открывает видимое окно с формой входа
TikTok (`https://www.tiktok.com/login`), ты вручную логинишься (+ 2FA, если
запросят), нажимаешь Enter в терминале, сессия сохраняется в
`.local_sessions/tiktok_state.json`. CAPTCHA/verify/check-потоки тоже
детектируются и приводят к `manual_intervention_required`, без попыток
автоматического обхода.

## SECURITY

- Пароли НИКОГДА не запрашиваются и не сохраняются кодом проекта - ты вводишь
  их напрямую в окно браузера, которое открывает Playwright.
- Файлы сессии (`.local_sessions/instagram_state.json`,
  `.local_sessions/tiktok_state.json`, `connector_credentials.json`)
  находятся ТОЛЬКО на твоём диске:
  - никогда не отправляются на Render;
  - никогда не отправляются в OpenRouter;
  - никогда не коммитятся в git (`.gitignore` явно их исключает, включая
    `.local_sessions/` целиком).
- `connector_token`, выданный при регистрации, - это просто bearer-токен для
  вызова `/api/connectors/heartbeat|jobs|results`, не пароль от соцсети.
- Job, который присылает Render, - это фиксированная pydantic-схема
  (`ConnectorJob`: `job_id, analysis_id, platform, brand, aliases, settings,
  created_at`). Никаких полей "command"/"script"/"code" не существует -
  connector физически не может быть использован для выполнения произвольных
  команд с твоей машины.
- Connector никогда не пытается автоматически решать CAPTCHA или обходить
  anti-bot защиту - при первом признаке challenge он останавливается и просит
  твоего ручного участия.

## WHAT STAYS LOCAL

Остаётся ТОЛЬКО на твоей машине, никогда не уходит на Render/куда-либо ещё:

- пароли от Instagram/TikTok (никогда даже не видны коду);
- файлы сессии `.local_sessions/*.json`;
- сам факт, как именно ты логинишься (2FA-метод и т.п.).

Уходит на Render (только нормализованные публичные данные, раздел 15-16):
username, profile_url, followers (если видно), post/video_url, caption,
published_at (если доступно), likes/comments/views (если видно), hashtags,
brand_mention/paid_partnership_label/collaboration_label, опциональный
screenshot (base64 PNG) - используется ТОЛЬКО если детектор признал случай
ambiguous (`manual_review`) для последующей visual-эскалации через OpenRouter.

## TROUBLESHOOTING

- **"Playwright не установлен"** - выполни `playwright install chromium`
  (одноразово).
- **Connector регистрируется, но Render всё равно показывает
  "connector_offline"** - проверь, что процесс `run.py` продолжает работать
  (heartbeat отправляется раз в ~20с) и что `RENDER_BASE_URL` указывает на тот
  же Render-инстанс, куда смотрит UI.
- **`401` при register/heartbeat/jobs/results** - проверь, что
  `CONNECTOR_SHARED_SECRET` (если задан на Render) совпадает в `.env` на
  твоей машине, и что `.local_sessions/connector_credentials.json` не устарел
  (удали файл - connector зарегистрируется заново при следующем запуске).
- **Статус "manual_intervention_required" не проходит** - значит Instagram/
  TikTok всё ещё показывает challenge/CAPTCHA этому браузерному профилю.
  Открой тот же профиль вручную (можно временно поставить
  `headless_if_authenticated=False` в `social_auth.py` для дебага), пройди
  challenge вручную, дождись обычной ленты/профиля - дальше connector
  продолжит нормально.
- **Сессия истекла (Instagram/TikTok разлогинил)** - удали соответствующий
  `.local_sessions/<platform>_state.json` и перезапусти `run.py` - откроется
  окно для повторного ручного входа.
- **Job приходит, но connector падает с исключением** - это ошибка в самом
  коде extraction (DOM-структура платформы могла измениться), не проблема
  безопасности; смотри traceback в консоли и, при необходимости, обнови
  selectors в `instagram_connector.py`/`tiktok_connector.py`.
