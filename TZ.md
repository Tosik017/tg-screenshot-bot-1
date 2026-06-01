# Техническое задание
## Telegram-бот: безопасный предпросмотр ссылок

**Версия 4.1** · Рабочая · Деплой на Render Free

---

## Назначение

Контекст для нового чата с Claude или передачи другому разработчику. Описывает что делает бот, как устроен, и **почему приняты именно такие решения** — чтобы не повторять путь проб и ошибок.

---

## 1. Что делает бот

Пользователь присылает в чат ссылку. Бот:

1. **Моментально** отвечает ярким предупреждением «Не переходите по ссылке» — до того как человек успеет кликнуть.
2. Параллельно делает скриншот страницы (мобильный вид) и вытягивает метаданные (название, цена, описание, бренд).
3. Отправляет скриншот + текстовую карточку с данными + предупреждение-цитату.
4. Если сайт за Cloudflare и скриншот не получился — отправляет только текстовую карточку с предупреждением.

Главная ценность для пользователя — **не переходить по подозрительной ссылке**, а увидеть что там, безопасно.

---

## 2. Технологический стек

| Компонент | Версия | Зачем именно он |
|---|---|---|
| Python | 3.12 | Базовый язык |
| aiogram | 3.7.0 | Telegram Bot API. Async-first — меньше RAM чем python-telegram-bot. **Важно:** туториалы под 2.x не подходят, API переписан |
| Playwright + Chromium | 1.44.0 | Скриншоты. Лучше Selenium по RAM и стабильности |
| playwright-stealth | 1.0.6 | Скрывает признаки headless-браузера от Cloudflare |
| FastAPI + uvicorn | 0.111.0 + 0.30.0 | HTTP-сервер для `/ping`. **Обязателен** — Render Free требует открытый порт, иначе деплой падает с `Port scan timeout` |
| httpx | 0.27.0 | Асинхронный HTTP для метаданных без браузера |
| selectolax | 0.3.21 | C-парсер HTML. В 30× быстрее BeautifulSoup, меньше RAM |
| cachetools | 5.3.3 | In-memory LRU кэш |
| loguru | 0.7.2 | Логи |
| psutil | 5.9.8 | Мониторинг RAM |
| Pillow | 10.3.0 | Нарезка скриншота на части |
| dumb-init | (в Docker) | **Критично:** Playwright 1.50+ оставляет zombie-процессы headless_shell после browser.close(). Без dumb-init они копятся и съедают RAM |

---

## 3. Поток данных (полный)

```
Пользователь присылает URL в чат
            │
            ▼
[security.py] Проверка SSRF
    DNS resolve → проверка IP на приватные диапазоны
    Заблокирован → "🚫 Ссылка ведёт на недоступный ресурс"
            │ безопасно
            ▼
[cache.py] Проверка кэша по MD5(url)
    Есть file_id → подгружаем метаданные через httpx →
                   отправляем фото + карточку → КОНЕЦ
            │ нет в кэше
            ▼
Моментально отправляем WARNING_INSTANT
(пользователь видит предупреждение СРАЗУ)
            │
            ▼
ПАРАЛЛЕЛЬНО (asyncio.gather):
    ┌─────────────────────┬──────────────────────────┐
    │ [metadata.fetch]    │ [screenshot.shoot]       │
    │ httpx, Slackbot UA  │ Playwright full_page     │
    │ OG + Twitter +      │ + page.content() →       │
    │ JSON-LD (@graph)    │   browser_meta           │
    │ → httpx_meta        │ + Pillow нарезка → parts │
    └─────────────────────┴──────────────────────────┘
            │
            ▼
[merge_meta] Объединяем httpx_meta + browser_meta
    title/description — берём ДЛИННЕЕ
    price/brand/rating — браузер приоритетнее
            │
            ▼
Есть parts (скриншот)?
    ├─ ДА, 1 часть   → reply_photo + карточка
    ├─ ДА, >1 часть  → reply_media_group, карточка на первой
    └─ НЕТ           → reply текстом, только карточка
            │
            ▼
Удаляем статусное сообщение
```

