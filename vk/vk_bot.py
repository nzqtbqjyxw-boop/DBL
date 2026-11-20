"""
VK-бот DBL. Полный дубль функционала Telegram-бота: допуск участника
(тариф/спорт/оплата), приём ежедневных отчётов, ручная проверка организатором
прямо во VK (отдельно от Telegram), жизни, медаль.

Все участники VK хранятся в ТОЙ ЖЕ Google-таблице, что и Telegram, но с
отдельным префиксом ID ("DBL-VK S01-0001") и отдельной колонкой "Платформа",
чтобы не путать и не дублировать логику поиска/нумерации.

Организатор получает карточки заявок и отчётов личным сообщением от
сообщества (VK_ORGANIZER_ID), с VK-клавиатурой вместо inline-кнопок (у VK
ботов в ЛС нет callback-кнопок с произвольными данными вроде Telegram
inline_keyboard — используем Keyboard с payload, см. ниже).
"""

import asyncio
import json
import logging
import os
from datetime import date

import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
from vk_api.utils import get_random_id

import sheets

log = logging.getLogger("vk_bot")

VK_GROUP_TOKEN = os.environ["VK_GROUP_TOKEN"]
VK_GROUP_ID = int(os.environ["VK_GROUP_ID"])
VK_ORGANIZER_ID = int(os.environ["VK_ORGANIZER_ID"])

PRICE_NO_MEDAL = 0
PRICE_WITH_MEDAL = 1390
PAYMENT_LINK_WITH_MEDAL = "https://yookassa.ru/my/i/au5A5aeec8yn/l"
GROUP_LINK = "https://t.me/+sK53VEAlDwo3YzVi"
ORGANIZER_CONTACT_NOTE = "Если есть вопрос — напиши в сообщения сообщества DBL."

OFFER_LINK = "https://telegra.ph/PUBLICHNAYA-OFERTA-11-03-27"
PRIVACY_POLICY_LINK = "https://telegra.ph/POLITIKA-OBRABOTKI-PERSONALNYH-DANNYH-DBL-11-03"
PD_CONSENT_LINK = "https://telegra.ph/SOGLASIE-NA-OBRABOTKU-PERSONALNYH-DANNYH-DBL-11-03"
PARENT_CONSENT_LINK = "https://telegra.ph/SOGLASIE-ZAKONNOGO-PREDSTAVITELYA-NA-UCHASTIE-NESOVERSHENNOLETNEGO-V-PROEKTE-DBL-11-03"

INSTRUCTION_LINK = "https://dbldobetterlish.tilda.ws"

PLATFORM = "VK"
VK_ID_PREFIX = "DBL-VK"

# ---------- Состояния участников (простой словарь в памяти процесса) ----------
# VK Bots API не даёт штатного FSM как aiogram — делаем по аналогии со
# state-машиной вручную: {user_id: {"state": "...", "data": {...}}}
_user_states: dict[int, dict] = {}

STATE_IDLE = "idle"
STATE_CHOOSING_AGE = "choosing_age"
STATE_WAITING_PARENT_CONSENT_PHOTO = "waiting_parent_consent_photo"
STATE_ACCEPTING_DOCUMENTS = "accepting_documents"
STATE_CHOOSING_SPORT = "choosing_sport"
STATE_CHOOSING_TARIFF = "choosing_tariff"
STATE_WAITING_PAYMENT_PROOF = "waiting_payment_proof"
STATE_WAITING_FREE_PHONE = "waiting_free_phone"
STATE_WAITING_REPORT_KM = "waiting_report_km"
STATE_WAITING_REPORT_PHOTO = "waiting_report_photo"

# Состояния организатора (при проверке конкретной заявки/отчёта)
STATE_ORG_WAITING_CONFIRM_KM = "org_waiting_confirm_km"
STATE_ORG_WAITING_CONFIRM_TIME = "org_waiting_confirm_time"


def _get_state(user_id: int) -> dict:
    return _user_states.setdefault(user_id, {"state": STATE_IDLE, "data": {}})


def _set_state(user_id: int, state: str, **data):
    entry = _get_state(user_id)
    entry["state"] = state
    entry["data"].update(data)


def _clear_state(user_id: int):
    _user_states[user_id] = {"state": STATE_IDLE, "data": {}}


# ---------- Клавиатуры ----------

