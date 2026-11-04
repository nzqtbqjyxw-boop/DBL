import logging
import os
from datetime import date

from aiogram import Router, F, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
)

import sheets


async def _safe_edit_caption(message, new_caption: str):
    """Добавляет текст к подписи, но не падает, если Telegram говорит,
    что содержимое не изменилось (повторное нажатие той же кнопки)."""
    try:
        await message.edit_caption(caption=new_caption)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise

router = Router()
ORGANIZER_CHAT_ID = int(os.environ["ORGANIZER_CHAT_ID"])
ORGANIZER_USERNAME = "@manchik_luts"
log = logging.getLogger(__name__)

# Клавиатура участника — та же, что в admission.py (MAIN_KEYBOARD). Продублирована
# здесь локально, чтобы не создавать циклический импорт между модулями.
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📝 Отправить отчёт")],
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="Правила")],
        [KeyboardButton(text="🆘 Написать организатору")],
    ],
    resize_keyboard=True,
)


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


def cancel_msg_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data="rep_msg_cancel"),
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
    if status == "Засчитано":
        await message.answer(
            "Отчёт за сегодня уже засчитан. "
            f"Если считаешь, что это ошибка — напиши организатору: {ORGANIZER_USERNAME}"
        )
        return
    if status in ("Не засчитано", "Подозрение на обман", "Плохой скриншот"):
        # Дополнительный скриншот к уже существующему отчёту за сегодня —
        # создаём НОВУЮ строку (не затираем предыдущую), организатор увидит оба.
        await state.update_data(
            participant_id=participant_id, season=rec.get("Сезон"), sport=rec.get("Спорт"),
            extra_attempt=True,
        )
        await message.answer("Пришли ещё один скриншот этой же тренировки.")
        await state.set_state(ReportStates.waiting_photo)
        await state.update_data(km=None)
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
    is_extra = bool(data.get("extra_attempt"))

    season_day = sheets.get_season_day()
    day_str = str(season_day) if season_day else ""
    km_value = data.get("km")
    row, sent_at, attempt = sheets.add_report(
        participant_id=data["participant_id"],
        season=data["season"],
        day=day_str,
        training_date=today,
        sport=data.get("sport", ""),
        km=km_value if km_value is not None else "",
        file_id=file_id,
    )
    km_display = km_value if km_value is not None else "—"

    await bot.send_photo(
        ORGANIZER_CHAT_ID,
        file_id,
        caption=(
            f"{f'Это {attempt} скриншот за сегодня' if is_extra else 'Новый отчёт на проверку'}\n"
            f"Участник: {data['participant_id']}\n"
            f"Дата: {today}\n"
            f"Время отправки: {sent_at}\n"
            f"Попытка: {attempt}\n"
            f"Км: {km_display}\n"
            f"Строка: {row}"
        ),
        reply_markup=report_keyboard(row),
    )
    await message.answer(f"Отчёт отправлен на проверку организатору ({sent_at}).")
    await state.clear()


def _get_participant_telegram_id(participant_id: str):
    records = sheets.get_all_participants()
    for rec in records:
        if str(rec.get("ID участника")) == str(participant_id):
            return str(rec.get("Telegram/контакт", "")).split(" ")[0]
    return None


@router.message(Command("pending"))
@router.message(F.text == "🔍 Досмотреть")
async def cmd_pending(message: Message, bot: Bot):
    """Организатор: показать все отчёты со статусом 'Подозрение на обман',
    требующие ручного решения, с рабочими кнопками заново."""
    if message.from_user.id != ORGANIZER_CHAT_ID:
        return

    records = sheets.reports_ws.get_all_records()
    pending_rows = []
    for i, rec in enumerate(records, start=2):
        if rec.get("Статус") == "Подозрение на обман":
            pending_rows.append((i, rec))

    if not pending_rows:
        await message.answer("Нечего досматривать — нет отчётов со статусом «Подозрение на обман».")
        return

    await message.answer(f"Отчётов на досмотр: {len(pending_rows)}")
    for row_idx, rec in pending_rows:
        file_id = rec.get("Скриншот (file_id)")
        if not file_id:
            continue
        await bot.send_photo(
            ORGANIZER_CHAT_ID,
            file_id,
            caption=(
                f"⚠️ На досмотре\n"
                f"Участник: {rec.get('ID участника')}\n"
                f"Дата: {rec.get('Дата тренировки')}\n"
                f"Время отправки: {rec.get('Время отправки')}\n"
                f"Км: {rec.get('Км') or '—'}\n"
                f"Строка: {row_idx}"
            ),
            reply_markup=report_keyboard(row_idx),
        )


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
        await _safe_edit_caption(callback.message, callback.message.caption + "\n\n✅ ЗАСЧИТАНО")
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
                "Можешь прислать ещё один скриншот через «📝 Отправить отчёт» — "
                f"или написать организатору: {ORGANIZER_USERNAME}",
            )
        await _safe_edit_caption(callback.message, callback.message.caption + f"\n\n❌ НЕ ЗАСЧИТАНО (жизни: {lives_after})")
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
                "📷 Скриншот плохого качества — пришли, пожалуйста, более чёткое фото этой же "
                "тренировки через «📝 Отправить отчёт».",
            )
        await _safe_edit_caption(callback.message, callback.message.caption + "\n\n📷 ПЛОХОЙ СКРИНШОТ")
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
    await callback.message.answer(
        f"Пришли текст для {participant_id} — отправлю от своего имени.",
        reply_markup=cancel_msg_keyboard(),
    )


@router.callback_query(OrganizerMsgStates.waiting_text, F.data == "rep_msg_cancel")
async def report_msg_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("Отменено")
    await callback.message.edit_text("Отменено, сообщение участнику не отправлено.")


@router.message(OrganizerMsgStates.waiting_text, Command("cancel"))
async def report_msg_cancel_cmd(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено, сообщение участнику не отправлено.")


@router.message(OrganizerMsgStates.waiting_text, F.text)
async def report_msg_send(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    try:
        await bot.send_message(
            int(data["target_tg_id"]),
            f"✉️ Сообщение от организатора:\n\n{message.text}",
            reply_markup=MAIN_KEYBOARD,
        )
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
        await _safe_edit_caption(callback.message, callback.message.caption + "\n\n⚠️ НА РУЧНОЙ ПРОВЕРКЕ")

        tg_id = _get_participant_telegram_id(participant_id)
        if tg_id:
            await bot.send_message(
                int(tg_id),
                "⚠️ Организатор отметил твой отчёт как требующий дополнительной проверки.\n\n"
                "Ничего делать не нужно — организатор примет решение по этому скриншоту сам. "
                f"Если хочешь что-то пояснить — напиши организатору: {ORGANIZER_USERNAME}\n\n"
                "Если до 23:59 сегодняшнего дня вопрос не будет решён, за этот день может быть "
                "снята жизнь, как при незачтённом дне.",
            )
    except Exception:
        log.exception("Ошибка в report_susp")
        await callback.message.answer("⚠️ Ошибка при обработке: см. логи Railway")
