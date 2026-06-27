"""
host_ui.py — Dark-themed host status window (customtkinter).

Provides `run_host_window(args)` which creates a modern dark UI,
starts host.run_host in a daemon thread, and runs the CTk mainloop.

Usage from app.py:
    from host_ui import run_host_window
    run_host_window(args)
"""

import re
import threading
import time

import customtkinter as ctk

import host

# ── Colour palette ──────────────────────────────────────────────────
_BG_DARK      = "#1a1a2e"
_BG_CARD      = "#16213e"
_BG_LOG       = "#0f0f1a"
_ACCENT_BLUE  = "#4e9af5"
_ACCENT_RED   = "#e74c3c"
_ACCENT_GREEN = "#2ecc71"
_ACCENT_YELLOW = "#f1c40f"
_TEXT_PRIMARY  = "#e0e0e0"
_TEXT_SECONDARY = "#8899aa"
_TEXT_DIM      = "#556677"

# ── Status states ───────────────────────────────────────────────────
_STATUS = {
    "waiting":      ("\U0001f7e2", _ACCENT_GREEN,  "Ожидание подключения…"),
    "connected":    ("\U0001f7e2", _ACCENT_GREEN,  "Клиент подключён"),
    "reconnecting": ("\U0001f7e1", _ACCENT_YELLOW, "Переподключение…"),
    "error":        ("\U0001f534", _ACCENT_RED,    "Ошибка"),
}

# ── Log-level colours (applied per line) ────────────────────────────
_LOG_COLOURS = {
    "error":   _ACCENT_RED,
    "ошибка":  _ACCENT_RED,
    "warning": _ACCENT_YELLOW,
    "warn":    _ACCENT_YELLOW,
}

# Regex to detect log-level from host output
_RE_LEVEL = re.compile(
    r"\b(error|ошибка|warning|warn)\b", re.IGNORECASE
)


# ═══════════════════════════════════════════════════════════════════
#  Optional system-tray support (pystray)
# ═══════════════════════════════════════════════════════════════════
_HAS_TRAY = False
try:
    import pystray                          # type: ignore
    from PIL import Image as PILImage       # pystray needs PIL for icon
    _HAS_TRAY = True
except ImportError:
    pass


def _make_tray_icon():
    """Create a simple 64x64 green-on-dark icon for the tray."""
    img = PILImage.new("RGB", (64, 64), _BG_DARK)
    # draw a green circle in the centre
    for y in range(64):
        for x in range(64):
            if (x - 32) ** 2 + (y - 32) ** 2 <= 20 ** 2:
                img.putpixel((x, y), (46, 204, 113))
    return img


