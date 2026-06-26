"""
Сигналинг для WebRTC (Фаза B). Переиспользует relay.py как «слепой» канал:
host и client сводятся по ID комнаты, после чего обмениваются SDP offer/answer
строками JSON (по одной на сообщение, разделитель — \\n).

SDP содержит переводы строк, но json.dumps экранирует их как \\n внутри строки,
поэтому на проводе сообщение остаётся однострочным — relay_read_line корректно
читает его до настоящего \\n-разделителя.

ICE-кандидаты не «тричклим»: aiortc в setLocalDescription дожидается окончания
сбора кандидатов и вкладывает их прямо в SDP — хватает двух сообщений.
"""

import json
import socket

import common


def connect_relay(relay_addr, room, role, timeout=20):
    """Подключиться к relay, зарегистрироваться, дождаться пары. -> socket."""
    host, port = relay_addr.rsplit(":", 1)
    s = socket.create_connection((host, int(port)), timeout=timeout)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    common.relay_register(s, role, room)
    common.relay_read_line(s)   # ждём '{"event":"paired"}'
    s.settimeout(None)
    return s


def send_msg(s, obj):
    s.sendall((json.dumps(obj) + "\n").encode("utf-8"))


def _read_line(sock, max_len=65536):
    """Читает строку до \\n — увеличенный лимит для SDP (audio+video > 4 KiB)."""
    buf = bytearray()
    while True:
        ch = sock.recv(1)
        if not ch:
            raise ConnectionError("соединение закрыто до завершения строки")
        if ch == b"\n":
            break
        buf.extend(ch)
        if len(buf) > max_len:
            raise ConnectionError("слишком длинная строка")
    return buf.decode("utf-8")


def recv_msg(s):
    return json.loads(_read_line(s))
