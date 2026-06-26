"""
Безголовый end-to-end smoke-тест: relay + host + (этот скрипт как client).
НЕ шлёт событий ввода — реальный курсор/клавиатура не трогаются.
Проверяет: шифрование/кадрирование, рукопожатие через relay, поток экрана
(видео-движок H.264 И плиточный фолбэк), декод, переключение монитора,
передачу файла host'у, а также юнит-roundtrip видео-кодека.
"""
import io
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time

import common
from PIL import Image

try:
    import video
except Exception:
    video = None

PY = sys.executable
HERE = os.path.dirname(os.path.abspath(__file__))
RELAY_PORT = 5811
PW = "smoke-test-password-123456"
ROOM = "smoketest"

ok = []
def check(name, cond):
    ok.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")


def unit_crypto():
    print("1) Юнит: шифрование + кадрирование (in-process через socketpair)")
    key = common.derive_key(PW)
    a, b = socket.socketpair()
    ca, cb = common.SecureChannel(key), common.SecureChannel(key)
    common.send_frame(a, ca, common.MSG_INPUT, b"hello-binary-\x00\xff")
    mt, body = common.recv_frame(b, cb)
    check("round-trip типа сообщения", mt == common.MSG_INPUT)
    check("round-trip тела", body == b"hello-binary-\x00\xff")
    bad = common.SecureChannel(common.derive_key("wrong"))
    common.send_frame(a, ca, common.MSG_INPUT, b"secret")
    try:
        common.recv_frame(b, bad)
        check("неверный пароль отвергается", False)
    except Exception:
        check("неверный пароль отвергается", True)
    a.close(); b.close()


def unit_video():
    print("1b) Юнит: видео-кодек H.264 (encode -> decode roundtrip)")
    if video is None:
        check("PyAV/video доступен", False)
        return
    import numpy as np
    w, h = 320, 240
    enc = video.VideoEncoder(w, h, fps=30, bitrate=video.quality_to_bitrate(70, w, h))
    dec = video.VideoDecoder()
    print(f"      энкодер: {enc.name}, битрейт {enc.bitrate // 1000} кбит/с")
    decoded = 0
    for i in range(8):
        img = (np.random.rand(h, w, 3) * 30 + i * 20).astype("uint8")
        for data, is_key in enc.encode(img):
            for rgb in dec.decode(data):
                if rgb.shape == (h, w, 3):
                    decoded += 1
    check("выбран H.264-энкодер", enc.name is not None)
    check("кадры проходят encode->decode", decoded >= 1)
    enc.close(); dec.close()


def e2e(use_video):
    label = "видео H.264" if use_video else "плитки"
    print(f"2) E2E через relay (порт {RELAY_PORT}) — движок: {label}")
    downloads = tempfile.mkdtemp(prefix="rd_smoke_")
    env = dict(os.environ)
    relay = subprocess.Popen([PY, "relay.py", "--port", str(RELAY_PORT)],
                             cwd=HERE, env=env)
    time.sleep(1.0)
    host_cmd = [PY, "host.py", "--relay", f"127.0.0.1:{RELAY_PORT}",
                "--id", ROOM, "--password", PW, "--downloads", downloads]
    if not use_video:
        host_cmd += ["--engine", "tiles"]
    host = subprocess.Popen(host_cmd, cwd=HERE, env=env)
    time.sleep(1.5)  # дать host'у зарегистрироваться на relay

    key = common.derive_key(PW)
    chan = common.SecureChannel(key)
    dec = video.VideoDecoder() if (use_video and video) else None
    try:
        s = socket.create_connection(("127.0.0.1", RELAY_PORT), timeout=5)
        common.relay_register(s, "client", ROOM)
        line = common.relay_read_line(s)            # "paired"
        check("relay свёл host и client", "paired" in line)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        common.send_frame(s, chan, common.MSG_HELLO,
                          json.dumps({"video": use_video}).encode("utf-8"))

        s.settimeout(8)
        got_info = None
        frames_seen = 0
        decoded_ok = False
        deadline = time.time() + 8
        while time.time() < deadline and (got_info is None or frames_seen < 1):
            mt, body = common.recv_frame(s, chan)
            if mt == common.MSG_SCREEN_INFO:
                got_info = common.parse_json(body)
            elif mt == common.MSG_VIDEO_INFO:
                vinfo = common.parse_json(body)
                print(f"      видео: {vinfo.get('codec')} "
                      f"{vinfo.get('w')}x{vinfo.get('h')} @ {vinfo.get('fps')}")
            elif mt == common.MSG_VIDEO and use_video:
                frames_seen += 1
                if not decoded_ok and dec is not None:
                    if dec.decode(body[1:]):
                        decoded_ok = True
            elif mt == common.MSG_TILES and not use_video:
                frames_seen += 1
                if not decoded_ok:
                    (n,) = struct.unpack(">H", body[:2])
                    if n:
                        x, y, ln = struct.unpack(">HHI", body[2:10])
                        Image.open(io.BytesIO(body[10:10 + ln])).convert("RGB").load()
                        decoded_ok = True

        check("получен SCREEN_INFO", got_info is not None)
        if got_info:
            check("в SCREEN_INFO есть w/h/monitors/index",
                  all(k in got_info for k in ("w", "h", "monitors", "index")))
            print(f"      экран {got_info.get('w')}x{got_info.get('h')}, "
                  f"мониторов: {got_info.get('monitors')}")
        check(f"получены кадры ({label})", frames_seen > 0)
        check(f"кадр декодируется ({label})", decoded_ok)

        # переключение монитора -> host пришлёт новый SCREEN_INFO
        common.send_frame(s, chan, common.MSG_SET_MONITOR, b'{"index": 1}')
        got_reinfo = False
        deadline = time.time() + 4
        while time.time() < deadline:
            mt, body = common.recv_frame(s, chan)
            if mt == common.MSG_SCREEN_INFO:
                got_reinfo = True
                break
        check("после set_monitor пришёл новый SCREEN_INFO", got_reinfo)

        # PING -> PONG (замер RTT)
        t_ping = time.time()
        common.send_json(s, chan, common.MSG_PING, {"t": t_ping})
        got_pong = False
        deadline = time.time() + 4
        while time.time() < deadline:
            mt, body = common.recv_frame(s, chan)
            if mt == common.MSG_PONG:
                got_pong = common.parse_json(body).get("t") == t_ping
                break
        check("PING отражается как PONG", got_pong)

        # передача файла host'у
        payload = b"smoke-file-content-" + os.urandom(8).hex().encode()
        common.send_json(s, chan, common.MSG_FILE_META,
                         {"name": "smoke.txt", "size": len(payload)})
        common.send_frame(s, chan, common.MSG_FILE_CHUNK, payload)
        common.send_frame(s, chan, common.MSG_FILE_END, b"")
        time.sleep(1.0)
        target = os.path.join(downloads, "smoke.txt")
        exists = os.path.exists(target)
        check("файл сохранён на host", exists)
        if exists:
            check("содержимое файла совпадает",
                  open(target, "rb").read() == payload)

        s.close()
    finally:
        if dec:
            dec.close()
        for p in (host, relay):
            try:
                p.terminate()
            except Exception:
                pass
        time.sleep(0.7)


if __name__ == "__main__":
    unit_crypto()
    unit_video()
    e2e(use_video=True)
    e2e(use_video=False)
    total = len(ok)
    passed = sum(1 for x in ok if x)
    print(f"\nИТОГ: {passed}/{total} проверок пройдено")
    sys.exit(0 if passed == total else 1)
