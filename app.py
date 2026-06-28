"""
RemoteDesktop — единое окно-лаунчер (AnyDesk-style UI).

Собирается в один app.exe (см. build_exe.ps1).
"""

import json
import math
import os
import random
import secrets
import string
import subprocess
import sys
import traceback
import threading
import tkinter as tk
from tkinter import messagebox, filedialog

import customtkinter as ctk

import host
import client
import settings_ui

# Optional modules — UI works without them
try:
    import session_history
except ImportError:
    session_history = None

try:
    import lan_discovery
except ImportError:
    lan_discovery = None

try:
    import updater
except ImportError:
    updater = None

APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "RemoteDesktop")
CONFIG = os.path.join(APP_DIR, "config.json")
DEFAULT_DOWNLOADS = os.path.join(os.path.expanduser("~"), "RemoteDesktop_received")

# -- Color palette --
BG_DARK = "#111827"       # main background
BG_CARD = "#1e293b"       # card / section background
BG_INPUT = "#0f172a"      # entry fields
ACCENT = "#3b82f6"        # blue accent
ACCENT_HOVER = "#2563eb"  # darker blue on hover
TEXT_PRIMARY = "#f1f5f9"   # bright text
TEXT_SECONDARY = "#94a3b8" # muted text
TEXT_HINT = "#64748b"      # faint hints
BORDER = "#334155"         # subtle border
GREEN = "#22c55e"
GREEN_DIM = "#166534"
RED = "#ef4444"
YELLOW = "#eab308"
ACCENT_BRIGHT = "#60a5fa"

# Gradient colors for ID section shimmer
GRAD_A = "#1e3a5f"
GRAD_B = "#0f172a"
GRAD_C = "#1a2744"

# -- Host ID persistence --
_HOST_ID_FILE = os.path.join(APP_DIR, "host_id.json")


