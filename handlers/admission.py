import logging
import os
from aiogram import Router, F, Bot
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
)
import sheets
from handlers.reports import ReportStates, OrganizerMsgStates, _safe_edit_caption
router = Router()
ORGANIZER_CHAT_ID = int(os.environ["ORGANIZER_CHAT_ID"])
ORGANIZER_USERNAME = "@manchik_luts"
log = logging.getLogger(__name__)
PRICE_NO_MEDAL = 500
PRICE_WITH_MEDAL = 1490
PAYMENT_LINK_NO_MEDAL = "https://yookassa.ru/my/i/atKmjoB8TKJB/l"
PAYMENT_LINK_WITH_MEDAL = "https://yookassa.ru/my/i/auhWxZqv8jGa/l"
CHANNEL_LINK = "https://t.me/DBL_Better_life"
GROUP_LINK = "https://t.me/+sK53VEAlDwo3YzVi"

OFFER_LINK = "https://telegra.ph/PUBLICHNAYA-OFERTA-11-03-27"
PRIVACY_POLICY_LINK = "https://telegra.ph/POLITIKA-OBRABOTKI-PERSONALNYH-DANNYH-DBL-11-03"
PD_CONSENT_LINK = "https://telegra.ph/SOGLASIE-NA-OBRABOTKU-PERSONALNYH-DANNYH-DBL-11-03"
PARENT_CONSENT_LINK = "https://telegra.ph/SOGLASIE-ZAKONNOGO-PREDSTAVITELYA-NA-UCHASTIE-NESOVERSHENNOLETNEGO-V-PROEKTE-DBL-11-03"

# Простая защита от повторной обработки одного и того же нажатия кнопки:
# если Railway на миг поднимает два процесса бота одновременно (перекрытие при
# деплое), Telegram может доставить один и тот же callback дважды. Храним id
# уже обработанных callback'ов в памяти процесса (последние ~500) и игнорируем повтор.
_PROCESSED_CALLBACKS: set[str] = set()
_PROCESSED_CALLBACKS_ORDER: list[str] = []
_MAX_PROCESSED_CALLBACKS = 500


def _already_processed(callback_id: str) -> bool:
    """True, если этот callback.id уже обрабатывался — тогда его нужно просто
    проигнорировать (но не забыть ответить callback.answer, иначе у человека
    будет крутиться часики на кнопке)."""
    if callback_id in _PROCESSED_CALLBACKS:
        return True
    _PROCESSED_CALLBACKS.add(callback_id)
    _PROCESSED_CALLBACKS_ORDER.append(callback_id)
    if len(_PROCESSED_CALLBACKS_ORDER) > _MAX_PROCESSED_CALLBACKS:
        oldest = _PROCESSED_CALLBACKS_ORDER.pop(0)
        _PROCESSED_CALLBACKS.discard(oldest)
    return False


class OnboardingStates(StatesGroup):
    choosing_age = State()
    waiting_parent_consent_photo = State()
    accepting_documents = State()
    choosing_sport = State()
    choosing_tariff = State()
    waiting_payment_proof = State()


class AddressStates(StatesGroup):
    waiting_address = State()


INSTRUCTION_LINK = "https://quietly-divine-jay.tilda.ws/#section-instruction"
TRACKER_APPS_TEXT = (
    "📱 <b>Установи приложение-трекер</b>, чтобы снимать скрин тренировки "
    "с маршрутом, датой, временем и км. Подойдёт любое:\n"
    "• Strava\n"
    "• Nike Run Club\n"
    "• Adidas Running\n"
    "• Apple Здоровье (если iPhone)\n"
    "• Google Fit (если Android)\n"
)
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📝 Отправить отчёт")],
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="Правила")],
        [KeyboardButton(text="🆘 Написать организатору")],
    ],
    resize_keyboard=True,
)
PAYMENT_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💳 Подтвердить оплату")],
        [KeyboardButton(text="Правила"), KeyboardButton(text="🆘 Написать организатору")],
    ],
    resize_keyboard=True,
)


