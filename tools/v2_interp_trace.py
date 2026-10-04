"""Trace replay between the C++ SnapshotBuffer (src/v2/SnapshotBuffer) and relay/coopnet/interp.py.

    python tools/v2_interp_trace.py run --exe build/Release/coopnet_v2_interp_tests.exe

1. Drives interp.py's InterpBuffer through the scenarios of the relay's tests/test_interp.py
   (a circle at 30 Hz with 120 ms transit, 40 ms jitter and 10 % loss; bounded extrapolation;
   teleport and late samples; the playout snap after reset_timing) and through random scenarios
   (both presets and odd configurations: jitter, loss, duplicates, reordering, route changes,
   teleports, samples without velocity, yaw wrapping through 0/360, timing resets, frame gaps long
   enough to hold, samples before the first and after the newest sample, large and negative
   timeline origins), and writes every call and its result to build/golden/interp_trace.txt.
2. Has coopnet_v2_interp_tests replay the calls on the C++ SnapshotBuffer ("trace <file>"). Every
   result must be equal to the last bit: buffer size, counters, oldest and newest sample time and
   transit count after each push; render times and playout points; target delays; sampled
   positions, yaw and mode.

Trace format, one record per line, fields separated by single spaces, floats as Python float.hex():

    SCENARIO <name>
    CONFIG <send_interval> <min_delay> <max_delay> <max_extrapolate> <history> <transit_window>
    PUSH <t> <arrival> <px> <py> <pz> <vx> <vy> <vz> <yaw> <teleported> <has_vel>
         <size> <late> <teleports> <transit> <oldest|-> <newest|->
    RESET
    DELAY <target_delay>
    RENDER <relay_now> <frame_dt> <render_time> <playout>
    SAMPLE <t> <none | mode px py pz yaw>
    COUNTS <interpolated> <extrapolated> <held> <early> <late> <teleports>
    END

The relay checkout is found through --relay, COOPNET_RELAY_DIR or the sibling folder ../relay.
Other subcommand: generate <out>.
"""
from __future__ import annotations

import argparse
import math
import os
import random
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
interp = None

COUNT_KEYS = ("interpolated", "extrapolated", "held", "early", "late", "teleports")


def load_relay(relay_dir: str) -> None:
    global interp
    if not os.path.isfile(os.path.join(relay_dir, "coopnet", "interp.py")):
        raise SystemExit(f"relay checkout not found at {relay_dir} (use --relay or COOPNET_RELAY_DIR)")
    sys.path.insert(0, relay_dir)
    from coopnet import interp as interp_module  # noqa: E402
    interp = interp_module


def default_relay() -> str:
    return os.environ.get("COOPNET_RELAY_DIR") or os.path.join(os.path.dirname(ROOT), "relay")


def fhex(value) -> str:
    return "-" if value is None else float(value).hex()


class TracedBuffer:
    """An InterpBuffer that writes every call and its result to the trace."""

    def __init__(self, lines: list, name: str, config: dict):
        self.lines = lines
        full = {"max_extrapolate_ms": 250.0, "history_ms": 2000.0, "transit_window": 90}
        full.update(config)
        self.buffer = interp.InterpBuffer(**full)
        lines.append(f"SCENARIO {name}")
        lines.append("CONFIG " + " ".join(fhex(full[key]) for key in (
            "send_interval_ms", "min_delay_ms", "max_delay_ms", "max_extrapolate_ms", "history_ms"))
            + f" {int(full['transit_window'])}")

    def push(self, t, arrival, pos, vel=(0.0, 0.0, 0.0), yaw=0.0, teleported=False, has_vel=True):
        buffer = self.buffer
        buffer.push(t, arrival, pos, vel, yaw, teleported=teleported, has_vel=has_vel)
        oldest = buffer.times[0] if buffer.times else None
        newest = buffer.times[-1] if buffer.times else None
        self.lines.append(" ".join(["PUSH", fhex(t), fhex(arrival)] + [fhex(v) for v in pos] + [fhex(v) for v in vel]
                                   + [fhex(yaw), str(int(teleported)), str(int(has_vel)), str(len(buffer.samples)),
                                      str(buffer.counts["late"]), str(buffer.counts["teleports"]),
                                      str(len(buffer.transit)), fhex(oldest), fhex(newest)]))

    def reset_timing(self):
        self.buffer.reset_timing()
        self.lines.append("RESET")

    def target_delay_ms(self):
        value = self.buffer.target_delay_ms()
        self.lines.append(f"DELAY {fhex(value)}")
        return value

    def render_time(self, now, dt):
        value = self.buffer.render_time(now, dt)
        self.lines.append(f"RENDER {fhex(now)} {fhex(dt)} {fhex(value)} {fhex(self.buffer.playout_ms)}")
        return value

    def sample_at(self, t):
        result = self.buffer.sample_at(t)
        if result is None:
            self.lines.append(f"SAMPLE {fhex(t)} none")
        else:
            pos, yaw, mode = result
            self.lines.append(f"SAMPLE {fhex(t)} {mode} " + " ".join(fhex(v) for v in pos) + f" {fhex(yaw)}")
        return result

    def end(self):
        counts = self.buffer.counts
        self.lines.append("COUNTS " + " ".join(str(counts[key]) for key in COUNT_KEYS))
        self.lines.append("END")


