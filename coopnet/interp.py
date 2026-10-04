"""Clock sync with the relay and the snapshot interpolation buffer.

Timeline: every snapshot carries ``sample_time`` = the sender's estimate of
the relay clock (ms) when the state was sampled. Receivers render remote
objects at

    render_time = relay_now - (fastest_transit + interp_delay)

where ``fastest_transit`` is the minimum observed (arrival - sample_time) over
the last few seconds (the network latency floor, including any residual
clock-offset error, which cancels out) and ``interp_delay`` adapts to jitter
inside [min_delay, max_delay]. The playout point slews at most 10 % of frame
time per frame, so delay changes never cause visible jumps.

Between two samples the position is a cubic Hermite spline using the sent
velocities; past the newest sample it is extrapolated with the last velocity
for at most ``max_extrapolate_ms`` and then held.
"""
from __future__ import annotations

import bisect
import math
from collections import deque


def _lerp(a, b, u):
    return a + (b - a) * u


def lerp_angle(a: float, b: float, u: float) -> float:
    delta = ((b - a + 180.0) % 360.0) - 180.0
    return (a + delta * u) % 360.0


class ClockSync:
    """NTP-style offset from TIME_REQ/TIME_RESP, using the lowest-RTT sample of a window."""

    WINDOW = 16
    STEP_THRESHOLD_MS = 50.0
    STEP_NOTIFY_MS = 20.0
    MAX_SLEW_MS = 2.0

    def __init__(self):
        self.samples = deque(maxlen=self.WINDOW)
        self.offset_ms = None
        self.rtt_ms = None

    def on_response(self, t0: float, t1: float, t2: float, t3: float) -> bool:
        """Adds one exchange. True when the offset stepped (receivers must reset their timing)."""
        previous = self.offset_ms
        rtt = max(0.0, (t3 - t0) - (t2 - t1))
        offset = ((t1 - t0) + (t2 - t3)) / 2.0
        self.samples.append((rtt, offset))
        best_rtt, best_offset = min(self.samples)
        warming_up = len(self.samples) < self.WINDOW
        if self.offset_ms is None or warming_up or abs(best_offset - self.offset_ms) > self.STEP_THRESHOLD_MS:
            self.offset_ms = best_offset
        else:
            correction = best_offset - self.offset_ms
            self.offset_ms += max(-self.MAX_SLEW_MS, min(self.MAX_SLEW_MS, correction))
        self.rtt_ms = best_rtt
        return previous is not None and abs(self.offset_ms - previous) > self.STEP_NOTIFY_MS

    @property
    def synced(self) -> bool:
        return len(self.samples) >= 3

    def relay_ms(self, local_ms: float) -> float:
        return local_ms + (self.offset_ms or 0.0)


class Sample:
    __slots__ = ("t", "pos", "vel", "yaw", "has_vel")

    def __init__(self, t, pos, vel, yaw, has_vel=True):
        self.t = t
        self.pos = pos
        self.vel = vel
        self.yaw = yaw
        self.has_vel = has_vel