def age_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Мне есть 18", callback_data="age:adult")],
        [InlineKeyboardButton(text="Мне 14–17", callback_data="age:teen")],
    ])


def documents_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Принимаю все условия", callback_data="docs:accept")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="docs:cancel")],
    ])


def sport_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏃 Бег (мин 2,5 км/день)", callback_data="sport:Бег")],
        [InlineKeyboardButton(text="🚶 Ходьба (мин 3 км/день)", callback_data="sport:Ходьба")],
    ])


def tariff_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Без медали — {PRICE_NO_MEDAL}₽", callback_data="tariff:Без медали")],
        [InlineKeyboardButton(text=f"С медалью — {PRICE_WITH_MEDAL}₽", callback_data="tariff:С медалью")],
    ])


def admission_keyboard(row_index: int):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"adm_ok:{row_index}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm_no:{row_index}"),
        InlineKeyboardButton(text="🔁 Запросить ещё раз", callback_data=f"adm_retry:{row_index}"),
    ]])


def parent_consent_keyboard(row_index: int):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Согласие получено", callback_data=f"pc_ok:{row_index}"),
        InlineKeyboardButton(text="🔁 Запросить повторно", callback_data=f"pc_retry:{row_index}"),
    ]])


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    row, rec = sheets.find_participant_row(message.from_user.id)
    if rec and rec.get("Статус оплаты") == "Оплачено":
        await message.answer(
            f"С возвращением! Твой номер участника: {rec.get('ID участника')}.",
            reply_markup=MAIN_KEYBOARD,
        )
        return
    if rec and rec.get("Статус оплаты") == "Ожидает проверки":
        await message.answer("Заявка на проверке у организатора. Жди подтверждения.")
        return
    await message.answer(
        "🏔️ <b>DBL — Do Better Life</b>\n\n"
        "Привет! Ты в боте сезона DBL — спортивного челленджа на 14 дней.\n\n"
        "Каждый день ты выполняешь норму (бег или ходьба), подтверждаешь тренировку. "
        "У тебя есть 3 жизни. Сохрани хотя бы одну до конца сезона — получишь медаль DBL.\n\n"
        f"{TRACKER_APPS_TEXT}\n"
        f"📖 <b>Полная пошаговая инструкция</b> — как зарегистрироваться, оплатить "
        f"и отправлять отчёты каждый день:\n{INSTRUCTION_LINK}\n\n"
        f"Канал проекта с новостями: {CHANNEL_LINK}",
        parse_mode="HTML",
        reply_markup=PAYMENT_KEYBOARD,
    )
    await message.answer(
        "Когда будешь готов — нажми «💳 Подтвердить оплату», чтобы выбрать вид спорта, тариф и оплатить участие."
    )


@router.message(F.text == "💳 Подтвердить оплату")
async def start_payment_flow(message: Message, state: FSMContext):
    row, rec = sheets.find_participant_row(message.from_user.id)
    if rec and rec.get("Статус оплаты") == "Оплачено":
        await message.answer(
            f"Ты уже оплатил участие. Твой номер: {rec.get('ID участника')}.",
            reply_markup=MAIN_KEYBOARD,
        )
        return
    if rec and rec.get("Статус оплаты") == "Ожидает проверки":
        await message.answer("Заявка уже на проверке у организатора. Жди подтверждения.")
        return
    # если согласие родителя уже когда-то получено для этого участника — не спрашиваем возраст снова
    if rec and str(rec.get("Согласие родителя (14-17, да/нет)", "")).strip().lower() == "да":
        await state.set_state(OnboardingStates.accepting_documents)
        await _send_documents(message)
        return
    await state.set_state(OnboardingStates.choosing_age)
    await message.answer(
        "Прежде чем продолжить — укажи свой возраст:",
        reply_markup=age_keyboard(),
    )


