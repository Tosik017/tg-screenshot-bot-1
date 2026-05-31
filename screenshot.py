import asyncio
import os
import psutil
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async
from loguru import logger
from config import USER_AGENT, TIMEOUT_MS, PAUSE_MS, SEMAPHORE

semaphore = asyncio.Semaphore(SEMAPHORE)
_browser = None

def log_ram(label: str):
    """Легковесный мониторинг RAM."""
    proc = psutil.Process(os.getpid())
    mb = proc.memory_info().rss / 1024 / 1024
    logger.info(f"[RAM | {label}] {mb:.1f} MB")

async def init():
    global _browser
    pw = await async_playwright().start()
    _browser = await pw.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--no-first-run",
            "--no-zygote",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-default-apps",
            "--disable-sync",
            "--disable-translate",
            "--metrics-recording-only",
            "--mute-audio",
            "--no-default-browser-check",
            "--hide-scrollbars",
        ]
    )

async def _close_cookies(page):
    """Логика скрытия/принятия cookie-баннеров."""
    try:
        # Инъекция JS для поиска и клика по типичным кнопкам согласия
        await page.evaluate("""
            document.querySelectorAll('button, a, div').forEach(el => {
                if (/accept|agree|got it|ok|close|согласен|принять|понятно/i.test(el.innerText)) {
                    el.click();
                }
            });
        """)
        await page.wait_for_timeout(500)  # Даем время баннеру исчезнуть
    except Exception as e:
        logger.warning(f"Cookie close error: {e}")

async def shoot(url: str) -> list[bytes]:
    log_ram("Start")
    async with semaphore:
        # Возвращаем мобильный viewport (390x844)
        ctx = await _browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 390, "height": 844}
        )
        try:
            page = await ctx.new_page()
            await stealth_async(page)
            await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            await page.wait_for_timeout(PAUSE_MS)
            
            await _close_cookies(page)
            
            # Нарезка на 4 части
            screenshots = []
            viewport_height = 844
            for i in range(4):
                clip_y = i * viewport_height
                screenshots.append(
                    await page.screenshot(
                        clip={"x": 0, "y": clip_y, "width": 390, "height": viewport_height},
                        full_page=False
                    )
                )
            
            log_ram("After screenshot")
            return screenshots
        finally:
            await ctx.close()
