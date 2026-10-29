import logging
import os
from datetime import date

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import sheets

router = Router()
ORGANIZER_CHAT_ID = int(os.environ["ORGANIZER_CHAT_ID"])
ORGANIZER_USERNAME = "@manchik_luts"
log = logging.getLogger(__name__)


class ReportStates(StatesGroup):
    waiting_km = State()
    waiting_photo = State()


class OrganizerMsgStates(StatesGroup):
    waiting_text = State()


def report_keyboard(report_row: int):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Засчитать", callback_data=f"rep_ok:{report_row}"),
        InlineKeyboardButton(text="❌ Не засчитать", callback_data=f"rep_no:{report_row}"),
    ], [
        InlineKeyboardButton(text="📷 Плохой скриншот", callback_data=f"rep_bad:{report_row}"),
        InlineKeyboardButton(text="⚠️ Подозрение на обман", callback_data=f"rep_susp:{report_row}"),
    ], [
        InlineKeyboardButton(text="✉️ Написать участнику", callback_data=f"rep_msg:{report_row}"),
    ]])


@router.message(Command("report"))
async def cmd_report(message: Message, state: FSMContext):
    row, rec = sheets.find_participant_row(message.from_user.id)
    if not rec or rec.get("Статус оплаты") != "Оплачено":
        await message.answer("Ты ещё не допущен к сезону. Сначала пройди подтверждение оплаты.")
        return

    participant_id = rec.get("ID участника")
    today = date.today().isoformat()
    _, status, attempts = sheets.get_today_report_status(participant_id, today)

    if status == "На проверке":
        await message.answer(
            "⏳ Твой отчёт за сегодня уже на проверке у организатора. "
            "Дождись решения, прежде чем отправлять новый."
        )
        return
    if status in ("Засчитано", "Не засчитано", "Подозрение на обман"):
        await message.answer(
            "Отчёт за сегодня уже обработан. "
            f"Если считаешь, что это ошибка — напиши организатору: {ORGANIZER_USERNAME}"
        )
        return
    if status != "Плохой скриншот" and attempts >= sheets.MAX_ATTEMPTS_PER_DAY:
        await message.answer(
            f"Ты уже использовал все {sheets.MAX_ATTEMPTS_PER_DAY} попытки отправить отчёт за сегодня. "
            f"Напиши организатору: {ORGANIZER_USERNAME}"
        )
        return
    if status == "Плохой скриншот":
        await state.update_data(
            participant_id=participant_id, season=rec.get("Сезон"), sport=rec.get("Спорт"),
            resend_bad=True,
        )
        await message.answer("Пришли более чёткое фото этой же тренировки.")
        await state.set_state(ReportStates.waiting_photo)
        await state.update_data(km=None)  # км не спрашиваем заново
        return

    await state.update_data(participant_id=participant_id, season=rec.get("Сезон"), sport=rec.get("Спорт"))
    await message.answer("Сколько километров сегодня? Пришли число (например 3.2).")
    await state.set_state(ReportStates.waiting_km)


@router.message(ReportStates.waiting_km)
async def got_km(message: Message, state: FSMContext):
    try:
        km = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer("Нужно число, например 3.2. Попробуй ещё раз.")
        return
    await state.update_data(km=km)
    await message.answer("Теперь пришли скриншот/фото тренировки (маршрут, дата, км).")
    await state.set_state(ReportStates.waiting_photo)


@router.message(ReportStates.waiting_photo, F.photo)
async def got_photo(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    file_id = message.photo[-1].file_id
    today = date.today().isoformat()

    if data.get("resend_bad"):
        # Повторная отправка после "Плохой скриншот" — обновляем ту же строку, не создаём новую
        row, _, _ = sheets.get_today_report_status(data["participant_id"], today)
        sent_at, attempt = sheets.update_report_photo_and_status(row, file_id, "На проверке")
        km_display = "—"
    else:
        season_day = sheets.get_season_day()
        day_str = str(season_day) if season_day else ""
        # ← распаковка в три переменные
        row, sent_at, attempt = sheets.add_report(
            participant_id=data["participant_id"],
            season=data["season"],
            day=day_str,
            training_date=today,
            sport=data.get("sport", ""),
            km=data["km"],
            file_id=file_id,
        )
        km_display = data["km"]

    await bot.send_photo(
        ORGANIZER_CHAT_ID,
        file_id,
        caption=(
            f"{'Повторный отчёт (был плохой скриншот)' if data.get('resend_bad') else 'Новый отчёт на проверку'}\n"
            f"Участник: {data['participant_id']}\n"
            f"Дата: {today}\n"
            f"Время отправки: {sent_at}\n"
            f"Попытка: {attempt}/{sheets.MAX_ATTEMPTS_PER_DAY}\n"
            f"Км: {km_display}\n"
            f"Строка: {row}"
        ),
        reply_markup=report_keyboard(row),
    )
    await message.answer(f"Отчёт отправлен на проверку организатору ({sent_at}).")
    await state.clear()
