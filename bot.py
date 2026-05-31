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
    text = ""
    entities = []

    if meta.get("site_name"):
        text += f"🌐 {meta['site_name']}\n"

    if meta.get("title"):
        title = meta["title"]
        prefix = "📌 "
        text += prefix
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

def trim_caption(text: str, entities: list[MessageEntity]) -> tuple[str, list[MessageEntity]]:
    """Обрезает caption до 1024 символов с учётом entities."""
    if len(text) <= 1024:
        return text, entities
    text = text[:1021] + "…"
    limit = len(text.encode("utf-16-le")) // 2
    entities = [e for e in entities if e.offset + e.length <= limit]
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

    # Кэш — берём file_id но метаданные всё равно подгружаем
    cached_file_id = cache.get(url)
    if cached_file_id:
        # Быстро берём только метаданные (без скриншота)
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

    # Моментальное предупреждение
    status = await msg.reply(WARNING_INSTANT)
    start = time.monotonic()

    # Параллельно: метаданные + скриншот
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
            cap_text, cap_entities = trim_caption(msg_text, msg_entities)
            sent = await msg.reply_photo(
                photo=BufferedInputFile(shot, filename="preview.png"),
                caption=cap_text,
                caption_entities=cap_entities,
            )
            if sent.photo:
                cache.save(url, sent.photo[-1].file_id)
            logger.info(f"OK+photo url={url} time={elapsed:.1f}s")
        else:
            await msg.reply(text=msg_text, entities=msg_entities)
            logger.info(f"OK+text url={url} time={elapsed:.1f}s")

    except Exception as e:
        logger.error(f"FAIL url={url} error={e}")
        await status.edit_text("❌ Не вдалось обробити посилання.")
        return

    await status.delete()