@router.callback_query(OnboardingStates.choosing_age, F.data.startswith("age:"))
async def chosen_age(callback: CallbackQuery, state: FSMContext):
    if _already_processed(callback.id):
        await callback.answer()
        return
    age_choice = callback.data.split(":", 1)[1]
    await state.update_data(age_choice=age_choice)
    if age_choice == "adult":
        await state.set_state(OnboardingStates.accepting_documents)
        await callback.message.edit_text("Принято.")
        await _send_documents(callback.message)
        await callback.answer()
        return

    # 14-17 лет
    await state.set_state(OnboardingStates.waiting_parent_consent_photo)
    await callback.message.edit_text(
        "Участие с 14 до 17 лет возможно только при согласии родителя "
        "(законного представителя).\n\n"
        f"📄 Форма согласия: {PARENT_CONSENT_LINK}\n\n"
        "Распечатать необязательно — можно переписать текст от руки на обычном листе "
        "(ФИО ребёнка, дата рождения, согласие, дата, подпись родителя).\n\n"
        "Когда родитель заполнит и подпишет — пришли сюда фото или скан документа."
    )
    await callback.answer()


@router.message(OnboardingStates.waiting_parent_consent_photo, F.photo | F.document)
async def handle_parent_consent_photo(message: Message, state: FSMContext, bot: Bot):
    row, rec = sheets.find_participant_row(message.from_user.id)
    if row is None:
        row = sheets.add_pending_participant(
            telegram_id=message.from_user.id,
            username=message.from_user.username or message.from_user.full_name,
            sport="", tariff="", price="",
        )
        sheets.set_participant_status(row, "Ожидает проверки (согласие родителя)")

    caption = (
        f"Согласие родителя — @{message.from_user.username or '—'} "
        f"({message.from_user.id})\n"
        f"Имя: {message.from_user.full_name}\n"
        f"Строка в таблице: {row}"
    )
    if message.photo:
        await bot.send_photo(
            ORGANIZER_CHAT_ID, message.photo[-1].file_id,
            caption=caption, reply_markup=parent_consent_keyboard(row),
        )
    else:
        await bot.send_document(
            ORGANIZER_CHAT_ID, message.document.file_id,
            caption=caption, reply_markup=parent_consent_keyboard(row),
        )
    await message.answer(
        "Фото отправлено организатору на проверку. Жди подтверждения — "
        "после него продолжим регистрацию."
    )
    await state.clear()


@router.callback_query(F.data.startswith("pc_ok:"))
async def parent_consent_ok(callback: CallbackQuery, bot: Bot, state: FSMContext):
    if _already_processed(callback.id):
        await callback.answer()
        return
    await callback.answer("Обрабатываю...")
    try:
        row = int(callback.data.split(":")[1])
        rec = sheets.participants_ws.row_values(row)

        def _col(name, default=""):
            idx = sheets.PARTICIPANTS_HEADERS.index(name)
            return rec[idx] if len(rec) > idx else default

        telegram_field = _col("Telegram/контакт")
        telegram_id = telegram_field.split(" ")[0]
        sheets.set_participant_field(row, "Согласие родителя (14-17, да/нет)", "Да")

        participant_key = StorageKey(bot_id=bot.id, chat_id=int(telegram_id), user_id=int(telegram_id))
        participant_state = FSMContext(storage=state.storage, key=participant_key)
        await participant_state.set_state(OnboardingStates.accepting_documents)

        await bot.send_message(
            int(telegram_id),
            "✅ Согласие родителя подтверждено! Теперь нужно принять условия участия.",
        )
        await _send_documents_by_bot(bot, int(telegram_id))
        await _safe_edit_caption(callback.message, callback.message.caption + "\n\n✅ СОГЛАСИЕ ПОДТВЕРЖДЕНО")
    except Exception:
        log.exception("Ошибка в parent_consent_ok")
        await callback.message.answer("⚠️ Ошибка при обработке: см. логи Railway")


