"""Runs coopnet_loopback.exe (two plugin Transports) against the relay's relay_v2.py over UDP.

    python tools/run_loopback.py                         # every profile
    python tools/run_loopback.py --profile bench         # one profile
    python tools/run_loopback.py --relay ..\\relay        # relay checkout (default: COOPNET_RELAY_DIR or ../relay)

Each profile starts relay_v2.py on a free port with its link simulation (latency, jitter, loss and
duplicates on every datagram the relay sends, so each client's downlink is impaired) and runs the
loopback program; see tests/LoopbackTest.cpp for what it checks. Rooms hold 3 players, so a
second host is refused for its role (ROLE_TAKEN) rather than for the room size. The "restart"
profile stops the relay when the program prints RESTART_RELAY and starts a new one on the same
port.

After each run the relay's own counters must be clean: no protocol violations, no malformed v2
datagrams, no rate-limit drops and no kicks (relay_v2.py validates every message the plugin sends).

Exit code 0 when every profile passes.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_EXE = os.path.join(ROOT, "build", "Release", "coopnet_loopback.exe")

# name: relay link simulation, reliable events A->B, p95 render error limit (m), restart
PROFILES = {
    "clean": {"latency_ms": 0, "jitter_ms": 0, "loss_pct": 0, "dup_pct": 0, "messages": 400, "error_m": 0.05},
    # the Phase 2 bench link: relay_v2.py on 127.0.0.1 with 115 ms + 20 ms jitter and 1 % loss
    "bench": {"latency_ms": 115, "jitter_ms": 20, "loss_pct": 1, "dup_pct": 0, "messages": 400, "error_m": 0.05},
    "lossy": {"latency_ms": 60, "jitter_ms": 40, "loss_pct": 10, "dup_pct": 2, "messages": 300, "error_m": 0.10},
    "restart": {"latency_ms": 20, "jitter_ms": 5, "loss_pct": 0, "dup_pct": 0, "messages": 0, "error_m": 0.05,
                "restart": True},
}


def default_relay() -> str:
    return os.environ.get("COOPNET_RELAY_DIR") or os.path.join(os.path.dirname(ROOT), "relay")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Relay:
    """One relay_v2.py process with its stats JSON and log in the profile's output folder."""

    def __init__(self, relay_dir: str, port: int, spec: dict, out_dir: str, tag: str):
        self.stats_path = os.path.join(out_dir, f"relay_{tag}.json")
        self.log_path = os.path.join(out_dir, f"relay_{tag}.log")
        for path in (self.stats_path, self.log_path):
            if os.path.exists(path):
                os.remove(path)
        args = [sys.executable, "relay_v2.py", "--port", str(port), "--room-size", "3",
                "--latency-ms", str(spec["latency_ms"]),
                "--jitter-ms", str(spec["jitter_ms"]), "--loss-pct", str(spec["loss_pct"]),
                "--dup-pct", str(spec["dup_pct"]), "--seed", "7", "--stats-json", self.stats_path,
                "--log", self.log_path, "--quiet"]
        self.process = subprocess.Popen(args, cwd=relay_dir)

    def stop(self) -> dict:
        """Stops the relay (Ctrl+Break is not portable; terminate, then read what it wrote)."""
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=10)
        if os.path.exists(self.stats_path):
            with open(self.stats_path, encoding="utf-8") as handle:
                return json.load(handle)
        return {}


def wait_for_stats(relay: Relay, seconds: float) -> dict:
    """relay_v2.py writes its stats JSON every 5 s; wait for a fresh one before stopping it."""
    deadline = time.monotonic() + seconds
    start = os.path.getmtime(relay.stats_path) if os.path.exists(relay.stats_path) else 0.0
    while time.monotonic() < deadline:
        if os.path.exists(relay.stats_path) and os.path.getmtime(relay.stats_path) > start:
            break
        time.sleep(0.1)
    return relay.stop()


def counters_clean(stats: dict) -> tuple[bool, str]:
    counters = stats.get("counters", {})
    keys = ("violations", "malformed_v2", "rate_dropped", "kicked", "slow_consumers")
    text = " ".join(f"{key}={counters.get(key, 0)}" for key in keys)
    text += f" welcomes={counters.get('welcomes', 0)} rejects={stats.get('reject_reasons', {})}"
    return bool(counters) and all(counters.get(key, 0) == 0 for key in keys), text


def run_profile(name: str, exe: str, relay_dir: str, out_root: str) -> bool:
    spec = PROFILES[name]
    out_dir = os.path.join(out_root, name)
    os.makedirs(out_dir, exist_ok=True)
    port = free_port()
    relays = [Relay(relay_dir, port, spec, out_dir, "1")]
    time.sleep(0.8)
    started = time.monotonic()
    args = [exe, "127.0.0.1", str(port), str(spec["messages"]), "90",
            "--restart" if spec.get("restart") else "--no-restart", str(spec["error_m"])]
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    lines = []
    restarted = threading.Event()

    def pump():
        for line in process.stdout:
            lines.append(line.rstrip("\n"))
            if line.strip() == "RESTART_RELAY" and not restarted.is_set():
                restarted.set()
                relays[0].final = wait_for_stats(relays[0], 6.5)  # its counters, then it is stopped
                time.sleep(1.0)
                relays.append(Relay(relay_dir, port, spec, out_dir, "2"))

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    try:
        code = process.wait(timeout=180)
    except subprocess.TimeoutExpired:
        process.kill()
        code = -1
    reader.join(timeout=10)
    elapsed = time.monotonic() - started
    final = wait_for_stats(relays[-1], 6.0)
    first = getattr(relays[0], "final", None) if len(relays) > 1 else final

    print(f"===== profile {name}: relay link {spec['latency_ms']} ms + {spec['jitter_ms']} ms jitter, "
          f"{spec['loss_pct']}% loss, {spec['dup_pct']}% dup; events {spec['messages']}; "
          f"exit={code} ({elapsed:.1f}s)")
    for line in lines:
        if line.startswith(("[A info]", "[B info]")) and "peer" not in line and "welcome" not in line \
                and "synced" not in line and "connecting" not in line:
            continue
        print(line)
    ok = code == 0
    for label, stats in (("relay", final),) if len(relays) == 1 else (("relay before restart", first),
                                                                       ("relay after restart", final)):
        clean, text = counters_clean(stats or {})
        print(f"{label}: {text} -> {'clean' if clean else 'NOT CLEAN'}")
        ok = ok and clean
    if spec.get("restart") and not restarted.is_set():
        print("the loopback program never asked for the relay restart")
        ok = False
    print(f"profile {name}: {'PASS' if ok else 'FAIL'}")
    return ok


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", choices=sorted(PROFILES), action="append")
    parser.add_argument("--exe", default=DEFAULT_EXE)
    parser.add_argument("--relay", default=default_relay(), help="relay checkout (default: %(default)s)")
    parser.add_argument("--out", default=os.path.join(ROOT, "build", "loopback"))
    args = parser.parse_args(argv)
    if not os.path.isfile(args.exe):
        print(f"missing {args.exe}; build first")
        return 2
    if not os.path.isfile(os.path.join(args.relay, "relay_v2.py")):
        print(f"relay checkout not found at {args.relay} (use --relay or COOPNET_RELAY_DIR)")
        return 2
    results = {name: run_profile(name, os.path.abspath(args.exe), args.relay, args.out)
               for name in (args.profile or PROFILES)}
    print("SUMMARY: " + ", ".join(f"{name}={'PASS' if ok else 'FAIL'}" for name, ok in results.items()))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
