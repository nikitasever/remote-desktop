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
import logging
import os
import socket
import struct
import threading
import time

import pygame
from PIL import Image

import common

log = logging.getLogger(__name__)

try:
    import video as video_mod
except Exception:
    video_mod = None

try:
    import audio as audio_mod
except Exception:
    audio_mod = None

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


DEFAULT_DOWNLOADS = os.path.join(os.path.expanduser("~"), "RemoteDesktop_received")

# ---- Стабильный per-install ID клиента (для контроля доступа на host'е) ----
import json as _json_mod
import random as _random_mod

CLIENT_CONFIG_DIR = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")), "RemoteDesktop"
)
CLIENT_ID_FILE = os.path.join(CLIENT_CONFIG_DIR, "client_id.json")


def get_or_create_client_id():
    """Постоянный 9-значный ID этого клиента (генерируется при первом запуске).
    Используется host'ом для назначения роли control/view/blocked."""
    try:
        with open(CLIENT_ID_FILE, "r", encoding="utf-8") as f:
            data = _json_mod.load(f)
            cid = data.get("id")
            if isinstance(cid, str) and len(cid) == 9 and cid.isdigit():
                return cid
    except (FileNotFoundError, KeyError, ValueError, OSError):
        pass
    new_id = str(_random_mod.randint(100_000_000, 999_999_999))
    try:
        os.makedirs(CLIENT_CONFIG_DIR, exist_ok=True)
        with open(CLIENT_ID_FILE, "w", encoding="utf-8") as f:
            _json_mod.dump({"id": new_id}, f)
    except OSError:
        pass
    return new_id


def get_client_name():
    """Человеко-читаемое имя этого ПК (для диалога подтверждения на host'е)."""
    try:
        return os.environ.get("COMPUTERNAME") or socket.gethostname()
    except Exception:
        return "ПК"


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
        # Контроль доступа: эффективная роль, назначенная host'ом (MSG_ACCESS).
        # "control" — полный контроль; "view" — только просмотр (не шлём ввод).
        self.role = "control"
        self.access_reason = ""
        self.status = "Подключение к хосту…"  # диагностика для чёрного экрана
        self.decode_err = None    # последняя ошибка декодера (показать на экране)
        self.decoder_name = None  # реально задействованный декодер
        self.decoder_hw = False   # True если декодер аппаратный
        # статистика канала
        self.recv_frames = 0     # счётчик пришедших кадров (MSG_TILES)
        self.recv_bytes = 0      # байт плиток за интервал
        self.rtt_ms = None       # последний замер RTT
        self.rtt_time = 0.0      # время последнего PONG (для обнаружения stale RTT)
        self.fps = 0
        self.kbps = 0
        self.show_stats = True    # Ctrl+Alt+I — вкл/выкл
        # Remote file browser state
        self.dir_listing = None   # последний ответ от host (dict)
        self.downloads_dir = DEFAULT_DOWNLOADS  # куда складывать принятые файлы


def reader_thread(sock, chan, state, clip, sender=None):
    """Фоновый поток: принимает кадры и обновляет surface."""
    decoder = None
    incoming_file = {"f": None, "name": None}
    audio_player = None
    try:
        while state.alive:
            try:
                mt, body = common.recv_frame(sock, chan)
            except (ConnectionError, socket.error):
                raise
            except Exception as e:
                print(f"[client] ошибка чтения кадра: {e}")
                continue
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
                if video_mod is None:
                    with state.lock:
                        state.decode_err = "Модуль video (PyAV) недоступен в сборке"
                    print("[client] video_mod=None — нет PyAV, видео не декодируется")
                try:
                    # Режим декодера из настроек: auto / hw / sw (по умолчанию auto).
                    # Декодер сам валидирует HW и тихо откатывается на софт.
                    _dec_pref = "auto"
                    try:
                        from settings_config import config as _scfg
                        _dec_pref = _scfg.get("hw_decoder", "auto")
                    except Exception:
                        _dec_pref = "auto"
                    decoder = video_mod.VideoDecoder(prefer=_dec_pref) if video_mod else None
                    if decoder is not None:
                        _hw = "HW" if decoder.is_hardware else "CPU"
                        print(f"[client] декодер: {decoder.active_decoder_name} ({_hw})")
                        with state.lock:
                            state.decoder_name = decoder.active_decoder_name
                            state.decoder_hw = decoder.is_hardware
                except Exception as e:
                    print(f"[client] не удалось создать декодер: {e}")
                    decoder = None
                    with state.lock:
                        state.decode_err = f"Не создать декодер: {e}"
                with state.lock:
                    state.video_mode = True
                    state.w, state.h = info["w"], info["h"]
                    state.title_dirty = True
                    state.status = f"Жду первый кадр ({info.get('codec')} {info.get('w')}x{info.get('h')})…"
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
                                state.decode_err = None
                    except Exception as e:
                        print(f"[client] ошибка декода: {e}")
                        with state.lock:
                            state.decode_err = f"Ошибка декода: {e}"
            elif mt == common.MSG_TILES:
                apply_tiles(body, state)
                with state.lock:
                    state.recv_frames += 1
                    state.recv_bytes += len(body)
            elif mt == common.MSG_PONG:
                t0 = common.parse_json(body).get("t", 0)
                with state.lock:
                    state.rtt_ms = (time.time() - t0) * 1000.0
                    state.rtt_time = time.time()
            elif mt == common.MSG_AUDIO_INFO:
                if audio_mod is not None and audio_player is None:
                    try:
                        audio_player = audio_mod.AudioPlayer()
                        if getattr(audio_player, "available", False):
                            print("[client] аудио-поток от host: воспроизведение включено")
                        else:
                            print("[client] аудио от host недоступно для воспроизведения")
                    except Exception as e:
                        print(f"[client] не удалось запустить воспроизведение звука: {e}")
                        audio_player = None
            elif mt == common.MSG_AUDIO:
                if audio_player is not None:
                    audio_player.feed(body)
            elif mt == common.MSG_ACCESS:
                try:
                    info = common.parse_json(body)
                    with state.lock:
                        state.role = info.get("role", "control")
                        state.access_reason = info.get("reason", "")
                        state.title_dirty = True
                    print(f"[client] роль доступа: {state.role}"
                          + (f" ({state.access_reason})" if state.access_reason else ""))
                except Exception as e:
                    print(f"[client] ошибка MSG_ACCESS: {e}")
            elif mt == common.MSG_CLIPBOARD:
                clip.on_remote(common.parse_json(body).get("text", ""))
            elif mt == common.MSG_CLIPBOARD_IMAGE:
                clip.on_remote_image(body)
            elif mt == common.MSG_HOST_FILE_META:
                meta = common.parse_json(body)
                dl = state.downloads_dir
                os.makedirs(dl, exist_ok=True)
                safe = os.path.basename(meta["name"]) or "file.bin"
                path = os.path.join(dl, safe)
                incoming_file["f"] = open(path, "wb")
                incoming_file["name"] = path
                print(f"[client] приём файла от host: {path} ({meta.get('size', '?')} байт)")
            elif mt == common.MSG_HOST_FILE_CHUNK:
                if incoming_file["f"]:
                    incoming_file["f"].write(body)
                else:
                    print(f"[client] FILE_CHUNK без открытого файла, {len(body)} байт потеряно")
            elif mt == common.MSG_HOST_FILE_END:
                if incoming_file["f"]:
                    incoming_file["f"].close()
                    print(f"[client] файл от host сохранён: {incoming_file['name']}")
                    incoming_file["f"] = None
                else:
                    print("[client] FILE_END без открытого файла")
            elif mt == common.MSG_DIR_LIST_RESP:
                resp = common.parse_json(body)
                with state.lock:
                    state.dir_listing = resp
                err = resp.get("error")
                if err:
                    print(f"[client] ошибка листинга: {err}")
                else:
                    print(f"[client] листинг '{resp.get('path', '')}': {len(resp.get('entries', []))} записей")
                    for e in resp.get("entries", []):
                        kind = "DIR " if e.get("is_dir") else "FILE"
                        sz = e.get("size", 0)
                        print(f"  {kind} {e['name']}" + (f"  ({sz} B)" if not e.get("is_dir") else ""))
    except (ConnectionError, socket.error) as e:
        print(f"[client] соединение закрыто: {e}")
    except Exception as e:
        print(f"[client] reader_thread неожиданная ошибка: {e}")
        import traceback; traceback.print_exc()
    finally:
        state.alive = False
        if incoming_file["f"]:
            try:
                incoming_file["f"].close()
            except OSError:
                pass
        if audio_player is not None:
            try:
                audio_player.stop()
            except Exception:
                pass


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


