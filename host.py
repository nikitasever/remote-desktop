"""
HOST — запускается на ПК, которым нужно управлять (удалённый/рабочий).

Захватывает экран, отправляет client'у только изменившиеся плитки,
принимает события мыши/клавиатуры и воспроизводит их локально.

Режимы подключения:
  1) Прямой (LAN/VPN/Tailscale/проброс портов):
        python host.py --listen 5900 --password СЕКРЕТ
  2) Через relay (вы за NAT, есть VPS с relay.py):
        python host.py --relay vps.example.com:5800 --id myroom --password СЕКРЕТ
"""

import argparse
import io
import os
import socket
import threading
import time

import numpy as np
import mss as mss_module
from PIL import Image
from pynput.mouse import Controller as MouseController, Button
from pynput.keyboard import Controller as KeyboardController, Key, KeyCode

import common

try:
    import video as video_mod
except Exception as _e:          # PyAV не установлен — видео-путь отключён
    video_mod = None

try:
    import dxcam as _dxcam       # GPU-захват (DXGI); фолбэк на mss ниже
except Exception:
    _dxcam = None

TILE = 128            # размер плитки в пикселях
# Значения по умолчанию (можно переопределить из GUI/CLI)
JPEG_QUALITY = 65     # качество JPEG для плиток (30..95), 4:4:4
TARGET_FPS = 20       # верхний предел частоты кадров
SCALE = 1.0           # масштаб передаваемого экрана (1.0 / 0.75 / 0.5)
CODEC = "auto"        # формат плиток: "auto" | "jpeg" | "png"

# Логгер можно переопределить из GUI (host.LOG = my_func)
LOG = print


# ---- Карта спец-клавиш: имя (от клиента) -> объект pynput ----
SPECIAL_KEYS = {
    "enter": Key.enter, "esc": Key.esc, "backspace": Key.backspace,
    "tab": Key.tab, "space": Key.space, "delete": Key.delete,
    "up": Key.up, "down": Key.down, "left": Key.left, "right": Key.right,
    "home": Key.home, "end": Key.end, "page_up": Key.page_up, "page_down": Key.page_down,
    "shift": Key.shift, "ctrl": Key.ctrl, "alt": Key.alt, "cmd": Key.cmd,
    "caps_lock": Key.caps_lock, "insert": Key.insert,
    "f1": Key.f1, "f2": Key.f2, "f3": Key.f3, "f4": Key.f4, "f5": Key.f5,
    "f6": Key.f6, "f7": Key.f7, "f8": Key.f8, "f9": Key.f9, "f10": Key.f10,
    "f11": Key.f11, "f12": Key.f12,
}


class InputInjector:
    def __init__(self, screen_w, screen_h):
        self.mouse = MouseController()
        self.kb = KeyboardController()
        self.w = screen_w
        self.h = screen_h

    def _key_from_event(self, ev):
        if "char" in ev and ev["char"]:
            return KeyCode.from_char(ev["char"])
        return SPECIAL_KEYS.get(ev.get("name"))

    def handle(self, ev):
        kind = ev.get("k")
        try:
            if kind == "move":
                self.mouse.position = (int(ev["x"] * self.w), int(ev["y"] * self.h))
            elif kind in ("down", "up"):
                self.mouse.position = (int(ev["x"] * self.w), int(ev["y"] * self.h))
                btn = {"left": Button.left, "right": Button.right, "middle": Button.middle}.get(ev["btn"])
                if btn:
                    (self.mouse.press if kind == "down" else self.mouse.release)(btn)
            elif kind == "scroll":
                self.mouse.scroll(ev.get("dx", 0), ev.get("dy", 0))
            elif kind in ("kdown", "kup"):
                key = self._key_from_event(ev)
                if key is not None:
                    (self.kb.press if kind == "kdown" else self.kb.release)(key)
        except Exception as e:
            LOG(f"[host] ошибка ввода: {e}")


