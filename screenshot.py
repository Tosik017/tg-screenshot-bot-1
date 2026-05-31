import asyncio, math, os, psutil
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async
from loguru import logger
from config import USER_AGENT, TIMEOUT_MS, PAUSE_MS, SEMAPHORE

semaphore = asyncio.Semaphore(SEMAPHORE)
_browser = None

MOBILE_WIDTH = 390
MOBILE_HEIGHT = 844
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
        ]
    )
    log_ram("Browser started")

async def shoot(url: str) -> list[bytes]:
    log_ram("Before request")
    async with semaphore:
        ctx = await _browser.new_context(
            viewport={"width": MOBILE_WIDTH, "height": MOBILE_HEIGHT},
            user_agent=USER_AGENT,
            device_scale_factor=2,
        )
        try:
            page = await ctx.new_page()
            await stealth_async(page)
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=TIMEOUT_MS
            )
            await page.wait_for_timeout(PAUSE_MS)
            await _close_cookies(page)
            page_height = await page.evaluate(
                "document.documentElement.scrollHeight"
            )
            page_height = min(page_height, MAX_PAGE_HEIGHT)
            part_height = math.ceil(page_height / PARTS)
            screenshots = []
            for i in range(PARTS):
                y = i * part_height
                if y >= page_height:
                    break
                await page.evaluate(f"window.scrollTo(0, {y})")
                await page.wait_for_timeout(300)
                shot = await page.screenshot(
                    full_page=False,
                    clip={
                        "x": 0,
                        "y": y,
                        "width": MOBILE_WIDTH,
                        "height": min(part_height, page_height - y)
                    }
                )
                screenshots.append(shot)
            log_ram("After screenshot")
            return screenshots
        finally:
            await ctx.close()

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
