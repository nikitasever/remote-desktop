"""
HOST через WebRTC (Фаза B) — экспериментальный транспорт.

Отдаёт экран видео-треком (aiortc сам кодирует и шлёт по UDP/DTLS-SRTP с
контролем перегрузки), а ввод/служебные команды принимает по DataChannel.
Захват и инъекция ввода переиспользованы из host.py.

Запуск:
    python host_rtc.py --relay vps:5800 --id myroom --password ...
(пароль пока используется только как ID-гейт комнаты на этом этапе PoC;
DTLS-SRTP шифрует медиапоток сам по себе.)
"""

import argparse
import asyncio
import fractions
import json
import os

import numpy as np
import av
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
from aiortc.mediastreams import VideoStreamTrack

import rtc_common
import host as host_mod

LOG = print


class ScreenTrack(VideoStreamTrack):
    """Видео-трек: на каждый recv() отдаёт свежий кадр экрана (или повтор
    последнего, если ничего не изменилось — поток должен быть непрерывным)."""

    def __init__(self, streamer, fps):
        super().__init__()
        self.streamer = streamer
        self.fps = max(1, int(fps))
        self._last = None
        self._pts = 0
        self._next = None
        self._tb = fractions.Fraction(1, self.fps)

    async def recv(self):
        loop = asyncio.get_event_loop()
        now = loop.time()
        if self._next is None:
            self._next = now
        if self._next > now:
            await asyncio.sleep(self._next - now)
        self._next += 1.0 / self.fps

        frame = await loop.run_in_executor(None, self.streamer.capture)
        if frame is None:
            frame = self._last
        if frame is None:
            frame = np.zeros((self.streamer.h, self.streamer.w, 3), np.uint8)
        self._last = frame

        vf = av.VideoFrame.from_ndarray(np.ascontiguousarray(frame), format="rgb24")
        vf.pts = self._pts
        self._pts += 1
        vf.time_base = self._tb
        return vf


def _ice_config(stun):
    servers = []
    if stun:
        servers.append(RTCIceServer(urls=[stun]))
    return RTCConfiguration(iceServers=servers)


async def run(args, stop_event=None):
    streamer = host_mod.ScreenStreamer(scale=args.scale, quality=args.quality)
    injector = host_mod.InputInjector(streamer.real_w, streamer.real_h)
    clip = None

    pc = RTCPeerConnection(_ice_config(args.stun))
    pc.addTrack(ScreenTrack(streamer, args.fps))
    channel = pc.createDataChannel("control")

    @channel.on("message")
    def on_message(message):
        try:
            ev = json.loads(message)
        except Exception:
            return
        k = ev.get("k")
        if k == "ping":
            try:
                channel.send(json.dumps({"k": "pong", "t": ev.get("t")}))
            except Exception:
                pass
        elif k in ("move", "down", "up", "scroll", "kdown", "kup"):
            injector.handle(ev)
        elif k == "clipboard" and clip is not None:
            clip.on_remote(ev.get("text", ""))

    closed = asyncio.Event()

    @pc.on("connectionstatechange")
    async def on_state():
        LOG(f"[host-rtc] состояние: {pc.connectionState}")
        if pc.connectionState in ("failed", "closed", "disconnected"):
            closed.set()

    loop = asyncio.get_event_loop()
    LOG(f"[host-rtc] подключаюсь к relay {args.relay}, комната '{args.id}'...")
    s = await loop.run_in_executor(None, rtc_common.connect_relay, args.relay, args.id, "host")
    LOG("[host-rtc] пара найдена, обмен SDP...")

    await pc.setLocalDescription(await pc.createOffer())
    await loop.run_in_executor(None, rtc_common.send_msg, s,
                               {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})
    ans = await loop.run_in_executor(None, rtc_common.recv_msg, s)
    await pc.setRemoteDescription(RTCSessionDescription(ans["sdp"], ans["type"]))
    LOG("[host-rtc] соединение устанавливается (ICE/DTLS)...")

    # Сигнальный сокет держим открытым до конца: закрытие сразу после обмена
    # ресетит peer через relay до того, как тот дочитает SDP.
    try:
        # держим, пока соединение живо
        while not closed.is_set():
            if stop_event is not None and stop_event.is_set():
                break
            await asyncio.sleep(0.3)
    finally:
        try:
            s.close()
        except Exception:
            pass
        await pc.close()
        streamer.close()
        LOG("[host-rtc] завершено")


def main():
    ap = argparse.ArgumentParser(description="Remote desktop HOST через WebRTC (PoC)")
    ap.add_argument("--relay", required=True, help="Сигналинг (relay.py) vps:порт")
    ap.add_argument("--id", default="default", help="ID комнаты")
    ap.add_argument("--password", default="", help="Пока только гейт комнаты (PoC)")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--quality", type=int, default=70)
    ap.add_argument("--stun", default="", help="STUN/TURN URL, напр. stun:stun.l.google.com:19302")
    args = ap.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
