import logging
import os

from aiogram import Router, F, Bot
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
)

import sheets
from handlers.reports import ReportStates

router = Router()
ORGANIZER_CHAT_ID = int(os.environ["ORGANIZER_CHAT_ID"])
ORGANIZER_USERNAME = "@manchik_luts"
log = logging.getLogger(__name__)

PRICE_NO_MEDAL = 500
PRICE_WITH_MEDAL = 1100
PAYMENT_LINK_NO_MEDAL = "https://yookassa.ru/my/i/atKmjoB8TKJB/l"
PAYMENT_LINK_WITH_MEDAL = "https://yookassa.ru/my/i/atKnClymrePv/l"
CHANNEL_LINK = "https://t.me/DBL_Better_life"
GROUP_LINK = "https://t.me/+sK53VEAlDwo3YzVi"


class OnboardingStates(StatesGroup):
    choosing_sport = State()
    choosing_tariff = State()
    waiting_address = State()
    waiting_offer_consent = State()
    waiting_payment_proof = State()


OFFER_SHORT_TEXT = (
    "📋 <b>Перед оплатой — коротко об условиях участия:</b>\n\n"
    "• Участие — в 14-дневном спортивном сезоне DBL (бег/ходьба)\n"
    "• Медаль (тариф «С медалью») отправляется после подведения итогов сезона "
    "по адресу, который ты указал\n"
    "• Твои данные (Telegram ID, адрес доставки) используются только для "
    "организации сезона и не передаются третьим лицам\n"
    "• Полные условия — кнопка «📄 Читать полностью» ниже\n\n"
    "Нажимая «✅ Принимаю условия», ты подтверждаешь, что ознакомлен и согласен."
)

OFFER_FULL_TEXT = (
    "📄 <b>Публичная оферта на участие в спортивном сезоне DBL (Do Better Life)</b>\n\n"
    "<b>1. Предмет</b>\n"
    "Участие в 14-дневном спортивном сезоне DBL. Участник выбирает вид спорта "
    "(бег — минимум 2,5 км/день, ходьба — минимум 3 км/день) и тариф.\n\n"
    "<b>2. Тарифы</b>\n"
    "— Без медали — 500₽\n"
    "— С медалью — 1100₽ (включает медаль и доставку по РФ)\n\n"
    "<b>3. Условия участия</b>\n"
    "У каждого участника 3 жизни. Подтверждение тренировки отправляется "
    "ежедневно до 23:59 через Telegram-бот. Правила начисления/снятия жизней "
    "и условия получения медали — в разделе «Правила» бота.\n\n"
    "<b>4. Организатор</b>\n"
    "Луночкина Юлия Анатольевна, самозанятая, ИНН 110404793883.\n"
    "Контакт: @manchik_luts\n\n"
    "<b>5. Оплата и отказ от участия</b>\n"
    "Оплата производится до начала сезона через ЮKassa. После начала сезона "
    "деньги не возвращаются; участник переносится на участие в следующем "
    "сезоне бесплатно.\n\n"
    "<b>6. Медаль и доставка</b>\n"
    "Для тарифа «С медалью» адрес доставки собирается при регистрации. "
    "Медаль отправляется после подведения итогов сезона.\n\n"
    "<b>7. Персональные данные</b>\n"
    "Организатор собирает: Telegram ID, username, адрес доставки (для тарифа "
    "с медалью). Данные используются только для организации сезона и не "
    "передаются третьим лицам.\n\n"
    "<b>8. Ответственность</b>\n"
    "DBL не является медицинской услугой. Участник самостоятельно оценивает "
    "состояние своего здоровья перед участием в тренировках.\n\n"
    "<b>9. Контакты</b>\n"
    "По всем вопросам — @manchik_luts"
)


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
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="ℹ️ Правила")],
        [KeyboardButton(text="🆘 Написать организатору")],
    ],
    resize_keyboard=True,
)

