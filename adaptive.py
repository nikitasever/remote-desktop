"""
Adaptive quality controller for WebRTC host.

Pure state-machine that decides video scale and FPS cap based on periodic
network stats (RTT, fraction lost, estimated throughput).  Designed to be
easy to unit-test: feed stats, read decisions, no I/O.

Knobs used instead of a bitrate API (aiortc lacks one):
  - target_scale: 1.0 / 0.75 / 0.5  (frame resolution multiplier)
  - fps_cap:      base_fps down to base_fps // 2

Hysteresis: after a DOWNGRADE, at least ``upgrade_hold`` consecutive "good"
readings are required before stepping back up.  This prevents oscillation
when conditions hover near a threshold.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional, Sequence, Tuple

# ---------- quality tiers (ordered best -> worst) ----------

SCALE_TIERS: Sequence[float] = (1.0, 0.75, 0.5)
"""Index 0 = best quality, last = lowest."""


@dataclass
class Stats:
    """Network snapshot fed to the controller."""
    rtt_ms: float = 0.0
    fraction_lost: float = 0.0        # 0..1
    throughput_kbps: float = 0.0       # estimated, 0 = unknown


@dataclass
class Decision:
    """Output of the controller: what ScreenTrack should use."""
    scale: float = 1.0
    fps_cap: int = 30


# ---------- thresholds ----------

@dataclass
class Thresholds:
    """Tuneable limits.  Defaults are conservative starting points."""
    rtt_bad_ms: float = 200.0          # RTT above this -> consider downgrade
    rtt_good_ms: float = 80.0          # RTT below this -> consider upgrade
    loss_bad: float = 0.05             # >5 % packet loss -> downgrade
    loss_good: float = 0.01            # <1 % -> upgrade candidate
    upgrade_hold: int = 4              # consecutive "good" readings before up
    interval_s: float = 2.0            # how often controller runs


# ---------- controller ----------

class QualityController:
    """Closed-loop controller (pure state machine, no I/O)."""

    def __init__(self, base_fps: int = 30, thresholds: Optional[Thresholds] = None):
        self.base_fps = max(1, base_fps)
        self.th = thresholds or Thresholds()
        self._tier_idx: int = 0                # index into SCALE_TIERS
        self._fps_reduced: bool = False
        self._good_streak: int = 0             # consecutive "good" readings
        self._last_decision = Decision(scale=SCALE_TIERS[0], fps_cap=self.base_fps)

    # -- public API --

    @property
    def decision(self) -> Decision:
        return self._last_decision

    def update(self, stats: Stats) -> Decision:
        """Feed one stats sample; returns the new decision."""
        bad = self._is_bad(stats)
        good = self._is_good(stats)

        if bad:
            self._good_streak = 0
            self._step_down()
        elif good:
            self._good_streak += 1
            if self._good_streak >= self.th.upgrade_hold:
                self._step_up()
                # don't reset streak to 0 — allow continued upgrades
                # but cap it so it doesn't grow unbounded
                self._good_streak = self.th.upgrade_hold
        else:
            # neutral — slowly decay streak but don't reset it fully
            self._good_streak = max(0, self._good_streak - 1)

        self._last_decision = Decision(
            scale=SCALE_TIERS[self._tier_idx],
            fps_cap=self._current_fps(),
        )
        return self._last_decision

    # -- internals --

    def _is_bad(self, s: Stats) -> bool:
        return s.rtt_ms > self.th.rtt_bad_ms or s.fraction_lost > self.th.loss_bad

    def _is_good(self, s: Stats) -> bool:
        return s.rtt_ms < self.th.rtt_good_ms and s.fraction_lost < self.th.loss_good

    def _step_down(self):
        """Reduce quality one notch.  First cut FPS, then scale."""
        if not self._fps_reduced:
            self._fps_reduced = True
        elif self._tier_idx < len(SCALE_TIERS) - 1:
            self._tier_idx += 1

    def _step_up(self):
        """Improve quality one notch.  First raise scale, then FPS."""
        if self._tier_idx > 0:
            self._tier_idx -= 1
            self._good_streak = 0        # reset after upgrade
        elif self._fps_reduced:
            self._fps_reduced = False
            self._good_streak = 0

    def _current_fps(self) -> int:
        if self._fps_reduced:
            return max(1, self.base_fps // 2)
        return self.base_fps


# ---------- async helper for host_rtc integration ----------

async def run_quality_loop(
    pc,                       # RTCPeerConnection
    track: "ScreenTrack",     # our ScreenTrack with .target_scale / .fps setters
    controller: QualityController,
    stop: asyncio.Event,
):
    """Periodically reads pc.getStats(), feeds the controller, applies decisions
    to *track*.  Runs until *stop* is set."""
    interval = controller.th.interval_s
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            pass

        stats = await _extract_stats(pc)
        dec = controller.update(stats)
        track.target_scale = dec.scale
        track.fps = dec.fps_cap
        # recalculate pacing interval
        track._tb_val = 1.0 / max(1, dec.fps_cap)


async def _extract_stats(pc) -> Stats:
    """Pull RTT and loss from aiortc getStats (best-effort)."""
    rtt = 0.0
    lost = 0.0
    try:
        reports = await pc.getStats()
        for report in reports.values():
            # outbound-rtp has packetsSent / packetsLost in some builds;
            # candidate-pair has currentRoundTripTime.
            if hasattr(report, "type"):
                if report.type == "candidate-pair" and getattr(report, "currentRoundTripTime", None) is not None:
                    rtt = report.currentRoundTripTime * 1000.0   # sec -> ms
                if report.type == "outbound-rtp":
                    sent = getattr(report, "packetsSent", 0) or 0
                    lost_n = getattr(report, "packetsLost", 0) or 0
                    if sent > 0:
                        lost = lost_n / sent
    except Exception:
        pass
    return Stats(rtt_ms=rtt, fraction_lost=lost)
