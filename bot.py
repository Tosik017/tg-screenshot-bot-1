import re, time, asyncio
from datetime import datetime, timezone
from aiogram import Router, Bot
from aiogram.types import (
    Message, BufferedInputFile, MessageEntity, InputMediaPhoto,
    ChatMemberUpdated, ReactionTypeEmoji,
)
from cachetools import TTLCache
from loguru import logger
from config import ALLOWED_GROUP_IDS
import cache, security, screenshot, metadata, queue_manager

# aiogram 3.7: msg.reply* сами проставляют message_thread_id из исходного
# сообщения — вручную НЕ передаём (иначе дубликат kwarg → TypeError).

router = Router()
URL_RE = re.compile(r'https?://[^\s]+')

MAX_MSG_AGE = 60

# --- Rate limiting (только на реальные запросы со ссылкой; не на болтовню/админов) ---
RATE_LIMIT_SEC = 5
# Время последнего РАЗРЕШЁННОГО запроса на user_id. TTLCache сам чистит записи по
# истечении окна — без ручной чистки и без утечки памяти.
_rate_store: TTLCache = TTLCache(maxsize=10_000, ttl=RATE_LIMIT_SEC)
# Кому уже показали уведомление о кулдауне в этом окне — чтобы не спамить чат.
_rate_notified: TTLCache = TTLCache(maxsize=10_000, ttl=RATE_LIMIT_SEC)

def _rate_cooldown(user_id: int) -> int:
    """Остаток кулдауна в секундах (0 = можно). При 0 — фиксирует текущий запрос."""
    last = _rate_store.get(user_id)
    if last is not None:
        remaining = RATE_LIMIT_SEC - (time.monotonic() - last)
        if remaining > 0:
            return int(remaining) + 1
    _rate_store[user_id] = time.monotonic()
    return 0

# --- Реакция на сообщение (для дубликатов) ---
# Лёгкое подтверждение эмодзи вместо текста/повторного скриншота: ноль засорения
# чата. 👀 — из дефолтного набора реакций, работает в обычных группах без прав
# админа. Если реакции в чате запрещены — молча пропускаем (анти-спам важнее).
async def _react(bot: Bot, msg: Message, emoji: str):
    try:
        await bot.set_message_reaction(
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )
    except Exception as e:
        logger.info(f"react skipped chat={msg.chat.id} emoji={emoji}: {e}")

# --- Привязка к группам + доверие админам ---
# Кэш админов: chat_id → set(user_ids). TTL 5 мин — состав админов меняется редко.
_admin_cache: TTLCache = TTLCache(maxsize=64, ttl=300)

async def _get_admin_ids(bot: Bot, chat_id: int) -> set[int]:
    cached = _admin_cache.get(chat_id)
    if cached is not None:
        return cached
    try:
        admins = await bot.get_chat_administrators(chat_id)
        ids = {a.user.id for a in admins if a.user}
        _admin_cache[chat_id] = ids  # кешируем ТОЛЬКО успех
        return ids
    except Exception as e:
        # На ошибке не считаем никого админом и НЕ кешируем: ссылку проверим
        # (безопасный дефолт), а на следующем сообщении повторим запрос.
        logger.warning(f"get_chat_administrators failed chat={chat_id}: {e}")
        return set()

async def _is_trusted_sender(bot: Bot, msg: Message) -> bool:
    """True → отправитель доверенный (админ), ссылку не проверяем."""
    # Анонимный админ постит от имени самой группы (sender_chat == chat).
    if msg.sender_chat and msg.sender_chat.id == msg.chat.id:
        return True
    if msg.chat.type not in ("group", "supergroup"):
        return False
    if not msg.from_user:
        return False
    return msg.from_user.id in await _get_admin_ids(bot, msg.chat.id)