def send_file_by_path(sender, state, path):
    """Отправляет файл по указанному пути на удалённый ПК (в фоновом потоке)."""
    def worker():
        try:
            if not os.path.isfile(path):
                print(f"[client] файл не найден: {path}")
                return
            size = os.path.getsize(path)
            name = os.path.basename(path)
            sender.send_json(common.MSG_FILE_META, {"name": name, "size": size})
            sent = 0
            with open(path, "rb") as f:
                while state.alive:
                    chunk = f.read(256 * 1024)
                    if not chunk:
                        break
                    sender.send(common.MSG_FILE_CHUNK, chunk)
                    sent += len(chunk)
            sender.send(common.MSG_FILE_END)
            print(f"[client] отправлено {sent} байт: {name}")
        except (ConnectionError, socket.error) as e:
            print(f"[client] ошибка отправки файла (сеть): {e}")
        except OSError as e:
            print(f"[client] ошибка отправки файла (диск): {e}")
        except Exception as e:
            print(f"[client] ошибка отправки файла: {e}")
            import traceback; traceback.print_exc()
    threading.Thread(target=worker, daemon=True).start()


def send_files_by_paths(sender, state, paths):
    """Отправляет НЕСКОЛЬКО файлов на удалённый ПК последовательно в одном
    фоновом потоке. Файлы передаются по очереди (META/CHUNK/END для каждого),
    без ручного повторного запуска между ними. Каталоги раскрываются в их файлы
    (нерекурсивно)."""
    # Раскрываем каталоги в список файлов верхнего уровня.
    expanded = []
    for p in paths:
        if os.path.isdir(p):
            try:
                for name in sorted(os.listdir(p)):
                    full = os.path.join(p, name)
                    if os.path.isfile(full):
                        expanded.append(full)
            except OSError as e:
                print(f"[client] не прочитать каталог {p}: {e}")
        elif os.path.isfile(p):
            expanded.append(p)

    def worker():
        total = len(expanded)
        done = 0
        for path in expanded:
            if not state.alive:
                break
            try:
                size = os.path.getsize(path)
                name = os.path.basename(path)
                sender.send_json(common.MSG_FILE_META, {"name": name, "size": size})
                sent = 0
                with open(path, "rb") as f:
                    while state.alive:
                        chunk = f.read(256 * 1024)
                        if not chunk:
                            break
                        sender.send(common.MSG_FILE_CHUNK, chunk)
                        sent += len(chunk)
                sender.send(common.MSG_FILE_END)
                done += 1
                print(f"[client] отправлено ({done}/{total}) {sent} байт: {name}")
            except (ConnectionError, socket.error) as e:
                print(f"[client] ошибка отправки файла (сеть): {e}")
                break
            except OSError as e:
                print(f"[client] ошибка отправки файла (диск): {e}")
            except Exception as e:
                print(f"[client] ошибка отправки файла: {e}")
                import traceback; traceback.print_exc()
        print(f"[client] передача завершена: {done}/{total} файлов")

    threading.Thread(target=worker, daemon=True).start()


def send_file_dialog(sender, state):
    """Открывает диалог выбора файлов (можно выбрать несколько) и отправляет
    их на удалённый ПК последовательно."""
    def worker():
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            paths = filedialog.askopenfilenames(
                title="Файлы для отправки на удалённый ПК (можно выбрать несколько)")
            root.destroy()
        except Exception as e:
            print(f"[client] диалог файла недоступен: {e}")
            return
        if not paths:
            return
        send_files_by_paths(sender, state, list(paths))

    threading.Thread(target=worker, daemon=True).start()


def browse_remote(sender, state):
    """Open a Tk dialog to enter a remote path, request directory listing,
    and let the user pick a file to pull from the host."""
    def worker():
        try:
            import tkinter as tk
            from tkinter import simpledialog, messagebox

            root = tk.Tk()
            root.withdraw()

            # Ask for directory path
            path = simpledialog.askstring(
                "Обзор удалённого ПК",
                "Путь к каталогу на удалённом ПК\n(пусто = домашняя папка):",
                parent=root)
            if path is None:
                root.destroy()
                return
            path = path.strip()

            # Request listing
            sender.send_json(common.MSG_DIR_LIST_REQ, {"path": path})

            # Wait for response (up to 5 seconds)
            deadline = time.time() + 5.0
            listing = None
            while time.time() < deadline:
                with state.lock:
                    if state.dir_listing is not None:
                        listing = state.dir_listing
                        state.dir_listing = None
                        break
                time.sleep(0.1)

            if listing is None:
                messagebox.showwarning("Таймаут", "Нет ответа от хоста.", parent=root)
                root.destroy()
                return

            if listing.get("error"):
                messagebox.showwarning("Ошибка", listing["error"], parent=root)
                root.destroy()
                return

            entries = listing.get("entries", [])
            remote_path = listing.get("path", path)
            if not entries:
                messagebox.showinfo("Пусто", f"Каталог '{remote_path}' пуст.", parent=root)
                root.destroy()
                return

            # Show file picker
            pick_win = tk.Toplevel(root)
            pick_win.title(f"Файлы: {remote_path}")
            pick_win.geometry("500x400")

            listbox = tk.Listbox(pick_win, font=("Consolas", 10),
                                 selectmode=tk.EXTENDED)
            listbox.pack(fill="both", expand=True, padx=4, pady=4)
            file_entries = []
            for e in entries:
                is_dir = e.get("is_dir", False)
                name = e["name"]
                if is_dir:
                    display = f"[DIR]  {name}"
                else:
                    sz = e.get("size", 0)
                    display = f"       {name}  ({sz} B)"
                listbox.insert(tk.END, display)
                file_entries.append(e)

            def _do_pull(paths):
                if not paths:
                    return
                # Один пакетный запрос — host выкачает файлы по очереди.
                sender.send_json(common.MSG_FILE_PULL_REQ, {"paths": paths})
                print(f"[client] запрос {len(paths)} файл(ов): {paths}")

            def on_pull():
                sel = listbox.curselection()
                if not sel:
                    return
                # Если выбран один каталог — заходим в него.
                if len(sel) == 1 and file_entries[sel[0]].get("is_dir"):
                    entry = file_entries[sel[0]]
                    new_path = remote_path.rstrip("/\\") + "/" + entry["name"]
                    pick_win.destroy()
                    root.destroy()
                    _browse_path(sender, state, new_path)
                    return
                # Иначе выкачиваем все выбранные файлы (каталоги пропускаем).
                paths = [remote_path.rstrip("/\\") + "/" + file_entries[i]["name"]
                         for i in sel if not file_entries[i].get("is_dir")]
                _do_pull(paths)
                pick_win.destroy()
                root.destroy()

            def on_pull_all():
                paths = [remote_path.rstrip("/\\") + "/" + e["name"]
                         for e in file_entries if not e.get("is_dir")]
                _do_pull(paths)
                pick_win.destroy()
                root.destroy()

            btn_frame = tk.Frame(pick_win)
            btn_frame.pack(fill="x", padx=4, pady=4)
            tk.Button(btn_frame, text="Скачать выбранное / Открыть папку", command=on_pull).pack(side="left")
            tk.Button(btn_frame, text="Скачать все файлы", command=on_pull_all).pack(side="left")
            tk.Button(btn_frame, text="Закрыть", command=lambda: (pick_win.destroy(), root.destroy())).pack(side="right")

            pick_win.protocol("WM_DELETE_WINDOW", lambda: (pick_win.destroy(), root.destroy()))
            root.mainloop()
        except Exception as e:
            print(f"[client] ошибка обзора: {e}")

    threading.Thread(target=worker, daemon=True).start()