def _get_or_create_host_id():
    """Load or generate a persistent 9-digit host ID."""
    try:
        with open(_HOST_ID_FILE, "r") as f:
            return json.load(f)["id"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        pass
    new_id = str(random.randint(100_000_000, 999_999_999))
    os.makedirs(APP_DIR, exist_ok=True)
    with open(_HOST_ID_FILE, "w") as f:
        json.dump({"id": new_id}, f)
    return new_id


def _format_id(id_str):
    """Format '847291035' as '847 291 035' for display."""
    s = id_str.zfill(9)
    return f"{s[:3]} {s[3:6]} {s[6:]}"


class Args:
    """Простой контейнер атрибутов вместо argparse.Namespace."""
    def __init__(self, **kw):
        self.relay = None
        self.connect = None
        self.listen = None
        self.id = "default"
        self.password = ""
        self.downloads = DEFAULT_DOWNLOADS
        self.quality = 55
        self.fps = 18
        self.scale = 1.0
        self.codec = "auto"
        self.engine = "auto"
        self.unique_id = None
        self.__dict__.update(kw)


def load_config():
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(data):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# -- Password strength helpers --
COMMON_PASSWORDS = {"12345", "password", "qwerty", "123456", "111111", "000000"}
PW_ALPHABET = string.ascii_letters + string.digits + "!@#$%^&*-_=+?"


def _is_weak_password(pw):
    """True if password is too short or a well-known weak one."""
    return len(pw) < 8 or pw.lower() in COMMON_PASSWORDS


def _password_strength(pw):
    """Return (label, color) describing the password strength."""
    if not pw or _is_weak_password(pw):
        return ("Слабый", RED)
    variety = sum(bool(s) for s in (
        any(c.islower() for c in pw),
        any(c.isupper() for c in pw),
        any(c.isdigit() for c in pw),
        any(not c.isalnum() for c in pw),
    ))
    if len(pw) >= 12 and variety >= 3:
        return ("Надёжный", GREEN)
    return ("Средний", YELLOW)


def _generate_password(length=16):
    """Generate a strong random password using the secrets module."""
    return "".join(secrets.choice(PW_ALPHABET) for _ in range(length))


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _rgb_to_hex(r, g, b):
    return f"#{int(r):02x}{int(g):02x}{int(b):02x}"


def _lerp_color(c1, c2, t):
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    return _rgb_to_hex(r1 + (r2 - r1) * t, g1 + (g2 - g1) * t, b1 + (b2 - b1) * t)


def _animate_button_hover(btn, target_color, current_color, base_color, step=0):
    """Smooth color transition on hover/leave with subtle scale effect."""
    t = min((step + 1) / 6.0, 1.0)
    c = _lerp_color(base_color, target_color, t)
    # Subtle padding change to simulate scale
    is_hover = (target_color != base_color)
    pad = int(2 * t) if is_hover else int(2 * (1 - t))
    try:
        btn.configure(fg_color=c)
        btn.configure(padx=max(0, btn._original_padx - pad) if hasattr(btn, '_original_padx') else None,
                      pady=max(0, btn._original_pady - pad) if hasattr(btn, '_original_pady') else None)
    except Exception:
        try:
            btn.configure(fg_color=c)
        except Exception:
            return
    if t < 1.0:
        btn.after(16, _animate_button_hover, btn, target_color, c, base_color, step + 1)


class LauncherUI:
    def __init__(self, root):
        self.root = root
        self._status_pulse_id = None
        self._id_pulse_id = None
        self._gradient_id = None
        self._connect_pulse_id = None
        self._gradient_step = 0
        self._id_pulse_step = 0
        root.title("RemoteDesktop")
        root.resizable(True, True)
        root.minsize(520, 600)
        root.configure(fg_color=BG_DARK)
        cfg = load_config()

        self.role = tk.StringVar(value=cfg.get("role", "client"))
        self.conn = tk.StringVar(value=cfg.get("conn", "relay"))
        self.address = tk.StringVar(value=cfg.get("address", ""))
        self.remote_id = tk.StringVar(value=cfg.get("remote_id", ""))
        self.password = tk.StringVar(value=cfg.get("password", ""))
        self.downloads = tk.StringVar(value=cfg.get("downloads", DEFAULT_DOWNLOADS))
        self.quality = tk.StringVar(value=str(cfg.get("quality", "70")))
        self.fps = tk.StringVar(value=str(cfg.get("fps", "20")))
        self.scale = tk.StringVar(value=cfg.get("scale", "100%"))
        self.codec = tk.StringVar(value=cfg.get("codec", "Авто"))
        self.engine = tk.StringVar(value=cfg.get("engine", "Видео H.264"))
        self._show_pw = tk.BooleanVar(value=False)
        self._hosting = tk.BooleanVar(value=cfg.get("hosting", False))
        self._active_tab = tk.StringVar(value="recent")

        self.host_id = _get_or_create_host_id()

        # Main scrollable container
        self.main_frame = ctk.CTkFrame(root, fg_color=BG_DARK, corner_radius=0)
        self.main_frame.pack(fill="both", expand=True, padx=0, pady=0)

        self._build_top_bar()
        self._build_id_section()
        self._build_host_toggle()
        self._build_tab_bar()
        self._build_tab_content()
        self._build_footer()

        self._switch_tab("recent")

        # Start decorative animations
        self._start_gradient_animation()
        self._start_id_pulse()
        self._start_connect_pulse()

    # ================================================================
    #  DECORATIVE ANIMATIONS
    # ================================================================

    def _start_gradient_animation(self):
        """Animate subtle gradient shimmer on the ID section canvas."""
        if not hasattr(self, '_id_canvas'):
            return
        self._gradient_step += 1
        t = self._gradient_step * 0.015
        t1 = (math.sin(t) + 1) / 2
        t2 = (math.sin(t + 2.094) + 1) / 2
        c1 = _lerp_color(GRAD_A, GRAD_B, t1)
        c2 = _lerp_color(GRAD_B, GRAD_C, t2)
        try:
            w = self._id_canvas.winfo_width() or 500
            h = self._id_canvas.winfo_height() or 120
            self._id_canvas.delete("gradient")
            bands = 8
            for i in range(bands):
                frac = i / max(bands - 1, 1)
                color = _lerp_color(c1, c2, frac)
                y0 = int(h * i / bands)
                y1 = int(h * (i + 1) / bands)
                self._id_canvas.create_rectangle(0, y0, w, y1, fill=color,
                                                 outline="", tags="gradient")
            self._id_canvas.tag_lower("gradient")
        except Exception:
            return
        self._gradient_id = self.root.after(50, self._start_gradient_animation)

    def _animate_title_wave(self):
        """Wave of rainbow colors flowing right-to-left across each letter."""
        self._title_wave_step += 1
        n = len(self._title_letters)
        for i, lbl in enumerate(self._title_letters):
            offset = (n - 1 - i) * 0.08
            h = (self._title_wave_step * 0.015 + offset) % 1.0
            lbl.configure(text_color=self._hue_to_hex(h))
        self.root.after(50, self._animate_title_wave)

    @staticmethod
    def _hue_to_hex(h):
        """Convert hue (0-1) to a bright saturated hex color."""
        import colorsys
        r, g, b = colorsys.hsv_to_rgb(h, 0.75, 0.95)
        return f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"

    def _start_id_pulse(self):
        """Pulse the host ID label color between ACCENT and ACCENT_BRIGHT."""
        self._id_pulse_step += 1
        t = (math.sin(self._id_pulse_step * 0.035) + 1) / 2
        c = _lerp_color(ACCENT, ACCENT_BRIGHT, t)
        try:
            self.host_id_label.configure(text_color=c)
        except Exception:
            return
        self._id_pulse_id = self.root.after(50, self._start_id_pulse)

    def _start_connect_pulse(self):
        """Gentle pulse on the connect button when idle."""
        if not hasattr(self, '_connect_btn'):
            return
        self._connect_pulse_step = getattr(self, '_connect_pulse_step', 0) + 1
        t = (math.sin(self._connect_pulse_step * 0.04) + 1) / 2
        c = _lerp_color(ACCENT, ACCENT_BRIGHT, t * 0.4)
        try:
            self._connect_btn.configure(fg_color=c)
        except Exception:
            return
        self._connect_pulse_id = self.root.after(50, self._start_connect_pulse)

    def _animate_card_hover(self, card, entering):
        """Smooth border color transition on card hover."""
        target = ACCENT if entering else BORDER
        start = BORDER if entering else ACCENT
        self._card_hover_anim(card, start, target, 0)

    def _card_hover_anim(self, card, start, target, step):
        t = min((step + 1) / 5.0, 1.0)
        c = _lerp_color(start, target, t)
        try:
            card.configure(border_color=c)
        except Exception:
            return
        if t < 1.0:
            card.after(30, self._card_hover_anim, card, start, target, step + 1)

    # ================================================================
    #  UI CONSTRUCTION
    # ================================================================

    def _build_top_bar(self):
        """Top bar: logo left, address input center, settings+update right."""
        bar = ctk.CTkFrame(self.main_frame, fg_color=BG_CARD, corner_radius=0, height=56)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        inner = ctk.CTkFrame(bar, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=0, expand=True)

        # -- Left: app name --
        name_frame = ctk.CTkFrame(inner, fg_color="transparent")
        name_frame.pack(side="left", pady=10)

        accent_bar = ctk.CTkFrame(name_frame, fg_color=ACCENT, width=4, height=28,
                                  corner_radius=2)
        accent_bar.pack(side="left", padx=(0, 8))

        self._title_letters = []
        title_font = ctk.CTkFont(size=20, weight="bold")
        for ch in "RemoteDesktop":
            lbl = ctk.CTkLabel(name_frame, text=ch, font=title_font,
                               text_color=TEXT_PRIMARY)
            lbl.pack(side="left", padx=0)
            self._title_letters.append(lbl)
        self._title_wave_step = 0
        self._animate_title_wave()

        # -- Right: update + settings --
        right_frame = ctk.CTkFrame(inner, fg_color="transparent")
        right_frame.pack(side="right", pady=10)

        if updater is not None:
            self._update_btn = ctk.CTkButton(
                right_frame, text="Обновление", width=90, height=30,
                corner_radius=8, font=ctk.CTkFont(size=11),
                fg_color=BG_INPUT, hover_color=BORDER,
                text_color=TEXT_SECONDARY,
                command=self._on_update,
            )
            self._update_btn.pack(side="left", padx=(0, 6))

        settings_btn = ctk.CTkButton(
            right_frame, text="⚙", width=34, height=34,
            corner_radius=8, font=ctk.CTkFont(size=18),
            fg_color=BG_INPUT, hover_color=BORDER,
            text_color=TEXT_SECONDARY,
            command=lambda: settings_ui.open_settings(self.root),
        )
        settings_btn.pack(side="left")

        # -- Center: address entry + connect button --
        center = ctk.CTkFrame(inner, fg_color="transparent")
        center.pack(side="left", fill="x", expand=True, padx=20, pady=10)

        addr_frame = ctk.CTkFrame(center, fg_color=BG_INPUT, corner_radius=8,
                                  border_width=1, border_color=BORDER)
        addr_frame.pack(fill="x")

        addr_inner = ctk.CTkFrame(addr_frame, fg_color="transparent")
        addr_inner.pack(fill="x", padx=2, pady=2)

        self.remote_id_entry = ctk.CTkEntry(
            addr_inner, textvariable=self.remote_id,
            height=32, corner_radius=6,
            font=ctk.CTkFont(family="Consolas", size=14),
            fg_color=BG_INPUT, border_width=0,
            text_color=TEXT_PRIMARY,
            placeholder_text="Введите удалённый адрес",
            placeholder_text_color=TEXT_HINT,
        )
        self.remote_id_entry.pack(side="left", fill="x", expand=True)

        self._connect_btn = ctk.CTkButton(
            addr_inner, text="➜", width=36, height=32,
            corner_radius=6, font=ctk.CTkFont(size=16, weight="bold"),
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color="#ffffff",
            command=self._on_connect,
        )
        self._connect_btn.pack(side="right", padx=(4, 0))

    def _build_id_section(self):
        """Big ID display with animated gradient canvas background."""
        id_outer = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        id_outer.pack(fill="x", padx=24, pady=(24, 0))

        # Canvas for animated gradient background
        self._id_canvas = tk.Canvas(id_outer, height=110, bg=GRAD_B,
                                    highlightthickness=0, bd=0)
        self._id_canvas.pack(fill="x")

        # Overlay frame on canvas
        id_section = ctk.CTkFrame(self._id_canvas, fg_color="transparent")
        self._id_canvas.create_window(0, 0, window=id_section, anchor="nw",
                                      tags="content")

        def _resize_canvas(event):
            self._id_canvas.itemconfigure("content", width=event.width)
        self._id_canvas.bind("<Configure>", _resize_canvas)

        ctk.CTkLabel(id_section, text="Это рабочее место",
                     font=ctk.CTkFont(size=14),
                     text_color=TEXT_SECONDARY).pack(anchor="center", pady=(16, 0))

        id_row = ctk.CTkFrame(id_section, fg_color="transparent")
        id_row.pack(anchor="center", pady=(6, 16))

        self.host_id_label = ctk.CTkLabel(
            id_row,
            text=_format_id(self.host_id),
            font=ctk.CTkFont(family="Consolas", size=38, weight="bold"),
            text_color=ACCENT,
        )
        self.host_id_label.pack(side="left")

        copy_btn = ctk.CTkButton(
            id_row, text="\U0001f4cb", width=36, height=36,
            corner_radius=8, font=ctk.CTkFont(size=16),
            fg_color=BG_CARD, hover_color=BORDER,
            text_color=TEXT_SECONDARY,
            command=self._copy_host_id,
        )
        copy_btn.pack(side="left", padx=(12, 0))

    def _build_host_toggle(self):
        """Host mode switch: 'Принимать подключения'."""
        toggle_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        toggle_frame.pack(fill="x", padx=24, pady=(16, 0))

        inner = ctk.CTkFrame(toggle_frame, fg_color=BG_CARD, corner_radius=10,
                             border_width=1, border_color=BORDER)
        inner.pack(fill="x")

        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=10)

        ctk.CTkLabel(row, text="Принимать подключения",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=TEXT_PRIMARY).pack(side="left")

        self.host_switch = ctk.CTkSwitch(
            row, text="", width=46,
            fg_color=BORDER, progress_color=GREEN,
            button_color=TEXT_PRIMARY, button_hover_color="#ffffff",
            command=self._on_host_toggle,
        )
        if self._hosting.get():
            self.host_switch.select()
        self.host_switch.pack(side="right")

        # Password row inside the same card
        pw_row = ctk.CTkFrame(inner, fg_color="transparent")
        pw_row.pack(fill="x", padx=16, pady=(0, 10))

        ctk.CTkLabel(pw_row, text="Пароль:",
                     font=ctk.CTkFont(size=12), text_color=TEXT_SECONDARY).pack(side="left")

        self.pw_entry = ctk.CTkEntry(
            pw_row, textvariable=self.password,
            height=30, width=160, corner_radius=6, show="*",
            fg_color=BG_INPUT, border_color=BORDER,
            text_color=TEXT_PRIMARY,
            placeholder_text="ключ шифрования",
            placeholder_text_color=TEXT_HINT,
        )
        self.pw_entry.pack(side="left", padx=(8, 0))

        self.pw_toggle_btn = ctk.CTkButton(
            pw_row, text="\U0001f441", width=30, height=30,
            corner_radius=6, fg_color=BG_INPUT, hover_color=BORDER,
            text_color=TEXT_SECONDARY, command=self._toggle_pw,
        )
        self.pw_toggle_btn.pack(side="left", padx=(4, 0))

        self.pw_gen_btn = ctk.CTkButton(
            pw_row, text="Сгенерировать", width=110, height=30,
            corner_radius=6, font=ctk.CTkFont(size=11),
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color="#ffffff", command=self._generate_pw,
        )
        self.pw_gen_btn.pack(side="left", padx=(6, 0))

        # Live strength indicator below the password field
        strength_row = ctk.CTkFrame(inner, fg_color="transparent")
        strength_row.pack(fill="x", padx=16, pady=(0, 10))

        ctk.CTkLabel(strength_row, text="Надёжность:",
                     font=ctk.CTkFont(size=11),
                     text_color=TEXT_HINT).pack(side="left")

        self.pw_strength_label = ctk.CTkLabel(
            strength_row, text="",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_HINT,
        )
        self.pw_strength_label.pack(side="left", padx=(6, 0))

        # Update live as the user types
        self.pw_entry.bind("<KeyRelease>", lambda e: self._update_pw_strength())
        self.password.trace_add("write", lambda *a: self._update_pw_strength())
        self._update_pw_strength()

        # Relay address (small)
        addr_row = ctk.CTkFrame(inner, fg_color="transparent")
        addr_row.pack(fill="x", padx=16, pady=(0, 10))

        ctk.CTkLabel(addr_row, text="Relay:",
                     font=ctk.CTkFont(size=12), text_color=TEXT_SECONDARY).pack(side="left")

        self.addr_entry = ctk.CTkEntry(
            addr_row, textvariable=self.address,
            height=30, width=200, corner_radius=6,
            fg_color=BG_INPUT, border_color=BORDER,
            text_color=TEXT_PRIMARY,
            placeholder_text="IP:порт",
            placeholder_text_color=TEXT_HINT,
        )
        self.addr_entry.pack(side="left", padx=(8, 0))

    def _build_tab_bar(self):
        """Tab bar: Недавние Сеансы | Избранное | Обнаруженные."""
        self.tab_bar = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.tab_bar.pack(fill="x", padx=24, pady=(16, 0))

        sep = ctk.CTkFrame(self.tab_bar, fg_color=BORDER, height=1)
        sep.pack(fill="x", pady=(0, 0))

        btn_row = ctk.CTkFrame(self.tab_bar, fg_color="transparent")
        btn_row.pack(fill="x")

        self._tab_buttons = {}
        tabs = [
            ("recent", "Недавние Сеансы"),
            ("favorites", "Избранное"),
            ("discovered", "Обнаруженные"),
        ]
        for key, label in tabs:
            btn = ctk.CTkButton(
                btn_row, text=label, height=34,
                corner_radius=0, font=ctk.CTkFont(size=13),
                fg_color="transparent", hover_color=BG_CARD,
                text_color=TEXT_HINT,
                command=lambda k=key: self._switch_tab(k),
            )
            btn.pack(side="left", padx=(0, 2))
            self._tab_buttons[key] = btn

    def _build_tab_content(self):
        """Scrollable area for session/device cards."""
        self.tab_content = ctk.CTkScrollableFrame(
            self.main_frame, fg_color="transparent",
            corner_radius=0,
        )
        self.tab_content.pack(fill="both", expand=True, padx=24, pady=(8, 0))

    def _build_footer(self):
        """Footer with status indicator and version."""
        self.footer_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.footer_frame.pack(fill="x", padx=24, pady=(4, 12))

        status_row = ctk.CTkFrame(self.footer_frame, fg_color="transparent")
        status_row.pack(side="left")

        self.status_dot = ctk.CTkLabel(status_row, text="⬤",
                                       font=ctk.CTkFont(size=10),
                                       text_color=TEXT_HINT, width=16)
        self.status_dot.pack(side="left")
        self.status_text = ctk.CTkLabel(status_row, text="Готов к подключению",
                                        font=ctk.CTkFont(size=11),
                                        text_color=TEXT_HINT)
        self.status_text.pack(side="left", padx=(4, 0))

        try:
            from version import __version__ as _ver
        except Exception:
            _ver = "?"
        ctk.CTkLabel(self.footer_frame, text=f"v{_ver}", font=ctk.CTkFont(size=11),
                     text_color=TEXT_HINT).pack(side="right")

    # ================================================================
    #  TAB SWITCHING & CONTENT
    # ================================================================

    def _switch_tab(self, key):
        self._active_tab.set(key)
        # Animate tab button color transitions
        for k, btn in self._tab_buttons.items():
            if k == key:
                self._animate_tab_btn(btn, TEXT_HINT, ACCENT, BG_CARD, 0)
            else:
                btn.configure(text_color=TEXT_HINT, fg_color="transparent")
        # Clear content
        for w in self.tab_content.winfo_children():
            w.destroy()
        # Populate
        if key == "recent":
            self._populate_recent()
        elif key == "favorites":
            self._populate_favorites()
        elif key == "discovered":
            self._populate_discovered()
        # Fade-in new content
        self._fade_tab_content(0)

    def _animate_tab_btn(self, btn, from_color, to_color, bg_color, step):
        t = min((step + 1) / 5.0, 1.0)
        c = _lerp_color(from_color, to_color, t)
        try:
            btn.configure(text_color=c, fg_color=bg_color)
        except Exception:
            return
        if t < 1.0:
            btn.after(30, self._animate_tab_btn, btn, from_color, to_color, bg_color, step + 1)

    def _fade_tab_content(self, step):
        """Fade in tab content by transitioning child text colors from dim to normal."""
        t = min((step + 1) / 6.0, 1.0)
        # Apply progressive opacity simulation to all children
        try:
            for w in self.tab_content.winfo_children():
                # Fade card backgrounds from BG_DARK to BG_CARD
                c = _lerp_color(BG_DARK, BG_CARD, t)
                try:
                    w.configure(fg_color=c)
                except Exception:
                    pass
        except Exception:
            return
        if t < 1.0:
            self.root.after(30, self._fade_tab_content, step + 1)

    def _populate_recent(self):
        if session_history is None:
            self._empty_placeholder("Модуль истории сеансов не установлен")
            return
        try:
            sessions = session_history.get_recent()
        except Exception:
            sessions = []
        if not sessions:
            self._empty_placeholder("Нет недавних сеансов")
            return
        for s in sessions:
            self._session_card(s)

    def _populate_favorites(self):
        if session_history is None:
            self._empty_placeholder("Модуль истории сеансов не установлен")
            return
        try:
            sessions = session_history.get_favorites()
        except Exception:
            sessions = []
        if not sessions:
            self._empty_placeholder("Нет избранных")
            return
        for s in sessions:
            self._session_card(s)

    def _populate_discovered(self):
        if lan_discovery is None:
            self._empty_placeholder("Модуль обнаружения не установлен")
            return
        try:
            devices = lan_discovery.get_discovered()
        except Exception:
            devices = []
        if not devices:
            self._empty_placeholder("Устройства не найдены")
            return
        for d in devices:
            self._device_card(d)

    def _empty_placeholder(self, text):
        ctk.CTkLabel(self.tab_content, text=text,
                     font=ctk.CTkFont(size=13), text_color=TEXT_HINT).pack(
                     anchor="center", pady=30)

    def _session_card(self, session):
        """Render a session card: name, ID, OS, favorite star, online dot."""
        card = ctk.CTkFrame(self.tab_content, fg_color=BG_CARD, corner_radius=10,
                            border_width=1, border_color=BORDER, height=60)
        card.pack(fill="x", pady=(0, 6))
        card.pack_propagate(False)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=10)

        # Online dot
        is_online = getattr(session, "is_online", False)
        dot_color = GREEN if is_online else TEXT_HINT
        ctk.CTkLabel(inner, text="⬤", font=ctk.CTkFont(size=8),
                     text_color=dot_color, width=14).pack(side="left")

        # Name + ID
        info = ctk.CTkFrame(inner, fg_color="transparent")
        info.pack(side="left", padx=(6, 0))

        name = getattr(session, "name", "") or "Unknown"
        ctk.CTkLabel(info, text=name, font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=TEXT_PRIMARY).pack(anchor="w")

        sid = getattr(session, "id", "")
        os_label = getattr(session, "os", "")
        sub = _format_id(str(sid)) if sid else ""
        if os_label:
            sub += f"  ·  {os_label}"
        ctk.CTkLabel(info, text=sub, font=ctk.CTkFont(size=11),
                     text_color=TEXT_SECONDARY).pack(anchor="w")

        # Favorite star
        is_fav = getattr(session, "is_favorite", False)
        star = "★" if is_fav else "☆"
        star_color = YELLOW if is_fav else TEXT_HINT
        ctk.CTkLabel(inner, text=star, font=ctk.CTkFont(size=16),
                     text_color=star_color, width=20).pack(side="right")

        # Hover effect
        card.bind("<Enter>", lambda e, c=card: self._animate_card_hover(c, True))
        card.bind("<Leave>", lambda e, c=card: self._animate_card_hover(c, False))

        # Click to connect
        card.bind("<Button-1>", lambda e, s=session: self._connect_to_session(s))
        for child in inner.winfo_children():
            child.bind("<Button-1>", lambda e, s=session: self._connect_to_session(s))

    def _device_card(self, device):
        """Render a discovered LAN device card."""
        card = ctk.CTkFrame(self.tab_content, fg_color=BG_CARD, corner_radius=10,
                            border_width=1, border_color=BORDER, height=60)
        card.pack(fill="x", pady=(0, 6))
        card.pack_propagate(False)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=10)

        is_online = getattr(device, "is_online", False)
        dot_color = GREEN if is_online else TEXT_HINT
        ctk.CTkLabel(inner, text="⬤", font=ctk.CTkFont(size=8),
                     text_color=dot_color, width=14).pack(side="left")

        info = ctk.CTkFrame(inner, fg_color="transparent")
        info.pack(side="left", padx=(6, 0))

        name = getattr(device, "name", "") or getattr(device, "ip", "Unknown")
        ctk.CTkLabel(info, text=name, font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=TEXT_PRIMARY).pack(anchor="w")

        did = getattr(device, "id", "")
        os_label = getattr(device, "os", "")
        ip = getattr(device, "ip", "")
        sub_parts = []
        if did:
            sub_parts.append(_format_id(str(did)))
        if os_label:
            sub_parts.append(os_label)
        if ip:
            sub_parts.append(ip)
        ctk.CTkLabel(info, text="  ·  ".join(sub_parts),
                     font=ctk.CTkFont(size=11),
                     text_color=TEXT_SECONDARY).pack(anchor="w")

        # Hover effect
        card.bind("<Enter>", lambda e, c=card: self._animate_card_hover(c, True))
        card.bind("<Leave>", lambda e, c=card: self._animate_card_hover(c, False))

        card.bind("<Button-1>", lambda e, d=device: self._connect_to_device(d))

    # ================================================================
    #  ACTIONS
    # ================================================================

    def _copy_host_id(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.host_id)
        self._set_status("connected", "ИД скопирован в буфер обмена")
        self.root.after(2000, lambda: self._set_status("idle", "Готов к подключению"))

    def _toggle_pw(self):
        self._show_pw.set(not self._show_pw.get())
        self.pw_entry.configure(show="" if self._show_pw.get() else "*")

    def _update_pw_strength(self):
        """Refresh the live password strength indicator."""
        if not hasattr(self, "pw_strength_label"):
            return
        pw = self.password.get()
        if not pw:
            try:
                self.pw_strength_label.configure(text="—", text_color=TEXT_HINT)
            except Exception:
                pass
            return
        label, color = _password_strength(pw)
        try:
            self.pw_strength_label.configure(text=label, text_color=color)
        except Exception:
            pass

    def _generate_pw(self):
        """Fill the password field with a strong random password and reveal it."""
        new_pw = _generate_password(16)
        self.password.set(new_pw)
        # Reveal so the user can copy it
        self._show_pw.set(True)
        try:
            self.pw_entry.configure(show="")
        except Exception:
            pass
        self._update_pw_strength()

    def _on_update(self):
        if updater is None:
            return
        self._update_status_label = None
        # Find or create a status label for progress
        try:
            if hasattr(self, '_update_btn'):
                self._update_btn.configure(state="disabled", text="Проверка...")
        except Exception:
            pass

        def _do_update():
            try:
                has_update, latest, url, changelog = updater.check_for_update()
                if not has_update:
                    self.root.after(0, lambda: self._update_done(None, "Установлена актуальная версия"))
                    return
                self.root.after(0, lambda: self._update_progress_text("Скачивание..."))
                def _progress(downloaded, total):
                    if total > 0:
                        pct = int(downloaded * 100 / total)
                        self.root.after(0, lambda p=pct: self._update_progress_text(f"Скачивание: {p}%"))
                new_exe = updater.download_update(url, _progress)
                self.root.after(0, lambda: self._update_progress_text("Установка..."))
                updater.apply_update(new_exe)
            except Exception as e:
                self.root.after(0, lambda err=str(e): self._update_done(err, None))

        threading.Thread(target=_do_update, daemon=True).start()

    def _update_progress_text(self, text):
        try:
            if hasattr(self, '_update_btn'):
                self._update_btn.configure(text=text)
        except Exception:
            pass

    def _update_done(self, error, info):
        try:
            if hasattr(self, '_update_btn'):
                self._update_btn.configure(state="normal", text="Обновление")
        except Exception:
            pass
        if error:
            messagebox.showerror("Обновление", error, parent=self.root)
        elif info:
            messagebox.showinfo("Обновление", info, parent=self.root)

    def _on_host_toggle(self):
        hosting = self.host_switch.get()  # 1 or 0
        self._hosting.set(bool(hosting))
        if hosting:
            self._start_hosting()
        else:
            self._stop_hosting()

    def _start_hosting(self):
        """Start host in background thread."""
        try:
            args = self._build_host_args()
        except ValueError as e:
            messagebox.showwarning("Проверьте поля", str(e))
            self.host_switch.deselect()
            self._hosting.set(False)
            return
        # Warn on weak passwords — they get brute-forced in seconds.
        if _is_weak_password(self.password.get()):
            proceed = messagebox.askyesno(
                "Слабый пароль",
                "Пароль слишком слабый и может быть подобран за секунды. "
                "Продолжить?",
                parent=self.root,
            )
            if not proceed:
                self.host_switch.deselect()
                self._hosting.set(False)
                self._set_status("idle", "Готов к подключению")
                return
        self._persist()
        self._set_status("waiting", "Хост запущен, ожидание…")
        self._host_stop_event = threading.Event()
        host.LOG = lambda *a: self.root.after(0, self._host_log, " ".join(str(x) for x in a))
        host.ACCESS_PROMPT = self._access_prompt
        threading.Thread(target=host.run_host, args=(args, self._host_stop_event),
                         daemon=True).start()

    def _stop_hosting(self):
        if hasattr(self, "_host_stop_event"):
            self._host_stop_event.set()
        self._set_status("idle", "Готов к подключению")

    def _host_log(self, msg):
        """Handle host log messages while in launcher view."""
        # Could show in status bar or a small log panel; for now update status
        self._set_status("connected", msg[:60])

    def _access_prompt(self, client_id, client_name, timeout):
        """Вызывается из потока host'а при политике 'ask'. Показывает модальный
        диалог в главном (tkinter) потоке и БЛОКИРУЕТ поток host'а до ответа
        либо таймаута. Возвращает {"role","remember"} или None (отказ/таймаут)."""
        result = {"resp": None}
        done = threading.Event()

        def _show():
            try:
                win = ctk.CTkToplevel(self.root)
                win.title("Запрос подключения")
                win.geometry("420x230")
                win.configure(fg_color=BG_DARK)
                win.transient(self.root)
                win.attributes("-topmost", True)
                try:
                    win.grab_set()
                except Exception:
                    pass

                ctk.CTkLabel(
                    win,
                    text=f"ПК «{client_name}» запрашивает подключение",
                    font=ctk.CTkFont(size=15, weight="bold"),
                    text_color=TEXT_PRIMARY, wraplength=380, justify="left",
                ).pack(anchor="w", padx=20, pady=(20, 4))
                ctk.CTkLabel(
                    win, text=f"ID клиента: {_format_id(client_id) if client_id else '—'}",
                    font=ctk.CTkFont(size=12), text_color=TEXT_SECONDARY,
                ).pack(anchor="w", padx=20, pady=(0, 10))

                remember = ctk.BooleanVar(value=False)
                ctk.CTkCheckBox(win, text="Запомнить для этого ID",
                                variable=remember, font=ctk.CTkFont(size=12),
                                text_color=TEXT_SECONDARY).pack(anchor="w", padx=20, pady=(0, 12))

                def _finish(role):
                    if not done.is_set():
                        result["resp"] = {"role": role, "remember": bool(remember.get())}
                        done.set()
                    try:
                        win.destroy()
                    except Exception:
                        pass

                btns = ctk.CTkFrame(win, fg_color="transparent")
                btns.pack(fill="x", padx=20, pady=(0, 16))
                ctk.CTkButton(btns, text="Разрешить управление", fg_color=GREEN,
                              hover_color=GREEN_DIM,
                              command=lambda: _finish("control")).pack(side="left", padx=(0, 6))
                ctk.CTkButton(btns, text="Только просмотр", fg_color=ACCENT,
                              hover_color=ACCENT_HOVER,
                              command=lambda: _finish("view")).pack(side="left", padx=6)
                ctk.CTkButton(btns, text="Отклонить", fg_color=RED,
                              hover_color="#b91c1c",
                              command=lambda: _finish("deny")).pack(side="right")

                win.protocol("WM_DELETE_WINDOW", lambda: _finish("deny"))
            except Exception:
                if not done.is_set():
                    result["resp"] = None
                    done.set()

        self.root.after(0, _show)
        # Блокируем поток host'а (не главный!) до ответа или таймаута.
        if not done.wait(timeout=max(1, int(timeout))):
            # Таймаут: трактуем как отказ; диалог закроется сам при ответе позже.
            return None
        return result["resp"]

    def _on_connect(self):
        """Connect button in the top bar address field."""
        remote = self.remote_id.get().replace(" ", "").strip()
        if not remote:
            messagebox.showwarning("Подключение",
                                   "Введите ID удалённого ПК.")
            return
        self._do_client_connect(remote)

    def _connect_to_session(self, session):
        sid = str(getattr(session, "id", ""))
        if sid:
            self.remote_id.set(_format_id(sid))
            self._do_client_connect(sid)

    def _connect_to_device(self, device):
        did = str(getattr(device, "id", ""))
        ip = getattr(device, "ip", "")
        port = getattr(device, "port", "")
        if did:
            self.remote_id.set(_format_id(did))
            self._do_client_connect(did)
        elif ip:
            addr = f"{ip}:{port}" if port else ip
            self.remote_id.set(addr)
            self._do_direct_connect(addr)

    def _launch_client_process(self, args):
        """Run the client viewer in a SEPARATE process so pygame never shares a
        thread with this tkinter launcher. The launcher stays alive and
        responsive; when the session ends, we just update the status — no crash,
        no closing the whole app. Password is passed via stdin (never on disk)."""
        payload = json.dumps({
            "password": args.password,
            "relay": getattr(args, "relay", None),
            "connect": getattr(args, "connect", None),
            "unique_id": getattr(args, "unique_id", None),
            "id": getattr(args, "id", "default"),
            "downloads": getattr(args, "downloads", DEFAULT_DOWNLOADS),
        }).encode("utf-8")

        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--rd-client"]
        else:
            cmd = [sys.executable, os.path.abspath(sys.argv[0]), "--rd-client"]

        err_path = os.path.join(APP_DIR, "client_error.txt")
        try:
            os.remove(err_path)
        except OSError:
            pass

        try:
            devnull = open(os.devnull, "wb")
            p = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=devnull, stderr=devnull,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            p.stdin.write(payload)
            p.stdin.close()
        except Exception as e:
            messagebox.showerror("Ошибка подключения", str(e), parent=self.root)
            self._set_status("error", str(e)[:60])
            return

        self._set_status("connected", "Сессия активна")
        self._poll_client_process(p, err_path)

    def _poll_client_process(self, p, err_path):
        """Poll the client subprocess; update launcher status when it ends."""
        if p.poll() is None:
            self.root.after(600, self._poll_client_process, p, err_path)
            return
        if p.returncode not in (0, None) and os.path.exists(err_path):
            try:
                msg = open(err_path, encoding="utf-8").read().strip()
            except Exception:
                msg = "Соединение завершено с ошибкой."
            self._set_status("error", msg[:60])
            messagebox.showerror("Подключение", msg, parent=self.root)
        else:
            self._set_status("idle", "Сессия завершена")

    def _do_client_connect(self, remote_id):
        """Connect to a remote host via relay using its 9-digit ID."""
        remote = remote_id.replace(" ", "").strip()
        if not remote.isdigit() or len(remote) != 9:
            messagebox.showwarning("Подключение",
                                   "ID должен состоять из 9 цифр.")
            return
        addr = self.address.get().strip()
        pw = self.password.get()
        if not addr:
            messagebox.showwarning("Подключение",
                                   "Укажите адрес relay.")
            return
        if not pw:
            messagebox.showwarning("Подключение",
                                   "Укажите пароль.")
            return
        self._persist()
        args = Args(password=pw, relay=addr, unique_id=remote)
        self._set_status("waiting", "Подключение…")
        self._launch_client_process(args)

    def _do_direct_connect(self, addr):
        """Connect directly to IP:port."""
        pw = self.password.get()
        if not pw:
            messagebox.showwarning("Подключение",
                                   "Укажите пароль.")
            return
        self._persist()
        args = Args(password=pw, connect=addr)
        self._set_status("waiting", "Подключение…")
        self._launch_client_process(args)

    # ================================================================
    #  ARGS BUILDING
    # ================================================================

    def _build_host_args(self):
        """Build Args for hosting mode."""
        addr = self.address.get().strip()
        pw = self.password.get()
        if not addr:
            raise ValueError("Укажите адрес relay.")
        if not pw:
            raise ValueError("Укажите пароль.")
        cfg = load_config()
        a = Args(password=pw, id="default")
        a.downloads = self.downloads.get().strip() or DEFAULT_DOWNLOADS
        a.quality = int(self.quality.get())
        a.fps = int(self.fps.get())
        a.scale = {"100%": 1.0, "75%": 0.75, "50%": 0.5}.get(self.scale.get(), 1.0)
        a.codec = {"Авто": "auto", "JPEG": "jpeg", "PNG": "png"}.get(self.codec.get(), "auto")
        a.engine = {"Видео H.264": "auto",
                    "Плитки (совместимость)": "tiles"}.get(self.engine.get(), "auto")
        # Подтягиваем выбранный энкодер из настроек (settings_display.py).
        try:
            from settings_config import config as _scfg
            a.hw_encoder = _scfg.get("hw_encoder", "auto")
        except Exception:
            a.hw_encoder = "auto"
        a.relay = addr
        a.unique_id = self.host_id
        return a

    def _build_args(self):
        """Legacy _build_args for compatibility."""
        role = self.role.get()
        is_relay = self.conn.get() == "relay"
        addr = self.address.get().strip()
        pw = self.password.get()
        if not addr:
            raise ValueError("Укажите адрес/порт.")
        if not pw:
            raise ValueError("Укажите пароль.")

        a = Args(password=pw, id="default")
        if role == "host":
            a.downloads = self.downloads.get().strip() or DEFAULT_DOWNLOADS
            a.quality = int(self.quality.get())
            a.fps = int(self.fps.get())
            a.scale = {"100%": 1.0, "75%": 0.75, "50%": 0.5}.get(self.scale.get(), 1.0)
            a.codec = {"Авто": "auto", "JPEG": "jpeg", "PNG": "png"}.get(self.codec.get(), "auto")
            a.engine = {"Видео H.264": "auto",
                        "Плитки (совместимость)": "tiles"}.get(self.engine.get(), "auto")
            if is_relay:
                a.relay = addr
                a.unique_id = self.host_id
            else:
                a.listen = int(addr)
        else:
            if is_relay:
                a.relay = addr
                remote = self.remote_id.get().replace(" ", "").strip()
                if not remote:
                    raise ValueError("Укажите ID удалённого ПК.")
                if not remote.isdigit() or len(remote) != 9:
                    raise ValueError("ID должен состоять из 9 цифр.")
                a.unique_id = remote
            else:
                a.connect = addr
        return role, a

    def _persist(self):
        save_config({
            "role": self.role.get(), "conn": self.conn.get(),
            "address": self.address.get(), "remote_id": self.remote_id.get(),
            "password": self.password.get(), "downloads": self.downloads.get(),
            "quality": self.quality.get(), "fps": self.fps.get(), "scale": self.scale.get(),
            "codec": self.codec.get(), "engine": self.engine.get(),
            "hosting": self._hosting.get(),
        })

    def _pick_dir(self):
        d = filedialog.askdirectory(initialdir=self.downloads.get() or os.path.expanduser("~"))
        if d:
            self.downloads.set(d)

    # ================================================================
    #  START (legacy — kept for host window mode)
    # ================================================================

    def _start(self):
        try:
            role, args = self._build_args()
        except ValueError as e:
            messagebox.showwarning("Проверьте поля", str(e))
            return
        self._persist()
        self._set_status("waiting", "Подключение…")

        if role == "host":
            self._run_host_window(args)
        else:
            self._launch_client_process(args)

    def _run_host_window(self, args):
        """Hide launcher, show host log window (dark-themed)."""
        self.main_frame.destroy()
        self.root.title("RemoteDesktop — хост")
        self.root.resizable(True, True)
        self.root.geometry("700x500")
        self.root.configure(fg_color=BG_DARK)

        container = ctk.CTkFrame(self.root, fg_color=BG_DARK, corner_radius=0)
        container.pack(fill="both", expand=True, padx=0, pady=0)

        header = ctk.CTkFrame(container, fg_color=BG_CARD, corner_radius=0, height=56)
        header.pack(fill="x")
        header.pack_propagate(False)

        header_inner = ctk.CTkFrame(header, fg_color="transparent")
        header_inner.pack(fill="x", padx=16, pady=0)

        self.host_status_dot = ctk.CTkLabel(header_inner, text="⬤",
                                            font=ctk.CTkFont(size=12),
                                            text_color=YELLOW, width=20)
        self.host_status_dot.pack(side="left", pady=16)
        self.host_status_lbl = ctk.CTkLabel(header_inner,
                                            text="Хост запущен. Ожидание подключения...",
                                            font=ctk.CTkFont(size=14, weight="bold"),
                                            text_color=TEXT_PRIMARY)
        self.host_status_lbl.pack(side="left", padx=(6, 0), pady=16)

        log_frame = ctk.CTkFrame(container, fg_color=BG_CARD, corner_radius=12,
                                 border_width=1, border_color=BORDER)
        log_frame.pack(fill="both", expand=True, padx=16, pady=(12, 8))

        self.log_text = tk.Text(log_frame, bg=BG_INPUT, fg=TEXT_PRIMARY,
                                insertbackground=TEXT_PRIMARY,
                                selectbackground=ACCENT,
                                font=("Consolas", 10),
                                relief="flat", bd=0,
                                state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True, padx=8, pady=8)

        scrollbar = ctk.CTkScrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side="right", fill="y", padx=(0, 4), pady=4)
        self.log_text.configure(yscrollcommand=scrollbar.set)

        footer = ctk.CTkFrame(container, fg_color="transparent")
        footer.pack(fill="x", padx=16, pady=(0, 12))

        stop_btn = ctk.CTkButton(footer, text="Остановить и выйти",
                                 height=40, corner_radius=10,
                                 font=ctk.CTkFont(size=14, weight="bold"),
                                 fg_color=RED, hover_color="#dc2626",
                                 text_color="#ffffff",
                                 command=lambda: on_close())
        stop_btn.pack(side="right")

        ctk.CTkLabel(footer, text="Этим ПК можно управлять с клиента.",
                     font=ctk.CTkFont(size=11),
                     text_color=TEXT_HINT).pack(side="left")

        def append(msg):
            self.log_text.config(state="normal")
            self.log_text.insert("end", str(msg).rstrip() + "\n")
            self.log_text.see("end")
            self.log_text.config(state="disabled")

        host.LOG = lambda *a: self.root.after(0, append, " ".join(str(x) for x in a))

        stop_event = threading.Event()
        threading.Thread(target=host.run_host, args=(args, stop_event), daemon=True).start()

        def on_close():
            stop_event.set()
            self.root.destroy()
        self.root.protocol("WM_DELETE_WINDOW", on_close)

    # ================================================================
    #  STATUS BAR
    # ================================================================

    def _set_status(self, state="idle", text=None):
        colors = {"idle": TEXT_HINT, "connected": GREEN, "error": RED, "waiting": YELLOW}
        if self._status_pulse_id:
            self.root.after_cancel(self._status_pulse_id)
            self._status_pulse_id = None
        self.status_dot.configure(text_color=colors.get(state, TEXT_HINT))
        if text:
            self.status_text.configure(text=text)
        if state == "waiting":
            self._pulse_status(YELLOW, 0)
        elif state == "connected":
            self._pulse_status_connected(0)

    def _pulse_status(self, color, step):
        alpha = 0.4 + 0.6 * abs(math.sin(step * 0.08))
        c = _lerp_color(BG_DARK, color, alpha)
        try:
            self.status_dot.configure(text_color=c)
        except Exception:
            return
        self._status_pulse_id = self.root.after(40, self._pulse_status, color, step + 1)

    def _pulse_status_connected(self, step):
        """Smooth green pulse between dim and bright when connected."""
        t = (math.sin(step * 0.05) + 1) / 2
        c = _lerp_color(GREEN_DIM, GREEN, t)
        try:
            self.status_dot.configure(text_color=c)
        except Exception:
            return
        self._status_pulse_id = self.root.after(50, self._pulse_status_connected, step + 1)


