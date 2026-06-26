"""
RemoteDesktop — единое окно-лаунчер.

Выбираете режим (этот ПК показывает экран / подключиться к другому ПК),
вводите адрес и пароль — и нажимаете «Запустить». Командная строка не нужна.

Собирается в один app.exe (см. build_exe.ps1).
"""

import json
import os
import random
import sys
import traceback
import threading
import tkinter as tk
from tkinter import messagebox, filedialog

import customtkinter as ctk

import host
import client

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
RED = "#ef4444"
YELLOW = "#eab308"

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
        self.engine = "auto"   # auto/x264 — видео H.264; tiles — старые плитки
        self.unique_id = None  # 9-digit host ID for relay routing
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


class LauncherUI:
    def __init__(self, root):
        self.root = root
        root.title("RemoteDesktop")
        root.resizable(True, True)
        root.minsize(480, 400)
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

        # Load/generate persistent host ID
        self.host_id = _get_or_create_host_id()

        # Main container
        self.main_frame = ctk.CTkFrame(root, fg_color=BG_DARK, corner_radius=0)
        self.main_frame.pack(fill="both", expand=True, padx=0, pady=0)

        self._build_header()
        self._build_role_section(cfg)
        self._build_connection_section()
        self._build_id_card()
        self._build_fields_section()
        self._build_host_settings()
        self._build_start_button()
        self._build_footer()
        self._refresh()

    # ======== UI construction ========

    def _build_header(self):
        """Logo / title area at the top."""
        header = ctk.CTkFrame(self.main_frame, fg_color=BG_DARK, corner_radius=0)
        header.pack(fill="x", padx=24, pady=(20, 4))

        title_frame = ctk.CTkFrame(header, fg_color="transparent")
        title_frame.pack(anchor="w")

        # Colored accent bar
        accent_bar = ctk.CTkFrame(title_frame, fg_color=ACCENT, width=4, height=32,
                                  corner_radius=2)
        accent_bar.pack(side="left", padx=(0, 12))

        ctk.CTkLabel(title_frame, text="Remote", font=ctk.CTkFont(size=26, weight="bold"),
                     text_color=TEXT_PRIMARY).pack(side="left")
        ctk.CTkLabel(title_frame, text="Desktop", font=ctk.CTkFont(size=26, weight="bold"),
                     text_color=ACCENT).pack(side="left")

    def _build_role_section(self, cfg):
        """Role selector: segmented button for host / client."""
        card = self._card(self.main_frame, pad_top=12)

        ctk.CTkLabel(card, text="Режим работы", font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=TEXT_PRIMARY).pack(anchor="w", padx=4, pady=(0, 8))

        role_map = {"client": "Подключиться к ПК", "host": "Этот ПК (дать управление)"}
        initial = role_map.get(self.role.get(), "Подключиться к ПК")

        self.role_seg = ctk.CTkSegmentedButton(
            card,
            values=list(role_map.values()),
            command=self._on_role_change,
            font=ctk.CTkFont(size=13),
            fg_color=BG_INPUT,
            selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER,
            unselected_color=BG_INPUT,
            unselected_hover_color=BORDER,
            text_color=TEXT_PRIMARY,
            text_color_disabled=TEXT_HINT,
            corner_radius=8,
        )
        self.role_seg.set(initial)
        self.role_seg.pack(fill="x", padx=4)
        self._role_map_inv = {v: k for k, v in role_map.items()}

    def _on_role_change(self, value):
        self.role.set(self._role_map_inv.get(value, "client"))
        self._refresh()

    def _build_connection_section(self):
        """Connection type: relay vs direct."""
        card = self._card(self.main_frame, pad_top=8)

        ctk.CTkLabel(card, text="Соединение", font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=TEXT_PRIMARY).pack(anchor="w", padx=4, pady=(0, 8))

        conn_map = {"relay": "Relay (NAT)", "direct": "Прямое (LAN)"}
        initial = conn_map.get(self.conn.get(), "Relay (NAT)")

        self.conn_seg = ctk.CTkSegmentedButton(
            card,
            values=list(conn_map.values()),
            command=self._on_conn_change,
            font=ctk.CTkFont(size=13),
            fg_color=BG_INPUT,
            selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER,
            unselected_color=BG_INPUT,
            unselected_hover_color=BORDER,
            text_color=TEXT_PRIMARY,
            text_color_disabled=TEXT_HINT,
            corner_radius=8,
        )
        self.conn_seg.set(initial)
        self.conn_seg.pack(fill="x", padx=4)
        self._conn_map_inv = {v: k for k, v in conn_map.items()}

    def _on_conn_change(self, value):
        self.conn.set(self._conn_map_inv.get(value, "relay"))
        self._refresh()

    def _build_id_card(self):
        """Host ID display card (AnyDesk-style) and client remote ID input."""
        # -- Host ID card: shown in host+relay mode --
        self.host_id_wrapper = ctk.CTkFrame(self.main_frame, fg_color="transparent",
                                            corner_radius=0)
        # Don't pack yet — _refresh() controls visibility

        self.host_id_card = ctk.CTkFrame(self.host_id_wrapper, fg_color=BG_CARD,
                                         corner_radius=12, border_width=2,
                                         border_color=ACCENT)
        self.host_id_card.pack(fill="x", padx=24, pady=(8, 0))

        id_inner = ctk.CTkFrame(self.host_id_card, fg_color="transparent")
        id_inner.pack(fill="x", padx=16, pady=14)

        ctk.CTkLabel(id_inner, text="Ваш ID для подключения:",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=TEXT_SECONDARY).pack(anchor="w", padx=4, pady=(0, 8))

        id_row = ctk.CTkFrame(id_inner, fg_color="transparent")
        id_row.pack(fill="x", padx=4)

        self.host_id_label = ctk.CTkLabel(
            id_row,
            text=_format_id(self.host_id),
            font=ctk.CTkFont(family="Consolas", size=30, weight="bold"),
            text_color=ACCENT,
        )
        self.host_id_label.pack(side="left")

        copy_btn = ctk.CTkButton(
            id_row, text="Копировать", width=100, height=36,
            corner_radius=8,
            font=ctk.CTkFont(size=12),
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color="#ffffff",
            command=self._copy_host_id,
        )
        copy_btn.pack(side="right")

        ctk.CTkLabel(id_inner,
                     text="Сообщите этот ID и пароль для подключения к вашему ПК",
                     font=ctk.CTkFont(size=11), text_color=TEXT_HINT,
                     wraplength=380, justify="left").pack(anchor="w", padx=4, pady=(8, 0))

        # -- Client remote ID input: shown in client+relay mode --
        self.client_id_wrapper = ctk.CTkFrame(self.main_frame, fg_color="transparent",
                                              corner_radius=0)
        # Don't pack yet — _refresh() controls visibility

        client_id_card = ctk.CTkFrame(self.client_id_wrapper, fg_color=BG_CARD,
                                      corner_radius=12, border_width=1,
                                      border_color=BORDER)
        client_id_card.pack(fill="x", padx=24, pady=(8, 0))

        cid_inner = ctk.CTkFrame(client_id_card, fg_color="transparent")
        cid_inner.pack(fill="x", padx=16, pady=14)

        ctk.CTkLabel(cid_inner, text="ID удалённого ПК:",
                     font=ctk.CTkFont(size=12), text_color=TEXT_SECONDARY).pack(
                     anchor="w", padx=4, pady=(0, 2))
        self.remote_id_entry = ctk.CTkEntry(
            cid_inner, textvariable=self.remote_id,
            height=40, corner_radius=8,
            font=ctk.CTkFont(family="Consolas", size=18),
            fg_color=BG_INPUT, border_color=BORDER,
            text_color=TEXT_PRIMARY,
            placeholder_text="xxx xxx xxx",
            placeholder_text_color=TEXT_HINT,
        )
        self.remote_id_entry.pack(fill="x", padx=4, pady=(0, 4))

        ctk.CTkLabel(cid_inner,
                     text="Введите ID удалённого ПК и пароль",
                     font=ctk.CTkFont(size=11), text_color=TEXT_HINT,
                     wraplength=380, justify="left").pack(anchor="w", padx=4, pady=(4, 0))

    def _copy_host_id(self):
        """Copy host ID to clipboard."""
        self.root.clipboard_clear()
        self.root.clipboard_append(self.host_id)
        self._set_status("connected", "ID скопирован в буфер обмена")
        self.root.after(2000, lambda: self._set_status("idle", "Готов к подключению"))

    def _build_fields_section(self):
        """Address and password fields."""
        self.fields_card = self._card(self.main_frame, pad_top=8)

        # Address row
        self.addr_lbl = ctk.CTkLabel(self.fields_card, text="Адрес relay:",
                                     font=ctk.CTkFont(size=12), text_color=TEXT_SECONDARY)
        self.addr_lbl.pack(anchor="w", padx=4, pady=(0, 2))
        self.addr_entry = ctk.CTkEntry(self.fields_card, textvariable=self.address,
                                       height=36, corner_radius=8,
                                       fg_color=BG_INPUT, border_color=BORDER,
                                       text_color=TEXT_PRIMARY,
                                       placeholder_text="IP:порт",
                                       placeholder_text_color=TEXT_HINT)
        self.addr_entry.pack(fill="x", padx=4, pady=(0, 8))

        # Password row
        pw_label_frame = ctk.CTkFrame(self.fields_card, fg_color="transparent")
        pw_label_frame.pack(fill="x", padx=4, pady=(0, 2))
        ctk.CTkLabel(pw_label_frame, text="Пароль:", font=ctk.CTkFont(size=12),
                     text_color=TEXT_SECONDARY).pack(side="left")

        pw_frame = ctk.CTkFrame(self.fields_card, fg_color="transparent")
        pw_frame.pack(fill="x", padx=4, pady=(0, 8))
        self.pw_entry = ctk.CTkEntry(pw_frame, textvariable=self.password,
                                     height=36, corner_radius=8, show="*",
                                     fg_color=BG_INPUT, border_color=BORDER,
                                     text_color=TEXT_PRIMARY,
                                     placeholder_text="ключ шифрования",
                                     placeholder_text_color=TEXT_HINT)
        self.pw_entry.pack(side="left", fill="x", expand=True)
        self.pw_toggle_btn = ctk.CTkButton(pw_frame, text="👁", width=36, height=36,
                                           corner_radius=8,
                                           fg_color=BG_INPUT, hover_color=BORDER,
                                           text_color=TEXT_SECONDARY,
                                           command=self._toggle_pw)
        self.pw_toggle_btn.pack(side="left", padx=(6, 0))

        # Hint label
        self.hint = ctk.CTkLabel(self.fields_card, text="", font=ctk.CTkFont(size=11),
                                 text_color=TEXT_HINT, wraplength=380, justify="left")
        self.hint.pack(anchor="w", padx=4, pady=(0, 2))

    def _build_host_settings(self):
        """Host-only settings: downloads dir, quality, fps, scale, codec, engine."""
        # Outer wrapper — used for pack/pack_forget visibility toggling.
        self.host_wrapper = ctk.CTkFrame(self.main_frame, fg_color="transparent",
                                         corner_radius=0)
        self.host_wrapper.pack(fill="x", padx=0, pady=0)

        # Card frame styled like the other sections.
        self.host_card_outer = ctk.CTkFrame(self.host_wrapper, fg_color=BG_CARD,
                                            corner_radius=12, border_width=1,
                                            border_color=BORDER)
        self.host_card_outer.pack(fill="x", padx=24, pady=(8, 0))

        # Section title
        ctk.CTkLabel(self.host_card_outer, text="Настройки хоста",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=TEXT_PRIMARY).pack(anchor="w", padx=16, pady=(14, 0))

        # Inner content area
        self.host_card = ctk.CTkFrame(self.host_card_outer, fg_color="transparent")
        self.host_card.pack(fill="x", padx=16, pady=(8, 14))

        # Downloads dir
        dl_row = ctk.CTkFrame(self.host_card, fg_color="transparent")
        dl_row.pack(fill="x", padx=4, pady=(0, 8))
        ctk.CTkLabel(dl_row, text="Файлы в:", font=ctk.CTkFont(size=12),
                     text_color=TEXT_SECONDARY, width=80).pack(side="left")
        self.dl_entry = ctk.CTkEntry(dl_row, textvariable=self.downloads,
                                     height=32, corner_radius=8,
                                     fg_color=BG_INPUT, border_color=BORDER,
                                     text_color=TEXT_PRIMARY)
        self.dl_entry.pack(side="left", fill="x", expand=True, padx=(4, 4))
        ctk.CTkButton(dl_row, text="...", width=36, height=32, corner_radius=8,
                      fg_color=BG_INPUT, hover_color=BORDER,
                      text_color=TEXT_SECONDARY,
                      command=self._pick_dir).pack(side="left")

        # Settings grid: quality, fps, scale in one row
        grid1 = ctk.CTkFrame(self.host_card, fg_color="transparent")
        grid1.pack(fill="x", padx=4, pady=(0, 6))
        for i in range(6):
            grid1.columnconfigure(i, weight=1 if i % 2 == 1 else 0)

        self._grid_label(grid1, "Чёткость", 0, 0)
        self.quality_cb = ctk.CTkComboBox(grid1, variable=self.quality, width=80,
                                          values=["50", "60", "70", "80", "90"],
                                          state="readonly", height=30, corner_radius=8,
                                          fg_color=BG_INPUT, border_color=BORDER,
                                          button_color=BORDER, button_hover_color=ACCENT,
                                          dropdown_fg_color=BG_CARD,
                                          dropdown_hover_color=ACCENT,
                                          dropdown_text_color=TEXT_PRIMARY,
                                          text_color=TEXT_PRIMARY)
        self.quality_cb.grid(row=0, column=1, padx=(2, 10), sticky="w")

        self._grid_label(grid1, "FPS", 0, 2)
        self.fps_cb = ctk.CTkComboBox(grid1, variable=self.fps, width=80,
                                      values=["15", "20", "25", "30", "40", "60"],
                                      state="readonly", height=30, corner_radius=8,
                                      fg_color=BG_INPUT, border_color=BORDER,
                                      button_color=BORDER, button_hover_color=ACCENT,
                                      dropdown_fg_color=BG_CARD,
                                      dropdown_hover_color=ACCENT,
                                      dropdown_text_color=TEXT_PRIMARY,
                                      text_color=TEXT_PRIMARY)
        self.fps_cb.grid(row=0, column=3, padx=(2, 10), sticky="w")

        self._grid_label(grid1, "Масштаб", 0, 4)
        self.scale_cb = ctk.CTkComboBox(grid1, variable=self.scale, width=80,
                                        values=["100%", "75%", "50%"],
                                        state="readonly", height=30, corner_radius=8,
                                        fg_color=BG_INPUT, border_color=BORDER,
                                        button_color=BORDER, button_hover_color=ACCENT,
                                        dropdown_fg_color=BG_CARD,
                                        dropdown_hover_color=ACCENT,
                                        dropdown_text_color=TEXT_PRIMARY,
                                        text_color=TEXT_PRIMARY)
        self.scale_cb.grid(row=0, column=5, padx=2, sticky="w")

        # Codec + engine row
        grid2 = ctk.CTkFrame(self.host_card, fg_color="transparent")
        grid2.pack(fill="x", padx=4, pady=(0, 4))

        self._grid_label(grid2, "Формат", 0, 0)
        self.codec_cb = ctk.CTkComboBox(grid2, variable=self.codec, width=90,
                                        values=["Авто", "JPEG", "PNG"],
                                        state="readonly", height=30, corner_radius=8,
                                        fg_color=BG_INPUT, border_color=BORDER,
                                        button_color=BORDER, button_hover_color=ACCENT,
                                        dropdown_fg_color=BG_CARD,
                                        dropdown_hover_color=ACCENT,
                                        dropdown_text_color=TEXT_PRIMARY,
                                        text_color=TEXT_PRIMARY)
        self.codec_cb.grid(row=0, column=1, padx=(2, 10), sticky="w")

        self._grid_label(grid2, "Движок", 0, 2)
        self.engine_cb = ctk.CTkComboBox(grid2, variable=self.engine, width=180,
                                         values=["Видео H.264", "Плитки (совместимость)"],
                                         state="readonly", height=30, corner_radius=8,
                                         fg_color=BG_INPUT, border_color=BORDER,
                                         button_color=BORDER, button_hover_color=ACCENT,
                                         dropdown_fg_color=BG_CARD,
                                         dropdown_hover_color=ACCENT,
                                         dropdown_text_color=TEXT_PRIMARY,
                                         text_color=TEXT_PRIMARY)
        self.engine_cb.grid(row=0, column=3, padx=2, sticky="w")

        # Info hints
        ctk.CTkLabel(self.host_card, text="PNG — без потерь (текст идеален) | "
                     "H.264 — быстрее и легче по сети",
                     font=ctk.CTkFont(size=11), text_color=TEXT_HINT).pack(
                     anchor="w", padx=8, pady=(4, 0))

    def _build_start_button(self):
        """Big prominent Connect / Start Host button."""
        self.btn_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.btn_frame.pack(fill="x", padx=24, pady=(12, 4))

        self.start_btn = ctk.CTkButton(
            self.btn_frame,
            text="Запустить",
            height=46,
            corner_radius=10,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color="#ffffff",
            command=self._start,
        )
        self.start_btn.pack(fill="x")

    def _build_footer(self):
        """Footer with status indicator and version."""
        self.footer_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.footer_frame.pack(fill="x", padx=24, pady=(4, 16))

        # Status dot + text
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

        ctk.CTkLabel(self.footer_frame, text="v1.0", font=ctk.CTkFont(size=11),
                     text_color=TEXT_HINT).pack(side="right")

    # ======== Helpers ========

    def _card(self, parent, pad_top=8):
        """Create a card-style frame with subtle background."""
        card = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=12,
                            border_width=1, border_color=BORDER)
        card.pack(fill="x", padx=24, pady=(pad_top, 0))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=14)
        return inner

    def _grid_label(self, parent, text, row, col):
        lbl = ctk.CTkLabel(parent, text=text, font=ctk.CTkFont(size=12),
                           text_color=TEXT_SECONDARY)
        lbl.grid(row=row, column=col, padx=(0, 4), sticky="w")
        return lbl

    def _set_status(self, state="idle", text=None):
        """Update the status indicator: idle, connected, error."""
        colors = {"idle": TEXT_HINT, "connected": GREEN, "error": RED, "waiting": YELLOW}
        self.status_dot.configure(text_color=colors.get(state, TEXT_HINT))
        if text:
            self.status_text.configure(text=text)

    # ---- Динамика формы ----
    def _toggle_pw(self):
        self._show_pw.set(not self._show_pw.get())
        self.pw_entry.configure(show="" if self._show_pw.get() else "*")

    def _pick_dir(self):
        d = filedialog.askdirectory(initialdir=self.downloads.get() or os.path.expanduser("~"))
        if d:
            self.downloads.set(d)

    def _refresh(self):
        is_host = self.role.get() == "host"
        is_relay = self.conn.get() == "relay"

        # ID cards visibility — unpack all, then re-pack in order
        self.host_id_wrapper.pack_forget()
        self.client_id_wrapper.pack_forget()

        # Host settings scrollable section visibility.
        # Re-pack bottom elements to maintain correct order after show/hide.
        self.host_wrapper.pack_forget()
        self.btn_frame.pack_forget()
        self.footer_frame.pack_forget()

        # Show appropriate ID card for relay mode
        if is_relay and is_host:
            self.host_id_wrapper.pack(fill="x", padx=0, pady=0, before=self.fields_card.master)
        elif is_relay and not is_host:
            self.client_id_wrapper.pack(fill="x", padx=0, pady=0, before=self.fields_card.master)

        if is_host:
            self.host_wrapper.pack(fill="x", padx=0, pady=0)

        self.btn_frame.pack(fill="x", padx=24, pady=(12, 4))
        self.footer_frame.pack(fill="x", padx=24, pady=(4, 16))

        # Address label text
        if is_relay:
            self.addr_lbl.configure(text="Адрес relay:")
            self.address_hint = "IP:порт вашего relay-сервера, напр. 203.0.113.5:5800"
        elif is_host:
            self.addr_lbl.configure(text="Слушать порт:")
            self.address_hint = "Порт, напр. 5900 (нужен проброс/туннель к этому ПК)"
        else:
            self.addr_lbl.configure(text="Адрес ПК:")
            self.address_hint = "IP:порт удалённого ПК, напр. 100.x.y.z:5900"

        # Start button text
        self.start_btn.configure(text="Запустить хост" if is_host else "Подключиться")

        # Hints
        hints = [self.address_hint]
        if is_relay and is_host:
            hints.append("Сообщите этот ID и пароль для подключения к вашему ПК")
        elif is_relay and not is_host:
            hints.append("Введите ID удалённого ПК и пароль")
        hints.append("Пароль одинаковый на обоих ПК — это ключ шифрования.")
        self.hint.configure(text="  •  " + "\n  •  ".join(hints))

    # ---- Сборка Args из формы ----
    def _build_args(self):
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
                a.listen = int(addr)            # тут addr = порт
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
        })

    # ---- Запуск ----
    def _start(self):
        try:
            role, args = self._build_args()
        except ValueError as e:
            messagebox.showwarning("Проверьте поля", str(e))
            return
        self._persist()

        if role == "host":
            self._run_host_window(args)
        else:
            # Клиент: закрываем форму и открываем pygame-окно в главном потоке.
            self.root.destroy()
            try:
                client.run_client(args)
            except Exception as e:
                # GUI уже закрыт — покажем отдельным окном
                err = tk.Tk(); err.withdraw()
                messagebox.showerror("Ошибка подключения", str(e))
                err.destroy()

    def _run_host_window(self, args):
        """Прячем форму, показываем окно-лог хоста (dark-themed)."""
        # Clear main frame
        self.main_frame.destroy()
        self.root.title("RemoteDesktop — хост")
        self.root.resizable(True, True)
        self.root.geometry("700x500")
        self.root.configure(fg_color=BG_DARK)

        container = ctk.CTkFrame(self.root, fg_color=BG_DARK, corner_radius=0)
        container.pack(fill="both", expand=True, padx=0, pady=0)

        # Header
        header = ctk.CTkFrame(container, fg_color=BG_CARD, corner_radius=0, height=56)
        header.pack(fill="x")
        header.pack_propagate(False)

        header_inner = ctk.CTkFrame(header, fg_color="transparent")
        header_inner.pack(fill="x", padx=16, pady=0)

        # Status dot in header
        self.host_status_dot = ctk.CTkLabel(header_inner, text="⬤",
                                            font=ctk.CTkFont(size=12),
                                            text_color=YELLOW, width=20)
        self.host_status_dot.pack(side="left", pady=16)
        self.host_status_lbl = ctk.CTkLabel(header_inner,
                                            text="Хост запущен. Ожидание подключения...",
                                            font=ctk.CTkFont(size=14, weight="bold"),
                                            text_color=TEXT_PRIMARY)
        self.host_status_lbl.pack(side="left", padx=(6, 0), pady=16)

        # Log area
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

        # Footer bar
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

        # host.LOG вызывается из рабочего потока -> обновляем UI через after()
        host.LOG = lambda *a: self.root.after(0, append, " ".join(str(x) for x in a))

        stop_event = threading.Event()
        threading.Thread(target=host.run_host, args=(args, stop_event), daemon=True).start()

        def on_close():
            stop_event.set()
            self.root.destroy()
        self.root.protocol("WM_DELETE_WINDOW", on_close)


def _setup_logging():
    """В собранном --windowed exe stdout=None. Перенаправляем вывод и
    необработанные ошибки в лог-файл, чтобы окно не закрывалось «молча»."""
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


def main():
    _setup_logging()
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    root.geometry("500x850")
    LauncherUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
