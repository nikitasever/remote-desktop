"""
RELAY — запускается на сервере с публичным IP (VPS).
Сводит host и client по одинаковому ID комнаты и слепо пересылает байты
между ними. Пароль relay НЕ знает — поток зашифрован end-to-end.

Запуск:   python relay.py --port 5800

На VPS не забудьте открыть порт в фаерволе/security group.
Зависимостей нет — только стандартная библиотека.
"""

import argparse
import json
import socket
import threading

rooms = {}            # session -> {"host": sock, "client": sock}
rooms_lock = threading.Lock()


def read_line(sock):
    buf = bytearray()
    while True:
        ch = sock.recv(1)
        if not ch:
            raise ConnectionError("закрыто до регистрации")
        if ch == b"\n":
            break
        buf.extend(ch)
        if len(buf) > 4096:
            raise ConnectionError("слишком длинная регистрация")
    return buf.decode("utf-8")


def pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.close()
            except OSError:
                pass


def handle(sock, addr):
    try:
        reg = json.loads(read_line(sock))
        role, session = reg["role"], reg["session"]
    except Exception as e:
        print(f"[relay] {addr}: ошибка регистрации: {e}")
        sock.close()
        return

    with rooms_lock:
        room = rooms.setdefault(session, {})
        old = room.get(role)
        if old is not None:
            # старое (возможно мёртвое) подключение этой роли — вытесняем его,
            # чтобы повторный вход после обрыва не упирался в "роль занята".
            print(f"[relay] комната '{session}': роль {role} переподключается — закрываю старое")
            try:
                old.close()
            except OSError:
                pass
        room[role] = sock
        print(f"[relay] {addr} -> комната '{session}' как {role}")
        ready = "host" in room and "client" in room
        if ready:
            host_s, client_s = room["host"], room["client"]

    if not ready:
        return  # ждём вторую сторону; её поток поднимет пайпы

    # Уведомим обе стороны, что пара готова (строкой с \n — до шифрованного потока).
    try:
        host_s.sendall(b'{"event":"paired"}\n')
        client_s.sendall(b'{"event":"paired"}\n')
    except OSError:
        pass

    # keepalive: если одна сторона тихо исчезла (обрыв, сон ПК), пайп
    # отвалится за ~20с и комната освободится, а не зависнет.
    for s in (host_s, client_s):
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            continue
        if hasattr(socket, "SIO_KEEPALIVE_VALS"):       # Windows
            try:
                s.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 10000, 3000))
            except OSError:
                pass
        else:                                            # Linux/*nix (сервер relay)
            for opt, val in (("TCP_KEEPIDLE", 10), ("TCP_KEEPINTVL", 3), ("TCP_KEEPCNT", 3)):
                o = getattr(socket, opt, None)
                if o is not None:
                    try:
                        s.setsockopt(socket.IPPROTO_TCP, o, val)
                    except OSError:
                        pass

    print(f"[relay] комната '{session}' соединена, пересылаю трафик")
    t1 = threading.Thread(target=pipe, args=(host_s, client_s), daemon=True)
    t2 = threading.Thread(target=pipe, args=(client_s, host_s), daemon=True)
    t1.start(); t2.start()
    t1.join(); t2.join()

    with rooms_lock:
        # удаляем комнату, только если в ней всё ещё наша пара
        # (иначе могли уже переподключиться новые сокеты)
        cur = rooms.get(session)
        if cur and cur.get("host") is host_s and cur.get("client") is client_s:
            rooms.pop(session, None)
            print(f"[relay] комната '{session}' закрыта")


def main():
    ap = argparse.ArgumentParser(description="Relay-сервер для remote desktop")
    ap.add_argument("--port", type=int, default=5800)
    args = ap.parse_args()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", args.port))
    srv.listen(16)
    print(f"[relay] слушаю 0.0.0.0:{args.port}")
    try:
        while True:
            sock, addr = srv.accept()
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            threading.Thread(target=handle, args=(sock, addr), daemon=True).start()
    except KeyboardInterrupt:
        print("\n[relay] выход")


if __name__ == "__main__":
    main()