@router.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated, bot: Bot):
    """
    Срабатывает когда меняется членство САМОГО бота (добавили/удалили/повысили).
    Лог chat_id используется для первичной настройки ALLOWED_GROUP_IDS.
    Топики роли не играют: chat.id один на всю форум-супергруппу.
    """
    chat = event.chat
    status = event.new_chat_member.status  # member / administrator / restricted / left / kicked
    logger.info(f"MY_CHAT_MEMBER chat_id={chat.id} type={chat.type} title={chat.title!r} status={status}")

    if not ALLOWED_GROUP_IDS:
        return
    if chat.type == "private":
        return

    present = status in ("member", "administrator", "restricted")
    if present and chat.id not in ALLOWED_GROUP_IDS:
        logger.warning(f"LEAVE non-allowed chat_id={chat.id} (allowed={sorted(ALLOWED_GROUP_IDS)})")
        try:
            await bot.leave_chat(chat.id)
        except Exception as e:
            logger.error(f"leave_chat failed chat={chat.id}: {e}")

# --- Сообщения ---
WARNING_INSTANT = (
    "\n"
    "🚨⚠️ СТОП! НЕ ПЕРЕХОДЬТЕ ЗА ПОСИЛАННЯМ! ⚠️🚨\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🛡 Готую безпечний перегляд сторінки.\n"
    "⏳ Зазвичай до 1–2 хвилин — не переходьте, дочекайтесь результату нижче. 👇"
)

DISCLAIMER = (
    "🚨 УВАГА! Не довіряйте незнайомим посиланням.\n"
    "⚠️ Ніколи не вводьте паролі та дані картки на невідомих сайтах.\n"
    "🔎 Безпечніше знайти цей товар через пошук Google."
)

def build_message(meta: dict) -> tuple[str, list[MessageEntity]]:
    text = ""
    entities = []

    if meta.get("site_name"):
        text += f"🌐 {meta['site_name']}\n"

    if meta.get("title"):
        title = meta["title"]
        text += "📌 "
        start = len(text.encode("utf-16-le")) // 2
        text += title + "\n"
        end = len(text.encode("utf-16-le")) // 2 - 1
        entities.append(MessageEntity(type="code", offset=start, length=end - start))

    if meta.get("brand"):
        text += f"🏷 Бренд: {meta['brand']}\n"

    if meta.get("price"):
        price_str = f"💰 Ціна: {meta['price']}"
        start = len(text.encode("utf-16-le")) // 2
        text += price_str + "\n"
        end = len(text.encode("utf-16-le")) // 2 - 1
        entities.append(MessageEntity(type="bold", offset=start, length=end - start))

    if meta.get("rating"):
        text += f"{meta['rating']}\n"

    if meta.get("description"):
        desc = meta["description"].strip()
        if len(desc) > 300:
            desc = desc[:300].rsplit(" ", 1)[0] + "…"
        text += f"\n📝 {desc}\n"

    text += "\n"
    start = len(text.encode("utf-16-le")) // 2
    text += DISCLAIMER
    end = len(text.encode("utf-16-le")) // 2
    entities.append(MessageEntity(type="blockquote", offset=start, length=end - start))

    return text, entities

def build_disclaimer_only() -> tuple[str, list[MessageEntity]]:
    text = "ℹ️ Не вдалось отримати дані про сторінку.\n\n" + DISCLAIMER
    start = len("ℹ️ Не вдалось отримати дані про сторінку.\n\n".encode("utf-16-le")) // 2
    end = len(text.encode("utf-16-le")) // 2
    entities = [MessageEntity(type="blockquote", offset=start, length=end - start)]
    return text, entities

def trim_caption(text: str, entities: list) -> tuple[str, list]:
    if len(text) <= 1024:
        return text, entities
    text = text[:1021] + "…"
    limit = len(text.encode("utf-16-le")) // 2
    entities = [e for e in entities if e.offset + e.length <= limit]
    return text, entities

