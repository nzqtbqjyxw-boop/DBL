import json
import os
import threading
from datetime import datetime, timezone, timedelta

import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SHEET_ID = os.environ["SHEET_ID"]
creds_dict = json.loads(os.environ["GOOGLE_CREDS_JSON"])
creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SHEET_ID)

participants_ws = sh.worksheet("Участники")
reports_ws = sh.worksheet("Отчёты")

_write_lock = threading.Lock()

MSK = timezone(timedelta(hours=3))

PARTICIPANTS_HEADERS = [
    "ID участника", "Telegram/контакт", "Email", "Сезон", "Спорт", "Тариф",
    "Сумма оплаты, ₽", "Статус оплаты", "Дата оплаты", "Право на медаль",
    "Адрес получен?", "Дата отправки", "Трек-номер", "Примечания",
    "Адрес доставки", "Согласие с офертой (дата/время)",
    "Согласие на обработку ПДн (дата/время)", "Согласие родителя (14-17, да/нет)",
    "Пригласил (чей реф.код)", "Приглашено друзей (оплативших)",
    "Реф-статус на след.сезон", "Телефон",
]

REPORTS_HEADERS = [
    "ID участника", "Сезон", "День", "Дата тренировки", "Вид спорта",
    "Км", "Скриншот (file_id)", "Статус", "Жизни до", "Жизни после",
    "Комментарий организатора", "Дата решения", "Время отправки", "Попытка №",
    "Км (подтверждено)", "Время тренировки",
]

PENDING_STATUSES = ("На проверке",)
FINAL_STATUSES = ("Засчитано", "Не засчитано", "Подозрение на обман")
MAX_ATTEMPTS_PER_DAY = 3

SEASON_START_DATE = datetime.strptime("2026-11-14", "%Y-%m-%d")

# Последняя колонка листа "Участники" в A1-нотации (должна соответствовать
# длине PARTICIPANTS_HEADERS). Сейчас 22 колонки -> V.
PARTICIPANTS_LAST_COL = "V"

# Последняя колонка листа "Отчёты" в A1-нотации.
# Сейчас 16 колонок -> P.
REPORTS_LAST_COL = "P"

# Пороги реферальной программы: сколько оплативших друзей нужно пригласить,
# чтобы получить соответствующий статус на СЛЕДУЮЩИЙ сезон.
REFERRAL_TIERS = [
    (4, "Бесплатно"),
    (1, "Скидка 1100₽"),
]


def now_str():
    return datetime.now(MSK).strftime("%Y-%m-%d %H:%M:%S")


def get_season_day():
    """Возвращает день сезона (1-14) или None если сезон ещё не начался."""
    today = datetime.now(MSK).date()
    start = SEASON_START_DATE.date()
    if today < start:
        return None
    day = (today - start).days + 1
    return day if day <= 14 else None


def _norm(value) -> str:
    """Нормализует значение для регистронезависимого сравнения (да/нет и т.п.)."""
    return str(value or "").strip().lower()


def is_yes(value) -> bool:
    return _norm(value) in ("да", "yes", "true", "1")


# ---------- Участники ----------

def get_all_participants():
    return participants_ws.get_all_records()


def set_participant_field(row_index: int, field: str, value):
    col = PARTICIPANTS_HEADERS.index(field) + 1
    participants_ws.update_cell(row_index, col, value)


def find_participant_row(telegram_id: str):
    """Возвращает (row_index, record) по Telegram ID, либо (None, None).
    Поле хранится как "<id> (<имя>)", поэтому сравниваем только часть до пробела.
    Если у одного Telegram ID оказалось НЕСКОЛЬКО строк (например, человек
    сначала начал регистрацию, она не завершилась, и позже создалась вторая
    строка) — возвращаем ПОСЛЕДНЮЮ (самую свежую), а из найденных строк
    приоритет отдаём той, где "Статус оплаты" == "Оплачено", если такая есть."""
    records = participants_ws.get_all_records()
    target = str(telegram_id).strip()
    matches = []
    for i, rec in enumerate(records, start=2):  # строка 1 — заголовок
        field = str(rec.get("Telegram/контакт", "")).strip()
        field_id = field.split(" ")[0] if field else ""
        if field_id == target:
            matches.append((i, rec))
    if not matches:
        return None, None
    for i, rec in matches:
        if rec.get("Статус оплаты") == "Оплачено":
            return i, rec
    return matches[-1]


