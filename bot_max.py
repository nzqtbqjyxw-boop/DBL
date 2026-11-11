"""
Точка входа MAX-бота DBL.

Запуск: python max_bot/bot_max.py
Переменные окружения (в общем .env проекта):
  MAX_BOT_TOKEN      — токен бота MAX (из MasterBot)
  ORGANIZER_CHAT_ID  — уже есть в .env, используем тот же (Telegram ID организатора НЕ подходит!
                        см. примечание в README_MAX.md про ORGANIZER_MAX_ID)
  ORGANIZER_MAX_ID   — числовой user_id организатора В MAX (узнаём на шаге настройки)
  SHEET_ID, GOOGLE_CREDS_JSON — те же, что у Telegram-бота (общий sheets.py)
"""
import asyncio
import logging
import os
import sys

# Чтобы импортировать общий sheets.py из корня проекта (на уровень выше max_bot/)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

import sheets  # noqa: E402
from max_bot.max_api import MaxAPI  # noqa: E402
from max_bot import state  # noqa: E402
from max_bot.handlers import admission_max, reports_max  # noqa: E402

logging.basicConfig(level=logging.INFO, force=True)
log = logging.getLogger(__name__)

ORGANIZER_MAX_ID = int(os.environ.get("ORGANIZER_MAX_ID", "0"))


async def handle_update(api: MaxAPI, update: dict):
    update_type = update.get("update_type")

    try:
        if update_type == "message_created":
            await _handle_message(api, update)
        elif update_type == "message_callback":
            await _handle_callback(api, update)
        elif update_type == "bot_started":
            await _handle_bot_started(api, update)
    except Exception:
        log.exception("Ошибка при обработке апдейта %s", update_type)


async def _handle_bot_started(api: MaxAPI, update: dict):
    user_id = update["user"]["user_id"]
    await admission_max.cmd_start(api, user_id, update["user"], payload=update.get("payload", ""))


async def _handle_message(api: MaxAPI, update: dict):
    message = update["message"]
    user_id = message["sender"]["user_id"]
    body = message.get("body", {})
    text = body.get("text", "") or ""
    attachments = body.get("attachments", [])

    user_state = state.get(user_id)

    # Организатор пишет в личку боту — обрабатывается отдельно (кнопки организатора вынесены в callback).
    if user_id == ORGANIZER_MAX_ID:
        handled = await reports_max.try_handle_organizer_text(api, user_id, text, user_state)
        if handled:
            return

    if text == "/start":
        await admission_max.cmd_start(api, user_id, message["sender"])
        return

    # Фото/документ — либо подтверждение оплаты, либо отчёт о тренировке
    if attachments:
        image_att = next((a for a in attachments if a.get("type") == "image"), None)
        if image_att:
            handled = await admission_max.try_handle_payment_proof(api, user_id, image_att, user_state)
            if handled:
                return
            handled = await reports_max.try_handle_report_photo(api, user_id, image_att, user_state)
            if handled:
                return

    # Текстовые шаги FSM
    if user_state.state == "waiting_km":
        await reports_max.got_km(api, user_id, text, user_state)
        return
    if user_state.state == "waiting_free_phone":
        await admission_max.handle_free_phone_text(api, user_id, text, message["sender"], user_state)
        return
    if user_state.state == "waiting_address":
        await admission_max.handle_address_text(api, user_id, text, user_state)
        return

    # Кнопки главного меню (обычный текст, т.к. в MAX reply-кнопки тоже приходят как текст)
    if text == "📝 Отправить отчёт":
        await reports_max.cmd_report(api, user_id)
        return
    if text == "👤 Профиль":
        await admission_max.cmd_profile(api, user_id)
        return
    if text == "Правила":
        await admission_max.cmd_rules(api, user_id)
        return
    if text == "💳 Подтвердить оплату":
        await admission_max.start_payment_flow(api, user_id, message["sender"], user_state)
        return


async def _handle_callback(api: MaxAPI, update: dict):
    callback = update["callback"]
    callback_id = callback["callback_id"]
    payload = callback.get("payload", "")
    user_id = update["message"]["sender"]["user_id"] if "message" in update else callback["user"]["user_id"]

    if payload.startswith("age:"):
        await admission_max.chosen_age(api, user_id, callback_id, payload)
    elif payload.startswith("sport:"):
        await admission_max.chosen_sport(api, user_id, callback_id, payload)
    elif payload.startswith("tariff:"):
        await admission_max.chosen_tariff(api, user_id, callback_id, payload)
    elif payload == "docs:accept":
        await admission_max.documents_accepted(api, user_id, callback_id)
    elif payload.startswith("adm_ok:"):
        await admission_max.admission_ok(api, callback_id, payload)
    elif payload.startswith("adm_no:"):
        await admission_max.admission_no(api, callback_id, payload)
    elif payload.startswith("rep_ok:"):
        await reports_max.report_ok_start(api, callback_id, payload, user_id)
    elif payload.startswith("rep_no:"):
        await reports_max.report_no(api, callback_id, payload)
    elif payload.startswith("rep_bad:"):
        await reports_max.report_bad(api, callback_id, payload)
    else:
        await api.answer_callback(callback_id, notification="Неизвестное действие")


async def main():
    api = MaxAPI()
    marker = None
    log.info("MAX-бот запущен, начинаю long polling...")
    try:
        while True:
            try:
                result = await api.get_updates(marker, timeout=30)
            except Exception:
                log.exception("Ошибка get_updates, повтор через 5 сек")
                await asyncio.sleep(5)
                continue

            updates = result.get("updates", [])
            for update in updates:
                asyncio.create_task(handle_update(api, update))

            new_marker = result.get("marker")
            if new_marker is not None:
                marker = new_marker
    finally:
        await api.close()


if __name__ == "__main__":
    asyncio.run(main())
