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
    def __init__(self, mute=False):
        self.lock = threading.Lock()
        self.frame = None          # последний RGB ndarray
        self.alive = True
        self.channel = None
        self.loop = None
        self.rtt = None
        self.connected = False
        self.mute = mute


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
        elif track.kind == "audio" and not state.mute:
            asyncio.ensure_future(_consume_audio(track, state))

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


async def _consume_audio(track, state):
    """Принимает аудио-трек и воспроизводит через sounddevice."""
    try:
        import sounddevice as sd
    except ImportError:
        print("[client-rtc] sounddevice не установлен — аудио выключено")
        return

    out_stream = None
    try:
        while state.alive:
            try:
                frame = await track.recv()
            except Exception:
                break
            # aiortc декодирует Opus -> av.AudioFrame (s16, 48kHz)
            pcm = frame.to_ndarray()  # shape: (channels, samples) s16
            sr = frame.sample_rate or 48000
            ch = pcm.shape[0] if pcm.ndim == 2 else 1
            # sounddevice ожидает (samples, channels), int16
            if pcm.ndim == 2:
                samples = pcm.T.copy()  # (samples, channels)
            else:
                samples = pcm.reshape(-1, 1).copy()
            if out_stream is None:
                out_stream = sd.OutputStream(
                    samplerate=sr, channels=ch, dtype="int16",
                    blocksize=samples.shape[0],
                )
                out_stream.start()
                print(f"[client-rtc] аудио: воспроизведение {sr} Hz, {ch} ch")
            try:
                out_stream.write(samples)
            except Exception:
                pass
    finally:
        if out_stream is not None:
            try:
                out_stream.stop()
                out_stream.close()
            except Exception:
                pass


def _start_loop(args, state):
    asyncio.run(amain(args, state))