class ScreenStreamer:
    """Захват экрана, масштабирование и формирование изменившихся плиток.

    real_w/real_h — реальный размер монитора (для маппинга ввода).
    w/h           — размер передаваемого экрана (real * scale).
    """

    def __init__(self, index=1, scale=1.0, quality=JPEG_QUALITY, codec=CODEC):
        self.sct = mss_module.MSS()
        # monitors[0] — все экраны вместе; monitors[1:] — по одному.
        self.count = len(self.sct.monitors) - 1
        self.scale = scale
        self.quality = int(quality)
        self.codec = codec
        self._dx = None          # активная dxcam-камера (или None -> mss)
        self.set_monitor(index)

    def _open_dxcam(self, index):
        """Открывает dxcam для монитора index (1-based). None при неудаче -> mss."""
        if _dxcam is None:
            return None
        if self._dx is not None:
            try:
                self._dx.release()
            except Exception:
                pass
            self._dx = None
        try:
            # index 1 -> первичный выход (output_idx=None); прочие -> index-1
            out_idx = None if index <= 1 else index - 1
            cam = _dxcam.create(output_idx=out_idx, output_color="RGB",
                                processor_backend="numpy")
            return cam
        except Exception as e:
            LOG(f"[host] dxcam недоступен для монитора {index} ({e}); захват через mss")
            return None

    def set_monitor(self, index):
        index = max(1, min(self.count, index))
        self.index = index
        self.mon = self.sct.monitors[index]
        self.real_w = self.mon["width"]
        self.real_h = self.mon["height"]
        self.w = max(1, int(round(self.real_w * self.scale)))
        self.h = max(1, int(round(self.real_h * self.scale)))
        self._prev_hashes = {}   # сброс кэша — отдадим монитор целиком
        self._last_full = None   # хэш всего кадра для пропуска статики
        self._dx = self._open_dxcam(index)
        LOG(f"[host] захват: {'dxcam (DXGI)' if self._dx else 'mss'} "
            f"монитор {self.index}/{self.count} {self.real_w}x{self.real_h}")

    def capture(self):
        """Кадр (numpy RGB) в потоковом размере, либо None если экран не менялся."""
        if self._dx is not None:
            arr = self._dx.grab()        # None, если кадр не изменился (DXGI сам так умеет)
            if arr is None:
                return None
        else:
            raw = self.sct.grab(self.mon)
            arr = np.frombuffer(raw.rgb, dtype=np.uint8).reshape(raw.height, raw.width, 3)
            # быстрый детект статики по прорежённому кадру (каждый 4-й пиксель)
            fh = hash(arr[::4, ::4].tobytes())
            if fh == self._last_full:
                return None              # ничего не изменилось — не тратим CPU/сеть
            self._last_full = fh
        if self.scale != 1.0:
            img = Image.fromarray(arr).resize((self.w, self.h), Image.BILINEAR)
            arr = np.asarray(img)
        return arr

    def close(self):
        if self._dx is not None:
            try:
                self._dx.release()
            except Exception:
                pass
            self._dx = None

    @staticmethod
    def _is_flat(tile):
        """«Плоская» плитка (текст/интерфейс, мало цветов) — лучше идёт в PNG."""
        return np.count_nonzero(np.bincount(tile[::2, ::2].ravel(), minlength=256)) <= 40

    def _encode_tile(self, tile):
        """Кодирует плитку. PNG (без потерь) для плоских, JPEG для фото/градиентов.
        Клиент определяет формат по байтам сам — менять его не нужно."""
        buf = io.BytesIO()
        use_png = self.codec == "png" or (self.codec == "auto" and self._is_flat(tile))
        if use_png:
            Image.fromarray(tile).save(buf, format="PNG", compress_level=1)
        else:
            # subsampling=0 (4:4:4): полная цветность — резкий текст
            Image.fromarray(tile).save(buf, format="JPEG",
                                       quality=self.quality, subsampling=0)
        return buf.getvalue()

    def dirty_tiles(self, frame: np.ndarray):
        """Возвращает список (x, y, bytes) для изменившихся плиток."""
        out = []
        h, w, _ = frame.shape
        for ty in range(0, h, TILE):
            for tx in range(0, w, TILE):
                tile = frame[ty:ty + TILE, tx:tx + TILE]
                hkey = (tx, ty)
                # детект по прорежённой плитке (каждый 3-й пиксель) — быстрее
                hval = hash(tile[::3, ::3].tobytes())
                if self._prev_hashes.get(hkey) == hval:
                    continue
                self._prev_hashes[hkey] = hval
                out.append((tx, ty, self._encode_tile(tile)))
        return out


