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


def report_keyboard(report_row: int):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Засчитать", callback_data=f"rep_ok:{report_row}"),
        InlineKeyboardButton(text="❌ Не засчитать", callback_data=f"rep_no:{report_row}"),
    ], [
        InlineKeyboardButton(text="📷 Плохой скриншот", callback_data=f"rep_bad:{report_row}"),
        InlineKeyboardButton(text="⚠️ Подозрение на обман", callback_data=f"rep_susp:{report_row}"),
    ]])


@router.message(Command("report"))
async def cmd_report(message: Message, state: FSMContext):
    row, rec = sheets.find_participant_row(message.from_user.id)
    if not rec or rec.get("Статус оплаты") != "Оплачено":
        await message.answer("Ты ещё не допущен к сезону. Сначала пройди подтверждение оплаты.")
        return
    await state.update_data(participant_id=rec.get("ID участника"), season=rec.get("Сезон"), sport=rec.get("Спорт"))
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
    season_day = sheets.get_season_day()
    day_str = str(season_day) if season_day else ""

    row = sheets.add_report(
        participant_id=data["participant_id"],
        season=data["season"],
        day=day_str,
        training_date=today,
        sport=data.get("sport", ""),
        km=data["km"],
        file_id=file_id,
    )

    await bot.send_photo(
        ORGANIZER_CHAT_ID,
        file_id,
        caption=(
            f"Новый отчёт на проверку\n"
            f"Участник: {data['participant_id']}\n"
            f"Дата: {today}\n"
            f"Км: {data['km']}\n"
            f"Строка: {row}"
        ),
        reply_markup=report_keyboard(row),
    )
    await message.answer("Отчёт отправлен на проверку организатору.")
    await state.clear()


def _get_participant_telegram_id(participant_id: str):
    records = sheets.get_all_participants()
    for rec in records:
        if str(rec.get("ID участника")) == str(participant_id):
            return str(rec.get("Telegram/контакт", "")).split(" ")[0]
    return None


@router.callback_query(F.data.startswith("rep_ok:"))
async def report_ok(callback: CallbackQuery, bot: Bot):
    await callback.answer("Обрабатываю...")
    try:
        row = int(callback.data.split(":")[1])
        values = sheets.find_report_row(row)
        participant_id = values[0]
        lives_before = int(values[8]) if len(values) > 8 and values[8] else 3
        lives_after = lives_before

        sheets.update_report_decision(row, "Засчитано", lives_after)

        tg_id = _get_participant_telegram_id(participant_id)
        if tg_id:
            await bot.send_message(int(tg_id), "✅ Твой отчёт засчитан!")
        await callback.message.edit_caption(caption=callback.message.caption + "\n\n✅ ЗАСЧИТАНО")
    except Exception:
        log.exception("Ошибка в report_ok")
        await callback.message.answer("⚠️ Ошибка при обработке: см. логи Railway")


@router.callback_query(F.data.startswith("rep_no:"))
async def report_no(callback: CallbackQuery, bot: Bot):
    await callback.answer("Обрабатываю...")
    try:
        row = int(callback.data.split(":")[1])
        values = sheets.find_report_row(row)
        participant_id = values[0]
        lives_before = int(values[8]) if len(values) > 8 and values[8] else 3
        lives_after = max(lives_before - 1, 0)

        sheets.update_report_decision(row, "Не засчитано", lives_after)

        tg_id = _get_participant_telegram_id(participant_id)
        if tg_id:
            await bot.send_message(
                int(tg_id),
                f"❌ Отчёт не засчитан. Осталось жизней: {lives_after}.\n"
                f"Если считаешь, что это ошибка — напиши организатору лично: {ORGANIZER_USERNAME}",
            )
        await callback.message.edit_caption(caption=callback.message.caption + f"\n\n❌ НЕ ЗАСЧИТАНО (жизни: {lives_after})")
    except Exception:
        log.exception("Ошибка в report_no")
        await callback.message.answer("⚠️ Ошибка при обработке: см. логи Railway")


@router.callback_query(F.data.startswith("rep_bad:"))
async def report_bad(callback: CallbackQuery, bot: Bot):
    await callback.answer("Обрабатываю...")
    try:
        row = int(callback.data.split(":")[1])
        values = sheets.find_report_row(row)
        participant_id = values[0]
        lives_before = int(values[8]) if len(values) > 8 and values[8] else 3

        sheets.update_report_decision(row, "Плохой скриншот", lives_before, "Запрошена повторная отправка")

        tg_id = _get_participant_telegram_id(participant_id)
        if tg_id:
            await bot.send_message(
                int(tg_id),
                "📷 Скриншот плохого качества — пришли, пожалуйста, более чёткое фото этой же тренировки через /report.",
            )
        await callback.message.edit_caption(caption=callback.message.caption + "\n\n📷 ПЛОХОЙ СКРИНШОТ")
    except Exception:
        log.exception("Ошибка в report_bad")
        await callback.message.answer("⚠️ Ошибка при обработке: см. логи Railway")


@router.callback_query(F.data.startswith("rep_susp:"))
async def report_susp(callback: CallbackQuery, bot: Bot):
    await callback.answer("Обрабатываю...")
    try:
        row = int(callback.data.split(":")[1])
        values = sheets.find_report_row(row)
        lives_before = int(values[8]) if len(values) > 8 and values[8] else 3

        sheets.update_report_decision(row, "Подозрение на обман", lives_before, "Требуется ручная проверка организатором")
        await callback.message.edit_caption(caption=callback.message.caption + "\n\n⚠️ НА РУЧНОЙ ПРОВЕРКЕ")
    except Exception:
        log.exception("Ошибка в report_susp")
        await callback.message.answer("⚠️ Ошибка при обработке: см. логи Railway")