def _setup_logging():
    if not getattr(sys, "frozen", False):
        return
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        f = open(os.path.join(APP_DIR, "app.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = f
    except Exception:
        pass

    def hook(exc_type, exc, tb):
        traceback.print_exception(exc_type, exc, tb)
        try:
            err = tk.Tk(); err.withdraw()
            messagebox.showerror("RemoteDesktop — ошибка", f"{exc}\n\nПодробности в app.log")
            err.destroy()
        except Exception:
            pass
    sys.excepthook = hook


def _run_client_subprocess():
    """Client-viewer mode: read args JSON from stdin, run the pygame viewer in
    THIS dedicated process so it never shares a thread with the launcher's
    tkinter loop (mixing pygame+tk crashed the app when a session ended).
    Any startup error is written to client_error.txt for the parent to show."""
    try:
        raw = sys.stdin.buffer.read().decode("utf-8")
        cfg = json.loads(raw)
    except Exception:
        sys.exit(2)
    try:
        client.run_client(Args(**cfg))
    except Exception as e:
        try:
            os.makedirs(APP_DIR, exist_ok=True)
            with open(os.path.join(APP_DIR, "client_error.txt"), "w", encoding="utf-8") as f:
                f.write(str(e))
        except Exception:
            pass
        sys.exit(2)
    sys.exit(0)


def _run_host_headless():
    """Unattended host mode (no GUI) for autostart/service. Reads the saved
    config and runs host.run_host until the process is stopped. Screen capture
    only works in the user's interactive session, so this is launched at LOGON
    (see install_service.ps1), NOT as a SYSTEM/session-0 service."""
    _setup_logging()
    cfg = load_config()
    pw = cfg.get("password", "")
    if not pw:
        print("[host-headless] нет пароля в конфиге — нечего запускать")
        return
    host_id = _get_or_create_host_id()
    a = Args(password=pw, id=cfg.get("id", "default"))
    a.downloads = cfg.get("downloads", DEFAULT_DOWNLOADS)
    try:
        a.quality = int(cfg.get("quality", 70))
        a.fps = int(cfg.get("fps", 20))
    except (TypeError, ValueError):
        a.quality, a.fps = 70, 20
    a.scale = {"100%": 1.0, "75%": 0.75, "50%": 0.5}.get(cfg.get("scale", "100%"), 1.0)
    a.engine = {"Видео H.264": "auto",
                "Плитки (совместимость)": "tiles"}.get(cfg.get("engine", "Видео H.264"), "auto")
    if cfg.get("conn", "relay") == "direct":
        try:
            a.listen = int(cfg.get("listen_port", 5900))
        except (TypeError, ValueError):
            a.listen = 5900
    else:
        a.relay = cfg.get("address", "").strip()
        a.unique_id = host_id
        if not a.relay:
            print("[host-headless] нет адреса relay в конфиге")
            return
    print(f"[host-headless] запуск хоста, ID {host_id}")
    try:
        host.run_host(a)
    except Exception as e:
        print(f"[host-headless] хост остановлен: {e}")


def main():
    if "--rd-client" in sys.argv:
        _run_client_subprocess()
        return
    if "--rd-host" in sys.argv:
        _run_host_headless()
        return
    _setup_logging()
    if updater is not None:
        try:
            updater.cleanup_old_update()
        except Exception:
            pass
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    root.geometry("540x700")
    root.attributes("-alpha", 0.0)
    LauncherUI(root)
    _fade_in(root, 0.0)
    root.mainloop()


def _fade_in(root, alpha):
    alpha = min(alpha + 0.06, 1.0)
    root.attributes("-alpha", alpha)
    if alpha < 1.0:
        root.after(16, _fade_in, root, alpha)


if __name__ == "__main__":
    main()
