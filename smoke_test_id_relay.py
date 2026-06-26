"""
Smoke-тест нового ID-based протокола relay (REGISTER/CONNECT).
Проверяет:
  1. REGISTER + CONNECT -> OK, двусторонний пайп работает
  2. CONNECT к несуществующему ID -> ERROR not_found
  3. Preemption: повторный REGISTER с тем же ID вытесняет старый хост
  4. Обратная совместимость: старый JSON-протокол по-прежнему работает
"""

import json
import os
import socket
import sys
import threading
import time

# Запускаем relay in-process
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import relay

PORT = 5819  # выбираем порт, не конфликтующий с другими тестами

ok = []


def check(name, cond):
    ok.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")


def start_relay():
    """Запускаем relay-сервер в отдельном потоке."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", PORT))
    srv.listen(16)
    srv.settimeout(0.5)

    def accept_loop():
        while not stop_event.is_set():
            try:
                sock, addr = srv.accept()
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                threading.Thread(target=relay.handle, args=(sock, addr), daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break
        srv.close()

    stop_event = threading.Event()
    t = threading.Thread(target=accept_loop, daemon=True)
    t.start()
    time.sleep(0.2)  # дать серверу подняться
    return stop_event


def connect_to_relay():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(("127.0.0.1", PORT))
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return s


def send_line(sock, line):
    sock.sendall((line + "\n").encode())


def recv_line(sock, timeout=5):
    sock.settimeout(timeout)
    buf = b""
    while not buf.endswith(b"\n"):
        ch = sock.recv(1)
        if not ch:
            raise ConnectionError("closed")
        buf += ch
    sock.settimeout(None)
    return buf.decode().strip()


def test_register_connect():
    """Тест 1: REGISTER + CONNECT, двусторонний пайп."""
    print("\n1) REGISTER + CONNECT -> bidirectional pipe")

    host = connect_to_relay()
    send_line(host, "REGISTER 123456789")
    resp = recv_line(host)
    check("host REGISTER -> OK", resp == "OK")

    client = connect_to_relay()
    send_line(client, "CONNECT 123456789")
    resp = recv_line(client)
    check("client CONNECT -> OK", resp == "OK")

    time.sleep(0.2)  # дать пайпу подняться

    # client -> host
    client.sendall(b"hello from client")
    host.settimeout(3)
    data = host.recv(1024)
    check("client -> host data", data == b"hello from client")

    # host -> client
    host.sendall(b"hello from host")
    client.settimeout(3)
    data = client.recv(1024)
    check("host -> client data", data == b"hello from host")

    client.close()
    host.close()
    time.sleep(0.3)


def test_connect_not_found():
    """Тест 2: CONNECT к несуществующему ID."""
    print("\n2) CONNECT to non-existent ID -> ERROR not_found")

    client = connect_to_relay()
    send_line(client, "CONNECT 999999999")
    resp = recv_line(client)
    check("CONNECT non-existent -> ERROR not_found", resp == "ERROR not_found")
    # сокет должен быть закрыт relay
    time.sleep(0.2)
    try:
        data = client.recv(1)
        check("socket closed after error", data == b"")
    except OSError:
        check("socket closed after error", True)
    client.close()


def test_preemption():
    """Тест 3: повторный REGISTER с тем же ID вытесняет старый хост."""
    print("\n3) Preemption: second REGISTER replaces first")

    host1 = connect_to_relay()
    send_line(host1, "REGISTER 111222333")
    resp = recv_line(host1)
    check("host1 REGISTER -> OK", resp == "OK")

    time.sleep(0.1)

    host2 = connect_to_relay()
    send_line(host2, "REGISTER 111222333")
    resp = recv_line(host2)
    check("host2 REGISTER -> OK (preempted host1)", resp == "OK")

    time.sleep(0.3)

    # host1 должен быть закрыт
    host1.settimeout(1)
    try:
        data = host1.recv(1)
        check("host1 socket closed after preemption", data == b"")
    except OSError:
        check("host1 socket closed after preemption", True)

    # Клиент подключается к новому хосту
    client = connect_to_relay()
    send_line(client, "CONNECT 111222333")
    resp = recv_line(client)
    check("client CONNECT after preemption -> OK", resp == "OK")

    time.sleep(0.2)

    client.sendall(b"data to host2")
    host2.settimeout(3)
    data = host2.recv(1024)
    check("client -> host2 data works", data == b"data to host2")

    client.close()
    host2.close()
    time.sleep(0.3)


def test_backward_compat():
    """Тест 4: старый JSON-протокол по-прежнему работает."""
    print("\n4) Backward compatibility: old JSON protocol")

    host = connect_to_relay()
    client = connect_to_relay()

    # Старый протокол: JSON-регистрация
    host.sendall(json.dumps({"role": "host", "session": "smoke-compat"}).encode() + b"\n")
    client.sendall(json.dumps({"role": "client", "session": "smoke-compat"}).encode() + b"\n")

    # Ждём paired-уведомление
    host.settimeout(3)
    client.settimeout(3)
    host_paired = recv_line(host)
    client_paired = recv_line(client)
    check("host gets paired event", "paired" in host_paired)
    check("client gets paired event", "paired" in client_paired)

    # Проверяем пайп
    host.sendall(b"compat-host-data")
    data = client.recv(1024)
    check("old protocol: host -> client works", data == b"compat-host-data")

    client.sendall(b"compat-client-data")
    data = host.recv(1024)
    check("old protocol: client -> host works", data == b"compat-client-data")

    host.close()
    client.close()
    time.sleep(0.3)


def test_common_helpers():
    """Тест 5: common.relay_register_id / relay_connect_id."""
    print("\n5) common.relay_register_id / relay_connect_id helpers")
    import common

    host = connect_to_relay()
    resp = common.relay_register_id(host, "555666777")
    check("relay_register_id -> OK", resp == "OK")

    client = connect_to_relay()
    resp = common.relay_connect_id(client, "555666777")
    check("relay_connect_id -> OK", resp == "OK")

    time.sleep(0.2)
    client.sendall(b"helper-test")
    host.settimeout(3)
    data = host.recv(1024)
    check("pipe works via helpers", data == b"helper-test")

    # Not found
    client2 = connect_to_relay()
    resp = common.relay_connect_id(client2, "000000000")
    check("relay_connect_id not found", resp == "ERROR not_found")

    host.close()
    client.close()
    client2.close()
    time.sleep(0.3)


if __name__ == "__main__":
    print("=== Smoke-тест: ID-based relay protocol ===")
    stop = start_relay()

    try:
        test_register_connect()
        test_connect_not_found()
        test_preemption()
        test_backward_compat()
        test_common_helpers()
    finally:
        stop.set()
        time.sleep(0.3)

    passed = sum(ok)
    total = len(ok)
    print(f"\n{'=' * 40}")
    print(f"Результат: {passed}/{total} тестов прошло")
    if passed == total:
        print("ALL PASSED")
    else:
        print("SOME TESTS FAILED")
        sys.exit(1)
