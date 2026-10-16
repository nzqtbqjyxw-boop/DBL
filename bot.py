import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

load_dotenv()

from handlers import admission, reports  # noqa: E402

logging.basicConfig(level=logging.INFO)


async def main():
    bot = Bot(token=os.environ["BOT_TOKEN"])
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(admission.router)
    dp.include_router(reports.router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
