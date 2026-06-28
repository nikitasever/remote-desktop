r"""
Smoke-test контроля доступа (access_control.py) — ЧИСТАЯ логика, без сокетов/GUI.

Покрывает:
  1) roles.json round-trip: set/get/remove/load + дефолт (пустая таблица).
  2) Логику decide() для всех веток: таблица (control/view/blocked),
     политика по умолчанию (ask/allow_view/allow_control/deny) и headless-фолбэк.
  3) Хелпер is_control_message: какие MSG_* блокируются для роли "view".

Запуск:  .\.venv\Scripts\python.exe smoke_test_access.py
"""

import os
import sys
import tempfile

# Изолируем roles.json во временный каталог ДО импорта модуля.
_TMP = tempfile.mkdtemp(prefix="rd_access_test_")

import access_control
import common

# Переназначаем пути на временный файл (модуль уже импортирован).
access_control.APP_DIR = _TMP
access_control.ROLES_FILE = os.path.join(_TMP, "roles.json")

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  OK   {name}")
    else:
        _failed += 1
        print(f"  FAIL {name}")


def test_roundtrip():
    print("[1] roles.json round-trip")
    # Нет файла -> пустая таблица.
    check("missing file => empty table", access_control.load_roles() == {})
    check("get on empty => None", access_control.get_role("111111111") is None)

    # set/get
    check("set control", access_control.set_role("111111111", "control"))
    check("get control", access_control.get_role("111111111") == "control")
    check("set view", access_control.set_role("222222222", "view"))
    check("set blocked", access_control.set_role("333333333", "blocked"))
    check("load has 3", len(access_control.load_roles()) == 3)

    # Невалидная роль/ID отвергается.
    check("set invalid role rejected", access_control.set_role("444", "boss") is False)
    check("set empty id rejected", access_control.set_role("", "view") is False)

    # remove
    check("remove existing", access_control.remove_role("222222222") is True)
    check("removed gone", access_control.get_role("222222222") is None)
    check("remove missing => False", access_control.remove_role("999999999") is False)

    # Перечитываем с диска (новый load) — персистентность.
    check("persisted control", access_control.load_roles().get("111111111") == "control")


def test_decide():
    print("[2] decide() — все ветки")
    table = {"AAA": "control", "BBB": "view", "CCC": "blocked"}

    # 2a) Таблица имеет приоритет над политикой (даже при ask).
    check("table control => apply control",
          access_control.decide("AAA", table=table, default_policy="ask") == ("apply", "control"))
    check("table view => apply view",
          access_control.decide("BBB", table=table, default_policy="deny") == ("apply", "view"))
    check("table blocked => apply blocked",
          access_control.decide("CCC", table=table, default_policy="allow_control") == ("apply", "blocked"))

    # 2b) Неизвестный ID + политика.
    check("unknown + ask (prompt) => ask",
          access_control.decide("ZZZ", table=table, default_policy="ask", allow_prompt=True) == ("ask", None))
    check("unknown + ask (headless) => deny",
          access_control.decide("ZZZ", table=table, default_policy="ask", allow_prompt=False) == ("deny", None))
    check("unknown + allow_view => apply view",
          access_control.decide("ZZZ", table=table, default_policy="allow_view") == ("apply", "view"))
    check("unknown + allow_control => apply control",
          access_control.decide("ZZZ", table=table, default_policy="allow_control") == ("apply", "control"))
    check("unknown + deny => deny",
          access_control.decide("ZZZ", table=table, default_policy="deny") == ("deny", None))

    # 2c) Некорректная политика нормализуется в ask.
    check("bad policy normalizes to ask",
          access_control.decide("ZZZ", table=table, default_policy="garbage", allow_prompt=True) == ("ask", None))

    # 2d) None client_id ведёт себя как неизвестный.
    check("None id + deny => deny",
          access_control.decide(None, table=table, default_policy="deny") == ("deny", None))

    # 2e) resolve_role
    check("resolve known", access_control.resolve_role("AAA", table=table) == "control")
    check("resolve unknown => None", access_control.resolve_role("ZZZ", table=table) is None)


def test_enforcement():
    print("[3] is_control_message — классификация для view")
    icm = access_control.is_control_message

    # Для "control" ничего не блокируется.
    check("control: INPUT allowed", icm(common.MSG_INPUT, "control") is False)
    check("control: FILE_META allowed", icm(common.MSG_FILE_META, "control") is False)

    # Для "view" блокируются управляющие сообщения.
    blocked_for_view = [
        common.MSG_INPUT, common.MSG_FILE_META, common.MSG_FILE_CHUNK,
        common.MSG_FILE_END, common.MSG_FILE_PULL_REQ,
        common.MSG_CLIPBOARD, common.MSG_CLIPBOARD_IMAGE,
    ]
    for mt in blocked_for_view:
        check(f"view: 0x{mt:02X} BLOCKED", icm(mt, "view") is True)

    # Для "view" пассивные сообщения проходят.
    allowed_for_view = [
        common.MSG_PING, common.MSG_SET_MONITOR, common.MSG_DIR_LIST_REQ,
    ]
    for mt in allowed_for_view:
        check(f"view: 0x{mt:02X} allowed", icm(mt, "view") is False)

    # Для "blocked" блокируется всё.
    check("blocked: PING blocked", icm(common.MSG_PING, "blocked") is True)
    check("blocked: INPUT blocked", icm(common.MSG_INPUT, "blocked") is True)

    # MSG_ACCESS определён как 0x30 (контракт протокола).
    check("MSG_ACCESS == 0x30", common.MSG_ACCESS == 0x30)


def main():
    test_roundtrip()
    test_decide()
    test_enforcement()
    print()
    print(f"ИТОГО: {_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