def pack_tiles(tiles):
    """[2 байта: кол-во] + на плитку: x(2) y(2) len(4) jpeg."""
    import struct
    parts = [struct.pack(">H", len(tiles))]
    for x, y, jpg in tiles:
        parts.append(struct.pack(">HHI", x, y, len(jpg)))
        parts.append(jpg)
    return b"".join(parts)


def screen_info(streamer):
    return {"w": streamer.w, "h": streamer.h,
            "monitors": streamer.count, "index": streamer.index}


def serve(sock, key, downloads_dir, quality=JPEG_QUALITY, fps=TARGET_FPS, scale=SCALE,
          codec=CODEC, engine="auto"):
    chan = common.SecureChannel(key)

    # Рукопожатие: клиент должен прислать корректно зашифрованный HELLO.
    msg_type, hello_body = common.recv_frame(sock, chan)  # упадёт, если пароль неверный
    if msg_type != common.MSG_HELLO:
        raise ConnectionError("Ожидался HELLO")
    # Новый клиент шлёт JSON {"video": true}; старый — b"hi" (тогда видео off).
    client_video = False
    try:
        client_video = bool(common.parse_json(hello_body).get("video"))
    except Exception:
        client_video = False
    # engine: "auto"/"x264" -> видео (если клиент умеет и PyAV есть); "tiles" -> плитки.
    use_video = client_video and video_mod is not None and engine != "tiles"
    LOG(f"[host] клиент аутентифицирован (движок={'H.264' if use_video else 'плитки'}, "
        f"FPS={fps}, качество={quality}, масштаб={scale})")

    sender = common.FrameSender(sock, chan)
    streamer = ScreenStreamer(scale=scale, quality=quality, codec=codec)
    injector = InputInjector(streamer.real_w, streamer.real_h)
    sender.send_json(common.MSG_SCREEN_INFO, screen_info(streamer))

    clip = common.ClipboardSync(lambda txt: sender.send_json(common.MSG_CLIPBOARD, {"text": txt}))
    clip.start()

    alive = {"v": True}
    pending_monitor = {"v": None}
    incoming_file = {"f": None, "name": None}

    # Приём команд — в отдельном потоке (блокирующее чтение, без частичных кадров).
    def recv_loop():
        try:
            while alive["v"]:
                mt, body = common.recv_frame(sock, chan)
                if mt == common.MSG_INPUT:
                    injector.handle(common.parse_json(body))
                elif mt == common.MSG_PING:
                    sender.send(common.MSG_PONG, body)  # отражаем как есть
                elif mt == common.MSG_CLIPBOARD:
                    clip.on_remote(common.parse_json(body).get("text", ""))
                elif mt == common.MSG_SET_MONITOR:
                    pending_monitor["v"] = int(common.parse_json(body).get("index", 1))
                elif mt == common.MSG_FILE_META:
                    meta = common.parse_json(body)
                    os.makedirs(downloads_dir, exist_ok=True)
                    safe = os.path.basename(meta["name"]) or "file.bin"
                    path = os.path.join(downloads_dir, safe)
                    incoming_file["f"] = open(path, "wb")
                    incoming_file["name"] = path
                    LOG(f"[host] приём файла: {path} ({meta.get('size', '?')} байт)")
                elif mt == common.MSG_FILE_CHUNK:
                    if incoming_file["f"]:
                        incoming_file["f"].write(body)
                elif mt == common.MSG_FILE_END:
                    if incoming_file["f"]:
                        incoming_file["f"].close()
                        LOG(f"[host] файл сохранён: {incoming_file['name']}")
                        incoming_file["f"] = None
        except (ConnectionError, socket.error):
            pass
        finally:
            alive["v"] = False
            if incoming_file["f"]:
                incoming_file["f"].close()

    threading.Thread(target=recv_loop, daemon=True).start()

    # Отправка кадров — в основном потоке, с ограничением FPS.
    frame_interval = 1.0 / max(1, fps)
    encoder = None

    def _new_encoder():
        prefer = "libx264" if engine == "x264" else "auto"
        enc = video_mod.VideoEncoder(
            streamer.w, streamer.h, fps=fps,
            bitrate=video_mod.quality_to_bitrate(quality, streamer.w, streamer.h),
            prefer=prefer)
        sender.send_json(common.MSG_VIDEO_INFO,
                         {"codec": enc.name, "w": enc.width, "h": enc.height, "fps": fps})
        LOG(f"[host] видео-поток: {enc.name} {enc.width}x{enc.height} @ {fps}к/с "
            f"~{enc.bitrate // 1000} кбит/с")
        return enc

    try:
        if use_video:
            encoder = _new_encoder()
        while alive["v"]:
            t0 = time.time()
            if pending_monitor["v"] is not None:
                streamer.set_monitor(pending_monitor["v"])
                injector.w, injector.h = streamer.real_w, streamer.real_h
                pending_monitor["v"] = None
                sender.send_json(common.MSG_SCREEN_INFO, screen_info(streamer))
                LOG(f"[host] переключение на монитор {streamer.index}/{streamer.count}")
                if use_video:
                    if encoder:
                        encoder.close()
                    encoder = _new_encoder()   # размер мог измениться -> новый энкодер
            frame = streamer.capture()
            if frame is not None:
                if use_video:
                    for data, is_key in encoder.encode(frame):
                        sender.send(common.MSG_VIDEO, bytes([1 if is_key else 0]) + data)
                else:
                    tiles = streamer.dirty_tiles(frame)
                    if tiles:
                        sender.send(common.MSG_TILES, pack_tiles(tiles))
            dt = time.time() - t0
            if dt < frame_interval:
                time.sleep(frame_interval - dt)
    finally:
        alive["v"] = False
        clip.stop()
        if encoder:
            encoder.close()
        streamer.close()


