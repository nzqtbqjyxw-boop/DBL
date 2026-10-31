import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

load_dotenv()

from handlers import admission, reports, organizer  # noqa: E402

logging.basicConfig(level=logging.WARNING, force=True)
log = logging.getLogger("bot")
log.setLevel(logging.WARNING)


async def _log_every_update(handler, event, data):
    """ВРЕМЕННЫЙ диагностический middleware."""
    print("###RAW_UPDATE_MARKER###", flush=True)
    log.warning("###RAW_UPDATE_MARKER_LOG###")
    try:
        upd_type = event.event_type if hasattr(event, "event_type") else "?"
        print(f"###UPDATE_TYPE={upd_type}###", flush=True)
    except Exception as e:
        print(f"###UPDATE_TYPE_ERROR={e}###", flush=True)
    return await handler(event, data)


async def main():
    bot = Bot(token=os.environ["BOT_TOKEN"])
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(_log_every_update)
    dp.include_router(admission.router)
    dp.include_router(reports.router)
    dp.include_router(organizer.router)

    asyncio.create_task(organizer.participant_reminder_loop(bot))
    asyncio.create_task(organizer.deadline_reminder_loop(bot))
    asyncio.create_task(organizer.daily_digest_loop(bot))

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
