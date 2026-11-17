import base64
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
_creds_raw = os.environ["GOOGLE_CREDS_JSON"]
try:
    # Значение хранится в base64 (так требует Amvera: сырой JSON с кавычками
    # не проходит валидацию переменных окружения)
    _creds_json_str = base64.b64decode(_creds_raw).decode("utf-8")
    creds_dict = json.loads(_creds_json_str)
except Exception:
    # На случай, если где-то (например, локально) переменная задана как
    # обычный JSON без base64 — пробуем распарсить напрямую
    creds_dict = json.loads(_creds_raw)
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
    "Реф-статус на след.сезон", "Телефон", "Платформа",
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

PARTICIPANTS_LAST_COL = "W"
REPORTS_LAST_COL = "P"

REFERRAL_TIERS = [
    (4, "Бесплатно"),
    (1, "Скидка 1100₽"),
]


def now_str():
    return datetime.now(MSK).strftime("%Y-%m-%d %H:%M:%S")


def get_season_day():
    today = datetime.now(MSK).date()
    start = SEASON_START_DATE.date()
    if today < start:
        return None
    day = (today - start).days + 1
    return day if day <= 14 else None


def _norm(value) -> str:
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
    records = participants_ws.get_all_records()
    target = str(telegram_id).strip()
    matches = []
    for i, rec in enumerate(records, start=2):
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
    telegram_value = f"{telegram_id} ({username})"
    with _write_lock:
        records = participants_ws.get_all_records()
        row_index = None
        for i, rec in enumerate(records, start=2):
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
            referred_by, "", "", phone, "Telegram",
        ]
        last_used = len(records) + 1
        row_index = last_used + 1
        participants_ws.update(
            f"A{row_index}:{PARTICIPANTS_LAST_COL}{row_index}",
            [row], value_input_option="USER_ENTERED",
        )
        return row_index


# ---------- Поддержка VK ----------

def parse_vk_id(raw_field: str):
    field = str(raw_field or "").strip()
    if not field.startswith("vk:"):
        return None
    id_part = field[3:].split(" ")[0]
    try:
        return int(id_part)
    except ValueError:
        return None


def find_participant_by_platform_id(platform_user_id, platform: str = "telegram"):
    """platform="VK" ищет по префиксу "vk:" в колонке "Telegram/контакт".
    Любое другое значение — обычный Telegram-поиск (для обратной совместимости)."""
    if str(platform).upper() != "VK":
        return find_participant_row(platform_user_id)

    records = participants_ws.get_all_records()
    target = str(platform_user_id).strip()
    matches = []
    for i, rec in enumerate(records, start=2):
        field = str(rec.get("Telegram/контакт", "")).strip()
        vk_id = parse_vk_id(field)
        if vk_id is not None and str(vk_id) == target:
            matches.append((i, rec))
    if not matches:
        return None, None
    for i, rec in matches:
        if rec.get("Статус оплаты") == "Оплачено":
            return i, rec
    return matches[-1]


def get_vk_id_by_participant(participant_id: str):
    row, rec = find_participant_by_id(participant_id)
    if not rec:
        return None
    return parse_vk_id(rec.get("Telegram/контакт", ""))


def add_pending_participant_vk(vk_id: int, full_name: str, sport: str, tariff: str, price,
                                season: str = "S01", consent_at: str = "", phone: str = "",
                                is_minor: bool = False):
    """Хранит VK ID в "Telegram/контакт" с префиксом "vk:", заполняет "Платформа"."""
    contact_value = f"vk:{vk_id} ({full_name})"
    with _write_lock:
        records = participants_ws.get_all_records()
        row_index = None
        for i, rec in enumerate(records, start=2):
            telegram_field = str(rec.get("Telegram/контакт", "")).strip()
            rec_season = str(rec.get("Сезон", "")).strip()
            if telegram_field == "" and rec_season == season:
                row_index = i
                break

        values = {
            "Telegram/контакт": contact_value,
            "Спорт": sport,
            "Тариф": tariff,
            "Сумма оплаты, ₽": price,
            "Статус оплаты": "Ожидает проверки",
            "Платформа": "VK",
        }
        if consent_at:
            values["Согласие с офертой (дата/время)"] = consent_at
        if is_minor:
            values["Согласие родителя (14-17, да/нет)"] = "Ожидается"
        if phone:
            values["Телефон"] = phone

        if row_index is not None:
            for col_name, value in values.items():
                participants_ws.update_cell(row_index, PARTICIPANTS_HEADERS.index(col_name) + 1, value)
            return row_index

        row = [""] * len(PARTICIPANTS_HEADERS)
        for col_name, value in values.items():
            row[PARTICIPANTS_HEADERS.index(col_name)] = value
        row[PARTICIPANTS_HEADERS.index("Сезон")] = season
        row[PARTICIPANTS_HEADERS.index("Право на медаль")] = "Не определено"
        row[PARTICIPANTS_HEADERS.index("Адрес получен?")] = "Нет"

        last_used = len(records) + 1
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
    value = rec.get("Согласие родителя (14-17, да/нет)", "")
    if value == "":
        return True
    return is_yes(value)


def get_lives(participant_id: str) -> int:
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
    for threshold, label in REFERRAL_TIERS:
        if count >= threshold:
            return label
    return ""


def increment_referral_count(referrer_participant_id: str):
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
    records = reports_ws.get_all_records()
    last_row_index, last_status, attempt_count = None, None, 0
    for i, rec in enumerate(records, start=2):
        if str(rec.get("ID участника")) == str(participant_id) and str(rec.get("Дата тренировки")) == training_date:
            attempt_count += 1
            last_row_index = i
            last_status = rec.get("Статус", "")
    return last_row_index, last_status, attempt_count


def can_submit_report(participant_id: str, training_date: str):
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
        col_a = reports_ws.col_values(1)
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
