import re, time, asyncio
from aiogram import Router
from aiogram.types import Message, BufferedInputFile
from loguru import logger
import cache, security, screenshot, metadata

router = Router()
URL_RE = re.compile(r'https?://[^\s]+')

@router.message()
async def handle(msg: Message):
    text = msg.text or msg.caption or ""
    urls = URL_RE.findall(text)
    if not urls:
        return

    url = urls[0]
    screenshot.log_ram("Start request")

    if not security.is_safe(url):
        await msg.reply("🚫 Ссылка ведёт на недоступный ресурс.")
        return

    # Кэш — мгновенный ответ если уже делали
    cached = cache.get(url)
    if cached:
        await msg.reply_photo(cached)
        return

    status = await msg.reply("⏳ Загружаю...")
    start = time.monotonic()

    # Метаданные и скриншот параллельно — не ждём одно ради другого
    meta_task = asyncio.create_task(metadata.fetch(url))
    shot_task = asyncio.create_task(screenshot.shoot(url))
    meta, shot = await asyncio.gather(meta_task, shot_task)

    card_text = metadata.format_card(meta, url)
    elapsed = time.monotonic() - start

    try:
        if shot:
            # Карточка + скриншот вместе
            sent = await msg.reply_photo(
                photo=BufferedInputFile(shot, filename="preview.png"),
                caption=card_text,
            )
            if sent.photo:
                cache.save(url, sent.photo[-1].file_id)
            logger.info(f"OK+photo url={url} time={elapsed:.1f}s")
        else:
            # Только карточка — скриншот не получился (Cloudflare и т.п.)
            await msg.reply(card_text)
            logger.info(f"OK+text url={url} time={elapsed:.1f}s")

    except Exception as e:
        logger.error(f"FAIL url={url} error={e}")
        await status.edit_text("❌ Не удалось обработать ссылку.")
        return

    await status.delete()
