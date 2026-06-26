"""
CLIENT через WebRTC (Фаза B) — экспериментальный транспорт.

Принимает экран видео-треком, рендерит в окне pygame; мышь/клавиатуру шлёт
по DataChannel. Переиспользует разбор клавиш (key_ident) и карту PG_SPECIAL
из client.py — включая фикс сочетаний клавиш.

Архитектура: asyncio (aiortc) крутится в фоновом потоке, pygame — в главном.
Кадры кладутся в общий буфер, ввод отправляется через loop.call_soon_threadsafe.

Запуск:
    python client_rtc.py --relay vps:5800 --id myroom --password ...
"""

import argparse
import asyncio
import json
import os
import threading
import time

import numpy as np
import pygame
from aiortc import RTCPeerConnection, RTCSessionDescription

import rtc_common
import client as client_mod


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.frame = None          # последний RGB ndarray
        self.alive = True
        self.channel = None
        self.loop = None
        self.rtt = None
        self.connected = False


def _parse_ice(args):
    """Build RTCConfiguration from CLI args / env vars."""
    stun_urls = None
    if args.stun is not None:
        stun_urls = [u.strip() for u in args.stun.split(",") if u.strip()]
    turn_url  = args.turn  or os.environ.get("RD_TURN_URL")
    turn_user = args.turn_user or os.environ.get("RD_TURN_USER")
    turn_pass = args.turn_pass or os.environ.get("RD_TURN_PASS")
    return rtc_common.build_ice_config(stun_urls, turn_url, turn_user, turn_pass)


async def amain(args, state):
    pc = RTCPeerConnection(_parse_ice(args))

    @pc.on("track")
    def on_track(track):
        if track.kind == "video":
            asyncio.ensure_future(_consume(track, state))

    @pc.on("datachannel")
    def on_dc(ch):
        state.channel = ch

        @ch.on("message")
        def on_msg(message):
            try:
                ev = json.loads(message)
            except Exception:
                return
            if ev.get("k") == "pong" and ev.get("t"):
                state.rtt = (time.time() - ev["t"]) * 1000.0

    @pc.on("connectionstatechange")
    async def on_state():
        print(f"[client-rtc] состояние: {pc.connectionState}")
        if pc.connectionState == "connected":
            state.connected = True
        if pc.connectionState in ("failed", "closed", "disconnected"):
            state.alive = False

    loop = asyncio.get_event_loop()
    state.loop = loop
    print(f"[client-rtc] подключаюсь к relay {args.relay}, комната '{args.id}'...")
    s = await loop.run_in_executor(None, rtc_common.connect_relay, args.relay, args.id, "client")
    offer = await loop.run_in_executor(None, rtc_common.recv_msg, s)
    await pc.setRemoteDescription(RTCSessionDescription(offer["sdp"], offer["type"]))
    await pc.setLocalDescription(await pc.createAnswer())
    await loop.run_in_executor(None, rtc_common.send_msg, s,
                               {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})
    print("[client-rtc] SDP обменян, устанавливаю соединение...")

    # Сигнальный сокет НЕ закрываем сразу (иначе relay ресетит host до чтения).
    try:
        while state.alive:
            await asyncio.sleep(0.2)
    finally:
        try:
            s.close()
        except Exception:
            pass
        await pc.close()


async def _consume(track, state):
    while state.alive:
        try:
            frame = await track.recv()
        except Exception:
            break
        img = frame.to_ndarray(format="rgb24")
        with state.lock:
            state.frame = img


def _start_loop(args, state):
    asyncio.run(amain(args, state))


