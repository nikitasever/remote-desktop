"""
Окно настроек в стиле AnyDesk.

Левая панель — навигация по разделам, правая — содержимое раздела.
Каждый раздел реализован в отдельном модуле settings_*.py
с функцией create_page(parent, config) -> CTkFrame.
"""

import customtkinter as ctk

from settings_config import config as _cfg
import settings_interface
import settings_connection
import settings_security
import settings_display
import settings_audio
import settings_autostart

# -- Color palette (из app.py) --
BG_DARK = "#111827"
BG_CARD = "#1e293b"
BG_INPUT = "#0f172a"
ACCENT = "#3b82f6"
ACCENT_HOVER = "#2563eb"
TEXT_PRIMARY = "#f1f5f9"
TEXT_SECONDARY = "#94a3b8"
TEXT_HINT = "#64748b"
BORDER = "#334155"
RED = "#ef4444"

# Красный индикатор как в AnyDesk
INDICATOR = "#ef4444"

# Ширина боковой панели
SIDEBAR_WIDTH = 220


# ============================================================
#  Placeholder for sections that don't have a dedicated module
# ============================================================

def _placeholder(parent, title, description):
    """Заглушка для раздела настроек."""
    frame = ctk.CTkFrame(parent, fg_color="transparent")

    ctk.CTkLabel(
        frame, text=title,
        font=ctk.CTkFont(size=22, weight="bold"),
        text_color=TEXT_PRIMARY,
    ).pack(anchor="w", padx=4, pady=(0, 8))

    ctk.CTkLabel(
        frame, text=description,
        font=ctk.CTkFont(size=13),
        text_color=TEXT_SECONDARY,
    ).pack(anchor="w", padx=4, pady=(0, 24))

    placeholder_card = ctk.CTkFrame(frame, fg_color=BG_CARD, corner_radius=12,
                                     border_width=1, border_color=BORDER)
    placeholder_card.pack(fill="x", padx=4, pady=4)

    ctk.CTkLabel(
        placeholder_card,
        text="Настройки этого раздела будут добавлены позже",
        font=ctk.CTkFont(size=12),
        text_color=TEXT_HINT,
    ).pack(padx=24, pady=40)

    return frame


# ============================================================
#  Page-creator wrappers (adapt module.create_page -> sidebar callback)
# ============================================================

def _make_page(module):
    """Wrap a settings_*.create_page(parent, config) into a sidebar callback."""
    def creator(parent):
        return module.create_page(parent, _cfg)
    return creator


def _create_privacy_page(parent):
    return _placeholder(parent, "Приватность", "Конфиденциальность и уведомления")


def _create_recording_page(parent):
    """Запись — часть аудио-модуля, но отдельный раздел в навигации."""
    # Аудио-модуль содержит секцию записи; показываем её отдельной страницей.
    # Если в settings_audio есть create_recording_page — используем, иначе заглушка.
    fn = getattr(settings_audio, "create_recording_page", None)
    if fn:
        return fn(parent, _cfg)
    return _placeholder(parent, "Запись", "Запись сессий в видеофайл")


# ============================================================
#  Sidebar sections definition
# ============================================================

SECTIONS = [
    # (header_or_none, key, label, page_creator)
    ("Приложение",   "interface",   "Интерфейс",     _make_page(settings_interface)),
    (None,           "connection",  "Соединение",     _make_page(settings_connection)),
    (None,           "autostart",   "Автозапуск",     _make_page(settings_autostart)),
    ("Безопасность", "access",      "Доступ",         _make_page(settings_security)),
    (None,           "privacy",     "Приватность",    _create_privacy_page),
    ("Сессия",       "display",     "Отображение",    _make_page(settings_display)),
    (None,           "audio",       "Аудио",          _make_page(settings_audio)),
    (None,           "recording",   "Запись",         _create_recording_page),
]


# ============================================================
#  Settings Window
# ============================================================

