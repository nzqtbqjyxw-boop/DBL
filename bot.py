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
    """ВРЕМЕННЫЙ диагностический middleware — логирует ЛЮБОЙ Update целиком
    (до любых роутеров/фильтров), чтобы понять, что реально приходит
    в апдейтах, которые aiogram помечает как 'not handled'."""
    try:
        log.warning("RAW UPDATE IN: %s", event.model_dump_json(exclude_none=True)[:2000])
    except Exception:
        log.exception("Ошибка логирования RAW UPDATE")
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
