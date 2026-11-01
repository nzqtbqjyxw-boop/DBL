import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

load_dotenv()

from handlers import admission, reports, organizer  # noqa: E402

logging.basicConfig(level=logging.INFO, force=True)


async def main():
    bot = Bot(token=os.environ["BOT_TOKEN"])
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(admission.router)
    dp.include_router(reports.router)
    dp.include_router(organizer.router)

    asyncio.create_task(organizer.participant_reminder_loop(bot))
    asyncio.create_task(organizer.deadline_reminder_loop(bot))
    asyncio.create_task(organizer.daily_digest_loop(bot))

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
