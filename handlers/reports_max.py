"""
Отчёты о тренировках — MAX-версия (аналог handlers/reports.py Telegram-бота).
"""
import logging
import os
from datetime import date

import sheets
from max_bot import state as st

log = logging.getLogger(__name__)

ORGANIZER_MAX_ID = int(os.environ.get("ORGANIZER_MAX_ID", "0"))
ORGANIZER_USERNAME = "@manchik_luts"


def _report_keyboard(report_row: int):
    return {"type": "inline_keyboard", "payload": {"buttons": [
        [{"type": "callback", "text": "✅ Засчитать", "payload": f"rep_ok:{report_row}"},
         {"type": "callback", "text": "❌ Не засчитать", "payload": f"rep_no:{report_row}"}],
        [{"type": "callback", "text": "📷 Плохой скриншот", "payload": f"rep_bad:{report_row}"}],
    ]}}


async def cmd_report(api, user_id: int):
    row, rec = sheets.find_participant_row(user_id)
    if not rec or rec.get("Статус оплаты") != "Оплачено":
        await api.send_message(user_id=user_id, text="Ты ещё не допущен к сезону.")
        return

    participant_id = rec.get("ID участника")
    today = date.today().isoformat()
    _, status, _ = sheets.get_today_report_status(participant_id, today)

    if status == "На проверке":
        await api.send_message(user_id=user_id, text="⏳ Твой отчёт за сегодня уже на проверке у организатора.")
        return
    if status == "Засчитано":
        await api.send_message(user_id=user_id, text="Отчёт за сегодня уже засчитан.")
        return

    st.update_data(user_id, participant_id=participant_id, season=rec.get("Сезон"), sport=rec.get("Спорт"))
    st.set_state(user_id, "waiting_km")
    await api.send_message(user_id=user_id, text="Сколько километров сегодня? Пришли число (например 3.2).")


async def got_km(api, user_id: int, text: str, user_state):
    try:
        km = float(text.replace(",", "."))
    except ValueError:
        await api.send_message(user_id=user_id, text="Нужно число, например 3.2. Попробуй ещё раз.")
        return
    st.update_data(user_id, km=km)
    st.set_state(user_id, "waiting_photo")
    await api.send_message(user_id=user_id, text="Теперь пришли фото тренировки (маршрут, дата, км).")


async def try_handle_report_photo(api, user_id: int, image_att: dict, user_state) -> bool:
    if user_state.state != "waiting_photo":
        return False

    data = user_state.data
    today = date.today().isoformat()
    season_day = sheets.get_season_day()
    day_str = str(season_day) if season_day else ""
    token = image_att["payload"]["token"]

    row, sent_at, attempt = sheets.add_report(
        participant_id=data["participant_id"], season=data.get("season", "S01"),
        day=day_str, training_date=today, sport=data.get("sport", ""),
        km=data.get("km", ""), file_id=f"max:{token}",
    )

    if ORGANIZER_MAX_ID:
        await api.send_message(
            user_id=ORGANIZER_MAX_ID,
            text=(f"Новый отчёт на проверку (MAX)\n"
                  f"Участник: {data['participant_id']}\nДата: {today}\n"
                  f"Км (заявлено): {data.get('km', '—')}\nСтрока: {row}"),
            attachments=[{"type": "image", "payload": {"token": token}}, _report_keyboard(row)],
        )
    await api.send_message(user_id=user_id, text=f"Отчёт отправлен на проверку организатору ({sent_at}).")
    st.clear(user_id)
    return True


def _get_participant_user_id(participant_id: str):
    records = sheets.get_all_participants()
    for rec in records:
        if str(rec.get("ID участника")) == str(participant_id):
            try:
                return int(str(rec.get("Telegram/контакт", "")).split(" ")[0])
            except ValueError:
                return None
    return None


async def report_ok_start(api, callback_id: str, payload: str, organizer_user_id: int):
    """Упрощённая версия (v1): сразу засчитываем без запроса подтверждённых км/времени
    (это отличие от Telegram-бота — добавим пошаговый запрос вторым этапом)."""
    await api.answer_callback(callback_id, text="Обрабатываю...")
    row = int(payload.split(":")[1])
    values = sheets.find_report_row(row)
    participant_id = values[0]
    lives_before = int(values[8]) if len(values) > 8 and values[8] else 3

    sheets.update_report_decision(row, "Засчитано", lives_before)

    participant_user_id = _get_participant_user_id(participant_id)
    if participant_user_id:
        await api.send_message(user_id=participant_user_id, text="✅ Твой отчёт засчитан!")
    await api.send_message(user_id=organizer_user_id, text=f"✅ ЗАСЧИТАНО (строка {row})")


async def report_no(api, callback_id: str, payload: str):
    await api.answer_callback(callback_id, text="Обрабатываю...")
    row = int(payload.split(":")[1])
    values = sheets.find_report_row(row)
    participant_id = values[0]
    lives_before = int(values[8]) if len(values) > 8 and values[8] else 3
    lives_after = max(lives_before - 1, 0)

    sheets.update_report_decision(row, "Не засчитано", lives_after)

    participant_user_id = _get_participant_user_id(participant_id)
    if participant_user_id:
        await api.send_message(
            user_id=participant_user_id,
            text=f"❌ Отчёт не засчитан. Осталось жизней: {lives_after}.\n"
                 f"Можешь прислать ещё один скриншот через «Отправить отчёт», или написать организатору: {ORGANIZER_USERNAME}",
        )


async def report_bad(api, callback_id: str, payload: str):
    await api.answer_callback(callback_id, text="Обрабатываю...")
    row = int(payload.split(":")[1])
    values = sheets.find_report_row(row)
    participant_id = values[0]
    lives_before = int(values[8]) if len(values) > 8 and values[8] else 3

    sheets.update_report_decision(row, "Плохой скриншот", lives_before, "Запрошена повторная отправка")

    participant_user_id = _get_participant_user_id(participant_id)
    if participant_user_id:
        await api.send_message(user_id=participant_user_id,
                                text="📷 Скриншот плохого качества — пришли более чёткое фото этой же тренировки.")
        st.set_state(participant_user_id, "waiting_photo")


async def try_handle_organizer_text(api, user_id: int, text: str, user_state) -> bool:
    """Заглушка для будущих текстовых команд организатора в MAX (например /stats).
    Пока ничего не обрабатывает — возвращает False, чтобы остальной код шёл дальше."""
    return False