---

## 4. Структура файлов

```
project/
├── bot.py          # Хендлер сообщений, merge_meta, build_message, entities
├── cache.py        # TTLCache: MD5(url) → file_id
├── config.py       # BOT_TOKEN, таймауты, константы
├── Dockerfile      # dumb-init + playwright + chromium
├── main.py         # polling + FastAPI (/ping, /) параллельно
├── metadata.py     # httpx fetch, _walk_jsonld, _parse
├── render.yaml     # healthCheckPath /ping
├── requirements.txt
├── screenshot.py   # Playwright, _route_handler, _split_image
└── security.py     # SSRF + DNS rebinding
```

---

## 5. Ключевые решения и ПОЧЕМУ

### 5.1. SEMAPHORE = 1
Только один скриншот за раз. **Почему:** Chromium при активной работе занимает 300–400 МБ. Render Free даёт 512 МБ на всё. Два одновременных скриншота = OOM crash.

### 5.2. Polling, а не Webhook
**Почему:** Render Free усыпляет сервис через 15 минут без входящего HTTP-трафика. Webhook при этом теряется. Polling возобновляется после пробуждения. НО: Render следит только за inbound HTTP, polling-запросы не считаются активностью — поэтому нужен keep-alive пинг на `/ping`.

### 5.3. wait_until="domcontentloaded", НЕ networkidle
**Почему:** сайты с аналитикой, WebSocket, рекламой никогда не достигают networkidle — бот завис бы на каждом втором сайте.

### 5.4. full_page=True + Pillow нарезка, MAX_HEIGHT=4000
**Почему:** делаем один полный скриншот, потом режем готовое изображение через Pillow — нет проблем с координатами clip. MAX_HEIGHT ограничивает высоту: страницы 50000–200000px вызвали бы OOM. 4000px = ~3 части, достаточно для товарной страницы.

### 5.5. Slackbot User-Agent первым
**Почему:** Cloudflare намеренно пропускает ботов соцсетей (Slack, Twitter, Facebook), иначе превью ссылок в этих сервисах сломались бы. Используем это для получения метаданных.

### 5.6. Метаданные из page.content() (браузер) + httpx параллельно
**Почему:** некоторые сайты (Elmir, Rozetka) блокируют httpx но пускают реальный браузер. Берём из обоих источников и объединяем. browser_meta видит результат JS-рендера.

### 5.7. _walk_jsonld с обходом @graph
**Почему:** магазины кладут Product не первым элементом массива, а внутри `@graph` или после BreadcrumbList. Простой `data[0]` не находил товар. Рекурсивный обход находит Product где бы он ни был.

### 5.8. merge_meta: длиннее title побеждает
**Почему:** Elmir через httpx отдаёт title="Elmir.ua", а браузер — полное название товара. Берём длиннее. Для Cloudflare-сайтов наоборот: httpx даёт нормальный title, браузер пустой.

### 5.9. Название в `code` entity
**Почему:** Telegram показывает code-текст в рамке и копирует одним тапом — удобно для поиска товара в Google.

### 5.10. Блокировка медиа, шрифтов, рекламы в _route_handler
**Почему:** ускоряет загрузку, снижает RAM. НЕ блокируем googletagmanager — ломает некоторые сайты.

---

## 6. Параметры (config.py)

```python
BOT_TOKEN = os.environ["BOT_TOKEN"]   # из переменных окружения
PORT = int(os.environ.get("PORT", 8000))  # Render подставляет сам

USER_AGENT = "Mozilla/5.0 ... Chrome/125.0.0.0 ..."  # для скриншота
TIMEOUT_MS = 20_000   # таймаут загрузки страницы
PAUSE_MS = 3_000      # пауза после domcontentloaded (для JS-рендера)
SEMAPHORE = 1         # один скриншот за раз — защита от OOM
CACHE_SIZE = 200      # записей в кэше
CACHE_TTL = 300       # 5 минут
```

