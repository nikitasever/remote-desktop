"""Display settings page — «Отображение» tab for the settings UI."""

import customtkinter as ctk


def create_page(parent, config):
    """Build and return the Display settings frame."""

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
        """options: list of (label, value)"""
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

    # ── Качество ─────────────────────────────────────────────────────────
    _section("Качество")
    _radios([
        ("Лучшее качество аудио и видео", "best"),
        ("Баланс между качеством и откликом", "balanced"),
        ("Максимальное быстродействие", "performance"),
    ], "display.quality", "balanced")

    # ── Визуальные помощники ─────────────────────────────────────────────
    _section("Визуальные помощники")
    _radios([
        ("Отключить удалённый курсор", "hide"),
        ("Показать удалённый курсор", "show"),
        ("Автоматически показывать удалённый курсор", "auto"),
    ], "display.cursor_mode", "auto")

    # ── Режим отображения ────────────────────────────────────────────────
    _section("Режим отображения")
    _radios([
        ("Оригинальный размер", "original"),
        ("Оптимизировать отображение (сжать)", "shrink"),
        ("Оптимизировать отображение (растянуть)", "stretch"),
    ], "display.mode", "shrink")

    # ── Full Screen Mode ─────────────────────────────────────────────────
    _section("Full Screen Mode")
    fs_var = ctk.BooleanVar(value=config.get("display.fullscreen_default", False))

    def _on_fs(*_a):
        config.set("display.fullscreen_default", fs_var.get())

    fs_var.trace_add("write", _on_fs)
    ctk.CTkCheckBox(page, text="Запускать новые сеансы в полноэкранном режиме",
                    variable=fs_var, font=NORMAL_FONT, text_color=TEXT,
                    fg_color=ACCENT, hover_color=ACCENT,
                    border_color=TEXT).pack(anchor="w", padx=32, pady=2)

    # ── Аппаратное ускорение ─────────────────────────────────────────────
    _section("Аппаратное ускорение")
    _radios([
        ("Direct3D (рекомендуется)", "direct3d"),
        ("DirectDraw", "directdraw"),
        ("Выключить аппаратное ускорение", "none"),
    ], "display.hw_accel", "direct3d")

    return page
