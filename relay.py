"""
RELAY — запускается на сервере с публичным IP (VPS).
Сводит host и client по одинаковому ID комнаты и слепо пересылает байты
между ними. Пароль relay НЕ знает — поток зашифрован end-to-end.

Поддерживает два протокола:
  1. Старый (JSON, обратная совместимость): {"role":"host","session":"..."}\n
  2. Новый (ID-based, как AnyDesk):
       Host:   REGISTER <9-digit-id>\n  -> OK\n
       Client: CONNECT <9-digit-id>\n  -> OK\n | ERROR not_found\n

Запуск:   python relay.py --port 5800

На VPS не забудьте открыть порт в фаерволе/security group.
Зависимостей нет — только стандартная библиотека.
"""

import argparse
import json
import socket
import threading
import time

# ---- Rate limiting / brute-force protection (tunable) ----
RL_WINDOW = 60          # окно отслеживания попыток, сек
RL_MAX_ATTEMPTS = 10    # > этого числа попыток в окне -> блокировка
RL_BASE_BLOCK = 30      # базовая длительность блокировки, сек
RL_MAX_BLOCK = 300      # максимум блокировки (cap), сек = 5 мин
MAX_CONN_PER_IP = 20    # максимум одновременных соединений с одного IP

# ip -> [timestamps] недавних попыток (CONNECT / room-регистраций)
_attempts = {}
# ip -> момент времени, до которого IP заблокирован
_blocked_until = {}
_rl_lock = threading.Lock()

# ip -> число активных соединений
_active_conns = {}
_active_lock = threading.Lock()


def _prune_attempts(ip, now):
    """Удаляет устаревшие метки времени. Вызывать под _rl_lock."""
    ts = _attempts.get(ip)
    if ts is None:
        return
    cutoff = now - RL_WINDOW
    fresh = [t for t in ts if t >= cutoff]
    if fresh:
        _attempts[ip] = fresh
    else:
        _attempts.pop(ip, None)


def check_rate_limit(ip):
    """Регистрирует попытку с данного IP и решает, не пора ли блокировать.
    Возвращает (allowed: bool, retry_after: int)."""
    now = time.time()
    with _rl_lock:
        # Периодически чистим словарь блокировок от истёкших записей
        expired = [k for k, v in _blocked_until.items() if v <= now]
        for k in expired:
            _blocked_until.pop(k, None)

        until = _blocked_until.get(ip)
        if until is not None and until > now:
            return False, int(until - now) + 1

        _prune_attempts(ip, now)
        attempts = _attempts.setdefault(ip, [])
        attempts.append(now)

        over = len(attempts) - RL_MAX_ATTEMPTS
        if over > 0:
            # Экспоненциальный backoff по степени превышения, с потолком
            block = min(RL_BASE_BLOCK * (2 ** (over - 1)), RL_MAX_BLOCK)
            _blocked_until[ip] = now + block
            return False, int(block)

        return True, 0


def reject_rate_limited(sock, addr, retry_after, what):
    ip = addr[0]
    print(f"[relay] RATE-LIMITED {ip} ({what}, retry_after={retry_after}s)")
    try:
        sock.sendall(b"ERROR rate_limited\n")
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


# ---- Old room-based protocol ----
rooms = {}            # session -> {"host": sock, "client": sock}
rooms_lock = threading.Lock()

# ---- New ID-based registry ----
registry = {}         # id_str -> {"sock": socket, "event": Event, "client": socket|None}
registry_lock = threading.Lock()


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


def enable_keepalive(sock):
    """Включает TCP keepalive на сокете."""
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except OSError:
        return
    if hasattr(socket, "SIO_KEEPALIVE_VALS"):       # Windows
        try:
            sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 10000, 3000))
        except OSError:
            pass
    else:                                            # Linux/*nix
        for opt, val in (("TCP_KEEPIDLE", 10), ("TCP_KEEPINTVL", 3), ("TCP_KEEPCNT", 3)):
            o = getattr(socket, opt, None)
            if o is not None:
                try:
                    sock.setsockopt(socket.IPPROTO_TCP, o, val)
                except OSError:
                    pass


def start_pipe(host_s, client_s, cleanup_cb=None):
    """Запускает двунаправленный пайп между двумя сокетами.
    Блокирует текущий поток до завершения. cleanup_cb вызывается после."""
    for s in (host_s, client_s):
        enable_keepalive(s)

    t1 = threading.Thread(target=pipe, args=(host_s, client_s), daemon=True)
    t2 = threading.Thread(target=pipe, args=(client_s, host_s), daemon=True)
    t1.start(); t2.start()
    t1.join(); t2.join()

    if cleanup_cb:
        cleanup_cb()


# ---- New ID-based protocol handlers ----

