import asyncio, os, psutil
from io import BytesIO
from PIL import Image
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async
from loguru import logger
from config import USER_AGENT, TIMEOUT_MS, PAUSE_MS, SEMAPHORE
from metadata import parse_from_html

semaphore = asyncio.Semaphore(SEMAPHORE)
_browser = None

MOBILE_WIDTH = 390
MOBILE_HEIGHT = 844

# Максимальная высота до обрезки — защита от OOM на Render Free
MAX_HEIGHT = 8000

# Максимальная высота одной части для отправки в Telegram
PART_HEIGHT = 1280

COOKIE_SELECTORS = [
    "button[id*='accept']",
    "button[class*='accept']",
    "button[aria-label*='Accept']",
    "button[aria-label*='Agree']",
    "[id*='cookie'] button",
    "[class*='cookie'] button",
    "[class*='consent'] button",
]

AD_HOSTS = (
    "doubleclick.net",
    "googlesyndication.com",
    "googleadservices.com",
    "adnxs.com",
    "criteo.com",
    "taboola.com",
    "outbrain.com",
    "facebook.net",
    "google-analytics.com",
    "mc.yandex.ru",
    "counter.yadro.ru",
)

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

async def _route_handler(route):
    req = route.request
    url = req.url

    if req.resource_type in ("media", "websocket"):
        await route.abort()
        return
    if any(url.endswith(ext) for ext in (".woff", ".woff2", ".ttf", ".otf", ".eot")):
        await route.abort()
        return
    if any(ext in url for ext in (".mp4", ".webm", ".avi", ".mov")):
        await route.abort()
        return
    if any(host in url for host in AD_HOSTS):
        await route.abort()
        return

    await route.continue_()

def _split_image(png_bytes: bytes) -> list[bytes]:
    """
    Умная нарезка через Pillow.
    Делает из одного большого PNG список частей по PART_HEIGHT пикселей.
    Максимальная высота ограничена MAX_HEIGHT — защита от OOM.
    """
    img = Image.open(BytesIO(png_bytes))
    width, height = img.size

    # Защита от огромных страниц
    if height > MAX_HEIGHT:
        img = img.crop((0, 0, width, MAX_HEIGHT))
        height = MAX_HEIGHT

    # Если страница короче одной части — возвращаем как есть
    if height <= PART_HEIGHT:
        buf = BytesIO()
        img.save(buf, format="PNG")
        return [buf.getvalue()]

    # Нарезаем на части
    parts = []
    top = 0
    while top < height:
        bottom = min(top + PART_HEIGHT, height)
        part = img.crop((0, top, width, bottom))
        buf = BytesIO()
        part.save(buf, format="PNG")
        parts.append(buf.getvalue())
        top = bottom

    logger.info(f"Split into {len(parts)} parts, total height={height}px")
    return parts

async def shoot(url: str) -> tuple[list[bytes], dict]:
    """
    Возвращает (список частей скриншота, метаданные).
    full_page=True — снимаем всю страницу.
    Потом нарезаем через Pillow — без проблем с координатами браузера.
    """
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
            await page.route("**/*", _route_handler)

            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=TIMEOUT_MS
            )
            await page.wait_for_timeout(PAUSE_MS)
            await _close_cookies(page)

            # Метаданные из браузера
            html = await page.content()
            browser_meta = parse_from_html(html, url)
            logger.info(
                f"Browser meta: title={browser_meta.get('title')} "
                f"price={browser_meta.get('price')}"
            )

            # Полная страница одним снимком
            full_png = await page.screenshot(
                full_page=True,
                animations="disabled",
                timeout=30_000
            )
            log_ram("After screenshot")

            # Нарезаем через Pillow
            parts = _split_image(full_png)
            return parts, browser_meta

        except Exception as e:
            logger.warning(f"Screenshot failed: {e}")
            return [], {}

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
