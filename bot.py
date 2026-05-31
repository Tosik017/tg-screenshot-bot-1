import re, time, asyncio
from aiogram import Router
from aiogram.types import Message, BufferedInputFile
from loguru import logger
import cache, security, screenshot, metadata

router = Router()
URL_RE = re.compile(r'https?://[^\s]+')

DISCLAIMER = (
    "\n\n"
    "⚠️ *Це автоматичний попередній перегляд.*\n"
    "Будьте обережні з незнайомими посиланнями — "
    "завжди перевіряйте адресу сайту перед тим, як вводити особисті дані або робити покупку. "
    "За потреби знайдіть цей товар або сторінку через пошук Google."
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

    # Кэш — мгновенный ответ
    cached = cache.get(url)
    if cached:
        await msg.reply_photo(cached)
        return

    status = await msg.reply("🔍 Аналізую посилання...")
    start = time.monotonic()

    # Метаданные и скриншот параллельно
    meta_task = asyncio.create_task(metadata.fetch(url))
    shot_task = asyncio.create_task(screenshot.shoot(url))
    meta, shot = await asyncio.gather(meta_task, shot_task)

    # Карточка без ссылки — она уже есть в оригинальном сообщении
    card = metadata.format_card(meta)
    caption = (card + DISCLAIMER) if card else DISCLAIMER.strip()

    elapsed = time.monotonic() - start

    try:
        if shot:
            # Скриншот + карточка + предупреждение
            sent = await msg.reply_photo(
                photo=BufferedInputFile(shot, filename="preview.png"),
                caption=caption,
                parse_mode="Markdown",
            )
            if sent.photo:
                cache.save(url, sent.photo[-1].file_id)
            logger.info(f"OK+photo url={url} time={elapsed:.1f}s")
        else:
            # Только карточка + предупреждение (Cloudflare или недоступный сайт)
            await msg.reply(caption, parse_mode="Markdown")
            logger.info(f"OK+text url={url} time={elapsed:.1f}s")

    except Exception as e:
        logger.error(f"FAIL url={url} error={e}")
        await status.edit_text("❌ Не вдалось обробити посилання.")
        return

    await status.delete()
