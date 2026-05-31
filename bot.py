import re, time
from aiogram import Router
from aiogram.types import Message, BufferedInputFile
from aiogram.utils.media_group import MediaGroupBuilder
from loguru import logger
import security, screenshot

router = Router()
URL_RE = re.compile(r'https?://[^\s]+')

@router.message()
async def handle(msg: Message):
    text = msg.text or msg.caption or ""
    urls = URL_RE.findall(text)
    if not urls:
        return

    url = urls[0]
    
    # 1. Замеряем память на старте (берем функцию из screenshot.py)
    screenshot.log_ram("Start Request")

    if not security.is_safe(url):
        await msg.reply("🚫 Ссылка ведёт на недоступный ресурс.")
        return

    status = await msg.reply("📸 Создаю превью (мобильный формат, 4 части)...")
    start = time.monotonic()

    try:
        # 2. Получаем список из 4 картинок
        data_list = await screenshot.shoot(url)
        
        # 3. Собираем картинки в альбом (MediaGroup)
        album = MediaGroupBuilder()
        for i, data in enumerate(data_list):
            album.add_photo(BufferedInputFile(data, filename=f"part_{i}.png"))
        
        # Отправляем альбом пользователю
        await msg.reply_media_group(media=album.build())

        elapsed = time.monotonic() - start
        logger.info(f"OK url={url} time={elapsed:.1f}s")
        
        # 4. Замеряем память после успешного рендера
        screenshot.log_ram("After Render")

    except Exception as e:
        # Замеряем память перед возможным падением бота
        screenshot.log_ram("After Render (Error)")
        logger.error(f"FAIL url={url} error={e}")
        await status.edit_text("❌ Не удалось сделать скриншот.")
        return

    await status.delete()