def kb_main() -> str:
    kb = VkKeyboard(one_time=False)
    kb.add_button("Отправить отчёт", color=VkKeyboardColor.PRIMARY)
    kb.add_line()
    kb.add_button("Профиль", color=VkKeyboardColor.SECONDARY)
    kb.add_button("Правила", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def kb_payment() -> str:
    kb = VkKeyboard(one_time=False)
    kb.add_button("Подтвердить оплату", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("Правила", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def kb_age() -> str:
    kb = VkKeyboard(one_time=True)
    kb.add_button("Мне есть 18", color=VkKeyboardColor.PRIMARY)
    kb.add_line()
    kb.add_button("Мне 14-17", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def kb_documents() -> str:
    kb = VkKeyboard(one_time=True)
    kb.add_button("Принимаю все условия", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("Отмена", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def kb_sport() -> str:
    kb = VkKeyboard(one_time=True)
    kb.add_button("Бег (мин 2,5 км/день)", color=VkKeyboardColor.PRIMARY)
    kb.add_line()
    kb.add_button("Ходьба (мин 3 км/день)", color=VkKeyboardColor.PRIMARY)
    return kb.get_keyboard()


def kb_tariff() -> str:
    kb = VkKeyboard(one_time=True)
    kb.add_button("Без медали — бесплатно", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button(f"С медалью — {PRICE_WITH_MEDAL}₽", color=VkKeyboardColor.POSITIVE)
    return kb.get_keyboard()


def kb_report_decision(row: int) -> str:
    """Клавиатура для организатора при проверке отчёта. VK-кнопки не умеют
    нести произвольный payload с текстом и одновременно показываться как
    отдельное сообщение на каждую карточку, поэтому используем payload с
    JSON {"action": ..., "row": row} — обрабатываем в message_event/payload."""
    kb = VkKeyboard(inline=True)
    kb.add_button("Засчитать", color=VkKeyboardColor.POSITIVE,
                  payload={"action": "rep_ok", "row": row})
    kb.add_button("Не засчитать", color=VkKeyboardColor.NEGATIVE,
                  payload={"action": "rep_no", "row": row})
    kb.add_line()
    kb.add_button("Плохой скриншот", color=VkKeyboardColor.SECONDARY,
                  payload={"action": "rep_bad", "row": row})
    kb.add_button("Подозрение на обман", color=VkKeyboardColor.SECONDARY,
                  payload={"action": "rep_susp", "row": row})
    return kb.get_keyboard()


def kb_admission_decision(row: int) -> str:
    kb = VkKeyboard(inline=True)
    kb.add_button("Подтвердить", color=VkKeyboardColor.POSITIVE,
                  payload={"action": "adm_ok", "row": row})
    kb.add_button("Отклонить", color=VkKeyboardColor.NEGATIVE,
                  payload={"action": "adm_no", "row": row})
    kb.add_line()
    kb.add_button("Запросить ещё раз", color=VkKeyboardColor.SECONDARY,
                  payload={"action": "adm_retry", "row": row})
    return kb.get_keyboard()


def kb_skip_time() -> str:
    kb = VkKeyboard(inline=True)
    kb.add_button("Пропустить (время неизвестно)", color=VkKeyboardColor.SECONDARY,
                  payload={"action": "rep_time_skip"})
    return kb.get_keyboard()


def kb_parent_consent_decision(row: int) -> str:
    kb = VkKeyboard(inline=True)
    kb.add_button("Согласие получено", color=VkKeyboardColor.POSITIVE,
                  payload={"action": "pc_ok", "row": row})
    kb.add_line()
    kb.add_button("Запросить повторно", color=VkKeyboardColor.SECONDARY,
                  payload={"action": "pc_retry", "row": row})
    return kb.get_keyboard()


class VKBot:
    def __init__(self):
        self.vk_session = vk_api.VkApi(token=VK_GROUP_TOKEN)
        self.vk = self.vk_session.get_api()
        self.longpoll = VkBotLongPoll(self.vk_session, VK_GROUP_ID)

    # ---------- Утилиты отправки ----------

    def send(self, user_id: int, text: str, keyboard: str | None = None, attachment: str | None = None):
        params = {
            "user_id": user_id,
            "message": text,
            "random_id": get_random_id(),
        }
        if keyboard:
            params["keyboard"] = keyboard
        if attachment:
            params["attachment"] = attachment
        try:
            self.vk.messages.send(**params)
        except Exception:
            log.exception("Ошибка отправки сообщения VK user_id=%s", user_id)

    def upload_photo_for_message(self, user_id_source_chat: int, photo_attachment_from_event) -> str:
        """Пересылаем фото, присланное участником, организатору.
        Проще всего — переслать оригинальное сообщение (forward), это не
        требует повторной загрузки фото на сервер VK."""
        return photo_attachment_from_event

    # ---------- Поток допуска участника ----------

    def handle_start(self, user_id: int):
        row, rec = sheets.find_participant_by_platform_id(user_id, PLATFORM)
        if rec and rec.get("Статус оплаты") == "Оплачено":
            self.send(user_id, f"С возвращением! Твой номер участника: {rec.get('ID участника')}.",
                      keyboard=kb_main())
            return
        if rec and rec.get("Статус оплаты") == "Ожидает проверки":
            self.send(user_id, "Заявка на проверке у организатора. Жди подтверждения.")
            return
        self.send(
            user_id,
            "🏔️ DBL — Do Better Life\n\n"
            "Привет! Ты в боте сезона DBL — спортивного челленджа на 14 дней.\n\n"
            "Каждый день ты выполняешь норму (бег или ходьба), подтверждаешь тренировку. "
            "У тебя есть 3 жизни. Сохрани хотя бы одну до конца сезона — получишь медаль DBL.\n\n"
            "📱 Установи приложение-трекер (Strava, Nike Run Club, Adidas Running, "
            "Apple Здоровье, Google Fit), чтобы снимать скрин тренировки с маршрутом, датой, "
            "временем и км.\n\n"
            f"📖 Полная инструкция — в самом низу официального сайта:\n{INSTRUCTION_LINK}",
            keyboard=kb_payment(),
        )

    def handle_payment_flow_start(self, user_id: int):
        row, rec = sheets.find_participant_by_platform_id(user_id, PLATFORM)
        if rec and rec.get("Статус оплаты") == "Оплачено":
            self.send(user_id, f"Ты уже допущен к сезону. Твой номер: {rec.get('ID участника')}.",
                      keyboard=kb_main())
            return
        if rec and rec.get("Статус оплаты") == "Ожидает проверки":
            self.send(user_id, "Заявка уже на проверке у организатора. Жди подтверждения.")
            return
        if rec and sheets.is_yes(rec.get("Согласие родителя (14-17, да/нет)", "")):
            _set_state(user_id, STATE_ACCEPTING_DOCUMENTS)
            self._send_documents(user_id)
            return
        _set_state(user_id, STATE_CHOOSING_AGE)
        self.send(user_id, "Прежде чем продолжить — укажи свой возраст:", keyboard=kb_age())

    def _send_documents(self, user_id: int):
        self.send(
            user_id,
            "Перед продолжением ознакомься с документами:\n\n"
            f"📄 Публичная оферта: {OFFER_LINK}\n"
            f"🔒 Политика обработки персональных данных: {PRIVACY_POLICY_LINK}\n"
            f"✅ Согласие на обработку персональных данных: {PD_CONSENT_LINK}",
            keyboard=kb_documents(),
        )

    def handle_text(self, user_id: int, text: str, full_name: str, attachments: list):
        entry = _get_state(user_id)
        state = entry["state"]
        data = entry["data"]
        text_norm = text.strip()

        # --- глобальные кнопки меню, работают из любого состояния вне узких веток ---
        if text_norm == "Правила":
            self._send_rules(user_id)
            return
        if text_norm == "Профиль":
            self._send_profile(user_id)
            return
        if text_norm == "Подтвердить оплату":
            self.handle_payment_flow_start(user_id)
            return
        if text_norm == "Отправить отчёт":
            self._start_report_flow(user_id)
            return

        # --- состояния онбординга ---
        if state == STATE_CHOOSING_AGE:
            if text_norm == "Мне есть 18":
                data["age_choice"] = "adult"
                _set_state(user_id, STATE_ACCEPTING_DOCUMENTS)
                self._send_documents(user_id)
                return
            if text_norm == "Мне 14-17":
                data["age_choice"] = "teen"
                _set_state(user_id, STATE_WAITING_PARENT_CONSENT_PHOTO)
                self.send(
                    user_id,
                    "Участие с 14 до 17 лет возможно только при согласии родителя "
                    "(законного представителя).\n\n"
                    f"📄 Форма согласия: {PARENT_CONSENT_LINK}\n\n"
                    "Распечатать необязательно — можно переписать текст от руки на обычном листе "
                    "(ФИО ребёнка, дата рождения, согласие, дата, подпись родителя).\n\n"
                    "Когда родитель заполнит и подпишет — пришли сюда фото документа.",
                )
                return
            self.send(user_id, "Выбери один из вариантов на клавиатуре.", keyboard=kb_age())
            return

        if state == STATE_ACCEPTING_DOCUMENTS:
            if text_norm == "Принимаю все условия":
                data["docs_accepted_at"] = sheets.now_str()
                _set_state(user_id, STATE_CHOOSING_SPORT)
                self.send(user_id, "Условия приняты. Выбери вид спорта:", keyboard=kb_sport())
                return
            if text_norm == "Отмена":
                _clear_state(user_id)
                self.send(user_id, "Хорошо, регистрация отменена. Когда будешь готов — "
                                    "нажми «Подтвердить оплату» ещё раз.", keyboard=kb_payment())
                return
            self.send(user_id, "Выбери один из вариантов на клавиатуре.", keyboard=kb_documents())
            return

        if state == STATE_CHOOSING_SPORT:
            if text_norm.startswith("Бег"):
                data["sport"] = "Бег"
            elif text_norm.startswith("Ходьба"):
                data["sport"] = "Ходьба"
            else:
                self.send(user_id, "Выбери вариант на клавиатуре.", keyboard=kb_sport())
                return
            _set_state(user_id, STATE_CHOOSING_TARIFF)
            self.send(user_id, f"Вид спорта: {data['sport']}\n\nТеперь выбери тариф:", keyboard=kb_tariff())
            return

        if state == STATE_CHOOSING_TARIFF:
            if text_norm.startswith("Без медали"):
                data["tariff"] = "Без медали"
                _set_state(user_id, STATE_WAITING_FREE_PHONE, **data)
                self.send(
                    user_id,
                    f"Тариф: Без медали — бесплатно 🆓\n\n"
                    "Последний шаг — пришли номер телефона текстом (нужно, чтобы отличать "
                    "реальных участников от спама), например +79001234567.",
                )
                return
            if text_norm.startswith("С медалью"):
                data["tariff"] = "С медалью"
                data["price"] = PRICE_WITH_MEDAL
                _set_state(user_id, STATE_WAITING_PAYMENT_PROOF, **data)
                self.send(
                    user_id,
                    f"Тариф: С медалью\nЦена: {PRICE_WITH_MEDAL}₽\n\n"
                    f"Оплати по ссылке: {PAYMENT_LINK_WITH_MEDAL}\n\n"
                    "После оплаты пришли сюда чек/скриншот оплаты и укажи ФИО, "
                    "которое использовал при оплате.",
                )
                return
            self.send(user_id, "Выбери вариант на клавиатуре.", keyboard=kb_tariff())
            return

        if state == STATE_WAITING_FREE_PHONE:
            digits = "".join(ch for ch in text_norm if ch.isdigit())
            if not (10 <= len(digits) <= 15):
                self.send(user_id, "Это не похоже на номер телефона. Пришли номер текстом, "
                                    "например +79001234567.")
                return
            self._register_free_participant(user_id, full_name, phone=text_norm, data=data)
            return

        if state == STATE_WAITING_PAYMENT_PROOF:
            if attachments:
                self._handle_payment_proof(user_id, full_name, attachments, data)
                return
            self.send(user_id, "Пришли, пожалуйста, фото чека/скриншота оплаты.")
            return

        if state == STATE_WAITING_REPORT_KM:
            try:
                km = float(text_norm.replace(",", "."))
            except ValueError:
                self.send(user_id, "Нужно число, например 3.2. Попробуй ещё раз.")
                return
            data["km"] = km
            _set_state(user_id, STATE_WAITING_REPORT_PHOTO, **data)
            self.send(user_id, "Теперь пришли скриншот/фото тренировки (маршрут, дата, км).")
            return

        if state == STATE_WAITING_REPORT_PHOTO:
            if attachments:
                self._handle_report_photo(user_id, attachments, data)
                return
            self.send(user_id, "Нужен именно скриншот/фото тренировки.")
            return

        # адрес доставки (свободный текст) — если участник допущен по тарифу
        # "С медалью" и адрес ещё не получен, любое текстовое сообщение вне
        # распознанных состояний считаем адресом
        row, rec = sheets.find_participant_by_platform_id(user_id, PLATFORM)
        if rec and rec.get("Адрес получен?") == "Ожидается":
            sheets.set_participant_field(row, "Адрес получен?", "Да")
            sheets.set_participant_field(row, "Адрес доставки", text_norm)
            self.send(user_id, "Адрес сохранён, спасибо!", keyboard=kb_main())
            self.send(VK_ORGANIZER_ID,
                      f"📍 Получен адрес доставки (VK) от {rec.get('ID участника')}:\n{text_norm}")
            return

        # ничего не подошло — просто покажем меню
        self.send(user_id, "Не понял сообщение. Используй кнопки меню.", keyboard=kb_main())

    def _register_free_participant(self, user_id: int, full_name: str, phone: str, data: dict):
        sport = data.get("sport", "")
        tariff = data.get("tariff", "")
        consent_at = data.get("docs_accepted_at", "")
        age_choice = data.get("age_choice", "")

        row = sheets.add_pending_participant_vk(
            vk_id=user_id, full_name=full_name, sport=sport, tariff=tariff,
            price=PRICE_NO_MEDAL, consent_at=consent_at, phone=phone,
            is_minor=(age_choice == "teen"),
        )
        season = "S01"
        participant_id = f"{VK_ID_PREFIX} {season}-{str(row).zfill(4)}"
        sheets.set_participant_status(row, "Оплачено", participant_id)

        self.send(
            user_id,
            f"✅ Готово! Твой номер участника: {participant_id}.\n\n"
            f"Присоединяйся к чату участников:\n{GROUP_LINK}",
            keyboard=kb_main(),
        )
        self.send(
            VK_ORGANIZER_ID,
            f"🆓 Новый бесплатный участник (VK)\n"
            f"Имя: {full_name} (vk.com/id{user_id})\n"
            f"Телефон: {phone}\n"
            f"Спорт: {sport}\n"
            f"Номер: {participant_id}\n"
            f"Строка: {row}",
        )
        _clear_state(user_id)

    def _handle_payment_proof(self, user_id: int, full_name: str, attachments: list, data: dict):
        sport = data.get("sport", "")
        tariff = data.get("tariff", "")
        price = data.get("price", "")
        consent_at = data.get("docs_accepted_at", "")
        age_choice = data.get("age_choice", "")
        retry_row = data.get("retry_row")

        if retry_row:
            row = retry_row
            sheets.set_participant_status(row, "Ожидает проверки")
        else:
            row = sheets.add_pending_participant_vk(
                vk_id=user_id, full_name=full_name, sport=sport, tariff=tariff,
                price=price, consent_at=consent_at, is_minor=(age_choice == "teen"),
            )

        caption = (
            f"Новая заявка на допуск (VK)\n"
            f"Имя: {full_name} (vk.com/id{user_id})\n"
            f"Спорт: {sport}\n"
            f"Тариф: {tariff} — {price}₽\n"
            f"Строка в таблице: {row}"
        )
        photo_attach = attachments[0] if attachments else None
        self.send(VK_ORGANIZER_ID, caption, keyboard=kb_admission_decision(row), attachment=photo_attach)
        self.send(user_id, "Заявка отправлена организатору на проверку. Жди подтверждения.")
        _clear_state(user_id)

    def _send_rules(self, user_id: int):
        self.send(
            user_id,
            "📋 Правила сезона DBL\n\n"
            "🗓 Сезон — 14 дней подряд\n"
            "🏃 Бег — минимум 2,5 км в день\n"
            "🚶 Ходьба — минимум 3 км в день\n\n"
            "📸 Каждый день отправляешь подтверждение тренировки (фото/скрин с датой, "
            "маршрутом, километражем) до 23:59.\n\n"
            "❤️ У тебя 3 жизни. Пропустил норму — минус жизнь. Если докажешь, что "
            "тренировка была выполнена в нужный день, до конца сезона — жизнь вернётся.\n\n"
            "🏅 Медаль получает тот, кто сохранил хотя бы 1 жизнь и имеет не более "
            "3 незачтённых дней за сезон.\n\n"
            f"{ORGANIZER_CONTACT_NOTE}",
        )

    def _send_profile(self, user_id: int):
        row, rec = sheets.find_participant_by_platform_id(user_id, PLATFORM)
        if not rec or rec.get("Статус оплаты") != "Оплачено":
            self.send(user_id, "Ты ещё не допущен к сезону.")
            return
        lives = sheets.get_lives(rec.get("ID участника"))
        season_day = sheets.get_season_day()
        day_line = f"День сезона: {season_day}/14\n" if season_day else "Сезон ещё не начался\n"
        hearts = "❤️" * lives + "🖤" * (3 - lives)
        text = (
            f"👤 Твой профиль DBL\n\n"
            f"Номер участника: {rec.get('ID участника')}\n"
            f"Вид спорта: {rec.get('Спорт') or '—'}\n"
            f"Тариф: {rec.get('Тариф') or '—'}\n"
            f"{day_line}"
            f"Жизни: {hearts} ({lives}/3)\n"
        )
        if rec.get("Тариф") == "С медалью":
            addr_status = rec.get("Адрес получен?", "Нет")
            text += f"Адрес доставки: {addr_status}\n"
        self.send(user_id, text)

    # ---------- Поток ежедневных отчётов ----------

    def _start_report_flow(self, user_id: int):
        row, rec = sheets.find_participant_by_platform_id(user_id, PLATFORM)
        if not rec or rec.get("Статус оплаты") != "Оплачено":
            self.send(user_id, "Ты ещё не допущен к сезону. Сначала пройди подтверждение оплаты.")
            return
        participant_id = rec.get("ID участника")
        today = date.today().isoformat()
        _, status, attempts = sheets.get_today_report_status(participant_id, today)

        if status == "На проверке":
            self.send(user_id, "⏳ Твой отчёт за сегодня уже на проверке у организатора. "
                                "Дождись решения, прежде чем отправлять новый.")
            return
        if status == "Засчитано":
            self.send(user_id, "Отчёт за сегодня уже засчитан.")
            return
        if status in ("Не засчитано", "Подозрение на обман", "Плохой скриншот"):
            _set_state(user_id, STATE_WAITING_REPORT_PHOTO, participant_id=participant_id,
                       season=rec.get("Сезон"), sport=rec.get("Спорт"), extra_attempt=True, km=None)
            self.send(user_id, "Пришли ещё один скриншот этой же тренировки.")
            return

        _set_state(user_id, STATE_WAITING_REPORT_KM, participant_id=participant_id,
                   season=rec.get("Сезон"), sport=rec.get("Спорт"))
        self.send(user_id, "Сколько километров сегодня? Пришли число (например 3.2).")

    def _handle_report_photo(self, user_id: int, attachments: list, data: dict):
        today = date.today().isoformat()
        is_extra = bool(data.get("extra_attempt"))
        season_day = sheets.get_season_day()
        day_str = str(season_day) if season_day else ""
        km_value = data.get("km")

        photo_attach = attachments[0]
        row, sent_at, attempt = sheets.add_report(
            participant_id=data["participant_id"], season=data["season"], day=day_str,
            training_date=today, sport=data.get("sport", ""),
            km=km_value if km_value is not None else "", file_id=photo_attach,
        )
        km_display = km_value if km_value is not None else "—"

        caption = (
            f"{'Это ' + str(attempt) + ' скриншот за сегодня' if is_extra else 'Новый отчёт на проверку (VK)'}\n"
            f"Участник: {data['participant_id']}\n"
            f"Дата: {today}\n"
            f"Время отправки: {sent_at}\n"
            f"Попытка: {attempt}\n"
            f"Км (заявлено участником): {km_display}\n"
            f"Строка: {row}"
        )
        self.send(VK_ORGANIZER_ID, caption, keyboard=kb_report_decision(row), attachment=photo_attach)
        self.send(user_id, f"Отчёт отправлен на проверку организатору ({sent_at}).")
        _clear_state(user_id)

    # ---------- Решения организатора (payload-кнопки) ----------

    def handle_organizer_payload(self, user_id: int, payload: dict, text: str):
        if user_id != VK_ORGANIZER_ID:
            return  # защита: решения принимает только организатор
        action = payload.get("action")
        row = payload.get("row")

        if action == "rep_ok":
            _set_state(VK_ORGANIZER_ID, STATE_ORG_WAITING_CONFIRM_KM, review_row=row)
            self.send(VK_ORGANIZER_ID, "Сколько км видно на скриншоте? Пришли число (например 3.2).")
            return

        if action == "rep_no":
            self._report_decision_simple(row, "Не засчитано", decrement_life=True)
            return

        if action == "rep_bad":
            self._report_decision_simple(row, "Плохой скриншот", decrement_life=False,
                                          comment="Запрошена повторная отправка",
                                          participant_note="📷 Скриншот плохого качества — пришли, "
                                                            "пожалуйста, более чёткое фото этой же "
                                                            "тренировки через «Отправить отчёт».")
            return

        if action == "rep_susp":
            self._report_decision_simple(row, "Подозрение на обман", decrement_life=False,
                                          comment="Требуется ручная проверка организатором",
                                          participant_note="⚠️ Организатор отметил твой отчёт как "
                                                            "требующий дополнительной проверки. "
                                                            "Ничего делать не нужно.")
            return

        if action == "adm_ok":
            self._admission_ok(row)
            return
        if action == "adm_no":
            self._admission_no(row)
            return
        if action == "adm_retry":
            self._admission_retry(row)
            return

        if action == "pc_ok":
            self._parent_consent_ok(row)
            return
        if action == "pc_retry":
            self._parent_consent_retry(row)
            return

        if action == "rep_time_skip":
            self._finish_report_ok(training_time="")
            return

    def handle_organizer_text(self, user_id: int, text: str) -> bool:
        """Обрабатывает текстовый ввод организатора в узких состояниях
        (км/время тренировки при "Засчитать"). Возвращает True, если
        сообщение было обработано как часть такого потока."""
        if user_id != VK_ORGANIZER_ID:
            return False
        entry = _get_state(VK_ORGANIZER_ID)
        state = entry["state"]

        if state == STATE_ORG_WAITING_CONFIRM_KM:
            try:
                km = float(text.strip().replace(",", "."))
            except ValueError:
                self.send(VK_ORGANIZER_ID, "Нужно число, например 3.2. Попробуй ещё раз.")
                return True
            entry["data"]["confirmed_km"] = km
            _set_state(VK_ORGANIZER_ID, STATE_ORG_WAITING_CONFIRM_TIME)
            self.send(VK_ORGANIZER_ID,
                      "Сколько длилась тренировка (время)? Например 00:25:30, или нажми «Пропустить».",
                      keyboard=kb_skip_time())
            return True

        if state == STATE_ORG_WAITING_CONFIRM_TIME:
            self._finish_report_ok(training_time=text.strip())
            return True

        return False

    def _finish_report_ok(self, training_time: str):
        entry = _get_state(VK_ORGANIZER_ID)
        row = entry["data"].get("review_row")
        confirmed_km = entry["data"].get("confirmed_km")
        try:
            values = sheets.find_report_row(row)
            participant_id = values[0]
            lives_before = int(values[8]) if len(values) > 8 and values[8] else 3
            lives_after = lives_before

            sheets.update_report_decision(row, "Засчитано", lives_after,
                                           confirmed_km=confirmed_km, training_time=training_time)

            vk_user_id = sheets.get_vk_id_by_participant(participant_id)
            if vk_user_id:
                self.send(vk_user_id, "✅ Твой отчёт засчитан!")
            self.send(VK_ORGANIZER_ID,
                      f"✅ ЗАСЧИТАНО (строка {row}): км: {confirmed_km}, время: {training_time or '—'}")
        except Exception:
            log.exception("Ошибка в _finish_report_ok (VK)")
            self.send(VK_ORGANIZER_ID, "⚠️ Ошибка при обработке: см. логи Amvera")
        finally:
            _clear_state(VK_ORGANIZER_ID)

    def _report_decision_simple(self, row: int, status: str, decrement_life: bool,
                                 comment: str = "", participant_note: str = ""):
        try:
            values = sheets.find_report_row(row)
            participant_id = values[0]
            lives_before = int(values[8]) if len(values) > 8 and values[8] else 3
            lives_after = max(lives_before - 1, 0) if decrement_life else lives_before

            sheets.update_report_decision(row, status, lives_after, comment)

            vk_user_id = sheets.get_vk_id_by_participant(participant_id)
            if vk_user_id and participant_note:
                note = participant_note
                if decrement_life:
                    note = f"❌ Отчёт не засчитан. Осталось жизней: {lives_after}."
                self.send(vk_user_id, note)
            elif vk_user_id and decrement_life:
                self.send(vk_user_id, f"❌ Отчёт не засчитан. Осталось жизней: {lives_after}.")
            self.send(VK_ORGANIZER_ID, f"Готово: {status} (строка {row}).")
        except Exception:
            log.exception("Ошибка в _report_decision_simple (VK)")
            self.send(VK_ORGANIZER_ID, "⚠️ Ошибка при обработке: см. логи Amvera")

    def _admission_ok(self, row: int):
        try:
            rec_values = sheets.participants_ws.row_values(row)

            def _col(name, default=""):
                idx = sheets.PARTICIPANTS_HEADERS.index(name)
                return rec_values[idx] if len(rec_values) > idx else default

            vk_id_raw = _col("Telegram/контакт")  # для VK храним "vk:<id> (<имя>)"
            vk_user_id = sheets.parse_vk_id(vk_id_raw)
            tariff = _col("Тариф")
            season = _col("Сезон", "S01")

            participant_id = f"{VK_ID_PREFIX} {season}-{str(row).zfill(4)}"
            sheets.set_participant_status(row, "Оплачено", participant_id)

            if vk_user_id:
                self.send(vk_user_id,
                          f"✅ Оплата подтверждена! Твой номер участника: {participant_id}.\n\n"
                          f"Присоединяйся к чату участников:\n{GROUP_LINK}",
                          keyboard=kb_main())
                if tariff == "С медалью":
                    self.send(vk_user_id, "Твой тариф включает медаль — пришли, пожалуйста, адрес "
                                           "доставки (город, улица, дом, квартира, индекс, получатель).")
                    sheets.set_participant_field(row, "Адрес получен?", "Ожидается")

            self.send(VK_ORGANIZER_ID, f"✅ ПОДТВЕРЖДЕНО: {participant_id} (строка {row})")
        except Exception:
            log.exception("Ошибка в _admission_ok (VK)")
            self.send(VK_ORGANIZER_ID, "⚠️ Ошибка при обработке: см. логи Amvera")

    def _admission_no(self, row: int):
        try:
            rec_values = sheets.participants_ws.row_values(row)
            idx = sheets.PARTICIPANTS_HEADERS.index("Telegram/контакт")
            vk_id_raw = rec_values[idx] if len(rec_values) > idx else ""
            vk_user_id = sheets.parse_vk_id(vk_id_raw)
            sheets.set_participant_status(row, "Отклонено")
            if vk_user_id:
                self.send(vk_user_id, f"❌ Оплата не подтверждена. {ORGANIZER_CONTACT_NOTE}\n\n"
                                       "Если хочешь попробовать снова — нажми «Подтвердить оплату».",
                          keyboard=kb_payment())
            self.send(VK_ORGANIZER_ID, f"❌ ОТКЛОНЕНО (строка {row})")
        except Exception:
            log.exception("Ошибка в _admission_no (VK)")
            self.send(VK_ORGANIZER_ID, "⚠️ Ошибка при обработке: см. логи Amvera")

    def _admission_retry(self, row: int):
        try:
            rec_values = sheets.participants_ws.row_values(row)

            def _col(name, default=""):
                idx = sheets.PARTICIPANTS_HEADERS.index(name)
                return rec_values[idx] if len(rec_values) > idx else default

            vk_id_raw = _col("Telegram/контакт")
            vk_user_id = sheets.parse_vk_id(vk_id_raw)
            if vk_user_id:
                _set_state(vk_user_id, STATE_WAITING_PAYMENT_PROOF,
                           sport=_col("Спорт"), tariff=_col("Тариф"),
                           price=_col("Сумма оплаты, ₽"), retry_row=row)
                self.send(vk_user_id, "Пришли, пожалуйста, более чёткий чек/скриншот оплаты.")
            self.send(VK_ORGANIZER_ID, f"🔁 ЗАПРОШЕНО ПОВТОРНО (строка {row})")
        except Exception:
            log.exception("Ошибка в _admission_retry (VK)")
            self.send(VK_ORGANIZER_ID, "⚠️ Ошибка при обработке: см. логи Amvera")

    def _parent_consent_ok(self, row: int):
        try:
            sheets.set_participant_field(row, "Согласие родителя (14-17, да/нет)", "Да")
            rec_values = sheets.participants_ws.row_values(row)
            idx = sheets.PARTICIPANTS_HEADERS.index("Telegram/контакт")
            vk_id_raw = rec_values[idx] if len(rec_values) > idx else ""
            vk_user_id = sheets.parse_vk_id(vk_id_raw)
            if vk_user_id:
                _set_state(vk_user_id, STATE_ACCEPTING_DOCUMENTS)
                self.send(vk_user_id, "✅ Согласие родителя подтверждено! Теперь нужно принять условия участия.")
                self._send_documents(vk_user_id)
            self.send(VK_ORGANIZER_ID, f"✅ СОГЛАСИЕ ПОДТВЕРЖДЕНО (строка {row})")
        except Exception:
            log.exception("Ошибка в _parent_consent_ok (VK)")
            self.send(VK_ORGANIZER_ID, "⚠️ Ошибка при обработке: см. логи Amvera")

    def _parent_consent_retry(self, row: int):
        try:
            rec_values = sheets.participants_ws.row_values(row)
            idx = sheets.PARTICIPANTS_HEADERS.index("Telegram/контакт")
            vk_id_raw = rec_values[idx] if len(rec_values) > idx else ""
            vk_user_id = sheets.parse_vk_id(vk_id_raw)
            if vk_user_id:
                _set_state(vk_user_id, STATE_WAITING_PARENT_CONSENT_PHOTO)
                self.send(vk_user_id, "Фото согласия плохо видно или заполнено не полностью. "
                                       "Пришли, пожалуйста, более чёткое фото документа ещё раз.")
            self.send(VK_ORGANIZER_ID, f"🔁 ЗАПРОШЕНО ПОВТОРНО (строка {row})")
        except Exception:
            log.exception("Ошибка в _parent_consent_retry (VK)")
            self.send(VK_ORGANIZER_ID, "⚠️ Ошибка при обработке: см. логи Amvera")

    # ---------- Фото согласия родителя ----------

    def handle_parent_consent_photo(self, user_id: int, full_name: str, attachments: list):
        row, rec = sheets.find_participant_by_platform_id(user_id, PLATFORM)
        if row is None:
            row = sheets.add_pending_participant_vk(
                vk_id=user_id, full_name=full_name, sport="", tariff="", price="",
            )
            sheets.set_participant_status(row, "Ожидает проверки (согласие родителя)")

        caption = f"Согласие родителя (VK) — {full_name} (vk.com/id{user_id})\nСтрока в таблице: {row}"
        photo_attach = attachments[0] if attachments else None
        self.send(VK_ORGANIZER_ID, caption, keyboard=kb_parent_consent_decision(row), attachment=photo_attach)
        self.send(user_id, "Фото отправлено организатору на проверку. Жди подтверждения — "
                            "после него продолжим регистрацию.")
        _clear_state(user_id)

    # ---------- Главный цикл ----------

    def run(self):
        log.info("VK bot: запуск Long Poll")
        for event in self.longpoll.listen():
            try:
                self._dispatch(event)
            except Exception:
                log.exception("Ошибка обработки события VK")

    def _dispatch(self, event):
        if event.type != VkBotEventType.MESSAGE_NEW:
            return
        message = event.object.message
        user_id = message["from_id"]
        text = message.get("text", "")
        attachments_raw = message.get("attachments", [])
        payload_raw = message.get("payload")

        # фото/документы -> строка attachment для пересылки/сохранения file_id
        attachments = []
        for att in attachments_raw:
            if att["type"] == "photo":
                photo = att["photo"]
                owner_id = photo["owner_id"]
                photo_id = photo["id"]
                access_key = photo.get("access_key", "")
                attach_str = f"photo{owner_id}_{photo_id}"
                if access_key:
                    attach_str += f"_{access_key}"
                attachments.append(attach_str)
            elif att["type"] == "doc":
                doc = att["doc"]
                attachments.append(f"doc{doc['owner_id']}_{doc['id']}")

        # payload с кнопки (inline-кнопки организатора)
        if payload_raw:
            try:
                payload = json.loads(payload_raw)
            except (TypeError, ValueError):
                payload = None
            if payload and "action" in payload:
                self.handle_organizer_payload(user_id, payload, text)
                return

        # узкие состояния организатора (ввод км/времени после "Засчитать")
        if self.handle_organizer_text(user_id, text):
            return

        # команда старта
        if text.strip().lower() in ("начать", "старт", "start", "/start"):
            self.handle_start(user_id)
            return

        # фото согласия родителя
        entry = _get_state(user_id)
        if entry["state"] == STATE_WAITING_PARENT_CONSENT_PHOTO and attachments:
            full_name = self._get_full_name(user_id)
            self.handle_parent_consent_photo(user_id, full_name, attachments)
            return

        full_name = self._get_full_name(user_id)
        self.handle_text(user_id, text, full_name, attachments)

    def _get_full_name(self, user_id: int) -> str:
        try:
            info = self.vk.users.get(user_ids=user_id)
            if info:
                return f"{info[0]['first_name']} {info[0]['last_name']}"
        except Exception:
            log.exception("Не удалось получить имя пользователя VK %s", user_id)
        return str(user_id)


def run_vk_bot_blocking():
    """Синхронная точка входа — запускается в отдельном потоке/процессе,
    т.к. vk_api.longpoll блокирующий (не asyncio).
    Любое исключение (неверный токен, неверный group_id, сетевая ошибка при
    первом запросе к Long Poll серверу) логируется явно — без этого
    исключение в отдельном потоке теряется молча."""
    log.info("VK bot: инициализация (group_id=%s)", VK_GROUP_ID)
    try:
        bot = VKBot()
        log.info("VK bot: VkApi и VkBotLongPoll созданы успешно, начинаю слушать события")
        bot.run()
    except Exception:
        log.exception("VK bot: КРИТИЧЕСКАЯ ОШИБКА — бот остановлен")


async def run_vk_bot_async():
    """Асинхронная обёртка для запуска вместе с aiogram через asyncio.gather:
    блокирующий Long Poll уводим в отдельный поток, чтобы не стопорить
    событийный цикл Telegram-бота."""
    log.info("VK bot: запуск фоновой задачи run_vk_bot_async")
    await asyncio.to_thread(run_vk_bot_blocking)
