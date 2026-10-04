"""End-to-end demo: real UDP relay + v2 host/joiner (+ v1 clients) under simulated links.

Each scenario starts relay_v2.py and the clients as separate processes on
localhost, impairs every leg in the clients (uplink and downlink), then
checks the JSON reports:

  reliable   every reliable event arrives exactly once, in order, both ways
  unreliable 30 Hz snapshot delivery matches the simulated loss; dups dropped
  delta      every entity snapshot the joiner decoded equals the host's view
  clock      relay clock estimate error vs the true offset (same machine)
  render     interpolated remote player / NPC error against ground truth,
             and smoothness compared with the v1 latest-packet model
  relay      no protocol violations or kicks for well-behaved clients
  legacy     v1 CP1/RP1 clients keep working beside v2 and through the bridge

Usage:
    python run_demo.py                 # all scenarios
    python run_demo.py --only realistic --duration 30
"""
from __future__ import annotations

import argparse
import json
import math
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable

# One-way leg models. Host in Russia <-> Warsaw relay, joiner in Los Angeles <-> Warsaw.
LEGS = {
    "clean": {"latency_ms": 0, "jitter_ms": 0, "loss_pct": 0, "dup_pct": 0},
    "ru_waw": {"latency_ms": 18, "jitter_ms": 6, "loss_pct": 1.0, "dup_pct": 0},
    "la_waw": {"latency_ms": 78, "jitter_ms": 18, "loss_pct": 2.0, "dup_pct": 0},
    "stress": {"latency_ms": 60, "jitter_ms": 40, "loss_pct": 10.0, "dup_pct": 2.0},
    "brutal": {"latency_ms": 90, "jitter_ms": 60, "loss_pct": 20.0, "dup_pct": 5.0},
}

SCENARIOS = {
    "clean": {"host_leg": "clean", "joiner_leg": "clean", "duration": 12, "legacy_pool": True},
    "realistic": {"host_leg": "ru_waw", "joiner_leg": "la_waw", "duration": 25, "legacy_pool": True},
    "stress": {"host_leg": "stress", "joiner_leg": "stress", "duration": 25},
    "brutal": {"host_leg": "brutal", "joiner_leg": "brutal", "duration": 25},
    "bridge": {"host_leg": "ru_waw", "joiner_leg": None, "duration": 12, "bridge": True},
}

THRESHOLDS = {
    # p95 of interpolated remote-player error (m) and NPC alignment error (m)
    "clean": (0.10, 0.10), "realistic": (0.15, 0.15), "stress": (0.60, 0.60), "brutal": (1.50, 1.50),
}


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def leg_args(leg: str, seed: int) -> list:
    model = LEGS[leg]
    args = ["--seed", str(seed)]
    for direction in ("up", "down"):
        args += [f"--{direction}-latency-ms", str(model["latency_ms"]), f"--{direction}-jitter-ms",
                 str(model["jitter_ms"]), f"--{direction}-loss-pct", str(model["loss_pct"]),
                 f"--{direction}-dup-pct", str(model["dup_pct"])]
    return args


def run_scenario(name: str, spec: dict, out_dir: str, duration_override: float | None) -> dict:
    duration = duration_override or spec["duration"]
    os.makedirs(out_dir, exist_ok=True)
    port = free_port()
    files = {key: os.path.join(out_dir, f"{key}.json") for key in ("relay", "host", "joiner", "v1a", "v1b")}
    for path in files.values():
        if os.path.exists(path):
            os.remove(path)
    relay = subprocess.Popen([PYTHON, "relay_v2.py", "--port", str(port), "--duration", str(duration + 4),
                              "--stats-json", files["relay"], "--log", os.path.join(out_dir, "relay.log"),
                              "--quiet"], cwd=HERE)
    time.sleep(0.6)
    procs = []
    room = "legacy" if spec.get("bridge") else f"room-{name}"
    common = ["--relay-port", str(port), "--duration", str(duration), "--room", room, "--password", "s3cret"]
    host_flags = ["--join-flags", "3"] if spec.get("bridge") else ["--join-flags", "1"]
    procs.append(subprocess.Popen([PYTHON, "client_v2.py", "--role", "host", "--report", files["host"],
                                   *common, *host_flags, *leg_args(spec["host_leg"], 11)], cwd=HERE,
                                  stdout=subprocess.DEVNULL))
    if spec.get("joiner_leg"):
        procs.append(subprocess.Popen([PYTHON, "client_v2.py", "--role", "joiner", "--report", files["joiner"],
                                       *common, *leg_args(spec["joiner_leg"], 23)], cwd=HERE,
                                      stdout=subprocess.DEVNULL))
    if spec.get("legacy_pool") or spec.get("bridge"):
        v1_duration = str(max(4.0, duration - 2))
        procs.append(subprocess.Popen([PYTHON, "legacy_client.py", "--relay-port", str(port), "--duration",
                                       v1_duration, "--flags", "2" if spec.get("bridge") else "259",
                                       "--report", files["v1a"]], cwd=HERE,
                                      stdout=subprocess.DEVNULL))
        if spec.get("legacy_pool"):
            procs.append(subprocess.Popen([PYTHON, "legacy_client.py", "--relay-port", str(port), "--duration",
                                           v1_duration, "--flags", "2", "--center", "-1300", "300",
                                           "--report", files["v1b"]], cwd=HERE, stdout=subprocess.DEVNULL))
    for proc in procs:
        proc.wait(timeout=duration + 60)
    relay.wait(timeout=30)
    reports = {}
    for key, path in files.items():
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                reports[key] = json.load(handle)
    return evaluate(name, spec, reports)


