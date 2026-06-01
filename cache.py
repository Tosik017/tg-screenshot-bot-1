"""
Умный кэш под скудные ресурсы Render Free.

Хранит:
- Успешные ответы (file_id + меta) с дифференцированным TTL
- Negative cache (неудачные URL) на короткий срок — защита от повторных дёрганий

НЕ хранит:
- PNG-байты (Telegram file_id достаточно, байты сожрут RAM)
- Ничего на диск (Render Free ephemeral, бесполезно)

Структура записи:
{
  "kind": "photo" | "media_group" | "text" | "failure",
  "file_id": "...",              # для photo
  "file_ids": [...],             # для media_group
  "meta": {...},                 # title, price, brand, etc
  "failure_reason": "...",       # для failure
  "cached_at": float,
}
"""
import hashlib, time
from cachetools import TTLCache
from loguru import logger
from config import CACHE_SIZE

# --- TTL по типам контента (секунды) ---
# Обычная страница со скриншотом — час. Контент в основном статичный.
TTL_PHOTO = 3600
# Длинная страница, нарезанная на части (медиагруппа) — тоже час.
TTL_MEDIA_GROUP = 3600
# Только текстовая карточка (Cloudflare заблокировал скриншот) — 5 минут.
# Сайт может разблокироваться или метаданные обновиться.
TTL_TEXT_ONLY = 300
# Страницы с ценой/рейтингом — 15 минут, чтобы не показывать устаревшие цены.
TTL_HAS_PRICE = 900
# Негативный кэш — короткий, защита от повторных дёрганий битых URL.
TTL_FAILURE = 180

# Один общий TTLCache с максимальным TTL — фактический TTL контролируется
# через cached_at в самой записи. cachetools TTLCache всё равно нужен для
# ограничения размера (CACHE_SIZE) и автоочистки старых записей.
_store = TTLCache(maxsize=CACHE_SIZE, ttl=TTL_PHOTO)

def _key(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()

def _effective_ttl(entry: dict) -> int:
    """Выбирает TTL по типу записи и наличию цены."""
    kind = entry.get("kind")
    if kind == "failure":
        return TTL_FAILURE
    if kind == "text":
        return TTL_TEXT_ONLY
    # Если в meta есть цена/рейтинг — TTL короче (контент может меняться)
    meta = entry.get("meta") or {}
    if meta.get("price") or meta.get("rating"):
        return TTL_HAS_PRICE
    if kind == "media_group":
        return TTL_MEDIA_GROUP
    return TTL_PHOTO

def get(url: str) -> dict | None:
    """
    Возвращает запись из кэша, если она ещё свежая по своему TTL.
    Учитывает дифференцированный TTL — не зависит от глобального TTL TTLCache.
    """
    entry = _store.get(_key(url))
    if entry is None:
        return None

    age = time.time() - entry.get("cached_at", 0)
    ttl = _effective_ttl(entry)
    if age > ttl:
        # Запись формально в TTLCache, но просрочена по дифференцированному TTL.
        # Удаляем и считаем что нет.
        _store.pop(_key(url), None)
        logger.info(f"CACHE expired url={url} kind={entry.get('kind')} age={age:.0f}s ttl={ttl}s")
        return None

    logger.info(f"CACHE hit url={url} kind={entry.get('kind')} age={age:.0f}s")
    return entry

def save_photo(url: str, file_id: str, meta: dict):
    """Кэшируем одиночное фото с метаданными."""
    _store[_key(url)] = {
        "kind": "photo",
        "file_id": file_id,
        "meta": meta or {},
        "cached_at": time.time(),
    }
    logger.info(f"CACHE save photo url={url}")

def save_media_group(url: str, file_ids: list[str], meta: dict):
    """Кэшируем медиагруппу (несколько частей)."""
    _store[_key(url)] = {
        "kind": "media_group",
        "file_ids": file_ids,
        "meta": meta or {},
        "cached_at": time.time(),
    }
    logger.info(f"CACHE save media_group url={url} parts={len(file_ids)}")

def save_text_only(url: str, meta: dict):
    """Кэшируем случай когда скриншот не получился, только метаданные."""
    _store[_key(url)] = {
        "kind": "text",
        "meta": meta or {},
        "cached_at": time.time(),
    }
    logger.info(f"CACHE save text_only url={url}")

def save_failure(url: str, reason: str):
    """
    Negative cache: запоминаем что URL не работает, чтобы не дёргать его повторно.
    Короткий TTL — даём шанс восстановления.
    """
    _store[_key(url)] = {
        "kind": "failure",
        "failure_reason": reason,
        "cached_at": time.time(),
    }
    logger.info(f"CACHE save failure url={url} reason={reason}")

def stats() -> dict:
    """Для /health endpoint."""
    counts = {"photo": 0, "media_group": 0, "text": 0, "failure": 0}
    for entry in _store.values():
        kind = entry.get("kind", "unknown")
        if kind in counts:
            counts[kind] += 1
    return {
        "size": len(_store),
        "maxsize": CACHE_SIZE,
        **counts,
    }
