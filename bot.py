import re, time, asyncio
from datetime import datetime, timezone
from aiogram import Router, BaseMiddleware
from aiogram.types import Message, BufferedInputFile, MessageEntity, InputMediaPhoto, TelegramObject
from typing import Any, Callable, Awaitable
from loguru import logger
import cache, security, screenshot, metadata, blacklist, domain_age

router = Router()
URL_RE = re.compile(r'https?://[^\s]+')

MAX_MSG_AGE = 60

# --- Rate limiting ---
# Один запрос на 5 сек с user_id. Тихий дроп — без ответа, чтобы не спамить в чат.
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
            logger.info(f"RATE_LIMIT user={user_id} cooldown={RATE_LIMIT_SEC - (now - last):.1f}s")
            return
        _rate_store[user_id] = now
        return await handler(event, data)

router.message.middleware(RateLimitMiddleware())

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

def build_message(meta: dict, age_warning: str | None = None) -> tuple[str, list[MessageEntity]]:
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

    # Предупреждение о молодом домене — перед DISCLAIMER, отдельной строкой
    if age_warning:
        text += f"\n{age_warning}\n"

    text += "\n"
    start = len(text.encode("utf-16-le")) // 2
    text += DISCLAIMER
    end = len(text.encode("utf-16-le")) // 2
    entities.append(MessageEntity(type="blockquote", offset=start, length=end - start))

    return text, entities

def build_disclaimer_only(age_warning: str | None = None) -> tuple[str, list[MessageEntity]]:
    warning_line = f"{age_warning}\n\n" if age_warning else ""
    text = f"ℹ️ Не вдалось отримати дані про сторінку.\n\n{warning_line}" + DISCLAIMER
    start = len(f"ℹ️ Не вдалось отримати дані про сторінку.\n\n{warning_line}".encode("utf-16-le")) // 2
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

@router.message()
async def handle(msg: Message):
    age = (datetime.now(timezone.utc) - msg.date).total_seconds()
    if age > MAX_MSG_AGE:
        logger.info(f"SKIP stale msg age={age:.0f}s chat={msg.chat.id}")
        return

    text = msg.text or msg.caption or ""
    urls = URL_RE.findall(text)
    if not urls:
        return

    url = urls[0]
    screenshot.log_ram("Start request")

    # Быстрая синхронная проверка SSRF — до WARNING_INSTANT
    if not security.is_safe(url):
        await msg.reply("🚫 Посилання веде на недоступний ресурс.")
        return

    # Проверка blacklist — до WARNING_INSTANT, мгновенно, без Playwright
    blocked, reason = blacklist.is_blacklisted(url)
    if blocked:
        logger.info(f"BLACKLIST block url={url} reason={reason}")
        await msg.reply(
            "🚫 Це посилання заблоковано як фішингове.\n"
            "⚠️ Не переходьте за ним і не вводьте жодних даних."
        )
        return

    cached_file_id = cache.get(url)
    if cached_file_id:
        meta = await metadata.fetch(url)
        if meta and meta.get("title"):
            msg_text, msg_entities = build_message(meta)
        else:
            msg_text, msg_entities = build_disclaimer_only()
        cap_text, cap_entities = trim_caption(msg_text, msg_entities)
        await msg.reply_photo(
            photo=cached_file_id,
            caption=cap_text,
            caption_entities=cap_entities,
        )
        return

    status = await msg.reply(WARNING_INSTANT)
    start = time.monotonic()

    # Четыре задачи параллельно: метаданные + скриншот + SSRF-редиректы + WHOIS
    httpx_task = asyncio.create_task(metadata.fetch(url))
    shot_task = asyncio.create_task(screenshot.shoot(url))
    ssrf_task = asyncio.create_task(security.is_safe_after_redirects(url))
    whois_task = asyncio.create_task(domain_age.get_domain_age_warning(url))

    httpx_meta, (parts, browser_meta), redirect_safe, age_warning = await asyncio.gather(
        httpx_task, shot_task, ssrf_task, whois_task
    )

    if not redirect_safe:
        logger.warning(f"SSRF blocked after redirects: {url}")
        await status.edit_text("🚫 Посилання веде на недоступний ресурс (редирект).")
        return

    meta = merge_meta(httpx_meta, browser_meta)
    logger.info(f"Final meta: title={meta.get('title')} price={meta.get('price')}")

    elapsed = time.monotonic() - start

    if meta and meta.get("title"):
        msg_text, msg_entities = build_message(meta, age_warning)
    else:
        msg_text, msg_entities = build_disclaimer_only(age_warning)

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
                    cache.save(url, sent.photo[-1].file_id)
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
                if sent_list and sent_list[0].photo:
                    cache.save(url, sent_list[0].photo[-1].file_id)

            logger.info(f"OK+photo parts={len(parts)} url={url} time={elapsed:.1f}s")
        else:
            await msg.reply(text=msg_text, entities=msg_entities)
            logger.info(f"OK+text url={url} time={elapsed:.1f}s")

    except Exception as e:
        logger.error(f"FAIL url={url} error={e}")
        await status.edit_text("❌ Не вдалось обробити посилання.")
        return

    await status.delete()
