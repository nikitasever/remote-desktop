"""
Фокусный тест вытеснения старой сессии в DIRECT-режиме host'а (--listen).

Сценарий: поднимаем host в direct-режиме в фоне, подключаем клиента №1
(делает HELLO, получает SCREEN_INFO — сессия живая). Затем подключаем
клиента №2. Ожидаем: host вытесняет №1 (его сокет закрывается -> recv падает),
а №2 нормально получает SCREEN_INFO. Реальный ввод/курсор не трогаются.
"""
import socket
import struct
import threading
import time

import common
import host as host_mod

PORT = 5821
PASSWORD = "evicttest"


class Args:
    listen = PORT
    relay = None
    password = PASSWORD
    downloads = "."
    quality = 60
    fps = 10
    scale = 0.5
    codec = "auto"
    engine = "tiles"   # без видео — быстрее и без энкодера


ok = []
def check(name, cond):
    ok.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")


def connect_client():
    """Подключается, шлёт HELLO, читает первый кадр (ждёт SCREEN_INFO).
    Возвращает (sock, chan, msg_type) первого принятого сообщения."""
    s = socket.create_connection(("127.0.0.1", PORT), timeout=5)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    chan = common.SecureChannel(common.derive_key(PASSWORD))
    common.send_frame(s, chan, common.MSG_HELLO, b'{"video": false}')
    mt, body = common.recv_frame(s, chan)
    return s, chan, mt


def main():
    host_mod.LOG = lambda *a, **k: None   # тише в логах
    stop = threading.Event()
    t = threading.Thread(target=host_mod.run_host, args=(Args(), stop), daemon=True)
    t.start()
    time.sleep(1.0)  # дать host'у забиндиться и слушать

    # Клиент №1
    s1, chan1, mt1 = connect_client()
    check("клиент №1: получил SCREEN_INFO", mt1 == common.MSG_SCREEN_INFO)
    time.sleep(0.5)

    # Клиент №2 — должен вытеснить №1
    s2, chan2, mt2 = connect_client()
    check("клиент №2: получил SCREEN_INFO (перехватил сессию)", mt2 == common.MSG_SCREEN_INFO)

    # Теперь сокет №1 должен быть закрыт host'ом (вытеснен)
    s1_dead = False
    try:
        s1.settimeout(5)
        # recv_frame должен упасть/вернуть EOF, т.к. host закрыл соединение
        common.recv_frame(s1, chan1)
    except (ConnectionError, socket.error, OSError, EOFError, struct.error):
        s1_dead = True
    except Exception:
        s1_dead = True
    check("клиент №1: соединение закрыто host'ом (вытеснен)", s1_dead)

    # Клиент №2 всё ещё живой — должен прийти ещё кадр (tiles или повторный info)
    s2_alive = False
    try:
        s2.settimeout(5)
        common.recv_frame(s2, chan2)
        s2_alive = True
    except Exception:
        s2_alive = False
    check("клиент №2: сессия активна (приходят кадры)", s2_alive)

    for s in (s1, s2):
        try:
            s.close()
        except OSError:
            pass
    stop.set()
    time.sleep(0.5)

    total, passed = len(ok), sum(1 for x in ok if x)
    print(f"\nИТОГ (вытеснение direct): {passed}/{total} проверок пройдено")
    raise SystemExit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