@router.callback_query(F.data.startswith("pc_retry:"))
async def parent_consent_retry(callback: CallbackQuery, bot: Bot, state: FSMContext):
    if _already_processed(callback.id):
        await callback.answer()
        return
    await callback.answer("Обрабатываю...")
    try:
        row = int(callback.data.split(":")[1])
        rec = sheets.participants_ws.row_values(row)
        idx = sheets.PARTICIPANTS_HEADERS.index("Telegram/контакт")
        telegram_field = rec[idx] if len(rec) > idx else ""
        telegram_id = telegram_field.split(" ")[0]

        participant_key = StorageKey(bot_id=bot.id, chat_id=int(telegram_id), user_id=int(telegram_id))
        participant_state = FSMContext(storage=state.storage, key=participant_key)
        await participant_state.set_state(OnboardingStates.waiting_parent_consent_photo)

        await bot.send_message(
            int(telegram_id),
            "Фото согласия плохо видно или заполнено не полностью. "
            "Пришли, пожалуйста, более чёткое фото документа ещё раз.",
        )
        await _safe_edit_caption(callback.message, callback.message.caption + "\n\n🔁 ЗАПРОШЕНО ПОВТОРНО")
    except Exception:
        log.exception("Ошибка в parent_consent_retry")
        await callback.message.answer("⚠️ Ошибка при обработке: см. логи Railway")


async def _send_documents(message: Message):
    await message.answer(
        "Перед оплатой ознакомься с документами:\n\n"
        f"📄 Публичная оферта: {OFFER_LINK}\n"
        f"🔒 Политика обработки персональных данных: {PRIVACY_POLICY_LINK}\n"
        f"✅ Согласие на обработку персональных данных: {PD_CONSENT_LINK}",
        reply_markup=documents_keyboard(),
    )


async def _send_documents_by_bot(bot: Bot, telegram_id: int):
    await bot.send_message(
        telegram_id,
        "Перед оплатой ознакомься с документами:\n\n"
        f"📄 Публичная оферта: {OFFER_LINK}\n"
        f"🔒 Политика обработки персональных данных: {PRIVACY_POLICY_LINK}\n"
        f"✅ Согласие на обработку персональных данных: {PD_CONSENT_LINK}",
        reply_markup=documents_keyboard(),
    )


@router.callback_query(OnboardingStates.accepting_documents, F.data == "docs:accept")
async def documents_accepted(callback: CallbackQuery, state: FSMContext):
    if _already_processed(callback.id):
        await callback.answer()
        return
    await state.update_data(docs_accepted_at=sheets.now_str())
    await state.set_state(OnboardingStates.choosing_sport)
    await callback.message.edit_text("Условия приняты. Выбери вид спорта:")
    await callback.message.answer("Вид спорта:", reply_markup=sport_keyboard())
    await callback.answer()


@router.callback_query(OnboardingStates.accepting_documents, F.data == "docs:cancel")
async def documents_cancelled(callback: CallbackQuery, state: FSMContext):
    if _already_processed(callback.id):
        await callback.answer()
        return
    await state.clear()
    await callback.message.edit_text(
        "Хорошо, регистрация отменена. Когда будешь готов — нажми «💳 Подтвердить оплату» ещё раз."
    )
    await callback.answer()


@router.callback_query(OnboardingStates.choosing_sport, F.data.startswith("sport:"))
async def chosen_sport(callback: CallbackQuery, state: FSMContext):
    if _already_processed(callback.id):
        await callback.answer()
        return
    sport = callback.data.split(":", 1)[1]
    await state.update_data(sport=sport)
    await state.set_state(OnboardingStates.choosing_tariff)
    await callback.message.edit_text(f"Вид спорта: {sport}\n\nТеперь выбери тариф:")
    await callback.message.answer("Тариф:", reply_markup=tariff_keyboard())
    await callback.answer()