def run_ui(args, state):
    pygame.init()
    pygame.display.set_caption("Remote Desktop (WebRTC)")
    win = pygame.display.set_mode((960, 600), pygame.RESIZABLE)
    clock = pygame.time.Clock()

    # Шрифты
    ui_font = pygame.font.SysFont("Segoe UI", 15)
    mono_font = pygame.font.SysFont("Consolas", 13)
    icon_font = pygame.font.SysFont("Segoe UI Emoji,Segoe UI Symbol,Arial", 16)

    # Цвета и константы
    COL_BG      = (26, 26, 46)      # #1a1a2e
    COL_ACCENT  = (0, 120, 212)     # #0078d4
    COL_HOVER   = (42, 42, 78)      # #2a2a4e
    COL_WHITE   = (255, 255, 255)
    COL_GREEN   = (50, 205, 50)
    COL_YELLOW  = (255, 200, 0)
    COL_RED     = (220, 50, 50)
    COL_DIM     = (160, 160, 180)
    TOOLBAR_H   = 40
    TOOLBAR_ALPHA = 200
    TRIGGER_ZONE = 5       # пикселей от верхнего края для показа тулбара
    HIDE_DELAY   = 2.0     # секунды до автоскрытия

    # Состояние тулбара и HUD
    toolbar_visible = False
    toolbar_show_time = 0.0
    show_stats = False
    hover_btn = None       # индекс кнопки под курсором

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

    def _rounded_rect(surf, rect, color, alpha, radius=6):
        """Рисует полупрозрачный прямоугольник со скруглёнными углами."""
        tmp = pygame.Surface((rect[2], rect[3]), pygame.SRCALPHA)
        pygame.draw.rect(tmp, (*color, alpha), (0, 0, rect[2], rect[3]),
                         border_radius=radius)
        surf.blit(tmp, (rect[0], rect[1]))

    # Определения кнопок тулбара: (label_fn, action)
    def _mute_label():
        return "\U0001f507" if state.mute else "\U0001f50a"   # 🔇 / 🔊

    def _toggle_mute():
        state.mute = not state.mute

    def _stats_label():
        return "\U0001f4ca"  # 📊

    def _toggle_stats():
        nonlocal show_stats
        show_stats = not show_stats

    def _close_label():
        return "✕"      # ✕

    def _close_action():
        state.alive = False

    toolbar_buttons = [
        (_mute_label, _toggle_mute),
        (_stats_label, _toggle_stats),
        (_close_label, _close_action),
    ]
    TB_BTN_W = 36
    TB_BTN_H = 28
    TB_BTN_PAD = 6

    def _toolbar_btn_rects(win_w):
        """Возвращает список pygame.Rect для кнопок тулбара (правая часть)."""
        rects = []
        x = win_w - TB_BTN_PAD - len(toolbar_buttons) * (TB_BTN_W + TB_BTN_PAD)
        y = (TOOLBAR_H - TB_BTN_H) // 2
        for _ in toolbar_buttons:
            rects.append(pygame.Rect(x, y, TB_BTN_W, TB_BTN_H))
            x += TB_BTN_W + TB_BTN_PAD
        return rects

    def _draw_toolbar(surf):
        nonlocal hover_btn
        ww = surf.get_width()

        # Фон тулбара
        _rounded_rect(surf, (0, 0, ww, TOOLBAR_H), COL_BG, TOOLBAR_ALPHA, radius=0)

        # Статус подключения (левая часть)
        status = "WebRTC OK" if state.connected else "Connecting..."
        status_col = COL_GREEN if state.connected else COL_YELLOW
        dot_x, dot_y = 12, TOOLBAR_H // 2
        pygame.draw.circle(surf, status_col, (dot_x, dot_y), 4)
        st_surf = ui_font.render(status, True, COL_WHITE)
        surf.blit(st_surf, (dot_x + 10, (TOOLBAR_H - st_surf.get_height()) // 2))

        # Кнопки (правая часть)
        mx, my = pygame.mouse.get_pos()
        rects = _toolbar_btn_rects(ww)
        hover_btn = None
        for i, (label_fn, _action) in enumerate(toolbar_buttons):
            r = rects[i]
            is_hover = r.collidepoint(mx, my)
            if is_hover:
                hover_btn = i
                _rounded_rect(surf, (r.x, r.y, r.w, r.h), COL_HOVER, 255, radius=4)
            lbl = label_fn()
            lbl_surf = icon_font.render(lbl, True, COL_WHITE)
            lx = r.x + (r.w - lbl_surf.get_width()) // 2
            ly = r.y + (r.h - lbl_surf.get_height()) // 2
            surf.blit(lbl_surf, (lx, ly))

    def _draw_hud(surf):
        """HUD-оверлей в правом нижнем углу."""
        ww, wh = surf.get_size()
        rtt = state.rtt
        lines = []

        # RTT с цветной точкой
        if rtt is not None:
            rtt_str = f"RTT: {rtt:.0f} ms"
            if rtt < 50:
                dot_col = COL_GREEN
            elif rtt < 150:
                dot_col = COL_YELLOW
            else:
                dot_col = COL_RED
        else:
            rtt_str = "RTT: —"
            dot_col = COL_DIM
        lines.append((rtt_str, dot_col))

        # Статус подключения
        conn_str = "WebRTC OK" if state.connected else "Connecting..."
        lines.append((conn_str, COL_GREEN if state.connected else COL_YELLOW))

        # Аудио
        audio_str = "Audio: muted" if state.mute else "Audio: active"
        lines.append((audio_str, COL_DIM))

        line_h = 18
        pad = 8
        max_w = 0
        rendered = []
        for text, _col in lines:
            s = mono_font.render(text, True, COL_WHITE)
            rendered.append((s, _col))
            max_w = max(max_w, s.get_width())

        box_w = max_w + pad * 2 + 14   # +14 для точки
        box_h = len(lines) * line_h + pad * 2
        bx = ww - box_w - 10
        by = wh - box_h - 10

        _rounded_rect(surf, (bx, by, box_w, box_h), COL_BG, 180, radius=6)

        for i, (s, dot_col) in enumerate(rendered):
            ty = by + pad + i * line_h
            pygame.draw.circle(surf, dot_col, (bx + pad + 4, ty + s.get_height() // 2), 3)
            surf.blit(s, (bx + pad + 14, ty))

    BTN = {1: "left", 2: "middle", 3: "right"}
    held = {}
    last_ping = 0.0
    surface = None
    last_id = None

    while state.alive:
        now = time.time()

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                state.alive = False
            elif event.type == pygame.VIDEORESIZE:
                win = pygame.display.set_mode(event.size, pygame.RESIZABLE)
            elif event.type == pygame.MOUSEMOTION:
                mx, my = event.pos
                # Показать тулбар при наведении на верхний край
                if my <= TRIGGER_ZONE:
                    toolbar_visible = True
                    toolbar_show_time = now
                elif toolbar_visible and my > TOOLBAR_H:
                    # Курсор ушёл ниже тулбара — запускаем таймер скрытия
                    pass
                # Пересылаем движение мыши (если не на тулбаре)
                if not (toolbar_visible and my <= TOOLBAR_H):
                    x, y = to_norm(event.pos)
                    send({"k": "move", "x": x, "y": y})
            elif event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                mx, my = event.pos
                # Клик по кнопке тулбара
                if (event.type == pygame.MOUSEBUTTONDOWN and event.button == 1
                        and toolbar_visible and my <= TOOLBAR_H):
                    rects = _toolbar_btn_rects(win.get_width())
                    for i, r in enumerate(rects):
                        if r.collidepoint(mx, my):
                            toolbar_buttons[i][1]()   # вызываем action
                            toolbar_show_time = now    # обновляем таймер
                            break
                    continue
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

        # Автоскрытие тулбара
        if toolbar_visible:
            mx, my = pygame.mouse.get_pos()
            if my > TOOLBAR_H and now - toolbar_show_time > HIDE_DELAY:
                toolbar_visible = False

        # PING раз в секунду
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

        # Тулбар (поверх кадра)
        if toolbar_visible:
            _draw_toolbar(win)

        # HUD (если включена статистика)
        if show_stats:
            _draw_hud(win)

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
    ap.add_argument("--mute", action="store_true", help="Отключить приём аудио")
    args = ap.parse_args()

    state = State(mute=args.mute)
    t = threading.Thread(target=_start_loop, args=(args, state), daemon=True)
    t.start()
    try:
        run_ui(args, state)
    finally:
        state.alive = False


if __name__ == "__main__":
    main()
