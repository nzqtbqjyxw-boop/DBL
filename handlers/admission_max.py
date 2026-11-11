"""
Допуск участников — MAX-версия. Логика и тексты повторяют Telegram-бот
(handlers/admission.py), но БЕЗ согласия родителя (14-17) и БЕЗ реферальной
программы в этой первой версии — добавим вторым этапом после того, как
базовый поток (регистрация -> тариф -> оплата -> допуск организатором)
будет проверен и стабильно работает.
"""
import logging
import os

import sheets
from max_bot import state as st

log = logging.getLogger(__name__)

ORGANIZER_MAX_ID = int(os.environ.get("ORGANIZER_MAX_ID", "0"))
ORGANIZER_USERNAME = "@manchik_luts"

PRICE_NO_MEDAL = 0
PRICE_WITH_MEDAL = 1390

CHANNEL_LINK = "https://t.me/DBL_Better_life"
GROUP_LINK = "https://t.me/+sK53VEAlDwo3YzVi"
INSTRUCTION_LINK = "https://dbldobetterlife.tilda.ws"

OFFER_LINK = "https://telegra.ph/PUBLICHNAYA-OFERTA-11-03-27"
PRIVACY_POLICY_LINK = "https://telegra.ph/POLITIKA-OBRABOTKI-PERSONALNYH-DANNYH-DBL-11-03"
PD_CONSENT_LINK = "https://telegra.ph/SOGLASIE-NA-OBRABOTKU-PERSONALNYH-DANNYH-DBL-11-03"

MAIN_KEYBOARD = {
    "type": "inline_keyboard",
    "payload": {
        "buttons": [
            [{"type": "callback", "text": "📝 Отправить отчёт", "payload": "menu:report"}],
            [{"type": "callback", "text": "👤 Профиль", "payload": "menu:profile"},
             {"type": "callback", "text": "Правила", "payload": "menu:rules"}],
        ]
    },
}

PAYMENT_KEYBOARD = {
    "type": "inline_keyboard",
    "payload": {
        "buttons": [
            [{"type": "callback", "text": "💳 Подтвердить оплату", "payload": "menu:pay"}],
        ]
    },
}


def _age_keyboard():
    return {"type": "inline_keyboard", "payload": {"buttons": [
        [{"type": "callback", "text": "Мне есть 18", "payload": "age:adult"}],
    ]}}
    # Примечание: ветка 14-17 с согласием родителя сознательно не перенесена
    # в v1 MAX-бота — добавим после проверки базового потока.


def _documents_keyboard():
    return {"type": "inline_keyboard", "payload": {"buttons": [
        [{"type": "callback", "text": "✅ Принимаю все условия", "payload": "docs:accept"}],
    ]}}


def _sport_keyboard():
    return {"type": "inline_keyboard", "payload": {"buttons": [
        [{"type": "callback", "text": "🏃 Бег (мин 2,5 км/день)", "payload": "sport:Бег"}],
        [{"type": "callback", "text": "🚶 Ходьба (мин 3 км/день)", "payload": "sport:Ходьба"}],
    ]}}


def _tariff_keyboard():
    return {"type": "inline_keyboard", "payload": {"buttons": [
        [{"type": "callback", "text": "🆓 Без медали — бесплатно", "payload": "tariff:Без медали"}],
        [{"type": "callback", "text": f"С медалью — {PRICE_WITH_MEDAL}₽", "payload": "tariff:С медалью"}],
    ]}}


def _admission_keyboard(row_index: int):
    return {"type": "inline_keyboard", "payload": {"buttons": [[
        {"type": "callback", "text": "✅ Подтвердить", "payload": f"adm_ok:{row_index}"},
        {"type": "callback", "text": "❌ Отклонить", "payload": f"adm_no:{row_index}"},
    ]]}}


