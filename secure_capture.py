"""
SECURE DESKTOP CAPTURE — захват «защищённого рабочего стола» Windows.

Назначение: дать управляющему пользователю ВИДЕТЬ (а при достаточных правах —
и взаимодействовать с) защищённым рабочим столом Windows: запросом UAC
(consent.exe), экраном входа/блокировки (LogonUI) и экраном Ctrl+Alt+Del
(Winlogon). Эти экраны рендерятся на ОТДЕЛЬНОМ рабочем столе
(Winsta0\\Winlogon, он же «secure desktop»), который обычный процесс из
пользовательской сессии захватить не может — поэтому при UAC удалённый
зритель видит чёрный/застывший кадр.

ТЕХНИКА (работает только для процесса, запущенного как SYSTEM в интерактивной
сессии — см. install_service_system.ps1):
  1) OpenInputDesktop() — получить рабочий стол, у которого сейчас фокус ввода
     (Default при обычной работе, Winlogon во время UAC/входа/CAD).
  2) SetThreadDesktop() — привязать ТЕКУЩИЙ поток к этому рабочему столу.
     ВАЖНО: SetThreadDesktop меняет рабочий стол только если у потока нет
     открытых окон/хуков; должно вызываться из выделенного потока захвата.
  3) GDI BitBlt из DC рабочего стола (CreateDCW("DISPLAY") + GetDC) — это
     единственный способ, который РАБОТАЕТ на secure desktop. dxcam/DXGI
     Desktop Duplication защищённый стол НЕ отдаёт (возвращает чёрный кадр
     или ошибку доступа).

Всё в этом модуле — НЕОБЯЗАТЕЛЬНОЕ дополнение с мягкой деградацией: если прав
нет (процесс не SYSTEM) или API падает, функции возвращают None / False и
НЕ бросают исключений наружу. Вызывающий (host.py) при этом продолжает
обычный dxcam/mss путь.

Проверяемость в dev-сессии: без SYSTEM-прав и без живого UAC реально
захватить secure desktop нельзя. Поэтому здесь упор на устойчивость:
определить имя input-desktop, аккуратно деградировать, не падать.
"""

import ctypes
from ctypes import wintypes

import numpy as np


# ------------------------------------------------------------------ logging --
# host.py заменит это на свой LOG; по умолчанию — тихий no-op-совместимый print.
def _default_log(msg):
    print(msg)

LOG = _default_log


# --------------------------------------------------------------- win32 const --
# Права доступа к рабочему столу (winuser.h).
DESKTOP_READOBJECTS     = 0x0001
DESKTOP_CREATEWINDOW    = 0x0002
DESKTOP_CREATEMENU      = 0x0004
DESKTOP_HOOKCONTROL     = 0x0008
DESKTOP_JOURNALRECORD   = 0x0010
DESKTOP_JOURNALPLAYBACK = 0x0020
DESKTOP_ENUMERATE       = 0x0040
DESKTOP_WRITEOBJECTS    = 0x0080
DESKTOP_SWITCHDESKTOP   = 0x0100

GENERIC_ALL = 0x10000000

UOI_NAME = 2

# GDI / BitBlt
SRCCOPY     = 0x00CC0020
CAPTUREBLT  = 0x40000000
DIB_RGB_COLORS = 0
BI_RGB      = 0

SM_XVIRTUALSCREEN  = 76
SM_YVIRTUALSCREEN  = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SM_CXSCREEN = 0
SM_CYSCREEN = 1


