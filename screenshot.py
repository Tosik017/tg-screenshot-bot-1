import asyncio, os, psutil
from PIL import Image
from io import BytesIO
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async
from loguru import logger
from config import USER_AGENT, TIMEOUT_MS, PAUSE_MS, SEMAPHORE

semaphore = asyncio.Semaphore(SEMAPHORE)
_browser = None

MOBILE_WIDTH = 390
PARTS = 4
MAX_PAGE_HEIGHT = 16000

COOKIE_SELECTORS = [
    "button[id*='accept']",
    "button[class*='accept']",
    "button[aria-label*='Accept']",
    "button[aria-label*='Agree']",
    "[id*='cookie'] button",
    "[class*='cookie'] button",
    "[class*='consent'] button",
]

def log_ram(label: str):
    mb = psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
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
            "--mute-audio",
            "--hide-scrollbars",
            # Отключаем загрузку шрифтов — главная причина таймаутов
            "--disable-remote-fonts",
        ]
    )
    log_ram("Browser started")

async def shoot(url: str) -> list[bytes]:
    log_ram("Before request")
    async with semaphore:
        ctx = await _browser.new_context(
            viewport={"width": MOBILE_WIDTH, "height": 844},
            user_agent=USER_AGENT,
            device_scale_factor=2,
        )
        try:
            page = await ctx.new_page()
            await stealth_async(page)

            # Блокируем внешние шрифты на уровне сети
            await page.route(
                "**/{*.woff,*.woff2,*.ttf,*.otf,*.eot}",
                lambda route: route.abort()
            )

            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=TIMEOUT_MS
            )
            await page.wait_for_timeout(PAUSE_MS)
            await _close_cookies(page)

            # timeout=10_000 на сам скриншот
            # animations="disabled" — не ждём анимаций и шрифтов
            full_png = await page.screenshot(
                full_page=True,
                animations="disabled",
                timeout=10_000
            )
            log_ram("After screenshot")

            return _split_image(full_png, PARTS, MAX_PAGE_HEIGHT)

        finally:
            await ctx.close()

def _split_image(png_bytes: bytes, parts: int, max_height: int) -> list[bytes]:
    img = Image.open(BytesIO(png_bytes))
    width, height = img.size

    if height > max_height:
        img = img.crop((0, 0, width, max_height))
        height = max_height

    part_height = height // parts
    result = []

    for i in range(parts):
        top = i * part_height
        bottom = top + part_height if i < parts - 1 else height
        if top >= height:
            break
        part = img.crop((0, top, width, bottom))
        buf = BytesIO()
        part.save(buf, format="PNG")
        result.append(buf.getvalue())

    return result

async def _close_cookies(page):
    for sel in COOKIE_SELECTORS:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=500):
                await btn.click()
                await page.wait_for_timeout(500)
                return
        except Exception:
            continue