def make_socket(args):
    """Только relay-режим: подключение к relay и ожидание клиента.
    Direct-режим обслуживается отдельно в _run_direct (нужен преемптивный
    accept-цикл, чтобы новый клиент вытеснял зависшую сессию)."""
    host, port = args.relay.rsplit(":", 1)
    s = socket.create_connection((host, int(port)))
    common.enable_keepalive(s)
    common.relay_register(s, "host", args.id)
    LOG(f"[host] зарегистрирован на relay {args.relay}, комната '{args.id}', жду клиента...")
    # Relay пришлёт строку, когда клиент подключится.
    line = common.relay_read_line(s)
    LOG(f"[host] relay: {line}")
    return s


def _run_relay(args, key, params, stop_event):
    """Relay-режим: реконнект-цикл. Вытеснение старой роли делает сам relay
    (см. relay.py), а keepalive ускоряет обнаружение мёртвой стороны."""
    while not (stop_event and stop_event.is_set()):
        try:
            sock = make_socket(args)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            serve(sock, key, args.downloads, **params)
        except (ConnectionError, socket.error) as e:
            LOG(f"[host] соединение разорвано: {e}. Переподключение через 2с...")
            time.sleep(2)
        except KeyboardInterrupt:
            LOG("\n[host] выход")
            return