# --------------------------------------------------------------- win32 libs ---
try:
    _user32  = ctypes.WinDLL("user32",  use_last_error=True)
    _gdi32   = ctypes.WinDLL("gdi32",   use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _AVAILABLE = True
except Exception as e:          # не Windows / нет библиотек — модуль выключен
    _user32 = _gdi32 = _kernel32 = None
    _AVAILABLE = False
    LOG(f"[secure] win32-библиотеки недоступны: {e}")


if _AVAILABLE:
    _user32.OpenInputDesktop.restype = wintypes.HANDLE
    _user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _user32.GetThreadDesktop.restype = wintypes.HANDLE
    _user32.GetThreadDesktop.argtypes = [wintypes.DWORD]
    _user32.SetThreadDesktop.restype = wintypes.BOOL
    _user32.SetThreadDesktop.argtypes = [wintypes.HANDLE]
    _user32.CloseDesktop.restype = wintypes.BOOL
    _user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    _user32.GetUserObjectInformationW.restype = wintypes.BOOL
    _user32.GetUserObjectInformationW.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetDC.restype = wintypes.HDC
    _user32.GetDC.argtypes = [wintypes.HWND]
    _user32.ReleaseDC.restype = ctypes.c_int
    _user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    _user32.GetSystemMetrics.restype = ctypes.c_int
    _user32.GetSystemMetrics.argtypes = [ctypes.c_int]

    _gdi32.CreateDCW.restype = wintypes.HDC
    _gdi32.CreateDCW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR,
                                 wintypes.LPCWSTR, wintypes.LPVOID]
    _gdi32.CreateCompatibleDC.restype = wintypes.HDC
    _gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    _gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    _gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    _gdi32.SelectObject.restype = wintypes.HGDIOBJ
    _gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    _gdi32.BitBlt.restype = wintypes.BOOL
    _gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wintypes.HDC,
                              ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    _gdi32.GetDIBits.restype = ctypes.c_int
    _gdi32.DeleteObject.restype = wintypes.BOOL
    _gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    _gdi32.DeleteDC.restype = wintypes.BOOL
    _gdi32.DeleteDC.argtypes = [wintypes.HDC]
    _gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT,
                                 wintypes.UINT, wintypes.LPVOID,
                                 wintypes.LPVOID, wintypes.UINT]

    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    _kernel32.GetCurrentThreadId.argtypes = []


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER),
                ("bmiColors", wintypes.DWORD * 3)]


# ------------------------------------------------------------------ helpers ---

def available():
    """Загрузились ли win32-библиотеки (Windows-only)."""
    return _AVAILABLE


def get_input_desktop_name():
    """Имя рабочего стола, у которого сейчас фокус ВВОДА.

    Обычная работа -> 'Default'. Во время UAC/входа/Ctrl+Alt+Del -> 'Winlogon'.
    Возвращает str или None (если прав/доступа нет — обычный пользовательский
    процесс часто НЕ может открыть Winlogon, тогда вернётся None — это нормально).
    """
    if not _AVAILABLE:
        return None
    hdesk = None
    try:
        # READOBJECTS достаточно для чтения имени; не запрашиваем switch-права.
        hdesk = _user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
        if not hdesk:
            return None
        return _desktop_name(hdesk)
    except Exception as e:
        LOG(f"[secure] get_input_desktop_name: {e}")
        return None
    finally:
        if hdesk:
            try:
                _user32.CloseDesktop(hdesk)
            except Exception:
                pass


