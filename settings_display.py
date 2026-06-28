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

    # ── Режим вписывания / масштабирование ───────────────────────────────
    _section("Масштабирование окна")
    _radios([
        ("Вписать в окно (с полями)", "fit"),
        ("1:1 / реальный размер", "actual"),
    ], "display.fit_mode", "fit")

    smooth_var = ctk.BooleanVar(value=config.get("display.smooth_scale", True))

    def _on_smooth(*_a):
        config.set("display.smooth_scale", smooth_var.get())

    smooth_var.trace_add("write", _on_smooth)
    ctk.CTkCheckBox(page, text="Сглаженное масштабирование (плавнее, но медленнее)",
                    variable=smooth_var, font=NORMAL_FONT, text_color=TEXT,
                    fg_color=ACCENT, hover_color=ACCENT,
                    border_color=TEXT).pack(anchor="w", padx=32, pady=2)

    # ── Разрешение потока (source scale) ─────────────────────────────────
    _section("Разрешение потока")
    ctk.CTkLabel(
        page,
        text="Меньше = меньше нагрузка на хост и трафик; GPU клиента растянет кадр.",
        font=("Segoe UI", 11), text_color="#94a3b8",
        anchor="w", justify="left").pack(fill="x", padx=32, pady=(0, 4))

    _src_options = ["100%", "85%", "75%", "50%"]
    _src_map = {"100%": 100, "85%": 85, "75%": 75, "50%": 50}
    _src_rev = {v: k for k, v in _src_map.items()}
    src_var = ctk.StringVar(
        value=_src_rev.get(int(config.get("display.source_scale", 100)), "100%"))

    def _on_src(*_a):
        config.set("display.source_scale", _src_map.get(src_var.get(), 100))

    src_var.trace_add("write", _on_src)
    src_row = ctk.CTkFrame(page, fg_color="transparent")
    src_row.pack(fill="x", padx=32, pady=(2, 2))
    ctk.CTkLabel(src_row, text="Разрешение:", font=NORMAL_FONT,
                 text_color=TEXT).pack(side="left")
    ctk.CTkOptionMenu(src_row, variable=src_var, values=_src_options,
                      width=140, font=NORMAL_FONT,
                      fg_color="#1e293b", button_color=ACCENT,
                      button_hover_color="#2563eb",
                      dropdown_fg_color="#1e293b",
                      dropdown_hover_color=ACCENT,
                      text_color=TEXT).pack(side="left", padx=(8, 0))

    # ── GPU-апскейл клиента ──────────────────────────────────────────────
    _section("GPU-апскейл (растяжение на видеокарте)")
    gpu_var = ctk.BooleanVar(value=config.get("display.gpu_upscale", True))

    def _on_gpu(*_a):
        config.set("display.gpu_upscale", gpu_var.get())

    gpu_var.trace_add("write", _on_gpu)
    ctk.CTkCheckBox(
        page,
        text="Растягивать кадр на GPU клиента (резче, чем низкобитрейтный поток)",
        variable=gpu_var, font=NORMAL_FONT, text_color=TEXT,
        fg_color=ACCENT, hover_color=ACCENT,
        border_color=TEXT).pack(anchor="w", padx=32, pady=2)
    ctk.CTkLabel(
        page,
        text="Если GPU-рендер недоступен — авто-откат на CPU (без сбоя).",
        font=("Segoe UI", 11), text_color="#94a3b8",
        anchor="w").pack(fill="x", padx=32, pady=(0, 2))

    # ── Резкость (sharpening) ────────────────────────────────────────────
    _section("Резкость (sharpening)")
    _shp_options = ["Выкл", "Слабо (25)", "Средне (50)", "Сильно (75)", "Макс (100)"]
    _shp_map = {"Выкл": 0, "Слабо (25)": 25, "Средне (50)": 50,
                "Сильно (75)": 75, "Макс (100)": 100}

    def _shp_label(val):
        val = int(val)
        best = min(_shp_map.items(), key=lambda kv: abs(kv[1] - val))
        return best[0]

    shp_var = ctk.StringVar(value=_shp_label(config.get("display.sharpen", 0)))

    def _on_shp(*_a):
        config.set("display.sharpen", _shp_map.get(shp_var.get(), 0))

    shp_var.trace_add("write", _on_shp)
    shp_row = ctk.CTkFrame(page, fg_color="transparent")
    shp_row.pack(fill="x", padx=32, pady=(2, 2))
    ctk.CTkLabel(shp_row, text="Резкость:", font=NORMAL_FONT,
                 text_color=TEXT).pack(side="left")
    ctk.CTkOptionMenu(shp_row, variable=shp_var, values=_shp_options,
                      width=160, font=NORMAL_FONT,
                      fg_color="#1e293b", button_color=ACCENT,
                      button_hover_color="#2563eb",
                      dropdown_fg_color="#1e293b",
                      dropdown_hover_color=ACCENT,
                      text_color=TEXT).pack(side="left", padx=(8, 0))
    ctk.CTkLabel(
        page,
        text="0 = без накладных расходов. >0: GPU-шейдер (moderngl) или CPU unsharp-mask (нагружает CPU).",
        font=("Segoe UI", 11), text_color="#94a3b8",
        anchor="w", justify="left").pack(fill="x", padx=32, pady=(0, 2))

    # ── Аппаратное ускорение (рендер) ────────────────────────────────────
    _section("Аппаратное ускорение")
    _radios([
        ("Direct3D (рекомендуется)", "direct3d11"),
        ("OpenGL (экспериментальный)", "opengl"),
        ("DirectDraw", "software"),
        ("Выключить аппаратное ускорение", "none"),
    ], "render_backend", "direct3d11")

    bit16_var = ctk.BooleanVar(value=config.get("render_16bit", False))

    def _on_16bit(*_a):
        config.set("render_16bit", bit16_var.get())

    bit16_var.trace_add("write", _on_16bit)
    ctk.CTkCheckBox(
        page,
        text="Использовать быстрый 16-битный рендер (снижает качество изображения)",
        variable=bit16_var, font=NORMAL_FONT, text_color=TEXT,
        fg_color=ACCENT, hover_color=ACCENT,
        border_color=TEXT).pack(anchor="w", padx=32, pady=(2, 4))

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

    # ── Видео-декодер (H.264) ───────────────────────────────────────────
    _section("Видео-декодер (H.264)")

    dec_options = [
        "Авто (HW → софт)",
        "Только аппаратный (HW)",
        "Только программный (CPU)",
    ]
    _dec_map = {
        "Авто (HW → софт)": "auto",
        "Только аппаратный (HW)": "hw",
        "Только программный (CPU)": "sw",
    }
    _dec_rev = {v: k for k, v in _dec_map.items()}

    dec_var = ctk.StringVar(value=_dec_rev.get(config.get("hw_decoder", "auto"),
                                               "Авто (HW → софт)"))

    def _on_dec_change(*_a):
        config.set("hw_decoder", _dec_map.get(dec_var.get(), "auto"))

    dec_var.trace_add("write", _on_dec_change)

    dec_row = ctk.CTkFrame(page, fg_color="transparent")
    dec_row.pack(fill="x", padx=32, pady=(4, 2))
    ctk.CTkLabel(dec_row, text="Декодер:", font=NORMAL_FONT,
                 text_color=TEXT).pack(side="left")
    ctk.CTkOptionMenu(dec_row, variable=dec_var, values=dec_options,
                      width=200, font=NORMAL_FONT,
                      fg_color="#1e293b", button_color=ACCENT,
                      button_hover_color="#2563eb",
                      dropdown_fg_color="#1e293b",
                      dropdown_hover_color=ACCENT,
                      text_color=TEXT).pack(side="left", padx=(8, 0))

    # ── Активный кодек (фактический) ────────────────────────────────────
    _section("Активный кодек")
    status_label = ctk.CTkLabel(page, text="(нажмите «Определить»)",
                                font=("Consolas", 11),
                                text_color="#94a3b8", anchor="w",
                                justify="left")
    status_label.pack(fill="x", padx=32, pady=(2, 0))

    def _detect():
        status_label.configure(text="Определение…")
        import threading

        def _run():
            try:
                import video
                enc_pref = _encoder_map.get(enc_var.get(), "auto")
                dec_pref = _dec_map.get(dec_var.get(), "auto")
                e = video.VideoEncoder(320, 240, fps=15, prefer=enc_pref)
                e_name, e_hw = e.active_encoder_name, e.is_hardware
                e.close()
                d = video.VideoDecoder(prefer=dec_pref)
                d_name, d_hw = d.active_decoder_name, d.is_hardware
                d.close()
                text = (
                    f"Кодек:   {e_name} "
                    f"({'HW — аппаратный' if e_hw else 'CPU — программный'})\n"
                    f"Декодер: {d_name} "
                    f"({'HW — аппаратный' if d_hw else 'CPU — программный'})"
                )
            except Exception as ex:
                text = f"Ошибка: {ex}"
            page.after(0, lambda: status_label.configure(text=text))

        threading.Thread(target=_run, daemon=True).start()

    ctk.CTkButton(page, text="Определить активный кодек",
                  width=220, height=30, corner_radius=6,
                  font=NORMAL_FONT, fg_color="#1e293b",
                  hover_color=ACCENT, text_color=TEXT,
                  command=_detect).pack(anchor="w", padx=32, pady=(6, 8))

    return page
