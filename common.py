"""
Общий код для host/client/relay: протокол кадрирования сообщений и
end-to-end шифрование (AES-GCM, ключ выводится из пароля через PBKDF2).

Формат сообщения в сокете:
    [4 байта BE: длина payload][payload]
Где payload = nonce(12) + AES-GCM-ciphertext.
Расшифрованные данные = [1 байт: тип][тело].

Relay НЕ знает пароль и видит только зашифрованный поток — он лишь
пересылает байты между host и client.
"""

import json
import os
import socket
import struct
import threading
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# ---- Типы сообщений (первый байт расшифрованного payload) ----
MSG_HELLO       = 0x01   # client -> host: проверка пароля/рукопожатие
MSG_SCREEN_INFO = 0x02   # host -> client: JSON {"w","h","monitors","index"}
MSG_TILES       = 0x03   # host -> client: бинарный список изменившихся плиток
MSG_INPUT       = 0x04   # client -> host: JSON событие ввода
MSG_CLIPBOARD   = 0x05   # обе стороны: JSON {"text": ...}
MSG_FILE_META   = 0x06   # client -> host: JSON {"name","size"}
MSG_FILE_CHUNK  = 0x07   # client -> host: бинарный кусок файла
MSG_FILE_END    = 0x08   # client -> host: конец передачи файла
MSG_SET_MONITOR = 0x09   # client -> host: JSON {"index": n}
MSG_PING        = 0x0A   # client -> host: JSON {"t": <время клиента>}
MSG_PONG        = 0x0B   # host -> client: эхо тела PING (для замера RTT)
MSG_VIDEO_INFO  = 0x0C   # host -> client: JSON {"codec","w","h","fps"} — старт видео-потока
MSG_VIDEO       = 0x0D   # host -> client: [1 байт keyframe] + H.264-пакет

# --- Двусторонняя передача файлов и удалённый браузер ---
MSG_FILE_PULL_REQ  = 0x10  # client -> host: JSON {"path": "..."} — запрос файла с host'а
MSG_HOST_FILE_META = 0x11  # host -> client: JSON {"name","size"} — мета для обратного файла
MSG_HOST_FILE_CHUNK= 0x12  # host -> client: бинарный кусок файла
MSG_HOST_FILE_END  = 0x13  # host -> client: конец передачи файла
MSG_DIR_LIST_REQ   = 0x14  # client -> host: JSON {"path": "..."} — запрос содержимого каталога
MSG_DIR_LIST_RESP  = 0x15  # host -> client: JSON {"path","entries":[{"name","size","is_dir"},...]}

# Соль фиксированная — пароль общий секрет, обмена солью нет.
# Безопасность держится на длине/случайности пароля.
_SALT = b"rdp-py-static-salt-v1"


def derive_key(password: str) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_SALT,
        iterations=200_000,
    )
    return kdf.derive(password.encode("utf-8"))


class SecureChannel:
    """Шифрование/дешифрование тела сообщений поверх сокета."""

    def __init__(self, key: bytes):
        self._aes = AESGCM(key)

    def encrypt(self, plaintext: bytes) -> bytes:
        nonce = os.urandom(12)
        return nonce + self._aes.encrypt(nonce, plaintext, None)

    def decrypt(self, blob: bytes) -> bytes:
        nonce, ct = blob[:12], blob[12:]
        return self._aes.decrypt(nonce, ct, None)  # бросит исключение при неверном пароле


# ---- Кадрирование на сокете ----