class Checks:
    def __init__(self):
        self.items = []

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.items.append({"check": name, "ok": bool(ok), "detail": detail})

    @property
    def ok(self) -> bool:
        return all(item["ok"] for item in self.items)


def routed_events(sender: dict) -> list:
    return [[event[0], event[2]] for event in sender.get("sent_events", [])]


def received_from(receiver: dict, src: int) -> list:
    return [[event[1], event[2]] for event in receiver.get("received_events", []) if event[0] == src]


def expected_delivery(host_leg: str, joiner_leg: str) -> float:
    return (1 - LEGS[host_leg]["loss_pct"] / 100.0) * (1 - LEGS[joiner_leg]["loss_pct"] / 100.0)


def evaluate(name: str, spec: dict, reports: dict) -> dict:
    checks = Checks()
    host, joiner, relay = reports.get("host"), reports.get("joiner"), reports.get("relay")
    metrics = {}
    if host is None or relay is None:
        checks.add("processes", False, f"missing reports: {sorted(reports)}")
        return {"scenario": name, "ok": False, "checks": checks.items, "metrics": metrics}
    checks.add("welcome", bool(host.get("welcome")) and (joiner is None or bool(joiner.get("welcome"))),
               f"host={host.get('role')} joiner={None if joiner is None else joiner.get('role')}")
    if joiner is not None:
        evaluate_pair(spec, host, joiner, relay, checks, metrics)
    if spec.get("legacy_pool"):
        evaluate_pool(reports, checks, metrics)
    if spec.get("bridge"):
        evaluate_bridge(host, reports.get("v1a"), checks, metrics)
    counters = relay.get("counters", {})
    checks.add("relay clean", counters.get("violations", 0) == 0 and counters.get("kicked", 0) == 0
               and counters.get("slow_consumers", 0) == 0,
               f"violations={counters.get('violations', 0)} kicked={counters.get('kicked', 0)} "
               f"malformed_v2={counters.get('malformed_v2', 0)} rate_dropped={counters.get('rate_dropped', 0)}")
    metrics["relay_counters"] = counters
    return {"scenario": name, "ok": checks.ok, "checks": checks.items, "metrics": metrics}


