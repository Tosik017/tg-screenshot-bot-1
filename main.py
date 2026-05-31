import asyncio, os, uvicorn
from fastapi import FastAPI
from aiogram import Bot, Dispatcher
from bot import router
import screenshot
from config import BOT_TOKEN, PORT

app = FastAPI()

@app.get("/")
@app.head("/")
async def root():
    return {"ok": True}

@app.get("/ping")
@app.head("/ping")
async def ping():
    return {"ok": True}

async def main():
    await screenshot.init()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    server = uvicorn.Server(
        uvicorn.Config(app, host="0.0.0.0", port=PORT)
    )
    await asyncio.gather(
        dp.start_polling(bot),
        server.serve()
    )

if __name__ == "__main__":
    asyncio.run(main())