def run_ui(args, state):
    pygame.init()
    pygame.display.set_caption("Remote Desktop (WebRTC) — подключение...")
    win = pygame.display.set_mode((960, 600), pygame.RESIZABLE)
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("Consolas", 16)

    def send(ev):
        ch, loop = state.channel, state.loop
        if ch is not None and loop is not None and ch.readyState == "open":
            try:
                loop.call_soon_threadsafe(ch.send, json.dumps(ev))
            except Exception:
                pass

    def to_norm(pos):
        ww, wh = win.get_size()
        return (max(0.0, min(1.0, pos[0] / ww)), max(0.0, min(1.0, pos[1] / wh)))

    BTN = {1: "left", 2: "middle", 3: "right"}
    held = {}
    last_ping = 0.0
    surface = None
    last_id = None

    while state.alive:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                state.alive = False
            elif event.type == pygame.VIDEORESIZE:
                win = pygame.display.set_mode(event.size, pygame.RESIZABLE)
            elif event.type == pygame.MOUSEMOTION:
                x, y = to_norm(event.pos)
                send({"k": "move", "x": x, "y": y})
            elif event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                if event.button in (4, 5):
                    if event.type == pygame.MOUSEBUTTONDOWN:
                        send({"k": "scroll", "dx": 0, "dy": 1 if event.button == 4 else -1})
                else:
                    x, y = to_norm(event.pos)
                    kind = "down" if event.type == pygame.MOUSEBUTTONDOWN else "up"
                    send({"k": kind, "x": x, "y": y, "btn": BTN.get(event.button, "left")})
            elif event.type in (pygame.KEYDOWN, pygame.KEYUP):
                mods = pygame.key.get_mods()
                hotkey = (mods & pygame.KMOD_CTRL) and (mods & pygame.KMOD_ALT)
                if event.type == pygame.KEYDOWN and hotkey and event.key == pygame.K_q:
                    state.alive = False
                    break
                if event.type == pygame.KEYDOWN:
                    ident = client_mod.key_ident(event, mods)
                    if ident is not None:
                        held[event.key] = ident
                        send({"k": "kdown", **ident})
                else:
                    ident = held.pop(event.key, None)
                    if ident is None and event.key in client_mod.PG_SPECIAL:
                        ident = {"name": client_mod.PG_SPECIAL[event.key]}
                    if ident is not None:
                        send({"k": "kup", **ident})
            elif event.type == getattr(pygame, "WINDOWFOCUSLOST", -1):
                for ident in list(held.values()):
                    send({"k": "kup", **ident})
                held.clear()

        # PING раз в секунду
        now = time.time()
        if now - last_ping >= 1.0:
            send({"k": "ping", "t": now})
            last_ping = now

        # Рендер кадра
        with state.lock:
            img = state.frame
        if img is not None and id(img) != last_id:
            last_id = id(img)
            surface = pygame.image.frombuffer(img.tobytes(), (img.shape[1], img.shape[0]), "RGB")
        if surface is not None:
            if surface.get_size() == win.get_size():
                win.blit(surface, (0, 0))
            else:
                win.blit(pygame.transform.smoothscale(surface, win.get_size()), (0, 0))
        else:
            win.fill((20, 20, 20))

        rtt = state.rtt
        line = (f" WebRTC {'OK' if state.connected else '...'}   "
                f"RTT: {rtt:.0f} ms" if rtt is not None else
                f" WebRTC {'OK' if state.connected else '...'}   RTT: — ")
        txt = font.render(line + "   (Ctrl+Alt+Q — выход)", True, (255, 255, 255))
        bg = pygame.Surface((txt.get_width(), txt.get_height()))
        bg.set_alpha(150); bg.fill((0, 0, 0))
        win.blit(bg, (6, 6)); win.blit(txt, (6, 6))

        pygame.display.flip()
        clock.tick(60)

    pygame.quit()


def main():
    ap = argparse.ArgumentParser(description="Remote desktop CLIENT через WebRTC (PoC)")
    ap.add_argument("--relay", required=True, help="Сигналинг (relay.py) vps:порт")
    ap.add_argument("--id", default="default", help="ID комнаты")
    ap.add_argument("--password", default="", help="Пока только гейт комнаты (PoC)")
    ap.add_argument("--stun", default=None,
                    help="STUN URL(s), через запятую. По умолчанию stun:stun.l.google.com:19302; "
                         "передайте '' чтобы отключить")
    ap.add_argument("--turn", default="", help="TURN URL, напр. turn:vps:3478 (или env RD_TURN_URL)")
    ap.add_argument("--turn-user", default="", help="TURN username (или env RD_TURN_USER)")
    ap.add_argument("--turn-pass", default="", help="TURN password (или env RD_TURN_PASS)")
    args = ap.parse_args()

    state = State()
    t = threading.Thread(target=_start_loop, args=(args, state), daemon=True)
    t.start()
    try:
        run_ui(args, state)
    finally:
        state.alive = False


if __name__ == "__main__":
    main()