def _desktop_name(hdesk):
    """Имя рабочего стола по его HDESK через GetUserObjectInformationW."""
    need = wintypes.DWORD(0)
    _user32.GetUserObjectInformationW(hdesk, UOI_NAME, None, 0, ctypes.byref(need))
    size = need.value or 256
    buf = ctypes.create_unicode_buffer(size // 2 + 1)
    ok = _user32.GetUserObjectInformationW(hdesk, UOI_NAME, buf, size,
                                           ctypes.byref(need))
    if not ok:
        return None
    return buf.value


def is_secure_desktop(name):
    """True, если имя соответствует защищённому столу (Winlogon / Secure...)."""
    if not name:
        return False
    n = name.lower()
    return n == "winlogon" or n.startswith("secure")


class SecureDesktopGrabber:
    """Захват КАДРА с input-desktop через переключение потока + GDI BitBlt.

    Использование (из ВЫДЕЛЕННОГО потока захвата):
        g = SecureDesktopGrabber()
        arr = g.grab()        # numpy RGB (H, W, 3) или None
        g.close()

    Каждый grab():
      * открывает текущий input-desktop, привязывает к нему ПОТОК;
      * делает GDI-снимок всего виртуального экрана;
      * возвращает RGB-массив, совместимый с пайплайном host.py.

    Любая ошибка/нехватка прав -> возврат None без исключения наружу.
    """

    def __init__(self):
        self._orig_desktop = None
        self.last_desktop_name = None
        if _AVAILABLE:
            try:
                # Запомним «родной» рабочий стол потока, чтобы вернуться.
                self._orig_desktop = _user32.GetThreadDesktop(
                    _kernel32.GetCurrentThreadId())
            except Exception:
                self._orig_desktop = None

    def _attach_input_desktop(self):
        """Открыть input-desktop и привязать к нему текущий поток.

        Возвращает HDESK (его НАДО закрыть после) или None.
        """
        access = (DESKTOP_READOBJECTS | DESKTOP_CREATEWINDOW |
                  DESKTOP_CREATEMENU | DESKTOP_HOOKCONTROL |
                  DESKTOP_WRITEOBJECTS | DESKTOP_ENUMERATE |
                  DESKTOP_SWITCHDESKTOP)
        hdesk = _user32.OpenInputDesktop(0, True, access)
        if not hdesk:
            # Частый случай у не-SYSTEM процесса: доступа к Winlogon нет.
            return None
        self.last_desktop_name = _desktop_name(hdesk)
        if not _user32.SetThreadDesktop(hdesk):
            try:
                _user32.CloseDesktop(hdesk)
            except Exception:
                pass
            return None
        return hdesk

    def grab(self):
        """Снимок текущего input-desktop как numpy RGB или None."""
        if not _AVAILABLE:
            return None
        hdesk = None
        hdc_screen = None
        hdc_mem = None
        hbmp = None
        try:
            hdesk = self._attach_input_desktop()
            if hdesk is None:
                return None
            # DC всего дисплея (CreateDCW("DISPLAY") видит привязанный стол).
            hdc_screen = _gdi32.CreateDCW("DISPLAY", None, None, None)
            if not hdc_screen:
                return None
            x = _user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
            y = _user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
            w = _user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
            h = _user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
            if w <= 0 or h <= 0:
                w = _user32.GetSystemMetrics(SM_CXSCREEN)
                h = _user32.GetSystemMetrics(SM_CYSCREEN)
                x = y = 0
            if w <= 0 or h <= 0:
                return None

            hdc_mem = _gdi32.CreateCompatibleDC(hdc_screen)
            hbmp = _gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
            if not hdc_mem or not hbmp:
                return None
            _gdi32.SelectObject(hdc_mem, hbmp)
            # CAPTUREBLT нужен для корректного захвата слоёв (UAC-затемнение).
            ok = _gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, x, y,
                               SRCCOPY | CAPTUREBLT)
            if not ok:
                return None

            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = w
            bmi.bmiHeader.biHeight = -h        # top-down (иначе кадр вверх ногами)
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = BI_RGB

            buf = (ctypes.c_ubyte * (w * h * 4))()
            got = _gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf,
                                   ctypes.byref(bmi), DIB_RGB_COLORS)
            if got == 0:
                return None
            arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)
            # BGRA -> RGB (как ожидает остальной пайплайн host.py).
            return np.ascontiguousarray(arr[:, :, 2::-1])
        except Exception as e:
            LOG(f"[secure] grab: {e}")
            return None
        finally:
            if hbmp:
                try: _gdi32.DeleteObject(hbmp)
                except Exception: pass
            if hdc_mem:
                try: _gdi32.DeleteDC(hdc_mem)
                except Exception: pass
            if hdc_screen:
                try: _gdi32.DeleteDC(hdc_screen)
                except Exception: pass
            # Вернём поток на родной рабочий стол (важно для чистоты).
            if self._orig_desktop:
                try: _user32.SetThreadDesktop(self._orig_desktop)
                except Exception: pass
            if hdesk:
                try: _user32.CloseDesktop(hdesk)
                except Exception: pass

    def close(self):
        if self._orig_desktop and _AVAILABLE:
            try:
                _user32.SetThreadDesktop(self._orig_desktop)
            except Exception:
                pass
        self._orig_desktop = None


# Self-test при прямом запуске (не падает без прав).
if __name__ == "__main__":
    print("available():", available())
    print("input desktop:", get_input_desktop_name())
    g = SecureDesktopGrabber()
    frame = g.grab()
    print("grab ->", None if frame is None else frame.shape)
    g.close()
