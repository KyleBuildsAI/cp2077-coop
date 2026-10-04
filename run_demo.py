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

The course-* scenarios run both players over the scripted on-foot course (walk, run, sprint,
turns, stops; coopnet/testworld.py) under three link profiles given end to end between the
players (one way): clean, us (30 +-10 ms, 1 % loss) and transatlantic (115 +-20 ms, 1 % loss).
Each client impairs its uplink and its downlink, so a profile is split over four legs: per leg
half the base latency, half the jitter spread (two uniform legs give the +-), and the loss that
compounds to the profile's loss over two legs. They also report the interpolation error per kind
of movement and require the relay clock estimates within 5 ms of the truth.

Any client can be replaced by another implementation that takes client_v2.py's arguments and
writes the same report (--host-exe / --joiner-exe, e.g. the C++ coopnet_v2_demo_client.exe).

Usage:
    python run_demo.py                 # all scenarios
    python run_demo.py --only realistic --duration 30
    python run_demo.py --host-exe path/to/coopnet_v2_demo_client.exe --out runs/cpp-host
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

# End-to-end link profiles (one way, player to player) for the course-* scenarios.
PROFILES = {
    "clean": {"latency_ms": 0.0, "spread_ms": 0.0, "loss_pct": 0.0},
    "us": {"latency_ms": 30.0, "spread_ms": 10.0, "loss_pct": 1.0},
    "transatlantic": {"latency_ms": 115.0, "spread_ms": 20.0, "loss_pct": 1.0},
}


def profile_leg(profile: dict) -> dict:
    """The per-leg model that adds up to an end-to-end profile over uplink + relay + downlink.

    Two legs of base L and uniform jitter [0, J] give 2L + [0, 2J]: mean 2L + J, spread +-J.
    """
    jitter = profile["spread_ms"]
    base = (profile["latency_ms"] - jitter) / 2.0
    keep = math.sqrt(1.0 - profile["loss_pct"] / 100.0)
    return {"latency_ms": base, "jitter_ms": jitter, "loss_pct": round((1.0 - keep) * 100.0, 6), "dup_pct": 0}


for _name, _profile in PROFILES.items():
    if _name != "clean":
        LEGS[_name] = profile_leg(_profile)

SCENARIOS = {
    "clean": {"host_leg": "clean", "joiner_leg": "clean", "duration": 12, "legacy_pool": True},
    "realistic": {"host_leg": "ru_waw", "joiner_leg": "la_waw", "duration": 25, "legacy_pool": True},
    "stress": {"host_leg": "stress", "joiner_leg": "stress", "duration": 25},
    "brutal": {"host_leg": "brutal", "joiner_leg": "brutal", "duration": 25},
    "bridge": {"host_leg": "ru_waw", "joiner_leg": None, "duration": 12, "bridge": True},
    "course-clean": {"host_leg": "clean", "joiner_leg": "clean", "duration": 45, "path": "course"},
    "course-us": {"host_leg": "us", "joiner_leg": "us", "duration": 45, "path": "course"},
    "course-transatlantic": {"host_leg": "transatlantic", "joiner_leg": "transatlantic", "duration": 45,
                             "path": "course"},
}
DEFAULT_ORDER = ["clean", "realistic", "stress", "brutal", "bridge", "course-clean", "course-us",
                 "course-transatlantic"]
COURSE_CLOCK_LIMIT_MS = 5.0
LINGER_MAX_S = 20.0  # client_v2.py LINGER_MAX_S