# ═══════════════════════════════════════════════════════════════════
#  Main window class
# ═══════════════════════════════════════════════════════════════════
class HostWindow:
    """Dark-themed host status & log window built with customtkinter."""

    def __init__(self, args):
        self.args = args
        self.stop_event = threading.Event()
        self._start_time = time.time()
        self._connection_count = 0
        self._tray_icon = None

        # ── CTk app setup ───────────────────────────────────────────
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.app = ctk.CTk()
        self.app.title("RemoteDesktop Host")
        self.app.geometry("560x480")
        self.app.minsize(400, 300)
        self.app.configure(fg_color=_BG_DARK)
        self.app.protocol("WM_DELETE_WINDOW", self._on_close)

        # Try to set a dark title-bar on Windows 10/11
        try:
            import ctypes as _ct
            hwnd = _ct.windll.user32.GetParent(self.app.winfo_id())
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            _ct.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
                _ct.byref(_ct.c_int(1)), _ct.sizeof(_ct.c_int))
        except Exception:
            pass

        self._build_ui()
        self._start_host_thread()
        self._tick_uptime()

    # ── UI construction ─────────────────────────────────────────────
    def _build_ui(self):
        # --- Header ---
        header = ctk.CTkFrame(self.app, fg_color=_BG_CARD, corner_radius=0)
        header.pack(fill="x", padx=0, pady=0)

        ctk.CTkLabel(
            header, text="RemoteDesktop Host",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=_ACCENT_BLUE,
        ).pack(side="left", padx=16, pady=10)

        # --- Status card ---
        card = ctk.CTkFrame(self.app, fg_color=_BG_CARD, corner_radius=8)
        card.pack(fill="x", padx=12, pady=(10, 4))

        status_row = ctk.CTkFrame(card, fg_color="transparent")
        status_row.pack(fill="x", padx=12, pady=(8, 2))

        self._status_dot = ctk.CTkLabel(
            status_row, text="\U0001f7e2",
            font=ctk.CTkFont(size=18), text_color=_ACCENT_GREEN,
        )
        self._status_dot.pack(side="left")

        self._status_text = ctk.CTkLabel(
            status_row, text="Ожидание подключения…",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=_TEXT_PRIMARY,
        )
        self._status_text.pack(side="left", padx=8)

        # --- Connection info ---
        info_frame = ctk.CTkFrame(card, fg_color="transparent")
        info_frame.pack(fill="x", padx=12, pady=(0, 8))

        mode = "relay" if self.args.relay else "direct"
        if self.args.relay:
            addr_text = self.args.relay
        else:
            addr_text = f"0.0.0.0:{self.args.listen}"
        room_text = getattr(self.args, "id", "—")

        for label, value in [
            ("Режим:", mode),
            ("Адрес:", addr_text),
            ("Комната:", room_text),
        ]:
            row = ctk.CTkFrame(info_frame, fg_color="transparent")
            row.pack(fill="x", pady=1)
            ctk.CTkLabel(
                row, text=label, width=80, anchor="w",
                font=ctk.CTkFont(size=12),
                text_color=_TEXT_SECONDARY,
            ).pack(side="left")
            ctk.CTkLabel(
                row, text=value, anchor="w",
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=_TEXT_PRIMARY,
            ).pack(side="left", padx=4)

        # --- Log area ---
        log_label = ctk.CTkLabel(
            self.app, text="Журнал", anchor="w",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=_TEXT_SECONDARY,
        )
        log_label.pack(fill="x", padx=16, pady=(8, 2))

        self._log = ctk.CTkTextbox(
            self.app,
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color=_BG_LOG,
            text_color=_TEXT_PRIMARY,
            corner_radius=6,
            wrap="word",
            state="disabled",
            activate_scrollbars=True,
        )
        self._log.pack(fill="both", expand=True, padx=12, pady=(0, 6))

        # Configure tags for coloured log lines
        for tag, colour in [
            ("error", _ACCENT_RED),
            ("warning", _ACCENT_YELLOW),
            ("info", _TEXT_PRIMARY),
        ]:
            self._log._textbox.tag_configure(tag, foreground=colour)

        # --- Bottom bar ---
        bottom = ctk.CTkFrame(self.app, fg_color=_BG_CARD, corner_radius=0, height=44)
        bottom.pack(fill="x", side="bottom")

        self._uptime_label = ctk.CTkLabel(
            bottom, text="Время работы: 0:00:00",
            font=ctk.CTkFont(size=11),
            text_color=_TEXT_DIM,
        )
        self._uptime_label.pack(side="left", padx=16)

        self._conn_label = ctk.CTkLabel(
            bottom, text="Подключений: 0",
            font=ctk.CTkFont(size=11),
            text_color=_TEXT_DIM,
        )
        self._conn_label.pack(side="left", padx=8)

        ctk.CTkButton(
            bottom, text="Остановить и выйти",
            fg_color=_ACCENT_RED, hover_color="#c0392b",
            text_color="white",
            font=ctk.CTkFont(size=12, weight="bold"),
            width=160, height=30,
            corner_radius=6,
            command=self._on_close,
        ).pack(side="right", padx=12, pady=7)

        ctk.CTkButton(
            bottom, text="Свернуть",
            fg_color="#334155", hover_color="#475569",
            text_color="white",
            font=ctk.CTkFont(size=12),
            width=100, height=30,
            corner_radius=6,
            command=self.app.iconify,
        ).pack(side="right", padx=(0, 4), pady=7)

        ctk.CTkButton(
            bottom, text="Свернуть в трей",
            fg_color="#334155", hover_color="#475569",
            text_color="white",
            font=ctk.CTkFont(size=12),
            width=130, height=30,
            corner_radius=6,
            command=self._minimize_to_tray,
        ).pack(side="right", padx=(0, 4), pady=7)

    # ── Host thread ─────────────────────────────────────────────────
    def _start_host_thread(self):
        host.LOG = self._log_callback
        t = threading.Thread(
            target=host.run_host,
            args=(self.args, self.stop_event),
            daemon=True,
        )
        t.start()

    def _log_callback(self, *args):
        """Thread-safe log callback — schedules UI update via after()."""
        text = " ".join(str(a) for a in args)
        try:
            self.app.after(0, self._append_log, text)
        except Exception:
            pass  # window already destroyed

    def _append_log(self, text: str):
        """Append a line to the log textbox (must be called on the UI thread)."""
        line = text.rstrip() + "\n"

        # Determine tag/colour based on content
        tag = "info"
        m = _RE_LEVEL.search(text)
        if m:
            word = m.group(1).lower()
            if word in ("error", "ошибка"):
                tag = "error"
            elif word in ("warning", "warn"):
                tag = "warning"

        # Detect status changes from log messages
        lower = text.lower()
        if "аутентифицирован" in lower or "подключился" in lower:
            self._connection_count += 1
            self._set_status("connected")
            self._conn_label.configure(
                text=f"Подключений: {self._connection_count}"
            )
        elif "переподключение" in lower or "reconnect" in lower:
            self._set_status("reconnecting")
        elif "разорвано" in lower or "завершена" in lower:
            self._set_status("waiting")
        elif "ошибка" in lower or "error" in lower:
            # Only set error status for serious errors, not input errors
            if "ввода" not in lower and "input" not in lower:
                self._set_status("error")

        self._log.configure(state="normal")
        self._log._textbox.insert("end", line, tag)
        self._log._textbox.see("end")
        self._log.configure(state="disabled")

    def _set_status(self, key: str):
        """Update the status indicator."""
        dot, colour, label = _STATUS.get(key, _STATUS["waiting"])
        self._status_dot.configure(text=dot, text_color=colour)
        self._status_text.configure(text=label)
        # Update window title
        self.app.title(f"RemoteDesktop Host — {label}")

    # ── Uptime ticker ───────────────────────────────────────────────
    def _tick_uptime(self):
        elapsed = int(time.time() - self._start_time)
        h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
        self._uptime_label.configure(text=f"Время работы: {h}:{m:02d}:{s:02d}")
        try:
            self.app.after(1000, self._tick_uptime)
        except Exception:
            pass

    # ── Close / tray ────────────────────────────────────────────────
    def _on_close(self):
        self.stop_event.set()
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        try:
            self.app.destroy()
        except Exception:
            pass

    def _minimize_to_tray(self):
        """Minimize to system tray (only if pystray available)."""
        if not _HAS_TRAY:
            self.app.iconify()
            return

        self.app.withdraw()

        def _show(icon, item):
            icon.stop()
            self._tray_icon = None
            self.app.after(0, self.app.deiconify)

        def _quit(icon, item):
            icon.stop()
            self._tray_icon = None
            self.app.after(0, self._on_close)

        menu = pystray.Menu(
            pystray.MenuItem("Показать", _show, default=True),
            pystray.MenuItem("Выход", _quit),
        )
        self._tray_icon = pystray.Icon(
            "RemoteDesktop Host",
            _make_tray_icon(),
            "RemoteDesktop Host",
            menu,
        )
        threading.Thread(target=self._tray_icon.run, daemon=True).start()

    # ── Run ─────────────────────────────────────────────────────────
    def run(self):
        self.app.mainloop()


# ═══════════════════════════════════════════════════════════════════
#  Public API
# ═══════════════════════════════════════════════════════════════════
def run_host_window(args):
    """Create and run the host status window.

    Parameters
    ----------
    args : argparse.Namespace
        Must contain at least: relay/listen, password, id, downloads,
        and optional quality/fps/scale/codec/engine fields
        (same as host.run_host expects).
    """
    win = HostWindow(args)
    win.run()