class InterpBuffer:
    PLAYER = {"send_interval_ms": 1000.0 / 30.0, "min_delay_ms": 100.0, "max_delay_ms": 150.0}
    ENTITY = {"send_interval_ms": 100.0, "min_delay_ms": 150.0, "max_delay_ms": 200.0}
    SNAP_PLAYOUT_MS = 250.0

    def __init__(self, send_interval_ms: float, min_delay_ms: float, max_delay_ms: float,
                 max_extrapolate_ms: float = 250.0, history_ms: float = 2000.0, transit_window: int = 90):
        self.send_interval_ms = send_interval_ms
        self.min_delay_ms = min_delay_ms
        self.max_delay_ms = max_delay_ms
        self.max_extrapolate_ms = max_extrapolate_ms
        self.history_ms = history_ms
        self.samples: list[Sample] = []
        self.times: list[float] = []
        self.transit = deque(maxlen=transit_window)
        self.playout_ms = None
        self.counts = {"interpolated": 0, "extrapolated": 0, "held": 0, "early": 0, "late": 0, "teleports": 0}

    def push(self, t: float, arrival_relay_ms: float, pos, vel=(0.0, 0.0, 0.0), yaw: float = 0.0,
             teleported: bool = False, has_vel: bool = True) -> None:
        self.transit.append(arrival_relay_ms - t)
        if teleported:
            self.samples, self.times = [], []
            self.counts["teleports"] += 1
        if self.samples and t <= self.times[0]:
            self.counts["late"] += 1
            return
        index = bisect.bisect_left(self.times, t)
        if index < len(self.times) and self.times[index] == t:
            return
        self.times.insert(index, t)
        self.samples.insert(index, Sample(t, tuple(pos), tuple(vel), yaw, has_vel))
        horizon = self.times[-1] - self.history_ms
        while len(self.times) > 2 and self.times[0] < horizon:
            self.times.pop(0)
            self.samples.pop(0)

    def reset_timing(self) -> None:
        """Forget transit statistics, e.g. after the local relay-clock estimate stepped."""
        self.transit.clear()
        self.playout_ms = None

    def target_delay_ms(self) -> float:
        if not self.transit:
            return self.min_delay_ms
        ordered = sorted(self.transit)
        jitter = ordered[int(0.95 * (len(ordered) - 1))] - ordered[0]
        wanted = max(2.0 * self.send_interval_ms, jitter + self.send_interval_ms)
        return min(self.max_delay_ms, max(self.min_delay_ms, wanted))

    def render_time(self, relay_now_ms: float, frame_dt_ms: float) -> float:
        if not self.transit:
            return relay_now_ms - self.min_delay_ms
        target = min(self.transit) + self.target_delay_ms()
        if self.playout_ms is None or abs(target - self.playout_ms) > self.SNAP_PLAYOUT_MS:
            self.playout_ms = target
        else:
            step = 0.1 * frame_dt_ms
            self.playout_ms += max(-step, min(step, target - self.playout_ms))
        return relay_now_ms - self.playout_ms

    def sample_at(self, t: float):
        """Returns (pos, yaw, mode) or None when empty."""
        if not self.samples:
            return None
        first, last = self.samples[0], self.samples[-1]
        if t <= first.t:
            # Before the first sample (fresh spawn): back-extrapolate along the spawn velocity.
            # A game proxy should stay hidden until render time reaches its first sample.
            self.counts["early"] += 1
            behind_s = min(first.t - t, self.max_extrapolate_ms) / 1000.0
            pos = tuple(p - v * behind_s for p, v in zip(first.pos, first.vel)) if first.has_vel else first.pos
            return pos, first.yaw, "early"
        if t >= last.t:
            ahead_s = min(t - last.t, self.max_extrapolate_ms) / 1000.0
            if t - last.t > self.max_extrapolate_ms:
                self.counts["held"] += 1
                mode = "held"
            else:
                self.counts["extrapolated"] += 1
                mode = "extrapolated"
            pos = tuple(p + v * ahead_s for p, v in zip(last.pos, last.vel)) if last.has_vel else last.pos
            return pos, last.yaw, mode
        index = bisect.bisect_right(self.times, t) - 1
        a, b = self.samples[index], self.samples[index + 1]
        span_s = (b.t - a.t) / 1000.0
        u = (t - a.t) / (b.t - a.t)
        self.counts["interpolated"] += 1
        if a.has_vel and b.has_vel:
            u2, u3 = u * u, u * u * u
            h00, h10 = 2 * u3 - 3 * u2 + 1, u3 - 2 * u2 + u
            h01, h11 = -2 * u3 + 3 * u2, u3 - u2
            pos = tuple(h00 * pa + h10 * span_s * va + h01 * pb + h11 * span_s * vb
                        for pa, va, pb, vb in zip(a.pos, a.vel, b.pos, b.vel))
        else:
            pos = tuple(_lerp(pa, pb, u) for pa, pb in zip(a.pos, b.pos))
        return pos, lerp_angle(a.yaw, b.yaw, u), "interpolated"


class LatestSlotFollower:
    """Model of the v1 receive path: one latest-packet slot, per-frame exponential follow."""

    def __init__(self, follow_rate: float = 12.0):
        self.follow_rate = follow_rate
        self.latest_seq = None
        self.target = None
        self.pos = None

    def on_sample(self, seq: int, pos) -> None:
        if self.latest_seq is None or ((seq - self.latest_seq) % 65536) < 32768:
            self.latest_seq = seq
            self.target = tuple(pos)

    def frame(self, dt_s: float):
        if self.target is None:
            return None
        if self.pos is None:
            self.pos = self.target
        else:
            u = min(1.0, dt_s * self.follow_rate)
            self.pos = tuple(_lerp(p, q, u) for p, q in zip(self.pos, self.target))
        return self.pos


def distance(a, b) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))
