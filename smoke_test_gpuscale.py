"""
Smoke test for client-side GPU upscaling + source_scale + sharpening.

Pure logic only — runs with SDL_VIDEODRIVER=dummy and does NOT open a real GPU
window. Verifies:
  - config round-trip for display.source_scale / gpu_upscale / sharpen
  - host scale-combination math (host_scale * source_scale, clamped)
  - sharpen capability detection (moderngl > numpy > PIL > off; no-op at 0)
  - render-path decision picks CPU fallback when GPU is unavailable

Run with the project venv python: smoke_test_gpuscale.py
"""

import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

_failures = []


def check(name, cond):
    status = "OK  " if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        _failures.append(name)


# ── Host scale-combination math ───────────────────────────────────────────
import host

check("combine_scale 100% = no change", host.combine_scale(1.0, 100) == 1.0)
check("combine_scale 50% halves", abs(host.combine_scale(1.0, 50) - 0.5) < 1e-9)
check("combine_scale 75% of host 0.8",
      abs(host.combine_scale(0.8, 75) - 0.6) < 1e-9)
check("combine_scale never upscales above host",
      host.combine_scale(0.5, 200) == 0.5)
check("combine_scale never above 1.0",
      host.combine_scale(1.0, 300) == 1.0)
check("combine_scale clamps tiny to >=0.1",
      host.combine_scale(1.0, 1) == 0.1)
check("combine_scale bad pct -> no change",
      host.combine_scale(1.0, "junk") == 1.0)
check("combine_scale missing/zero pct -> no change",
      host.combine_scale(1.0, 0) == 1.0)

# ── Client render-path decision + sharpen detection ───────────────────────
import client

# render path: GPU only when enabled + accelerated backend + sdl2 available.
check("render gpu when all ok",
      client.choose_render_path(True, "direct3d11", True) == "gpu")
check("render cpu when disabled",
      client.choose_render_path(False, "direct3d11", True) == "cpu")
check("render cpu on software backend",
      client.choose_render_path(True, "software", True) == "cpu")
check("render cpu on none backend",
      client.choose_render_path(True, "none", True) == "cpu")
check("render cpu when sdl2 unavailable (GPU init would fail)",
      client.choose_render_path(True, "opengl", False) == "cpu")

# sharpen detection: off at 0; preference moderngl > numpy > PIL > off.
check("sharpen off at 0",
      client.detect_sharpen_backend(0, True, True, True) == "off")
check("sharpen off at negative",
      client.detect_sharpen_backend(-5, True, True, True) == "off")
check("sharpen prefers moderngl (gpu)",
      client.detect_sharpen_backend(50, True, True, True) == "gpu")
check("sharpen numpy when no moderngl",
      client.detect_sharpen_backend(50, False, True, True) == "cpu_numpy")
check("sharpen PIL when no moderngl/numpy",
      client.detect_sharpen_backend(50, False, False, True) == "cpu_pil")
check("sharpen off when nothing available",
      client.detect_sharpen_backend(50, False, False, False) == "off")

# sharpen no-op guarantee: passthrough surface at amount 0 / backend off.
import pygame
pygame.display.init()
surf = pygame.Surface((8, 8))
check("sharpen_surface_cpu no-op at 0 returns same object",
      client.sharpen_surface_cpu(surf, 0, "cpu_numpy") is surf)
check("sharpen_surface_cpu no-op when backend off",
      client.sharpen_surface_cpu(surf, 50, "off") is surf)
# When numpy present, a real sharpen returns a NEW surface of same size.
if client._numpy_available():
    out = client.sharpen_surface_cpu(surf, 50, "cpu_numpy")
    check("sharpen_surface_cpu numpy returns surface of same size",
          out.get_size() == (8, 8))

# ── Config round-trip ─────────────────────────────────────────────────────
from settings_config import config

for key, val in [("display.source_scale", 75),
                 ("display.gpu_upscale", False),
                 ("display.sharpen", 50)]:
    orig = config.get(key)
    config.set(key, val)
    got = config.get(key)
    config.set(key, orig)  # restore
    check(f"config round-trip {key}={val}", got == val)

# defaults present
check("default source_scale=100", config.get("display.source_scale") in (100, "100"))
check("default gpu_upscale True", config.get("display.gpu_upscale") in (True, "True"))
check("default sharpen 0", config.get("display.sharpen") in (0, "0"))

print()
if _failures:
    print(f"FAILED ({len(_failures)}): {_failures}")
    sys.exit(1)
print("ALL PASSED")
sys.exit(0)
