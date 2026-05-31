import re, time, asyncio
from aiogram import Router
from aiogram.types import Message, BufferedInputFile, MessageEntity, InputMediaPhoto
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
    result = {**httpx_meta}
    for key, value in browser_meta.items():
        if value and (key not in result or not result[key]):
            result[key] = value
        if key in ("price", "rating", "brand") and value:
            result[key] = value
    return result

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

    # Кэш
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

    httpx_task = asyncio.create_task(metadata.fetch(url))
    shot_task = asyncio.create_task(screenshot.shoot(url))

    httpx_meta, (parts, browser_meta) = await asyncio.gather(httpx_task, shot_task)

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
                # Одна часть — фото с карточкой
                sent = await msg.reply_photo(
                    photo=BufferedInputFile(parts[0], filename="preview.png"),
                    caption=cap_text,
                    caption_entities=cap_entities,
                )
                if sent.photo:
                    cache.save(url, sent.photo[-1].file_id)
            else:
                # Несколько частей — медиагруппа
                # Карточка идёт к первой части
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
                # Кэшируем первое фото
                if sent_list and sent_list[0].photo:
                    cache.save(url, sent_list[0].photo[-1].file_id)

            logger.info(f"OK+photo parts={len(parts)} url={url} time={elapsed:.1f}s")
        else:
            # Скриншот не получился — только карточка
            await msg.reply(text=msg_text, entities=msg_entities)
            logger.info(f"OK+text url={url} time={elapsed:.1f}s")

    except Exception as e:
        logger.error(f"FAIL url={url} error={e}")
        await status.edit_text("❌ Не вдалось обробити посилання.")
        return

    await status.delete()