@router.callback_query(OnboardingStates.choosing_tariff, F.data.startswith("tariff:"))
async def chosen_tariff(callback: CallbackQuery, state: FSMContext):
    if _already_processed(callback.id):
        await callback.answer()
        return
    tariff = callback.data.split(":", 1)[1]
    price = PRICE_WITH_MEDAL if tariff == "С медалью" else PRICE_NO_MEDAL
    link = PAYMENT_LINK_WITH_MEDAL if tariff == "С медалью" else PAYMENT_LINK_NO_MEDAL
    await state.update_data(tariff=tariff, price=price)
    await state.set_state(OnboardingStates.waiting_payment_proof)
    pay_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💳 Оплатить {price}₽", url=link)],
    ])
    await callback.message.edit_text(
        f"Тариф: {tariff} — {price}₽\n\n"
        f"Нажми кнопку ниже, чтобы оплатить. После оплаты пришли сюда чек/скриншот "
        f"и укажи ФИО, которое использовал при оплате.",
        reply_markup=pay_kb,
    )
    await callback.answer()


async def _not_in_report_flow(message: Message, state: FSMContext) -> bool:
    current_state = await state.get_state()
    return current_state not in (
        ReportStates.waiting_photo.state,
        ReportStates.waiting_km.state,
        OrganizerMsgStates.waiting_text.state,
    )