def _recv_exactly(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Соединение закрыто")
        buf.extend(chunk)
    return bytes(buf)


def send_frame(sock: socket.socket, chan: SecureChannel, msg_type: int, body: bytes):
    payload = chan.encrypt(bytes([msg_type]) + body)
    sock.sendall(struct.pack(">I", len(payload)) + payload)


def recv_frame(sock: socket.socket, chan: SecureChannel):
    (length,) = struct.unpack(">I", _recv_exactly(sock, 4))
    if length > 64 * 1024 * 1024:
        raise ConnectionError("Слишком большой кадр — вероятно, рассинхрон/неверный пароль")
    payload = _recv_exactly(sock, length)
    data = chan.decrypt(payload)
    return data[0], data[1:]


class FrameSender:
    """Потокобезопасная отправка: несколько потоков (экран/буфер/файлы)
    шлют в один сокет, поэтому каждый кадр атомарен под локом."""
    def __init__(self, sock, chan):
        self.sock = sock
        self.chan = chan
        self.lock = threading.Lock()

    def send(self, msg_type, body=b""):
        with self.lock:
            send_frame(self.sock, self.chan, msg_type, body)

    def send_json(self, msg_type, obj):
        self.send(msg_type, json.dumps(obj).encode("utf-8"))


# ---- Хелперы для JSON-сообщений ----

def send_json(sock, chan, msg_type, obj):
    send_frame(sock, chan, msg_type, json.dumps(obj).encode("utf-8"))


def parse_json(body: bytes):
    return json.loads(body.decode("utf-8"))


# ---- Настройка сокета ----

def enable_keepalive(sock: socket.socket, idle=10, interval=3, count=3):
    """Включает TCP keepalive, чтобы полу-мёртвое соединение (другая сторона
    исчезла без FIN — обрыв сети, спящий ПК) отвалилось за ~idle+interval*count
    секунд, а не висело минутами. Это и есть «закрытие старых/зависших сессий»:
    освобождает роль на relay и слот на host. Тихо игнорирует, если ОС не даёт.
    """
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except OSError:
        return
    # Windows: интервалы задаются одним ioctl (значения в миллисекундах).
    if hasattr(socket, "SIO_KEEPALIVE_VALS"):
        try:
            sock.ioctl(socket.SIO_KEEPALIVE_VALS,
                       (1, int(idle * 1000), int(interval * 1000)))
        except OSError:
            pass
    else:  # Linux/*nix: отдельные опции, если доступны
        for opt, val in (("TCP_KEEPIDLE", idle), ("TCP_KEEPINTVL", interval),
                         ("TCP_KEEPCNT", count)):
            o = getattr(socket, opt, None)
            if o is not None:
                try:
                    sock.setsockopt(socket.IPPROTO_TCP, o, val)
                except OSError:
                    pass


# ---- Регистрация на relay (НЕ шифруется: это служебная маршрутизация) ----

def relay_register(sock: socket.socket, role: str, session: str):
    """Отправляет relay'ю строку-регистрацию, оканчивающуюся \\n."""
    line = json.dumps({"role": role, "session": session}) + "\n"
    sock.sendall(line.encode("utf-8"))


def relay_read_line(sock: socket.socket) -> str:
    buf = bytearray()
    while True:
        ch = sock.recv(1)
        if not ch:
            raise ConnectionError("Соединение закрыто до завершения регистрации")
        if ch == b"\n":
            break
        buf.extend(ch)
        if len(buf) > 4096:
            raise ConnectionError("Слишком длинная строка регистрации")
    return buf.decode("utf-8")


# ---- Синхронизация буфера обмена (используют и host, и client) ----

class ClipboardSync:
    """Следит за локальным буфером обмена и шлёт изменения наружу;
    входящий текст ставит в буфер, не создавая эха обратно."""

    def __init__(self, send_text_cb):
        self._send = send_text_cb
        self._last = None
        self._alive = True
        try:
            import pyperclip
            self._pc = pyperclip
            self._last = self._safe_paste()
        except Exception:
            self._pc = None  # буфер недоступен — тихо отключаемся

    def _safe_paste(self):
        try:
            return self._pc.paste()
        except Exception:
            return None

    def start(self):
        if not self._pc:
            return
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self._alive:
            cur = self._safe_paste()
            if cur is not None and cur != self._last:
                self._last = cur
                try:
                    self._send(cur)
                except Exception:
                    pass
            time.sleep(0.5)

    def on_remote(self, text):
        """Пришёл буфер с другой стороны."""
        if not self._pc:
            return
        self._last = text  # чтобы не отправить его же обратно
        try:
            self._pc.copy(text)
        except Exception:
            pass

    def stop(self):
        self._alive = False
