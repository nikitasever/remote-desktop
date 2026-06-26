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
import os
import socket

from aiortc import RTCConfiguration, RTCIceServer

import common

# ── ICE configuration ────────────────────────────────────────────────

DEFAULT_STUN = ["stun:stun.l.google.com:19302"]


def build_ice_config(
    stun_urls=None,
    turn_url=None,
    turn_user=None,
    turn_pass=None,
):
    """Build an aiortc RTCConfiguration with STUN and optional TURN servers.

    Parameters
    ----------
    stun_urls : list[str] | None
        STUN server URLs (e.g. ``["stun:stun.l.google.com:19302"]``).
        *None* (default) → use ``DEFAULT_STUN``.
        Pass an empty list to disable STUN.
    turn_url : str | None
        TURN server URL, e.g. ``"turn:vps.example.com:3478"``.
    turn_user, turn_pass : str | None
        Long-term credentials for the TURN server.

    Returns
    -------
    RTCConfiguration
    """
    servers: list[RTCIceServer] = []

    # STUN
    urls = DEFAULT_STUN if stun_urls is None else list(stun_urls)
    if urls:
        servers.append(RTCIceServer(urls=urls))

    # TURN (requires credentials)
    if turn_url:
        servers.append(
            RTCIceServer(
                urls=[turn_url],
                username=turn_user or "",
                credential=turn_pass or "",
            )
        )

    return RTCConfiguration(iceServers=servers)


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
