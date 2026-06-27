"""
LAN Discovery — обнаружение других экземпляров RemoteDesktop в локальной сети.

Каждый запущенный экземпляр рассылает UDP-beacon каждые 5 секунд.
Слушатель собирает beacon'ы от других машин и поддерживает список
обнаруженных устройств (аналог вкладки «Обнаруженные» в AnyDesk).

Зависимости: только стандартная библиотека Python.
"""

import json
import logging
import platform
import socket
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

BEACON_INTERVAL = 5        # секунд между отправками beacon
ONLINE_TIMEOUT = 15        # секунд — устройство считается онлайн
STALE_TIMEOUT = 60         # секунд — устройство удаляется из списка
DEFAULT_PORT = 5899         # UDP-порт для discovery


@dataclass
class DiscoveredDevice:
    """Обнаруженное устройство в локальной сети."""
    id: str
    name: str
    os: str
    version: str
    ip: str
    port: int               # TCP-порт для подключения (из beacon)
    last_seen: float = field(default_factory=time.time)

    @property
    def is_online(self) -> bool:
        return (time.time() - self.last_seen) < ONLINE_TIMEOUT


class DiscoveryService:
    """
    Сервис обнаружения устройств в LAN.

    Запускает два фоновых daemon-потока:
      - broadcaster: рассылает UDP broadcast beacon каждые BEACON_INTERVAL сек
      - listener:    слушает входящие beacon'ы и обновляет список устройств

    Использование:
        svc = DiscoveryService(host_id="847291035")
        svc.start()
        ...
        devices = svc.get_discovered()
        ...
        svc.stop()
    """

    def __init__(self, host_id: str, host_name: str | None = None,
                 port: int = DEFAULT_PORT, service_port: int = 5800,
                 version: str = "1.0.0"):
        self.host_id = str(host_id)
        self.host_name = host_name or socket.gethostname()
        self.port = port              # UDP discovery port
        self.service_port = service_port  # TCP port advertised in beacon
        self.version = version
        self._os = platform.system() or "Unknown"

        self._devices: dict[str, DiscoveredDevice] = {}  # id -> device
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._broadcaster: threading.Thread | None = None
        self._listener: threading.Thread | None = None

    # ------------------------------------------------------------------ public

    def start(self) -> None:
        """Запуск фоновых потоков broadcaster и listener."""
        self._stop_event.clear()

        self._broadcaster = threading.Thread(
            target=self._broadcast_loop, daemon=True, name="discovery-tx")
        self._listener = threading.Thread(
            target=self._listen_loop, daemon=True, name="discovery-rx")

        self._broadcaster.start()
        self._listener.start()
        logger.info("DiscoveryService started on UDP port %d", self.port)

    def stop(self) -> None:
        """Остановка потоков (неблокирующая, потоки — daemon)."""
        self._stop_event.set()
        if self._broadcaster:
            self._broadcaster.join(timeout=2)
        if self._listener:
            self._listener.join(timeout=2)
        logger.info("DiscoveryService stopped")

    def get_discovered(self) -> list[DiscoveredDevice]:
        """
        Возвращает список обнаруженных устройств (thread-safe).
        Устройства, не отвечавшие более STALE_TIMEOUT секунд, удаляются.
        """
        now = time.time()
        with self._lock:
            # Удаляем устаревшие
            stale_ids = [
                did for did, dev in self._devices.items()
                if (now - dev.last_seen) > STALE_TIMEOUT
            ]
            for did in stale_ids:
                del self._devices[did]

            return list(self._devices.values())

    # ----------------------------------------------------------------- private

    def _make_beacon(self) -> bytes:
        payload = {
            "id": self.host_id,
            "name": self.host_name,
            "os": self._os,
            "version": self.version,
            "port": self.service_port,
        }
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")

    def _broadcast_loop(self) -> None:
        beacon = self._make_beacon()
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(1.0)
        except OSError as exc:
            logger.warning("Discovery broadcaster: cannot create socket: %s", exc)
            return

        try:
            while not self._stop_event.is_set():
                try:
                    sock.sendto(beacon, ("<broadcast>", self.port))
                except OSError as exc:
                    logger.debug("Discovery broadcast send failed: %s", exc)
                self._stop_event.wait(BEACON_INTERVAL)
        finally:
            sock.close()

    def _listen_loop(self) -> None:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # На Windows для приёма broadcast нужно bind на 0.0.0.0
            sock.bind(("", self.port))
            sock.settimeout(1.0)
        except OSError as exc:
            logger.warning("Discovery listener: cannot bind port %d: %s",
                           self.port, exc)
            if sock:
                sock.close()
            return

        try:
            while not self._stop_event.is_set():
                try:
                    data, addr = sock.recvfrom(4096)
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop_event.is_set():
                        break
                    continue

                self._handle_beacon(data, addr[0])
        finally:
            sock.close()

    def _handle_beacon(self, data: bytes, ip: str) -> None:
        try:
            payload = json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        dev_id = payload.get("id")
        if not dev_id or str(dev_id) == self.host_id:
            return  # игнорируем свои beacon'ы

        dev = DiscoveredDevice(
            id=str(dev_id),
            name=payload.get("name", "Unknown"),
            os=payload.get("os", "Unknown"),
            version=payload.get("version", "?"),
            ip=ip,
            port=payload.get("port", 5800),
            last_seen=time.time(),
        )

        with self._lock:
            self._devices[dev.id] = dev
