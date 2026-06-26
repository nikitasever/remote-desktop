"""
CLIENT — запускается на ВАШЕМ ПК. Показывает экран удалённого ПК в окне
и передаёт мышь/клавиатуру.

Режимы:
  Прямой:   python client.py --connect 192.168.1.50:5900 --password СЕКРЕТ
  Relay:    python client.py --relay vps.example.com:5800 --id myroom --password СЕКРЕТ

Управление:
  - Мышь/клавиатура транслируются на удалённый ПК, когда окно в фокусе.
  - Ctrl+Alt+Q  — выход.
"""

import argparse
import io
import os
import socket
import struct
import threading
import time

import pygame
from PIL import Image

import common

try:
    import video as video_mod
except Exception:
    video_mod = None

# Pygame-клавиша -> имя спец-клавиши, понятное host'у
PG_SPECIAL = {
    pygame.K_RETURN: "enter", pygame.K_ESCAPE: "esc", pygame.K_BACKSPACE: "backspace",
    pygame.K_TAB: "tab", pygame.K_SPACE: "space", pygame.K_DELETE: "delete",
    pygame.K_UP: "up", pygame.K_DOWN: "down", pygame.K_LEFT: "left", pygame.K_RIGHT: "right",
    pygame.K_HOME: "home", pygame.K_END: "end", pygame.K_PAGEUP: "page_up", pygame.K_PAGEDOWN: "page_down",
    pygame.K_LSHIFT: "shift", pygame.K_RSHIFT: "shift",
    pygame.K_LCTRL: "ctrl", pygame.K_RCTRL: "ctrl",
    pygame.K_LALT: "alt", pygame.K_RALT: "alt",
    pygame.K_CAPSLOCK: "caps_lock", pygame.K_INSERT: "insert",
    pygame.K_F1: "f1", pygame.K_F2: "f2", pygame.K_F3: "f3", pygame.K_F4: "f4",
    pygame.K_F5: "f5", pygame.K_F6: "f6", pygame.K_F7: "f7", pygame.K_F8: "f8",
    pygame.K_F9: "f9", pygame.K_F10: "f10", pygame.K_F11: "f11", pygame.K_F12: "f12",
}


def key_ident(event, mods):
    """Стабильная «личность» клавиши для отправки host'у: {"name":...} для
    спец-клавиш или {"char":...} для печатных. None — клавишу не шлём.

    Главное: при зажатых Ctrl/Alt pygame отдаёт в event.unicode управляющий
    символ (или пусто), поэтому Ctrl+C/V/A раньше не доходили. Здесь в этом
    случае берём базовый символ клавиши через pygame.key.name."""
    if event.key in PG_SPECIAL:
        return {"name": PG_SPECIAL[event.key]}
    uni = event.unicode
    if uni and uni.isprintable():
        return {"char": uni}          # обычный ввод, вкл. кириллицу и шифт-символы
    if mods & (pygame.KMOD_CTRL | pygame.KMOD_ALT):
        nm = pygame.key.name(event.key)
        if len(nm) == 1:              # буква/цифра — латинская база для комбинаций
            return {"char": nm}
    return None


class RemoteState:
    """Разделяемый surface удалённого экрана + потокобезопасный доступ."""
    def __init__(self):
        self.lock = threading.Lock()
        self.surface = None
        self.w = 0
        self.h = 0
        self.monitors = 1
        self.index = 1
        self.title_dirty = True
        self.alive = True
        self.video_mode = False   # True после MSG_VIDEO_INFO (H.264-поток)
        # статистика канала
        self.recv_frames = 0     # счётчик пришедших кадров (MSG_TILES)
        self.recv_bytes = 0      # байт плиток за интервал
        self.rtt_ms = None       # последний замер RTT
        self.fps = 0
        self.kbps = 0
        self.show_stats = True    # Ctrl+Alt+I — вкл/выкл


def reader_thread(sock, chan, state, clip):
    """Фоновый поток: принимает кадры и обновляет surface."""
    decoder = None
    try:
        while state.alive:
            mt, body = common.recv_frame(sock, chan)
            if mt == common.MSG_SCREEN_INFO:
                info = common.parse_json(body)
                with state.lock:
                    state.monitors = info.get("monitors", 1)
                    state.index = info.get("index", 1)
                    # surface создаём только в плиточном режиме (в видео он
                    # приходит из VIDEO_INFO/декодера)
                    if not state.video_mode:
                        state.w, state.h = info["w"], info["h"]
                        state.surface = pygame.Surface((state.w, state.h))
                    state.title_dirty = True
            elif mt == common.MSG_VIDEO_INFO:
                info = common.parse_json(body)
                try:
                    decoder = video_mod.VideoDecoder() if video_mod else None
                except Exception as e:
                    print(f"[client] не удалось создать декодер: {e}")
                    decoder = None
                with state.lock:
                    state.video_mode = True
                    state.w, state.h = info["w"], info["h"]
                    state.title_dirty = True
                print(f"[client] видео-поток: {info.get('codec')} "
                      f"{info.get('w')}x{info.get('h')} @ {info.get('fps')}к/с")
            elif mt == common.MSG_VIDEO:
                if decoder is not None and len(body) >= 1:
                    try:
                        for rgb in decoder.decode(body[1:]):  # body[0] = флаг keyframe
                            surf = pygame.image.frombuffer(
                                rgb.tobytes(), (rgb.shape[1], rgb.shape[0]), "RGB")
                            with state.lock:
                                state.surface = surf
                                state.recv_frames += 1
                                state.recv_bytes += len(body)
                    except Exception as e:
                        print(f"[client] ошибка декода: {e}")
            elif mt == common.MSG_TILES:
                apply_tiles(body, state)
                with state.lock:
                    state.recv_frames += 1
                    state.recv_bytes += len(body)
            elif mt == common.MSG_PONG:
                t0 = common.parse_json(body).get("t", 0)
                with state.lock:
                    state.rtt_ms = (time.time() - t0) * 1000.0
            elif mt == common.MSG_CLIPBOARD:
                clip.on_remote(common.parse_json(body).get("text", ""))
    except (ConnectionError, socket.error) as e:
        print(f"[client] соединение закрыто: {e}")
    finally:
        state.alive = False


