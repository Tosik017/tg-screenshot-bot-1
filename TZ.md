# Технічне завдання: tg-screenshot-bot — v5.2
> Актуальний стан: **реалізовано і задеплоєно на Render Free**, помічено тегом `stable-v2`.
> Цей документ — єдине джерело правди про архітектуру, рішення та черги завдань.

---

## 1. Суть проекту

Telegram-бот для **безпечного попереднього перегляду посилань** у чатах і групах.

Сценарій: учасник чату отримує підозріле посилання → надсилає боту → бот відповідає **миттєвим попередженням** + через 30–120 сек — **скриншотом сторінки** + **текстовою карткою** з метаданими. Людина бачить, що на сайті, не переходячи туди.

Цільова аудиторія: україномовні Telegram-групи, де активна фішинг-загроза.

---

## 2. Поточний стек

| Компонент | Версія | Роль |
|-----------|--------|------|
| Python | 3.12 | Мова |
| aiogram | 3.7.0 | Telegram Bot API, async |
| Playwright | 1.44.0 | Headless Chromium, скриншоти |
| playwright-stealth | 1.0.6 | Обхід Cloudflare detection |
| FastAPI | 0.111.0 | HTTP-сервер для health check |
| uvicorn | 0.30.0 | ASGI сервер |
| httpx | 0.27.0 | Async HTTP для метаданих |
| selectolax | 0.3.21 | C-парсер HTML, ~30× швидше BS4 |
| cachetools | 5.3.3 | TTLCache як база для типізованого кешу |
| loguru | 0.7.2 | Структуроване логування |
| Pillow | 10.3.0 | Нарізка скриншотів |
| psutil | 5.9.8 | Моніторинг RAM |

**Деплой**: Render Free (Docker), 512 МБ RAM, 0.1 CPU. GitHub → auto-deploy.
**Рестарт**: Render Dashboard → Manual Deploy, або `git commit --allow-empty -m "restart" && git push`.

---

## 3. Архітектура (поточна, реалізована)

```
User → Telegram
  ↓
aiogram Router (bot.py)
  ├─ RateLimitMiddleware: 5 сек / user_id → відповідь з відліком
  ├─ Фільтр бекологу: age > MAX_MSG_AGE (60 сек) → skip
  ├─ URL_RE: витягуємо першу http(s) URL
  ├─ security.is_safe() → блокуємо private IP
  ├─ cache.get(url) → перевірка кешу:
  │     ├─ photo / media_group / text → миттєва відповідь з метаданими
  │     └─ failure → миттєва відмова без Playwright
  └─ Cache miss:
       → queue_manager.enqueue(url):
            ├─ Дедуплікація: вже в роботі → той самий Future
            ├─ Черга повна → QueueFull → "Бот перевантажений"
            └─ Нова задача → Future + позиція
       → reply WARNING_INSTANT з позицією в черзі
       → паралельно: metadata.fetch(url) (httpx, 5 UA)
       → await future (chекаємо воркер з таймаутом 90с)
       → merge_meta(httpx_meta, browser_meta)
       → build_message(meta)
       → reply_photo / reply_media_group / reply text-only
       → cache.save_photo / save_media_group / save_text_only / save_failure
       → status.delete()

Worker (queue_manager._worker):
  └─ asyncio.wait_for(_processor(url), timeout=90)
       → screenshot.shoot(url) у семафорі
       → browser restart кожні 50 запитів
```

### Файлова структура

```
.
├── bot.py             # handle(), RateLimitMiddleware, _format_warning, _send_from_cache
├── main.py            # /health (queue + cache stats), SIGTERM, queue init
├── screenshot.py      # shoot(), _restart_browser(), semaphore=1
├── metadata.py        # httpx fetch + 5 UA fallback, JSON-LD парсинг
├── security.py        # is_safe() — синхронна перевірка private IP
├── cache.py           # типізовані записи + диференційований TTL + negative
├── queue_manager.py   # enqueue, _worker, QueueFull, get_stats
├── config.py          # ENV vars + константи
├── Dockerfile         # Playwright base + dumb-init
├── render.yaml
├── requirements.txt
├── .env.example
├── .gitignore
├── .dockerignore
├── LICENSE            # MIT
└── README.md
```

---

## 4. Ключові рішення та їх причини

### 4.1 SEMAPHORE = 1
Один скриншот одночасно. Два Chromium-контексти = ~600–700 МБ → OOM на 512 МБ Render Free. **Не змінювати без збільшення RAM.**