def _run_direct(args, key, params, stop_event):
    """Direct-режим (--listen): принимаем подключения в цикле. Новый клиент
    ВЫТЕСНЯЕТ текущую сессию — закрываем её сокет, serve() из-за этого выходит.
    Так зависшее (полу-мёртвое) подключение не блокирует повторный вход."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", args.listen))
    srv.listen(8)            # backlog >1: новый клиент не ждёт в очереди за старым
    srv.settimeout(1.0)      # периодически просыпаемся, чтобы проверить stop_event
    LOG(f"[host] слушаю порт {args.listen}, жду клиента...")

    cur = {"sock": None, "thread": None}

    def _session(conn, addr):
        try:
            serve(conn, key, args.downloads, **params)
        except (ConnectionError, socket.error) as e:
            LOG(f"[host] сессия {addr} завершена: {e}")
        except Exception as e:
            LOG(f"[host] сессия {addr} ошибка: {e}")
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _evict():
        old_t, old_s = cur["thread"], cur["sock"]
        if old_t is not None and old_t.is_alive():
            LOG("[host] закрываю предыдущую сессию")
            try:
                old_s.close()      # рвём сокет -> recv/send в serve падают -> выход
            except OSError:
                pass
            old_t.join(timeout=5)

    try:
        while not (stop_event and stop_event.is_set()):
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            common.enable_keepalive(conn)
            _evict()               # вытесняем старую сессию перед запуском новой
            cur["sock"] = conn
            cur["thread"] = threading.Thread(target=_session, args=(conn, addr), daemon=True)
            cur["thread"].start()
            LOG(f"[host] подключился {addr}")
    except KeyboardInterrupt:
        LOG("\n[host] выход")
    finally:
        _evict()
        try:
            srv.close()
        except OSError:
            pass


def run_host(args, stop_event=None):
    """Цикл хоста. Вызывается из CLI и из GUI."""
    key = common.derive_key(args.password)
    params = dict(
        quality=getattr(args, "quality", JPEG_QUALITY),
        fps=getattr(args, "fps", TARGET_FPS),
        scale=getattr(args, "scale", SCALE),
        codec=getattr(args, "codec", CODEC),
        engine=getattr(args, "engine", "auto"),
    )
    LOG(f"[host] принятые файлы будут сохраняться в: {args.downloads}")
    if args.relay:
        _run_relay(args, key, params, stop_event)
    else:
        _run_direct(args, key, params, stop_event)


def main():
    ap = argparse.ArgumentParser(description="Remote desktop HOST (управляемый ПК)")
    ap.add_argument("--listen", type=int, help="Прямой режим: слушать порт")
    ap.add_argument("--relay", help="Режим relay: адрес vps:порт")
    ap.add_argument("--id", default="default", help="ID комнаты для relay")
    ap.add_argument("--password", required=True, help="Общий пароль (E2E)")
    ap.add_argument("--downloads", default=os.path.join(os.path.expanduser("~"), "RemoteDesktop_received"),
                    help="Папка для принятых файлов")
    ap.add_argument("--quality", type=int, default=JPEG_QUALITY, help="Качество JPEG 30..90")
    ap.add_argument("--fps", type=int, default=TARGET_FPS, help="Предел кадров/с")
    ap.add_argument("--scale", type=float, default=SCALE, help="Масштаб экрана 1.0/0.75/0.5")
    ap.add_argument("--codec", default=CODEC, choices=["auto", "jpeg", "png"], help="Формат плиток (фолбэк)")
    ap.add_argument("--engine", default="auto", choices=["auto", "x264", "tiles"],
                    help="Движок: auto/x264 — видео H.264; tiles — старые плитки")
    args = ap.parse_args()
    if not args.listen and not args.relay:
        ap.error("укажите --listen ПОРТ или --relay vps:порт")
    run_host(args)


if __name__ == "__main__":
    main()
