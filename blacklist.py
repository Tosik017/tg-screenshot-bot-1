import asyncio
import httpx
from urllib.parse import urlparse
from loguru import logger

# --- Фиды доменов (plain text, один домен на строку) ---
# Phishing.Army: агрегатор из 6 источников, обновляется каждые 6 часов
# OpenPhish: реальный URL-фид, извлекаем домены
FEEDS = [
    "https://phishing.army/download/phishing_army_blocklist_extended.txt",
    "https://openphish.com/feed.txt",
]

# Подозрительные TLD — статический список, не требует обновления.
# Статистически лидируют по фишинг-атакам. Проверка до загрузки фидов.
SUSPICIOUS_TLDS = {
    ".tk", ".ml", ".ga", ".cf", ".gq",  # Freenom — 90%+ фишинга
    ".top", ".xyz", ".pw", ".cc", ".su",
    ".buzz", ".cyou", ".icu", ".monster",
}

# Домены из фидов — хранятся как set для O(1) lookup
_blacklisted: set[str] = set()
_update_task: asyncio.Task | None = None

def _extract_domain(line: str) -> str | None:
    """Извлекает домен из строки — поддерживает URL и просто домены."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    # Если строка — URL (из OpenPhish feed.txt)
    if line.startswith("http"):
        host = urlparse(line).hostname
        return host.lower() if host else None
    # Если просто домен (Phishing.Army)
    return line.lower()

async def _fetch_feed(url: str) -> set[str]:
    """Загружает один фид и возвращает set доменов."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url)
        domains = set()
        for line in r.text.splitlines():
            d = _extract_domain(line)
            if d:
                domains.add(d)
        logger.info(f"Blacklist feed loaded: {len(domains)} domains from {url}")
        return domains
    except Exception as e:
        logger.warning(f"Blacklist feed failed ({url}): {e}")
        return set()

async def update():
    """Загружает все фиды и обновляет _blacklisted. Вызывается при старте и раз в 6 часов."""
    global _blacklisted
    results = await asyncio.gather(*[_fetch_feed(url) for url in FEEDS])
    merged = set().union(*results)
    _blacklisted = merged
    logger.info(f"Blacklist updated: {len(_blacklisted)} total domains")

async def _background_updater():
    """Фоновая задача: обновляем фиды каждые 6 часов."""
    while True:
        await asyncio.sleep(6 * 60 * 60)
        await update()

def start_background_updater():
    """Запускает фоновое обновление. Вызывается из main.py после первой загрузки."""
    global _update_task
    _update_task = asyncio.create_task(_background_updater())

def is_blacklisted(url: str) -> tuple[bool, str]:
    """
    Проверяет URL по двум уровням:
    1. TLD — мгновенно (статический список)
    2. Domain — O(1) lookup в set из фидов

    Возвращает (заблокирован: bool, причина: str)
    """
    try:
        host = urlparse(url).hostname
        if not host:
            return False, ""

        host = host.lower()

        # Уровень 1: подозрительный TLD
        for tld in SUSPICIOUS_TLDS:
            if host.endswith(tld):
                return True, f"suspicious TLD ({tld})"

        # Уровень 2: домен в фиде
        # Проверяем сам хост и корневой домен (sub.evil.com → evil.com)
        parts = host.split(".")
        root_domain = ".".join(parts[-2:]) if len(parts) >= 2 else host

        if host in _blacklisted or root_domain in _blacklisted:
            return True, "phishing feed"

        return False, ""
    except Exception:
        return False, ""