# ---------------------------------------------------------------------------
# the scenarios of relay/tests/test_interp.py
# ---------------------------------------------------------------------------

def circle_jitter_loss(lines):
    """test_interpolation_error_small_with_jitter_and_loss, call for call."""
    rng = random.Random(2)
    buffer = TracedBuffer(lines, "test_interp_circle_jitter_loss", interp.InterpBuffer.PLAYER)
    arrivals = []
    t = 0.0
    while t < 10 * 1000:
        if rng.random() >= 0.1:
            arrivals.append((t + 120 + rng.random() * 40, t))
        t += 1000 / 30
    arrivals.sort()
    radius, omega = 10.0, 0.6

    def truth(t_ms):
        angle = omega * t_ms / 1000
        return (radius * math.cos(angle), radius * math.sin(angle), 0.0)

    def velocity(t_ms):
        angle = omega * t_ms / 1000
        return (-radius * omega * math.sin(angle), radius * omega * math.cos(angle), 0.0)

    index = 0
    now = 0.0
    while now < 10000:
        while index < len(arrivals) and arrivals[index][0] <= now:
            sample_t = arrivals[index][1]
            buffer.push(sample_t, arrivals[index][0], truth(sample_t), velocity(sample_t))
            index += 1
        if buffer.buffer.samples and now > 1000:
            buffer.sample_at(buffer.render_time(now, 16.7))
        now += 16.7
    buffer.target_delay_ms()
    buffer.end()


def extrapolation_bounded(lines):
    buffer = TracedBuffer(lines, "test_interp_extrapolation_bounded", interp.InterpBuffer.PLAYER)
    buffer.push(0.0, 50.0, (0.0, 0.0, 0.0), (10.0, 0.0, 0.0))
    buffer.push(100.0, 150.0, (1.0, 0.0, 0.0), (10.0, 0.0, 0.0))
    buffer.sample_at(200.0)
    buffer.sample_at(1000.0)
    buffer.end()


def teleport_late(lines):
    buffer = TracedBuffer(lines, "test_interp_teleport_late", interp.InterpBuffer.PLAYER)
    for t in range(0, 300, 33):
        buffer.push(float(t), t + 50.0, (t / 100.0, 0.0, 0.0))
    buffer.push(300.0, 350.0, (500.0, 500.0, 0.0), teleported=True)
    buffer.push(10.0, 360.0, (0.0, 0.0, 0.0))
    buffer.end()


def playout_snap(lines):
    buffer = TracedBuffer(lines, "test_interp_playout_snap", interp.InterpBuffer.PLAYER)
    for t in range(0, 1000, 33):
        buffer.push(float(t), t + 60.0, (0.0, 0.0, 0.0))
    buffer.render_time(1100.0, 16.7)
    buffer.reset_timing()
    for t in range(1000, 2000, 33):
        buffer.push(float(t), t + 900.0, (0.0, 0.0, 0.0))
    buffer.render_time(2900.0, 16.7)
    buffer.end()


