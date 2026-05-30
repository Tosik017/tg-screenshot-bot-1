import asyncio
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async
from config import USER_AGENT, TIMEOUT_MS, PAUSE_MS, SEMAPHORE

semaphore = asyncio.Semaphore(SEMAPHORE)
_browser = None

async def init():
    global _browser
    pw = await async_playwright().start()
    _browser = await pw.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
    )

async def shoot(url: str) -> bytes:
    async with semaphore:
        ctx = await _browser.new_context(user_agent=USER_AGENT)
        try:
            page = await ctx.new_page()
            await stealth_async(page)
            await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            await page.wait_for_timeout(PAUSE_MS)
            return await page.screenshot(clip={"x": 0, "y": 0, "width": 1280, "height": 4000})
        finally:
            await ctx.close()
