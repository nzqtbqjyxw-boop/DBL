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
    _, status, _ = sheets.get_today_report_status(participant_id, today)

    if status in ("На проверке", "Засчитано", "Не засчитано", "Подозрение на обман"):
        await message.answer(
            "Отчёт за сегодня уже отправлен и обрабатывается или уже обработан. "
            f"Если считаешь, что это ошибка — напиши организатору: {ORGANIZER_USERNAME}"
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
        sheets.update_report_photo_and_status(row, file_id, "На проверке")
        km_display = "—"
    else:
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
        km_display = data["km"]

    await bot.send_photo(
        ORGANIZER_CHAT_ID,
        file_id,
        caption=(
            f"{'Повторный отчёт (был плохой скриншот)' if data.get('resend_bad') else 'Новый отчёт на проверку'}\n"
            f"Участник: {data['participant_id']}\n"
            f"Дата: {today}\n"
            f"Км: {km_display}\n"
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


@router.callback_query(F.data.startswith("rep_msg:"))
async def report_msg_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    row = int(callback.data.split(":")[1])
    values = sheets.find_report_row(row)
    participant_id = values[0]
    tg_id = _get_participant_telegram_id(participant_id)
    if not tg_id:
        await callback.message.answer("Не нашёл Telegram ID участника.")
        return
    await state.update_data(target_tg_id=tg_id, target_participant_id=participant_id)
    await state.set_state(OrganizerMsgStates.waiting_text)
    await callback.message.answer(f"Пришли текст для {participant_id} — отправлю от своего имени.")


@router.message(OrganizerMsgStates.waiting_text, F.text)
async def report_msg_send(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    try:
        await bot.send_message(int(data["target_tg_id"]), message.text)
        await message.answer("Отправлено.")
    except Exception:
        log.exception("Ошибка отправки личного сообщения участнику")
        await message.answer("⚠️ Не удалось отправить — участник мог заблокировать бота.")
    await state.clear()


@router.callback_query(F.data.startswith("rep_susp:"))
async def report_susp(callback: CallbackQuery, bot: Bot):
    await callback.answer("Обрабатываю...")
    try:
        row = int(callback.data.split(":")[1])
        values = sheets.find_report_row(row)
        participant_id = values[0]
        lives_before = int(values[8]) if len(values) > 8 and values[8] else 3

        sheets.update_report_decision(row, "Подозрение на обман", lives_before, "Требуется ручная проверка организатором")
        await callback.message.edit_caption(caption=callback.message.caption + "\n\n⚠️ НА РУЧНОЙ ПРОВЕРКЕ")

        tg_id = _get_participant_telegram_id(participant_id)
        if tg_id:
            await bot.send_message(
                int(tg_id),
                "⚠️ Организатор отметил твой отчёт как требующий дополнительной проверки.\n\n"
                f"Если ты уверен, что тренировка настоящая — напиши организатору: {ORGANIZER_USERNAME}\n\n"
                "Если до 23:59 сегодняшнего дня вопрос не будет решён, за этот день может быть "
                "снята жизнь, как при незачтённом дне.",
            )
    except Exception:
        log.exception("Ошибка в report_susp")
        await callback.message.answer("⚠️ Ошибка при обработке: см. логи Railway")
