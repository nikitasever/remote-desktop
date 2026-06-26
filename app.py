"""
RemoteDesktop — единое окно-лаунчер.

Выбираете режим (этот ПК показывает экран / подключиться к другому ПК),
вводите адрес и пароль — и нажимаете «Запустить». Командная строка не нужна.

Собирается в один app.exe (см. build_exe.ps1).
"""

import json
import os
import sys
import traceback
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import host
import client

APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "RemoteDesktop")
CONFIG = os.path.join(APP_DIR, "config.json")
DEFAULT_DOWNLOADS = os.path.join(os.path.expanduser("~"), "RemoteDesktop_received")


class Args:
    """Простой контейнер атрибутов вместо argparse.Namespace."""
    def __init__(self, **kw):
        self.relay = None
        self.connect = None
        self.listen = None
        self.id = "default"
        self.password = ""
        self.downloads = DEFAULT_DOWNLOADS
        self.quality = 55
        self.fps = 18
        self.scale = 1.0
        self.codec = "auto"
        self.engine = "auto"   # auto/x264 — видео H.264; tiles — старые плитки
        self.__dict__.update(kw)


def load_config():
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(data):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


class LauncherUI:
    def __init__(self, root):
        self.root = root
        root.title("RemoteDesktop")
        root.resizable(False, False)
        cfg = load_config()

        self.role = tk.StringVar(value=cfg.get("role", "client"))
        self.conn = tk.StringVar(value=cfg.get("conn", "relay"))
        self.address = tk.StringVar(value=cfg.get("address", ""))
        self.room = tk.StringVar(value=cfg.get("room", "myroom"))
        self.password = tk.StringVar(value=cfg.get("password", ""))
        self.downloads = tk.StringVar(value=cfg.get("downloads", DEFAULT_DOWNLOADS))
        self.quality = tk.StringVar(value=str(cfg.get("quality", "70")))
        self.fps = tk.StringVar(value=str(cfg.get("fps", "20")))
        self.scale = tk.StringVar(value=cfg.get("scale", "100%"))
        self.codec = tk.StringVar(value=cfg.get("codec", "Авто"))
        self.engine = tk.StringVar(value=cfg.get("engine", "Видео H.264"))

        pad = {"padx": 8, "pady": 4}
        frm = ttk.Frame(root, padding=12)
        frm.grid(sticky="nsew")

        # --- Роль ---
        ttk.Label(frm, text="Режим:", font=("", 10, "bold")).grid(row=0, column=0, sticky="w", **pad)
        rf = ttk.Frame(frm); rf.grid(row=0, column=1, sticky="w")
        ttk.Radiobutton(rf, text="Подключиться к ПК (управлять)", value="client",
                        variable=self.role, command=self._refresh).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(rf, text="Этот ПК (дать управление собой)", value="host",
                        variable=self.role, command=self._refresh).grid(row=1, column=0, sticky="w")

        # --- Тип соединения ---
        ttk.Label(frm, text="Соединение:", font=("", 10, "bold")).grid(row=1, column=0, sticky="w", **pad)
        cf = ttk.Frame(frm); cf.grid(row=1, column=1, sticky="w")
        ttk.Radiobutton(cf, text="Через relay (за NAT)", value="relay",
                        variable=self.conn, command=self._refresh).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(cf, text="Прямое (LAN/VPN/Tailscale)", value="direct",
                        variable=self.conn, command=self._refresh).grid(row=0, column=1, sticky="w")

        # --- Адрес ---
        self.addr_lbl = ttk.Label(frm, text="Адрес:")
        self.addr_lbl.grid(row=2, column=0, sticky="w", **pad)
        self.addr_entry = ttk.Entry(frm, textvariable=self.address, width=34)
        self.addr_entry.grid(row=2, column=1, sticky="w", **pad)

        # --- ID комнаты ---
        self.room_lbl = ttk.Label(frm, text="ID комнаты:")
        self.room_lbl.grid(row=3, column=0, sticky="w", **pad)
        self.room_entry = ttk.Entry(frm, textvariable=self.room, width=34)
        self.room_entry.grid(row=3, column=1, sticky="w", **pad)

        # --- Пароль ---
        ttk.Label(frm, text="Пароль:").grid(row=4, column=0, sticky="w", **pad)
        pw = ttk.Frame(frm); pw.grid(row=4, column=1, sticky="w", **pad)
        self.pw_entry = ttk.Entry(pw, textvariable=self.password, width=26, show="•")
        self.pw_entry.grid(row=0, column=0)
        self._show_pw = tk.BooleanVar(value=False)
        ttk.Checkbutton(pw, text="показать", variable=self._show_pw,
                        command=self._toggle_pw).grid(row=0, column=1, padx=6)

        # --- Папка для файлов (только host) ---
        self.dl_lbl = ttk.Label(frm, text="Файлы в:")
        self.dl_lbl.grid(row=5, column=0, sticky="w", **pad)
        self.dl_frame = ttk.Frame(frm); self.dl_frame.grid(row=5, column=1, sticky="w", **pad)
        self.dl_entry = ttk.Entry(self.dl_frame, textvariable=self.downloads, width=26)
        self.dl_entry.grid(row=0, column=0)
        ttk.Button(self.dl_frame, text="…", width=3, command=self._pick_dir).grid(row=0, column=1, padx=4)

        # --- Качество передачи (только host) ---
        self.tune_lbl = ttk.Label(frm, text="Качество:")
        self.tune_lbl.grid(row=6, column=0, sticky="w", **pad)
        self.tune_frame = ttk.Frame(frm); self.tune_frame.grid(row=6, column=1, sticky="w", **pad)
        ttk.Label(self.tune_frame, text="чёткость").grid(row=0, column=0)
        ttk.Combobox(self.tune_frame, textvariable=self.quality, width=4, state="readonly",
                     values=["50", "60", "70", "80", "90"]).grid(row=0, column=1, padx=(2, 8))
        ttk.Label(self.tune_frame, text="FPS").grid(row=0, column=2)
        ttk.Combobox(self.tune_frame, textvariable=self.fps, width=4, state="readonly",
                     values=["15", "20", "25", "30", "40", "60"]).grid(row=0, column=3, padx=(2, 8))
        ttk.Label(self.tune_frame, text="масштаб").grid(row=0, column=4)
        ttk.Combobox(self.tune_frame, textvariable=self.scale, width=6, state="readonly",
                     values=["100%", "75%", "50%"]).grid(row=0, column=5, padx=2)
        ttk.Label(self.tune_frame, text="формат").grid(row=1, column=0, pady=(4, 0))
        ttk.Combobox(self.tune_frame, textvariable=self.codec, width=6, state="readonly",
                     values=["Авто", "JPEG", "PNG"]).grid(row=1, column=1, padx=(2, 8), pady=(4, 0), sticky="w")
        ttk.Label(self.tune_frame, text="PNG — без потерь (текст идеален)",
                  foreground="#888").grid(row=1, column=2, columnspan=4, sticky="w", pady=(4, 0))
        ttk.Label(self.tune_frame, text="движок").grid(row=2, column=0, pady=(4, 0))
        ttk.Combobox(self.tune_frame, textvariable=self.engine, width=14, state="readonly",
                     values=["Видео H.264", "Плитки (совместимость)"]
                     ).grid(row=2, column=1, columnspan=2, padx=(2, 8), pady=(4, 0), sticky="w")
        ttk.Label(self.tune_frame, text="H.264 — быстрее и легче по сети",
                  foreground="#888").grid(row=2, column=3, columnspan=3, sticky="w", pady=(4, 0))

        # --- Кнопка запуска ---
        self.start_btn = ttk.Button(frm, text="Запустить", command=self._start)
        self.start_btn.grid(row=7, column=0, columnspan=2, pady=(12, 4), sticky="ew")

        self.hint = ttk.Label(frm, text="", foreground="#666", wraplength=380, justify="left")
        self.hint.grid(row=8, column=0, columnspan=2, sticky="w", **pad)

        self._refresh()

    # ---- Динамика формы ----
    def _toggle_pw(self):
        self.pw_entry.config(show="" if self._show_pw.get() else "•")

    def _pick_dir(self):
        d = filedialog.askdirectory(initialdir=self.downloads.get() or os.path.expanduser("~"))
        if d:
            self.downloads.set(d)

    def _refresh(self):
        is_host = self.role.get() == "host"
        is_relay = self.conn.get() == "relay"

        # ID комнаты — только для relay
        for w in (self.room_lbl, self.room_entry):
            w.grid() if is_relay else w.grid_remove()
        # Папка файлов и настройки качества — только для host
        for w in (self.dl_lbl, self.dl_entry, self.dl_frame, self.tune_lbl, self.tune_frame):
            w.grid() if is_host else w.grid_remove()

        # Подпись адреса в зависимости от режима
        if is_relay:
            self.addr_lbl.config(text="Адрес relay:")
            self.address_hint = "IP:порт вашего relay-сервера, напр. 203.0.113.5:5800"
        elif is_host:
            self.addr_lbl.config(text="Слушать порт:")
            self.address_hint = "Порт, напр. 5900 (нужен проброс/туннель к этому ПК)"
        else:
            self.addr_lbl.config(text="Адрес ПК:")
            self.address_hint = "IP:порт удалённого ПК, напр. 100.x.y.z:5900"

        hints = []
        hints.append(self.address_hint)
        if is_relay:
            hints.append("ID комнаты должен совпадать на обоих ПК.")
        hints.append("Пароль одинаковый на обоих ПК — это ключ шифрования.")
        self.hint.config(text="  •  " + "\n  •  ".join(hints))

    # ---- Сборка Args из формы ----
    def _build_args(self):
        role = self.role.get()
        is_relay = self.conn.get() == "relay"
        addr = self.address.get().strip()
        pw = self.password.get()
        if not addr:
            raise ValueError("Укажите адрес/порт.")
        if not pw:
            raise ValueError("Укажите пароль.")

        a = Args(password=pw, id=self.room.get().strip() or "default")
        if role == "host":
            a.downloads = self.downloads.get().strip() or DEFAULT_DOWNLOADS
            a.quality = int(self.quality.get())
            a.fps = int(self.fps.get())
            a.scale = {"100%": 1.0, "75%": 0.75, "50%": 0.5}.get(self.scale.get(), 1.0)
            a.codec = {"Авто": "auto", "JPEG": "jpeg", "PNG": "png"}.get(self.codec.get(), "auto")
            a.engine = {"Видео H.264": "auto",
                        "Плитки (совместимость)": "tiles"}.get(self.engine.get(), "auto")
            if is_relay:
                a.relay = addr
            else:
                a.listen = int(addr)            # тут addr = порт
        else:
            if is_relay:
                a.relay = addr
            else:
                a.connect = addr
        return role, a

    def _persist(self):
        save_config({
            "role": self.role.get(), "conn": self.conn.get(),
            "address": self.address.get(), "room": self.room.get(),
            "password": self.password.get(), "downloads": self.downloads.get(),
            "quality": self.quality.get(), "fps": self.fps.get(), "scale": self.scale.get(),
            "codec": self.codec.get(), "engine": self.engine.get(),
        })

    # ---- Запуск ----
    def _start(self):
        try:
            role, args = self._build_args()
        except ValueError as e:
            messagebox.showwarning("Проверьте поля", str(e))
            return
        self._persist()

        if role == "host":
            self._run_host_window(args)
        else:
            # Клиент: закрываем форму и открываем pygame-окно в главном потоке.
            self.root.destroy()
            try:
                client.run_client(args)
            except Exception as e:
                # GUI уже закрыт — покажем отдельным окном
                err = tk.Tk(); err.withdraw()
                messagebox.showerror("Ошибка подключения", str(e))
                err.destroy()

    def _run_host_window(self, args):
        """Прячем форму, показываем окно-лог хоста."""
        for w in self.root.winfo_children():
            w.destroy()
        self.root.title("RemoteDesktop — хост (ожидание)")
        self.root.resizable(True, True)

        frm = ttk.Frame(self.root, padding=8)
        frm.grid(sticky="nsew")
        self.root.columnconfigure(0, weight=1); self.root.rowconfigure(0, weight=1)
        frm.columnconfigure(0, weight=1); frm.rowconfigure(1, weight=1)

        ttk.Label(frm, text="Хост запущен. Этим ПК можно управлять с клиента.",
                  font=("", 10, "bold")).grid(row=0, column=0, sticky="w")
        log = tk.Text(frm, height=16, width=68, state="disabled", wrap="word")
        log.grid(row=1, column=0, sticky="nsew", pady=6)
        sb = ttk.Scrollbar(frm, command=log.yview); sb.grid(row=1, column=1, sticky="ns")
        log.config(yscrollcommand=sb.set)

        def append(msg):
            log.config(state="normal")
            log.insert("end", str(msg).rstrip() + "\n")
            log.see("end")
            log.config(state="disabled")

        # host.LOG вызывается из рабочего потока -> обновляем UI через after()
        host.LOG = lambda *a: self.root.after(0, append, " ".join(str(x) for x in a))

        stop_event = threading.Event()
        threading.Thread(target=host.run_host, args=(args, stop_event), daemon=True).start()

        def on_close():
            stop_event.set()
            self.root.destroy()
        self.root.protocol("WM_DELETE_WINDOW", on_close)
        ttk.Button(frm, text="Остановить и выйти", command=on_close).grid(row=2, column=0, pady=4)


def _setup_logging():
    """В собранном --windowed exe stdout=None. Перенаправляем вывод и
    необработанные ошибки в лог-файл, чтобы окно не закрывалось «молча»."""
    if not getattr(sys, "frozen", False):
        return
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        f = open(os.path.join(APP_DIR, "app.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = f
    except Exception:
        pass

    def hook(exc_type, exc, tb):
        traceback.print_exception(exc_type, exc, tb)
        try:
            err = tk.Tk(); err.withdraw()
            messagebox.showerror("RemoteDesktop — ошибка", f"{exc}\n\nПодробности в app.log")
            err.destroy()
        except Exception:
            pass
    sys.excepthook = hook


def main():
    _setup_logging()
    root = tk.Tk()
    LauncherUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