def _max_user_tag(sender: dict) -> str:
    """MAX-аналог 'Telegram/контакт': '<user_id> (<имя>)'."""
    name = sender.get("name") or sender.get("username") or "?"
    return f"{sender['user_id']} ({name})"


async def cmd_start(api, user_id: int, sender: dict, payload: str = ""):
    row, rec = sheets.find_participant_row(user_id)
    if rec and rec.get("Статус оплаты") == "Оплачено":
        await api.send_message(user_id=user_id,
                                text=f"С возвращением! Твой номер участника: {rec.get('ID участника')}.",
                                attachments=[MAIN_KEYBOARD])
        return
    if rec and rec.get("Статус оплаты") == "Ожидает проверки":
        await api.send_message(user_id=user_id, text="Заявка на проверке у организатора. Жди подтверждения.")
        return

    await api.send_message(
        user_id=user_id,
        text=(
            "🏔️ <b>DBL — Do Better Life</b>\n\n"
            "Привет! Ты в боте сезона DBL — спортивного челленджа на 14 дней.\n\n"
            "Каждый день ты выполняешь норму (бег или ходьба), подтверждаешь тренировку. "
            "У тебя есть 3 жизни. Сохрани хотя бы одну до конца сезона — получишь медаль DBL.\n\n"
            f"📖 Полная инструкция — на сайте: {INSTRUCTION_LINK}\n\n"
            f"Канал проекта: {CHANNEL_LINK}"
        ),
        attachments=[PAYMENT_KEYBOARD],
    )


async def start_payment_flow(api, user_id: int, sender: dict, user_state):
    row, rec = sheets.find_participant_row(user_id)
    if rec and rec.get("Статус оплаты") == "Оплачено":
        await api.send_message(user_id=user_id,
                                text=f"Ты уже допущен к сезону. Твой номер: {rec.get('ID участника')}.",
                                attachments=[MAIN_KEYBOARD])
        return
    if rec and rec.get("Статус оплаты") == "Ожидает проверки":
        await api.send_message(user_id=user_id, text="Заявка уже на проверке у организатора. Жди подтверждения.")
        return

    st.set_state(user_id, "choosing_age")
    await api.send_message(user_id=user_id, text="Прежде чем продолжить — укажи свой возраст:",
                            attachments=[_age_keyboard()])


async def chosen_age(api, user_id: int, callback_id: str, payload: str):
    await api.answer_callback(callback_id)
    age_choice = payload.split(":", 1)[1]
    st.update_data(user_id, age_choice=age_choice)
    st.set_state(user_id, "accepting_documents")
    await _send_documents(api, user_id)


async def _send_documents(api, user_id: int):
    await api.send_message(
        user_id=user_id,
        text=(
            "Перед продолжением ознакомься с документами:\n\n"
            f"📄 Публичная оферта: {OFFER_LINK}\n"
            f"🔒 Политика обработки персональных данных: {PRIVACY_POLICY_LINK}\n"
            f"✅ Согласие на обработку персональных данных: {PD_CONSENT_LINK}"
        ),
        attachments=[_documents_keyboard()],
    )


async def documents_accepted(api, user_id: int, callback_id: str):
    await api.answer_callback(callback_id)
    st.update_data(user_id, docs_accepted_at=sheets.now_str())
    st.set_state(user_id, "choosing_sport")
    await api.send_message(user_id=user_id, text="Условия приняты. Выбери вид спорта:",
                            attachments=[_sport_keyboard()])


async def chosen_sport(api, user_id: int, callback_id: str, payload: str):
    await api.answer_callback(callback_id)
    sport = payload.split(":", 1)[1]
    st.update_data(user_id, sport=sport)
    st.set_state(user_id, "choosing_tariff")
    await api.send_message(user_id=user_id, text="Выбери тариф:", attachments=[_tariff_keyboard()])


