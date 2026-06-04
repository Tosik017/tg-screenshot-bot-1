import os

BOT_TOKEN = os.environ["BOT_TOKEN"]
PORT = int(os.environ.get("PORT", 8000))

def _parse_group_ids(*raw_values: str) -> frozenset[int]:
    """
    Разбирает ID групп из env. Принимает НЕСКОЛЬКО значений через запятую/пробел:
        ALLOWED_GROUP_IDS="-1001111111111, -1002222222222"
    Битый токен не роняет бот — логируем и пропускаем (валидные остаются).
    """
    ids: set[int] = set()
    for raw in raw_values:
        for token in (raw or "").replace(",", " ").split():
            try:
                ids.add(int(token))
            except ValueError:
                print(f"[config] WARN: пропускаю некорректный group id: {token!r}")
    return frozenset(ids)

# Разрешённые группы. Можно НЕСКОЛЬКО — через запятую или пробел.
# Основная переменная — ALLOWED_GROUP_IDS. Для совместимости со старой настройкой
# читаем и одиночную ALLOWED_GROUP_ID, объединяя в одно множество.
# Пустое множество = ограничение ВЫКЛЮЧЕНО (бот работает везде; забытый env не
# приводит к выходу из целевых групп). ID супергруппы отрицательный (-100...).
# Топики НЕ перечисляются: один ID покрывает все топики форум-супергруппы.
ALLOWED_GROUP_IDS = _parse_group_ids(
    os.environ.get("ALLOWED_GROUP_IDS", ""),
    os.environ.get("ALLOWED_GROUP_ID", ""),
)

def _parse_thread_id(raw: str) -> int:
    """
    ID топика (форум-темы), в котором боту разрешено работать.
    0 = ВЫКЛЮЧЕНО (бот работает во всех топиках разрешённых групп — старое поведение).
    Битое значение НЕ роняет бот: логируем и считаем 0 (выключено).
    Ставится ПОВЕРХ фильтра по группе, не вместо: сначала проверяется группа,
    потом топик. Чужая группа с тем же thread_id всё равно отсекается фильтром группы.
    """
    raw = (raw or "").strip()
    if not raw:
        return 0
    try:
        return int(raw)
    except ValueError:
        print(f"[config] WARN: некорректный ALLOWED_THREAD_ID={raw!r} — игнорирую (0)")
        return 0

# Один разрешённый топик форум-супергруппы. Пусто/0 = ограничение по топику выключено.
# ВНИМАНИЕ: если группа НЕ форумная (топики выключены), у всех сообщений
# message_thread_id = None — при заданном ALLOWED_THREAD_ID бот замолчит везде.
# Поэтому задавать только для форум-групп.
ALLOWED_THREAD_ID = _parse_thread_id(os.environ.get("ALLOWED_THREAD_ID", "0"))

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
TIMEOUT_MS = 20_000  # 20 секунд — баланс між швидкістю і надійністю
PAUSE_MS = 3_000
SEMAPHORE = 1 # Обязательно 1, чтобы не уронить Render
CACHE_SIZE = 200
CACHE_TTL = 300
