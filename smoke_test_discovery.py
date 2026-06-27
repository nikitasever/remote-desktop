"""
Smoke test: запускаем два DiscoveryService на localhost и проверяем,
что они обнаруживают друг друга в течение нескольких секунд.
"""

import sys
import time

from lan_discovery import DiscoveryService


def main():
    # Используем нестандартный порт, чтобы не конфликтовать с продакшеном
    PORT = 15899

    svc_a = DiscoveryService(
        host_id="111111", host_name="PC-ALPHA",
        port=PORT, service_port=5800, version="1.0.0")
    svc_b = DiscoveryService(
        host_id="222222", host_name="PC-BETA",
        port=PORT, service_port=5801, version="1.0.0")

    svc_a.start()
    svc_b.start()

    print("Waiting for discovery (up to 12 seconds)...")

    a_found = False
    b_found = False

    for i in range(24):  # 24 × 0.5s = 12s max
        time.sleep(0.5)

        devs_a = svc_a.get_discovered()
        devs_b = svc_b.get_discovered()

        if not a_found and any(d.id == "222222" for d in devs_a):
            a_found = True
            dev = next(d for d in devs_a if d.id == "222222")
            print(f"  A found B: {dev.name} (is_online={dev.is_online})")

        if not b_found and any(d.id == "111111" for d in devs_b):
            b_found = True
            dev = next(d for d in devs_b if d.id == "111111")
            print(f"  B found A: {dev.name} (is_online={dev.is_online})")

        if a_found and b_found:
            break

    svc_a.stop()
    svc_b.stop()

    # --- Verify self-filtering ---
    devs_a = svc_a.get_discovered()
    assert not any(d.id == "111111" for d in devs_a), "A should not see itself"

    if a_found and b_found:
        print("\n[PASS] Both instances discovered each other.")
        return 0
    else:
        print(f"\n[FAIL] a_found={a_found}, b_found={b_found}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
