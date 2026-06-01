import asyncio, signal, uvicorn
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

async def shutdown(dp: Dispatcher, server: uvicorn.Server):
    """
    Graceful shutdown при SIGTERM (Render останавливает контейнер при деплое).
    Порядок важен:
    1. Останавливаем polling — новые сообщения не принимаем
    2. Ждём семафор — текущий скриншот должен завершиться
    3. Закрываем браузер чисто — без zombie Chromium
    4. Останавливаем HTTP-сервер
    """
    from loguru import logger
    logger.info("SIGTERM received — starting graceful shutdown")

    # 1. Останавливаем aiogram polling
    await dp.stop_polling()
    logger.info("Polling stopped")

    # 2. Ждём освобождения семафора — текущий скриншот завершается
    # Таймаут 60 сек — максимальное время одного скриншота
    try:
        await asyncio.wait_for(screenshot.semaphore.acquire(), timeout=60)
        screenshot.semaphore.release()
        logger.info("Semaphore free — no active screenshot")
    except asyncio.TimeoutError:
        logger.warning("Semaphore timeout — forcing shutdown anyway")

    # 3. Закрываем браузер
    if screenshot._browser is not None:
        try:
            await screenshot._browser.close()
            logger.info("Browser closed cleanly")
        except Exception as e:
            logger.warning(f"Browser close error: {e}")

    if screenshot._pw is not None:
        try:
            await screenshot._pw.stop()
            logger.info("Playwright stopped")
        except Exception as e:
            logger.warning(f"Playwright stop error: {e}")

    # 4. Останавливаем uvicorn
    server.should_exit = True
    logger.info("Shutdown complete")

async def main():
    global _bot

    await screenshot.init()

    await blacklist.update()
    blacklist.start_background_updater()

    _bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    await _bot.delete_webhook(drop_pending_updates=True)

    server = uvicorn.Server(
        uvicorn.Config(app, host="0.0.0.0", port=PORT)
    )

    # Регистрируем SIGTERM handler — Render шлёт его при остановке контейнера
    loop = asyncio.get_event_loop()
    loop.add_signal_handler(
        signal.SIGTERM,
        lambda: asyncio.create_task(shutdown(dp, server))
    )

    await asyncio.gather(
        dp.start_polling(_bot),
        server.serve()
    )

if __name__ == "__main__":
    asyncio.run(main())