def handle_register(sock, addr, unique_id):
    """Host: REGISTER <id> — регистрирует хост и ждёт клиентов."""
    with registry_lock:
        old_entry = registry.get(unique_id)
        if old_entry is not None:
            # Вытесняем старый хост
            print(f"[relay] ID '{unique_id}': preempting old host")
            try:
                old_entry["sock"].close()
            except OSError:
                pass
            old_entry["event"].set()  # разбудить старый поток, чтобы он завершился

        entry = {"sock": sock, "event": threading.Event(), "client": None}
        registry[unique_id] = entry

    sock.sendall(b"OK\n")
    print(f"[relay] {addr} REGISTER '{unique_id}' -> OK")

    try:
        while True:
            # Ждём, пока клиент подключится
            entry["event"].wait()
            entry["event"].clear()

            # Проверяем, не вытеснили ли нас
            with registry_lock:
                current = registry.get(unique_id)
                if current is not entry:
                    print(f"[relay] {addr} ID '{unique_id}': preempted, exiting")
                    return

            client_sock = entry["client"]
            if client_sock is None:
                continue

            print(f"[relay] ID '{unique_id}' paired, piping traffic")

            def cleanup():
                entry["client"] = None
                print(f"[relay] ID '{unique_id}' client disconnected, host waiting")

            start_pipe(sock, client_sock, cleanup_cb=cleanup)

            # После отключения клиента проверяем, не вытеснили ли нас
            with registry_lock:
                current = registry.get(unique_id)
                if current is not entry:
                    return
                # Хост жив — ждём нового клиента (цикл продолжается)
    except (ConnectionError, OSError):
        pass
    finally:
        with registry_lock:
            current = registry.get(unique_id)
            if current is entry:
                del registry[unique_id]
                print(f"[relay] ID '{unique_id}' host disconnected, removed from registry")
        try:
            sock.close()
        except OSError:
            pass


def handle_connect(sock, addr, unique_id):
    """Client: CONNECT <id> — подключается к зарегистрированному хосту."""
    with registry_lock:
        entry = registry.get(unique_id)
        if entry is None:
            sock.sendall(b"ERROR not_found\n")
            print(f"[relay] {addr} CONNECT '{unique_id}' -> ERROR not_found")
            sock.close()
            return

        entry["client"] = sock

    sock.sendall(b"OK\n")
    print(f"[relay] {addr} CONNECT '{unique_id}' -> OK")

    # Разбудить хост-поток, чтобы он запустил пайп
    entry["event"].set()

    # Клиентский поток завершается здесь — пайп обслуживается хост-потоком


# ---- Old room-based protocol handler ----

def handle_room(sock, addr, role, session):
    """Старый протокол: JSON-регистрация по роли и комнате."""
    with rooms_lock:
        room = rooms.setdefault(session, {})
        old = room.get(role)
        if old is not None:
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

    # Уведомим обе стороны
    try:
        host_s.sendall(b'{"event":"paired"}\n')
        client_s.sendall(b'{"event":"paired"}\n')
    except OSError:
        pass

    print(f"[relay] комната '{session}' соединена, пересылаю трафик")

    def cleanup():
        with rooms_lock:
            cur = rooms.get(session)
            if cur and cur.get("host") is host_s and cur.get("client") is client_s:
                rooms.pop(session, None)
                print(f"[relay] комната '{session}' закрыта")

    start_pipe(host_s, client_s, cleanup_cb=cleanup)


# ---- Dispatcher ----

def _dispatch(sock, addr):
    try:
        line = read_line(sock)
    except Exception as e:
        print(f"[relay] {addr}: ошибка чтения первой строки: {e}")
        sock.close()
        return

    ip = addr[0]

    # New protocol: REGISTER <id> or CONNECT <id>
    if line.startswith("REGISTER "):
        # REGISTER — это host, не вектор brute-force пароля; не лимитируем по попыткам.
        unique_id = line[len("REGISTER "):]
        handle_register(sock, addr, unique_id)
        return

    if line.startswith("CONNECT "):
        allowed, retry_after = check_rate_limit(ip)
        if not allowed:
            reject_rate_limited(sock, addr, retry_after, "CONNECT")
            return
        unique_id = line[len("CONNECT "):]
        handle_connect(sock, addr, unique_id)
        return

    # Old protocol: JSON {"role": ..., "session": ...}
    try:
        reg = json.loads(line)
        role, session = reg["role"], reg["session"]
    except Exception as e:
        print(f"[relay] {addr}: ошибка регистрации: {e}")
        sock.close()
        return

    # Лимитируем клиентов и в старом протоколе (host-регистрации не штрафуем).
    if role != "host":
        allowed, retry_after = check_rate_limit(ip)
        if not allowed:
            reject_rate_limited(sock, addr, retry_after, "room")
            return

    handle_room(sock, addr, role, session)


def handle(sock, addr):
    """Обёртка диспетчера: учитывает лимит одновременных соединений с IP."""
    ip = addr[0]
    with _active_lock:
        count = _active_conns.get(ip, 0)
        if count >= MAX_CONN_PER_IP:
            print(f"[relay] RATE-LIMITED {ip} (concurrent>{MAX_CONN_PER_IP})")
            try:
                sock.sendall(b"ERROR rate_limited\n")
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
            return
        _active_conns[ip] = count + 1

    try:
        _dispatch(sock, addr)
    finally:
        with _active_lock:
            c = _active_conns.get(ip, 0) - 1
            if c > 0:
                _active_conns[ip] = c
            else:
                _active_conns.pop(ip, None)


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