def _browse_path(sender, state, path):
    """Request a specific path and show results (re-entry for directory navigation)."""
    def worker():
        try:
            import tkinter as tk
            from tkinter import messagebox

            sender.send_json(common.MSG_DIR_LIST_REQ, {"path": path})

            deadline = time.time() + 5.0
            listing = None
            while time.time() < deadline:
                with state.lock:
                    if state.dir_listing is not None:
                        listing = state.dir_listing
                        state.dir_listing = None
                        break
                time.sleep(0.1)

            if listing is None or listing.get("error"):
                return

            entries = listing.get("entries", [])
            remote_path = listing.get("path", path)

            root = tk.Tk()
            root.withdraw()
            pick_win = tk.Toplevel(root)
            pick_win.title(f"Файлы: {remote_path}")
            pick_win.geometry("500x400")

            listbox = tk.Listbox(pick_win, font=("Consolas", 10),
                                 selectmode=tk.EXTENDED)
            listbox.pack(fill="both", expand=True, padx=4, pady=4)
            file_entries = []
            for e in entries:
                is_dir = e.get("is_dir", False)
                name = e["name"]
                if is_dir:
                    display = f"[DIR]  {name}"
                else:
                    sz = e.get("size", 0)
                    display = f"       {name}  ({sz} B)"
                listbox.insert(tk.END, display)
                file_entries.append(e)

            def _do_pull(paths):
                if not paths:
                    return
                sender.send_json(common.MSG_FILE_PULL_REQ, {"paths": paths})
                print(f"[client] запрос {len(paths)} файл(ов): {paths}")

            def on_pull():
                sel = listbox.curselection()
                if not sel:
                    return
                if len(sel) == 1 and file_entries[sel[0]].get("is_dir"):
                    entry = file_entries[sel[0]]
                    new_path = remote_path.rstrip("/\\") + "/" + entry["name"]
                    pick_win.destroy()
                    root.destroy()
                    _browse_path(sender, state, new_path)
                    return
                paths = [remote_path.rstrip("/\\") + "/" + file_entries[i]["name"]
                         for i in sel if not file_entries[i].get("is_dir")]
                _do_pull(paths)
                pick_win.destroy()
                root.destroy()

            def on_pull_all():
                paths = [remote_path.rstrip("/\\") + "/" + e["name"]
                         for e in file_entries if not e.get("is_dir")]
                _do_pull(paths)
                pick_win.destroy()
                root.destroy()

            btn_frame = tk.Frame(pick_win)
            btn_frame.pack(fill="x", padx=4, pady=4)
            tk.Button(btn_frame, text="Скачать выбранное / Открыть папку", command=on_pull).pack(side="left")
            tk.Button(btn_frame, text="Скачать все файлы", command=on_pull_all).pack(side="left")
            tk.Button(btn_frame, text="Закрыть", command=lambda: (pick_win.destroy(), root.destroy())).pack(side="right")

            pick_win.protocol("WM_DELETE_WINDOW", lambda: (pick_win.destroy(), root.destroy()))
            root.mainloop()
        except Exception as e:
            print(f"[client] ошибка обзора: {e}")

    threading.Thread(target=worker, daemon=True).start()


def ping_loop(sender, state):
    """Раз в секунду шлёт PING для замера RTT."""
    while state.alive:
        try:
            sender.send_json(common.MSG_PING, {"t": time.time()})
        except (ConnectionError, socket.error):
            return
        time.sleep(1.0)


def _draw_rounded_rect(surface, color, rect, radius, alpha=255):
    """Draw a filled rounded rectangle with optional alpha transparency."""
    x, y, w, h = rect
    if alpha < 255:
        tmp = pygame.Surface((w, h), pygame.SRCALPHA)
        c = (*color[:3], alpha)
        pygame.draw.rect(tmp, c, (0, 0, w, h), border_radius=radius)
        surface.blit(tmp, (x, y))
    else:
        pygame.draw.rect(surface, color, rect, border_radius=radius)


class _ToolbarButton:
    """A single toolbar button with icon text, label, hover state."""
    def __init__(self, icon, label, action):
        self.icon = icon
        self.label = label
        self.action = action
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.hovered = False


# ── Display-optimization helpers (pure, testable — no window required) ─────

# Map saved render_backend value -> SDL_RENDERDRIVER hint. "none" means force
# a plain software surface (no accelerated renderer at all).
RENDER_BACKEND_HINTS = {
    "direct3d11": "direct3d11",
    "direct3d": "direct3d",
    "opengl": "opengl",
    "software": "software",
    "none": None,
}


def apply_render_backend(env, backend, want_16bit):
    """Set SDL environment hints for the chosen render backend, BEFORE pygame
    init. Mutates and returns ``env`` (normally ``os.environ``). Fully graceful:
    unknown backend -> default. Returns a dict describing what was applied so
    the caller can decide on display depth/flags.

    - direct3d11/direct3d/opengl -> SDL_RENDERDRIVER hint, hardware path
    - software / none            -> SDL_RENDERDRIVER=software, no HW accel
    """
    hint = RENDER_BACKEND_HINTS.get(backend, "direct3d11")
    accel = backend not in ("software", "none")
    if hint is not None:
        env["SDL_RENDERDRIVER"] = hint
    else:
        # Force software rendering, drop any accelerated renderer hint.
        env["SDL_RENDERDRIVER"] = "software"
        env["SDL_FRAMEBUFFER_ACCELERATION"] = "0"
    return {
        "backend": backend,
        "render_driver": env.get("SDL_RENDERDRIVER"),
        "accelerated": accel,
        "depth": 16 if want_16bit else 0,  # 0 = let pygame pick native depth
    }