def edge_cases(lines):
    """Empty buffer, one sample, equal times, exact boundaries, a tiny transit window."""
    buffer = TracedBuffer(lines, "edges", {"send_interval_ms": 50.0, "min_delay_ms": 80.0, "max_delay_ms": 120.0,
                                           "max_extrapolate_ms": 100.0, "history_ms": 300.0, "transit_window": 3})
    buffer.sample_at(0.0)
    buffer.target_delay_ms()
    buffer.render_time(1000.0, 16.0)
    buffer.push(100.0, 140.0, (1.0, 2.0, 3.0), (1.0, 0.0, -1.0), 359.0)
    buffer.sample_at(100.0)       # equal to the only sample: early
    buffer.sample_at(150.0)
    buffer.sample_at(200.0)       # exactly max_extrapolate past: still extrapolated
    buffer.sample_at(200.0000001)
    buffer.push(100.0, 160.0, (9.0, 9.0, 9.0))   # same time as the only sample: late
    buffer.push(150.0, 190.0, (2.0, 2.0, 2.0), (0.0, 0.0, 0.0), 1.0)
    buffer.push(125.0, 200.0, (1.5, 2.5, 2.5), (1.0, 1.0, 1.0), 0.5)   # inserted in the middle
    buffer.push(125.0, 210.0, (7.0, 7.0, 7.0))   # duplicate time: ignored
    buffer.sample_at(125.0)
    buffer.sample_at(137.5)
    buffer.sample_at(150.0)
    buffer.push(400.0, 420.0, (3.0, 3.0, 3.0), (0.0, 0.0, 0.0), 180.0, has_vel=False)
    buffer.push(500.0, 530.0, (4.0, 3.0, 3.0), (0.0, 0.0, 0.0), 181.0, has_vel=False)
    buffer.sample_at(450.0)
    buffer.sample_at(560.0)
    buffer.target_delay_ms()
    for now, dt in ((600.0, 16.0), (616.0, 16.0), (632.0, 0.0), (900.0, 268.0)):
        buffer.sample_at(buffer.render_time(now, dt))
    buffer.reset_timing()
    buffer.target_delay_ms()
    buffer.render_time(950.0, 16.0)
    buffer.push(-5.0, 960.0, (0.0, 0.0, 0.0))
    buffer.push(700.0, 700.0, (5.0, 5.0, 5.0), (-1.0, 0.0, 0.0), 90.0, teleported=True)
    buffer.sample_at(650.0)
    buffer.sample_at(-1.0e9)
    buffer.end()


# ---------------------------------------------------------------------------
# random scenarios
# ---------------------------------------------------------------------------

