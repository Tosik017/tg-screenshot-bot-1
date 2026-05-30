import hashlib
from cachetools import TTLCache
from config import CACHE_SIZE, CACHE_TTL

_store = TTLCache(maxsize=CACHE_SIZE, ttl=CACHE_TTL)

def _key(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()

def get(url: str): return _store.get(_key(url))
def save(url: str, file_id: str): _store[_key(url)] = file_id
