# Технічне завдання: tg-screenshot-bot — v6.0
> Актуальний стан: **реалізовано і задеплоєно на Render Free**, помічено тегом `stable-v4`.
> Єдине джерело правди про архітектуру, рішення та чергу завдань.

---

## 1. Суть проекту

Telegram-бот для **безпечного попереднього перегляду посилань** у групах + **модерація спаму**.

Сценарій: учасник надсилає підозріле посилання → бот відповідає **миттєвим попередженням** + через 30–90 сек — **скриншотом сторінки** + **карткою метаданих**. Людина бачить сайт, не переходячи туди.

Бот працює лише в дозволених групах (`ALLOWED_GROUP_IDS`), довіряє адмінам і автоматично гасить спам однаковими посиланнями (ескалація аж до реального mute).

Аудиторія: україномовні Telegram-групи з фішинг-загрозою.

---

## 2. Поточний стек

| Компонент | Версія | Роль |
|-----------|--------|------|
| Python | 3.10 | З Playwright base image (`mcr.microsoft.com/playwright/python`) |
| aiogram | 3.7.0 | Telegram Bot API, async |
| Playwright | 1.44.0 | Headless Chromium, скриншоти |
| playwright-stealth | 1.0.6 | Обхід Cloudflare detection |
| FastAPI / uvicorn | 0.111 / 0.30 | HTTP-сервер для /health |
| httpx | 0.27.0 | Async HTTP для метаданих |
| selectolax | 0.3.21 | C-парсер HTML |
| cachetools | 5.3.3 | TTLCache: кеш + увесь стан анти-спаму |
| loguru | 0.7.2 | Логування |
| Pillow | 10.3.0 | Нарізка скриншотів |
| psutil | 5.9.8 | Моніторинг RAM |

**Деплой**: Render Free (Docker), 512 МБ RAM, 0.1 CPU, ephemeral диск. GitHub → auto-deploy.
**Права бота в групі**: для модерації потрібні **Delete Messages** + **Ban Users**. Без них — лише попередження (видалення/mute тихо пропускаються).
**Рестарт**: Render Dashboard → Manual Deploy, або `git commit --allow-empty -m "restart" && git push`.

---

## 3. Архітектура (поточна)

```
User → Telegram → aiogram Router (bot.py)
  ├─ Фільтр бекологу: age > 60с → skip
  ├─ Прив'язка: chat не в ALLOWED_GROUP_IDS → leave_chat + ігнор
  ├─ URL_RE: немає посилання → ігнор (на балаканину не реагуємо)
  ├─ _is_trusted_sender: адмін/власник/анонім-адмін → пропустити без обробки
  ├─ Анти-спам (не-адмін):
  │    ├─ (chat,user,url) у _dup_seen → ескалація: 🗑+⏳→⚠️→🛑→🚫 mute 5хв
  │    └─ rate-limit 5с (різні посилання) → "Зачекайте" раз на вікно
  ├─ security.is_safe() → private IP блок
  ├─ cache.get(url): photo/media/text → миттєво; failure → відмова без Playwright
  └─ Cache miss → queue_manager.enqueue((chat,thread,url), url):
       ├─ QueueFull → "перевантажений"
       ├─ in-flight dup (інший юзер) → 👀 реакція
       └─ нова задача → WARNING з позицією → await future →
          merge_meta → reply_photo/media_group → cache.save_*

Worker: asyncio.wait_for(screenshot.shoot, 90с) у семафорі; browser restart кожні 50.
Активний mute зберігає САМ Telegram (restrictChatMember + until_date) — у нас 0 пам'яті.
```

### Файлова структура
```
bot.py            # handle, анти-спам ескалація+mute, модерація, реакції, довіра адмінам, прив'язка
main.py           # /health (queue+cache), SIGTERM, queue init
screenshot.py     # shoot() — bounded viewport capture (без full_page), restart кожні 50
metadata.py       # httpx + 5 UA fallback, JSON-LD @graph
security.py       # is_safe() — private IP (синхронно)
cache.py          # типізовані записи + диф. TTL + negative
queue_manager.py  # enqueue(key,url), дедуп по (chat,thread,url), _worker, QueueFull
config.py         # ENV (BOT_TOKEN, ALLOWED_GROUP_IDS, PORT) + константи
Dockerfile · render.yaml · requirements.txt · .env.example · LICENSE · README.md
```

