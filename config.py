import os

BOT_TOKEN = os.environ["BOT_TOKEN"]
PORT = int(os.environ.get("PORT", 8000))

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
TIMEOUT_MS = 15_000 # Увеличили для тяжелых сайтов
PAUSE_MS = 3_000
SEMAPHORE = 1 # Обязательно 1, чтобы не уронить Render
CACHE_SIZE = 200
CACHE_TTL = 300
