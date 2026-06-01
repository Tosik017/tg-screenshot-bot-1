"""
Умная очередь для запросов на скриншот.
- Уровень A: лимит глубины + глобальный таймаут на задачу
- Уровень B: видимость позиции в очереди для пользователя
- Уровень C: in-flight дедупликация одинаковых URL
"""
import asyncio
from dataclasses import dataclass, field
from loguru import logger

# --- Конфигурация ---
# Максимум задач в очереди. На 12-м запросе бот отвечает "перегружен" вместо
# вставания в очередь. Защита от лавины апдейтов после рестарта или флуда.
MAX_QUEUE_SIZE = 10

# Глобальный таймаут на одну задачу: Playwright TIMEOUT_MS=20000 — внутренний
# таймаут навигации, но скриншот после navigation может зависнуть.
# 90 сек = 20 (goto) + 3 (pause) + 20 (screenshot) + 47 (запас).
TASK_TIMEOUT_SEC = 90

@dataclass
class QueueTask:
    """Задача в очереди. Future освобождается воркером с результатом."""
    url: str
    future: asyncio.Future = field(default_factory=asyncio.Future)
    position: int = 0  # обновляется воркером перед стартом обработки

class QueueFull(Exception):
    """Очередь заполнена — бот перегружен."""
    pass

# --- Состояние ---
# Очередь активных задач (FIFO). asyncio.Queue для естественного await.
_queue: asyncio.Queue[QueueTask] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)

# In-flight cache: url → Future. Если URL уже обрабатывается,
# второй запрос подцепляется к существующему Future вместо новой задачи.
# Очищается ПОСЛЕ освобождения Future.
_inflight: dict[str, asyncio.Future] = {}

# Воркер — единственный обработчик очереди (соответствует SEMAPHORE=1).
_worker_task: asyncio.Task | None = None

# Callback для обработки задачи. Регистрируется из main.py.
# Принимает url, возвращает (parts: list[bytes], browser_meta: dict).
_processor = None

def register_processor(processor):
    """Регистрация функции которая делает реальную работу (screenshot.shoot)."""
    global _processor
    _processor = processor

async def enqueue(url: str) -> tuple[asyncio.Future, int, bool]:
    """
    Ставит URL в очередь. Возвращает (future, position, is_duplicate).
    - future: ожидаемый результат
    - position: позиция в очереди на момент enqueue (для UX)
    - is_duplicate: True если URL уже обрабатывается (дедупликация)

    Бросает QueueFull если очередь заполнена.
    """
    # Уровень C: дедупликация. Если URL уже обрабатывается — отдаём тот же Future.
    if url in _inflight:
        logger.info(f"QUEUE dedup url={url} — attaching to in-flight task")
        return _inflight[url], 0, True

    # Уровень A: проверка лимита глубины ДО добавления.
    if _queue.qsize() >= MAX_QUEUE_SIZE:
        logger.warning(f"QUEUE full ({_queue.qsize()}/{MAX_QUEUE_SIZE}) — rejecting url={url}")
        raise QueueFull()

    task = QueueTask(url=url)
    _inflight[url] = task.future

    # Позиция = текущий размер очереди до добавления + 1 (учёт активной задачи воркера если есть)
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

            # Уровень A: глобальный таймаут на задачу.
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
            # Очищаем in-flight — следующий запрос на этот URL создаст новую задачу
            _inflight.pop(task.url, None)
            _queue.task_done()

def start_worker():
    """Запускает фоновый воркер. Вызывается из main.py после init."""
    global _worker_task
    _worker_task = asyncio.create_task(_worker())

def get_stats() -> dict:
    """Для /health и отладки."""
    return {
        "queue_size": _queue.qsize(),
        "queue_max": MAX_QUEUE_SIZE,
        "inflight_urls": len(_inflight),
    }
