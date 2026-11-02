import asyncio
import logging
import os
from datetime import date, datetime, timedelta

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton

import sheets

router = Router()
ORGANIZER_CHAT_ID = int(os.environ["ORGANIZER_CHAT_ID"])
log = logging.getLogger(__name__)

ORGANIZER_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🔍 Досмотреть")],
        [KeyboardButton(text="📊 Статистика")],
    ],
    resize_keyboard=True,
)


def _is_organizer(message: Message) -> bool:
    return message.from_user.id == ORGANIZER_CHAT_ID


class BroadcastStates(StatesGroup):
    waiting_text = State()


# ---------- /stats — статистика сезона ----------

@router.message(Command("stats"))
@router.message(F.text == "📊 Статистика")
async def cmd_stats(message: Message):
    if not _is_organizer(message):
        return

    participants = sheets.get_all_participants()
    total_paid = sum(1 for p in participants if p.get("Статус оплаты") == "Оплачено")
    total_pending = sum(1 for p in participants if p.get("Статус оплаты") == "Ожидает проверки")
    total_with_medal = sum(
        1 for p in participants if p.get("Статус оплаты") == "Оплачено" and p.get("Тариф") == "С медалью"
    )
    total_no_medal_paid = sum(
        1 for p in participants if p.get("Статус оплаты") == "Оплачено" and p.get("Тариф") == "Без медали"
    )
    revenue = sum(
        int(p.get("Сумма оплаты, ₽") or 0) for p in participants if p.get("Статус оплаты") == "Оплачено"
    )

    season_day = sheets.get_season_day()
    day_line = f"День сезона: {season_day}/14\n" if season_day else "Сезон ещё не начался\n"

    text = (
        f"📊 <b>Статистика DBL</b>\n\n"
        f"{day_line}"
        f"Оплатили: {total_paid}\n"
        f"  — Без медали: {total_no_medal_paid}\n"
        f"  — С медалью: {total_with_medal}\n"
        f"Ждут проверки оплаты: {total_pending}\n"
        f"Выручка: {revenue}₽\n"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=ORGANIZER_KEYBOARD)


# ---------- /start для организатора — показать клавиатуру ----------

@router.message(Command("menu"))
async def cmd_organizer_menu(message: Message):
    if not _is_organizer(message):
        return
    await message.answer("Меню организатора:", reply_markup=ORGANIZER_KEYBOARD)


# ---------- /broadcast — рассылка всем оплатившим (только по запросу) ----------

@router.message(Command("broadcast"))
async def cmd_broadcast_start(message: Message, state: FSMContext):
    if not _is_organizer(message):
        return
    await message.answer("Пришли текст рассылки — отправлю всем оплатившим участникам.")
    await state.set_state(BroadcastStates.waiting_text)


@router.message(BroadcastStates.waiting_text, F.text)
async def cmd_broadcast_send(message: Message, state: FSMContext, bot: Bot):
    text = message.text
    participants = sheets.get_all_participants()
    sent, failed = 0, 0

    await message.answer("Начинаю рассылку...")
    for p in participants:
        if p.get("Статус оплаты") != "Оплачено":
            continue
        tg_field = str(p.get("Telegram/контакт", ""))
        tg_id = tg_field.split(" ")[0]
        if not tg_id.strip().lstrip("-").isdigit():
            continue
        try:
            await bot.send_message(int(tg_id), text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)  # не упереться в лимиты Telegram

    await message.answer(f"Рассылка завершена. Отправлено: {sent}, не доставлено: {failed}.")
    await state.clear()


# ---------- /find — поиск участника по номеру ----------