def compute_fit_rect(src_w, src_h, dst_w, dst_h, fit_mode="fit"):
    """Compute the letterboxed destination rect (x, y, w, h) for drawing a
    src_w×src_h frame into a dst_w×dst_h window.

    fit_mode="fit"    -> scale preserving aspect ratio, centered (letterbox).
    fit_mode="actual" -> 1:1, centered; cropped implicitly by clamping origin
                          to keep top-left visible when larger than window.
    """
    if src_w <= 0 or src_h <= 0 or dst_w <= 0 or dst_h <= 0:
        return (0, 0, 0, 0)
    if fit_mode == "actual":
        x = (dst_w - src_w) // 2
        y = (dst_h - src_h) // 2
        return (x, y, src_w, src_h)
    # fit: preserve aspect ratio
    scale = min(dst_w / src_w, dst_h / src_h)
    w = max(1, int(round(src_w * scale)))
    h = max(1, int(round(src_h * scale)))
    x = (dst_w - w) // 2
    y = (dst_h - h) // 2
    return (x, y, w, h)


class ScaledFrameCache:
    """Caches the scaled surface keyed by (id(src_surface), dst_size, smooth).
    Returns the SAME object when inputs are unchanged, so the render loop can
    skip redundant scaling and skip duplicate blits."""

    def __init__(self):
        self._key = None
        self._surf = None

    def get(self, src_surface, dst_size, smooth, scale_fn=None, smooth_fn=None):
        key = (id(src_surface), tuple(dst_size), bool(smooth))
        if key == self._key and self._surf is not None:
            return self._surf, False  # unchanged -> caller may skip blit
        if dst_size == src_surface.get_size():
            scaled = src_surface
        else:
            fn = (smooth_fn if smooth else scale_fn)
            if fn is None:
                # default to pygame transforms (real run)
                fn = (pygame.transform.smoothscale if smooth
                      else pygame.transform.scale)
            scaled = fn(src_surface, dst_size)
        self._key = key
        self._surf = scaled
        return scaled, True  # changed -> caller should blit


# ── GPU upscaling + sharpening helpers (pure decision logic, testable) ─────

def choose_render_path(gpu_upscale, render_backend, sdl2_available):
    """Decide whether to use the GPU (pygame._sdl2 Renderer/Texture) render path
    or fall back to the CPU surface-blit path. Pure function (no GPU needed).

    Returns "gpu" only when ALL hold:
      - the user enabled display.gpu_upscale,
      - the render backend is an accelerated one (not software/none),
      - pygame._sdl2.video is importable (Renderer creatable in principle).
    Otherwise "cpu" (the existing compute_fit_rect + ScaledFrameCache path).
    """
    if not gpu_upscale:
        return "cpu"
    if render_backend in ("software", "none"):
        return "cpu"
    if not sdl2_available:
        return "cpu"
    return "gpu"


def sdl2_video_available():
    """True if pygame._sdl2.video can be imported (graceful, never raises)."""
    try:
        import pygame._sdl2.video  # noqa: F401
        return True
    except Exception:
        return False


def detect_sharpen_backend(sharpen, moderngl_available, numpy_available, pil_available):
    """Pick the sharpening implementation given the requested strength and which
    libraries are present. Pure function — zero overhead semantics at 0.

    Returns one of: "off", "gpu" (moderngl), "cpu_numpy", "cpu_pil".
    Order of preference when sharpen>0: moderngl (GPU) -> numpy -> PIL -> off.
    """
    if not sharpen or sharpen <= 0:
        return "off"
    if moderngl_available:
        return "gpu"
    if numpy_available:
        return "cpu_numpy"
    if pil_available:
        return "cpu_pil"
    return "off"


def _moderngl_available():
    try:
        import moderngl  # noqa: F401
        return True
    except Exception:
        return False


def _numpy_available():
    try:
        import numpy  # noqa: F401
        return True
    except Exception:
        return False


def _pil_available_for_sharpen():
    try:
        from PIL import ImageFilter  # noqa: F401
        return True
    except Exception:
        return False


def sharpen_surface_cpu(surface, amount, backend):
    """Apply an unsharp-mask to a pygame Surface on the CPU. Returns a NEW
    surface (or the same one if amount<=0 / backend off). Graceful: any failure
    returns the original surface unchanged. `amount` is 0..100.
    """
    if not amount or amount <= 0 or backend in ("off", "gpu"):
        return surface
    strength = max(0.0, min(amount, 100)) / 100.0
    try:
        w, h = surface.get_size()
        raw = pygame.image.tostring(surface, "RGB")
        if backend == "cpu_numpy":
            import numpy as np
            arr = np.frombuffer(raw, dtype=np.uint8).reshape((h, w, 3)).astype(np.float32)
            # Cheap 3x3 box blur as the "unsharp" low-pass, separable.
            blur = arr.copy()
            blur[1:-1, :, :] = (arr[:-2, :, :] + arr[1:-1, :, :] + arr[2:, :, :]) / 3.0
            tmp = blur.copy()
            blur[:, 1:-1, :] = (tmp[:, :-2, :] + tmp[:, 1:-1, :] + tmp[:, 2:, :]) / 3.0
            out = arr + strength * (arr - blur)
            out = np.clip(out, 0, 255).astype(np.uint8)
            return pygame.image.frombuffer(out.tobytes(), (w, h), "RGB")
        elif backend == "cpu_pil":
            from PIL import Image, ImageFilter
            img = Image.frombytes("RGB", (w, h), raw)
            img = img.filter(ImageFilter.UnsharpMask(
                radius=2, percent=int(strength * 150), threshold=2))
            return pygame.image.frombuffer(img.tobytes(), (w, h), "RGB")
    except Exception as e:
        log.warning("sharpen failed (%s) — passthrough", e)
        return surface
    return surface