def random_scenario(lines, name: str, seed: int, config: dict):
    rng = random.Random(seed)
    buffer = TracedBuffer(lines, name, config)
    interval = buffer.buffer.send_interval_ms
    base = rng.choice([0.0, 12345.678, 1.0e6, 4294960000.0, -7000.0])
    duration = rng.uniform(2500.0, 8000.0)
    transit = rng.uniform(0.0, 250.0)
    jitter = rng.choice([0.0, 3.0, 25.0, 60.0, 150.0])
    loss = rng.choice([0.0, 0.03, 0.15, 0.4])
    legacy_share = rng.choice([0.0, 0.0, 0.2, 1.0])
    pos = [rng.uniform(-500.0, 500.0), rng.uniform(-500.0, 500.0), rng.uniform(-20.0, 60.0)]
    vel = [rng.uniform(-8.0, 8.0), rng.uniform(-8.0, 8.0), rng.uniform(-1.0, 1.0)]
    yaw = rng.uniform(0.0, 360.0)
    events = []
    t = base
    while t < base + duration:
        step_s = interval / 1000.0
        if rng.random() < 0.05:
            vel = [rng.uniform(-12.0, 12.0), rng.uniform(-12.0, 12.0), rng.uniform(-2.0, 2.0)]
        pos = [p + v * step_s for p, v in zip(pos, vel)]
        yaw = (yaw + rng.uniform(-40.0, 40.0)) % 360.0
        teleported = rng.random() < 0.01
        if teleported:
            pos = [rng.uniform(-2000.0, 2000.0), rng.uniform(-2000.0, 2000.0), rng.uniform(-10.0, 100.0)]
        has_vel = rng.random() >= legacy_share
        if rng.random() >= loss:
            arrival = t + transit + rng.random() * jitter
            events.append((arrival, t, tuple(pos), tuple(vel), yaw, teleported, has_vel))
            if rng.random() < 0.03:
                events.append((arrival + rng.random() * 40.0, t, tuple(pos), tuple(vel), yaw, teleported, has_vel))
        if rng.random() < 0.01:
            transit = max(0.0, transit + rng.uniform(-60.0, 300.0))
        t += interval * rng.uniform(0.6, 1.4)
    events.sort(key=lambda event: event[0])
    now = base
    index = 0
    end = (events[-1][0] if events else base) + 800.0
    while now < end:
        dt = rng.choice([16.7, 16.7, 8.3, 33.3, 6.9, rng.uniform(0.0, 60.0), 0.0])
        if rng.random() < 0.01:
            dt = rng.uniform(200.0, 700.0)
        now += dt
        while index < len(events) and events[index][0] <= now:
            arrival, sample_t, p, v, y, teleported, has_vel = events[index]
            buffer.push(sample_t, arrival, p, v, y, teleported=teleported, has_vel=has_vel)
            index += 1
        if rng.random() < 0.004:
            buffer.reset_timing()
        if rng.random() < 0.05:
            buffer.target_delay_ms()
        if buffer.buffer.samples or rng.random() < 0.05:
            buffer.sample_at(buffer.render_time(now, dt))
        if buffer.buffer.samples and rng.random() < 0.05:
            first, last = buffer.buffer.times[0], buffer.buffer.times[-1]
            buffer.sample_at(rng.uniform(first - 400.0, last + 600.0))
    buffer.target_delay_ms()
    buffer.end()


RANDOM_CONFIGS = [
    ("player", None),
    ("entity", None),
    ("odd", {"send_interval_ms": 20.0, "min_delay_ms": 40.0, "max_delay_ms": 400.0, "max_extrapolate_ms": 80.0,
             "history_ms": 600.0, "transit_window": 7}),
    ("slow", {"send_interval_ms": 250.0, "min_delay_ms": 300.0, "max_delay_ms": 900.0, "max_extrapolate_ms": 500.0,
              "history_ms": 5000.0, "transit_window": 30}),
]


def generate(out: str) -> dict:
    lines = []
    circle_jitter_loss(lines)
    extrapolation_bounded(lines)
    teleport_late(lines)
    playout_snap(lines)
    edge_cases(lines)
    for index in range(32):
        label, config = RANDOM_CONFIGS[index % len(RANDOM_CONFIGS)]
        if config is None:
            config = interp.InterpBuffer.PLAYER if label == "player" else interp.InterpBuffer.ENTITY
        random_scenario(lines, f"random_{label}_{index}", 1000 + index, config)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="ascii", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    summary = {op: sum(1 for line in lines if line.startswith(op + " ")) for op in
               ("PUSH", "RENDER", "SAMPLE", "DELAY")}
    summary["RESET"] = sum(1 for line in lines if line == "RESET")
    summary["SCENARIO"] = sum(1 for line in lines if line.startswith("SCENARIO "))
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--relay", default=default_relay(), help="relay checkout (default: %(default)s)")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="generate the trace and replay it on the C++ SnapshotBuffer")
    run.add_argument("--exe", required=True, help="coopnet_v2_interp_tests.exe")
    run.add_argument("--out", default=os.path.join(ROOT, "build", "golden", "interp_trace.txt"))
    gen = commands.add_parser("generate", help="only write the trace")
    gen.add_argument("out")
    args = parser.parse_args(argv)
    load_relay(args.relay)
    out = args.out
    summary = generate(out)
    print(f"interp.py trace: {summary['SCENARIO']} scenarios, {summary['PUSH']} pushes, {summary['RENDER']} renders, "
          f"{summary['SAMPLE']} samples, {summary['DELAY']} delays, {summary['RESET']} timing resets -> {out}")
    if args.command == "generate":
        return 0
    result = subprocess.run([os.path.abspath(args.exe), "trace", out])
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
