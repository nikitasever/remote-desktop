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
import adaptive

LOG = print


class ScreenTrack(VideoStreamTrack):
    """Видео-трек: на каждый recv() отдаёт свежий кадр экрана (или повтор
    последнего, если ничего не изменилось — поток должен быть непрерывным)."""

    def __init__(self, streamer, fps):
        super().__init__()
        self.streamer = streamer
        self.fps = max(1, int(fps))
        self.target_scale = 1.0           # adaptive: 1.0 / 0.75 / 0.5
        self._last = None
        self._pts = 0
        self._next = None
        self._tb = fractions.Fraction(1, self.fps)
        self._tb_val = 1.0 / self.fps     # float cache for pacing

    async def recv(self):
        loop = asyncio.get_event_loop()
        now = loop.time()
        if self._next is None:
            self._next = now
        if self._next > now:
            await asyncio.sleep(self._next - now)
        self._next += self._tb_val

        frame = await loop.run_in_executor(None, self.streamer.capture)
        if frame is None:
            frame = self._last
        if frame is None:
            frame = np.zeros((self.streamer.h, self.streamer.w, 3), np.uint8)
        self._last = frame

        # --- adaptive downscale ---
        scale = self.target_scale
        if scale < 1.0:
            h, w = frame.shape[:2]
            new_h, new_w = max(1, int(h * scale)), max(1, int(w * scale))
            # Use av (Pillow-free, no cv2) for fast resize via VideoFrame
            tmp = av.VideoFrame.from_ndarray(np.ascontiguousarray(frame), format="rgb24")
            tmp = tmp.reformat(width=new_w, height=new_h)
            frame = tmp.to_ndarray(format="rgb24")

        vf = av.VideoFrame.from_ndarray(np.ascontiguousarray(frame), format="rgb24")
        vf.pts = self._pts
        self._pts += 1
        vf.time_base = fractions.Fraction(1, max(1, self.fps))
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
    screen_track = ScreenTrack(streamer, args.fps)
    pc.addTrack(screen_track)
    channel = pc.createDataChannel("control")

    # Adaptive quality controller
    qc = adaptive.QualityController(base_fps=args.fps)
    qc_stop = asyncio.Event()

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

    # Start adaptive quality loop
    qc_task = asyncio.ensure_future(
        adaptive.run_quality_loop(pc, screen_track, qc, qc_stop)
    )

    # Сигнальный сокет держим открытым до конца: закрытие сразу после обмена
    # ресетит peer через relay до того, как тот дочитает SDP.
    try:
        # держим, пока соединение живо
        while not closed.is_set():
            if stop_event is not None and stop_event.is_set():
                break
            await asyncio.sleep(0.3)
    finally:
        qc_stop.set()
        try:
            await asyncio.wait_for(qc_task, timeout=3)
        except Exception:
            pass
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
