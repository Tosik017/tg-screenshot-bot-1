import re, time, asyncio
from aiogram import Router
from aiogram.types import Message, BufferedInputFile
from loguru import logger
import cache, security, screenshot, metadata

router = Router()
URL_RE = re.compile(r'https?://[^\s]+')

# Моментальное предупреждение — без MarkdownV2, просто текст
WARNING = (
    "🚨 СТОП! Не переходьте за посиланням!\n\n"
    "⏳ Генерую безпечний попередній перегляд...\n"
    "Зачекайте — я покажу що там є, без ризику для вас."
)

# Дисклеймер под превью — тоже простой текст
DISCLAIMER = (
    "\n\n"
    "━━━━━━━━━━━━━━━\n"
    "🛡 Автоматичний попередній перегляд\n"
    "Перевіряйте адресу сайту перед покупкою або введенням даних. "
    "За потреби знайдіть цей товар через пошук Google."
)

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

    # Моментально показываем предупреждение
    status = await msg.reply(WARNING)
    start = time.monotonic()

    # Параллельно грузим всё
    meta_task = asyncio.create_task(metadata.fetch(url))
    shot_task = asyncio.create_task(screenshot.shoot(url))
    meta, shot = await asyncio.gather(meta_task, shot_task)

    card = metadata.format_card(meta)
    caption = (card + DISCLAIMER) if card else DISCLAIMER.strip()
    elapsed = time.monotonic() - start

    try:
        if shot:
            sent = await msg.reply_photo(
                photo=BufferedInputFile(shot, filename="preview.png"),
                caption=caption,
            )
            if sent.photo:
                cache.save(url, sent.photo[-1].file_id)
            logger.info(f"OK+photo url={url} time={elapsed:.1f}s")
        else:
            await msg.reply(caption)
            logger.info(f"OK+text url={url} time={elapsed:.1f}s")

    except Exception as e:
        logger.error(f"FAIL url={url} error={e}")
        await status.edit_text("❌ Не вдалось обробити посилання.")
        return

    await status.delete()
