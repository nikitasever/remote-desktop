"""
Безголовый smoke-тест WebRTC-пути (Фаза B): relay + host_rtc + in-process
aiortc-клиент. Проверяет: установку соединения, приём видео-кадров по медиа-
треку и round-trip по DataChannel (ping->pong). Ввод НЕ инжектируется
(шлём только ping), реальный курсор не трогается.
"""
import asyncio
import json
import os
import subprocess
import sys
import time

from aiortc import RTCPeerConnection, RTCSessionDescription

import rtc_common

PY = sys.executable
HERE = os.path.dirname(os.path.abspath(__file__))
RELAY_PORT = 5812
ROOM = "rtcsmoke"

ok = []
def check(name, cond):
    ok.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")


async def client_test():
    pc = RTCPeerConnection()
    got = {"frames": 0}
    ch_open = asyncio.Event()
    pong = asyncio.Event()
    chan = {"c": None}

    @pc.on("track")
    def on_track(track):
        if track.kind == "video":
            asyncio.ensure_future(pull(track))

    async def pull(track):
        while got["frames"] < 30:
            try:
                await asyncio.wait_for(track.recv(), timeout=12)
            except Exception:
                break
            got["frames"] += 1

    @pc.on("datachannel")
    def on_dc(ch):
        chan["c"] = ch

        @ch.on("message")
        def on_msg(m):
            try:
                if json.loads(m).get("k") == "pong":
                    pong.set()
            except Exception:
                pass

        @ch.on("open")
        def on_open():
            ch_open.set()

        if ch.readyState == "open":
            ch_open.set()

    loop = asyncio.get_event_loop()
    s = await loop.run_in_executor(None, rtc_common.connect_relay,
                                   f"127.0.0.1:{RELAY_PORT}", ROOM, "client")
    offer = await loop.run_in_executor(None, rtc_common.recv_msg, s)
    await pc.setRemoteDescription(RTCSessionDescription(offer["sdp"], offer["type"]))
    await pc.setLocalDescription(await pc.createAnswer())
    await loop.run_in_executor(None, rtc_common.send_msg, s,
                               {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})
    # сигнальный сокет держим открытым до конца сессии

    # ждём кадры
    t0 = time.time()
    while got["frames"] < 5 and time.time() - t0 < 18:
        await asyncio.sleep(0.2)
    check("получены видео-кадры по WebRTC (>=5)", got["frames"] >= 5)
    print(f"      кадров получено: {got['frames']}")

    # datachannel ping -> pong
    dc_ok = False
    try:
        await asyncio.wait_for(ch_open.wait(), timeout=12)
        chan["c"].send(json.dumps({"k": "ping", "t": time.time()}))
        await asyncio.wait_for(pong.wait(), timeout=12)
        dc_ok = True
    except Exception as e:
        print(f"      datachannel error: {e}")
    check("DataChannel ping->pong", dc_ok)

    try:
        s.close()
    except Exception:
        pass
    await pc.close()


def main():
    env = dict(os.environ)
    relay = subprocess.Popen([PY, "relay.py", "--port", str(RELAY_PORT)], cwd=HERE, env=env)
    time.sleep(1.0)
    host = subprocess.Popen([PY, "host_rtc.py", "--relay", f"127.0.0.1:{RELAY_PORT}",
                             "--id", ROOM, "--fps", "20"], cwd=HERE, env=env)
    time.sleep(1.5)
    try:
        asyncio.run(client_test())
    finally:
        for p in (host, relay):
            try:
                p.terminate()
            except Exception:
                pass
        time.sleep(0.7)

    total = len(ok)
    passed = sum(1 for x in ok if x)
    print(f"\nИТОГ (WebRTC): {passed}/{total} проверок пройдено")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