def apply_tiles(body, state):
    (ntiles,) = struct.unpack(">H", body[:2])
    off = 2
    with state.lock:
        surf = state.surface
        if surf is None:
            return
        for _ in range(ntiles):
            x, y, ln = struct.unpack(">HHI", body[off:off + 8])
            off += 8
            jpg = body[off:off + ln]
            off += ln
            img = Image.open(io.BytesIO(jpg)).convert("RGB")
            tile_surf = pygame.image.fromstring(img.tobytes(), img.size, "RGB")
            surf.blit(tile_surf, (x, y))


def send_file_dialog(sender, state):
    """Открывает диалог выбора файла и отправляет его на удалённый ПК."""
    def worker():
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            path = filedialog.askopenfilename(title="Файл для отправки на удалённый ПК")
            root.destroy()
        except Exception as e:
            print(f"[client] диалог файла недоступен: {e}")
            return
        if not path:
            return
        try:
            size = os.path.getsize(path)
            sender.send_json(common.MSG_FILE_META, {"name": os.path.basename(path), "size": size})
            sent = 0
            with open(path, "rb") as f:
                while state.alive:
                    chunk = f.read(256 * 1024)
                    if not chunk:
                        break
                    sender.send(common.MSG_FILE_CHUNK, chunk)
                    sent += len(chunk)
            sender.send(common.MSG_FILE_END)
            print(f"[client] отправлено {sent} байт: {os.path.basename(path)}")
        except (ConnectionError, socket.error, OSError) as e:
            print(f"[client] ошибка отправки файла: {e}")

    threading.Thread(target=worker, daemon=True).start()


def ping_loop(sender, state):
    """Раз в секунду шлёт PING для замера RTT."""
    while state.alive:
        try:
            sender.send_json(common.MSG_PING, {"t": time.time()})
        except (ConnectionError, socket.error):
            return
        time.sleep(1.0)


