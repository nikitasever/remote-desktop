import customtkinter as ctk
from tkinter import filedialog


BG_COLOR = "#111827"
TEXT_COLOR = "white"
ACCENT_COLOR = "#3b82f6"


def _section_header(parent, text):
    label = ctk.CTkLabel(
        parent, text=text, font=("", 16, "bold"),
        text_color=TEXT_COLOR, anchor="w"
    )
    label.pack(fill="x", padx=20, pady=(18, 6))
    return label


def _make_checkbox(parent, text, config, key):
    var = ctk.BooleanVar(value=config.get(key, False))

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

    # --- Язык ---
    _section_header(frame, "Язык")
    lang_values = ["Автовыбор", "Русский", "English"]
    lang_var = ctk.StringVar(value=config.get("interface.language", "Автовыбор"))

    def on_lang(choice):
        config.set("interface.language", choice)

    lang_combo = ctk.CTkComboBox(
        frame, values=lang_values, variable=lang_var,
        command=on_lang, width=250,
        fg_color=BG_COLOR, text_color=TEXT_COLOR,
        button_color=ACCENT_COLOR, border_color=ACCENT_COLOR
    )
    lang_combo.pack(padx=40, pady=(4, 10), anchor="w")

    # --- Тема ---
    _section_header(frame, "Тема")
    theme_values = ["тема по умолчанию", "Тёмная", "Светлая"]
    theme_var = ctk.StringVar(value=config.get("interface.theme", "тема по умолчанию"))

    def on_theme(choice):
        config.set("interface.theme", choice)

    theme_combo = ctk.CTkComboBox(
        frame, values=theme_values, variable=theme_var,
        command=on_theme, width=250,
        fg_color=BG_COLOR, text_color=TEXT_COLOR,
        button_color=ACCENT_COLOR, border_color=ACCENT_COLOR
    )
    theme_combo.pack(padx=40, pady=(4, 10), anchor="w")

    # --- Разное ---
    _section_header(frame, "Разное")
    checkboxes = [
        ("Скрывать панель задач в полноэкранном режиме", "interface.hide_taskbar"),
        ("Запрашивать комментарий при закрытии сеанса", "interface.ask_comment"),
        ("Звук при входящем запросе на подключение", "interface.connection_sound"),
        ("Передавать сочетания клавиш", "interface.forward_shortcuts"),
        ("Показывать уведомления о входящих приглашениях на сессию", "interface.show_notifications"),
    ]
    for text, key in checkboxes:
        _make_checkbox(frame, text, config, key)

    # --- Каталог скриншотов ---
    _section_header(frame, "Каталог скриншотов")

    screenshot_auto = config.get("interface.screenshot_auto", True)
    radio_var = ctk.IntVar(value=0 if screenshot_auto else 1)
    dir_var = ctk.StringVar(value=config.get("interface.screenshot_dir", ""))

    dir_entry = None
    browse_btn = None

    def update_dir_widgets():
        custom = radio_var.get() == 1
        if dir_entry:
            dir_entry.configure(state="normal" if custom else "disabled")
        if browse_btn:
            browse_btn.configure(state="normal" if custom else "disabled")

    def on_radio():
        is_auto = radio_var.get() == 0
        config.set("interface.screenshot_auto", is_auto)
        update_dir_widgets()

    radio_auto = ctk.CTkRadioButton(
        frame, text="Автоматически", variable=radio_var, value=0,
        text_color=TEXT_COLOR, fg_color=ACCENT_COLOR,
        hover_color=ACCENT_COLOR, command=on_radio
    )
    radio_auto.pack(padx=40, pady=3, anchor="w")

    radio_custom = ctk.CTkRadioButton(
        frame, text="Пользовательский", variable=radio_var, value=1,
        text_color=TEXT_COLOR, fg_color=ACCENT_COLOR,
        hover_color=ACCENT_COLOR, command=on_radio
    )
    radio_custom.pack(padx=40, pady=3, anchor="w")

    dir_frame = ctk.CTkFrame(frame, fg_color=BG_COLOR)
    dir_frame.pack(fill="x", padx=40, pady=(4, 10))

    def on_dir_change(*_args):
        config.set("interface.screenshot_dir", dir_var.get())

    dir_var.trace_add("write", on_dir_change)

    dir_entry = ctk.CTkEntry(
        dir_frame, textvariable=dir_var, width=350,
        fg_color=BG_COLOR, text_color=TEXT_COLOR,
        border_color=ACCENT_COLOR
    )
    dir_entry.pack(side="left", padx=(0, 8))

    def browse():
        path = filedialog.askdirectory()
        if path:
            dir_var.set(path)

    browse_btn = ctk.CTkButton(
        dir_frame, text="Выбрать...", command=browse, width=100,
        fg_color=ACCENT_COLOR, hover_color="#2563eb", text_color=TEXT_COLOR
    )
    browse_btn.pack(side="left")

    update_dir_widgets()

    return frame
