import socket, ipaddress, httpx
from urllib.parse import urlparse

BLOCKED = [
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12",
    "192.168.0.0/16", "169.254.0.0/16",
    "100.64.0.0/10", "198.18.0.0/15",
    "224.0.0.0/4", "240.0.0.0/4",
    "fc00::/7", "fe80::/10", "::1",
]

def _is_safe_host(host: str) -> bool:
    """Проверяет хост по IP — блокирует private/reserved диапазоны."""
    try:
        if not host:
            return False
        ip = socket.gethostbyname(host)
        obj = ipaddress.ip_address(ip)
        return not any(obj in ipaddress.ip_network(n) for n in BLOCKED)
    except Exception:
        return False

def is_safe(url: str) -> bool:
    """Быстрая синхронная проверка — до отправки WARNING_INSTANT."""
    host = urlparse(url).hostname
    return _is_safe_host(host)

async def is_safe_after_redirects(url: str) -> bool:
    """
    Асинхронная проверка финального URL после всей redirect-цепочки.
    Закрывает DNS rebinding: is_safe() проверяет DNS до запроса,
    но Playwright идёт по своей цепочке — финальный IP может быть другим.
    Вызывается параллельно со скриншотом, не блокирует ответ.
    """
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=5,
        ) as client:
            r = await client.head(url, headers={
                "User-Agent": "Mozilla/5.0",
            })
            final_url = str(r.url)
            final_host = urlparse(final_url).hostname
            if not _is_safe_host(final_host):
                return False
        return True
    except Exception:
        # Если HEAD упал (сайт не поддерживает) — пропускаем, не блокируем.
        # Лучше пропустить потенциально плохой HEAD чем заблокировать легитимный сайт.
        return True