async def chosen_tariff(api, user_id: int, callback_id: str, payload: str):
    await api.answer_callback(callback_id)
    tariff = payload.split(":", 1)[1]
    st.update_data(user_id, tariff=tariff)

    if tariff == "Без медали":
        st.update_data(user_id, price=PRICE_NO_MEDAL)
        st.set_state(user_id, "waiting_free_phone")
        await api.send_message(
            user_id=user_id,
            text="Отлично! Для бесплатного тарифа нужен номер телефона (для связи в спорных ситуациях). "
                 "Введи номер текстом (например +79001234567).",
        )
    else:
        st.update_data(user_id, price=PRICE_WITH_MEDAL)
        st.set_state(user_id, "waiting_payment_proof")
        await api.send_message(
            user_id=user_id,
            text=f"Тариф «С медалью» — {PRICE_WITH_MEDAL}₽.\n\n"
                 "Оплати по ссылке, затем пришли сюда скриншот/чек оплаты:\n"
                 "https://yookassa.ru/my/i/au5A5aeec8yn/l",
        )


async def handle_free_phone_text(api, user_id: int, text: str, sender: dict, user_state):
    phone_raw = text.strip()
    digits = "".join(ch for ch in phone_raw if ch.isdigit())
    if not (10 <= len(digits) <= 15):
        await api.send_message(user_id=user_id,
                                text="Это не похоже на номер телефона. Введи номер текстом, например +79001234567.")
        return
    await _register_free_participant(api, user_id, sender, user_state, phone=phone_raw)


async def _register_free_participant(api, user_id: int, sender: dict, user_state, phone: str):
    data = user_state.data
    sport = data.get("sport", "")
    tariff = data.get("tariff", "")
    consent_at = data.get("docs_accepted_at", "")

    row = sheets.add_pending_participant(
        telegram_id=user_id,  # используем тот же столбец "Telegram/контакт" для MAX ID (см. README_MAX.md)
        username=sender.get("name") or sender.get("username") or "?",
        sport=sport, tariff=tariff, price=PRICE_NO_MEDAL,
        consent_at=consent_at, phone=phone,
    )
    season = "S01"
    participant_id = f"DBL {season}-{str(row).zfill(4)}"
    sheets.set_participant_status(row, "Оплачено", participant_id)

    await api.send_message(
        user_id=user_id,
        text=f"✅ Готово! Твой номер участника: {participant_id}.\n\nЧат участников: {GROUP_LINK}",
        attachments=[MAIN_KEYBOARD],
    )
    if ORGANIZER_MAX_ID:
        await api.send_message(
            user_id=ORGANIZER_MAX_ID,
            text=f"🆓 Новый участник (MAX, бесплатный тариф)\n"
                 f"Имя: {sender.get('name')}\nТелефон: {phone}\nСпорт: {sport}\nНомер: {participant_id}",
        )
    st.clear(user_id)


async def try_handle_payment_proof(api, user_id: int, image_att: dict, user_state) -> bool:
    """Обрабатывает фото оплаты, если пользователь сейчас на этом шаге. Возвращает True, если обработано."""
    if user_state.state != "waiting_payment_proof":
        return False

    data = user_state.data
    sport = data.get("sport", "")
    tariff = data.get("tariff", "")
    price = data.get("price", "")
    consent_at = data.get("docs_accepted_at", "")

    row = sheets.add_pending_participant(
        telegram_id=user_id,
        username="MAX-участник",
        sport=sport, tariff=tariff, price=price, consent_at=consent_at,
    )

    token = image_att["payload"]["token"]
    caption = (
        f"Новая заявка на допуск (MAX)\n"
        f"User ID: {user_id}\n"
        f"Спорт: {sport}\nТариф: {tariff} — {price}₽\n"
        f"Строка в таблице: {row}"
    )
    if ORGANIZER_MAX_ID:
        await api.send_message(
            user_id=ORGANIZER_MAX_ID,
            text=caption,
            attachments=[{"type": "image", "payload": {"token": token}}, _admission_keyboard(row)],
        )
    await api.send_message(user_id=user_id, text="Заявка отправлена организатору на проверку. Жди подтверждения.")
    st.clear(user_id)
    return True


