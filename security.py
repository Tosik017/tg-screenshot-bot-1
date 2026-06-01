import socket, ipaddress
from urllib.parse import urlparse

BLOCKED = [
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12",
    "192.168.0.0/16", "169.254.0.0/16",
    "100.64.0.0/10", "198.18.0.0/15",
    "224.0.0.0/4", "240.0.0.0/4",
    "fc00::/7", "fe80::/10", "::1",
]

def is_safe(url: str) -> bool:
    try:
        host = urlparse(url).hostname
        if not host: return False
        ip = socket.gethostbyname(host)
        obj = ipaddress.ip_address(ip)
        return not any(obj in ipaddress.ip_network(n) for n in BLOCKED)
    except Exception: return False
