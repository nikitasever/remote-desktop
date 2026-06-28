"""
Управление правами доступа к УПРАВЛЕНИЮ хостом (control / view / blocked).

Гибрид двух механизмов:
  1) Роли по client_id — постоянная таблица ролей (roles.json в
     %APPDATA%/RemoteDesktop/). Если для client_id задана роль — применяем
     БЕЗ запроса: blocked => отказ, view => только просмотр, control => полный.
  2) Если роли нет — поведение задаёт "политика по умолчанию" из настроек:
     ask / allow_view / allow_control / deny.

Этот модуль — ЧИСТАЯ логика + чтение/запись JSON. Никаких сокетов и GUI:
host вызывает resolve_role()/decide() и применяет результат, app.py — менеджер
ролей в настройках. Всё graceful: нет roles.json => пустая таблица.

Роли:
  "control" — полный контроль (мышь/клавиатура/файлы/буфер).
  "view"    — только просмотр: все управляющие сообщения host отбрасывает.
  "blocked" — подключение запрещено.

Решения decide():
  ("apply", role)  — роль уже определена (из таблицы), применить.
  ("ask",   None)  — нужно спросить пользователя (GUI-диалог на host).
  ("deny",  None)  — отказать без вопроса (политика deny / headless).
"""

import json
import os
import threading

APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "RemoteDesktop")
ROLES_FILE = os.path.join(APP_DIR, "roles.json")

# Допустимые роли (control сильнее view сильнее blocked).
ROLES = ("control", "view", "blocked")

# Допустимые значения политики по умолчанию.
DEFAULT_POLICIES = ("ask", "allow_view", "allow_control", "deny")
DEFAULT_POLICY = "ask"

# Политика -> что делает decide(), когда роль для ID не задана.
_POLICY_RESULT = {
    "ask": ("ask", None),
    "allow_view": ("apply", "view"),
    "allow_control": ("apply", "control"),
    "deny": ("deny", None),
}

# Какие типы сообщений считаются УПРАВЛЕНИЕМ и должны блокироваться для view.
# Импортируем common лениво/мягко, чтобы модуль оставался тестируемым без него.
try:
    import common as _common
    _MSG_INPUT = _common.MSG_INPUT
    _MSG_FILE_META = _common.MSG_FILE_META
    _MSG_FILE_CHUNK = _common.MSG_FILE_CHUNK
    _MSG_FILE_END = _common.MSG_FILE_END
    _MSG_FILE_PULL_REQ = _common.MSG_FILE_PULL_REQ
    _MSG_CLIPBOARD = _common.MSG_CLIPBOARD
    _MSG_CLIPBOARD_IMAGE = _common.MSG_CLIPBOARD_IMAGE
except Exception:  # pragma: no cover - запасные литералы из протокола
    _MSG_INPUT = 0x04
    _MSG_FILE_META = 0x06
    _MSG_FILE_CHUNK = 0x07
    _MSG_FILE_END = 0x08
    _MSG_FILE_PULL_REQ = 0x10
    _MSG_CLIPBOARD = 0x05
    _MSG_CLIPBOARD_IMAGE = 0x16

# Управляющие сообщения, запрещённые в режиме "view".
# Включает: ввод (мышь/клавиатура), запись файлов client->host (push),
# вытягивание файлов host->client (pull) и запись буфера обмена (текст+картинка
# рассматриваются как изменение состояния host'а -> блокируем для просмотра).
# DIR_LIST_REQ (обзор каталогов) НЕ блокируем здесь — это пассивный просмотр,
# и он уже ограничен path-jail на host'е.
_CONTROL_MSGS_VIEW = frozenset({
    _MSG_INPUT,
    _MSG_FILE_META,
    _MSG_FILE_CHUNK,
    _MSG_FILE_END,
    _MSG_FILE_PULL_REQ,
    _MSG_CLIPBOARD,
    _MSG_CLIPBOARD_IMAGE,
})


_lock = threading.Lock()


def _read_raw():
    """Читает roles.json -> dict {client_id: role}. Пусто при любой ошибке."""
    try:
        with open(ROLES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for cid, role in data.items():
        if isinstance(cid, str) and role in ROLES:
            out[cid] = role
    return out


def _write_raw(table):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(ROLES_FILE, "w", encoding="utf-8") as f:
            json.dump(table, f, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


def load_roles():
    """Вернуть копию таблицы ролей {client_id: role}."""
    with _lock:
        return dict(_read_raw())


def get_role(client_id):
    """Роль для client_id или None, если не задана."""
    if not client_id:
        return None
    with _lock:
        return _read_raw().get(client_id)


def set_role(client_id, role):
    """Назначить роль client_id (control/view/blocked). True при успехе."""
    if not client_id or role not in ROLES:
        return False
    with _lock:
        table = _read_raw()
        table[client_id] = role
        return _write_raw(table)


def remove_role(client_id):
    """Удалить роль для client_id. True, если запись была и удалена."""
    if not client_id:
        return False
    with _lock:
        table = _read_raw()
        if client_id in table:
            del table[client_id]
            return _write_raw(table)
        return False


def normalize_policy(policy):
    """Привести политику по умолчанию к допустимому значению."""
    return policy if policy in DEFAULT_POLICIES else DEFAULT_POLICY


def resolve_role(client_id, table=None, default_policy=DEFAULT_POLICY):
    """Определить роль ТОЛЬКО по таблице (без политики).

    Возвращает роль из ROLES, либо None если для ID ничего не задано.
    table — словарь {id: role}; если None, читается из roles.json.
    """
    if table is None:
        table = load_roles()
    role = table.get(client_id) if client_id else None
    if role in ROLES:
        return role
    return None


def decide(client_id, table=None, default_policy=DEFAULT_POLICY, allow_prompt=True):
    """Главная логика принятия решения о подключении.

    Возвращает кортеж (action, role):
      ("apply", "control"/"view"/"blocked") — роль определена, применить её.
      ("ask",   None) — нужно спросить пользователя (только если allow_prompt).
      ("deny",  None) — отказать без вопроса.

    Порядок: сначала таблица ролей (включая "blocked"); если роли нет —
    политика по умолчанию. В headless-режиме allow_prompt=False: вместо "ask"
    падаем обратно в политику, а если она тоже "ask" — это "deny".
    """
    role = resolve_role(client_id, table=table)
    if role is not None:
        return ("apply", role)

    policy = normalize_policy(default_policy)
    action, presolved = _POLICY_RESULT[policy]
    if action == "ask" and not allow_prompt:
        # Headless / нет GUI: вопрос задать некому -> отказ.
        return ("deny", None)
    return (action, presolved)


def is_control_message(msg_type, role):
    """True, если сообщение msg_type должно быть ЗАБЛОКИРОВАНО для роли.

    Для role == "view": блокируются управляющие сообщения (ввод, файлы,
    запись буфера). Для "control": ничего не блокируется. Для "blocked"
    блокируется всё (сессия и так не должна была начаться).
    """
    if role == "control":
        return False
    if role == "blocked":
        return True
    if role == "view":
        return msg_type in _CONTROL_MSGS_VIEW
    # Неизвестная роль — безопасно трактуем как view.
    return msg_type in _CONTROL_MSGS_VIEW


def control_message_types():
    """Набор типов сообщений, блокируемых в режиме view (для тестов/диагностики)."""
    return set(_CONTROL_MSGS_VIEW)
