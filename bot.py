import re, time
from aiogram import Router
from aiogram.types import Message, BufferedInputFile
from aiogram.utils.media_group import MediaGroupBuilder
from loguru import logger
import cache, security, screenshot

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
    cached = cache.get(url)
    if cached:
        await msg.reply_photo(cached)
        return
    status = await msg.reply("📸 Создаю превью...")
    start = time.monotonic()
    try:
        parts = await screenshot.shoot(url)
        album = MediaGroupBuilder()
        for i, data in enumerate(parts):
            album.add_photo(
                BufferedInputFile(data, filename=f"part_{i+1}.png")
            )
        sent_list = await msg.reply_media_group(media=album.build())
        if sent_list and sent_list[0].photo:
            cache.save(url, sent_list[0].photo[-1].file_id)
        elapsed = time.monotonic() - start
        total_kb = sum(len(p) for p in parts) // 1024
        screenshot.log_ram("After render")
        logger.info(
            f"OK url={url} parts={len(parts)} "
            f"time={elapsed:.1f}s size={total_kb}kb"
        )
    except Exception as e:
        screenshot.log_ram("After render (error)")
        logger.error(f"FAIL url={url} error={e}")
        await status.edit_text("❌ Не удалось сделать скриншот.")
        return
    await status.delete()