---

## 4. Ключові рішення та причини

### 4.1 SEMAPHORE = 1
Два Chromium = ~600–700 МБ → OOM на 512 МБ. **Не змінювати без збільшення RAM.**

### 4.2 dumb-init у Docker
Без нього headless_shell після close() — зомбі. → Playwright Issue #34190.

### 4.3 domcontentloaded + PAUSE_MS=3000
networkidle вішає бота на сайтах з аналітикою.

### 4.4 Паралельний збір метаданих і скриншоту
`asyncio.create_task(metadata.fetch)` + `await future`.

### 4.5 merge_meta: довший title/description перемагає
Browser бачить JS-рендер, httpx обходить Cloudflare.

### 4.6 _walk_jsonld з @graph
Elmir/Rozetka/Comfy ховають Product у `@graph`.

### 4.7 Bounded viewport capture (КЛЮЧОВЕ — fix OOM)
Раніше `page.screenshot(full_page=True)` рендерив **усю** сторінку в один битмап ДО повернення байтів; на лістингах (hotline.ua, десятки тис. px) це OOM на 512 МБ, а обрізка в Pillow спрацьовувала запізно.
Тепер: меряємо `document.scrollHeight`, обмежуємо висоту захвату `MAX_CAPTURE_HEIGHT` (= `MAX_HEIGHT // DEVICE_SCALE` = 2560 CSS px), ставимо viewport на цю висоту і знімаємо **звичайний** (не full_page) скриншот. Битмап завжди ≤ 780×5120 px — OOM неможливий. `DEVICE_SCALE=2`, viewport 390×844.

### 4.8 Slackbot UA для httpx
Cloudflare пропускає ботів соцмереж. Chain: Slack → Twitter → Facebook → 2× Chrome.

### 4.9 Фільтр бекологу MAX_MSG_AGE=60
`delete_webhook(drop_pending_updates=True)` + фільтр за `msg.date`.

### 4.10 Rate limiting у handle (не middleware)
Перенесено з blanket-middleware у `handle` ПІСЛЯ фільтрів: спрацьовує лише на реальний запит зі **посиланням** від **не-адміна**, не на балаканину і не на адмінів. Повідомлення про кулдаун — раз на вікно.

### 4.11 Browser restart кожні 50 запитів
Playwright накопичує V8 heap. Перезапуск у семафорі. → Playwright Issue #15400.

### 4.12 Smart queue + дедуп по (chat,thread,url)
MAX_QUEUE_SIZE=10, TASK_TIMEOUT_SEC=90, один воркер (= SEMAPHORE=1). Ключ дедупу — `(chat_id, thread_id, url)`: той самий URL в іншому чаті/топіку доставляється окремо; той самий у цьому ж — один раз. In-flight dup (інший юзер) → реакція 👀.

### 4.13 Smart cache (cache.py)
Типізовані записи (photo/media_group/text/failure), диф. TTL за вмістом, negative cache 3хв, cache hit повністю минає httpx.

### 4.14 Graceful shutdown (SIGTERM)
stop polling → wait semaphore (60с) → close browser → stop uvicorn.

### 4.15 /health endpoint
`_browser is not None` + `bot.get_me()` + queue/cache stats. HTTP 503 при деградації.

### 4.16 Прив'язка до груп + авто-leave
`ALLOWED_GROUP_IDS` (кілька через кому/пробіл; сумісність зі старою `ALLOWED_GROUP_ID`; порожньо = вимкнено — щоб забутий env не вигнав з цільових груп). `on_my_chat_member` гасить бота з чужих чатів. Топіки не враховуються: chat.id один на всю форум-супергрупу.

### 4.17 Довіра адмінам
`_is_trusted_sender`: власник/адмін/анонім-адмін (sender_chat==chat) — повний пропуск (ні перевірки, ні лімітів, ні модерації). Кеш складу адмінів — TTLCache 5 хв; помилка API → нікого не вважаємо адміном (безпечний дефолт).