def evaluate_pair(spec: dict, host: dict, joiner: dict, relay: dict, checks: Checks, metrics: dict) -> None:
    for sender, receiver, label in ((host, joiner, "host->joiner"), (joiner, host, "joiner->host")):
        sent = routed_events(sender)
        got = received_from(receiver, sender["peer_id"])
        mismatch = next((i for i, (a, b) in enumerate(zip(sent, got)) if a != b), None)
        checks.add(f"reliable {label}", sent == got and len(sent) > 40,
                   f"sent={len(sent)} received={len(got)} first_mismatch={mismatch}")
        metrics[f"reliable_{label}"] = {"sent": len(sent), "received": len(got),
                                        "resent": sender["link"]["reliable_resent"]}
    expected = expected_delivery(spec["host_leg"], spec["joiner_leg"])
    for sender, receiver, label in ((host, joiner, "host->joiner"), (joiner, host, "joiner->host")):
        remote = receiver["remote_players"].get(str(sender["peer_id"]), {})
        ratio = remote.get("received", 0) / max(1, sender["snapshots_sent"])
        checks.add(f"unreliable {label}", ratio >= expected - 0.05 and ratio <= 1.0,
                   f"delivered {ratio:.1%} (expected ~{expected:.1%}), dups dropped={remote.get('duplicates')} "
                   f"reordered={remote.get('reordered')}")
        metrics[f"snapshots_{label}"] = {"sent": sender["snapshots_sent"], "received": remote.get("received", 0),
                                         "ratio": round(ratio, 4), "duplicates": remote.get("duplicates"),
                                         "reordered": remote.get("reordered")}
    entity = joiner["entity"]
    host_hashes = host["entity"]["host_view_hashes"]
    joiner_hashes = entity["joiner_view_hashes"]
    mismatched = [tick for tick, value in joiner_hashes.items() if host_hashes.get(tick) != value]
    decoded = len(joiner_hashes)
    sent_ticks = len(host_hashes)
    checks.add("delta snapshots exact", decoded > 0 and not mismatched,
               f"decoded {decoded}/{sent_ticks} ticks, mismatches={len(mismatched)}, "
               f"missing_baseline={entity['decoder']['missing_baseline']}")
    encoder = host["entity"]["encoder"]
    metrics["entity"] = {
        "ticks_sent": sent_ticks, "ticks_decoded": decoded, "decoder": entity["decoder"],
        "avg_bytes": round(encoder["bytes"] / max(1, encoder["snapshots"]), 1),
        "avg_full_bytes": round(encoder["full_bytes"] / max(1, encoder["snapshots"]), 1),
        "compression": round(encoder["full_bytes"] / max(1, encoder["bytes"]), 2),
        "deferred_records": encoder["deferred"], "full_snapshots": encoder["full_snapshots"],
        "spawns": encoder["spawns"], "removals": encoder["removals"],
        "view_size_end": entity["view_size_end"], "alignment_error_m": entity["alignment_error_m"]}
    for client, label in ((host, "host"), (joiner, "joiner")):
        true_offset = (client["start_perf"] - relay["start_perf"]) * 1000.0
        estimate = client["clock"]["offset_ms"]
        error = None if estimate is None else estimate - true_offset
        args = client["args"]
        asymmetry = abs(args["up_latency_ms"] - args["down_latency_ms"]) / 2.0
        bound = asymmetry + args["up_jitter_ms"] / 2.0 + args["down_jitter_ms"] / 2.0 + 5.0
        checks.add(f"clock sync {label}", error is not None and abs(error) <= bound,
                   f"offset error {error:+.2f} ms (bound {bound:.1f} ms), rtt to relay {client['clock']['rtt_ms']:.1f} ms")
        metrics[f"clock_{label}"] = {"error_ms": round(error, 3), "rtt_ms": client["clock"]["rtt_ms"]}
    for client, label in ((joiner, "joiner sees host"), (host, "host sees joiner")):
        metrics[f"render_{label}"] = client["render"]
    player_limit, npc_limit = THRESHOLDS.get(spec["name"], (1.0, 1.0))
    for client, label in ((joiner, "joiner sees host"), (host, "host sees joiner")):
        error = client["render"]["player_error_m"]
        checks.add(f"interp {label}", error.get("count", 0) > 100 and error["p95"] <= player_limit,
                   f"p50 {error.get('p50', 0):.3f} m, p95 {error.get('p95', 0):.3f} m, max {error.get('max', 0):.3f} m "
                   f"(limit p95 {player_limit} m); modes {client['render']['modes']}")
        v2 = client["render"]["accel_v2_mps2"]
        v1 = client["render"]["accel_v1_model_mps2"]
        checks.add(f"smoother than v1 model ({label})", v2.get("p99", 1e9) < v1.get("p99", 0),
                   f"|accel| p99 v2 {v2.get('p99', 0):.1f} vs v1-model {v1.get('p99', 0):.1f} m/s^2 "
                   f"(truth {client['render']['accel_truth_mps2'].get('p99', 0):.1f})")
    npc = entity["alignment_error_m"]
    checks.add("npc alignment", npc.get("count", 0) > 100 and npc["p95"] <= npc_limit,
               f"p50 {npc.get('p50', 0):.3f} m, p95 {npc.get('p95', 0):.3f} m, max {npc.get('max', 0):.3f} m "
               f"over {npc.get('count', 0)} entity-frames (limit p95 {npc_limit} m)")
    metrics["bandwidth"] = {label: {"up_bytes": c["link"]["bytes_sent"], "down_bytes": c["link"]["bytes_received"],
                                    "seconds": c["args"]["duration"]}
                            for c, label in ((host, "host"), (joiner, "joiner"))}


