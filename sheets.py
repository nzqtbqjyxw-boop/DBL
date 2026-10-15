import json
import os
from datetime import datetime

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

PARTICIPANTS_HEADERS = [
    "ID участника", "Telegram/контакт", "Сезон", "Спорт", "Тариф",
    "Сумма оплаты, ₽", "Статус оплаты", "Дата оплаты", "Право на медаль",
    "Адрес получен?", "Дата отправки", "Трек-номер", "Примечания",
]

REPORTS_HEADERS = [
    "ID участника", "Сезон", "День", "Дата тренировки", "Вид спорта",
    "Км", "Скриншот (file_id)", "Статус", "Жизни до", "Жизни после",
    "Комментарий организатора", "Дата решения",
]


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------- Участники ----------

def get_all_participants():
    return participants_ws.get_all_records()


def find_participant_row(telegram_id: str):
    """Возвращает (row_index, record) по Telegram ID, либо (None, None)."""
    records = participants_ws.get_all_records()
    for i, rec in enumerate(records, start=2):  # строка 1 — заголовок
        if str(rec.get("Telegram/контакт", "")) == str(telegram_id):
            return i, rec
    return None, None


def add_pending_participant(telegram_id: str, username: str, season: str = "S01"):
    """Добавляет участника со статусом 'Ожидает проверки оплаты'."""
    row = [
        "",  # ID участника — присваивается после подтверждения
        f"{telegram_id} ({username})",
        season,
        "",
        "",
        "",
        "Ожидает проверки",
        "",
        "Не определено",
        "Нет",
        "", "", "",
    ]
    participants_ws.append_row(row, value_input_option="USER_ENTERED")
    return participants_ws.row_count


def set_participant_status(row_index: int, status: str, participant_id: str = None):
    updates = {"Статус оплаты": status}
    if participant_id:
        updates["ID участника"] = participant_id
    for col_name, value in updates.items():
        col = PARTICIPANTS_HEADERS.index(col_name) + 1
        participants_ws.update_cell(row_index, col, value)


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


# ---------- Отчёты ----------

def add_report(participant_id, season, day, training_date, sport, km, file_id):
    lives_before = get_lives(participant_id)
    row = [
        participant_id, season, day, training_date, sport, km, file_id,
        "На проверке", lives_before, "", "", "",
    ]
    reports_ws.append_row(row, value_input_option="USER_ENTERED")
    return reports_ws.row_count


def find_report_row(report_row_number: int):
    return reports_ws.row_values(report_row_number)


def update_report_decision(row_index: int, status: str, lives_after: int, comment: str = ""):
    updates = {
        "Статус": status,
        "Жизни после": lives_after,
        "Комментарий организатора": comment,
        "Дата решения": now_str(),
    }
    for col_name, value in updates.items():
        col = REPORTS_HEADERS.index(col_name) + 1
        reports_ws.update_cell(row_index, col, value)
