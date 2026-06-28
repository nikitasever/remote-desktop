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

# --- Синхронизация изображения в буфере обмена ---
MSG_CLIPBOARD_IMAGE = 0x16  # обе стороны: сырые PNG-байты картинки из буфера обмена

# --- Аудио host -> client (TCP-путь). Отдельный диапазон 0x20+ во избежание
#     коллизий с буфером/файлами (которые заняли 0x10..0x16). ---
MSG_AUDIO_INFO = 0x20  # host -> client: JSON {"sample_rate","channels","codec"} — старт аудио
MSG_AUDIO      = 0x21  # host -> client: один закодированный (Opus) аудио-пакет

# --- Контроль доступа (роли control/view/blocked). Диапазон 0x30+. ---
MSG_ACCESS = 0x30  # host -> client: JSON {"role": "...", "reason": "..."} — эффективная роль клиента

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


class ReplayError(Exception):
    """Кадр с непрогрессирующим порядковым номером — повтор или переупорядочивание."""


class SecureChannel:
    """Шифрование/дешифрование тела сообщений поверх сокета.

    Анти-replay: внутрь каждого шифруемого тела добавляется монотонный
    8-байтный порядковый номер. Получатель отвергает кадр, чей номер не
    строго больше последнего принятого (TCP упорядочен и надёжен, поэтому
    легитимные кадры всегда возрастают; записанный/переигранный кадр имеет
    старый номер и будет отброшен с ReplayError). Счётчики per-instance и
    однонаправленные: _send_seq — для исходящих, _recv_seq — для входящих.
    """

    def __init__(self, key: bytes):
        self._aes = AESGCM(key)
        self._send_seq = 0
        self._recv_seq = -1
        self._send_lock = threading.Lock()
        self._recv_lock = threading.Lock()

    def encrypt(self, plaintext: bytes) -> bytes:
        with self._send_lock:
            seq = self._send_seq
            self._send_seq += 1
        nonce = os.urandom(12)
        body = struct.pack(">Q", seq) + plaintext
        return nonce + self._aes.encrypt(nonce, body, None)

    def decrypt(self, blob: bytes) -> bytes:
        nonce, ct = blob[:12], blob[12:]
        body = self._aes.decrypt(nonce, ct, None)  # бросит исключение при неверном пароле
        (seq,) = struct.unpack(">Q", body[:8])
        with self._recv_lock:
            if seq <= self._recv_seq:
                raise ReplayError(f"повтор кадра seq={seq} (последний {self._recv_seq})")
            self._recv_seq = seq
        return body[8:]


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


def relay_register_id(sock: socket.socket, unique_id: str) -> str:
    """Host: register unique ID on relay (new ID-based protocol)."""
    sock.sendall(f"REGISTER {unique_id}\n".encode())
    resp = b""
    while not resp.endswith(b"\n"):
        ch = sock.recv(1)
        if not ch:
            raise ConnectionError("Соединение закрыто при ожидании ответа REGISTER")
        resp += ch
    return resp.decode().strip()


def relay_connect_id(sock: socket.socket, unique_id: str) -> str:
    """Client: connect to host by unique ID via relay (new ID-based protocol)."""
    sock.sendall(f"CONNECT {unique_id}\n".encode())
    resp = b""
    while not resp.endswith(b"\n"):
        ch = sock.recv(1)
        if not ch:
            raise ConnectionError("Соединение закрыто при ожидании ответа CONNECT")
        resp += ch
    return resp.decode().strip()


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

def encode_clipboard_image(img) -> bytes:
    """PIL.Image -> сжатые PNG-байты для передачи по сети."""
    import io
    buf = io.BytesIO()
    img.convert("RGBA").save(buf, format="PNG")
    return buf.getvalue()


def grab_clipboard_image():
    """Читает картинку из буфера обмена Windows как PNG-байты.

    Возвращает bytes (PNG) либо None, если в буфере нет картинки или
    Pillow недоступен. Файловые списки (CF_HDROP) игнорируются здесь —
    ImageGrab.grabclipboard() в этом случае вернёт список путей (str),
    а не Image, и мы его пропускаем (картинку не трогаем)."""
    try:
        from PIL import ImageGrab, Image
    except Exception:
        return None
    try:
        data = ImageGrab.grabclipboard()
    except Exception:
        return None
    if not isinstance(data, Image.Image):
        return None  # либо текст/файлы, либо пусто
    try:
        return encode_clipboard_image(data)
    except Exception:
        return None


def set_clipboard_image(png_bytes: bytes) -> bool:
    """Кладёт PNG-байты в буфер обмена Windows как DIB (CF_DIB).

    Возвращает True при успехе. Использует win32clipboard (pywin32);
    при его отсутствии тихо возвращает False."""
    try:
        import io
        from PIL import Image
        import win32clipboard
    except Exception:
        return False
    try:
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        out = io.BytesIO()
        img.save(out, format="BMP")
        # BMP-файл = 14-байтный BITMAPFILEHEADER + DIB; в буфер кладём DIB.
        dib = out.getvalue()[14:]
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib)
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception:
        return False


class ClipboardSync:
    """Следит за локальным буфером обмена (текст И картинки) и шлёт
    изменения наружу; входящие данные ставит в буфер, не создавая эха
    обратно. Текстовый путь полностью совместим со старой версией.

    send_text_cb(text)         — отправить текст (как раньше).
    send_image_cb(png_bytes)   — отправить картинку PNG (опционально);
                                 если не передан, картинки не синхронятся.
    """

    def __init__(self, send_text_cb, send_image_cb=None):
        self._send = send_text_cb
        self._send_img = send_image_cb
        self._last = None          # последний известный текст
        self._last_img_hash = None  # хэш последней известной картинки
        self._alive = True
        try:
            import pyperclip
            self._pc = pyperclip
            self._last = self._safe_paste()
        except Exception:
            self._pc = None  # буфер недоступен — тихо отключаемся
        # Картинки работают независимо от pyperclip (через Pillow/pywin32).
        if self._send_img is not None:
            self._last_img_hash = self._img_hash(grab_clipboard_image())

    def _safe_paste(self):
        try:
            return self._pc.paste()
        except Exception:
            return None

    @staticmethod
    def _img_hash(png_bytes):
        if not png_bytes:
            return None
        # Дёшево: длина + hash() — достаточно, чтобы заметить смену картинки.
        return (len(png_bytes), hash(png_bytes))

    def start(self):
        # Поток нужен, если доступен хотя бы текст или картинки.
        if not self._pc and self._send_img is None:
            return
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self._alive:
            if self._pc:
                cur = self._safe_paste()
                if cur is not None and cur != self._last:
                    self._last = cur
                    try:
                        self._send(cur)
                    except Exception:
                        pass
            if self._send_img is not None:
                png = grab_clipboard_image()
                h = self._img_hash(png)
                if png is not None and h != self._last_img_hash:
                    self._last_img_hash = h
                    try:
                        self._send_img(png)
                    except Exception:
                        pass
            time.sleep(0.5)

    def on_remote(self, text):
        """Пришёл текстовый буфер с другой стороны."""
        if not self._pc:
            return
        self._last = text  # чтобы не отправить его же обратно
        try:
            self._pc.copy(text)
        except Exception:
            pass

    def on_remote_image(self, png_bytes):
        """Пришла картинка с другой стороны."""
        if self._send_img is None:
            return
        # Анти-эхо: запоминаем хэш до записи, чтобы не отправить обратно.
        self._last_img_hash = self._img_hash(png_bytes)
        set_clipboard_image(png_bytes)

    def stop(self):
        self._alive = False
