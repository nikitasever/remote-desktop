"""
Управление историей сессий и избранным.

Хранит записи в config.json под ключом "sessions",
не затрагивая остальные ключи конфигурации.
"""

import json
import os
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import List, Optional


@dataclass
class SessionRecord:
    id: str                  # 9-значный ID удалённого ПК
    name: str = ""           # отображаемое имя (по умолчанию "ПК-XXX")
    last_connected: str = "" # ISO 8601
    os: str = "unknown"      # "Windows" / "Linux" / "unknown"
    is_favorite: bool = False
    connection_count: int = 0


APP_DIR = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "RemoteDesktop",
)
DEFAULT_CONFIG = os.path.join(APP_DIR, "config.json")


class SessionHistory:
    """Потокобезопасный менеджер истории подключений."""

    def __init__(self, config_path: Optional[str] = None):
        self._config_path = config_path or DEFAULT_CONFIG
        self._lock = threading.Lock()
        self._sessions: dict[str, SessionRecord] = {}
        self._load()

    # ---- public API ----

    def record_connection(
        self,
        id: str,
        name: Optional[str] = None,
        os: Optional[str] = None,
    ) -> SessionRecord:
        with self._lock:
            rec = self._sessions.get(id)
            if rec is None:
                rec = SessionRecord(
                    id=id,
                    name=name or f"ПК-{id[-3:]}",
                    os=os or "unknown",
                )
                self._sessions[id] = rec
            else:
                if name is not None:
                    rec.name = name
                if os is not None:
                    rec.os = os
            rec.last_connected = datetime.now(timezone.utc).isoformat()
            rec.connection_count += 1
            self._save_unlocked()
            return rec

    def get_recent(self, limit: int = 20) -> List[SessionRecord]:
        with self._lock:
            items = sorted(
                self._sessions.values(),
                key=lambda r: r.last_connected,
                reverse=True,
            )
            return items[:limit]

    def get_favorites(self) -> List[SessionRecord]:
        with self._lock:
            return sorted(
                [r for r in self._sessions.values() if r.is_favorite],
                key=lambda r: r.name,
            )

    def toggle_favorite(self, id: str) -> bool:
        with self._lock:
            rec = self._sessions.get(id)
            if rec is None:
                raise KeyError(f"Session {id} not found")
            rec.is_favorite = not rec.is_favorite
            self._save_unlocked()
            return rec.is_favorite

    def rename_session(self, id: str, new_name: str) -> None:
        with self._lock:
            rec = self._sessions.get(id)
            if rec is None:
                raise KeyError(f"Session {id} not found")
            rec.name = new_name
            self._save_unlocked()

    def remove_session(self, id: str) -> None:
        with self._lock:
            if id not in self._sessions:
                raise KeyError(f"Session {id} not found")
            del self._sessions[id]
            self._save_unlocked()

    def save(self) -> None:
        with self._lock:
            self._save_unlocked()

    # ---- internals ----

    def _load(self) -> None:
        if not os.path.isfile(self._config_path):
            return
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        for raw in data.get("sessions", []):
            try:
                rec = SessionRecord(**{
                    k: raw[k] for k in SessionRecord.__dataclass_fields__ if k in raw
                })
                self._sessions[rec.id] = rec
            except Exception:
                continue

    def _save_unlocked(self) -> None:
        """Сохраняет sessions в config.json, сохраняя остальные ключи."""
        os.makedirs(os.path.dirname(self._config_path), exist_ok=True)

        config: dict = {}
        if os.path.isfile(self._config_path):
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        config["sessions"] = [asdict(r) for r in self._sessions.values()]

        with open(self._config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