def find_participant_by_id(participant_id: str):
    """Возвращает (row_index, record) по "ID участника" (DBL S01-0042), либо (None, None)."""
    records = participants_ws.get_all_records()
    target = str(participant_id).strip()
    for i, rec in enumerate(records, start=2):
        if str(rec.get("ID участника", "")).strip() == target:
            return i, rec
    return None, None


def add_pending_participant(telegram_id: str, username: str, sport: str, tariff: str, price: int,
                             season: str = "S01", address: str = "", consent_at: str = "",
                             pdn_consent_at: str = "", is_minor: bool = False,
                             referred_by: str = "", phone: str = ""):
    """
    Заполняет первую пустую заготовку (Telegram/контакт пуст, Сезон совпадает)
    или, если заготовок не осталось, добавляет новую строку в конец.
    Записывает также спорт/тариф/сумму/адрес/согласие с офертой/согласие на ПДн/
    кто пригласил/телефон.
    Если участник несовершеннолетний (14-17), колонка "Согласие родителя" ставится
    в "Ожидается" — пустая строка означает "вопрос ещё не возникал" (18+).
    Возвращает номер строки.
    """
    telegram_value = f"{telegram_id} ({username})"
    with _write_lock:
        records = participants_ws.get_all_records()
        row_index = None
        for i, rec in enumerate(records, start=2):  # строка 1 — заголовок
            telegram_field = str(rec.get("Telegram/контакт", "")).strip()
            rec_season = str(rec.get("Сезон", "")).strip()
            if telegram_field == "" and rec_season == season:
                row_index = i
                break

        values = {
            "Telegram/контакт": telegram_value,
            "Спорт": sport,
            "Тариф": tariff,
            "Сумма оплаты, ₽": price,
            "Статус оплаты": "Ожидает проверки",
        }
        if address:
            values["Адрес доставки"] = address
        if consent_at:
            values["Согласие с офертой (дата/время)"] = consent_at
        if pdn_consent_at:
            values["Согласие на обработку ПДн (дата/время)"] = pdn_consent_at
        if is_minor:
            values["Согласие родителя (14-17, да/нет)"] = "Ожидается"
        if referred_by:
            values["Пригласил (чей реф.код)"] = referred_by
        if phone:
            values["Телефон"] = phone

        if row_index is not None:
            for col_name, value in values.items():
                participants_ws.update_cell(row_index, PARTICIPANTS_HEADERS.index(col_name) + 1, value)
            return row_index

        row = [
            "", telegram_value, "", season, sport, tariff, price,
            "Ожидает проверки", "", "Не определено", "Нет", "", "", "",
            address, consent_at, pdn_consent_at,
            "Ожидается" if is_minor else "",
            referred_by, "", "", phone,
        ]
        last_used = len(records) + 1  # +1 за заголовок
        row_index = last_used + 1
        participants_ws.update(
            f"A{row_index}:{PARTICIPANTS_LAST_COL}{row_index}",
            [row], value_input_option="USER_ENTERED",
        )
        return row_index


def set_participant_status(row_index: int, status: str, participant_id: str = None):
    updates = {"Статус оплаты": status}
    if participant_id:
        updates["ID участника"] = participant_id
    for col_name, value in updates.items():
        col = PARTICIPANTS_HEADERS.index(col_name) + 1
        participants_ws.update_cell(row_index, col, value)


def has_parent_consent(rec: dict) -> bool:
    """True, если согласие родителя не требуется (поле пустое, участник 18+)
    либо получено и подтверждено организатором."""
    value = rec.get("Согласие родителя (14-17, да/нет)", "")
    if value == "":
        return True
    return is_yes(value)


def get_lives(participant_id: str) -> int:
    """Текущие жизни = 3 минус кол-во незачтённых дней без возврата, по логу Отчёты."""
    records = reports_ws.get_all_records()
    lives = 3
    for rec in records:
        if str(rec.get("ID участника")) == str(participant_id):
            status = rec.get("Статус")
            if status in ("Не засчитано",):
                lives -= 1
            elif status == "Засчитано" and rec.get("Жизни после") not in ("", None):
                try:
                    lives = int(rec.get("Жизни после"))
                except ValueError:
                    pass
    return max(lives, 0)


