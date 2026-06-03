import os

BOT_TOKEN = os.environ["BOT_TOKEN"]
PORT = int(os.environ.get("PORT", 8000))

# ID единственной разрешённой группы. Бот обрабатывает ссылки только здесь и
# автоматически выходит из любого другого чата (см. bot.on_my_chat_member).
# 0 / пусто = ограничение ВЫКЛЮЧЕНО (бот работает везде). Так забытый env не
# приводит к выходу из ЦЕЛЕВОЙ группы — выход включается только когда ID задан.
# ID супергруппы отрицательный, вида -1001234567890.
ALLOWED_GROUP_ID = int(os.environ.get("ALLOWED_GROUP_ID", "0") or "0")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
TIMEOUT_MS = 20_000  # 20 секунд — баланс між швидкістю і надійністю
PAUSE_MS = 3_000
SEMAPHORE = 1 # Обязательно 1, чтобы не уронить Render
CACHE_SIZE = 200
CACHE_TTL = 300