THRESHOLDS = {
    # p95 of interpolated remote-player error (m) and NPC alignment error (m)
    "clean": (0.10, 0.10), "realistic": (0.15, 0.15), "stress": (0.60, 0.60), "brutal": (1.50, 1.50),
    "course-clean": (0.10, 0.10), "course-us": (0.15, 0.15), "course-transatlantic": (0.15, 0.15),
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


def client_command(exe: str | None) -> list:
    """client_v2.py, or another client implementation that takes the same arguments."""
    return [PYTHON, "client_v2.py"] if not exe else [os.path.abspath(exe)]


def run_scenario(name: str, spec: dict, out_dir: str, duration_override: float | None,
                 host_exe: str | None = None, joiner_exe: str | None = None) -> dict:
    duration = duration_override or spec["duration"]
    os.makedirs(out_dir, exist_ok=True)
    port = free_port()
    files = {key: os.path.join(out_dir, f"{key}.json") for key in ("relay", "host", "joiner", "v1a", "v1b")}
    for path in files.values():
        if os.path.exists(path):
            os.remove(path)
    # The clients linger past --duration until their reliable streams are drained (client_v2.py), so the
    # relay runs until the last v2 client has left, at most LINGER_MAX_S longer than before.
    relay = subprocess.Popen([PYTHON, "relay_v2.py", "--port", str(port), "--duration",
                              str(duration + 4 + LINGER_MAX_S), "--exit-when-idle", "1.0",
                              "--stats-json", files["relay"], "--log", os.path.join(out_dir, "relay.log"),
                              "--quiet"], cwd=HERE)
    time.sleep(0.6)
    procs = []
    room = "legacy" if spec.get("bridge") else f"room-{name}"
    common = ["--relay-port", str(port), "--duration", str(duration), "--room", room, "--password", "s3cret"]
    if spec.get("path"):
        common += ["--player-path", spec["path"]]
    host_flags = ["--join-flags", "3"] if spec.get("bridge") else ["--join-flags", "1"]
    procs.append(subprocess.Popen([*client_command(host_exe), "--role", "host", "--report", files["host"],
                                   *common, *host_flags, *leg_args(spec["host_leg"], 11)], cwd=HERE,
                                  stdout=subprocess.DEVNULL))
    if spec.get("joiner_leg"):
        procs.append(subprocess.Popen([*client_command(joiner_exe), "--role", "joiner", "--report",
                                       files["joiner"], *common, *leg_args(spec["joiner_leg"], 23)], cwd=HERE,
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
        proc.wait(timeout=duration + LINGER_MAX_S + 60)
    relay.wait(timeout=LINGER_MAX_S + 30)
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


def reliable_violations(sent: list, got: list) -> int:
    """Positions where the received stream differs from the sent one, plus missing or extra events."""
    return sum(1 for a, b in zip(sent, got) if a != b) + abs(len(sent) - len(got))


def evaluate_pair(spec: dict, host: dict, joiner: dict, relay: dict, checks: Checks, metrics: dict) -> None:
    for sender, receiver, label in ((host, joiner, "host->joiner"), (joiner, host, "joiner->host")):
        sent = routed_events(sender)
        got = received_from(receiver, sender["peer_id"])
        mismatch = next((i for i, (a, b) in enumerate(zip(sent, got)) if a != b), None)
        violations = reliable_violations(sent, got)
        checks.add(f"reliable {label}", sent == got and len(sent) > 40,
                   f"sent={len(sent)} received={len(got)} first_mismatch={mismatch} violations={violations}")
        metrics[f"reliable_{label}"] = {"sent": len(sent), "received": len(got), "violations": violations,
                                        "resent": sender["link"]["reliable_resent"]}
    expected = expected_delivery(spec["host_leg"], spec["joiner_leg"])
    for sender, receiver, label in ((host, joiner, "host->joiner"), (joiner, host, "joiner->host")):
        remote = receiver["remote_players"].get(str(sender["peer_id"]), {})
        ratio = remote.get("received", 0) / max(1, sender["snapshots_sent"])
        checks.add(f"unreliable {label}", ratio >= expected - 0.05 and ratio <= 1.0,
                   f"delivered {ratio:.1%} (expected ~{expected:.1%}), dups dropped={remote.get('duplicates')} "
                   f"reordered={remote.get('reordered')}")
        span = remote.get("seq_span") or 0
        metrics[f"snapshots_{label}"] = {"sent": sender["snapshots_sent"], "received": remote.get("received", 0),
                                         "ratio": round(ratio, 4), "loss": round(1.0 - ratio, 4),
                                         "expected_loss": round(1.0 - expected, 4), "seq_span": span,
                                         # loss between the first and the last snapshot the receiver got: the
                                         # network loss without the start (not yet in the room) and the end
                                         "span_loss": None if not span else round(1.0 - remote["received"] / span, 4),
                                         "duplicates": remote.get("duplicates"), "reordered": remote.get("reordered")}
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
    clock_errors = {}
    for client, label in ((host, "host"), (joiner, "joiner")):
        true_offset = (client["start_perf"] - relay["start_perf"]) * 1000.0
        estimate = client["clock"]["offset_ms"]
        error = None if estimate is None else estimate - true_offset
        args = client["args"]
        asymmetry = abs(args["up_latency_ms"] - args["down_latency_ms"]) / 2.0
        bound = asymmetry + args["up_jitter_ms"] / 2.0 + args["down_jitter_ms"] / 2.0 + 5.0
        if spec.get("path") == "course":
            bound = COURSE_CLOCK_LIMIT_MS
        detail = "no estimate" if error is None else (f"offset error {error:+.2f} ms (bound {bound:.1f} ms), "
                                                      f"rtt to relay {client['clock']['rtt_ms']:.1f} ms")
        checks.add(f"clock sync {label}", error is not None and abs(error) <= bound, detail)
        metrics[f"clock_{label}"] = {"error_ms": None if error is None else round(error, 3),
                                     "rtt_ms": client["clock"]["rtt_ms"]}
        clock_errors[label] = error
    if spec.get("path") == "course" and None not in clock_errors.values():
        gap = abs(clock_errors["host"] - clock_errors["joiner"])
        checks.add("clock instances agree", gap < COURSE_CLOCK_LIMIT_MS,
                   f"|host - joiner| relay clock {gap:.2f} ms (limit {COURSE_CLOCK_LIMIT_MS} ms)")
        metrics["clock_instances_ms"] = round(gap, 3)
    for client, label in ((host, "host"), (joiner, "joiner")):
        drain = client.get("drain")
        metrics[f"drain_{label}"] = drain
        checks.add(f"stream drained ({label})", drain is not None and not drain["timed_out"]
                   and drain["pending_reliable"] == 0 and drain["other_done"],
                   "no drain report" if drain is None else
                   f"lingered {drain['linger_s']:.2f} s after the run, other stream complete {drain['other_done']}, "
                   f"own events unacked {drain['pending_reliable']}, timed out {drain['timed_out']}")
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
    if spec.get("path") == "course":
        from coopnet.testworld import COURSE_MOTIONS  # noqa: E402  (scenario-only import)
        for client, label in ((joiner, "joiner sees host"), (host, "host sees joiner")):
            by_motion = client["render"].get("by_motion", {})
            counts = {motion: by_motion.get(motion, {}).get("error_m", {}).get("count", 0) for motion in COURSE_MOTIONS}
            checks.add(f"course covered ({label})", all(count >= 30 for count in counts.values()),
                       "frames per motion " + ", ".join(f"{m} {c}" for m, c in counts.items()))
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
    parser.add_argument("--host-exe", default=None, help="host client executable (default: python client_v2.py)")
    parser.add_argument("--joiner-exe", default=None, help="joiner client executable (default: python client_v2.py)")
    args = parser.parse_args(argv)
    names = args.only or DEFAULT_ORDER
    results = []
    clients = (f"host {'python' if not args.host_exe else os.path.basename(args.host_exe)}, "
               f"joiner {'python' if not args.joiner_exe else os.path.basename(args.joiner_exe)}")
    for name in names:
        spec = dict(SCENARIOS[name], name=name)
        legs = f"host leg {spec['host_leg']}, joiner leg {spec['joiner_leg']}"
        print(f"running {name} ({legs}, {args.duration or spec['duration']} s; {clients}) ...", flush=True)
        result = run_scenario(name, spec, os.path.join(args.out, name), args.duration, args.host_exe,
                              args.joiner_exe)
        print_result(result)
        results.append(result)
    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as handle:
        json.dump({"legs": LEGS, "profiles": PROFILES, "host_exe": args.host_exe, "joiner_exe": args.joiner_exe,
                   "results": results}, handle, indent=1, default=str)
    failed = [r["scenario"] for r in results if not r["ok"]]
    print(f"\n{len(results) - len(failed)}/{len(results)} scenarios passed" + (f"; failed: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
