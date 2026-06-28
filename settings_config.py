"""
Менеджер настроек приложения.

Загружает/сохраняет настройки из %APPDATA%/RemoteDesktop/settings.json.
Экспортирует синглтон `config`.
"""

import json
import os
import threading

APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "RemoteDesktop")
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")

DEFAULTS = {
    # Интерфейс
    "ui.language": "ru",
    "ui.theme": "dark",
    "ui.show_tray_icon": True,
    "ui.minimize_to_tray": False,
    "ui.start_with_windows": False,

    # Соединение
    "connection.relay_address": "",
    "connection.auto_reconnect": True,
    "connection.reconnect_delay_sec": 5,
    "connection.timeout_sec": 30,

    # Доступ
    "security.require_password": True,
    "security.allow_unattended": False,
    "security.password_hash": "",

    # Контроль доступа к управлению (роли control/view/blocked по client_id)
    # Политика для НЕИЗВЕСТНЫХ ID: ask / allow_view / allow_control / deny
    "access.default_policy": "ask",
    "access.prompt_timeout_sec": 30,

    # Права доступа
    "permissions.allow_clipboard": True,
    "permissions.allow_file_transfer": True,
    "permissions.allow_remote_input": True,
    "permissions.allow_remote_restart": False,

    # Приватность
    "privacy.show_notification_on_connect": True,
    "privacy.lock_screen_on_disconnect": False,
    "privacy.log_sessions": True,

    # Отображение
    "display.quality": 70,
    "display.fps": 20,
    "display.scale": "100%",
    "display.codec": "auto",
    "display.engine": "auto",
    "display.show_cursor": True,
    "hw_encoder": "auto",
    "hw_decoder": "auto",   # auto / hw / sw — режим аппаратного декодера на клиенте

    # Рендер-бэкенд клиента (применяется ДО инициализации pygame)
    # direct3d11 / opengl / software / none
    "render_backend": "direct3d11",
    "render_16bit": False,        # быстрый 16-битный рендер (ниже качество)
    "display.fit_mode": "fit",    # fit (letterbox) / actual (1:1) / stretch (на всё окно)
    "display.smooth_scale": True, # сглаженное масштабирование (иначе быстрое)

    # AnyDesk-подобная панель «Отображение»
    # Пресет качества (клиентское предпочтение, влияет на quality/fps хоста через HELLO):
    # quality / balance / speed
    "display.quality_preset": "balance",
    # Удалённый курсор в окне просмотра: off / on / auto (показывать ~1.5с после движения)
    "display.remote_cursor": "auto",
    # Запускать новые сеансы в полноэкранном режиме
    "display.fullscreen": False,

    # GPU-апскейл / разрешение потока (PART A/B/C)
    "display.source_scale": 100,  # % разрешения потока: 100/85/75/50 (хост ужмёт захват)
    "display.gpu_upscale": True,  # тянуть кадр на GPU клиента (pygame._sdl2 Renderer/Texture)
    "display.sharpen": 0,         # резкость 0..100 (0 = выкл; moderngl→GPU, иначе CPU unsharp)

    # Аудио
    "audio.enabled": False,
    "audio.transmit_mic": False,
    "audio.volume": 80,

    # Запись
    "recording.enabled": False,
    "recording.path": "",
    "recording.format": "mp4",
    "recording.auto_record": False,
}


class SettingsConfig:
    """Потокобезопасный менеджер настроек с автосохранением."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data: dict = {}
        self._load()

    def _load(self):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            self._data = {}

    def _save(self):
        try:
            os.makedirs(APP_DIR, exist_ok=True)
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def get(self, key: str, default=None):
        """Получить значение настройки. Если не задано, вернёт default или значение из DEFAULTS."""
        with self._lock:
            if key in self._data:
                return self._data[key]
            if default is not None:
                return default
            return DEFAULTS.get(key)

    def set(self, key: str, value):
        """Установить значение и автоматически сохранить."""
        with self._lock:
            self._data[key] = value
            self._save()

    def get_all(self) -> dict:
        """Вернуть все настройки (DEFAULTS + пользовательские)."""
        with self._lock:
            merged = dict(DEFAULTS)
            merged.update(self._data)
            return merged

    def reset(self, key: str):
        """Сбросить настройку к значению по умолчанию."""
        with self._lock:
            self._data.pop(key, None)
            self._save()

    def reset_all(self):
        """Сбросить все настройки."""
        with self._lock:
            self._data = {}
            self._save()


config = SettingsConfig()
