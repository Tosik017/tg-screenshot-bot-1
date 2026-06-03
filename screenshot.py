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
_pw = None  # держим playwright-инстанс чтобы корректно перезапускать браузер

# Счётчик запросов — каждые RESTART_EVERY скриншотов перезапускаем браузер.
# Playwright постепенно раздувает V8 heap и internal page cache.
# 50 запросов — ~2–5 часов работы на Render Free при реальной нагрузке.
_request_count = 0
RESTART_EVERY = 50

DEVICE_SCALE = 2  # ретина-качество скриншота (физ. px = логич. × DEVICE_SCALE)

MOBILE_WIDTH = 390
MOBILE_HEIGHT = 844

# Висота однієї частини при нарізці — більше ризиковано: Telegram ліміт ~10 МБ на фото
PART_HEIGHT = 1280

# Скільки повних частин максимум обробляємо — захист від OOM на Render Free (512 МБ)
MAX_PARTS = 4

# Гранична фізична висота: PART_HEIGHT × MAX_PARTS рівно без хвостика
MAX_HEIGHT = PART_HEIGHT * MAX_PARTS  # 1280 × 4 = 5120 px (фізичні, вже з DEVICE_SCALE)

# Гранична висота ЗАХВАТУ в CSS px = MAX_HEIGHT / DEVICE_SCALE.
# КЛЮЧОВЕ: обмежуємо висоту ТУТ, на рівні браузера, а не постфактум у Pillow.
# full_page=True змушує Chromium відрендерити ВСЮ сторінку в один битмап ще до
# повернення байтів; на лістингах (hotline.ua) це десятки тисяч px → OOM на 512 МБ.
# 5120 / 2 = 2560 CSS px.
MAX_CAPTURE_HEIGHT = MAX_HEIGHT // DEVICE_SCALE

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
    global _browser, _pw
    _pw = await async_playwright().start()
    _browser = await _pw.chromium.launch(
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

async def _restart_browser():
    """Перезапуск браузера для сброса накопленной памяти Playwright.
    Вызывается между запросами (семафор уже захвачен), поэтому безопасно."""
    global _browser, _pw, _request_count
    logger.info(f"[BROWSER RESTART] after {RESTART_EVERY} requests — clearing V8 heap")
    log_ram("Before restart")
    try:
        await _browser.close()
    except Exception as e:
        logger.warning(f"Browser close error (non-critical): {e}")
    try:
        await _pw.stop()
    except Exception as e:
        logger.warning(f"Playwright stop error (non-critical): {e}")
    await init()
    _request_count = 0
    log_ram("After restart")

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
    Розумна нарізка через Pillow.
    Робить з одного великого PNG список частин по PART_HEIGHT пікселів.
    Захват уже обмежений MAX_CAPTURE_HEIGHT на рівні браузера, тож crop тут —
    лише страховка (фактично не спрацьовує).
    """
    img = Image.open(BytesIO(png_bytes))
    width, height = img.size

    # Страховка: якщо раптом більше MAX_HEIGHT — обрізаємо.
    if height > MAX_HEIGHT:
        img = img.crop((0, 0, width, MAX_HEIGHT))
        height = MAX_HEIGHT

    # Якщо сторінка коротша за одну частину — повертаємо як є
    if height <= PART_HEIGHT:
        buf = BytesIO()
        img.save(buf, format="PNG")
        return [buf.getvalue()]

    # Ріжемо на частини
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
    Повертає (список частин скриншота, метадані).
    Захоплюємо ОБМЕЖЕНУ висоту на рівні браузера (НЕ full_page):
    full_page рендерить всю сторінку в один битмап ДО повернення байтів —
    на довгих лістингах (hotline.ua) це десятки тисяч px → OOM на 512 МБ.
    Обрізка в Pillow тут не рятує: пам'ять вже вибухнула в браузері.
    """
    global _request_count

    log_ram("Before screenshot")
    async with semaphore:
        # Перезапуск браузера кожні RESTART_EVERY запитів — скидаємо V8 heap і page cache.
        # Виконується всередині семафору — жодного паралельного запиту в цей момент.
        _request_count += 1
        if _request_count >= RESTART_EVERY:
            await _restart_browser()

        ctx = await _browser.new_context(
            viewport={"width": MOBILE_WIDTH, "height": MOBILE_HEIGHT},
            user_agent=USER_AGENT,
            device_scale_factor=DEVICE_SCALE,
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

            # Метадані з браузера
            html = await page.content()
            browser_meta = parse_from_html(html, url)
            logger.info(
                f"Browser meta: title={browser_meta.get('title')} "
                f"price={browser_meta.get('price')}"
            )

            # Висота документа (CSS px). Обмежуємо захват зверху до MAX_CAPTURE_HEIGHT,
            # щоб Chromium НЕ рендерив гігантський битмап (це і є причина OOM).
            doc_height = await page.evaluate(
                "Math.max(document.body.scrollHeight, document.documentElement.scrollHeight, "
                "document.body.offsetHeight, document.documentElement.offsetHeight)"
            )
            capture_h = min(max(int(doc_height), MOBILE_HEIGHT), MAX_CAPTURE_HEIGHT)
            logger.info(f"Capture height: doc={int(doc_height)} → clamp={capture_h} CSS px")

            # Розширюємо viewport рівно на висоту захвату і знімаємо БЕЗ full_page.
            # Скриншот viewport = битмап рівно MOBILE_WIDTH × capture_h × DEVICE_SCALE —
            # пам'ять обмежена і не залежить від реальної довжини сторінки.
            await page.set_viewport_size({"width": MOBILE_WIDTH, "height": capture_h})
            await page.wait_for_timeout(500)  # reflow після зміни viewport

            full_png = await page.screenshot(
                animations="disabled",
                timeout=20_000
            )
            log_ram("After screenshot")

            # Ріжемо через Pillow
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
