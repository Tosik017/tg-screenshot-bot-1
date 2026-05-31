import re, time, asyncio
from aiogram import Router
from aiogram.types import Message, BufferedInputFile
from aiogram.utils.formatting import Text, Bold, Code, BlockQuote, as_section, as_line
from loguru import logger
import cache, security, screenshot, metadata

router = Router()
URL_RE = re.compile(r'https?://[^\s]+')

WARNING_INSTANT = (
    "🚨 СТОП! Не переходьте за посиланням!\n\n"
    "⏳ Генерую безпечний попередній перегляд...\n"
    "Зачекайте — я покажу що там є, без ризику для вас."
)

def build_caption(meta: dict) -> tuple[str, list]:
    """
    Повертає (text, entities) для відправки з форматуванням.
    Використовуємо aiogram formatting API.
    """
    content = []

    if meta.get("site_name"):
        content.append(as_line("🌐 ", meta["site_name"]))

    # Назва — жирний текст для виділення
    if meta.get("title"):
        content.append(as_line(Bold("📌 " + meta["title"])))

    if meta.get("brand"):
        content.append(as_line("🏷 Бренд: ", meta["brand"]))

    if meta.get("price"):
        content.append(as_line(Bold("💰 Ціна: " + str(meta["price"]))))

    if meta.get("rating"):
        content.append(as_line(meta["rating"]))

    if meta.get("description"):
        desc = meta["description"].strip()
        if len(desc) > 300:
            desc = desc[:300].rsplit(" ", 1)[0] + "…"
        content.append(Text("\n📝 " + desc))

    # Предупреждение цитатой
    disclaimer = (
        "🛡 УВАГА! Це автоматичний попередній перегляд.\n"
        "❗ Не вводьте паролі та дані картки на незнайомих сайтах.\n"
        "🔍 Краще знайдіть цей товар через пошук Google."
    )
    content.append(Text("\n"))
    content.append(BlockQuote(disclaimer))

    result = as_section(*content)
    return result.render()

def build_disclaimer_only() -> tuple[str, list]:
    """Тільки попередження якщо метаданих немає."""
    disclaimer = (
        "ℹ️ Не вдалось отримати дані про сторінку.\n\n"
        "🛡 УВАГА! Це автоматичний попередній перегляд.\n"
        "❗ Не вводьте паролі та дані картки на незнайомих сайтах.\n"
        "🔍 Краще знайдіть цей товар через пошук Google."
    )
    result = BlockQuote(disclaimer)
    return result.render()

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
    cached = cache.get(url)
    if cached:
        await msg.reply_photo(cached)
        return

    # Моментальное предупреждение
    status = await msg.reply(WARNING_INSTANT)
    start = time.monotonic()

    # Параллельно: метаданные + скриншот
    meta_task = asyncio.create_task(metadata.fetch(url))
    shot_task = asyncio.create_task(screenshot.shoot(url))
    meta, shot = await asyncio.gather(meta_task, shot_task)

    elapsed = time.monotonic() - start

    # Строим форматированный текст
    if meta and meta.get("title"):
        formatted_text, entities = build_caption(meta)
    else:
        formatted_text, entities = build_disclaimer_only()

    # Обрезаем caption до лимита Telegram (1024 для фото, 4096 для текста)
    if len(formatted_text) > 1024:
        formatted_text = formatted_text[:1020] + "…"
        entities = [e for e in entities if e.offset + e.length <= 1020]

    try:
        if shot:
            sent = await msg.reply_photo(
                photo=BufferedInputFile(shot, filename="preview.png"),
                caption=formatted_text,
                caption_entities=entities,
            )
            if sent.photo:
                cache.save(url, sent.photo[-1].file_id)
            logger.info(f"OK+photo url={url} time={elapsed:.1f}s")
        else:
            # Скриншота нет — отправляем текст (лимит 4096)
            full_text = formatted_text
            if len(full_text) > 4096:
                full_text = full_text[:4092] + "…"
            await msg.reply(
                text=full_text,
                entities=entities,
            )
            logger.info(f"OK+text url={url} time={elapsed:.1f}s")

    except Exception as e:
        logger.error(f"FAIL url={url} error={e}")
        await status.edit_text("❌ Не вдалось обробити посилання.")
        return

    await status.delete()
