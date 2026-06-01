import asyncio, os, uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from aiogram import Bot, Dispatcher
from bot import router
import screenshot
from config import BOT_TOKEN, PORT

app = FastAPI()

_bot: Bot | None = None  # держим ссылку для проверки в /health

@app.get("/")
@app.head("/")
async def root():
    return {"ok": True}

@app.get("/ping")
@app.head("/ping")
async def ping():
    return {"ok": True}

@app.get("/health")
async def health():
    """
    Реальная проверка состояния сервиса:
    - browser: Playwright запущен и browser-объект существует
    - bot: удалось получить getMe от Telegram API
    HTTP 200 = всё ок, HTTP 503 = сервис нездоров.
    """
    browser_ok = screenshot._browser is not None

    bot_ok = False
    if _bot is not None:
        try:
            await asyncio.wait_for(_bot.get_me(), timeout=5)
            bot_ok = True
        except Exception:
            bot_ok = False

    status = "ok" if (browser_ok and bot_ok) else "degraded"
    code = 200 if status == "ok" else 503

    return JSONResponse(
        status_code=code,
        content={
            "status": status,
            "browser": browser_ok,
            "bot": bot_ok,
        }
    )

async def main():
    global _bot

    await screenshot.init()

    _bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    # Сброс накопленной очереди апдейтов ДО старта polling.
    # ВАЖНО: drop_pending_updates нельзя передавать в start_polling — в aiogram 3.x
    # этот kwarg уходит в workflow_data и молча игнорируется (no-op).
    # Рабочий способ — отдельный вызов delete_webhook.
    await _bot.delete_webhook(drop_pending_updates=True)

    server = uvicorn.Server(
        uvicorn.Config(app, host="0.0.0.0", port=PORT)
    )
    await asyncio.gather(
        dp.start_polling(_bot),
        server.serve()
    )

if __name__ == "__main__":
    asyncio.run(main())
