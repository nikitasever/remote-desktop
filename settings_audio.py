"""Audio settings page — «Звук» tab for the settings UI."""

import os
import customtkinter as ctk
from tkinter import filedialog

try:
    import sounddevice as _sd
except ImportError:
    _sd = None


def _list_devices(kind):
    """Return list of device names for 'input' or 'output'. Empty if unavailable."""
    if _sd is None:
        return []
    try:
        devices = _sd.query_devices()
        return [
            d["name"] for d in devices
            if d[f"max_{kind}_channels"] > 0
        ]
    except Exception:
        return []


def create_page(parent, config):
    """Build and return the Audio settings frame."""

    BG = "#111827"
    TEXT = "white"
    ACCENT = "#3b82f6"
    HEADER_FONT = ("Segoe UI", 15, "bold")
    NORMAL_FONT = ("Segoe UI", 13)

    page = ctk.CTkFrame(parent, fg_color=BG)

    # ── helpers ──────────────────────────────────────────────────────────
    def _section(label_text):
        lbl = ctk.CTkLabel(page, text=label_text, font=HEADER_FONT,
                           text_color=TEXT, anchor="w")
        lbl.pack(fill="x", padx=16, pady=(18, 6))

    def _radios(options, config_key, default):
        var = ctk.StringVar(value=config.get(config_key, default))

        def _on_change(*_a):
            config.set(config_key, var.get())

        var.trace_add("write", _on_change)
        for label, value in options:
            rb = ctk.CTkRadioButton(page, text=label, variable=var,
                                    value=value, font=NORMAL_FONT,
                                    text_color=TEXT, fg_color=ACCENT,
                                    hover_color=ACCENT,
                                    border_color=TEXT)
            rb.pack(anchor="w", padx=32, pady=2)
        return var

    def _checkbox(label_text, config_key, default):
        var = ctk.BooleanVar(value=config.get(config_key, default))

        def _on_change(*_a):
            config.set(config_key, var.get())

        var.trace_add("write", _on_change)
        ctk.CTkCheckBox(page, text=label_text, variable=var,
                        font=NORMAL_FONT, text_color=TEXT,
                        fg_color=ACCENT, hover_color=ACCENT,
                        border_color=TEXT).pack(anchor="w", padx=32, pady=2)
        return var

    # ── Передача звука ───────────────────────────────────────────────────
    _section("Передача звука")
    _checkbox("Включить передачу звука", "audio.enabled", True)

    _radios([
        ("Только воспроизведение (слушать удалённый ПК)", "playback"),
        ("Только захват (передавать микрофон)", "capture"),
        ("Двусторонняя передача", "both"),
    ], "audio.direction", "playback")

    # ── Качество звука ───────────────────────────────────────────────────
    _section("Качество звука")
    _radios([
        ("Низкое (экономия трафика)", "low"),
        ("Среднее", "medium"),
        ("Высокое", "high"),
    ], "audio.quality", "medium")

    # ── Устройства ───────────────────────────────────────────────────────
    _section("Устройства")

    default_label = "По умолчанию"

    # Output device
    output_devices = [default_label] + _list_devices("output")
    ctk.CTkLabel(page, text="Устройство вывода:", font=NORMAL_FONT,
                 text_color=TEXT, anchor="w").pack(fill="x", padx=32, pady=(6, 2))

    out_var = ctk.StringVar(value=config.get("audio.output_device", default_label))
    out_combo = ctk.CTkComboBox(page, values=output_devices, variable=out_var,
                                font=NORMAL_FONT, width=350,
                                fg_color="#1f2937", border_color=ACCENT,
                                button_color=ACCENT, button_hover_color=ACCENT,
                                text_color=TEXT, dropdown_fg_color="#1f2937",
                                dropdown_text_color=TEXT,
                                dropdown_hover_color=ACCENT)
    out_combo.pack(anchor="w", padx=32, pady=2)

    def _on_out(*_a):
        config.set("audio.output_device", out_var.get())

    out_var.trace_add("write", _on_out)

    # Input device
    input_devices = [default_label] + _list_devices("input")
    ctk.CTkLabel(page, text="Устройство ввода:", font=NORMAL_FONT,
                 text_color=TEXT, anchor="w").pack(fill="x", padx=32, pady=(10, 2))

    in_var = ctk.StringVar(value=config.get("audio.input_device", default_label))
    in_combo = ctk.CTkComboBox(page, values=input_devices, variable=in_var,
                               font=NORMAL_FONT, width=350,
                               fg_color="#1f2937", border_color=ACCENT,
                               button_color=ACCENT, button_hover_color=ACCENT,
                               text_color=TEXT, dropdown_fg_color="#1f2937",
                               dropdown_text_color=TEXT,
                               dropdown_hover_color=ACCENT)
    in_combo.pack(anchor="w", padx=32, pady=2)

    def _on_in(*_a):
        config.set("audio.input_device", in_var.get())

    in_var.trace_add("write", _on_in)

    # ── Запись экрана ────────────────────────────────────────────────────
    _section("Запись экрана")
    _checkbox("Автоматически записывать сеансы", "recording.auto_record", False)

    # Directory picker
    default_dir = config.get("recording.directory",
                             os.path.join(os.path.expanduser("~"), "Recordings"))
    dir_var = ctk.StringVar(value=default_dir)

    dir_frame = ctk.CTkFrame(page, fg_color="transparent")
    dir_frame.pack(fill="x", padx=32, pady=(6, 2))

    dir_entry = ctk.CTkEntry(dir_frame, textvariable=dir_var, font=NORMAL_FONT,
                             text_color=TEXT, fg_color="#1f2937",
                             border_color=ACCENT, width=300)
    dir_entry.pack(side="left", padx=(0, 8))

    def _browse():
        chosen = filedialog.askdirectory(initialdir=dir_var.get())
        if chosen:
            dir_var.set(chosen)

    ctk.CTkButton(dir_frame, text="Обзор…", command=_browse, width=80,
                  font=NORMAL_FONT, fg_color=ACCENT,
                  hover_color="#2563eb", text_color=TEXT).pack(side="left")

    def _on_dir(*_a):
        config.set("recording.directory", dir_var.get())

    dir_var.trace_add("write", _on_dir)

    return page
