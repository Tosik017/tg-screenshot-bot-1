import re, time, asyncio
from datetime import datetime, timezone
from aiogram import Router, BaseMiddleware, Bot
from aiogram.types import (
    Message, BufferedInputFile, MessageEntity, InputMediaPhoto,
    TelegramObject, ChatMemberUpdated,
)
from typing import Any, Callable, Awaitable
from cachetools import TTLCache
from loguru import logger
from config import ALLOWED_GROUP_IDS
import cache, security, screenshot, metadata, queue_manager

router = Router()
URL_RE = re.compile(r'https?://[^\s]+')

MAX_MSG_AGE = 60

# --- Rate limiting ---
RATE_LIMIT_SEC = 5
_rate_store: dict[int, float] = {}

class RateLimitMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)

        user_id = event.from_user.id if event.from_user else None
        if user_id is None:
            return await handler(event, data)

        now = time.monotonic()
        last = _rate_store.get(user_id, 0)
        if now - last < RATE_LIMIT_SEC:
            remaining = int(RATE_LIMIT_SEC - (now - last)) + 1
            logger.info(f"RATE_LIMIT user={user_id} cooldown={RATE_LIMIT_SEC - (now - last):.1f}s")
            # message_thread_id — чтобы ответ попал в тот же топик форум-группы
            await event.reply(
                f"⏳ Зачекайте {remaining} сек. перед наступним запитом.",
                message_thread_id=event.message_thread_id,
            )
            return
        _rate_store[user_id] = now
        return await handler(event, data)

router.message.middleware(RateLimitMiddleware())

# --- Привязка к группам + доверие админам ---
# Кэш админов: chat_id → set(user_ids). TTL 5 мин — состав админов меняется редко,
# но один get_chat_administrators раз в 5 мин дешевле, чем API на каждое сообщение.
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
    # Понятие «админ» есть только в группах/супергруппах.
    if msg.chat.type not in ("group", "supergroup"):
        return False
    if not msg.from_user:
        return False
    return msg.from_user.id in await _get_admin_ids(bot, msg.chat.id)

@router.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated, bot: Bot):
    """
    Срабатывает когда меняется членство САМОГО бота (добавили/удалили/повысили).
    Штатный способ ловить добавление в чат — точнее и дешевле проверки на сообщениях.
    Лог chat_id здесь же используется для первичной настройки ALLOWED_GROUP_IDS.
    Топики роли не играют: chat.id один на всю форум-супергруппу.
    """
    chat = event.chat
    status = event.new_chat_member.status  # member / administrator / restricted / left / kicked
    logger.info(f"MY_CHAT_MEMBER chat_id={chat.id} type={chat.type} title={chat.title!r} status={status}")

    # Список не задан → ограничение выключено, ниоткуда не выходим (но лог выше есть).
    if not ALLOWED_GROUP_IDS:
        return

    # В личке выйти нельзя (leave_chat только для групп/каналов) — игнорируем.
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

def _format_warning(position: int, is_duplicate: bool) -> str:
    if is_duplicate:
        return (
            "\n"
            "🚨⚠️ СТОП! НЕ ПЕРЕХОДЬТЕ ЗА ПОСИЛАННЯМ! ⚠️🚨\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🔁 Це посилання вже обробляється — почекайте результат разом з іншими.\n"
            "⏳ Не переходьте, дочекайтесь результату нижче. 👇"
        )
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
    thread_id = msg.message_thread_id  # тот же топик форум-группы

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
            message_thread_id=thread_id,
        )
    elif kind == "media_group":
        media = []
        for i, fid in enumerate(entry["file_ids"]):
            if i == 0:
                media.append(InputMediaPhoto(media=fid, caption=cap_text, caption_entities=cap_entities))
            else:
                media.append(InputMediaPhoto(media=fid))
        await msg.reply_media_group(media=media, message_thread_id=thread_id)
    elif kind == "text":
        await msg.reply(text=msg_text, entities=msg_entities, message_thread_id=thread_id)

@router.message()
async def handle(msg: Message, bot: Bot):
    age = (datetime.now(timezone.utc) - msg.date).total_seconds()
    if age > MAX_MSG_AGE:
        logger.info(f"SKIP stale msg age={age:.0f}s chat={msg.chat.id}")
        return

    # Привязка к группам: если список задан и это другой чат — не обрабатываем.
    # Для групп ещё и выходим (страховка, если my_chat_member не сработал).
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
        return

    # Доверие админам: ссылка от админа группы считается доверенной — пропускаем
    # без проверки и без ответа. Проверяем ТОЛЬКО когда в сообщении есть ссылка,
    # чтобы не дёргать API на каждое сообщение.
    if await _is_trusted_sender(bot, msg):
        uid = msg.from_user.id if msg.from_user else "anon"
        logger.info(f"SKIP trusted sender user={uid} chat={msg.chat.id}")
        return

    url = urls[0]
    thread_id = msg.message_thread_id  # тот же топик форум-группы во всех ответах
    screenshot.log_ram("Start request")

    if not security.is_safe(url):
        await msg.reply("🚫 Посилання веде на недоступний ресурс.", message_thread_id=thread_id)
        return

    # Cache check — все типы включая negative
    entry = cache.get(url)
    if entry:
        kind = entry.get("kind")
        if kind == "failure":
            # Negative cache hit — не дёргаем Playwright
            await msg.reply(
                f"🚫 Сторінка недоступна.\n"
                f"Причина: {entry.get('failure_reason', 'unknown')}\n"
                f"Спробуйте через декілька хвилин.",
                message_thread_id=thread_id,
            )
            return
        # Успешный кэш: photo / media_group / text — отвечаем мгновенно
        await _send_from_cache(msg, url, entry)
        return

    # Cache miss — ставим в очередь
    try:
        future, position, is_duplicate = await queue_manager.enqueue(url)
    except queue_manager.QueueFull:
        await msg.reply(
            "⚠️ Бот зараз перевантажений (черга заповнена).\n"
            "Будь ласка, спробуйте через хвилину.",
            message_thread_id=thread_id,
        )
        return

    status = await msg.reply(_format_warning(position, is_duplicate), message_thread_id=thread_id)
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
                    message_thread_id=thread_id,
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
                sent_list = await msg.reply_media_group(media=media, message_thread_id=thread_id)
                if sent_list:
                    file_ids = [s.photo[-1].file_id for s in sent_list if s.photo]
                    if file_ids:
                        cache.save_media_group(url, file_ids, meta)

            logger.info(f"OK+photo parts={len(parts)} url={url} time={elapsed:.1f}s")
        else:
            # Скриншот не получился — кэшируем как text_only
            await msg.reply(text=msg_text, entities=msg_entities, message_thread_id=thread_id)
            if meta and meta.get("title"):
                cache.save_text_only(url, meta)
            else:
                # Совсем ничего не получили — это failure
                cache.save_failure(url, "empty result")
            logger.info(f"OK+text url={url} time={elapsed:.1f}s")

    except Exception as e:
        logger.error(f"FAIL url={url} error={e}")
        cache.save_failure(url, str(e)[:80])
        await status.edit_text("❌ Не вдалось обробити посилання.")
        return

    await status.delete()
