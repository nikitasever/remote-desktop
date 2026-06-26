"""
Headless smoke-test for build_ice_config (NAT traversal helpers).

Validates:
1. Default config has the expected STUN server.
2. Custom STUN list is respected.
3. Empty STUN list disables STUN.
4. TURN server with credentials produces the correct RTCIceServer.
5. Combined STUN + TURN works.
"""

import sys
import os

# Ensure the worktree directory is on sys.path so imports resolve.
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from rtc_common import build_ice_config, DEFAULT_STUN

ok = []


def check(name, cond):
    ok.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")


def main():
    # 1. Defaults -> one STUN server entry with DEFAULT_STUN urls
    cfg = build_ice_config()
    check("default config has iceServers", len(cfg.iceServers) == 1)
    check("default STUN urls match", cfg.iceServers[0].urls == DEFAULT_STUN)

    # 2. Custom STUN list
    custom = ["stun:stun1.example.com:3478", "stun:stun2.example.com:3478"]
    cfg2 = build_ice_config(stun_urls=custom)
    check("custom STUN urls", cfg2.iceServers[0].urls == custom)

    # 3. Empty STUN list -> no STUN server (can still have TURN)
    cfg3 = build_ice_config(stun_urls=[])
    check("empty STUN -> no iceServers", len(cfg3.iceServers) == 0)

    # 4. TURN with credentials
    cfg4 = build_ice_config(
        stun_urls=[],
        turn_url="turn:vps.example.com:3478",
        turn_user="alice",
        turn_pass="s3cret",
    )
    check("TURN-only config has 1 server", len(cfg4.iceServers) == 1)
    srv = cfg4.iceServers[0]
    check("TURN url correct", srv.urls == ["turn:vps.example.com:3478"])
    check("TURN username", srv.username == "alice")
    check("TURN credential", srv.credential == "s3cret")

    # 5. STUN + TURN combined
    cfg5 = build_ice_config(
        stun_urls=["stun:stun.l.google.com:19302"],
        turn_url="turn:relay.example.com:3478",
        turn_user="bob",
        turn_pass="pw",
    )
    check("STUN+TURN -> 2 servers", len(cfg5.iceServers) == 2)
    check("first is STUN", cfg5.iceServers[0].urls == ["stun:stun.l.google.com:19302"])
    check("second is TURN", cfg5.iceServers[1].urls == ["turn:relay.example.com:3478"])
    check("TURN creds present", cfg5.iceServers[1].username == "bob")

    total = len(ok)
    passed = sum(ok)
    print(f"\nИТОГ (ICE config): {passed}/{total} проверок пройдено")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