async def admission_ok(api, callback_id: str, payload: str):
    await api.answer_callback(callback_id, text="Обрабатываю...")
    row = int(payload.split(":")[1])
    rec = sheets.participants_ws.row_values(row)

    def _col(name, default=""):
        idx = sheets.PARTICIPANTS_HEADERS.index(name)
        return rec[idx] if len(rec) > idx else default

    telegram_field = _col("Telegram/контакт")
    participant_user_id = int(telegram_field.split(" ")[0])
    tariff = _col("Тариф")
    season = _col("Сезон", "S01")
    participant_id = f"DBL {season}-{str(row).zfill(4)}"
    sheets.set_participant_status(row, "Оплачено", participant_id)

    await api.send_message(
        user_id=participant_user_id,
        text=f"✅ Оплата подтверждена! Твой номер участника: {participant_id}.\n\nЧат участников: {GROUP_LINK}",
        attachments=[MAIN_KEYBOARD],
    )
    if tariff == "С медалью":
        await api.send_message(user_id=participant_user_id,
                                text="Твой тариф включает медаль — пришли, пожалуйста, адрес доставки "
                                     "(город, улица, дом, квартира, индекс, получатель).")
        sheets.set_participant_field(row, "Адрес получен?", "Ожидается")
        st.set_state(participant_user_id, "waiting_address")


async def admission_no(api, callback_id: str, payload: str):
    await api.answer_callback(callback_id, text="Обрабатываю...")
    row = int(payload.split(":")[1])
    rec = sheets.participants_ws.row_values(row)
    idx = sheets.PARTICIPANTS_HEADERS.index("Telegram/контакт")
    telegram_field = rec[idx] if len(rec) > idx else ""
    participant_user_id = int(telegram_field.split(" ")[0])
    sheets.set_participant_status(row, "Отклонено")
    await api.send_message(
        user_id=participant_user_id,
        text=f"❌ Оплата не подтверждена. Напиши организатору лично: {ORGANIZER_USERNAME}",
        attachments=[PAYMENT_KEYBOARD],
    )


async def handle_address_text(api, user_id: int, text: str, user_state):
    row, rec = sheets.find_participant_row(user_id)
    if rec and rec.get("Адрес получен?") == "Ожидается":
        sheets.set_participant_field(row, "Адрес получен?", "Да")
        sheets.set_participant_field(row, "Адрес доставки", text)
        await api.send_message(user_id=user_id, text="Адрес сохранён, спасибо!", attachments=[MAIN_KEYBOARD])
        if ORGANIZER_MAX_ID:
            await api.send_message(user_id=ORGANIZER_MAX_ID,
                                    text=f"📍 Получен адрес доставки от {rec.get('ID участника')}:\n{text}")
        st.set_state(user_id, None)


async def cmd_profile(api, user_id: int):
    row, rec = sheets.find_participant_row(user_id)
    if not rec or rec.get("Статус оплаты") != "Оплачено":
        await api.send_message(user_id=user_id, text="Ты ещё не допущен к сезону.")
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
    await api.send_message(user_id=user_id, text=text)


async def cmd_rules(api, user_id: int):
    await api.send_message(
        user_id=user_id,
        text=(
            "📋 <b>Правила сезона DBL</b>\n\n"
            "🗓 Сезон — 14 дней подряд\n"
            "🏃 Бег — минимум 2,5 км в день\n"
            "🚶 Ходьба — минимум 3 км в день\n\n"
            "📸 Каждый день отправляешь подтверждение тренировки до 23:59.\n\n"
            "❤️ У тебя 3 жизни. 🏅 Медаль получает тот, кто сохранил хотя бы 1 жизнь "
            "и имеет не более 3 незачтённых дней.\n\n"
            f"Вопросы — организатору: {ORGANIZER_USERNAME}"
        ),
    )