### 4.18 Анти-спам дублікатів + реальна модерація
Повтор того ж URL одним юзером у вікні → ескалація per `(chat,user)`: 🗑 видалення дубля + `strike 1 ⏳ → 2 ⚠️ → 3 🛑 → 4+ 🚫`. На 4-му — **реальний `restrictChatMember(until_date=now+BAN_SEC)`** (5 хв, знімає Telegram сам). Одне ескалююче повідомлення редагується на місці (не плодиться). In-flight dup → 👀. Адмін може зняти mute вручну будь-коли.

### 4.19 Дисципліна пам'яті (без БД)
На Render Free диск ephemeral — файлова БД безглузда. Активний бан зберігає **Telegram** (until_date), нам — 0 байт. Strikes/дедуп — короткоживуче на **bounded TTLCache** (`maxsize` обмежує кількість, `ttl` викидає старе → рости до гігабайтів фізично не може). Потолок усіх кешів — одиниці МБ при насиченні, реально — десятки КБ. Скидання при рестарті безпечне.

### 4.20 Форум-топіки
`msg.reply*` у aiogram 3.7 САМІ проставляють `message_thread_id` з вихідного — вручну НЕ передаємо (інакше дубль kwarg → TypeError). `bot.send_message` тред сам НЕ ставить → йому передаємо явно.

---

## 5. Поточний стан: що реалізовано

### ✅ Інфраструктура
Rate limiting (у handle), browser restart, /health, graceful shutdown, фільтр бекологу.

### ✅ Smart queue / cache
Ліміт 10, таймаут 90с, дедуп по (chat,thread,url), позиція; типізований кеш + диф. TTL + negative.

### ✅ Базова обробка
Миттєве попередження, паралельний збір, merge_meta, **bounded viewport capture** (fix OOM), SSRF private IP, блок реклами/медіа/шрифтів, обхід cookie, dumb-init.

### ✅ Модерація і доступ
ALLOWED_GROUP_IDS + авто-leave, довіра адмінам, анти-спам ескалація + реальний mute, реакції 👀, форум-топіки.

### ⚠️ Свідомі обмеження
Кеш/strikes у пам'яті (скидаються при рестарті — безпечно); SSRF без перевірки після редиректів; без blacklist/WHOIS (чекають повернення); модерація потребує прав бота Delete+Ban.

---

## 6. Черга завдань

### Готово до поетапного повернення (досі НЕ внедрено)
1. **SSRF після редиректів** — низький ризик, мале async-додавання
2. **WHOIS вік домену** — середній ризик, зовнішній запит
3. **Blacklist доменів** (Phishing.Army + OpenPhish + TLD-фільтр) — середній ризик, ~145k у RAM

### Опціонально (потребує переїзду з Render Free)
OCR на скриншоті · Persistent кеш (Upstash Redis/VPS) · Sentry/Prometheus · Uptime Kuma · растучий бан (5→10→20 хв).

### Свідомо пропущено
VirusTotal / Google Safe Browsing (реєстрація API) · Oracle Cloud Always Free (idle reclaim + ризик термінації, див. §9) · persistent на Render Free (ephemeral диск).

---

## 7. Константи

### config.py
| Константа | Значення | Чому |
|---|---|---|
| `ALLOWED_GROUP_IDS` | frozenset з env | Дозволені групи (кілька); порожньо = без обмеження |
| `SEMAPHORE` | 1 | Два Chromium = OOM |
| `TIMEOUT_MS` | 20 000 | Navigation timeout |
| `PAUSE_MS` | 3 000 | JS-рендер після domcontentloaded |
| `CACHE_SIZE` / `CACHE_TTL` | 200 / 300 | База TTLCache (фактичний TTL — у cache.py) |

### bot.py
| Константа | Значення | Чому |
|---|---|---|
| `MAX_MSG_AGE` | 60 | Фільтр бекологу |
| `RATE_LIMIT_SEC` | 5 | Між запитами різних посилань |
| `DUP_WINDOW_SEC` | 120 | Вікно, де повтори одного URL = спам |
| `STRIKE_DECAY_SEC` | 120 | Тиша → лічильник попереджень обнуляється |
| `BAN_SEC` | 300 | mute 5 хв (знімає Telegram) |

