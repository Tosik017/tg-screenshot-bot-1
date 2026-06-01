# Технічне завдання: tg-screenshot-bot — v5.0
> Актуальний стан: **реалізовано і задеплоєно на Render Free**.  
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
| httpx | 0.27.0 | Async HTTP для метаданих (без браузера) |
| selectolax | 0.3.21 | C-парсер HTML, ~30× швидше BS4 |
| cachetools | 5.3.3 | In-memory TTLCache для file_id |
| loguru | 0.7.2 | Структуроване логування |
| Pillow | 10.3.0 | Нарізка скриншотів на частини |
| psutil | 5.9.8 | Моніторинг RAM |

**Деплой**: Render Free (Docker), 512 МБ RAM, 0.1 CPU. GitHub → auto-deploy.  
**Рестарт**: Suspend/Resume у Settings або `git commit --allow-empty -m "restart" && git push`.

---

## 3. Архітектура (поточна, реалізована)

```
User → Telegram
  ↓
aiogram Router (bot.py)
  ├─ Фільтр бекологу: age > MAX_MSG_AGE (60 сек) → skip
  ├─ URL_RE: витягуємо першу http(s) URL
  ├─ security.is_safe() → блокуємо private IP
  ├─ cache.get(url) → якщо є: відповідаємо одразу (кеш у пам'яті, TTL 300 сек)
  └─ cache miss:
       → reply WARNING_INSTANT (миттєве попередження до генерації)
       → asyncio.gather(
           metadata.fetch(url),    ← httpx, 5 User-Agent fallback
           screenshot.shoot(url)   ← Playwright semaphore=1
         )
       → merge_meta(httpx_meta, browser_meta)
       → reply_photo / reply_media_group / reply text-only
       → cache.save(url, file_id)
       → status.delete()
```

### Файлова структура

```
.
├── bot.py          # Обробка повідомлень, build_message, merge_meta
├── main.py         # Точка входу, Playwright init, polling + FastAPI
├── screenshot.py   # Playwright: shoot(), init(), _split_image()
├── metadata.py     # httpx: fetch(), _parse(), _walk_jsonld()
├── security.py     # SSRF-фільтр: is_safe()
├── cache.py        # TTLCache обгортка: get(), save()
├── config.py       # ENV vars + константи
├── Dockerfile      # playwright base image + dumb-init
├── render.yaml     # Render Blueprint
├── requirements.txt
├── .env.example
├── .gitignore
├── .dockerignore
├── LICENSE         # MIT
└── README.md
```

---

## 4. Ключові рішення та їх причини

### 4.1 SEMAPHORE = 1
Один скриншот одночасно. Два Chromium-контексти = ~600–700 МБ → OOM на 512 МБ Render Free. **Не змінювати без збільшення RAM.**

