"""
Smoke test for display optimization + render-backend settings.

Tests pure helpers WITHOUT opening a real window:
  - render_backend / render_16bit / scaling config round-trip via settings_config
  - apply_render_backend sets correct SDL hints into a fake env dict
  - compute_fit_rect aspect-ratio letterbox math for several cases
  - ScaledFrameCache returns the SAME object when inputs are unchanged

Run:  .\\.venv\\Scripts\\python.exe smoke_test_display.py
"""

import os
import sys

# Force a dummy SDL driver so importing client (which imports pygame) is safe
# and never tries to open a real window during this test.
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import client  # noqa: E402


def check(cond, msg):
    if not cond:
        print(f"  FAIL: {msg}")
        raise AssertionError(msg)
    print(f"  ok: {msg}")


def test_config_roundtrip():
    print("[1] settings_config round-trip")
    from settings_config import config
    # Save originals to restore.
    orig = {k: config.get(k) for k in
            ("render_backend", "render_16bit", "display.fit_mode",
             "display.smooth_scale")}
    try:
        config.set("render_backend", "opengl")
        config.set("render_16bit", True)
        config.set("display.fit_mode", "actual")
        config.set("display.smooth_scale", False)
        check(config.get("render_backend") == "opengl", "render_backend persists")
        check(config.get("render_16bit") is True, "render_16bit persists")
        check(config.get("display.fit_mode") == "actual", "fit_mode persists")
        check(config.get("display.smooth_scale") is False, "smooth_scale persists")
        # Re-read from a fresh instance to prove it hit disk.
        import importlib
        import settings_config as sc
        fresh = sc.SettingsConfig()
        check(fresh.get("render_backend") == "opengl", "render_backend survives reload")
        check(fresh.get("render_16bit") is True, "render_16bit survives reload")
    finally:
        for k, v in orig.items():
            if v is None:
                config.reset(k)
            else:
                config.set(k, v)


def test_apply_backend():
    print("[2] apply_render_backend hints")
    env = {}
    r = client.apply_render_backend(env, "direct3d11", False)
    check(env["SDL_RENDERDRIVER"] == "direct3d11", "direct3d11 -> direct3d11 hint")
    check(r["accelerated"] is True, "direct3d11 accelerated")
    check(r["depth"] == 0, "no 16-bit -> depth 0")

    env = {}
    r = client.apply_render_backend(env, "opengl", True)
    check(env["SDL_RENDERDRIVER"] == "opengl", "opengl -> opengl hint")
    check(r["depth"] == 16, "16-bit -> depth 16")

    env = {}
    r = client.apply_render_backend(env, "software", False)
    check(env["SDL_RENDERDRIVER"] == "software", "software -> software hint")
    check(r["accelerated"] is False, "software not accelerated")

    env = {}
    r = client.apply_render_backend(env, "none", False)
    check(env["SDL_RENDERDRIVER"] == "software", "none -> software hint")
    check(env.get("SDL_FRAMEBUFFER_ACCELERATION") == "0", "none disables fb accel")
    check(r["accelerated"] is False, "none not accelerated")

    env = {}
    r = client.apply_render_backend(env, "bogus", False)
    check(env["SDL_RENDERDRIVER"] == "direct3d11", "unknown -> default direct3d11")


def test_fit_rect():
    print("[3] compute_fit_rect letterbox math")
    # Same size -> exact fill, no offset.
    check(client.compute_fit_rect(800, 600, 800, 600) == (0, 0, 800, 600),
          "identical size fills exactly")
    # Wider window than frame aspect -> letterbox left/right (pillarbox).
    # frame 800x600 (4:3) into 1000x600 window: scale=1.0 -> 800x600 centered x.
    check(client.compute_fit_rect(800, 600, 1000, 600) == (100, 0, 800, 600),
          "pillarbox when window wider")
    # Taller window -> letterbox top/bottom.
    # 800x600 into 800x800: scale=1.0 -> 800x600, y offset 100.
    check(client.compute_fit_rect(800, 600, 800, 800) == (0, 100, 800, 600),
          "letterbox when window taller")
    # Downscale 1920x1080 into 960x540: scale=0.5 -> 960x540 exact.
    check(client.compute_fit_rect(1920, 1080, 960, 540) == (0, 0, 960, 540),
          "16:9 downscale exact")
    # 1920x1080 into 960x600: limited by width -> 960x540, y=30.
    check(client.compute_fit_rect(1920, 1080, 960, 600) == (0, 30, 960, 540),
          "16:9 into 16:10 letterboxed")
    # actual mode: 1:1 centered.
    check(client.compute_fit_rect(400, 300, 800, 600, "actual") == (200, 150, 400, 300),
          "actual mode centers 1:1")
    # Degenerate inputs.
    check(client.compute_fit_rect(0, 0, 800, 600) == (0, 0, 0, 0),
          "zero src -> empty rect")


class _FakeSurface:
    def __init__(self, size):
        self._size = size

    def get_size(self):
        return self._size


def test_scaled_cache():
    print("[4] ScaledFrameCache identity")
    cache = client.ScaledFrameCache()
    src = _FakeSurface((800, 600))

    calls = {"n": 0}

    def fake_smooth(surface, size):
        calls["n"] += 1
        return _FakeSurface(size)

    # First call: changed=True, scales once.
    s1, changed1 = cache.get(src, (400, 300), True, smooth_fn=fake_smooth)
    check(changed1 is True, "first call reports changed")
    check(calls["n"] == 1, "first call scales once")

    # Same inputs: same object, changed=False, no re-scale.
    s2, changed2 = cache.get(src, (400, 300), True, smooth_fn=fake_smooth)
    check(s2 is s1, "same inputs -> SAME object")
    check(changed2 is False, "same inputs report unchanged")
    check(calls["n"] == 1, "same inputs do not re-scale")

    # Different size: re-scales.
    s3, changed3 = cache.get(src, (200, 150), True, smooth_fn=fake_smooth)
    check(changed3 is True, "new size reports changed")
    check(calls["n"] == 2, "new size re-scales")

    # Dest size == src size: returns src untouched, no scale fn called.
    s4, changed4 = cache.get(src, (800, 600), True, smooth_fn=fake_smooth)
    check(s4 is src, "dst==src returns source surface")
    check(calls["n"] == 2, "dst==src does not call scale fn")


def main():
    tests = [test_config_roundtrip, test_apply_backend,
             test_fit_rect, test_scaled_cache]
    for t in tests:
        t()
    print("\nALL DISPLAY SMOKE TESTS PASSED")


if __name__ == "__main__":
    try:
        main()
    except AssertionError:
        sys.exit(1)