def referral_tier_for_count(count: int) -> str:
    """Возвращает текст реф-статуса для данного числа оплативших друзей,
    либо "" если порог ещё не достигнут."""
    for threshold, label in REFERRAL_TIERS:
        if count >= threshold:
            return label
    return ""


def increment_referral_count(referrer_participant_id: str):
    """Находит реферера по его "ID участника", увеличивает счётчик
    "Приглашено друзей (оплативших)" на 1 и пересчитывает реф-статус
    на следующий сезон. Возвращает (row_index, новый_счётчик, новый_статус)
    либо (None, None, None), если реферер не найден."""
    row, rec = find_participant_by_id(referrer_participant_id)
    if row is None:
        return None, None, None
    try:
        current = int(rec.get("Приглашено друзей (оплативших)") or 0)
    except ValueError:
        current = 0
    new_count = current + 1
    new_status = referral_tier_for_count(new_count)
    set_participant_field(row, "Приглашено друзей (оплативших)", new_count)
    set_participant_field(row, "Реф-статус на след.сезон", new_status)
    return row, new_count, new_status


# ---------- Отчёты ----------

def get_today_report_status(participant_id: str, training_date: str):
    """Возвращает (row_index, статус, номер_попытки) ПОСЛЕДНЕГО отчёта за training_date,
    либо (None, None, 0), если отчётов за эту дату ещё не было.
    Номер попытки — реальный счётчик по всем строкам за этот день."""
    records = reports_ws.get_all_records()
    last_row_index, last_status, attempt_count = None, None, 0
    for i, rec in enumerate(records, start=2):
        if str(rec.get("ID участника")) == str(participant_id) and str(rec.get("Дата тренировки")) == training_date:
            attempt_count += 1
            last_row_index = i
            last_status = rec.get("Статус", "")
    return last_row_index, last_status, attempt_count


def can_submit_report(participant_id: str, training_date: str):
    """Можно ли отправить новый отчёт за training_date.
    Возвращает (можно: bool, причина: str|None)."""
    _, status, attempts = get_today_report_status(participant_id, training_date)
    if status in PENDING_STATUSES:
        return False, "pending"
    return True, None


def add_report(participant_id, season, day, training_date, sport, km, file_id):
    lives_before = get_lives(participant_id)
    _, _, prev_attempts = get_today_report_status(participant_id, training_date)
    attempt_number = prev_attempts + 1
    sent_at = now_str()
    row = [
        participant_id, season, day, training_date, sport, km, file_id,
        "На проверке", lives_before, "", "", "", sent_at, attempt_number,
        "", "",
    ]
    with _write_lock:
        col_a = reports_ws.col_values(1)  # ID участника — последняя реально заполненная строка
        row_index = len(col_a) + 1
        reports_ws.update(f"A{row_index}:{REPORTS_LAST_COL}{row_index}", [row], value_input_option="USER_ENTERED")
        return row_index, sent_at, attempt_number


def find_report_row(report_row_number: int):
    return reports_ws.row_values(report_row_number)


def update_report_photo_and_status(row_index: int, file_id: str, status: str):
    sent_at = now_str()
    reports_ws.update_cell(row_index, REPORTS_HEADERS.index("Скриншот (file_id)") + 1, file_id)
    reports_ws.update_cell(row_index, REPORTS_HEADERS.index("Статус") + 1, status)
    reports_ws.update_cell(row_index, REPORTS_HEADERS.index("Время отправки") + 1, sent_at)
    # увеличиваем счётчик попытки
    current = reports_ws.cell(row_index, REPORTS_HEADERS.index("Попытка №") + 1).value
    try:
        attempt = int(current) + 1
    except (TypeError, ValueError):
        attempt = 2
    reports_ws.update_cell(row_index, REPORTS_HEADERS.index("Попытка №") + 1, attempt)
    return sent_at, attempt


def update_report_decision(row_index: int, status: str, lives_after: int, comment: str = "",
                            confirmed_km=None, training_time: str = ""):
    updates = {
        "Статус": status,
        "Жизни после": lives_after,
        "Комментарий организатора": comment,
        "Дата решения": now_str(),
    }
    if confirmed_km is not None:
        updates["Км (подтверждено)"] = confirmed_km
    if training_time:
        updates["Время тренировки"] = training_time
    for col_name, value in updates.items():
        col = REPORTS_HEADERS.index(col_name) + 1
        reports_ws.update_cell(row_index, col, value)