### screenshot.py
| Константа | Значення | Чому |
|---|---|---|
| `DEVICE_SCALE` | 2 | Ретина; фіз.px = лог.×2 |
| `MOBILE_WIDTH/HEIGHT` | 390 / 844 | Мобільний viewport |
| `PART_HEIGHT` | 1 280 | Telegram ліміт ~10 МБ |
| `MAX_PARTS` | 4 | Захист від OOM |
| `MAX_HEIGHT` | 5 120 | PART_HEIGHT×MAX_PARTS (фіз.) |
| `MAX_CAPTURE_HEIGHT` | 2 560 | MAX_HEIGHT//DEVICE_SCALE (CSS px) — межа захвату |
| `RESTART_EVERY` | 50 | Скриншотів між рестартами браузера |

### queue_manager.py
| `MAX_QUEUE_SIZE` | 10 | · | `TASK_TIMEOUT_SEC` | 90 |

### cache.py (TTL)
photo/media_group 3600 · text_only 300 (Cloudflare) · has_price 900 · failure 180.

---

## 8. Команди проекту

```bash
# Запушити
git add . && git commit -m "опис" && git push

# Форсований рестарт
git commit --allow-empty -m "restart" && git push

# Дамп проекту у файл
{ echo '# Дамп'; echo '```'; find . -not -path './.git/*' | sort; echo '```'; for f in $(find . -name "*.py" -o -name "*.txt" -o -name "Dockerfile" | grep -v .git | sort); do echo "## $f"; echo '```python'; cat "$f"; echo '```'; done; } > project_dump.md

# Повернутись до стабільної версії
git checkout stable-v4 -- .
```

---

## 9. Деплой

### Render Free
750 год/міс · sleep після 15 хв idle (cold start 30–60с) · Health Check Path `/health` · ephemeral диск.
Env: `BOT_TOKEN`, `ALLOWED_GROUP_IDS` (через кому). Бот у групі — адмін з **Delete Messages + Ban Users**.
**TelegramConflictError** → Manual Deploy → Deploy latest commit.

### Production 24/7
Hetzner CX22 (€4.51, 4 ГБ) ✅ · Contabo VPS S (€4.50, 8 ГБ) ✅ · Fly.io ($0–5) ⚠️ · Raspberry Pi 4 ✅.

### ❌ Oracle Cloud Always Free — НЕ підходить
Idle reclaim (CPU<20% за 7 днів → відкликання) · масові термінації акаунтів без пояснень · 30 днів неактивності = abandoned · дефіцит ARM Ampere A1 · обов'язкова карта (hold може не пройти) · без SLA · ARM ≠ x86 Playwright base. Пастка з ризиком втрати акаунту.

---

## 10. Теги Git

| Тег | Зміст |
|---|---|
| `stable-v1` | rate limit (тихий) + browser restart |
| `stable-v2` | + /health + graceful + smart queue + smart cache |
| `stable-v3` | stable-v2 + актуальна документація (без Oracle) |
| **`stable-v4`** | + bounded capture (fix OOM) + ALLOWED_GROUP_IDS + авто-leave + довіра адмінам + анти-спам ескалація з реальним mute + дедуп (chat,thread,url) + реакції + форум-топіки |

---

## 11. Версії TZ

| Версія | Що змінилось |
|---|---|
| v4.1–v5.2 | Реальний код, smart queue/cache, без Oracle |
| **v6.0** | Fix OOM (bounded viewport capture замість full_page). Прив'язка до груп `ALLOWED_GROUP_IDS` + авто-leave. Довіра адмінам. Анти-спам ескалація дублікатів + реальний `restrictChatMember` (бан зберігає Telegram). Дедуп черги по (chat,thread,url). Реакції 👀. Форум-топіки. Rate-limit перенесено в handle. Пам'ять: усе на bounded TTLCache, без БД. Виправлено Python 3.10. Тег `stable-v4`. |