@router.message(Command("find"))
async def cmd_find(message: Message):
    if not _is_organizer(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /find DBL S01-0042")
        return
    query = parts[1].strip()

    participants = sheets.get_all_participants()
    for p in participants:
        if str(p.get("ID участника", "")).strip() == query:
            lives = sheets.get_lives(query)
            text = (
                f"👤 <b>{p.get('ID участника')}</b>\n"
                f"Telegram: {p.get('Telegram/контакт') or '—'}\n"
                f"Спорт: {p.get('Спорт') or '—'}\n"
                f"Тариф: {p.get('Тариф') or '—'}\n"
                f"Статус оплаты: {p.get('Статус оплаты') or '—'}\n"
                f"Жизни: {lives}/3\n"
            )
            if p.get("Тариф") == "С медалью":
                text += f"Адрес: {p.get('Адрес получен?') or '—'}\n"
            await message.answer(text, parse_mode="HTML")
            return

    await message.answer("Участник с таким номером не найден.")


# ---------- Напоминание участникам в 20:00, если нет отчёта за сегодня ----------

async def participant_reminder_loop(bot: Bot):
    """Раз в сутки, в 20:00, напоминает каждому оплаченному участнику без отчёта
    за сегодня — нужно успеть прислать до 23:59."""
    while True:
        now = datetime.now()
        target = now.replace(hour=20, minute=0, second=0, microsecond=0)
        if now >= target:
            target = target + timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())

        try:
            season_day = sheets.get_season_day()
            if not season_day:
                continue
            today = date.today().isoformat()
            participants = sheets.get_all_participants()
            paid = [p for p in participants if p.get("Статус оплаты") == "Оплачено"]
            for p in paid:
                pid = p.get("ID участника")
                _, status, _ = sheets.get_today_report_status(pid, today)
                if status is None:
                    tg_field = str(p.get("Telegram/контакт", ""))
                    tg_id = tg_field.split(" ")[0]
                    if not tg_id.strip().lstrip("-").isdigit():
                        continue
                    try:
                        await bot.send_message(
                            int(tg_id),
                            f"⏰ Напоминание: сегодня день {season_day}/14, а отчёта ещё нет.\n"
                            "Успей отправить до 23:59, иначе рискуешь потерять жизнь.",
                        )
                    except Exception:
                        log.exception("Не удалось отправить напоминание участнику %s", pid)
                    await asyncio.sleep(0.05)
        except Exception:
            log.exception("Ошибка в participant_reminder_loop")


# ---------- Напоминание организатору о дедлайне 23:59 ----------

async def deadline_reminder_loop(bot: Bot):
    """Раз в сутки, в 22:00, присылает организатору число участников,
    ещё не отправивших отчёт за сегодня (если сезон идёт)."""
    while True:
        now = datetime.now()
        target = now.replace(hour=22, minute=0, second=0, microsecond=0)
        if now >= target:
            target = target + timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())

        try:
            season_day = sheets.get_season_day()
            if season_day:
                today = date.today().isoformat()
                participants = sheets.get_all_participants()
                paid = [p for p in participants if p.get("Статус оплаты") == "Оплачено"]
                missing = []
                for p in paid:
                    pid = p.get("ID участника")
                    _, status, _ = sheets.get_today_report_status(pid, today)
                    if status is None:
                        missing.append(pid)
                if missing:
                    await bot.send_message(
                        ORGANIZER_CHAT_ID,
                        f"⏰ До дедлайна 2 часа. Без отчёта за сегодня: {len(missing)}\n"
                        + ", ".join(missing[:30]),
                    )
        except Exception:
            log.exception("Ошибка в deadline_reminder_loop")


# ---------- Дневной дайджест в 23:59 ----------

async def daily_digest_loop(bot: Bot):
    while True:
        now = datetime.now()
        target = now.replace(hour=23, minute=59, second=0, microsecond=0)
        if now >= target:
            target = target + timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())

        try:
            season_day = sheets.get_season_day()
            if season_day:
                today = date.today().isoformat()
                participants = sheets.get_all_participants()
                paid = [p for p in participants if p.get("Статус оплаты") == "Оплачено"]
                reports_today = 0
                missing = 0
                for p in paid:
                    pid = p.get("ID участника")
                    _, status, _ = sheets.get_today_report_status(pid, today)
                    if status:
                        reports_today += 1
                    else:
                        missing += 1
                await bot.send_message(
                    ORGANIZER_CHAT_ID,
                    f"🌙 Итоги дня {season_day}/14\n"
                    f"Отчётов получено: {reports_today}\n"
                    f"Без отчёта: {missing}",
                )
        except Exception:
            log.exception("Ошибка в daily_digest_loop")
