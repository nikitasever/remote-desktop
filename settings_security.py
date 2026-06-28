"""Security/Access settings page — «Безопасность» tab for the settings UI."""

import customtkinter as ctk

import access_control


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

    # Security advisory: weak passwords are the only barrier to full control.
    ctk.CTkLabel(
        page,
        text=("Используйте пароль не короче "
              + str(config.get("security.min_password_length", 12))
              + " символов: подключившийся получает полный контроль над ПК."),
        font=("Segoe UI", 12), text_color="#eab308",
        anchor="w", wraplength=420, justify="left",
    ).pack(fill="x", padx=32, pady=(2, 4))

    # ── Контроль доступа к управлению (роли по ID) ───────────────────────
    _section("Права доступа к управлению")

    ctk.CTkLabel(page, text="Политика для НЕИЗВЕСТНЫХ ID при подключении:",
                 font=NORMAL_FONT, text_color="#9ca3af",
                 anchor="w").pack(fill="x", padx=32, pady=(0, 4))

    _POLICY_LABELS = {
        "ask": "Спрашивать каждый раз",
        "allow_view": "Разрешать только просмотр",
        "allow_control": "Разрешать полный контроль",
        "deny": "Отклонять подключение",
    }
    _LABEL_TO_POLICY = {v: k for k, v in _POLICY_LABELS.items()}

    cur_policy = access_control.normalize_policy(
        config.get("access.default_policy", "ask"))
    policy_var = ctk.StringVar(value=_POLICY_LABELS[cur_policy])

    def _on_policy(choice):
        config.set("access.default_policy", _LABEL_TO_POLICY.get(choice, "ask"))

    ctk.CTkOptionMenu(
        page, variable=policy_var,
        values=list(_POLICY_LABELS.values()),
        command=_on_policy, font=NORMAL_FONT,
        fg_color="#1f2937", button_color=ACCENT, button_hover_color="#2563eb",
        width=300,
    ).pack(anchor="w", padx=32, pady=(0, 8))

    # ── Менеджер известных ID ────────────────────────────────────────────
    ctk.CTkLabel(page, text="Известные ID и их роли:",
                 font=NORMAL_FONT, text_color="#9ca3af",
                 anchor="w").pack(fill="x", padx=32, pady=(6, 4))

    roles_box = ctk.CTkScrollableFrame(page, fg_color="#0f172a", height=160)
    roles_box.pack(fill="x", padx=32, pady=(0, 6))

    _ROLE_LABELS = {"control": "Контроль", "view": "Просмотр", "blocked": "Заблокирован"}

    def _refresh_roles():
        for w in roles_box.winfo_children():
            w.destroy()
        table = access_control.load_roles()
        if not table:
            ctk.CTkLabel(roles_box, text="Список пуст — ID добавляются при подключении.",
                         font=("Segoe UI", 12), text_color="#6b7280",
                         anchor="w").pack(fill="x", padx=8, pady=8)
            return
        for cid, role in sorted(table.items()):
            row = ctk.CTkFrame(roles_box, fg_color="transparent")
            row.pack(fill="x", padx=4, pady=2)
            ctk.CTkLabel(row, text=cid, font=("Consolas", 12), text_color=TEXT,
                         width=110, anchor="w").pack(side="left", padx=(4, 8))

            rvar = ctk.StringVar(value=_ROLE_LABELS.get(role, role))

            def _make_setter(client_id):
                def _set(choice):
                    inv = {v: k for k, v in _ROLE_LABELS.items()}
                    access_control.set_role(client_id, inv.get(choice, "view"))
                return _set

            ctk.CTkOptionMenu(
                row, variable=rvar, values=list(_ROLE_LABELS.values()),
                command=_make_setter(cid), font=("Segoe UI", 12), width=140,
                fg_color="#1f2937", button_color=ACCENT, button_hover_color="#2563eb",
            ).pack(side="left", padx=(0, 8))

            def _make_remover(client_id):
                def _rm():
                    access_control.remove_role(client_id)
                    _refresh_roles()
                return _rm

            ctk.CTkButton(row, text="Удалить", width=70, font=("Segoe UI", 12),
                          fg_color="#374151", hover_color="#4b5563",
                          command=_make_remover(cid)).pack(side="right", padx=4)

    ctk.CTkButton(page, text="Обновить список", width=140, font=NORMAL_FONT,
                  fg_color="#374151", hover_color="#4b5563",
                  command=_refresh_roles).pack(anchor="w", padx=32, pady=(0, 8))
    _refresh_roles()

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
