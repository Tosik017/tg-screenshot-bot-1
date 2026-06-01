import asyncio, os, uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from aiogram import Bot, Dispatcher
from bot import router
import screenshot, blacklist
from config import BOT_TOKEN, PORT

app = FastAPI()

_bot: Bot | None = None

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
    browser_ok = screenshot._browser is not None
    blacklist_ok = len(blacklist._blacklisted) > 0

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
            "blacklist_domains": len(blacklist._blacklisted),
            "blacklist_ok": blacklist_ok,
        }
    )

async def main():
    global _bot

    await screenshot.init()

    # Загружаем фиды до старта polling — бот не обрабатывает запросы без blacklist
    await blacklist.update()
    blacklist.start_background_updater()

    _bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

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
