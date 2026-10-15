import logging
import os

from aiogram import Router, F, Bot
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import sheets
from handlers.reports import ReportStates

router = Router()
ORGANIZER_CHAT_ID = int(os.environ["ORGANIZER_CHAT_ID"])
log = logging.getLogger(__name__)


def admission_keyboard(row_index: int):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"adm_ok:{row_index}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm_no:{row_index}"),
        InlineKeyboardButton(text="🔁 Запросить ещё раз", callback_data=f"adm_retry:{row_index}"),
    ]])


@router.message(CommandStart())
async def cmd_start(message: Message):
    row, rec = sheets.find_participant_row(message.from_user.id)
    if rec and rec.get("Статус оплаты") == "Оплачено":
        await message.answer(
            f"С возвращением! Твой номер участника: {rec.get('ID участника')}.\n"
            f"Отправляй ежедневные отчёты командой /report."
        )
        return
    if rec and rec.get("Статус оплаты") == "Ожидает проверки":
        await message.answer("Заявка на проверке у организатора. Жди подтверждения.")
        return
    await message.answer(
        "Привет! Это бот сезона DBL.\n\n"
        "Чтобы начать, пришли сюда чек/скриншот оплаты и укажи ФИО, "
        "которое использовал при оплате."
    )


async def _not_in_report_flow(message: Message, state: FSMContext) -> bool:
    current_state = await state.get_state()
    return current_state != ReportStates.waiting_photo.state


@router.message((F.photo | F.document), _not_in_report_flow)
async def handle_payment_proof(message: Message, bot: Bot):
    row, rec = sheets.find_participant_row(message.from_user.id)
    if rec is None:
        row = sheets.add_pending_participant(
            telegram_id=message.from_user.id,
            username=message.from_user.username or message.from_user.full_name,
        )

    caption = (
        f"Новая заявка на допуск\n"
        f"Telegram: @{message.from_user.username or '—'} ({message.from_user.id})\n"
        f"Имя: {message.from_user.full_name}\n"
        f"Строка в таблице: {row}\n\n"
        f"{message.caption or ''}"
    )
    if message.photo:
        await bot.send_photo(
            ORGANIZER_CHAT_ID, message.photo[-1].file_id,
            caption=caption, reply_markup=admission_keyboard(row),
        )
    else:
        await bot.send_document(
            ORGANIZER_CHAT_ID, message.document.file_id,
            caption=caption, reply_markup=admission_keyboard(row),
        )
    await message.answer("Заявка отправлена организатору на проверку. Жди подтверждения.")


@router.callback_query(F.data.startswith("adm_ok:"))
async def admission_ok(callback: CallbackQuery, bot: Bot):
    await callback.answer("Обрабатываю...")
    try:
        row = int(callback.data.split(":")[1])
        rec = sheets.participants_ws.row_values(row)
        telegram_field = rec[1] if len(rec) > 1 else ""
        telegram_id = telegram_field.split(" ")[0]

        season = rec[2] if len(rec) > 2 else "S01"
        participant_id = f"DBL {season}-{str(row).zfill(4)}"
        sheets.set_participant_status(row, "Оплачено", participant_id)

        await bot.send_message(
            int(telegram_id),
            f"✅ Оплата подтверждена! Твой номер участника: {participant_id}.\n"
            f"Отправляй ежедневные отчёты командой /report.",
        )
        await callback.message.edit_caption(
            caption=callback.message.caption + f"\n\n✅ ПОДТВЕРЖДЕНО: {participant_id}"
        )
    except Exception:
        log.exception("Ошибка в admission_ok")
        await callback.message.answer(f"⚠️ Ошибка при обработке: см. логи Railway")


@router.callback_query(F.data.startswith("adm_no:"))
async def admission_no(callback: CallbackQuery, bot: Bot):
    await callback.answer("Обрабатываю...")
    try:
        row = int(callback.data.split(":")[1])
        rec = sheets.participants_ws.row_values(row)
        telegram_field = rec[1] if len(rec) > 1 else ""
        telegram_id = telegram_field.split(" ")[0]

        sheets.set_participant_status(row, "Отклонено")
        await bot.send_message(
            int(telegram_id),
            "❌ Оплата не подтверждена. Свяжись с организатором для уточнения.",
        )
        await callback.message.edit_caption(caption=callback.message.caption + "\n\n❌ ОТКЛОНЕНО")
    except Exception:
        log.exception("Ошибка в admission_no")
        await callback.message.answer("⚠️ Ошибка при обработке: см. логи Railway")


@router.callback_query(F.data.startswith("adm_retry:"))
async def admission_retry(callback: CallbackQuery, bot: Bot):
    await callback.answer("Обрабатываю...")
    try:
        row = int(callback.data.split(":")[1])
        rec = sheets.participants_ws.row_values(row)
        telegram_field = rec[1] if len(rec) > 1 else ""
        telegram_id = telegram_field.split(" ")[0]

        await bot.send_message(
            int(telegram_id),
            "Пришли, пожалуйста, более чёткий чек/скриншот оплаты и ФИО.",
        )
        await callback.message.edit_caption(caption=callback.message.caption + "\n\n🔁 ЗАПРОШЕНО ПОВТОРНО")
    except Exception:
        log.exception("Ошибка в admission_retry")
        await callback.message.answer("⚠️ Ошибка при обработке: см. логи Railway")