def run_ui(sender, state, clip):
    # ── Apply saved render backend / 16-bit BEFORE pygame init ────────────
    render_backend = "direct3d11"
    want_16bit = False
    fit_mode = "fit"
    smooth_scale = True
    gpu_upscale = True
    sharpen = 0
    try:
        from settings_config import config as _scfg
        render_backend = _scfg.get("render_backend", "direct3d11")
        want_16bit = bool(_scfg.get("render_16bit", False))
        fit_mode = _scfg.get("display.fit_mode", "fit")
        smooth_scale = bool(_scfg.get("display.smooth_scale", True))
        gpu_upscale = bool(_scfg.get("display.gpu_upscale", True))
        sharpen = int(_scfg.get("display.sharpen", 0))
    except Exception:
        pass

    # Texture scale quality for the SDL renderer (1=linear, 2=anisotropic/best).
    # Must be set BEFORE pygame.init(). Cheap and harmless on the CPU path.
    os.environ.setdefault("SDL_HINT_RENDER_SCALE_QUALITY", "2")

    # Sharpening backend selection (PART C): off at 0 -> zero overhead.
    sharpen_backend = detect_sharpen_backend(
        sharpen, _moderngl_available(), _numpy_available(),
        _pil_available_for_sharpen())
    if sharpen_backend != "off":
        print(f"[client] резкость={sharpen} backend={sharpen_backend}")
    # 16-bit forces fast (non-smooth) scaling for speed.
    if want_16bit:
        smooth_scale = False
    applied = apply_render_backend(os.environ, render_backend, want_16bit)
    print(f"[client] рендер-бэкенд: {applied}")

    pygame.init()
    pygame.display.set_caption("Remote Desktop")

    depth = applied["depth"]
    base_flags = pygame.RESIZABLE
    try:
        if depth:
            win = pygame.display.set_mode((960, 600), base_flags, depth)
        else:
            win = pygame.display.set_mode((960, 600), base_flags)
    except pygame.error as exc:
        # Backend failed to init -> fall back to plain software default.
        log.warning("display init failed (%s) — fallback to software default", exc)
        os.environ["SDL_RENDERDRIVER"] = "software"
        win = pygame.display.set_mode((960, 600), pygame.RESIZABLE)

    scaled_cache = ScaledFrameCache()
    clock = pygame.time.Clock()

    # ── GPU upscale path (PART B): pygame._sdl2 Renderer + Texture ─────────
    # We create a Renderer attached to the EXISTING pygame window. Each decoded
    # RGB frame is uploaded to a Texture and drawn scaled to the fit-rect by the
    # active SDL hardware renderer (Direct3D11/OpenGL → GPU bilinear). The HUD/
    # toolbar overlay is drawn as a second (streaming) texture on top.
    # If anything fails, render_path falls back to "cpu" (never black screen).
    render_path = choose_render_path(gpu_upscale, render_backend,
                                     sdl2_video_available())
    gpu = {"renderer": None, "frame_tex": None, "frame_key": None,
           "overlay_tex": None, "video": None}

    def _init_gpu_renderer():
        """Create the _sdl2 Renderer over the current window. Called AFTER the
        splash loop so the splash still presents via pygame.display.flip()."""
        nonlocal render_path
        if render_path != "gpu":
            return
        try:
            from pygame._sdl2 import video as _sdl2video
            gpu["video"] = _sdl2video
            gpu["renderer"] = _sdl2video.Renderer.from_window(
                _sdl2video.Window.from_display_module())
            print("[client] GPU-апскейл: pygame._sdl2 Renderer активен")
        except Exception as e:
            log.warning("GPU renderer unavailable (%s) — CPU fallback", e)
            render_path = "cpu"
            gpu["renderer"] = None
        print(f"[client] путь рендера: {render_path}")

    def _rebuild_gpu_renderer():
        """Recreate the Renderer after the window is recreated (resize/fullscreen).
        Textures are invalidated, so reset the cache too. Graceful fallback."""
        nonlocal render_path
        if render_path != "gpu":
            return
        gpu["frame_tex"] = None
        gpu["frame_key"] = None
        gpu["overlay_tex"] = None
        try:
            v = gpu["video"]
            gpu["renderer"] = v.Renderer.from_window(v.Window.from_display_module())
        except Exception as e:
            log.warning("GPU renderer rebuild failed (%s) — CPU fallback", e)
            render_path = "cpu"
            gpu["renderer"] = None

    def _gpu_upload_frame(src_surf):
        """Upload `src_surf` to a Texture (cached by surface identity)."""
        v = gpu["video"]
        key = id(src_surf)
        if key != gpu["frame_key"] or gpu["frame_tex"] is None:
            try:
                gpu["frame_tex"] = v.Texture.from_surface(gpu["renderer"], src_surf)
                gpu["frame_key"] = key
            except Exception as e:
                log.warning("texture upload failed (%s)", e)
                return None
        return gpu["frame_tex"]

    # Fullscreen state
    is_fullscreen = False
    windowed_size = (960, 600)  # remember windowed size for restore

    def _toggle_fullscreen():
        nonlocal win, is_fullscreen, windowed_size, toolbar_visible, toolbar_last_hover, toolbar_alpha
        try:
            if is_fullscreen:
                # Exit fullscreen — restore windowed mode
                win = pygame.display.set_mode(windowed_size, pygame.RESIZABLE)
                is_fullscreen = False
            else:
                # Enter fullscreen — save current window size first
                windowed_size = win.get_size()
                win = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
                is_fullscreen = True
                # Flash toolbar briefly so user sees the UI
                toolbar_visible = True
                toolbar_alpha = TOOLBAR_ALPHA
                toolbar_last_hover = time.time()
            pygame.display.set_caption(f"Remote Desktop — {state.w}×{state.h}")
            _rebuild_gpu_renderer()
        except pygame.error as exc:
            log.warning("Fullscreen toggle failed: %s — staying in current mode", exc)

    # Fonts
    try:
        font_ui = pygame.font.SysFont("Segoe UI", 14)
        font_icon = pygame.font.SysFont("Segoe UI Symbol,Segoe UI Emoji,Segoe UI", 16)
    except Exception:
        font_ui = pygame.font.SysFont("Consolas", 14)
        font_icon = font_ui
    font_mono = pygame.font.SysFont("Consolas", 13)
    font_tooltip = pygame.font.SysFont("Segoe UI", 12)

    # Colors
    COL_TOOLBAR_BG = (26, 26, 46)       # #1a1a2e
    COL_ACCENT = (0, 120, 212)          # #0078d4
    COL_HOVER = (42, 42, 78)            # #2a2a4e
    COL_TEXT = (255, 255, 255)
    COL_TEXT_DIM = (180, 180, 200)
    COL_GREEN = (0, 200, 83)
    COL_YELLOW = (255, 193, 7)
    COL_RED = (244, 67, 54)

    TOOLBAR_H = 40
    BUTTON_H = 32
    TOOLBAR_ALPHA = 200
    TOOLBAR_TRIGGER_ZONE = 5        # px from top to trigger toolbar
    TOOLBAR_AUTOHIDE_DELAY = 2.0    # seconds

    # Toolbar buttons — defined once, actions bound via lambdas
    toolbar_buttons = []

    def _action_monitor():
        with state.lock:
            nxt = state.index % max(1, state.monitors) + 1
        sender.send_json(common.MSG_SET_MONITOR, {"index": nxt})

    def _action_send():
        send_file_dialog(sender, state)

    def _action_browse():
        browse_remote(sender, state)

    def _action_stats():
        state.show_stats = not state.show_stats

    def _action_disconnect():
        state.alive = False

    def _action_fullscreen():
        _toggle_fullscreen()

    toolbar_buttons.append(_ToolbarButton("M", "Monitors", _action_monitor))
    toolbar_buttons.append(_ToolbarButton("S", "Send File", _action_send))
    toolbar_buttons.append(_ToolbarButton("D", "Browse Remote", _action_browse))
    toolbar_buttons.append(_ToolbarButton("I", "Stats", _action_stats))
    toolbar_buttons.append(_ToolbarButton("F", "Fullscreen", _action_fullscreen))
    toolbar_buttons.append(_ToolbarButton("X", "Disconnect", _action_disconnect))

    # Toolbar state
    toolbar_visible = False
    toolbar_alpha = 0
    toolbar_y_offset = float(-TOOLBAR_H)
    toolbar_last_hover = 0.0
    toolbar_hovered = False

    threading.Thread(target=ping_loop, args=(sender, state), daemon=True).start()
    last_stat = time.time()

    # Ждём первый кадр, НО продолжаем качать события и рисовать статус —
    # иначе Windows помечает окно «Не отвечает» и оно остаётся чёрным.
    import math as _math
    splash = pygame.font.SysFont("Segoe UI", 22)
    splash_sm = pygame.font.SysFont("Consolas", 15)
    t_wait = time.time()
    while state.alive and state.surface is None:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                state.alive = False
            elif event.type == pygame.VIDEORESIZE:
                win = pygame.display.set_mode(event.size, pygame.RESIZABLE)
        with state.lock:
            status = state.status
            err = state.decode_err
        win.fill((17, 24, 39))
        ww, wh = win.get_size()

        elapsed = time.time() - t_wait
        cx, cy = ww // 2, wh // 2 - 50
        for i in range(12):
            angle = _math.radians(i * 30 - elapsed * 360)
            alpha = int(80 + 175 * ((i / 12 + elapsed * 2) % 1.0))
            alpha = min(255, alpha)
            dx = int(_math.cos(angle) * 20)
            dy = int(_math.sin(angle) * 20)
            dot_surf = pygame.Surface((8, 8), pygame.SRCALPHA)
            pygame.draw.circle(dot_surf, (100, 140, 220, alpha), (4, 4), 4)
            win.blit(dot_surf, (cx + dx - 4, cy + dy - 4))

        msg = err or status
        color = (255, 110, 110) if err else (220, 220, 230)
        txt = splash.render(msg, True, color)
        win.blit(txt, (ww // 2 - txt.get_width() // 2, wh // 2 + 10))
        waited = int(elapsed)
        sub = splash_sm.render(f"ожидание: {waited}s   (Ctrl+Alt+Q — выход)", True, (130, 140, 160))
        win.blit(sub, (ww // 2 - sub.get_width() // 2, wh // 2 + 48))
        pygame.display.flip()
        clock.tick(30)
    if not state.alive:
        return

    # First frame arrived — now create the GPU renderer (if enabled). Done here
    # so the splash animation above keeps presenting via pygame.display.flip().
    _init_gpu_renderer()

    fade_alpha = 0

    def send_input(ev):
        # Belt-and-suspenders: при роли "только просмотр" не шлём ввод вообще
        # (host всё равно блокирует, но так не засоряем канал).
        if state.role == "view":
            return
        try:
            sender.send_json(common.MSG_INPUT, ev)
        except (ConnectionError, socket.error):
            state.alive = False
        except Exception as exc:
            log.warning("send_input error: %s (event: %s)", exc, ev)

    def to_norm(pos):
        ww, wh = win.get_size()
        return (max(0.0, min(1.0, pos[0] / ww)), max(0.0, min(1.0, pos[1] / wh)))

    BTN = {1: "left", 2: "middle", 3: "right"}
    held = {}   # pygame key -> ident
    consumed_keys = set()  # keys consumed by local hotkeys — suppress their KEYUP

    def release_all():
        for ident in list(held.values()):
            send_input({"k": "kup", **ident})
        held.clear()
        consumed_keys.clear()

    while state.alive:
        now = time.time()

        # Clean window title (no hotkey list)
        if state.title_dirty:
            with state.lock:
                state.title_dirty = False
                cap = f"Remote Desktop — {state.w}×{state.h}"
                if state.role == "view":
                    cap += "  (только просмотр)"
            pygame.display.set_caption(cap)

        # Track mouse for toolbar
        mouse_pos = pygame.mouse.get_pos()
        mouse_in_trigger = mouse_pos[1] <= TOOLBAR_TRIGGER_ZONE
        mouse_in_toolbar = mouse_pos[1] <= TOOLBAR_H + int(toolbar_y_offset) and toolbar_visible

        if mouse_in_trigger or mouse_in_toolbar:
            toolbar_last_hover = now
            toolbar_hovered = True
        else:
            toolbar_hovered = False

        if toolbar_hovered or (now - toolbar_last_hover < TOOLBAR_AUTOHIDE_DELAY):
            toolbar_visible = True
            toolbar_alpha = min(toolbar_alpha + 30, TOOLBAR_ALPHA)
            toolbar_y_offset = min(toolbar_y_offset + 4.0, 0.0)
        else:
            toolbar_alpha = max(toolbar_alpha - 15, 0)
            toolbar_y_offset = max(toolbar_y_offset - 4.0, float(-TOOLBAR_H))
            if toolbar_alpha == 0:
                toolbar_visible = False

        # Check button hover states
        for btn in toolbar_buttons:
            btn.hovered = toolbar_visible and btn.rect.collidepoint(mouse_pos)

        # Event handling
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                state.alive = False
            elif event.type == pygame.VIDEORESIZE:
                if not is_fullscreen:
                    windowed_size = event.size
                    win = pygame.display.set_mode(event.size, pygame.RESIZABLE)
                    _rebuild_gpu_renderer()
            elif event.type == pygame.MOUSEMOTION:
                # Don't forward mouse to remote when in toolbar area
                if not (toolbar_visible and event.pos[1] <= TOOLBAR_H + int(toolbar_y_offset)):
                    x, y = to_norm(event.pos)
                    send_input({"k": "move", "x": x, "y": y})
            elif event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                if (event.type == pygame.MOUSEBUTTONDOWN and event.button == 1
                        and toolbar_visible and event.pos[1] <= TOOLBAR_H + int(toolbar_y_offset)):
                    for btn in toolbar_buttons:
                        if btn.rect.collidepoint(event.pos):
                            btn.action()
                            break
                    continue
                if event.button in (4, 5):  # scroll wheel
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
                # Fullscreen: F11 toggles, Esc exits, Ctrl+Alt+F toggles
                if event.type == pygame.KEYDOWN and event.key == pygame.K_F11:
                    _toggle_fullscreen()
                    consumed_keys.add(event.key)
                    continue
                if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE and is_fullscreen:
                    _toggle_fullscreen()
                    consumed_keys.add(event.key)
                    continue
                if event.type == pygame.KEYDOWN and hotkey and event.key == pygame.K_f:
                    _toggle_fullscreen()
                    consumed_keys.add(event.key)
                    continue
                if event.type == pygame.KEYDOWN and hotkey and event.key == pygame.K_m:
                    _action_monitor()
                    consumed_keys.add(event.key)
                    continue
                if event.type == pygame.KEYDOWN and hotkey and event.key == pygame.K_s:
                    send_file_dialog(sender, state)
                    consumed_keys.add(event.key)
                    continue
                if event.type == pygame.KEYDOWN and hotkey and event.key == pygame.K_d:
                    browse_remote(sender, state)
                    consumed_keys.add(event.key)
                    continue
                if event.type == pygame.KEYDOWN and hotkey and event.key == pygame.K_i:
                    state.show_stats = not state.show_stats
                    consumed_keys.add(event.key)
                    continue
                if event.type == pygame.KEYUP and event.key in consumed_keys:
                    consumed_keys.discard(event.key)
                    continue
                if event.type == pygame.KEYDOWN:
                    ident = key_ident(event, mods)
                    if ident is not None:
                        held[event.key] = ident
                        send_input({"k": "kdown", **ident})
                else:  # KEYUP
                    ident = held.pop(event.key, None)
                    if ident is None and event.key in PG_SPECIAL:
                        ident = {"name": PG_SPECIAL[event.key]}
                    if ident is not None:
                        send_input({"k": "kup", **ident})
            elif event.type == getattr(pygame, "DROPFILE", -1):
                dropped = getattr(event, "file", None)
                if dropped and os.path.isfile(dropped):
                    print(f"[client] drag-and-drop: отправка {dropped}")
                    send_file_by_path(sender, state, dropped)
            elif event.type == getattr(pygame, "WINDOWFOCUSLOST", -1):
                release_all()

        # Stats recalculation (once per second)
        if now - last_stat >= 1.0:
            dt = now - last_stat
            with state.lock:
                state.fps = round(state.recv_frames / dt, 1)
                state.kbps = round(state.recv_bytes / 1024 / dt, 1)
                state.recv_frames = 0
                state.recv_bytes = 0
            last_stat = now

        # --- Render ---
        gpu_frame_rect = None  # set in GPU path; tells present step what to draw
        with state.lock:
            src = state.surface
            if src is not None and render_path == "gpu" and gpu["renderer"] is not None:
                # ── GPU path: upload frame, optional sharpen, draw via Texture.
                # Overlays are drawn onto `win` (transparent) and presented as a
                # second texture below. We clear `win` each frame here.
                ww, wh = win.get_size()
                sw, sh = src.get_size()
                dx, dy, dw, dh = compute_fit_rect(sw, sh, ww, wh, fit_mode)
                draw_src = src
                if sharpen_backend not in ("off", "gpu"):
                    draw_src = sharpen_surface_cpu(src, sharpen, sharpen_backend)
                tex = _gpu_upload_frame(draw_src)
                if tex is None:
                    # Upload failed permanently this frame -> fall back to CPU.
                    render_path = "cpu"
                else:
                    gpu_frame_rect = (dx, dy, dw, dh)
                    if fade_alpha < 255:
                        fade_alpha = min(fade_alpha + 18, 255)
                # Overlay surface starts fully transparent each frame.
                win.fill((0, 0, 0, 0))
            elif src is not None:
                ww, wh = win.get_size()
                sw, sh = src.get_size()
                dx, dy, dw, dh = compute_fit_rect(sw, sh, ww, wh, fit_mode)
                # CPU path: optional sharpen on the source before scaling.
                draw_src = src
                if sharpen_backend not in ("off", "gpu"):
                    draw_src = sharpen_surface_cpu(src, sharpen, sharpen_backend)
                # Scale (cached): same object returned when nothing changed.
                frame, changed = scaled_cache.get(draw_src, (dw, dh), smooth_scale)
                # Blit when the frame/window changed, during fade, or when an
                # overlay (stats HUD / toolbar) is active and would otherwise
                # leave stale pixels over a skipped redraw.
                overlay_active = state.show_stats or toolbar_visible
                need_blit = changed or fade_alpha < 255 or overlay_active
                if need_blit:
                    win.fill((17, 24, 39))  # letterbox / clear stale pixels
                    if fade_alpha < 255:
                        frame.set_alpha(fade_alpha)
                        win.blit(frame, (dx, dy))
                        frame.set_alpha(255)
                        fade_alpha = min(fade_alpha + 18, 255)
                    else:
                        win.blit(frame, (dx, dy))
            show = state.show_stats
            fps, kbps, rtt = state.fps, state.kbps, state.rtt_ms
            rtt_age = now - state.rtt_time if state.rtt_time else None
            mon_index, mon_total = state.index, state.monitors

        # --- HUD overlay (bottom-right) ---
        if show:
            # Mark RTT as stale if no PONG received for >5 seconds
            rtt_stale = rtt_age is None or rtt_age > 5.0
            if rtt_stale:
                rtt_txt = "--"
            else:
                rtt_txt = f"{rtt:.0f}ms" if rtt is not None else "--"
            # Quality dot color
            if rtt_stale or rtt is None or rtt > 150:
                dot_col = COL_RED
            elif rtt > 50:
                dot_col = COL_YELLOW
            else:
                dot_col = COL_GREEN

            hud_lines = [
                f"FPS {fps}",
                f"RTT {rtt_txt}",
                f"{kbps} KB/s",
            ]
            line_h = 18
            hud_w = 110
            hud_h = line_h * len(hud_lines) + 12
            ww, wh = win.get_size()
            hud_x = ww - hud_w - 10
            hud_y = wh - hud_h - 10

            # Semi-transparent background
            hud_bg = pygame.Surface((hud_w, hud_h), pygame.SRCALPHA)
            pygame.draw.rect(hud_bg, (0, 0, 0, 140), (0, 0, hud_w, hud_h), border_radius=6)
            win.blit(hud_bg, (hud_x, hud_y))

            # Quality dot
            pygame.draw.circle(win, dot_col, (hud_x + hud_w - 14, hud_y + 12), 5)

            # Text lines
            for i, line in enumerate(hud_lines):
                txt = font_mono.render(line, True, COL_TEXT_DIM)
                win.blit(txt, (hud_x + 8, hud_y + 6 + i * line_h))

        # Update fullscreen button label to reflect current state
        for btn in toolbar_buttons:
            if btn.action == _action_fullscreen:
                btn.label = "Windowed" if is_fullscreen else "Fullscreen"
                btn.icon = "W" if is_fullscreen else "F"

        # --- Toolbar (top, auto-hiding) ---
        if toolbar_visible and toolbar_alpha > 0:
            ww = win.get_size()[0]
            tb_surf = pygame.Surface((ww, TOOLBAR_H), pygame.SRCALPHA)
            pygame.draw.rect(tb_surf, (*COL_TOOLBAR_BG, toolbar_alpha),
                             (0, 0, ww, TOOLBAR_H))

            # Layout buttons centered horizontally
            btn_pad = 6
            btn_w_list = []
            for btn in toolbar_buttons:
                icon_w = font_icon.size(btn.icon)[0]
                label_w = font_ui.size(btn.label)[0]
                w = icon_w + label_w + 20
                btn_w_list.append(max(w, 70))

            # Monitor indicator width
            mon_text = f"Monitor {mon_index}/{mon_total}"
            mon_w = font_ui.size(mon_text)[0] + 16

            total_w = sum(btn_w_list) + btn_pad * (len(toolbar_buttons) - 1) + mon_w + 20
            start_x = (ww - total_w) // 2

            # Draw monitor indicator on the left side of button group
            mon_rect = pygame.Rect(start_x, (TOOLBAR_H - BUTTON_H) // 2, mon_w, BUTTON_H)
            pygame.draw.rect(tb_surf, (*COL_ACCENT, min(toolbar_alpha, 180)),
                             mon_rect, border_radius=4)
            mon_txt = font_ui.render(mon_text, True, COL_TEXT)
            tb_surf.blit(mon_txt, (mon_rect.x + (mon_w - mon_txt.get_width()) // 2,
                                   mon_rect.y + (BUTTON_H - mon_txt.get_height()) // 2))

            # Draw buttons
            bx = start_x + mon_w + 20
            for i, btn in enumerate(toolbar_buttons):
                bw = btn_w_list[i]
                by = (TOOLBAR_H - BUTTON_H) // 2
                btn.rect = pygame.Rect(bx, by + int(toolbar_y_offset), bw, BUTTON_H)

                # Hover highlight
                if btn.hovered:
                    pygame.draw.rect(tb_surf, (*COL_HOVER, min(toolbar_alpha, 220)),
                                     btn.rect, border_radius=4)

                # Icon + label
                icon_surf = font_icon.render(btn.icon, True, COL_TEXT)
                label_surf = font_ui.render(btn.label, True,
                                            COL_TEXT if btn.hovered else COL_TEXT_DIM)
                icon_y = by + (BUTTON_H - icon_surf.get_height()) // 2
                label_y = by + (BUTTON_H - label_surf.get_height()) // 2
                tb_surf.blit(icon_surf, (bx + 8, icon_y))
                tb_surf.blit(label_surf, (bx + 8 + icon_surf.get_width() + 4, label_y))

                # Disconnect button: red accent on hover
                if btn.label == "Disconnect" and btn.hovered:
                    pygame.draw.rect(tb_surf, (*COL_RED, 60),
                                     btn.rect, border_radius=4)

                bx += bw + btn_pad

            # Bottom border accent line
            pygame.draw.line(tb_surf, (*COL_ACCENT, toolbar_alpha),
                             (0, TOOLBAR_H - 1), (ww, TOOLBAR_H - 1))

            win.blit(tb_surf, (0, int(toolbar_y_offset)))

        # --- Present ---
        if render_path == "gpu" and gpu["renderer"] is not None:
            try:
                r = gpu["renderer"]
                v = gpu["video"]
                r.draw_color = (17, 24, 39, 255)
                r.clear()
                # 1) Frame texture scaled to fit-rect (GPU bilinear/anisotropic).
                if gpu_frame_rect is not None and gpu["frame_tex"] is not None:
                    gpu["frame_tex"].draw(dstrect=pygame.Rect(*gpu_frame_rect))
                # 2) Overlay (HUD + toolbar) — the whole window surface on top.
                try:
                    gpu["overlay_tex"] = v.Texture.from_surface(r, win)
                    gpu["overlay_tex"].blend_mode = 1  # SDL_BLENDMODE_BLEND
                    gpu["overlay_tex"].draw()
                except Exception:
                    pass
                r.present()
            except Exception as e:
                log.warning("GPU present failed (%s) — switching to CPU", e)
                render_path = "cpu"
                gpu["renderer"] = None
        else:
            pygame.display.flip()
        clock.tick(30)

    pygame.quit()


def make_socket(args):
    unique_id = getattr(args, "unique_id", None)

    if args.relay:
        host, port = args.relay.rsplit(":", 1)
        try:
            s = socket.create_connection((host, int(port)), timeout=10)
        except socket.timeout:
            raise ConnectionError(f"Не удалось подключиться к relay {host}:{port} (таймаут 10с)")
        except OSError as e:
            raise ConnectionError(f"Не удалось подключиться к relay {host}:{port}: {e}")

        if unique_id:
            resp = common.relay_connect_id(s, unique_id)
            if "not_found" in resp:
                s.close()
                raise ConnectionError(
                    f"Хост с ID {unique_id} не найден.\n"
                    f"Убедитесь, что хост запущен и подключён к relay.")
            if resp.startswith("ERROR"):
                s.close()
                raise ConnectionError(f"Relay отклонил подключение: {resp}")
            print(f"[client] подключаюсь через relay {args.relay}, ID {unique_id}")
        else:
            common.relay_register(s, "client", args.id)
            print(f"[client] подключаюсь через relay {args.relay}, комната '{args.id}'")
            line = common.relay_read_line(s)
            print(f"[client] relay: {line}")
        return s
    else:
        host, port = args.connect.rsplit(":", 1)
        try:
            s = socket.create_connection((host, int(port)), timeout=10)
        except (socket.timeout, OSError) as e:
            raise ConnectionError(f"Не удалось подключиться к {host}:{port}: {e}")
        print(f"[client] подключился к {args.connect}")
        return s


def run_client(args):
    """Подключение + окно. Вызывается из CLI и из GUI (в главном потоке)."""
    key = common.derive_key(args.password)
    sock = make_socket(args)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    common.enable_keepalive(sock)
    chan = common.SecureChannel(key)

    import json as _json
    # Клиентское предпочтение разрешения потока (PART A): отправляем хосту,
    # он умножит на свой scale. По умолчанию 100% = без изменений.
    _source_scale = 100
    try:
        from settings_config import config as _scfg
        _source_scale = int(_scfg.get("display.source_scale", 100))
    except Exception:
        _source_scale = 100
    try:
        common.send_frame(sock, chan, common.MSG_HELLO,
                          _json.dumps({
                              "video": video_mod is not None,
                              "client_id": get_or_create_client_id(),
                              "client_name": get_client_name(),
                              "source_scale": _source_scale,
                          }).encode("utf-8"))
    except (ConnectionError, socket.error, OSError) as e:
        sock.close()
        raise ConnectionError(
            f"Соединение разорвано при отправке HELLO.\n"
            f"Возможно, неверный пароль или хост закрыл соединение.\n({e})")

    sender = common.FrameSender(sock, chan)
    clip = common.ClipboardSync(
        lambda txt: sender.send_json(common.MSG_CLIPBOARD, {"text": txt}),
        lambda png: sender.send(common.MSG_CLIPBOARD_IMAGE, png),
    )
    clip.start()

    state = RemoteState()
    state.downloads_dir = getattr(args, "downloads", DEFAULT_DOWNLOADS)
    t = threading.Thread(target=reader_thread, args=(sock, chan, state, clip, sender), daemon=True)
    t.start()
    try:
        run_ui(sender, state, clip)
    except Exception as e:
        print(f"[client] ошибка UI: {e}")
    finally:
        state.alive = False
        clip.stop()
        try:
            sock.close()
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser(description="Remote desktop CLIENT (просмотрщик)")
    ap.add_argument("--connect", help="Прямой режим: host:порт")
    ap.add_argument("--relay", help="Режим relay: vps:порт")
    ap.add_argument("--id", default="default", help="ID комнаты для relay (старый протокол)")
    ap.add_argument("--unique-id", dest="unique_id", default=None,
                    help="9-значный ID хоста для подключения (новый протокол CONNECT)")
    ap.add_argument("--password", required=True, help="Общий пароль (E2E)")
    args = ap.parse_args()
    if not args.connect and not args.relay:
        ap.error("укажите --connect host:порт или --relay vps:порт")
    run_client(args)


if __name__ == "__main__":
    main()
