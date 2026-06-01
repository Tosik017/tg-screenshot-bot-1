# 🛡️ Telegram Screenshot Bot

[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-ready-blue.svg)](https://www.docker.com/)
[![Playwright](https://img.shields.io/badge/Playwright-1.44.0-green.svg)](https://playwright.dev/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Stable](https://img.shields.io/badge/Stable-v2-success.svg)](https://github.com/Tosik017/tg-screenshot-bot-1/releases/tag/stable-v2)

Telegram-бот для **безопасного предпросмотра ссылок**. Вместо того чтобы переходить по подозрительной ссылке, пользователь отправляет её боту и получает скриншот страницы + текстовую карточку с метаданными.

Оптимизирован под **Render Free (512 МБ RAM, 0.1 CPU)** и разворачивается одним кликом.

---

## ✨ Возможности

### 🚨 Защита пользователя
- **Мгновенное предупреждение** ещё до генерации скриншота — пользователь видит что нужно подождать и не переходить по ссылке
- **SSRF-фильтр** — блокирует обращения к приватным IP-диапазонам (`127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, link-local, multicast, IPv6 private)
- **Текстовая карточка** с метаданными: site_name, title, brand, price, rating, description (OpenGraph + Twitter Cards + JSON-LD Schema.org Product)
- **Disclaimer-цитата** в каждой карточке — напоминание не вводить пароли и данные карт

### ⚡ Smart queue (умная очередь)
- **Лимит глубины очереди** (`MAX_QUEUE_SIZE=10`) — при переполнении бот мягко отказывает: «бот перегружен, попробуйте через минуту»
- **Глобальный таймаут на задачу** (`TASK_TIMEOUT_SEC=90`) — защита от зависшего Playwright
- **Видимость позиции в очереди** — пользователь видит `📊 Ваша позиція в черзі: N. Орієнтовний час: ~N сек.`
- **In-flight дедупликация** — если URL уже обрабатывается, второй запрос подцепляется к существующему результату вместо запуска нового Playwright
- **Единый фоновый воркер** — соответствует `SEMAPHORE=1`

### 💾 Smart cache (умный кэш)
- **Типизированные записи**: `photo` / `media_group` / `text` / `failure`
- **Дифференцированный TTL по типу контента**:
  - Обычная страница со скриншотом — **1 час**
  - Длинная страница (медиагруппа) — **1 час**
  - Товарная страница с ценой/рейтингом — **15 минут** (цены меняются)
  - Cloudflare-блокировка (только метаданные) — **5 минут** (может разблокироваться)
  - Negative cache (битые URL) — **3 минуты** (защита от повторных дёрганий)
- **Cache hit без httpx** — метаданные берутся из кэша, без повторного сетевого запроса
- **Статистика в `/health`** — счётчики по типам

### 🏎️ Производительность
- **Параллельный сбор** — `metadata.fetch` (httpx) и `screenshot.shoot` (Playwright) идут одновременно
- **Browser restart каждые 50 скриншотов** — защита от утечек памяти Playwright (V8 heap, internal page cache)
- **Нарезка длинных страниц** через Pillow на части до 5120 px (4 × 1280)
- **Блокировка рекламы, медиа, шрифтов, аналитики** в Playwright — ускоряет загрузку и экономит RAM
- **Мобильный viewport** 390 × 844 @ 2× DPR — реалистичные скриншоты

### 🛡️ Стабильность
- **Rate limiting** — 5 секунд между запросами от одного user_id, уведомление с обратным отсчётом
- **Graceful shutdown** — при SIGTERM (деплой) корректно дожидает текущий скриншот и закрывает браузер, без zombie-процессов
- **`/health` endpoint** — реальная проверка состояния браузера, бота, очереди и кэша. HTTP 503 при деградации
- **dumb-init** в Docker — предотвращает zombie-процессы Chromium
- **Фильтр бэклога** — игнорирует сообщения старше 60 секунд (защита от спама после рестарта)
- **`delete_webhook(drop_pending_updates=True)`** — сброс накопленной очереди апдейтов при старте

### 🌐 Обход блокировок
- **5 User-Agent fallback** для httpx — Slackbot, Twitterbot, facebookexternalhit, 2× Chrome
- **playwright-stealth** — скрытие признаков headless-браузера
- **Автоматическое закрытие cookie-баннеров** — 7 паттернов селекторов
- **Slackbot UA обходит Cloudflare** — метаданные извлекаются даже когда Playwright блокируется

### 🧠 Умный парсинг
- **JSON-LD Schema.org Product** — корректно извлекает цену, бренд, рейтинг
- **Обход `@graph`** — фикс для Elmir, Rozetka, Comfy, которые прячут Product не первым объектом
- **merge_meta** — объединяет данные из httpx (Cloudflare-friendly) и Playwright (JS-рендер), берёт более полные значения

---

## 🏗️ Архитектура

```mermaid
flowchart TD
    A[User → Telegram] --> RL{RateLimit<br/>5 sec/user?}
    RL -->|Превышен| RLM[⏳ Зачекайте N сек.]
    RL -->|OK| AGE{Age > 60 sec?}
    AGE -->|Да| SKIP[Skip stale]
    AGE -->|Нет| URL[Parse URL]
    URL --> SSRF{SSRF<br/>private IP?}
    SSRF -->|Да| BLOCK[🚫 Заблокировано]
    SSRF -->|Нет| CACHE{Cache hit?}
    CACHE -->|photo/media/text| FAST[⚡ Мгновенный ответ]
    CACHE -->|failure| NEG[🚫 Сторінка недоступна]
    CACHE -->|miss| Q{queue_manager<br/>.enqueue}
    Q -->|QueueFull| OVER[⚠️ Бот перевантажений]
    Q -->|Duplicate| DUP[🔁 Вже обробляється]
    Q -->|OK| WARN[⚠️ WARNING с позицией в очереди]
    WARN --> PAR[Параллельно]
    PAR --> M[metadata.fetch<br/>httpx + 5 UA]
    PAR --> W[Worker: screenshot.shoot<br/>Playwright + 90s timeout]
    W --> R[browser restart<br/>каждые 50 запросов]
    M --> MERGE[merge_meta]
    W --> MERGE
    MERGE --> BUILD[build_message]
    BUILD --> SEND[reply_photo /<br/>reply_media_group]
    SEND --> SAVE[cache.save_photo /<br/>save_media_group / save_text / save_failure]
```

---

## 🛠️ Стек технологий

| Компонент | Версия | Роль |
|-----------|--------|------|
| Python | 3.12 | Базовый язык |
| aiogram | 3.7.0 | Telegram Bot API, async-first |
| Playwright | 1.44.0 | Headless Chromium для скриншотов |
| playwright-stealth | 1.0.6 | Обход headless-detection |
| FastAPI | 0.111.0 | HTTP-сервер для healthcheck |
| uvicorn | 0.30.0 | ASGI сервер |
| httpx | 0.27.0 | Async HTTP для метаданных |
| selectolax | 0.3.21 | C-парсер HTML (~30× быстрее BeautifulSoup) |
| cachetools | 5.3.3 | TTLCache как база для типизированного кэша |
| loguru | 0.7.2 | Структурированное логирование |
| Pillow | 10.3.0 | Нарезка скриншотов |
| psutil | 5.9.8 | Мониторинг RAM |

---

## 📁 Структура проекта

```
tg-screenshot-bot-1/
├── bot.py              # handle(), RateLimitMiddleware, _format_warning, _send_from_cache
├── main.py             # точка входа, /health, SIGTERM handler, queue init
├── screenshot.py       # shoot(), browser restart, нарезка через Pillow
├── metadata.py         # httpx fetch + 5 UA fallback, JSON-LD парсинг
├── security.py         # SSRF: блокировка private IP
├── cache.py            # типизированные записи + дифференцированный TTL + negative
├── queue_manager.py    # enqueue, _worker, QueueFull, дедупликация, статистика
├── config.py           # ENV vars + константы
├── Dockerfile          # Playwright base image + dumb-init
├── render.yaml         # Render Blueprint
├── requirements.txt
├── .env.example
├── .gitignore
├── .dockerignore
├── LICENSE
└── README.md
```

---

## 📋 Требования

- **Python** 3.12+
- **Docker** 20+ (для контейнерного запуска)
- **RAM** ≥ 512 МБ
- **CPU** ≥ 1 core (Playwright требователен)
- **BOT_TOKEN** от [@BotFather](https://t.me/BotFather)

---

## 🚀 Быстрый старт

### Получение Telegram Bot Token

1. Откройте Telegram и найдите [@BotFather](https://t.me/BotFather)
2. Отправьте `/newbot`, укажите имя и username (должен заканчиваться на `bot`)
3. Скопируйте токен формата `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`

### Docker (рекомендуется)

```bash
git clone https://github.com/Tosik017/tg-screenshot-bot-1.git
cd tg-screenshot-bot-1

docker build -t tg-screenshot-bot .

docker run -d \
  --name tg-screenshot-bot \
  --restart unless-stopped \
  -e BOT_TOKEN="YOUR_BOT_TOKEN_HERE" \
  -p 8000:8000 \
  tg-screenshot-bot
```

### Docker Compose

```yaml
version: '3.8'
services:
  bot:
    build: .
    container_name: tg-screenshot-bot
    restart: unless-stopped
    mem_limit: 512m
    environment:
      - BOT_TOKEN=${BOT_TOKEN}
      - PORT=8000
    ports:
      - "8000:8000"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
```

```bash
echo "BOT_TOKEN=YOUR_BOT_TOKEN_HERE" > .env
docker-compose up -d
```

### Локальный запуск (без Docker)

```bash
git clone https://github.com/Tosik017/tg-screenshot-bot-1.git
cd tg-screenshot-bot-1

python -m venv venv
source venv/bin/activate

pip install -r requirements.txt
playwright install chromium

export BOT_TOKEN="YOUR_BOT_TOKEN_HERE"
python main.py
```

---

## ☁️ Деплой на Render

1. Форкните репозиторий на GitHub
2. На [render.com](https://render.com) → **New +** → **Web Service**
3. Выберите ваш форк, Runtime: **Docker**, Branch: `main`
4. В **Environment** добавьте: `BOT_TOKEN` = ваш токен
5. **Health Check Path**: `/health`
6. **Create Web Service**

⚠️ **Ограничения Render Free**:
- 750 часов/месяц
- Sleep после 15 минут idle, cold start 30–60 сек
- 512 МБ RAM, 0.1 CPU
- Ephemeral диск (данные не сохраняются между рестартами)

---

## 🖥️ Деплой на VPS (Ubuntu 22.04)

```bash
sudo apt update && sudo apt install -y docker.io docker-compose
sudo systemctl enable --now docker

git clone https://github.com/Tosik017/tg-screenshot-bot-1.git
cd tg-screenshot-bot-1
echo "BOT_TOKEN=YOUR_BOT_TOKEN_HERE" > .env
docker-compose up -d
```

---

## 🔐 Переменные окружения

| Переменная | Обязательная | Описание | По умолчанию |
|------------|--------------|----------|--------------|
| `BOT_TOKEN` | ✅ | Токен от @BotFather | — |
| `PORT` | ❌ | Порт для FastAPI сервера | `8000` |

---

## ⚙️ Конфигурация

Все константы в соответствующих модулях:

### Общие (`config.py`)
| Константа | Значение | Зачем |
|-----------|----------|-------|
| `SEMAPHORE` | 1 | Один скриншот одновременно — два Chromium = OOM на 512 МБ |
| `TIMEOUT_MS` | 20 000 | Таймаут навигации Playwright (20 сек) |
| `PAUSE_MS` | 3 000 | Пауза после `domcontentloaded` для JS-рендера |
| `CACHE_SIZE` | 200 | Максимум записей в кэше |

### Bot (`bot.py`)
| Константа | Значение | Зачем |
|-----------|----------|-------|
| `MAX_MSG_AGE` | 60 сек | Фильтр бэклога после рестарта |
| `RATE_LIMIT_SEC` | 5 сек | Между запросами от одного user_id |

### Screenshot (`screenshot.py`)
| Константа | Значение | Зачем |
|-----------|----------|-------|
| `MAX_PARTS` | 4 | Макс. частей при нарезке (защита от OOM) |
| `PART_HEIGHT` | 1 280 | Высота одной части (Telegram лимит ~10 МБ) |
| `MAX_HEIGHT` | 5 120 | `PART_HEIGHT × MAX_PARTS` |
| `RESTART_EVERY` | 50 | Скриншотов между перезапусками браузера |

### Queue (`queue_manager.py`)
| Константа | Значение | Зачем |
|-----------|----------|-------|
| `MAX_QUEUE_SIZE` | 10 | Защита от лавины апдейтов |
| `TASK_TIMEOUT_SEC` | 90 | Страховка от зависаний |

### Cache (`cache.py`)
| Константа | Значение | Тип записей |
|-----------|----------|-------------|
| `TTL_PHOTO` | 3600 сек | Одиночное фото, стабильный контент |
| `TTL_MEDIA_GROUP` | 3600 сек | Многочастное фото, длинная страница |
| `TTL_TEXT_ONLY` | 300 сек | Cloudflare/блок — может разблокироваться |
| `TTL_HAS_PRICE` | 900 сек | Товар с ценой — может измениться |
| `TTL_FAILURE` | 180 сек | Negative cache — даём шанс восстановиться |

---

## 📝 Использование

### Базовый сценарий

1. Отправьте боту ссылку: `https://example.com`
2. Бот мгновенно отвечает: **"🚨⚠️ СТОП! НЕ ПЕРЕХОДЬТЕ ЗА ПОСИЛАННЯМ!"**
3. Если очередь не пуста — показывается **позиция и оценка времени**
4. Через 5–60 секунд получаете:
   - Скриншот страницы (одно фото или медиагруппа из 2–4 частей)
   - Карточку: site_name, title, brand, price, rating, description
   - Disclaimer в виде цитаты

### Реакции бота

| Ситуация | Ответ |
|----------|-------|
| Нормальная ссылка | Скриншот + текстовая карточка |
| Cloudflare-сайт | Только текстовая карточка (метаданные через httpx) |
| Повтор той же ссылки (cache hit) | Мгновенно из кэша |
| URL уже обрабатывается (дедуп) | `🔁 Це посилання вже обробляється` |
| Очередь полна | `⚠️ Бот зараз перевантажений` |
| Битый URL (negative cache) | `🚫 Сторінка недоступна` |
| Приватный IP | `🚫 Посилання веде на недоступний ресурс.` |
| Превышен rate limit | `⏳ Зачекайте N сек. перед наступним запитом.` |
| Сообщение без URL | Бот молчит |
| Сообщение старше 60 сек | Skip (защита от бэклога) |
| Ошибка обработки / таймаут | `❌ Не вдалось обробити посилання.` |

### Healthcheck

```bash
curl https://your-bot.onrender.com/health
```

Ответ:
```json
{
  "status": "ok",
  "browser": true,
  "bot": true,
  "queue": {"queue_size": 0, "queue_max": 10, "inflight_urls": 0},
  "cache": {"size": 12, "maxsize": 200, "photo": 8, "media_group": 2, "text": 1, "failure": 1}
}
```

HTTP 200 — всё работает. HTTP 503 — сервис деградировал.

---

## 🔧 Troubleshooting

### TelegramConflictError при деплое

**Причина**: Старый инстанс ещё не умер, новый уже стартует — оба пытаются получать апдейты.

**Решение**:
- Render Dashboard → **Manual Deploy** → **Deploy latest commit**
- Или: Settings → **Suspend** → подождать 10 сек → **Resume**
- Или: `git commit --allow-empty -m "restart" && git push`

### Бот не отвечает после деплоя

```bash
# Проверка токена
curl https://api.telegram.org/bot<TOKEN>/getMe

# Проверка состояния
curl https://your-bot.onrender.com/health

# Логи: Render Dashboard → Logs
```

### Cloudflare блокирует скриншот

Известное ограничение. Playwright показывает страницу CF challenge ("Just a moment..."), но **метаданные всё равно извлекаются** через httpx с `Slackbot-LinkExpanding` User-Agent. Результат кэшируется как `text` на 5 минут — даём шанс разблокировке.

### Playwright OOM (Out of Memory)

Если контейнер падает по памяти:
- Уменьшите `MAX_PARTS` в `screenshot.py` (по умолчанию 4)
- Уменьшите `RESTART_EVERY` (по умолчанию 50)
- Увеличьте RAM до 1 ГБ

### Зомби-процессы Chromium

Если видите много `headless_shell` после `browser.close()` — проверьте что в `Dockerfile` есть `ENTRYPOINT ["dumb-init", "--"]`.

---

## 🔒 Безопасность

### SSRF-защита

Бот блокирует обращения к приватным IP-диапазонам:
- `127.0.0.0/8` (localhost)
- `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` (private networks)
- `169.254.0.0/16` (link-local)
- `100.64.0.0/10` (CGNAT)
- `198.18.0.0/15` (benchmarking)
- `224.0.0.0/4`, `240.0.0.0/4` (multicast, reserved)
- `fc00::/7`, `fe80::/10`, `::1` (IPv6 private)

**Известное ограничение**: проверка по DNS до запроса, без верификации финального URL после редиректов.

### Защита от перегрузки

- **Rate limit** 5 сек/user — защита от флуда в групповых чатах
- **Queue depth limit** 10 — защита от лавины апдейтов
- **Task timeout** 90 сек — защита от зависшего Playwright
- **Negative cache** 3 минуты — защита от повторных дёрганий битых URL
- **Browser restart** каждые 50 запросов — защита от утечек памяти

### Bot Token

- Хранится только в env var, не в коде
- Не коммитьте `.env` (есть в `.gitignore`)
- При утечке — создайте нового бота через `/deletebot` в @BotFather

---

## ❓ FAQ

**Q: Почему бот иногда долго отвечает?**
A: На Render Free после 15 минут idle происходит cold start (~30–60 сек). Также если перед вами есть очередь — бот покажет позицию и оценку времени.

**Q: Поддерживает ли бот webhook вместо polling?**
A: Нет, polling надёжнее для free tier (не требует постоянного доступа извне).

**Q: Поддерживает ли бот PDF?**
A: Нет, только PNG.

**Q: Можно ли ограничить доступ только для определённых пользователей?**
A: Да, добавьте проверку `message.from_user.id` в начало `handle()` в `bot.py`.

**Q: Как долго хранится кэш?**
A: От 3 минут (negative cache) до 1 часа (обычные страницы). Зависит от типа контента. После рестарта обнуляется.

**Q: Работает ли на ARM (Oracle Cloud, Raspberry Pi)?**
A: Требует ARM-сборки Chromium через `playwright install chromium` на ARM-машине.

**Q: Можно ли увеличить параллельность?**
A: На Render Free `SEMAPHORE=1` обязательно. На VPS с 2+ ГБ RAM можно увеличить до 2–3 и одновременно поднять `MAX_QUEUE_SIZE`.

**Q: Что делать при утечке токена?**
A: В @BotFather: `/deletebot`, создать нового, обновить env var.

---

## ⚠️ Известные ограничения

- **Кэш не персистентный** — сбрасывается при рестарте
- **Polling, не webhook** — возможна задержка 1–2 сек
- **Cloudflare частично блокирует** — скриншот может не пройти, но метаданные работают
- **Render Free спит** — cold start после 15 минут idle
- **SSRF только до запроса** — без проверки финального URL после редиректов

---

## 🚀 Платформы деплоя

| Платформа | $/мес | RAM | Подходит для |
|-----------|-------|-----|--------------|
| Render Free | $0 | 512 МБ | Тестирование, light usage |
