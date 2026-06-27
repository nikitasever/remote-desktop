import sys
import os
import winreg
import customtkinter as ctk


BG_COLOR = "#111827"
TEXT_COLOR = "white"
ACCENT_COLOR = "#3b82f6"

REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_KEY_NAME = "RemoteDesktop"


def _get_exe_command() -> str:
    """Return the command string to register in the registry."""
    if getattr(sys, 'frozen', False):
        # Running as PyInstaller exe
        return f'"{sys.executable}"'
    else:
        # Running as Python script
        script = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "app.py")
        )
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.isfile(pythonw):
            pythonw = sys.executable
        return f'"{pythonw}" "{script}"'


def is_autostart_enabled() -> bool:
    """Check if the app is registered in Windows startup."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0,
                            winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, REG_KEY_NAME)
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def enable_autostart() -> None:
    """Add the app to Windows startup via registry."""
    cmd = _get_exe_command()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, REG_KEY_NAME, 0, winreg.REG_SZ, cmd)
    except PermissionError:
        pass


def disable_autostart() -> None:
    """Remove the app from Windows startup."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, REG_KEY_NAME)
    except FileNotFoundError:
        pass
    except PermissionError:
        pass


# ── Settings UI ──────────────────────────────────────────────


def _section_header(parent, text):
    label = ctk.CTkLabel(
        parent, text=text, font=("", 16, "bold"),
        text_color=TEXT_COLOR, anchor="w"
    )
    label.pack(fill="x", padx=20, pady=(18, 6))
    return label


def _make_checkbox(parent, text, config, key, default=False):
    var = ctk.BooleanVar(value=config.get(key, default))

    def on_change():
        config.set(key, var.get())

    cb = ctk.CTkCheckBox(
        parent, text=text, variable=var,
        text_color=TEXT_COLOR, fg_color=ACCENT_COLOR,
        hover_color=ACCENT_COLOR, command=on_change
    )
    cb.pack(fill="x", padx=40, pady=3, anchor="w")
    return cb


def create_page(parent, config):
    frame = ctk.CTkFrame(parent, fg_color=BG_COLOR)

    # ── Автозапуск ──
    _section_header(frame, "Автозапуск")

    autostart_var = ctk.BooleanVar(value=is_autostart_enabled())

    def on_autostart_change():
        if autostart_var.get():
            enable_autostart()
        else:
            disable_autostart()
        # Update the var to reflect actual state
        autostart_var.set(is_autostart_enabled())

    cb_autostart = ctk.CTkCheckBox(
        frame,
        text="Запускать RemoteDesktop при старте Windows",
        variable=autostart_var,
        text_color=TEXT_COLOR,
        fg_color=ACCENT_COLOR,
        hover_color=ACCENT_COLOR,
        command=on_autostart_change
    )
    cb_autostart.pack(fill="x", padx=40, pady=3, anchor="w")

    # Show registered exe path
    exe_path_label = ctk.CTkLabel(
        frame,
        text=f"Путь: {_get_exe_command()}",
        font=("", 12),
        text_color="#9ca3af",
        anchor="w"
    )
    exe_path_label.pack(fill="x", padx=44, pady=(0, 6))

    # ── Запуск ──
    _section_header(frame, "Запуск")

    _make_checkbox(
        frame,
        "Запускать хост автоматически при старте приложения",
        config, "startup.auto_host", default=False
    )
    _make_checkbox(
        frame,
        "Запускать свёрнутым в трей",
        config, "startup.start_minimized", default=False
    )

    return frame
