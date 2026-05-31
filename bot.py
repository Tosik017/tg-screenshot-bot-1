import re, time, asyncio
from aiogram import Router
from aiogram.types import Message, BufferedInputFile, MessageEntity
from loguru import logger
import cache, security, screenshot, metadata

router = Router()
URL_RE = re.compile(r'https?://[^\s]+')

WARNING_INSTANT = (
    "🚨 СТОП! Не переходьте за посиланням!\n\n"
    "⏳ Генерую безпечний попередній перегляд...\n"
    "Зачекайте — я покажу що там є, без ризику для вас."
)

DISCLAIMER = (
    "🛡 УВАГА! Це автоматичний попередній перегляд.\n"
    "❗ Не вводьте паролі та дані картки на незнайомих сайтах.\n"
    "🔍 Краще знайдіть цей товар через пошук Google."
)

def build_message(meta: dict) -> tuple[str, list[MessageEntity]]:
    """
    Будує текст + entities вручну.
    Code для назви — виділяє в рамку і дозволяє копіювати одним тапом.
    BlockQuote для попередження.
    """
    text = ""
    entities = []

    if meta.get("site_name"):
        text += f"🌐 {meta['site_name']}\n"

    # Назва в CODE — рамка + копіювання одним тапом
    if meta.get("title"):
        title = meta["title"]
        prefix = "📌 "
        text += prefix
        start = len(text.encode("utf-16-le")) // 2
        text += title + "\n"
        end = len(text.encode("utf-16-le")) // 2 - 1  # -1 за \n
        entities.append(MessageEntity(
            type="code",
            offset=start,
            length=end - start
        ))

    if meta.get("brand"):
        text += f"🏷 Бренд: {meta['brand']}\n"

    # Ціна жирна
    if meta.get("price"):
        price_str = f"💰 Ціна: {meta['price']}"
        start = len(text.encode("utf-16-le")) // 2
        text += price_str + "\n"
        end = len(text.encode("utf-16-le")) // 2 - 1
        entities.append(MessageEntity(
            type="bold",
            offset=start,
            length=end - start
        ))

    if meta.get("rating"):
        text += f"{meta['rating']}\n"

    if meta.get("description"):
        desc = meta["description"].strip()
        if len(desc) > 300:
            desc = desc[:300].rsplit(" ", 1)[0] + "…"
        text += f"\n📝 {desc}\n"

    # Відступ перед попередженням
    text += "\n"

    # Попередження — blockquote
    start = len(text.encode("utf-16-le")) // 2
    text += DISCLAIMER
    end = len(text.encode("utf-16-le")) // 2
    entities.append(MessageEntity(
        type="blockquote",
        offset=start,
        length=end - start
    ))

    return text, entities

def build_disclaimer_only() -> tuple[str, list[MessageEntity]]:
    text = "ℹ️ Не вдалось отримати дані про сторінку.\n\n" + DISCLAIMER
    start = len("ℹ️ Не вдалось отримати дані про сторінку.\n\n".encode("utf-16-le")) // 2
    end = len(text.encode("utf-16-le")) // 2
    entities = [MessageEntity(type="blockquote", offset=start, length=end - start)]
    return text, entities

@router.message()
async def handle(msg: Message):
    text = msg.text or msg.caption or ""
    urls = URL_RE.findall(text)
    if not urls:
        return

    url = urls[0]
    screenshot.log_ram("Start request")

    if not security.is_safe(url):
        await msg.reply("🚫 Посилання веде на недоступний ресурс.")
        return

    cached = cache.get(url)
    if cached:
        await msg.reply_photo(cached)
        return

    status = await msg.reply(WARNING_INSTANT)
    start = time.monotonic()

    meta_task = asyncio.create_task(metadata.fetch(url))
    shot_task = asyncio.create_task(screenshot.shoot(url))
    meta, shot = await asyncio.gather(meta_task, shot_task)

    elapsed = time.monotonic() - start

    if meta and meta.get("title"):
        msg_text, msg_entities = build_message(meta)
    else:
        msg_text, msg_entities = build_disclaimer_only()

    try:
        if shot:
            # Caption limit = 1024
            cap_text = msg_text
            cap_entities = msg_entities
            if len(cap_text) > 1024:
                cap_text = cap_text[:1021] + "…"
                cap_entities = [
                    e for e in cap_entities
                    if e.offset + e.length <= len(cap_text.encode("utf-16-le")) // 2
                ]

            sent = await msg.reply_photo(
                photo=BufferedInputFile(shot, filename="preview.png"),
                caption=cap_text,
                caption_entities=cap_entities,
            )
            if sent.photo:
                cache.save(url, sent.photo[-1].file_id)
            logger.info(f"OK+photo url={url} time={elapsed:.1f}s")
        else:
            await msg.reply(
                text=msg_text,
                entities=msg_entities,
            )
            logger.info(f"OK+text url={url} time={elapsed:.1f}s")

    except Exception as e:
        logger.error(f"FAIL url={url} error={e}")
        await status.edit_text("❌ Не вдалось обробити посилання.")
        return

    await status.delete()