## 7. Параметры (screenshot.py)

```python
MOBILE_WIDTH = 390    # iPhone мобильный viewport
MOBILE_HEIGHT = 844
MAX_HEIGHT = 4000     # макс. высота до нарезки (защита от OOM)
PART_HEIGHT = 1280    # высота одной части для Telegram
```

---

## 8. Формат карточки (bot.py → build_message)

```
🌐 site_name
📌 title         ← code entity: рамка + копирование одним тапом
🏷 Бренд: brand
💰 Ціна: price   ← bold entity
⭐ rating

📝 description (обрезается до 300 символов)

━━━━━━━━━━━━━━━
🛡 УВАГА!...     ← blockquote entity
```

Caption фото ограничен 1024 символами (Telegram), текстовое сообщение — 4096. Entities считаются в UTF-16 code units (особенность Telegram API).

---

## 9. SSRF-фильтр (security.py)

Проверяется IP после DNS-резолва (защита от DNS rebinding), не строка URL.

Блокируемые диапазоны:
```
127.0.0.0/8      loopback
10.0.0.0/8       приватная сеть
172.16.0.0/12    приватная сеть
192.168.0.0/16   приватная сеть
169.254.0.0/16   cloud metadata (169.254.169.254 — ключи AWS/GCP)
100.64.0.0/10    shared address space
198.18.0.0/15    benchmark
224.0.0.0/4      multicast
240.0.0.0/4      reserved
fc00::/7, fe80::/10, ::1   IPv6
```

---

## 10. Блокировка ресурсов (screenshot.py → _route_handler)

```
resource_type: media, websocket
расширения: .woff .woff2 .ttf .otf .eot .mp4 .webm .avi .mov
домены: doubleclick.net, googlesyndication.com, googleadservices.com,
        adnxs.com, criteo.com, taboola.com, outbrain.com,
        facebook.net, google-analytics.com, mc.yandex.ru, counter.yadro.ru
```

---

## 11. Деплой (Render Free)

1. Repo на GitHub, runtime Docker
2. Переменная окружения: `BOT_TOKEN`
3. `PORT` — Render подставляет автоматически
4. Keep-alive: UptimeRobot → GET `https://app.onrender.com/ping` каждые 14 минут

---

## 12. Известные проблемы и решения

| Проблема | Причина | Решение |
|---|---|---|
| Cloudflare-сайты медленные/без скриншота | Datacenter IP Render в чёрном списке Cloudflare | Запуск локально (домашний IP) или residential proxy |
| Elmir без цены | Цена рендерится JS после domcontentloaded | Увеличить PAUSE_MS (замедлит всё) |
| TelegramConflictError при деплое | Render запускает 2 контейнера, оба делают polling | Settings → Delete or suspend → Suspend → Resume |
| Скриншот падает timeout на Cloudflare | challenge-страница не дорисовывается | Бот отправляет только карточку — это штатно |

---

## 13. Запуск локально (без Docker)

```bash
pip install -r requirements.txt
playwright install chromium
BOT_TOKEN=ваш_токен python main.py
```

Преимущество: Cloudflare-сайты работают (домашний IP). Минус: только пока компьютер включён.

---

## 14. Как продолжить разработку в новом чате

1. `git add . && git commit -m "stable" && git push` — сохранить состояние
2. В новом чате вставить это ТЗ + вывод команды:
```bash
find . -not -path './.git/*' | sort && echo "---" && for f in $(find . -name "*.py" -o -name "*.txt" -o -name "Dockerfile" | grep -v .git | sort); do echo "== $f =="; cat "$f"; done
```
3. Код на GitHub — источник правды, не память Claude.

---

*Документ отражает рабочее состояние проекта на момент версии 4.1.*
