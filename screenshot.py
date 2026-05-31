import asyncio, os, psutil
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async
from loguru import logger
from config import USER_AGENT, TIMEOUT_MS, PAUSE_MS, SEMAPHORE

semaphore = asyncio.Semaphore(SEMAPHORE)
_browser = None

MOBILE_WIDTH = 390
MOBILE_HEIGHT = 844

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
            "--disable-remote-fonts",
        ]
    )
    log_ram("Browser started")

async def shoot(url: str) -> bytes | None:
    """Один скриншот. Возвращает bytes или None если не получилось."""
    log_ram("Before screenshot")
    async with semaphore:
        ctx = await _browser.new_context(
            viewport={"width": MOBILE_WIDTH, "height": MOBILE_HEIGHT},
            user_agent=USER_AGENT,
            device_scale_factor=2,
        )
        try:
            page = await ctx.new_page()
            await stealth_async(page)

            await page.route(
                "**/*.{woff,woff2,ttf,otf,eot}",
                lambda route: route.abort()
            )

            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=TIMEOUT_MS
            )
            await page.wait_for_timeout(PAUSE_MS)
            await _close_cookies(page)

            shot = await page.screenshot(
                full_page=False,
                clip={
                    "x": 0,
                    "y": 0,
                    "width": MOBILE_WIDTH,
                    "height": MOBILE_HEIGHT
                },
                animations="disabled",
                timeout=10_000
            )
            log_ram("After screenshot")
            return shot

        except Exception as e:
            logger.warning(f"Screenshot failed: {e}")
            return None

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
