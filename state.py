"""
Простое хранилище состояний диалога в памяти процесса — аналог aiogram FSM,
но без зависимости от aiogram (он заточен под Telegram Update-объекты).

Ключ — user_id участника (в MAX это числовой ID пользователя).
"""
from dataclasses import dataclass, field


@dataclass
class UserState:
    state: str | None = None
    data: dict = field(default_factory=dict)


_STATES: dict[int, UserState] = {}


def get(user_id: int) -> UserState:
    if user_id not in _STATES:
        _STATES[user_id] = UserState()
    return _STATES[user_id]


def set_state(user_id: int, state: str | None):
    get(user_id).state = state


def update_data(user_id: int, **kwargs):
    get(user_id).data.update(kwargs)


def get_data(user_id: int) -> dict:
    return get(user_id).data


def clear(user_id: int):
    _STATES[user_id] = UserState()
