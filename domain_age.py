import whois
from datetime import datetime, timezone
from urllib.parse import urlparse
from loguru import logger

# Домен моложе этого порога — красный флаг фишинга.
# 30 дней — стандарт в threat intelligence: большинство фишинг-доменов
# живут 1–7 дней, редко больше месяца.
YOUNG_DOMAIN_DAYS = 30

def _get_creation_date(w) -> datetime | None:
    """Извлекает дату создания из whois-ответа — может быть list или datetime."""
    cd = w.creation_date
    if isinstance(cd, list):
        cd = cd[0]
    if isinstance(cd, datetime):
        return cd
    return None

async def get_domain_age_warning(url: str) -> str | None:
    """
    Возвращает строку-предупреждение если домен молодой, иначе None.
    Запускается в executor чтобы не блокировать event loop —
    python-whois синхронный и делает DNS/socket запросы.
    """
    import asyncio
    try:
        host = urlparse(url).hostname
        if not host:
            return None

        loop = asyncio.get_event_loop()
        w = await loop.run_in_executor(None, whois.whois, host)

        created = _get_creation_date(w)
        if not created:
            return None

        # whois может вернуть naive datetime — приводим к UTC
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)

        age_days = (datetime.now(timezone.utc) - created).days

        if age_days < 0:
            return None

        if age_days < YOUNG_DOMAIN_DAYS:
            logger.info(f"Young domain: {host} age={age_days}d")
            return f"🆕 Домен зареєстровано {age_days} дн. тому — типова ознака фішингу!"

        logger.info(f"Domain age OK: {host} age={age_days}d")
        return None

    except Exception as e:
        logger.warning(f"WHOIS failed for {url}: {e}")
        return None
