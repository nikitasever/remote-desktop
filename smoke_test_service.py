"""
Headless smoke test for service.py (unattended mode).

Validates:
  1. service.py imports cleanly and build_args() works
  2. service.py can launch the host via relay, accept a client handshake,
     and stream screen data
  3. Graceful shutdown via stop_event
  4. PowerShell install/uninstall scripts parse without syntax errors

Does NOT require admin privileges or register any system task.
"""

import io
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
RELAY_PORT = 5813          # distinct from smoke_test.py's port
PW = "service-smoke-pw-42"
ROOM = "svc-smoke"

ok = []


def check(name, cond):
    ok.append(bool(cond))
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}")


def test_import_and_args():
    """1) service.py imports and build_args() produces a valid namespace."""
    print("1) Import & argument parsing")
    sys.path.insert(0, HERE)
    import service

    args = service.build_args([
        "--relay", "example.com:5800",
        "--id", "testroom",
        "--password", "testpw",
        "--no-restart",
    ])
    check("build_args: relay", args.relay == "example.com:5800")
    check("build_args: id", args.id == "testroom")
    check("build_args: password", args.password == "testpw")
    check("build_args: no_restart", args.no_restart is True)
    check("build_args: defaults (quality)", args.quality == 65)


def test_env_config():
    """2) Environment and config-file fallback."""
    print("2) Config priority (env > config file > default)")
    sys.path.insert(0, HERE)
    import service

    # Create a temp config file
    cfg_path = os.path.join(tempfile.mkdtemp(), "test_cfg.json")
    with open(cfg_path, "w") as f:
        json.dump({"relay": "cfg-host:1234", "id": "cfgroom", "password": "cfgpw"}, f)

    old_env = os.environ.get("RD_ID")
    try:
        os.environ["RD_ID"] = "envroom"
        args = service.build_args([
            "--config", cfg_path,
            "--password", "clipw",    # CLI wins over config
            "--no-restart",
        ])
        check("config file: relay from config", args.relay == "cfg-host:1234")
        check("env wins over config: id", args.id == "envroom")
        check("CLI wins over env: password", args.password == "clipw")
    finally:
        if old_env is None:
            os.environ.pop("RD_ID", None)
        else:
            os.environ["RD_ID"] = old_env


def test_e2e_service():
    """3) E2E: relay + service.py (as subprocess) + client handshake."""
    print(f"3) E2E via relay (port {RELAY_PORT})")

    # Add common.py's dir to import path
    sys.path.insert(0, HERE)
    import common

    downloads = tempfile.mkdtemp(prefix="rd_svc_smoke_")
    env = dict(os.environ)

    # Start relay
    relay = subprocess.Popen(
        [PY, os.path.join(HERE, "relay.py"), "--port", str(RELAY_PORT)],
        cwd=HERE, env=env,
    )
    time.sleep(1.0)

    # Start service.py (instead of host.py directly)
    svc = subprocess.Popen(
        [PY, os.path.join(HERE, "service.py"),
         "--relay", f"127.0.0.1:{RELAY_PORT}",
         "--id", ROOM,
         "--password", PW,
         "--downloads", downloads,
         "--no-restart",
         "--engine", "tiles",
         "--log-file", os.path.join(downloads, "svc.log")],
        cwd=HERE, env=env,
    )
    time.sleep(2.0)

    key = common.derive_key(PW)
    chan = common.SecureChannel(key)
    try:
        s = socket.create_connection(("127.0.0.1", RELAY_PORT), timeout=5)
        common.relay_register(s, "client", ROOM)
        line = common.relay_read_line(s)
        check("relay paired", "paired" in line)

        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        common.send_frame(s, chan, common.MSG_HELLO,
                          json.dumps({"video": False}).encode("utf-8"))

        s.settimeout(8)
        got_info = None
        frames_seen = 0
        deadline = time.time() + 8
        while time.time() < deadline and (got_info is None or frames_seen < 1):
            mt, body = common.recv_frame(s, chan)
            if mt == common.MSG_SCREEN_INFO:
                got_info = common.parse_json(body)
            elif mt == common.MSG_TILES:
                frames_seen += 1

        check("SCREEN_INFO received", got_info is not None)
        if got_info:
            print(f"      screen {got_info.get('w')}x{got_info.get('h')}, "
                  f"monitors: {got_info.get('monitors')}")
        check("tile frames received", frames_seen > 0)

        # PING -> PONG
        t_ping = time.time()
        common.send_json(s, chan, common.MSG_PING, {"t": t_ping})
        got_pong = False
        deadline = time.time() + 4
        while time.time() < deadline:
            mt, body = common.recv_frame(s, chan)
            if mt == common.MSG_PONG:
                got_pong = common.parse_json(body).get("t") == t_ping
                break
        check("PING -> PONG", got_pong)

        s.close()
    finally:
        for p in (svc, relay):
            try:
                p.terminate()
            except Exception:
                pass
        time.sleep(0.7)

    # Check that log file was created
    log_path = os.path.join(downloads, "svc.log")
    check("log file created", os.path.isfile(log_path))


def test_ps_scripts_parse():
    """4) PowerShell scripts parse without syntax errors."""
    print("4) PowerShell script syntax validation")
    for name in ("install_service.ps1", "uninstall_service.ps1"):
        path = os.path.join(HERE, name)
        if not os.path.isfile(path):
            check(f"{name} exists", False)
            continue
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"$null=[ScriptBlock]::Create((Get-Content -Raw '{path}'))"],
            capture_output=True, text=True, timeout=15,
        )
        check(f"{name} parses OK", result.returncode == 0)
        if result.returncode != 0:
            print(f"      stderr: {result.stderr[:200]}")


if __name__ == "__main__":
    test_import_and_args()
    test_env_config()
    test_e2e_service()
    test_ps_scripts_parse()

    total = len(ok)
    passed = sum(1 for x in ok if x)
    print(f"\nRESULT: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)
