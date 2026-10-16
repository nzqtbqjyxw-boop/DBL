import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()

from handlers import admission, reports  # noqa: E402
from webhook import create_app  # noqa: E402

logging.basicConfig(level=logging.INFO)


async def run_webhook_server():
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Webhook-сервер запущен на порту {port}")


async def main():
    bot = Bot(token=os.environ["BOT_TOKEN"])
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(admission.router)
    dp.include_router(reports.router)

    await run_webhook_server()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