def merge_meta(httpx_meta: dict, browser_meta: dict) -> dict:
    result = {}
    h_title = httpx_meta.get("title") or ""
    b_title = browser_meta.get("title") or ""
    result["title"] = b_title if len(b_title) > len(h_title) else h_title
    h_desc = httpx_meta.get("description") or ""
    b_desc = browser_meta.get("description") or ""
    result["description"] = b_desc if len(b_desc) > len(h_desc) else h_desc
    for key in ("price", "brand", "rating"):
        result[key] = browser_meta.get(key) or httpx_meta.get(key)
    result["site_name"] = httpx_meta.get("site_name") or browser_meta.get("site_name")
    result["image"] = httpx_meta.get("image") or browser_meta.get("image")
    return result

def _format_warning(position: int) -> str:
    if position <= 1:
        return WARNING_INSTANT
    eta = position * 60
    return (
        "\n"
        "🚨⚠️ СТОП! НЕ ПЕРЕХОДЬТЕ ЗА ПОСИЛАННЯМ! ⚠️🚨\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🛡 Готую безпечний перегляд сторінки.\n"
        f"📊 Ваша позиція в черзі: {position}. Орієнтовний час: ~{eta} сек.\n"
        "⏳ Не переходьте, дочекайтесь результату нижче. 👇"
    )

async def _send_from_cache(msg: Message, url: str, entry: dict):
    """Отправляет ответ из кэша по типу записи. Метаданные уже в entry — никаких httpx."""
    kind = entry.get("kind")
    meta = entry.get("meta") or {}

    if meta and meta.get("title"):
        msg_text, msg_entities = build_message(meta)
    else:
        msg_text, msg_entities = build_disclaimer_only()
    cap_text, cap_entities = trim_caption(msg_text, msg_entities)

    if kind == "photo":
        await msg.reply_photo(
            photo=entry["file_id"],
            caption=cap_text,
            caption_entities=cap_entities,
        )
    elif kind == "media_group":
        media = []
        for i, fid in enumerate(entry["file_ids"]):
            if i == 0:
                media.append(InputMediaPhoto(media=fid, caption=cap_text, caption_entities=cap_entities))
            else:
                media.append(InputMediaPhoto(media=fid))
        await msg.reply_media_group(media=media)
    elif kind == "text":
        await msg.reply(text=msg_text, entities=msg_entities)

