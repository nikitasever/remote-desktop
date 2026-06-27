"""Security/Access settings page — «Безопасность» tab for the settings UI."""

import customtkinter as ctk


def create_page(parent, config):
    """Build and return the Security settings frame."""

    BG = "#111827"
    TEXT = "white"
    ACCENT = "#3b82f6"
    HEADER_FONT = ("Segoe UI", 16, "bold")
    NORMAL_FONT = ("Segoe UI", 13)

    page = ctk.CTkFrame(parent, fg_color=BG)

    # ── helpers ──────────────────────────────────────────────────────────
    def _section(label_text):
        lbl = ctk.CTkLabel(page, text=label_text, font=HEADER_FONT,
                           text_color=TEXT, anchor="w")
        lbl.pack(fill="x", padx=16, pady=(18, 6))

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

    # ── Глобальные разрешения ────────────────────────────────────────────
    _section("Глобальные разрешения")
    ctk.CTkLabel(page, text="Другим пользователям разрешено...",
                 font=NORMAL_FONT, text_color="#9ca3af",
                 anchor="w").pack(fill="x", padx=32, pady=(0, 4))

    _checkbox("Управлять моими клавиатурой и мышью",
              "security.allow_input", True)
    _checkbox("Синхронизировать текстовый буфер обмена",
              "security.sync_clipboard", True)
    _checkbox("Синхронизировать файловый буфер обмена",
              "security.sync_file_clipboard", True)
    _checkbox("Прослушивать звук моего устройства",
              "security.allow_audio", True)
    _checkbox("Перезагружать мой компьютер",
              "security.allow_reboot", False)

    # ── Пароль для неконтролируемого доступа ─────────────────────────────
    _section("Пароль для неконтролируемого доступа")

    pwd_frame = ctk.CTkFrame(page, fg_color=BG)
    pwd_frame.pack(fill="x", padx=32, pady=(4, 2))

    pwd_var = ctk.StringVar(value=config.get("security.unattended_password", ""))
    pwd_entry = ctk.CTkEntry(pwd_frame, textvariable=pwd_var, show="*",
                             font=NORMAL_FONT, width=260,
                             fg_color="#1f2937", text_color=TEXT,
                             border_color=ACCENT)
    pwd_entry.pack(side="left", padx=(0, 8))

    def _toggle_show():
        if pwd_entry.cget("show") == "*":
            pwd_entry.configure(show="")
            toggle_btn.configure(text="Скрыть")
        else:
            pwd_entry.configure(show="*")
            toggle_btn.configure(text="Показать")

    toggle_btn = ctk.CTkButton(pwd_frame, text="Показать", width=80,
                               font=NORMAL_FONT, fg_color=ACCENT,
                               hover_color="#2563eb", command=_toggle_show)
    toggle_btn.pack(side="left")

    def _on_pwd_change(*_a):
        config.set("security.unattended_password", pwd_var.get())

    pwd_var.trace_add("write", _on_pwd_change)

    # ── Двухфакторная аутентификация ─────────────────────────────────────
    _section("Двухфакторная аутентификация")
    _checkbox("Требовать подтверждение при подключении",
              "security.require_confirm", True)

    # ── Приватность ──────────────────────────────────────────────────────
    _section("Приватность")
    _checkbox("Включать режим приватности при подключении",
              "security.privacy_mode", False)
    ctk.CTkLabel(page, text="Экран удалённого ПК будет выключен при подключении",
                 font=("Segoe UI", 12), text_color="#6b7280",
                 anchor="w").pack(fill="x", padx=48, pady=(0, 4))

    return page
