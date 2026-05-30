import re, time
from aiogram import Router
from aiogram.types import Message, BufferedInputFile
from loguru import logger
import cache, security, screenshot

router = Router()
URL_RE = re.compile(r'https?://[^\s]+')

@router.message()
async def handle(msg: Message):
    text = msg.text or msg.caption or ""
    urls = URL_RE.findall(text)
    if not urls: return
    url = urls[0]

    if not security.is_safe(url):
        await msg.reply("🚫 Ссылка недоступна (защита SSRF).")
        return

    cached = cache.get(url)
    if cached:
        await msg.reply_photo(cached)
        return

    status = await msg.reply("📸 Создаю превью...")
    try:
        data = await screenshot.shoot(url)
        sent = await msg.reply_photo(BufferedInputFile(data, filename="p.png"))
        if sent.photo: cache.save(url, sent.photo[-1].file_id)
        await status.delete()
    except Exception as e:
        logger.error(f"FAIL {url}: {e}")
        await status.edit_text("❌ Ошибка при генерации.")
