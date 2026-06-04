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
# Топики НЕ перечисляются здесь: один ID покрывает все топики форум-супергруппы.
ALLOWED_GROUP_IDS = _parse_group_ids(
    os.environ.get("ALLOWED_GROUP_IDS", ""),
    os.environ.get("ALLOWED_GROUP_ID", ""),
)

def _parse_disabled_threads(raw: str) -> tuple[frozenset[tuple[int, int]], frozenset[int]]:
    """
    Топики, в которых бот ВЫКЛЮЧЕН (denylist). Формат — пары group:thread через
    запятую/пробел:
        DISABLED_THREADS="-1002638592297:5, -1002638592297:12, -1003972508539:general"
    Привязка к КОНКРЕТНОЙ группе обязательна: номера топиков в разных форумах
    совпадают (свой "топик 5" есть в каждом форуме), поэтому голый номер выключил бы
    топик сразу во всех форумах. Пара group:thread однозначна.
    - group:число   → выключить этот топик в этом форуме;
    - group:general (или :gen / :none) → выключить General этого форума (thread_id = None).
    Пусто = бот работает во ВСЕХ топиках разрешённых групп (старое поведение).
    Битый токен НЕ роняет бот: логируем и пропускаем.
    Denylist безопаснее whitelist: при ошибке бот в худшем случае ОСТАЁТСЯ включённым
    там, где не нужен (видно сразу, легко поправить), а не замолкает везде.
    """
    pairs: set[tuple[int, int]] = set()
    general_chats: set[int] = set()
    for token in (raw or "").replace(",", " ").split():
        if ":" not in token:
            print(f"[config] WARN: пропускаю токен без ':' (нужно group:thread): {token!r}")
            continue
        gid_s, thr_s = token.rsplit(":", 1)
        try:
            gid = int(gid_s)
        except ValueError:
            print(f"[config] WARN: некорректный group id в токене {token!r}")
            continue
        if thr_s.lower() in ("general", "gen", "none"):
            general_chats.add(gid)
            continue
        try:
            pairs.add((gid, int(thr_s)))
        except ValueError:
            print(f"[config] WARN: некорректный thread id в токене {token!r}")
    return frozenset(pairs), frozenset(general_chats)

# Denylist топиков, привязанных к группе. Бот молчит в перечисленных (группа, топик);
# во всех остальных топиках разрешённых групп — работает. Стоит ПОВЕРХ фильтра группы.
DISABLED_THREADS, DISABLED_GENERAL_CHATS = _parse_disabled_threads(
    os.environ.get("DISABLED_THREADS", "")
)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
TIMEOUT_MS = 20_000  # 20 секунд — баланс між швидкістю і надійністю
PAUSE_MS = 3_000
SEMAPHORE = 1 # Обязательно 1, чтобы не уронить Render
CACHE_SIZE = 200
CACHE_TTL = 300