### 4.2 dumb-init у Docker
Без нього headless_shell Chromium після `browser.close()` залишається зомбі-процесом. dumb-init є PID 1 і коректно реапить дочірні процеси.  
→ [Playwright Issue #34190](https://github.com/microsoft/playwright/issues/34190)

### 4.3 domcontentloaded замість networkidle
networkidle вішає бота на сайтах з аналітикою та live-чатами (бесконечний трафік). domcontentloaded + `wait_for_timeout(PAUSE_MS=3000)` — баланс між швидкістю і JS-рендером.

### 4.4 Паралельний збір метаданих і скриншоту
`asyncio.gather(metadata.fetch, screenshot.shoot)` — httpx і Playwright йдуть одночасно. Без цього час відповіді подвоюється.

### 4.5 merge_meta: довший title перемагає
Browser бачить JS-рендер (SPA), httpx обходить Cloudflare. Беремо довший title/description з двох джерел. Ціна/бренд/рейтинг — звідки є.

### 4.6 _walk_jsonld з @graph
Elmir, Rozetka, Comfy ховають Product не першим об'єктом, а всередині `@graph`. Рекурсивний обхід вирішує.

### 4.7 PART_HEIGHT = 1280, MAX_PARTS = 4 → MAX_HEIGHT = 5120 px
Telegram ліміт ~10 МБ на фото. 1280px @ 390px ширина @ 2x DPR ≈ 2–4 МБ. 4 частини = максимум що влізе без OOM на 512 МБ.

### 4.8 Слабкий httpx fallback на Cloudflare
User-Agent `Slackbot-LinkExpanding...` — Cloudflare пропускає ботів соцмереж. Fallback chain: Slack → Twitter → Facebook → 2× Chrome.

### 4.9 Фільтр бекологу (MAX_MSG_AGE = 60 сек)
Після рестарту бота в Telegram накопичуються сотні старих повідомлень. Фільтр за `msg.date` + `delete_webhook(drop_pending_updates=True)` — захист від масового спаму відповідями.

---

## 5. Поточний стан: що реалізовано

### ✅ Готово і працює

- Мгновенне попередження до генерації скриншоту
- Паралельний збір метаданих (httpx) + скриншоту (Playwright)
- Розумне злиття метаданих з двох джерел (`merge_meta`)
- Нарізка довгих сторінок на частини через Pillow
- Захист від бекологу після рестартів
- SSRF-фільтр (private IP ranges)
- In-memory TTL кеш (5 хв, 200 записів)
- Блокування реклами, медіа, шрифтів у Playwright
- Обхід cookie-банерів
- dumb-init у Docker
- FastAPI `/ping` та `/` для health check на Render
- `.gitignore`, `.dockerignore`, `.env.example`, `LICENSE`, `README.md`
- Структуроване логування (loguru) з RAM-мітками

### ⚠️ Відомі обмеження (прийняті свідомо)

- Кеш живе тільки в пам'яті процесу — після рестарту скидається
- SSRF-фільтр не перевіряє redirect-ланцюжки (DNS rebinding теоретично можливий)
- Нема rate limiting — один користувач може заспамити
- Браузер не перезапускається — після ~100+ запитів можливий повільний memory leak

---

## 6. Черга завдань

### Рівень 1 — Стабільність (наступний крок)

**Пріоритет: КРИТИЧНИЙ**

#### 1.1 Rate limiting per user
**Проблема**: без ліміту один юзер може відправити 50 посилань підряд → черга забивається → Playwright копить пам'ять → OOM → Render вбиває контейнер.  
**Рішення**: middleware aiogram — 1 запит / 15 сек на user_id.  
**Файл**: `bot.py` (middleware) або окремий `rate_limit.py`.  
**Поведінка**: тихе ігнорування надлишкових запитів (без відповіді, щоб не спамити).

#### 1.2 Periodic browser restart
**Проблема**: Playwright поступово накопичує пам'ять через internal page cache, V8 heap, накладні витрати контекстів.  
→ [Playwright Issue #15400](https://github.com/microsoft/playwright/issues/15400)  
**Рішення**: лічильник запитів у `screenshot.py`; кожні 50 скриншотів — `_browser.close()` + `init()`.  
**Важливо**: перезапуск відбувається тільки між запитами (семафор захищає), не під час.  
**Файл**: `screenshot.py`.

---

### Рівень 2 — Безпека

**Пріоритет: СЕРЕДНІЙ**

#### 2.1 SSRF-захист: перевірка після redirect
**Проблема**: `socket.gethostbyname()` і реальний запрос Playwright — різні моменти. При DNS rebinding можна обійти фільтр.  
**Рішення**: httpx HEAD-запит з `follow_redirects=True` → перевірка `final_url` через `is_safe()`.  
**Файл**: `security.py` або `metadata.py` (до основного fetch).

#### 2.2 Healthcheck бота (не тільки FastAPI)
**Проблема**: `/ping` підтверджує що FastAPI живий, але не що бот підключений до Telegram і браузер запущений.  
**Рішення**: `/health` endpoint з перевіркою `_browser is not None`.  
**Файл**: `main.py`.

---

### Рівень 3 — Можливості (майбутнє)

**Пріоритет: НИЗЬКИЙ / За бажанням**

#### 3.1 OCR після скриншоту
Запускати `pytesseract` або `easyocr` на скриншоті, шукати слова: `seed phrase`, `metamask`, `wallet`, `login`, `password`.  
Додавати `⚠️ На сторінці виявлені підозрілі форми` до картки.  
**Обмеження**: важкий пакет (~300 МБ), потребує більше RAM ніж є на Render Free.

#### 3.2 VirusTotal / URLScan інтеграція
Перевіряти URL через публічні API (безкоштовні ліміти).  
`VirusTotal`: 4 запити/хвилину безкоштовно → `X / 92 vendors`.  
`URLScan.io`: публічний scan → додаткові скриншоти з хмари.

#### 3.3 Whitelist / Blacklist доменів
`blocked_domains.txt`: автоматично блокувати *.tk, *.top, *.xyz — відомі фішинг-TLD.  
`trusted_domains.txt`: rozetka.com.ua, amazon.com — скорочена відповідь без попередження.

#### 3.4 Phishing score
Аналізувати ознаки: вік домену (WHOIS), наявність форм логіну, редиректи, Cloudflare challenge.  
Виводити: `🛡 Risk Score: 78/100` з переліком ознак.

#### 3.5 Персистентний кеш
Замінити `TTLCache` в пам'яті на `SQLite` (локально) або `Redis` (Upstash безкоштовний).  
Кеш виживає після рестарту. Популярні посилання не перезнімаються.

---

## 7. Константи (config.py) — довідник

| Константа | Значення | Чому |
|-----------|----------|------|
| `SEMAPHORE` | 1 | Два Chromium = OOM на 512 МБ |
| `TIMEOUT_MS` | 20 000 | 20 сек — баланс швидкості і надійності |
| `PAUSE_MS` | 3 000 | Чекаємо JS-рендер після domcontentloaded |
| `MAX_PARTS` | 4 | Захист від OOM — 4 × 1280 px = 5120 px max |
| `PART_HEIGHT` | 1 280 | Telegram ліміт ~10 МБ на фото |
| `MAX_MSG_AGE` | 60 сек | Фільтр бекологу після рестарту |
| `CACHE_SIZE` | 200 | Кількість URL у кеші |
| `CACHE_TTL` | 300 сек | 5 хвилин — баланс між свіжістю і навантаженням |

---

## 8. Команди проекту

```bash
# Запушити зміни
git add . && git commit -m "опис" && git push

# Форсований рестарт (TelegramConflictError або зависання)
git commit --allow-empty -m "restart" && git push

# Подивитись весь код
find . -not -path './.git/*' | sort && echo "---" && for f in $(find . -name "*.py" -o -name "*.txt" -o -name "Dockerfile" | grep -v .git | sort); do echo "== $f =="; cat "$f"; done

# Зберегти дамп проекту
{ echo '# Дамп'; echo '```'; find . -not -path './.git/*' | sort; echo '```'; for f in $(find . -name "*.py" -o -name "*.txt" -o -name "Dockerfile" | grep -v .git | sort); do echo "## $f"; echo '```python'; cat "$f"; echo '```'; done; } > project_dump.md
```

---

## 9. Деплой

**Поточне середовище**: Render Free  
- 750 годин/місяць (~31 день для 1 сервісу)  
- Sleep після 15 хв idle, cold start ~30–60 сек  
- Shell недоступний — рестарт тільки через Suspend/Resume або порожній коміт  
- Автодеплой: push у `main` → Render підхоплює  

**Шлях до production 24/7**:
1. Oracle Cloud Always Free ARM (4 OCPU, 24 ГБ RAM) — безкоштовно, але ARM вимагає додаткової настройки Playwright
2. Hetzner CX22 (~€6/місяць) — найкращий price/performance для стабільного production

---

## 10. Версії TZ

| Версія | Що змінилось |
|--------|--------------|
| v1–v3 | Початкові концепції, не збереглись |
| v4.1 | Фінальна архітектура: single screenshot, asyncio паралелізм, Cloudflare fallback |
| **v5.0** | Актуалізація під реальний код. Зафіксовано всі прийняті рішення та їх причини. Черга завдань: Рівень 1 (rate limit + browser restart), Рівень 2 (SSRF, healthcheck), Рівень 3 (OCR, VirusTotal, phishing score). |
