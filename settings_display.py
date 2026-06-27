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

    # ── Видео-энкодер (H.264) ───────────────────────────────────────────
    _section("Видео-энкодер (H.264)")

    encoder_options = [
        "Авто",
        "h264_nvenc (NVIDIA)",
        "h264_amf (AMD)",
        "h264_qsv (Intel)",
        "h264_vaapi (Linux)",
        "libx264 (софт)",
    ]
    _encoder_map = {
        "Авто": "auto",
        "h264_nvenc (NVIDIA)": "h264_nvenc",
        "h264_amf (AMD)": "h264_amf",
        "h264_qsv (Intel)": "h264_qsv",
        "h264_vaapi (Linux)": "h264_vaapi",
        "libx264 (софт)": "libx264",
    }
    _encoder_rev = {v: k for k, v in _encoder_map.items()}

    current_enc = config.get("hw_encoder", "auto")
    enc_var = ctk.StringVar(value=_encoder_rev.get(current_enc, "Авто"))

    def _on_enc_change(*_a):
        config.set("hw_encoder", _encoder_map.get(enc_var.get(), "auto"))

    enc_var.trace_add("write", _on_enc_change)

    enc_row = ctk.CTkFrame(page, fg_color="transparent")
    enc_row.pack(fill="x", padx=32, pady=(4, 2))

    ctk.CTkLabel(enc_row, text="Энкодер:", font=NORMAL_FONT,
                 text_color=TEXT).pack(side="left")

    ctk.CTkOptionMenu(enc_row, variable=enc_var, values=encoder_options,
                      width=200, font=NORMAL_FONT,
                      fg_color="#1e293b", button_color=ACCENT,
                      button_hover_color="#2563eb",
                      dropdown_fg_color="#1e293b",
                      dropdown_hover_color=ACCENT,
                      text_color=TEXT).pack(side="left", padx=(8, 0))

    # Probe button — показывает доступные энкодеры
    probe_label = ctk.CTkLabel(page, text="", font=("Consolas", 11),
                               text_color="#94a3b8", anchor="w")
    probe_label.pack(fill="x", padx=32, pady=(2, 0))

    def _probe():
        probe_label.configure(text="Проверка…")
        import threading

        def _run():
            try:
                import video
                results = video.get_available_encoders(force_recheck=True)
                if results:
                    lines = [f"  {n}: {t} ms" for n, t in results]
                    text = "Доступные энкодеры:\n" + "\n".join(lines)
                else:
                    text = "Ни один энкодер не прошёл проверку"
            except Exception as e:
                text = f"Ошибка: {e}"
            page.after(0, lambda: probe_label.configure(text=text))

        threading.Thread(target=_run, daemon=True).start()

    ctk.CTkButton(page, text="Проверить доступные энкодеры",
                  width=220, height=30, corner_radius=6,
                  font=NORMAL_FONT, fg_color="#1e293b",
                  hover_color=ACCENT, text_color=TEXT,
                  command=_probe).pack(anchor="w", padx=32, pady=(6, 4))

    return page
