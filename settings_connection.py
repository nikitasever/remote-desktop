import customtkinter as ctk


def create_page(parent, config):
    BG = "#111827"
    TEXT = "white"
    ACCENT = "#3b82f6"

    frame = ctk.CTkFrame(parent, fg_color=BG)

    def section_header(master, text, row):
        lbl = ctk.CTkLabel(master, text=text, font=("", 16, "bold"),
                           text_color=TEXT, anchor="w")
        lbl.grid(row=row, column=0, columnspan=3, sticky="w", pady=(18, 6), padx=10)
        return row + 1

    # ── Прокси ──────────────────────────────────────────────
    row = section_header(frame, "Прокси", 0)

    proxy_mode = ctk.StringVar(value=config.get("connection.proxy_mode", "none"))

    proxy_host_var = ctk.StringVar(value=config.get("connection.proxy_host", ""))
    proxy_port_var = ctk.StringVar(value=config.get("connection.proxy_port", ""))

    host_entry = None
    port_entry = None
    host_label = None
    port_label = None

    def _toggle_custom(*_):
        show = proxy_mode.get() == "custom"
        state = "normal" if show else "disabled"
        for w in (host_entry, port_entry):
            if w:
                w.configure(state=state)
        config.set("connection.proxy_mode", proxy_mode.get())

    for val, label in [("none", "Не использовать прокси"),
                       ("system", "Системные настройки прокси"),
                       ("custom", "Пользовательский прокси")]:
        rb = ctk.CTkRadioButton(frame, text=label, variable=proxy_mode, value=val,
                                fg_color=ACCENT, text_color=TEXT,
                                command=_toggle_custom)
        rb.grid(row=row, column=0, columnspan=3, sticky="w", padx=20, pady=2)
        row += 1

    host_label = ctk.CTkLabel(frame, text="Хост:", text_color=TEXT)
    host_label.grid(row=row, column=0, sticky="w", padx=30, pady=2)
    host_entry = ctk.CTkEntry(frame, textvariable=proxy_host_var, width=220)
    host_entry.grid(row=row, column=1, sticky="w", padx=5, pady=2)
    row += 1

    port_label = ctk.CTkLabel(frame, text="Порт:", text_color=TEXT)
    port_label.grid(row=row, column=0, sticky="w", padx=30, pady=2)
    port_entry = ctk.CTkEntry(frame, textvariable=proxy_port_var, width=120)
    port_entry.grid(row=row, column=1, sticky="w", padx=5, pady=2)
    row += 1

    proxy_host_var.trace_add("write", lambda *_: config.set("connection.proxy_host", proxy_host_var.get()))
    proxy_port_var.trace_add("write", lambda *_: config.set("connection.proxy_port", proxy_port_var.get()))

    _toggle_custom()

    # ── Relay-сервер ────────────────────────────────────────
    row = section_header(frame, "Relay-сервер", row)

    relay_var = ctk.StringVar(value=config.get("connection.relay_address", "77.110.98.222:5810"))
    ctk.CTkLabel(frame, text="Адрес relay:", text_color=TEXT).grid(
        row=row, column=0, sticky="w", padx=20, pady=4)
    ctk.CTkEntry(frame, textvariable=relay_var, width=220).grid(
        row=row, column=1, sticky="w", padx=5, pady=4)
    relay_var.trace_add("write", lambda *_: config.set("connection.relay_address", relay_var.get()))
    row += 1

    # ── Порт прямого подключения ────────────────────────────
    row = section_header(frame, "Порт прямого подключения", row)

    direct_port_var = ctk.StringVar(value=config.get("connection.direct_port", "5900"))
    ctk.CTkLabel(frame, text="Порт:", text_color=TEXT).grid(
        row=row, column=0, sticky="w", padx=20, pady=4)
    ctk.CTkEntry(frame, textvariable=direct_port_var, width=120).grid(
        row=row, column=1, sticky="w", padx=5, pady=4)
    direct_port_var.trace_add("write", lambda *_: config.set("connection.direct_port", direct_port_var.get()))
    row += 1

    # ── Таймауты ────────────────────────────────────────────
    row = section_header(frame, "Таймауты", row)

    timeout_val = ctk.IntVar(value=int(config.get("connection.timeout", 10)))
    timeout_label = ctk.CTkLabel(frame, text=f"Таймаут подключения: {timeout_val.get()} сек",
                                 text_color=TEXT)
    timeout_label.grid(row=row, column=0, columnspan=2, sticky="w", padx=20, pady=4)
    row += 1

    def _on_timeout(value):
        v = int(value)
        timeout_val.set(v)
        timeout_label.configure(text=f"Таймаут подключения: {v} сек")
        config.set("connection.timeout", v)

    slider = ctk.CTkSlider(frame, from_=5, to=60, number_of_steps=55,
                           variable=timeout_val, command=_on_timeout,
                           button_color=ACCENT, button_hover_color=ACCENT,
                           width=300)
    slider.grid(row=row, column=0, columnspan=2, sticky="w", padx=20, pady=4)
    row += 1

    # ── Сеть ────────────────────────────────────────────────
    row = section_header(frame, "Сеть", row)

    tcp_nodelay_var = ctk.BooleanVar(value=config.get("connection.tcp_nodelay", True))
    auto_reconnect_var = ctk.BooleanVar(value=config.get("connection.auto_reconnect", True))

    ctk.CTkCheckBox(frame, text="Включить TCP_NODELAY", variable=tcp_nodelay_var,
                    fg_color=ACCENT, text_color=TEXT,
                    command=lambda: config.set("connection.tcp_nodelay", tcp_nodelay_var.get())
                    ).grid(row=row, column=0, columnspan=2, sticky="w", padx=20, pady=4)
    row += 1

    ctk.CTkCheckBox(frame, text="Автопереподключение при разрыве", variable=auto_reconnect_var,
                    fg_color=ACCENT, text_color=TEXT,
                    command=lambda: config.set("connection.auto_reconnect", auto_reconnect_var.get())
                    ).grid(row=row, column=0, columnspan=2, sticky="w", padx=20, pady=4)

    return frame