class SettingsWindow(ctk.CTkToplevel):
    """Окно настроек — CTkToplevel с боковой навигацией."""

    def __init__(self, master=None):
        super().__init__(master)
        self.title("Настройки")
        self.geometry("820x560")
        self.minsize(700, 450)
        self.configure(fg_color=BG_DARK)
        self.transient(master)
        self.grab_set()

        self._current_key = None
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._pages: dict[str, ctk.CTkFrame] = {}

        self._build_layout()
        # Выбираем первый раздел
        self._select("interface")

    # ---- Layout ----

    def _build_layout(self):
        # Основной контейнер
        container = ctk.CTkFrame(self, fg_color=BG_DARK, corner_radius=0)
        container.pack(fill="both", expand=True)

        # Боковая панель
        self._sidebar = ctk.CTkFrame(container, fg_color=BG_CARD, width=SIDEBAR_WIDTH,
                                      corner_radius=0)
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)

        # Заголовок боковой панели
        header = ctk.CTkFrame(self._sidebar, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(20, 16))
        ctk.CTkLabel(header, text="Настройки",
                     font=ctk.CTkFont(size=18, weight="bold"),
                     text_color=TEXT_PRIMARY).pack(anchor="w")

        # Разделитель
        ctk.CTkFrame(self._sidebar, fg_color=BORDER, height=1).pack(fill="x", padx=12, pady=(0, 8))

        # Навигация
        self._build_sidebar_nav()

        # Правая панель — контент
        self._content_area = ctk.CTkFrame(container, fg_color=BG_DARK, corner_radius=0)
        self._content_area.pack(side="left", fill="both", expand=True)

        # Внутренний фрейм с отступами
        self._content_inner = ctk.CTkFrame(self._content_area, fg_color="transparent")
        self._content_inner.pack(fill="both", expand=True, padx=28, pady=24)

    def _build_sidebar_nav(self):
        """Строит навигацию в боковой панели по SECTIONS."""
        for header, key, label, creator in SECTIONS:
            if header:
                # Заголовок группы
                ctk.CTkLabel(
                    self._sidebar, text=header.upper(),
                    font=ctk.CTkFont(size=11, weight="bold"),
                    text_color=TEXT_HINT,
                ).pack(anchor="w", padx=20, pady=(12, 4))

            # Фрейм-обёртка для индикатора + кнопки
            row = ctk.CTkFrame(self._sidebar, fg_color="transparent", height=36)
            row.pack(fill="x", padx=8, pady=1)
            row.pack_propagate(False)

            # Индикатор (красная полоска слева, как AnyDesk)
            indicator = ctk.CTkFrame(row, fg_color="transparent", width=3,
                                      corner_radius=2)
            indicator.pack(side="left", fill="y", padx=(4, 0), pady=4)

            btn = ctk.CTkButton(
                row,
                text=label,
                anchor="w",
                height=28,
                corner_radius=6,
                font=ctk.CTkFont(size=13),
                fg_color="transparent",
                hover_color=BG_INPUT,
                text_color=TEXT_SECONDARY,
                text_color_disabled=TEXT_PRIMARY,
                command=lambda k=key: self._select(k),
            )
            btn.pack(side="left", fill="both", expand=True, padx=(2, 8))

            self._nav_buttons[key] = (btn, indicator)

    # ---- Navigation ----

    def _select(self, key: str):
        """Переключить активный раздел."""
        if key == self._current_key:
            return

        # Снять выделение с текущего
        if self._current_key and self._current_key in self._nav_buttons:
            btn, ind = self._nav_buttons[self._current_key]
            btn.configure(fg_color="transparent", text_color=TEXT_SECONDARY)
            ind.configure(fg_color="transparent")

        # Выделить новый
        btn, ind = self._nav_buttons[key]
        btn.configure(fg_color=BG_INPUT, text_color=TEXT_PRIMARY)
        ind.configure(fg_color=INDICATOR)

        self._current_key = key

        # Скрыть все страницы, показать нужную
        for page in self._pages.values():
            page.pack_forget()

        if key not in self._pages:
            # Найти creator
            for _, k, _, creator in SECTIONS:
                if k == key:
                    page = creator(self._content_inner)
                    self._pages[key] = page
                    break

        self._pages[key].pack(fill="both", expand=True)


def open_settings(master=None):
    """Открыть окно настроек. Возвращает ссылку на окно."""
    return SettingsWindow(master)
