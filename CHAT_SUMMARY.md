# Подытог чата — tg-screenshot-bot (→ stable-v4)

## Текущее состояние
Тег **`stable-v4`**. Прод на Render Free, RAM ~175–200 МБ. Зависимостей не добавляли (всё на cachetools, который уже был).

---

## Что сделано в этом чате (по порядку)

1. **Fix OOM (screenshot.py).** Убран `full_page=True` — он рендерил всю страницу в битмап ДО возврата байтов, на длинных листингах (hotline.ua) → OOM на 512 МБ. Теперь меряем `document.scrollHeight`, ограничиваем `MAX_CAPTURE_HEIGHT` (=`MAX_HEIGHT//DEVICE_SCALE`=2560 CSS px), ставим viewport на эту высоту и снимаем обычный (не full_page) скриншот. Битмап всегда ≤ 780×5120 px. Новые константы `DEVICE_SCALE=2`, `MAX_CAPTURE_HEIGHT`.

2. **Привязка к группам + авто-leave (config.py, bot.py, .env.example).** `ALLOWED_GROUP_IDS` (несколько через запятую/пробел; обратная совместимость с одиночной `ALLOWED_GROUP_ID`; пусто = ограничение выключено). Хендлер `on_my_chat_member` гасит бота из чужих чатов. Топики не важны (один chat.id на всю форум-супергруппу).

3. **Доверие админам.** `_is_trusted_sender`: владелец/админ/анонимный админ — полный пропуск (ни проверок, ни лимитов, ни модерации). Кэш админов TTLCache 5 мин, при ошибке API — никого не считаем админом.

4. **message_thread_id (форум-топики).** Сначала ошибочно передавали вручную → `TypeError` (aiogram 3.7 reply* проставляют тред сами) → откатили. `bot.send_message` тред не ставит сам → ему передаём явно (нужно для модераторских уведомлений).

5. **Rate-limit перенесён из middleware в handle.** Только на запросы со ссылкой от не-админов, не на болтовню. Уведомление о кулдауне — раз за окно. `RateLimitMiddleware` удалён.

6. **Дедуп очереди по (chat, thread, url)** (queue_manager.py): `enqueue(key, url)`. Тот же URL в другом чате/топике — отдельная доставка; in-flight dup от другого юзера → реакция 👀 (`setMessageReaction`).

7. **Анти-спам дубликатов + реальная модерация (bot.py).** Повтор одной ссылки одним юзером → эскалация per `(chat,user)`: 🗑 удаление дубля + `strike 1 ⏳ → 2 ⚠️ → 3 🛑 → 4+ 🚫 реальный mute` через `restrictChatMember(until_date=now+BAN_SEC=300с)`. Mute хранит Telegram (until_date), снимает сам; админ может снять вручную. Одно эскалирующее уведомление редактируется на месте. Весь стол анти-спама — bounded TTLCache (`_dup_seen`, `_strikes`, `_warn_msg`), без БД/диска. Убран фиктивный «soft-ban» (он не блокировал — сообщения проходили).

---

## Теги git
| Тег | Состояние |
|---|---|
| `stable-v3` | инфраструктура + актуальная документация |
| **`stable-v4`** | + fix OOM (bounded capture) + ALLOWED_GROUP_IDS + auto-leave + доверие админам + анти-спам эскалация с реальным mute + дедуп (chat,thread,url) + реакции + форум-топики |

---

## Текущая структура файлов
```
bot.py            — handle, анти-спам ескалація+mute, модерація, реакції, довіра адмінам, прив'язка
main.py           — /health, SIGTERM, queue init
screenshot.py     — bounded viewport capture (без full_page), restart кожні 50
metadata.py       — httpx + 5 UA, JSON-LD @graph
security.py       — is_safe() private IP
cache.py          — типізовані записи + диф. TTL + negative
queue_manager.py  — enqueue((chat,thread,url),url), _worker, QueueFull
config.py         — BOT_TOKEN + ALLOWED_GROUP_IDS + константи
Dockerfile · render.yaml · requirements.txt · .env.example · LICENSE · README.md · TZ.md
```

---

## Что осталось из плана (НЕ внедрено)
### Антифишинг (поэтапный возврат из stable-v1)
1. SSRF после редиректов — низкий риск
2. WHOIS возраст домена — средний риск
3. Blacklist (Phishing.Army + OpenPhish + TLD) — средний риск, ~145k в RAM

### Опционально (при переезде с Render Free)
OCR · persistent кэш · Sentry/Prometheus · Uptime Kuma · растущий бан (5→10→20 мин).

### Сознательно пропущено (не предлагать)
VirusTotal · Google Safe Browsing · Oracle Cloud Free · persistent на Render Free (ephemeral диск).

---

## Известные наблюдения
- RAM стабильна ~175–200 МБ; bounded capture устранил OOM.
- mute требует прав бота Delete Messages + Ban Users (иначе в логах `delete skipped`/`mute failed`).
- Cloudflare (hotline/rozetka) блокирует Playwright → метаданные через httpx работают.
- TelegramConflictError при деплое → Manual Deploy.

---

## Что передать в новый чат
1. HOW_TO_HELP_ME.md (без изменений)
2. TZ.md (v6.0 — уже в репо)
3. project_dump.md (свежий дамп)
4. CHAT_SUMMARY.md (этот файл)
