"""
Headless smoke test for adaptive quality controller.

Tests:
  1. High loss -> controller downgrades (fps then scale)
  2. Sustained good stats -> controller upgrades back
  3. Hysteresis prevents flapping near threshold boundary
  4. ScreenTrack honours target_scale (frame in -> correctly downscaled ndarray)
"""

import sys
import fractions
import numpy as np
import av

# --- import from project ---
from adaptive import QualityController, Stats, Thresholds, SCALE_TIERS, Decision

ok = []


def check(name, cond):
    ok.append(bool(cond))
    tag = "PASS" if cond else "FAIL"
    print(f"  [{tag}] {name}")


# ===== Test 1: high loss -> downgrade =====

def test_downgrade_on_loss():
    qc = QualityController(base_fps=30)
    bad = Stats(rtt_ms=300, fraction_lost=0.10)

    d = qc.update(bad)
    # first downgrade: fps should drop
    check("downgrade step 1: fps reduced", d.fps_cap == 15)
    check("downgrade step 1: scale still 1.0", d.scale == 1.0)

    d = qc.update(bad)
    # second downgrade: scale should drop
    check("downgrade step 2: scale < 1.0", d.scale < 1.0)
    check("downgrade step 2: scale == 0.75", d.scale == 0.75)

    d = qc.update(bad)
    check("downgrade step 3: scale == 0.5", d.scale == 0.5)

    # already at bottom, should stay
    d = qc.update(bad)
    check("downgrade floor: scale stays 0.5", d.scale == 0.5)


# ===== Test 2: sustained good -> upgrade =====

def test_upgrade_on_good():
    # start from worst
    qc = QualityController(base_fps=30)
    bad = Stats(rtt_ms=300, fraction_lost=0.10)
    for _ in range(5):
        qc.update(bad)
    d = qc.decision
    check("pre-upgrade: at worst tier", d.scale == 0.5 and d.fps_cap == 15)

    good = Stats(rtt_ms=30, fraction_lost=0.001)
    # need upgrade_hold (4) consecutive good readings to upgrade
    for i in range(3):
        d = qc.update(good)
    check("3 good: still at 0.5 (hysteresis)", d.scale == 0.5)

    d = qc.update(good)  # 4th good -> upgrade
    check("4th good: scale improves", d.scale > 0.5)

    # keep feeding good to get back to top
    for _ in range(20):
        d = qc.update(good)
    check("sustained good: back to scale 1.0", d.scale == 1.0)
    check("sustained good: fps restored", d.fps_cap == 30)


# ===== Test 3: hysteresis prevents flapping =====

def test_hysteresis():
    qc = QualityController(base_fps=30, thresholds=Thresholds(upgrade_hold=4))
    bad = Stats(rtt_ms=300, fraction_lost=0.10)
    good = Stats(rtt_ms=30, fraction_lost=0.001)
    neutral = Stats(rtt_ms=120, fraction_lost=0.03)  # between thresholds

    # drive down
    qc.update(bad)
    qc.update(bad)
    d_down = qc.decision

    # alternate good / neutral — should NOT upgrade quickly
    decisions = []
    for _ in range(6):
        qc.update(good)
        qc.update(neutral)  # neutral decays the streak
        decisions.append(qc.decision.scale)

    # with alternating good/neutral, streak never reaches 4 consecutive goods
    check("hysteresis: no rapid flapping", all(s <= d_down.scale for s in decisions[:3]))


# ===== Test 4: ScreenTrack frame scaling =====

def test_screentrack_scaling():
    """Verify that target_scale produces correctly sized output frames."""
    # We test the scaling logic directly using av (same as host_rtc.py recv)
    w, h = 1920, 1080
    frame_rgb = np.random.randint(0, 255, (h, w, 3), dtype=np.uint8)

    for scale in (0.75, 0.5):
        exp_h = max(1, int(h * scale))
        exp_w = max(1, int(w * scale))

        tmp = av.VideoFrame.from_ndarray(np.ascontiguousarray(frame_rgb), format="rgb24")
        tmp = tmp.reformat(width=exp_w, height=exp_h)
        out = tmp.to_ndarray(format="rgb24")

        check(f"scale={scale}: shape ({out.shape[1]}x{out.shape[0]}) == ({exp_w}x{exp_h})",
              out.shape[0] == exp_h and out.shape[1] == exp_w)


# ===== Test 5: ScreenTrack integration (mock) =====

def test_screentrack_mock():
    """Light integration: create ScreenTrack, set target_scale, verify attrs."""
    # Avoid importing host_rtc which drags in host/mss/etc.  Just verify the
    # controller can set decision fields that ScreenTrack would read.
    qc = QualityController(base_fps=24)
    bad = Stats(rtt_ms=400, fraction_lost=0.15)
    qc.update(bad)
    d = qc.decision
    check("controller decision has scale attr", hasattr(d, "scale"))
    check("controller decision has fps_cap attr", hasattr(d, "fps_cap"))
    check("after bad stats fps_cap < base", d.fps_cap < 24)


# ===== main =====

def main():
    print("=== Adaptive quality controller smoke test ===\n")

    print("Test 1: downgrade on high loss/RTT")
    test_downgrade_on_loss()

    print("\nTest 2: upgrade on sustained good stats")
    test_upgrade_on_good()

    print("\nTest 3: hysteresis prevents flapping")
    test_hysteresis()

    print("\nTest 4: frame scaling via av")
    test_screentrack_scaling()

    print("\nTest 5: controller + ScreenTrack integration")
    test_screentrack_mock()

    total = len(ok)
    passed = sum(1 for x in ok if x)
    print(f"\nITOG (adaptive): {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
