"""
SMOKE TEST для secure_capture.py.

ВАЖНО про окружение: в dev-сессии процесс НЕ SYSTEM и живого UAC нет, поэтому
по-настоящему снять защищённый стол здесь НЕЛЬЗЯ. Тест устроен так, чтобы
ПРОЙТИ за счёт проверки МЯГКОЙ деградации и безопасности к исключениям:

  1) модуль импортируется;
  2) available() возвращает bool (на Windows -> True);
  3) get_input_desktop_name() не падает (обычно вернёт 'Default' или None);
  4) is_secure_desktop() корректно классифицирует имена;
  5) SecureDesktopGrabber().grab() либо возвращает корректный (H,W,3) RGB-массив
     (если поток сейчас на обычном Default-столе — GDI его снимет), либо None
     (если прав нет) — но В ЛЮБОМ случае не бросает исключение;
  6) close() безопасен и повторно безопасен.

Запуск:  python smoke_test_secure.py  (через .venv\\Scripts\\python.exe)
"""

import sys
import traceback

import numpy as np

import secure_capture as sc


def check(name, cond):
    status = "OK  " if cond else "FAIL"
    print(f"[{status}] {name}")
    return cond


def main():
    ok = True

    # 1) импорт уже прошёл, если мы здесь
    ok &= check("import secure_capture", True)

    # 2) available()
    av = sc.available()
    ok &= check(f"available() -> bool ({av})", isinstance(av, bool))

    # 3) детект input-desktop не падает
    try:
        name = sc.get_input_desktop_name()
        ok &= check(f"get_input_desktop_name() -> {name!r} (без исключения)", True)
    except Exception:
        traceback.print_exc()
        ok &= check("get_input_desktop_name() без исключения", False)

    # 4) классификация имён
    ok &= check("is_secure_desktop('Winlogon') == True",
                sc.is_secure_desktop("Winlogon") is True)
    ok &= check("is_secure_desktop('Default') == False",
                sc.is_secure_desktop("Default") is False)
    ok &= check("is_secure_desktop(None) == False",
                sc.is_secure_desktop(None) is False)

    # 5) grab() — корректный массив ЛИБО None, но без исключения
    g = None
    try:
        g = sc.SecureDesktopGrabber()
        arr = g.grab()
        if arr is None:
            ok &= check("grab() -> None (нет прав/доступа) — мягкая деградация", True)
        else:
            shape_ok = (isinstance(arr, np.ndarray) and arr.ndim == 3
                        and arr.shape[2] == 3 and arr.dtype == np.uint8
                        and arr.shape[0] > 0 and arr.shape[1] > 0)
            ok &= check(f"grab() -> RGB-массив {arr.shape} dtype={arr.dtype}", shape_ok)
    except Exception:
        traceback.print_exc()
        ok &= check("grab() без исключения", False)
    finally:
        # 6) close() безопасен и идемпотентен
        try:
            if g is not None:
                g.close()
                g.close()
            ok &= check("close() x2 без исключения", True)
        except Exception:
            traceback.print_exc()
            ok &= check("close() без исключения", False)

    # 7) интеграция: модуль должен импортироваться внутри host.py без побочек
    try:
        import importlib
        importlib.import_module("secure_capture")
        ok &= check("повторный импорт безопасен", True)
    except Exception:
        traceback.print_exc()
        ok &= check("повторный импорт безопасен", False)

    print()
    if ok:
        print("РЕЗУЛЬТАТ: PASS (мягкая деградация подтверждена; реальный захват")
        print("защищённого стола требует SYSTEM-прав и живого UAC — см. ниже).")
        return 0
    print("РЕЗУЛЬТАТ: FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