# Клавиатура для тех, кто ещё не оплатил активный сезон — показываем кнопку оплаты.
PAYMENT_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💳 Подтвердить оплату")],
        [KeyboardButton(text="ℹ️ Правила"), KeyboardButton(text="🆘 Написать организатору")],
    ],
    resize_keyboard=True,
)


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
        f"📖 Полная пошаговая инструкция — пролистай сайт по ссылке до самого низа, "
        f"там будет раздел «Инструкция участника»: {INSTRUCTION_LINK}\n\n"
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

    await state.set_state(OnboardingStates.choosing_sport)
    await message.answer(
        "Выбери вид спорта:",
        reply_markup=sport_keyboard(),
    )


@router.callback_query(OnboardingStates.choosing_sport, F.data.startswith("sport:"))
async def chosen_sport(callback: CallbackQuery, state: FSMContext):
    sport = callback.data.split(":", 1)[1]
    await state.update_data(sport=sport)
    await state.set_state(OnboardingStates.choosing_tariff)
    await callback.message.edit_text(f"Вид спорта: {sport}\n\nТеперь выбери тариф:")
    await callback.message.answer("Тариф:", reply_markup=tariff_keyboard())
    await callback.answer()


def offer_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Читать полностью", callback_data="offer_full")],
        [InlineKeyboardButton(text="✅ Принимаю условия", callback_data="offer_accept")],
    ])


@router.callback_query(OnboardingStates.choosing_tariff, F.data.startswith("tariff:"))
async def chosen_tariff(callback: CallbackQuery, state: FSMContext):
    tariff = callback.data.split(":", 1)[1]
    price = PRICE_WITH_MEDAL if tariff == "С медалью" else PRICE_NO_MEDAL
    await state.update_data(tariff=tariff, price=price)

    if tariff == "С медалью":
        await state.set_state(OnboardingStates.waiting_address)
        await callback.message.edit_text(
            f"Тариф: {tariff} — {price}₽\n\n"
            "Твой тариф включает медаль — укажи, пожалуйста, адрес доставки "
            "(город, улица, дом, квартира, индекс, получатель)."
        )
        await callback.answer()
        return

    await state.set_state(OnboardingStates.waiting_offer_consent)
    await callback.message.edit_text(f"Тариф: {tariff} — {price}₽")
    await callback.message.answer(OFFER_SHORT_TEXT, parse_mode="HTML", reply_markup=offer_keyboard())
    await callback.answer()


@router.message(OnboardingStates.waiting_address, F.text)
async def got_address(message: Message, state: FSMContext):
    await state.update_data(address=message.text)
    data = await state.get_data()
    await state.set_state(OnboardingStates.waiting_offer_consent)
    await message.answer(
        f"Адрес сохранён: {message.text}\n\nТариф: {data.get('tariff')} — {data.get('price')}₽"
    )
    await message.answer(OFFER_SHORT_TEXT, parse_mode="HTML", reply_markup=offer_keyboard())


@router.callback_query(OnboardingStates.waiting_offer_consent, F.data == "offer_full")
async def offer_full(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(OFFER_FULL_TEXT, parse_mode="HTML", reply_markup=offer_keyboard())


@router.callback_query(OnboardingStates.waiting_offer_consent, F.data == "offer_accept")
async def offer_accept(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Принято")
    consent_at = sheets.now_str()
    await state.update_data(consent_at=consent_at)
    data = await state.get_data()
    tariff = data.get("tariff")
    price = data.get("price")
    link = PAYMENT_LINK_WITH_MEDAL if tariff == "С медалью" else PAYMENT_LINK_NO_MEDAL

    await state.set_state(OnboardingStates.waiting_payment_proof)
    pay_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💳 Оплатить {price}₽", url=link)],
    ])
    await callback.message.answer(
        f"Спасибо! Условия приняты ({consent_at}).\n\n"
        f"Тариф: {tariff} — {price}₽\n\n"
        f"Нажми кнопку ниже, чтобы оплатить. После оплаты пришли сюда чек/скриншот "
        f"и укажи ФИО, которое использовал при оплате.",
        reply_markup=pay_kb,
    )