@router.message((F.photo | F.document), _not_in_report_flow, OnboardingStates.waiting_payment_proof)
async def handle_payment_proof(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    sport = data.get("sport", "")
    tariff = data.get("tariff", "")
    price = data.get("price", "")
    retry_row = data.get("retry_row")
    consent_at = data.get("docs_accepted_at", "")
    age_choice = data.get("age_choice", "")

    if retry_row:
        row = retry_row
        sheets.set_participant_status(row, "Ожидает проверки")
        if consent_at:
            sheets.set_participant_field(row, "Согласие с офертой", consent_at)
            sheets.set_participant_field(row, "Согласие на обработку ПДн (дата/время)", consent_at)
    else:
        row = sheets.add_pending_participant(
            telegram_id=message.from_user.id,
            username=message.from_user.username or message.from_user.full_name,
            sport=sport,
            tariff=tariff,
            price=price,
            consent_at=consent_at,
        )
        if consent_at:
            sheets.set_participant_field(row, "Согласие на обработку ПДн (дата/время)", consent_at)
        if age_choice == "teen":
            sheets.set_participant_field(row, "Согласие родителя (14-17, да/нет)", "Да")

    caption = (
        f"Новая заявка на допуск\n"
        f"Telegram: @{message.from_user.username or '—'} ({message.from_user.id})\n"
        f"Имя: {message.from_user.full_name}\n"
        f"Спорт: {sport}\n"
        f"Тариф: {tariff} — {price}₽\n"
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
    await state.clear()


@router.callback_query(F.data.startswith("adm_ok:"))
async def admission_ok(callback: CallbackQuery, bot: Bot):
    if _already_processed(callback.id):
        await callback.answer()
        return
    await callback.answer("Обрабатываю...")
    try:
        row = int(callback.data.split(":")[1])
        rec = sheets.participants_ws.row_values(row)

        def _col(name, default=""):
            idx = sheets.PARTICIPANTS_HEADERS.index(name)
            return rec[idx] if len(rec) > idx else default

        telegram_field = _col("Telegram/контакт")
        telegram_id = telegram_field.split(" ")[0]
        tariff = _col("Тариф")
        season = _col("Сезон", "S01")
        participant_id = f"DBL {season}-{str(row).zfill(4)}"
        sheets.set_participant_status(row, "Оплачено", participant_id)
        await bot.send_message(
            int(telegram_id),
            f"✅ Оплата подтверждена! Твой номер участника: {participant_id}.\n\n"
            f"Присоединяйся к чату участников — там мы делимся результатами и поддерживаем друг друга:\n{GROUP_LINK}",
            reply_markup=MAIN_KEYBOARD,
        )
        if tariff == "С медалью":
            await bot.send_message(
                int(telegram_id),
                "Твой тариф включает медаль — пришли, пожалуйста, адрес доставки "
                "(город, улица, дом, квартира, индекс, получатель).",
            )
            sheets.set_participant_field(row, "Адрес получен?", "Ожидается")
        await _safe_edit_caption(
            callback.message, callback.message.caption + f"\n\n✅ ПОДТВЕРЖДЕНО: {participant_id}"
        )
    except Exception:
        log.exception("Ошибка в admission_ok")
        await callback.message.answer("⚠️ Ошибка при обработке: см. логи Railway")


@router.callback_query(F.data.startswith("adm_no:"))
async def admission_no(callback: CallbackQuery, bot: Bot):
    if _already_processed(callback.id):
        await callback.answer()
        return
    await callback.answer("Обрабатываю...")
    try:
        row = int(callback.data.split(":")[1])
        rec = sheets.participants_ws.row_values(row)
        idx = sheets.PARTICIPANTS_HEADERS.index("Telegram/контакт")
        telegram_field = rec[idx] if len(rec) > idx else ""
        telegram_id = telegram_field.split(" ")[0]
        sheets.set_participant_status(row, "Отклонено")
        await bot.send_message(
            int(telegram_id),
            f"❌ Оплата не подтверждена. Напиши организатору лично: {ORGANIZER_USERNAME}\n\n"
            f"Если хочешь попробовать снова — нажми «💳 Подтвердить оплату».",
            reply_markup=PAYMENT_KEYBOARD,
        )
        await _safe_edit_caption(callback.message, callback.message.caption + "\n\n❌ ОТКЛОНЕНО")
    except Exception:
        log.exception("Ошибка в admission_no")
        await callback.message.answer("⚠️ Ошибка при обработке: см. логи Railway")


@router.callback_query(F.data.startswith("adm_retry:"))
async def admission_retry(callback: CallbackQuery, bot: Bot, state: FSMContext):
    if _already_processed(callback.id):
        await callback.answer()
        return
    await callback.answer("Обрабатываю...")
    try:
        row = int(callback.data.split(":")[1])
        rec = sheets.participants_ws.row_values(row)

        def _col(name, default=""):
            idx = sheets.PARTICIPANTS_HEADERS.index(name)
            return rec[idx] if len(rec) > idx else default

        telegram_field = _col("Telegram/контакт")
        telegram_id = telegram_field.split(" ")[0]
        sport = _col("Спорт")
        tariff = _col("Тариф")
        price = _col("Сумма оплаты, ₽")

        participant_key = StorageKey(
            bot_id=bot.id,
            chat_id=int(telegram_id),
            user_id=int(telegram_id),
        )
        participant_state = FSMContext(storage=state.storage, key=participant_key)
        await participant_state.set_state(OnboardingStates.waiting_payment_proof)
        await participant_state.update_data(sport=sport, tariff=tariff, price=price, retry_row=row)
        await bot.send_message(
            int(telegram_id),
            "Пришли, пожалуйста, более чёткий чек/скриншот оплаты и ФИО.",
        )
        await _safe_edit_caption(callback.message, callback.message.caption + "\n\n🔁 ЗАПРОШЕНО ПОВТОРНО")
    except Exception:
        log.exception("Ошибка в admission_retry")
        await callback.message.answer("⚠️ Ошибка при обработке: см. логи Railway")


@router.message(F.text == "👤 Профиль")
async def cmd_profile(message: Message):
    row, rec = sheets.find_participant_row(message.from_user.id)
    if not rec or rec.get("Статус оплаты") != "Оплачено":
        await message.answer("Ты ещё не допущен к сезону.")
        return
    lives = sheets.get_lives(rec.get("ID участника"))
    season_day = sheets.get_season_day()
    day_line = f"День сезона: {season_day}/14\n" if season_day else "Сезон ещё не начался\n"
    hearts = "❤️" * lives + "🖤" * (3 - lives)
    text = (
        f"👤 <b>Твой профиль DBL</b>\n\n"
        f"Номер участника: <b>{rec.get('ID участника')}</b>\n"
        f"Вид спорта: {rec.get('Спорт') or '—'}\n"
        f"Тариф: {rec.get('Тариф') or '—'}\n"
        f"{day_line}"
        f"Жизни: {hearts} ({lives}/3)\n"
    )
    if rec.get("Тариф") == "С медалью":
        addr_status = rec.get("Адрес получен?", "Нет")
        text += f"Адрес доставки: {addr_status}\n"
    await message.answer(text, parse_mode="HTML")


@router.message(F.text == "Правила")
async def cmd_rules(message: Message):
    await message.answer(
        "📋 <b>Правила сезона DBL</b>\n\n"
        "🗓 Сезон — 14 дней подряд\n"
        "🏃 Бег — минимум 2,5 км в день\n"
        "🚶 Ходьба — минимум 3 км в день\n\n"
        "📸 Каждый день отправляешь подтверждение тренировки (фото/скрин с датой, "
        "маршрутом, километражем) до 23:59.\n\n"
        "❤️ У тебя 3 жизни. Пропустил норму — минус жизнь. Если докажешь, что "
        "тренировка была выполнена в нужный день, до конца сезона — жизнь вернётся.\n\n"
        "🏅 Медаль получает тот, кто сохранил хотя бы 1 жизнь и имеет не более "
        "3 незачтённых дней за сезон.\n\n"
        "📝 Отправить отчёт — кнопка «Отправить отчёт» в меню или команда /report.\n"
        "Если скриншот плохо виден — организатор попросит переслать более чёткий, "
        "это не считается нарушением.\n\n"
        f"Вопросы — организатору: {ORGANIZER_USERNAME}",
        parse_mode="HTML",
    )


@router.message(F.text == "📝 Отправить отчёт")
async def cmd_report_button(message: Message, state: FSMContext):
    from handlers.reports import cmd_report
    await cmd_report(message, state)


@router.message(Command("help"))
@router.message(F.text == "🆘 Написать организатору")
async def cmd_contact_organizer(message: Message):
    await message.answer(
        "Если у тебя вопрос, проблема с отчётом, оплатой или что-то ещё — "
        f"пиши прямо организатору: {ORGANIZER_USERNAME}\n\n"
        "Опиши ситуацию и, если есть, свой номер участника (DBL S01-XXXX) — "
        "так быстрее разберёмся."
    )


MENU_BUTTONS = (
    "📝 Отправить отчёт", "👤 Профиль", "Правила", "🆘 Написать организатору",
    "💳 Подтвердить оплату",
)


@router.message(F.text, ~F.text.startswith("/"), _not_in_report_flow)
async def catch_address(message: Message, state: FSMContext, bot: Bot):
    """Если участник допущен по тарифу 'С медалью' и адрес ещё не получен —
    любое его текстовое сообщение (кроме кнопок меню и команд) считаем адресом."""
    if message.from_user.id == ORGANIZER_CHAT_ID:
        return
    if message.text in MENU_BUTTONS:
        return
    row, rec = sheets.find_participant_row(message.from_user.id)
    # Статус "Ожидается" в колонке "Адрес получен?" выставляется только в
    # admission_ok и только для тарифа "С медалью" — его одного достаточно,
    # чтобы понять, что мы ждём от человека именно адрес. Доп. проверка
    # тарифа здесь была лишней и ломалась, если поле "Тариф" в таблице
    # почему-то оказывалось пустым/не совпадало по формату.
    if rec and rec.get("Адрес получен?") == "Ожидается":
        sheets.set_participant_field(row, "Адрес получен?", "Да")
        sheets.set_participant_field(row, "Адрес доставки", message.text)
        await message.answer("Адрес сохранён, спасибо!", reply_markup=MAIN_KEYBOARD)
        await bot.send_message(
            ORGANIZER_CHAT_ID,
            f"📍 Получен адрес доставки от {rec.get('ID участника')}:\n{message.text}",
        )
