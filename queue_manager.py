"""
Умная очередь для запросов на скриншот.
- Уровень A: лимит глубины + глобальный таймаут на задачу
- Уровень B: видимость позиции в очереди для пользователя
- Уровень C: дедупликация по (chat_id, thread_id, url)
"""
import asyncio
from dataclasses import dataclass, field
from loguru import logger

# --- Конфигурация ---
# Максимум задач в очереди. На переполнении бот отвечает "перегружен".
MAX_QUEUE_SIZE = 10

# Глобальный таймаут на одну задачу.
# 90 сек = 20 (goto) + 3 (pause) + 20 (screenshot) + 47 (запас).
TASK_TIMEOUT_SEC = 90

# Ключ дедупликации: (chat_id, thread_id, url).
# Один и тот же URL в одном и том же чате+топике обрабатывается один раз.
# Тот же URL в другом чате/топике — отдельная задача (каждому адресату свой ответ).
DedupKey = tuple[int, "int | None", str]

@dataclass
class QueueTask:
    """Задача в очереди. Future освобождается воркером с результатом."""
    key: DedupKey
    url: str
    future: asyncio.Future = field(default_factory=asyncio.Future)
    position: int = 0  # позиция на момент enqueue (для UX)

class QueueFull(Exception):
    """Очередь заполнена — бот перегружен."""
    pass

# --- Состояние ---
_queue: asyncio.Queue[QueueTask] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)

# In-flight: dedup-ключ → Future. Второй запрос с тем же ключом подцепляется
# к существующему Future и НЕ создаёт новую задачу. Чистится воркером после задачи.
_inflight: dict[DedupKey, asyncio.Future] = {}

# Воркер — единственный обработчик очереди (соответствует SEMAPHORE=1).
_worker_task: "asyncio.Task | None" = None

# Callback реальной работы (screenshot.shoot). Принимает url → (parts, browser_meta).
_processor = None

def register_processor(processor):
    global _processor
    _processor = processor

async def enqueue(key: DedupKey, url: str) -> tuple[asyncio.Future, int, bool]:
    """
    Ставит (key, url) в очередь. Возвращает (future, position, is_duplicate).
    - is_duplicate=True → этот URL уже обрабатывается для ЭТОГО чата+топика.
      Вызывающий должен молча выйти (НЕ ждать future, НЕ слать второй результат) —
      оригинальный запрос сам пришлёт скриншот в этот чат.
    Бросает QueueFull если очередь заполнена.
    """
    # Уровень C: дедупликация по чату+топику+url.
    if key in _inflight:
        logger.info(f"QUEUE dedup key={key} — already in-flight")
        return _inflight[key], 0, True

    # Уровень A: лимит глубины ДО добавления.
    if _queue.qsize() >= MAX_QUEUE_SIZE:
        logger.warning(f"QUEUE full ({_queue.qsize()}/{MAX_QUEUE_SIZE}) — rejecting url={url}")
        raise QueueFull()

    task = QueueTask(key=key, url=url)
    _inflight[key] = task.future
    position = _queue.qsize() + 1
    task.position = position

    await _queue.put(task)
    logger.info(f"QUEUE enqueued url={url} position={position} qsize={_queue.qsize()}")
    return task.future, position, False

async def _worker():
    """Фоновый воркер: единственный обработчик очереди. Соответствует SEMAPHORE=1."""
    logger.info("Queue worker started")
    while True:
        task = await _queue.get()
        try:
            logger.info(f"QUEUE processing url={task.url} qsize_remaining={_queue.qsize()}")
            try:
                result = await asyncio.wait_for(
                    _processor(task.url),
                    timeout=TASK_TIMEOUT_SEC,
                )
                if not task.future.done():
                    task.future.set_result(result)
            except asyncio.TimeoutError:
                logger.warning(f"QUEUE task timeout url={task.url} after {TASK_TIMEOUT_SEC}s")
                if not task.future.done():
                    task.future.set_result(([], {}))  # пустой результат — клиент покажет fallback
            except Exception as e:
                logger.error(f"QUEUE task failed url={task.url} error={e}")
                if not task.future.done():
                    task.future.set_exception(e)
        finally:
            _inflight.pop(task.key, None)
            _queue.task_done()

def start_worker():
    global _worker_task
    _worker_task = asyncio.create_task(_worker())

def get_stats() -> dict:
    return {
        "queue_size": _queue.qsize(),
        "queue_max": MAX_QUEUE_SIZE,
        "inflight_urls": len(_inflight),
    }
