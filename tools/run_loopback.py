"""Runs coopnet_loopback.exe against a local coopnet_relay.py with a simulated link.

Usage:
    python tools/run_loopback.py                          # all profiles
    python tools/run_loopback.py --profile transatlantic  # one profile

Exit code 0 when every profile passes.
"""
import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELAY = os.path.join(ROOT, "tools", "coopnet_relay.py")
DEFAULT_EXE = os.path.join(ROOT, "build", "Release", "coopnet_loopback.exe")

# name: (latency ms one way, jitter ms, loss %, reorder, reliable messages)
PROFILES = {
    "clean": (0, 0, 0.0, False, 1000),
    "transatlantic": (160, 15, 2.0, False, 400),  # Russia <-> Warsaw <-> Los Angeles, ~320 ms RTT
    "hostile": (80, 40, 20.0, True, 300),         # heavy loss + reordering
}


def run_profile(name, exe, port):
    latency, jitter, loss, reorder, count = PROFILES[name]
    relay_log = os.path.join(ROOT, "build", f"relay_{name}.log")
    with open(relay_log, "w", encoding="utf-8") as log_handle:
        relay = subprocess.Popen(
            [sys.executable, RELAY, "--port", str(port), "--latency-ms", str(latency), "--jitter-ms", str(jitter),
             "--loss-pct", str(loss), "--seed", "7"] + (["--reorder"] if reorder else []),
            stdout=log_handle, stderr=subprocess.STDOUT,
        )
        try:
            time.sleep(0.5)
            started = time.monotonic()
            result = subprocess.run([exe, "127.0.0.1", str(port), str(count), "90"], capture_output=True, text=True,
                                    timeout=150)
            elapsed = time.monotonic() - started
        finally:
            relay.terminate()
            relay.wait(timeout=10)
    print(f"===== profile {name}: latency={latency}ms jitter={jitter}ms loss={loss}% reorder={reorder} "
          f"reliable={count} "
          f"exit={result.returncode} ({elapsed:.1f}s)")
    print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip())
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", choices=sorted(PROFILES), action="append")
    parser.add_argument("--exe", default=DEFAULT_EXE)
    parser.add_argument("--port", type=int, default=11789)
    args = parser.parse_args()
    if not os.path.isfile(args.exe):
        print(f"missing {args.exe}; build first")
        return 2
    results = {name: run_profile(name, args.exe, args.port) for name in (args.profile or PROFILES)}
    print("SUMMARY: " + ", ".join(f"{name}={'PASS' if ok else 'FAIL'}" for name, ok in results.items()))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
