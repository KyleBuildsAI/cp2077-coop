"""Two C++ v2 clients through the relay's relay_v2.py over real UDP on 127.0.0.1.

    python tools/run_v2_loopback.py --exe build/Release/coopnet_v2_loopback_client.exe
    python tools/run_v2_loopback.py --exe ... --only jitter --duration 30

For each profile it starts relay_v2.py from the relay checkout (--relay, COOPNET_RELAY_DIR or
../relay), then a host and a joiner coopnet_v2_loopback_client. Each client impairs its own uplink
and downlink (latency, uniform jitter, loss, duplicates), so every relay hop sees the profile. The
clients use the C++ Connection (src/v2/V2Reliability) and ClockSync (src/v2/ClockSync).

Checks per profile:
  welcome      both clients completed the cookie handshake and negotiated minor 1
  reliable     every SCRIPT_MSG event (channel 16) arrived exactly once and in order, both ways
  unreliable   30 Hz snapshots (channel 1): no duplicates, delivery near (1 - loss)^2
  clock        relay clock estimate against the truth after warm-up (the clients report their
               QueryPerformanceCounter epoch, relay_v2.py its time.perf_counter() start: one clock)
  instances    the two clients' relay clock estimates agree (the Phase 2 exit criterion: < 5 ms)
  relay        no protocol violations, malformed datagrams or rate-limit drops at the relay
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOCK_LIMIT_MS = 5.0
WARMUP_MS = 5000.0

PROFILES = {
    "clean": {"latency_ms": 0, "jitter_ms": 0, "loss_pct": 0, "dup_pct": 0, "duration": 14, "messages": 200},
    "jitter": {"latency_ms": 40, "jitter_ms": 20, "loss_pct": 1, "dup_pct": 0, "duration": 22, "messages": 300},
    "lossy": {"latency_ms": 60, "jitter_ms": 40, "loss_pct": 10, "dup_pct": 2, "duration": 30, "messages": 300},
}
EVENT_RATE = 20.0  # per second; a 6 s repair burst stays within relay_v2.py's reliable bucket (120)


def default_relay() -> str:
    return os.environ.get("COOPNET_RELAY_DIR") or os.path.join(os.path.dirname(ROOT), "relay")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Checks:
    def __init__(self):
        self.rows = []

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.rows.append((name, bool(ok), detail))

    @property
    def passed(self) -> bool:
        return all(ok for _, ok, _ in self.rows)

    def print(self, title: str) -> None:
        print(f"\n=== profile {title}: {'PASS' if self.passed else 'FAIL'}")
        for name, ok, detail in self.rows:
            print(f"  [{'ok' if ok else 'XX'}] {name:<26} {detail}")


def clock_errors(report: dict, true_offset_ms: float) -> list:
    """(absolute perf time s, estimate error ms) for every history row after warm-up."""
    rows = []
    for local_ms, offset_ms, _bound, _samples in report["clock"]["history"]:
        if local_ms >= WARMUP_MS:
            rows.append((report["epoch_perf_s"] + local_ms / 1000.0, offset_ms - true_offset_ms))
    return rows


def run_profile(name: str, spec: dict, exe: str, relay_dir: str, out_dir: str, duration_override) -> Checks:
    duration = duration_override or spec["duration"]
    os.makedirs(out_dir, exist_ok=True)
    port = free_port()
    relay_json = os.path.join(out_dir, "relay.json")
    relay = subprocess.Popen([sys.executable, "relay_v2.py", "--port", str(port), "--duration", str(duration + 4),
                              "--stats-json", relay_json, "--log", os.path.join(out_dir, "relay.log"), "--quiet"],
                             cwd=relay_dir)
    time.sleep(0.8)
    clients = {}
    for index, role in enumerate(("host", "joiner")):
        report = os.path.join(out_dir, f"{role}.json")
        args = [exe, "--relay-port", str(port), "--role", role, "--room", f"loop-{name}", "--password", "s3cret",
                "--name", role, "--duration", str(duration), "--messages", str(spec["messages"]),
                "--rate", str(EVENT_RATE), "--latency-ms", str(spec["latency_ms"]), "--jitter-ms", str(spec["jitter_ms"]),
                "--loss-pct", str(spec["loss_pct"]), "--dup-pct", str(spec["dup_pct"]), "--seed", str(17 + index),
                "--report", report]
        clients[role] = (subprocess.Popen(args), report)
        time.sleep(0.3)
    for process, _ in clients.values():
        process.wait(timeout=duration + 30)
    relay.wait(timeout=duration + 30)
    reports = {role: json.load(open(path, encoding="utf-8")) for role, (_, path) in clients.items()}
    with open(relay_json, encoding="utf-8") as handle:
        relay_stats = json.load(handle)
    return check(reports, relay_stats, spec)


def check(reports: dict, relay_stats: dict, spec: dict) -> Checks:
    checks = Checks()
    host, joiner = reports["host"], reports["joiner"]
    checks.add("welcome", host["welcomed"] and joiner["welcomed"] and host["minor"] == 1 and joiner["minor"] == 1,
               f"host peer {host['peer_id']} ({host['handshake_ms']:.0f} ms), joiner peer {joiner['peer_id']} "
               f"({joiner['handshake_ms']:.0f} ms), errors {host['error']}/{joiner['error']}")
    for sender, receiver, label in ((host, joiner, "host->joiner"), (joiner, host, "joiner->host")):
        sent = sender["reliable"]["sent"]
        got = receiver["reliable"]["received"]
        checks.add(f"reliable {label}", sent == spec["messages"] and got == sent
                   and receiver["reliable"]["first_mismatch"] is None,
                   f"sent={sent} received={got} first_mismatch={receiver['reliable']['first_mismatch']} "
                   f"resent={sender['link']['reliable_resent']} pending={sender['link']['pending_reliable']}")
    keep = (1.0 - spec["loss_pct"] / 100.0) ** 2
    for sender, receiver, label in ((host, joiner, "host->joiner"), (joiner, host, "joiner->host")):
        sent = sender["unreliable"]["sent"]
        got = receiver["unreliable"]["received"]
        ratio = got / sent if sent else 0.0
        # The receiver starts counting only once it is in the room: allow for the late joiner.
        checks.add(f"unreliable {label}", receiver["unreliable"]["duplicates"] == 0 and ratio >= keep - 0.06,
                   f"delivered {ratio:.1%} (expected ~{keep:.1%}), dups {receiver['unreliable']['duplicates']}, "
                   f"reordered {receiver['unreliable']['reordered']}")
    start = relay_stats["start_perf"]
    errors = {}
    for report, label in ((host, "host"), (joiner, "joiner")):
        true_offset = (report["epoch_perf_s"] - start) * 1000.0
        rows = clock_errors(report, true_offset)
        errors[label] = rows
        worst = max((abs(err) for _, err in rows), default=float("inf"))
        final = report["clock"]["offset_ms"] - true_offset
        clock = report["clock"]
        checks.add(f"clock {label}", rows and worst < CLOCK_LIMIT_MS,
                   f"error after {WARMUP_MS / 1000:.0f} s: max {worst:.2f} ms, final {final:+.2f} ms over {len(rows)} "
                   f"samples; bound {clock['bound_ms']:.2f} ms, min rtt {clock['rtt_ms']:.1f} ms, "
                   f"steps {clock['steps']}, results {clock['results']}")
    pairs = []
    joiner_rows = errors["joiner"]
    for at, host_error in errors["host"]:
        nearest = min(joiner_rows, key=lambda row: abs(row[0] - at), default=None)
        if nearest is not None and abs(nearest[0] - at) <= 0.15:
            pairs.append(abs(host_error - nearest[1]))
    worst_pair = max(pairs, default=float("inf"))
    checks.add("instances agree", pairs and worst_pair < CLOCK_LIMIT_MS,
               f"|host - joiner| relay clock: max {worst_pair:.2f} ms over {len(pairs)} paired samples "
               f"(limit {CLOCK_LIMIT_MS} ms)")
    counters = relay_stats["counters"]
    checks.add("relay clean", counters.get("violations", 0) == 0 and counters.get("malformed_v2", 0) == 0
               and counters.get("rate_dropped", 0) == 0 and counters.get("kicked", 0) == 0,
               f"violations={counters.get('violations', 0)} malformed_v2={counters.get('malformed_v2', 0)} "
               f"rate_dropped={counters.get('rate_dropped', 0)} kicked={counters.get('kicked', 0)}")
    for report, label in ((host, "host"), (joiner, "joiner")):
        link = report["link"]
        checks.add(f"link {label}", report["errors"]["framing"] == 0 and report["errors"]["decode"] == 0,
                   f"srtt {link['srtt_ms']:.1f} ms, rto {link['rto_ms']:.1f} ms, rtt samples {link['rtt_samples']} "
                   f"(skipped {link['rtt_skipped']}), max sample {link['max_rtt_sample_ms']:.1f} ms, "
                   f"loss in {link['loss_in']:.1%} out {link['loss_out']:.1%}")
    return checks


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--exe", required=True, help="coopnet_v2_loopback_client.exe")
    parser.add_argument("--relay", default=default_relay(), help="relay checkout (default: %(default)s)")
    parser.add_argument("--only", nargs="*", choices=sorted(PROFILES), default=None)
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--out", default=os.path.join(ROOT, "build", "v2_loopback"))
    args = parser.parse_args(argv)
    if not os.path.isfile(os.path.join(args.relay, "relay_v2.py")):
        raise SystemExit(f"relay checkout not found at {args.relay} (use --relay or COOPNET_RELAY_DIR)")
    failed = []
    names = args.only or list(PROFILES)
    for name in names:
        spec = PROFILES[name]
        print(f"running {name} (each leg +{spec['latency_ms']} ms, jitter {spec['jitter_ms']} ms, "
              f"loss {spec['loss_pct']}%, dup {spec['dup_pct']}%, {args.duration or spec['duration']} s) ...", flush=True)
        checks = run_profile(name, spec, os.path.abspath(args.exe), args.relay, os.path.join(args.out, name),
                             args.duration)
        checks.print(name)
        if not checks.passed:
            failed.append(name)
    print(f"\n{len(names) - len(failed)}/{len(names)} profiles passed" + (f"; failed: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