### 4.2 dumb-init у Docker
Без нього headless_shell після `browser.close()` залишається зомбі-процесом.
→ [Playwright Issue #34190](https://github.com/microsoft/playwright/issues/34190)

### 4.3 domcontentloaded + PAUSE_MS=3000
networkidle вішає бота на сайтах з аналітикою. domcontentloaded + пауза — баланс.

### 4.4 Паралельний збір метаданих і скриншоту
`asyncio.create_task(metadata.fetch)` + `await future` зі скриншотом — httpx і Playwright йдуть одночасно.

### 4.5 merge_meta: довший title перемагає
Browser бачить JS-рендер, httpx обходить Cloudflare. Беремо довший title/description.

### 4.6 _walk_jsonld з @graph
Elmir, Rozetka, Comfy ховають Product всередині `@graph`. Рекурсивний обхід вирішує.

### 4.7 PART_HEIGHT=1280, MAX_PARTS=4 → MAX_HEIGHT=5120px
Telegram ліміт ~10 МБ на фото. 4 частини — максимум без OOM на 512 МБ.

### 4.8 Slackbot UA для httpx
Cloudflare пропускає ботів соцмереж. Fallback chain: Slack → Twitter → Facebook → 2× Chrome.

### 4.9 Фільтр бекологу MAX_MSG_AGE=60сек
Після рестарту накопичуються старі повідомлення. `delete_webhook(drop_pending_updates=True)` + фільтр за `msg.date`.

### 4.10 Rate limiting з повідомленням
5 сек між запитами на user_id. Повідомляємо користувача з залишком секунд.

### 4.11 Browser restart кожні 50 запитів
Playwright поступово накопичує V8 heap. Перезапуск всередині семафору — безпечно.
→ [Playwright Issue #15400](https://github.com/microsoft/playwright/issues/15400)

### 4.12 Smart queue (queue_manager.py)
- **MAX_QUEUE_SIZE=10**: захист від лавини апдейтів. 11-й запит отримує "перевантажений".
- **TASK_TIMEOUT_SEC=90**: страховка від зависань. 20 (goto) + 3 (pause) + 20 (screenshot) + 47 запасу.
- **In-flight дедуплікація**: `dict[url, Future]`. Другий запит на той же URL чекає того самого Future.
- **Позиція в черзі**: показуємо `📊 Позиція: N. ~N сек.` коли є черга.
- **Один воркер** — відповідає `SEMAPHORE=1`.

### 4.13 Smart cache (cache.py)
- **Типізовані записи**: `photo` / `media_group` / `text` / `failure`.
- **Диференційований TTL**:
  - Звичайна сторінка: 1 година
  - Сторінка з ціною/рейтингом: 15 хвилин (контент змінюється)
  - Cloudflare-сайт (тільки текст): 5 хвилин (може розблокуватись)
  - Negative cache: 3 хвилини (даємо шанс відновитись)
- **Cache hit без httpx**: метадані вже в записі, повторний `metadata.fetch` не робиться.
- **Negative cache**: битий URL → миттєва відмова без Playwright протягом 3 хв.

### 4.14 Graceful shutdown (SIGTERM)
Render шле SIGTERM при зупинці контейнера. Порядок: stop polling → wait semaphore (60с таймаут) → close browser → stop uvicorn. Без цього можливі zombie Chromium.

### 4.15 /health endpoint
Перевіряє реальний стан: `_browser is not None` + `bot.get_me()`. Render бачить HTTP 503 при деградації. Включає статистику queue і cache.

---

## 5. Поточний стан: що реалізовано

### ✅ Інфраструктурний слой
- Rate limiting 5сек/user з повідомленням і відліком
- Browser restart кожні 50 скриншотів
- `/health` з реальною перевіркою (browser + bot + queue + cache)
- Graceful shutdown при SIGTERM
- Захист від бекологу (MAX_MSG_AGE=60)

### ✅ Smart queue
- Ліміт глибини 10
- Глобальний таймаут 90с
- Видимість позиції в черзі
- In-flight дедуплікація
- Єдиний фоновий воркер

### ✅ Smart cache
- Типізовані записи (photo / media_group / text / failure)
- Диференційований TTL за типом і вмістом
- Negative cache на 3 хвилини
- Cache hit повністю минає httpx
- Статистика в /health

### ✅ Базова обробка
- Миттєве попередження до генерації
- Паралельний збір метаданих + скриншот
- Розумне злиття метаданих
- Нарізка довгих сторінок (до 5120px, 4 частини)
- SSRF-фільтр (private IP) — синхронний
- Блокування реклами, медіа, шрифтів у Playwright
- Обхід cookie-банерів
- dumb-init у Docker

### ⚠️ Відомі обмеження (свідомі)
- Кеш живе тільки в пам'яті — після рестарту скидається
- На Render Free диск ephemeral — SQLite/Redis локально не має сенсу
- SSRF без перевірки після редиректів (відкочено)
- Без blacklist / WHOIS (відкочено, чекає поетапного повернення)

---

## 6. Черга завдань

### Готово до поетапного повернення (відкочено в попередньому циклі)
1. **SSRF після редиректів** — низький ризик, маленьке async-додавання
2. **WHOIS вік домену** — середній ризик, зовнішній мережевий запит
3. **Blacklist доменів** — середній ризик, ~145k у RAM

### Опціонально (на майбутнє, потребує переїзду з Render Free)
- OCR на скриншоті (pytesseract / easyocr) — шукає seed phrase, wallet, login
- Persistent кеш (Upstash Redis або SQLite на VPS)
- Sentry / Prometheus метрики
- Uptime Kuma моніторинг

### Свідомо пропущено
- VirusTotal — потребує реєстрацію API
- Google Safe Browsing — потребує реєстрацію API

---

## 7. Константи

### config.py
| Константа | Значення | Чому |
|-----------|----------|------|
| `SEMAPHORE` | 1 | Два Chromium = OOM на 512 МБ |
| `TIMEOUT_MS` | 20 000 | Playwright navigation timeout |
| `PAUSE_MS` | 3 000 | JS-рендер після domcontentloaded |
| `CACHE_SIZE` | 200 | Максимум записів у TTLCache |
| `CACHE_TTL` | 300 сек | (deprecated, тепер використовується для базового TTLCache, фактичний TTL — у cache.py) |

### bot.py
| Константа | Значення | Чому |
|-----------|----------|------|
| `MAX_MSG_AGE` | 60 сек | Фільтр бекологу |
| `RATE_LIMIT_SEC` | 5 сек | Між запитами від одного user_id |

### screenshot.py
| Константа | Значення | Чому |
|-----------|----------|------|
| `MAX_PARTS` | 4 | Захист від OOM |
| `PART_HEIGHT` | 1 280 | Telegram ліміт ~10 МБ |
| `MAX_HEIGHT` | 5 120 | PART_HEIGHT × MAX_PARTS |
| `RESTART_EVERY` | 50 | Скриншотів між перезапусками браузера |

### queue_manager.py
| Константа | Значення | Чому |
|-----------|----------|------|
| `MAX_QUEUE_SIZE` | 10 | Захист від лавини апдейтів |
| `TASK_TIMEOUT_SEC` | 90 | Страховка від зависань |

### cache.py
| Константа | Значення | Тип записів |
|-----------|----------|-------------|
| `TTL_PHOTO` | 3600 сек | Одиночне фото, стабільний контент |
| `TTL_MEDIA_GROUP` | 3600 сек | Багатократне фото, довга сторінка |
| `TTL_TEXT_ONLY` | 300 сек | Cloudflare/блок — може розблокуватись |
| `TTL_HAS_PRICE` | 900 сек | Товар з ціною — може змінитись |
| `TTL_FAILURE` | 180 сек | Negative cache — даємо шанс відновитись |

---

## 8. Команди проекту

```bash
# Запушити зміни
git add . && git commit -m "опис" && git push

# Форсований рестарт
git commit --allow-empty -m "restart" && git push

# Подивитись весь код
find . -not -path './.git/*' | sort && echo "---" && for f in $(find . -name "*.py" -o -name "*.txt" -o -name "Dockerfile" | grep -v .git | sort); do echo "== $f =="; cat "$f"; done

# Зберегти дамп
{ echo '# Дамп'; echo '```'; find . -not -path './.git/*' | sort; echo '```'; for f in $(find . -name "*.py" -o -name "*.txt" -o -name "Dockerfile" | grep -v .git | sort); do echo "## $f"; echo '```python'; cat "$f"; echo '```'; done; } > project_dump.md
```

---

## 9. Деплой

**Поточне середовище**: Render Free
- 750 годин/місяць
- Sleep після 15 хв idle, cold start ~30–60 сек
- Health Check Path: `/health` (не `/ping`)

**TelegramConflictError при деплої** — Render Dashboard → Manual Deploy → Deploy latest commit.

**Шлях до production 24/7**:
1. Oracle Cloud Always Free ARM (4 OCPU, 24 ГБ RAM) — безкоштовно
2. Hetzner CX22 (~€6/місяць) — найкращий price/performance

---

## 10. Теги Git

| Тег | Зміст |
|-----|-------|
| `stable-v1` (`c6cef02`) | Мінімум: rate limit (тихий дроп) + browser restart |
| `stable-v2` | Все інфраструктурне: rate limit з notify + /health + graceful shutdown + smart queue + smart cache |

---

## 11. Версії TZ

| Версія | Що змінилось |
|--------|--------------|
| v1–v3 | Початкові концепції |
| v4.1 | Single screenshot, asyncio паралелізм, Cloudflare fallback |
| v5.0 | Актуалізація під реальний код, черга завдань по рівнях |
| v5.1 | Додано: RateLimitMiddleware, browser restart, SSRF після редиректів, /health, blacklist, WHOIS, graceful shutdown |
| **v5.2** | Відкочено v5.1 → stable-v1 → повернуто інфраструктурний слой (rate limit notify + /health + graceful shutdown). Додано: **Smart queue** (queue_manager.py — лімит/таймаут/позиція/дедуп) і **Smart cache** (cache.py — типізовані записи + диференційований TTL + negative). Helmет тегом `stable-v2`. Антифішинг (SSRF після редиректів, WHOIS, blacklist) — чекає поетапного повернення. |