def evaluate_pool(reports: dict, checks: Checks, metrics: dict) -> None:
    v1a, v1b = reports.get("v1a"), reports.get("v1b")
    if v1a is None or v1b is None:
        checks.add("v1 pool", False, "missing v1 reports")
        return
    ok = True
    details = []
    for me, other, label in ((v1a, v1b, "a"), (v1b, v1a, "b")):
        sources = me["rp1"]
        if len(sources) != 1:
            ok = False
            details.append(f"{label}: RP1 sources {sorted(sources)}")
            continue
        entry = next(iter(sources.values()))
        center = other["args"]["center"]
        radius_errors = [abs(math.dist(p[:2], center) - other["args"]["radius"]) for p in entry["positions"]]
        ratio = entry["count"] / max(1, other["sent"])
        good = (me["welcome_ids"] and ratio > 0.95 and max(radius_errors) < 0.01
                and other["args"]["flags"] in entry["flags_values"] and entry["regressions"] == 0)
        ok = ok and bool(good)
        details.append(f"{label}: welcome={me['welcome_ids']} rp1 {entry['count']}/{other['sent']} "
                       f"flags={entry['flags_values']} pos_err_max={max(radius_errors):.4f}")
    checks.add("v1 pool unchanged beside v2", ok, "; ".join(details))
    metrics["v1_pool"] = details


def evaluate_bridge(host: dict, v1: dict | None, checks: Checks, metrics: dict) -> None:
    if v1 is None:
        checks.add("bridge", False, "missing v1 report")
        return
    entries = v1["rp1"]
    entry = next(iter(entries.values()), None) if entries else None
    if entry is None:
        checks.add("bridge v2->v1", False, "v1 client got no RP1 from the v2 host")
    else:
        from coopnet import testworld  # noqa: E402  (scenario-only import)
        radius_errors = [abs(math.dist(p[:2], testworld.ORIGIN[:2]) - testworld.HOST_RADIUS_M) for p in entry["positions"]]
        expected_flags = 2 + 4 * 32 + 256
        ok = (entry["count"] > 100 and max(radius_errors) < 0.01 and expected_flags in entry["flags_values"]
              and entry["time_values"] and entry["weather_values"] and entry["pongs"] > 0)
        checks.add("bridge v2->v1", ok,
                   f"RP1 {entry['count']}, pos_err_max {max(radius_errors):.4f} m, flags {entry['flags_values']}, "
                   f"time {entry['time_values'][:4]}.., weather {entry['weather_values']}, pongs {entry['pongs']}, "
                   f"pong rtt {v1['pong_rtt_ms'][:3]}")
    legacy_peers = {k: v for k, v in host.get("remote_players", {}).items() if int(k) >= 200}
    remote = next(iter(legacy_peers.values()), None)
    if remote is None:
        checks.add("bridge v1->v2", False, "v2 host got no PLAYER_SNAPSHOT from the v1 client")
    else:
        legacy_flag = 1 << 14
        ok = (remote["received"] > 100 and remote["flags_seen"] & legacy_flag
              and remote["legacy_radius_error"]["max"] < 0.01 and any(p >= 200 for p in host["peers_seen"]))
        checks.add("bridge v1->v2", bool(ok),
                   f"snapshots {remote['received']}, flags 0x{remote['flags_seen']:04x}, "
                   f"pos_err_max {remote['legacy_radius_error']['max']:.4f} m, peers seen {host['peers_seen']}")
    metrics["bridge"] = {"v1_pong_rtt_ms": v1.get("pong_rtt_ms", [])[:5]}


def print_result(result: dict) -> None:
    status = "PASS" if result["ok"] else "FAIL"
    print(f"\n=== scenario {result['scenario']}: {status}")
    for item in result["checks"]:
        print(f"  [{'ok' if item['ok'] else 'XX'}] {item['check']:<34} {item['detail']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the v2 relay end-to-end scenarios")
    parser.add_argument("--only", nargs="*", choices=sorted(SCENARIOS), default=None)
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--out", default=os.path.join(HERE, "runs"))
    args = parser.parse_args(argv)
    names = args.only or ["clean", "realistic", "stress", "brutal", "bridge"]
    results = []
    for name in names:
        spec = dict(SCENARIOS[name], name=name)
        legs = f"host leg {spec['host_leg']}, joiner leg {spec['joiner_leg']}"
        print(f"running {name} ({legs}, {args.duration or spec['duration']} s) ...", flush=True)
        result = run_scenario(name, spec, os.path.join(args.out, name), args.duration)
        print_result(result)
        results.append(result)
    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as handle:
        json.dump({"legs": LEGS, "results": results}, handle, indent=1, default=str)
    failed = [r["scenario"] for r in results if not r["ok"]]
    print(f"\n{len(results) - len(failed)}/{len(results)} scenarios passed" + (f"; failed: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