def run_ui(sender, state, clip):
    pygame.init()
    pygame.display.set_caption("Remote Desktop — подключение...")
    win = pygame.display.set_mode((960, 600), pygame.RESIZABLE)
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("Consolas", 16)

    threading.Thread(target=ping_loop, args=(sender, state), daemon=True).start()
    last_stat = time.time()

    # Ждём информацию об экране
    while state.alive and state.surface is None:
        clock.tick(30)
    if not state.alive:
        return

    def send_input(ev):
        try:
            sender.send_json(common.MSG_INPUT, ev)
        except (ConnectionError, socket.error):
            state.alive = False

    def to_norm(pos):
        ww, wh = win.get_size()
        return (max(0.0, min(1.0, pos[0] / ww)), max(0.0, min(1.0, pos[1] / wh)))

    BTN = {1: "left", 2: "middle", 3: "right"}
    held = {}   # pygame key -> ident: отслеживаем нажатые, чтобы слать и kup

    def release_all():
        for ident in list(held.values()):
            send_input({"k": "kup", **ident})
        held.clear()

    while state.alive:
        if state.title_dirty:
            with state.lock:
                state.title_dirty = False
                cap = (f"Remote Desktop {state.w}x{state.h}  монитор {state.index}/{state.monitors}"
                       f"   (Ctrl+Alt: Q-выход  M-монитор  S-файл  I-статистика)")
            pygame.display.set_caption(cap)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                state.alive = False
            elif event.type == pygame.VIDEORESIZE:
                win = pygame.display.set_mode(event.size, pygame.RESIZABLE)
            elif event.type == pygame.MOUSEMOTION:
                x, y = to_norm(event.pos)
                send_input({"k": "move", "x": x, "y": y})
            elif event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                if event.button in (4, 5):  # колесо
                    if event.type == pygame.MOUSEBUTTONDOWN:
                        send_input({"k": "scroll", "dx": 0, "dy": 1 if event.button == 4 else -1})
                else:
                    x, y = to_norm(event.pos)
                    kind = "down" if event.type == pygame.MOUSEBUTTONDOWN else "up"
                    send_input({"k": kind, "x": x, "y": y, "btn": BTN.get(event.button, "left")})
            elif event.type in (pygame.KEYDOWN, pygame.KEYUP):
                mods = pygame.key.get_mods()
                hotkey = (mods & pygame.KMOD_CTRL) and (mods & pygame.KMOD_ALT)
                if event.type == pygame.KEYDOWN and hotkey and event.key == pygame.K_q:
                    state.alive = False
                    break
                if event.type == pygame.KEYDOWN and hotkey and event.key == pygame.K_m:
                    with state.lock:
                        nxt = state.index % max(1, state.monitors) + 1
                    sender.send_json(common.MSG_SET_MONITOR, {"index": nxt})
                    continue
                if event.type == pygame.KEYDOWN and hotkey and event.key == pygame.K_s:
                    send_file_dialog(sender, state)
                    continue
                if event.type == pygame.KEYDOWN and hotkey and event.key == pygame.K_i:
                    state.show_stats = not state.show_stats
                    continue
                if event.type == pygame.KEYDOWN:
                    ident = key_ident(event, mods)
                    if ident is not None:
                        held[event.key] = ident
                        send_input({"k": "kdown", **ident})
                else:  # KEYUP — шлём release для ранее нажатой клавиши
                    ident = held.pop(event.key, None)
                    if ident is None and event.key in PG_SPECIAL:
                        ident = {"name": PG_SPECIAL[event.key]}
                    if ident is not None:
                        send_input({"k": "kup", **ident})
            elif event.type == getattr(pygame, "WINDOWFOCUSLOST", -1):
                release_all()   # окно потеряло фокус — отпускаем всё, чтобы не залипало

        # Пересчёт статистики раз в секунду
        now = time.time()
        if now - last_stat >= 1.0:
            dt = now - last_stat
            with state.lock:
                state.fps = round(state.recv_frames / dt, 1)
                state.kbps = round(state.recv_bytes / 1024 / dt, 1)
                state.recv_frames = 0
                state.recv_bytes = 0
            last_stat = now

        # Рендер
        with state.lock:
            if state.surface is not None:
                if state.surface.get_size() == win.get_size():
                    win.blit(state.surface, (0, 0))          # 1:1 — без масштабирования
                else:
                    win.blit(pygame.transform.smoothscale(state.surface, win.get_size()), (0, 0))
            show = state.show_stats
            fps, kbps, rtt = state.fps, state.kbps, state.rtt_ms

        if show:
            rtt_txt = f"{rtt:.0f} ms" if rtt is not None else "—"
            line = f" FPS: {fps}   RTT: {rtt_txt}   {kbps} KB/s "
            txt = font.render(line, True, (255, 255, 255))
            bg = pygame.Surface((txt.get_width(), txt.get_height()))
            bg.set_alpha(150)
            bg.fill((0, 0, 0))
            win.blit(bg, (6, 6))
            win.blit(txt, (6, 6))

        pygame.display.flip()
        clock.tick(30)

    pygame.quit()


def make_socket(args):
    if args.relay:
        host, port = args.relay.rsplit(":", 1)
        s = socket.create_connection((host, int(port)))
        common.relay_register(s, "client", args.id)
        print(f"[client] подключаюсь через relay {args.relay}, комната '{args.id}'")
        line = common.relay_read_line(s)
        print(f"[client] relay: {line}")
        return s
    else:
        host, port = args.connect.rsplit(":", 1)
        s = socket.create_connection((host, int(port)))
        print(f"[client] подключился к {args.connect}")
        return s


def run_client(args):
    """Подключение + окно. Вызывается из CLI и из GUI (в главном потоке)."""
    key = common.derive_key(args.password)
    sock = make_socket(args)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    common.enable_keepalive(sock)
    chan = common.SecureChannel(key)

    # HELLO — host проверит пароль (расшифровка) и узнает, умеем ли мы видео.
    import json as _json
    common.send_frame(sock, chan, common.MSG_HELLO,
                      _json.dumps({"video": video_mod is not None}).encode("utf-8"))

    sender = common.FrameSender(sock, chan)
    clip = common.ClipboardSync(lambda txt: sender.send_json(common.MSG_CLIPBOARD, {"text": txt}))
    clip.start()

    state = RemoteState()
    t = threading.Thread(target=reader_thread, args=(sock, chan, state, clip), daemon=True)
    t.start()
    try:
        run_ui(sender, state, clip)
    finally:
        state.alive = False
        clip.stop()
        sock.close()


def main():
    ap = argparse.ArgumentParser(description="Remote desktop CLIENT (просмотрщик)")
    ap.add_argument("--connect", help="Прямой режим: host:порт")
    ap.add_argument("--relay", help="Режим relay: vps:порт")
    ap.add_argument("--id", default="default", help="ID комнаты для relay")
    ap.add_argument("--password", required=True, help="Общий пароль (E2E)")
    args = ap.parse_args()
    if not args.connect and not args.relay:
        ap.error("укажите --connect host:порт или --relay vps:порт")
    run_client(args)


if __name__ == "__main__":
    main()