async def _not_in_report_flow(message: Message, state: FSMContext) -> bool:
    current_state = await state.get_state()
    return current_state not in (ReportStates.waiting_photo.state, ReportStates.waiting_km.state)


def _is_awaiting_payment_proof(message: Message, state_name) -> bool:
    """True, если участник либо сейчас в FSM-состоянии ожидания чека (новый поток),
    либо уже есть заявка со статусом 'Ожидает проверки' (после «Запросить ещё раз» —
    тогда FSM уже очищен, но заявка в таблице ещё ждёт чек)."""
    if state_name == OnboardingStates.waiting_payment_proof.state:
        return True
    _, rec = sheets.find_participant_row(message.from_user.id)
    return bool(rec) and rec.get("Статус оплаты") == "Ожидает проверки"


@router.message((F.photo | F.document), _not_in_report_flow)
async def handle_payment_proof(message: Message, state: FSMContext, bot: Bot):
    current_state = await state.get_state()
    if not _is_awaiting_payment_proof(message, current_state):
        return  # фото не относится к оплате — пусть обработают другие хендлеры/ничего не делаем

    data = await state.get_data()
    sport = data.get("sport", "")
    tariff = data.get("tariff", "")
    price = data.get("price", "")
    address = data.get("address", "")
    consent_at = data.get("consent_at", "")

    row = sheets.add_pending_participant(
        telegram_id=message.from_user.id,
        username=message.from_user.username or message.from_user.full_name,
        sport=sport,
        tariff=tariff,
        price=price,
        address=address,
        consent_at=consent_at,
    )

    address_line = f"Адрес доставки: {address}\n" if address else ""
    caption = (
        f"Заявка на допуск (повторная отправка чека)\n" if current_state != OnboardingStates.waiting_payment_proof.state
        else f"Новая заявка на допуск\n"
    )
    caption += (
        f"Telegram: @{message.from_user.username or '—'} ({message.from_user.id})\n"
        f"Имя: {message.from_user.full_name}\n"
        f"Спорт: {sport}\n"
        f"Тариф: {tariff} — {price}₽\n"
        f"{address_line}"
        f"Согласие с офертой: {consent_at or '—'}\n"
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
            sheets.set_participant_field(row, "Адрес получен?", "Да")

        await callback.message.edit_caption(
            caption=callback.message.caption + f"\n\n✅ ПОДТВЕРЖДЕНО: {participant_id}"
        )
    except Exception:
        log.exception("Ошибка в admission_ok")
        await callback.message.answer("⚠️ Ошибка при обработке: см. логи Railway")


@router.callback_query(F.data.startswith("adm_no:"))
async def admission_no(callback: CallbackQuery, bot: Bot):
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
        idx = sheets.PARTICIPANTS_HEADERS.index("Telegram/контакт")
        telegram_field = rec[idx] if len(rec) > idx else ""
        telegram_id = telegram_field.split(" ")[0]

        await bot.send_message(
            int(telegram_id),
            "Пришли, пожалуйста, более чёткий чек/скриншот оплаты и ФИО.",
        )
        await callback.message.edit_caption(caption=callback.message.caption + "\n\n🔁 ЗАПРОШЕНО ПОВТОРНО")
    except Exception:
        log.exception("Ошибка в admission_retry")
        await callback.message.answer("⚠️ Ошибка при обработке: см. логи Railway")


# ---------- Профиль / Правила / кнопка отчёта ----------

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
        addr = rec.get("Адрес доставки") or "не указан"
        text += f"Адрес доставки: {addr}\n"
    await message.answer(text, parse_mode="HTML")


@router.message(F.text == "ℹ️ Правила")
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
    "📝 Отправить отчёт", "👤 Профиль", "ℹ️ Правила", "🆘 Написать организатору",
    "💳 Подтвердить оплату",
)