@router.message()
async def handle(msg: Message, bot: Bot):
    age = (datetime.now(timezone.utc) - msg.date).total_seconds()
    if age > MAX_MSG_AGE:
        logger.info(f"SKIP stale msg age={age:.0f}s chat={msg.chat.id}")
        return

    # Привязка к группам: чужой чат → не обрабатываем (для групп ещё и выходим).
    if ALLOWED_GROUP_IDS and msg.chat.id not in ALLOWED_GROUP_IDS:
        if msg.chat.type in ("group", "supergroup", "channel"):
            logger.warning(f"Message in non-allowed chat {msg.chat.id} — leaving")
            try:
                await bot.leave_chat(msg.chat.id)
            except Exception as e:
                logger.warning(f"leave_chat failed: {e}")
        return

    text = msg.text or msg.caption or ""
    urls = URL_RE.findall(text)
    if not urls:
        return  # нет ссылки → ни обработки, ни rate limit (не реагируем на болтовню)

    # Доверие админам — ДО rate limit: на админов лимит не распространяется.
    if await _is_trusted_sender(bot, msg):
        uid = msg.from_user.id if msg.from_user else "anon"
        logger.info(f"SKIP trusted sender user={uid} chat={msg.chat.id}")
        return

    # Rate limit — ТОЛЬКО на реальный запрос (ссылка, не-админ).
    # Сообщение о кулдауне шлём один раз за окно, чтобы не спамить чат.
    user_id = msg.from_user.id if msg.from_user else None
    if user_id is not None:
        cooldown = _rate_cooldown(user_id)
        if cooldown:
            logger.info(f"RATE_LIMIT user={user_id} cooldown={cooldown}s")
            if user_id not in _rate_notified:
                _rate_notified[user_id] = True
                await msg.reply(f"⏳ Зачекайте {cooldown} сек. перед наступним запитом.")
            return

    url = urls[0]
    screenshot.log_ram("Start request")

    if not security.is_safe(url):
        await msg.reply("🚫 Посилання веде на недоступний ресурс.")
        return

    # Cache check — все типы включая negative
    entry = cache.get(url)
    if entry:
        kind = entry.get("kind")
        if kind == "failure":
            await msg.reply(
                f"🚫 Сторінка недоступна.\n"
                f"Причина: {entry.get('failure_reason', 'unknown')}\n"
                f"Спробуйте через декілька хвилин."
            )
            return
        # Кэш-хит (успех): шлём превью повторно — новому спрашивающему нужно его увидеть.
        await _send_from_cache(msg, url, entry)
        return

    # Cache miss — в очередь. Ключ дедупа учитывает чат и топик.
    dest_key = (msg.chat.id, msg.message_thread_id, url)
    try:
        future, position, is_duplicate = await queue_manager.enqueue(dest_key, url)
    except queue_manager.QueueFull:
        await msg.reply(
            "⚠️ Бот зараз перевантажений (черга заповнена).\n"
            "Будь ласка, спробуйте через хвилину."
        )
        return

    if is_duplicate:
        # Этот URL уже обрабатывается для ЭТОГО чата/топика. Оригинальный запрос сам
        # пришлёт скриншот сюда. Дубль НЕ дублируем (иначе N копий → Flood control),
        # а реагируем 👀 на сообщение: «побачив, результат буде нижче».
        logger.info(f"DUPLICATE inflight url={url} chat={msg.chat.id} thread={msg.message_thread_id} — react 👀")
        await _react(bot, msg, "👀")
        return

    status = await msg.reply(_format_warning(position))
    start = time.monotonic()

    httpx_task = asyncio.create_task(metadata.fetch(url))

    try:
        parts, browser_meta = await future
    except Exception as e:
        logger.error(f"FAIL url={url} error={e}")
        cache.save_failure(url, str(e)[:80])
        await status.edit_text("❌ Не вдалось обробити посилання.")
        return

    httpx_meta = await httpx_task

    meta = merge_meta(httpx_meta, browser_meta)
    logger.info(f"Final meta: title={meta.get('title')} price={meta.get('price')}")

    elapsed = time.monotonic() - start

    if meta and meta.get("title"):
        msg_text, msg_entities = build_message(meta)
    else:
        msg_text, msg_entities = build_disclaimer_only()

    try:
        if parts:
            cap_text, cap_entities = trim_caption(msg_text, msg_entities)

            if len(parts) == 1:
                sent = await msg.reply_photo(
                    photo=BufferedInputFile(parts[0], filename="preview.png"),
                    caption=cap_text,
                    caption_entities=cap_entities,
                )
                if sent.photo:
                    cache.save_photo(url, sent.photo[-1].file_id, meta)
            else:
                media = []
                for i, part in enumerate(parts):
                    if i == 0:
                        media.append(InputMediaPhoto(
                            media=BufferedInputFile(part, filename=f"part_{i+1}.png"),
                            caption=cap_text,
                            caption_entities=cap_entities,
                        ))
                    else:
                        media.append(InputMediaPhoto(
                            media=BufferedInputFile(part, filename=f"part_{i+1}.png"),
                        ))
                sent_list = await msg.reply_media_group(media=media)
                if sent_list:
                    file_ids = [s.photo[-1].file_id for s in sent_list if s.photo]
                    if file_ids:
                        cache.save_media_group(url, file_ids, meta)

            logger.info(f"OK+photo parts={len(parts)} url={url} time={elapsed:.1f}s")
        else:
            await msg.reply(text=msg_text, entities=msg_entities)
            if meta and meta.get("title"):
                cache.save_text_only(url, meta)
            else:
                cache.save_failure(url, "empty result")
            logger.info(f"OK+text url={url} time={elapsed:.1f}s")

    except Exception as e:
        logger.error(f"FAIL url={url} error={e}")
        cache.save_failure(url, str(e)[:80])
        await status.edit_text("❌ Не вдалось обробити посилання.")
        return

    await status.delete()
