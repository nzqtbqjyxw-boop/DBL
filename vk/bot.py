import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()

from handlers import admission, reports, organizer  # noqa: E402
from vk.vk_bot import run_vk_bot_async  # noqa: E402

logging.basicConfig(level=logging.INFO, force=True)


async def _healthcheck(request):
    """Простая HTTP-заглушка на порт из переменной PORT (по умолчанию 80).
    Нужна только для того, чтобы Amvera видела "живой" контейнер по
    настроенному containerPort и не перезапускала его циклически —
    сами боты (Telegram/VK) работают через long polling и в HTTP не нуждаются."""
    return web.Response(text="DBL bot is running")


async def _run_healthcheck_server():
    port = int(os.environ.get("PORT", "80"))
    app = web.Application()
    app.router.add_get("/", _healthcheck)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info("Healthcheck HTTP server started on port %s", port)


async def main():
    bot = Bot(token=os.environ["BOT_TOKEN"])
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(admission.router)
    dp.include_router(reports.router)
    dp.include_router(organizer.router)

    asyncio.create_task(organizer.participant_reminder_loop(bot))
    asyncio.create_task(organizer.deadline_reminder_loop(bot))
    asyncio.create_task(organizer.daily_digest_loop(bot))

    # VK-бот запускается параллельно в этом же процессе (его Long Poll
    # блокирующий, поэтому уведён в отдельный поток внутри run_vk_bot_async).
    asyncio.create_task(run_vk_bot_async())

    # HTTP-заглушка для healthcheck Amvera (см. _run_healthcheck_server)
    asyncio.create_task(_run_healthcheck_server())

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
