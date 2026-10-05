"""Every run_demo.py scenario of the relay repo with the C++ demo client on one or both ends.

    python tools/run_v2_demo.py --exe build/Release/coopnet_v2_demo_client.exe
    python tools/run_v2_demo.py --exe ... --pairings cpp-host --only clean course-us --repeat 3

coopnet_v2_demo_client (tests/V2DemoClient.cpp) is a C++ drop-in for the relay's client_v2.py
built on the plugin's protocol v2 modules (codec, delta snapshots, Connection, ClockSync,
SnapshotBuffer). This runner:

1. self test and world check: the client checks its own helpers (--self-test: the received
   sequence span past 32,768 snapshots and across the 16-bit wrap), then its port of
   coopnet/testworld.py (tests/V2DemoWorld.cpp) dumps its entity table, entity truth and player
   truth (demo and course paths); the table must equal testworld.py exactly and the truth within
   1e-9, or the two clients would measure each other against different ground truth.
2. runs the relay's run_demo.py scenarios (relay_v2.py over real UDP on 127.0.0.1, every leg
   impaired in the clients) for each pairing:
     cpp-host    C++ host, Python joiner
     cpp-joiner  Python host, C++ joiner
     cpp-both    C++ on both ends (the Phase 2 exit criterion)
   run_demo.py's own checks decide pass or fail. The bridge scenario has no joiner, so it runs
   for the pairings with a C++ host only.
3. prints the measurements per run: snapshot loss each way against the simulated loss, reliable
   order violations (must be 0), relay violations, the relay clock error of each client against
   the truth and between the two, and the interpolated pose error against the sender's ground
   truth (p95 position and yaw; per kind of movement on the scripted course).

Results go to build/v2_demo/<pairing>/<scenario>[-<n>]/ and build/v2_demo/summary.json.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAIRINGS = ("cpp-host", "cpp-joiner", "cpp-both")
TRUTH_TOLERANCE = 1e-9
CLOCK_LIMIT_MS = 5.0
CLOCK_WARMUP_MS = 5000.0
CLOCK_PAIR_WINDOW_S = 0.3


def default_relay() -> str:
    return os.environ.get("COOPNET_RELAY_DIR") or os.path.join(os.path.dirname(ROOT), "relay")


def load_relay(relay_dir: str):
    if not os.path.isfile(os.path.join(relay_dir, "run_demo.py")):
        raise SystemExit(f"relay checkout not found at {relay_dir} (use --relay or COOPNET_RELAY_DIR)")
    sys.path.insert(0, relay_dir)
    import run_demo  # noqa: E402
    from coopnet import proto, testworld  # noqa: E402

    if "course-us" not in run_demo.SCENARIOS or not hasattr(testworld, "course_truth"):
        raise SystemExit(f"{relay_dir} predates the course scenarios (needs relay feat/phase2-v2 with run_demo "
                         "--host-exe/--joiner-exe and testworld.course_truth)")
    return run_demo, proto, testworld


# ---- 1. world check ------------------------------------------------------------------------------


def angle_gap(a: float, b: float) -> float:
    return abs(((a - b + 180.0) % 360.0) - 180.0)


def close(a, b) -> bool:
    return all(abs(x - y) <= TRUTH_TOLERANCE for x, y in zip(a, b)) and len(a) == len(b)


def check_world(exe: str, out_dir: str, proto, testworld) -> list:
    """Differences between the C++ dump and testworld.py (empty when they agree)."""
    path = os.path.join(out_dir, "world_cpp.json")
    subprocess.run([exe, "--dump-world", path], check=True)
    with open(path, encoding="utf-8") as handle:
        dump = json.load(handle)
    problems = []
    world = testworld.build_entities(7)
    expected = [[s.net_id, int(s.kind), list(s.center), s.radius, s.speed, s.phase, s.spawn_at, s.despawn_at,
                 s.death_at, int(s.flags)] for s in world]
    if dump["entities"] != expected:
        first = next((i for i, (a, b) in enumerate(zip(dump["entities"], expected)) if a != b), None)
        problems.append(f"entity table differs (C++ {len(dump['entities'])} entries, Python {len(expected)}, "
                        f"first difference at index {first})")
    by_id = {spec.net_id: spec for spec in world}
    for net_id, t, alive, pos, vel, yaw, move, flags, health in dump["entity_truth"]:
        spec = by_id.get(net_id)
        if spec is None:
            problems.append(f"entity {net_id} unknown to testworld.py")
            continue
        truth = testworld.entity_truth(spec, t)
        if (alive != testworld.alive(spec, t) or not close(pos, truth["pos"]) or not close(vel, truth["vel"])
                or angle_gap(yaw, truth["yaw"]) > TRUTH_TOLERANCE or move != truth["move"]
                or flags != truth["flags"] or health != truth["health"]):
            problems.append(f"entity {net_id} truth at t={t} differs")
    for path_name, role, t, pos, vel, yaw, motion, move, driving in dump["player_truth"]:
        truth = testworld.player_truth(role, t, path_name)
        expected_move = truth.get("move", int(proto.MoveState.VEHICLE if truth["driving"] else proto.MoveState.RUN))
        if (not close(pos, truth["pos"]) or not close(vel, truth["vel"]) or angle_gap(yaw, truth["yaw"]) > TRUTH_TOLERANCE
                or motion != truth["motion"] or move != expected_move or driving != truth["driving"]):
            problems.append(f"player truth {path_name} role {role} t={t} differs: C++ {pos} {motion}, "
                            f"Python {truth['pos']} {truth['motion']}")
    period = testworld._COURSES[int(proto.Role.HOST)][1]
    if abs(dump["course_period_s"] - period) > TRUTH_TOLERANCE:
        problems.append(f"course period C++ {dump['course_period_s']} vs Python {period}")
    return problems


# ---- 2. scenarios --------------------------------------------------------------------------------


def executables(pairing: str, exe: str):
    return {"cpp-host": (exe, None), "cpp-joiner": (None, exe), "cpp-both": (exe, exe)}[pairing]


def scenario_names(run_demo, only) -> list:
    return list(only) if only else list(run_demo.DEFAULT_ORDER)


def run_all(args, run_demo) -> list:
    results = []
    names = scenario_names(run_demo, args.only)
    for pairing in args.pairings:
        host_exe, joiner_exe = executables(pairing, args.exe)
        for name in names:
            spec = dict(run_demo.SCENARIOS[name], name=name)
            if spec.get("bridge") and host_exe is None:
                print(f"skipping {pairing}/{name}: the bridge scenario has no joiner (its host is Python here)")
                continue
            for attempt in range(args.repeat):
                label = name if args.repeat == 1 else f"{name}-{attempt + 1}"
                out_dir = os.path.join(args.out, pairing, label)
                duration = args.duration or spec["duration"]
                print(f"running {pairing}/{label} (host leg {spec['host_leg']}, joiner leg {spec['joiner_leg']}, "
                      f"{duration} s) ...", flush=True)
                started = time.time()
                result = run_demo.run_scenario(name, spec, out_dir, args.duration, host_exe, joiner_exe)
                result["pairing"] = pairing
                result["run"] = label
                result["seconds"] = round(time.time() - started, 1)
                result["implementations"] = implementations(out_dir)
                clock_over_run(result, out_dir)
                run_demo.print_result(result)
                results.append(result)
    return results


def implementations(out_dir: str) -> dict:
    found = {}
    for role in ("host", "joiner"):
        path = os.path.join(out_dir, f"{role}.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                found[role] = json.load(handle).get("implementation", "python client_v2.py")
    return found


def load_reports(out_dir: str) -> dict:
    reports = {}
    for key in ("relay", "host", "joiner"):
        path = os.path.join(out_dir, f"{key}.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                reports[key] = json.load(handle)
    return reports


def clock_errors(report: dict, relay_start: float) -> list:
    """(perf time s, estimate - truth ms) for every exchange after the warm-up (C++ clients keep a history)."""
    true_offset = (report["start_perf"] - relay_start) * 1000.0
    return [(report["start_perf"] + local_ms / 1000.0, offset - true_offset)
            for local_ms, offset, _rtt in report.get("clock", {}).get("history", []) if local_ms >= CLOCK_WARMUP_MS]


def clock_over_run(result: dict, out_dir: str) -> None:
    """The plugin's estimator over the whole run, not only its final value: every C++ client's relay clock
    error after the warm-up, and (C++ on both ends) the gap between the two instances' estimates at the
    same moment. On the course scenarios both must stay under 5 ms (the Phase 2 exit criterion)."""
    reports = load_reports(out_dir)
    relay = reports.get("relay")
    tracks = {role: clock_errors(reports[role], relay["start_perf"]) for role in ("host", "joiner")
              if relay and role in reports and reports[role].get("clock", {}).get("history")}
    summary = {f"{role}_max_ms": round(max(abs(e) for _, e in rows), 3) for role, rows in tracks.items() if rows}
    summary.update({f"{role}_exchanges": len(rows) for role, rows in tracks.items()})
    if len(tracks) == 2 and tracks["host"] and tracks["joiner"]:
        gaps = []
        for at, host_error in tracks["host"]:
            nearest = min(tracks["joiner"], key=lambda row: abs(row[0] - at))
            if abs(nearest[0] - at) <= CLOCK_PAIR_WINDOW_S:
                gaps.append(abs(host_error - nearest[1]))
        summary["pair_max_ms"] = round(max(gaps), 3) if gaps else None
        summary["pairs"] = len(gaps)
    result["clock_over_run"] = summary
    if not result["scenario"].startswith("course-"):
        return
    problems = [f"{key} {value} ms" for key, value in summary.items()
                if key.endswith("_max_ms") and value is not None and value >= CLOCK_LIMIT_MS]
    detail = ", ".join(f"{k} {v}" for k, v in summary.items()) or "no C++ clock history"
    result["checks"].append({"check": "clock over the run (C++)", "ok": not problems,
                             "detail": detail + (f"; over {CLOCK_LIMIT_MS} ms: {problems}" if problems else "")})
    result["ok"] = result["ok"] and not problems


# ---- 3. measurements -----------------------------------------------------------------------------


def pct(value) -> str:
    return "-" if value is None else f"{100.0 * value:.1f}%"


def ms(value) -> str:
    return "-" if value is None else f"{value:+.2f}"


def meters(value) -> str:
    return "-" if value is None else f"{value:.3f}"


def row_of(result: dict) -> dict:
    metrics = result["metrics"]
    row = {"pairing": result["pairing"], "run": result["run"], "ok": result["ok"]}
    if "snapshots_host->joiner" in metrics:
        hj, jh = metrics["snapshots_host->joiner"], metrics["snapshots_joiner->host"]
        row.update(loss_hj=hj["loss"], loss_jh=jh["loss"], loss_expected=hj.get("expected_loss"),
                   span_loss_hj=hj.get("span_loss"), span_loss_jh=jh.get("span_loss"))
        row["reliable_violations"] = (metrics["reliable_host->joiner"]["violations"]
                                      + metrics["reliable_joiner->host"]["violations"])
        row["reliable_events"] = (metrics["reliable_host->joiner"]["sent"] + metrics["reliable_joiner->host"]["sent"])
        row["clock_host"] = metrics["clock_host"]["error_ms"]
        row["clock_joiner"] = metrics["clock_joiner"]["error_ms"]
        if row["clock_host"] is not None and row["clock_joiner"] is not None:
            row["clock_gap"] = abs(row["clock_host"] - row["clock_joiner"])
        row["interp_joiner_sees_host_p95"] = metrics["render_joiner sees host"]["player_error_m"].get("p95")
        row["interp_host_sees_joiner_p95"] = metrics["render_host sees joiner"]["player_error_m"].get("p95")
        row["yaw_joiner_sees_host_p95"] = metrics["render_joiner sees host"].get("yaw_error_deg", {}).get("p95")
        row["yaw_host_sees_joiner_p95"] = metrics["render_host sees joiner"].get("yaw_error_deg", {}).get("p95")
        row["render_delay_p50"] = metrics["render_joiner sees host"].get("render_delay_ms", {}).get("p50")
        row["npc_p95"] = metrics["entity"]["alignment_error_m"].get("p95")
        row["by_motion"] = {label: metrics[f"render_{label}"].get("by_motion", {})
                            for label in ("joiner sees host", "host sees joiner")}
    row["clock_over_run"] = result.get("clock_over_run", {})
    counters = metrics.get("relay_counters", {})
    row["relay_violations"] = counters.get("violations", 0)
    row["relay_rate_dropped"] = counters.get("rate_dropped", 0)
    return row


def print_tables(rows: list, motions) -> None:
    print("\n=== measurements")
    print("loss: snapshots missing between the first and the last one the receiver got (network loss), against the")
    print("      simulated loss; 'all' also counts the start and the end of the run (run_demo.py's delivery check)")
    print("clock: relay clock estimate minus the truth at the end (ms) per client and |gap| between the two;")
    print("       'C++ max' is the largest error of a C++ client over the run after 5 s, 'pair' the largest gap")
    print("       between two C++ instances at the same moment")
    print("delay: p50 render delay (ms) of the joiner's view of the host: how far behind real time it is drawn")
    print("interp: p95 distance of the interpolated remote player from the sender's ground truth at render time (m)")
    header = (f"{'pairing':<11} {'run':<22} {'ok':<4} {'loss h>j':>8} {'loss j>h':>8} {'expect':>7} {'all h>j':>7} "
              f"{'all j>h':>7} {'rel.viol':>8} {'relay.v':>7} {'clk host':>8} {'clk join':>8} {'|gap|':>6} "
              f"{'C++ max':>7} {'pair':>5} {'delay':>5} {'interp j<h':>10} {'interp h<j':>10} {'yaw p95':>7} {'npc p95':>7}")
    print(header)
    for row in rows:
        if "loss_hj" not in row:
            print(f"{row['pairing']:<11} {row['run']:<22} {'PASS' if row['ok'] else 'FAIL':<4} no joiner in this "
                  f"scenario; relay violations {row['relay_violations']}")
            continue
        yaws = [v for v in (row["yaw_joiner_sees_host_p95"], row["yaw_host_sees_joiner_p95"]) if v is not None]
        yaw = max(yaws) if yaws else None
        gap = row.get("clock_gap")
        over_run = row.get("clock_over_run", {})
        run_max = max((v for k, v in over_run.items() if k.endswith("_max_ms") and k != "pair_max_ms"), default=None)
        pair = over_run.get("pair_max_ms")
        print(f"{row['pairing']:<11} {row['run']:<22} {'PASS' if row['ok'] else 'FAIL':<4} "
              f"{pct(row['span_loss_hj']):>8} {pct(row['span_loss_jh']):>8} {pct(row['loss_expected']):>7} "
              f"{pct(row['loss_hj']):>7} {pct(row['loss_jh']):>7} {row['reliable_violations']:>8} "
              f"{row['relay_violations']:>7} {ms(row['clock_host']):>8} {ms(row['clock_joiner']):>8} "
              f"{'-' if gap is None else format(gap, '.2f'):>6} "
              f"{'-' if run_max is None else format(run_max, '.2f'):>7} {'-' if pair is None else format(pair, '.2f'):>5} "
              f"{'-' if row['render_delay_p50'] is None else format(row['render_delay_p50'], '.0f'):>5} "
              f"{meters(row['interp_joiner_sees_host_p95']):>10} {meters(row['interp_host_sees_joiner_p95']):>10} "
              f"{'-' if yaw is None else format(yaw, '.2f'):>7} {meters(row['npc_p95']):>7}")
    course_rows = [row for row in rows if row["run"].startswith("course-") and "by_motion" in row]
    if not course_rows:
        return
    print("\n=== scripted course: p95 position error (m) / p95 yaw error (deg) per kind of movement")
    print(f"{'pairing':<11} {'run':<24} {'viewer':<17} " + " ".join(f"{m:>15}" for m in motions))
    for row in course_rows:
        for label, by_motion in row["by_motion"].items():
            cells = []
            for motion in motions:
                entry = by_motion.get(motion)
                if not entry or not entry["error_m"].get("count"):
                    cells.append(f"{'-':>15}")
                    continue
                cells.append(f"{entry['error_m']['p95']:.4f}/{entry['yaw_error_deg']['p95']:.2f}".rjust(15))
            print(f"{row['pairing']:<11} {row['run']:<24} {label:<17} " + " ".join(cells))


def worst(rows: list) -> dict:
    pairs = [row for row in rows if "loss_hj" in row]
    if not pairs:
        return {}
    gaps = [row["clock_gap"] for row in pairs if row.get("clock_gap") is not None]
    clocks = [abs(v) for row in pairs for v in (row["clock_host"], row["clock_joiner"]) if v is not None]
    over_runs = [row.get("clock_over_run", {}) for row in pairs]
    cpp_max = [v for o in over_runs for k, v in o.items() if k in ("host_max_ms", "joiner_max_ms") and v is not None]
    cpp_pairs = [o["pair_max_ms"] for o in over_runs if o.get("pair_max_ms") is not None]
    return {
        "runs": len(rows), "passed": sum(1 for row in rows if row["ok"]),
        "reliable_events": sum(row["reliable_events"] for row in pairs),
        "reliable_violations": sum(row["reliable_violations"] for row in pairs),
        "relay_violations": sum(row["relay_violations"] for row in rows),
        "max_abs_clock_error_ms": max(clocks, default=None), "max_clock_gap_ms": max(gaps, default=None),
        "cpp_clock_max_over_run_ms": max(cpp_max, default=None),
        "cpp_instance_gap_max_over_run_ms": max(cpp_pairs, default=None),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--exe", required=True, help="coopnet_v2_demo_client.exe")
    parser.add_argument("--relay", default=default_relay(), help="relay checkout (default: %(default)s)")
    parser.add_argument("--pairings", nargs="*", choices=PAIRINGS, default=list(PAIRINGS))
    parser.add_argument("--only", nargs="*", default=None, help="scenario names (default: run_demo.DEFAULT_ORDER)")
    parser.add_argument("--repeat", type=int, default=1, help="runs per scenario and pairing")
    parser.add_argument("--duration", type=float, default=None, help="override every scenario's duration (s)")
    parser.add_argument("--out", default=os.path.join(ROOT, "build", "v2_demo"))
    parser.add_argument("--skip-world-check", action="store_true")
    args = parser.parse_args(argv)
    args.exe = os.path.abspath(args.exe)
    args.out = os.path.abspath(args.out)
    run_demo, proto, testworld = load_relay(os.path.abspath(args.relay))
    if args.only:
        unknown = [name for name in args.only if name not in run_demo.SCENARIOS]
        if unknown:
            raise SystemExit(f"unknown scenarios {unknown}; run_demo.py has {sorted(run_demo.SCENARIOS)}")
    os.makedirs(args.out, exist_ok=True)
    world_problems = []
    if not args.skip_world_check:
        if subprocess.run([args.exe, "--self-test"]).returncode != 0:
            print("self test of the demo client FAILED")
            return 1
        world_problems = check_world(args.exe, args.out, proto, testworld)
        print("world check (V2DemoWorld vs testworld.py): " + ("OK, entity table, entity truth and player truth "
              "agree" if not world_problems else "FAILED"))
        for problem in world_problems[:20]:
            print(f"  {problem}")
        if world_problems:
            return 1
    results = run_all(args, run_demo)
    rows = [row_of(result) for result in results]
    print_tables(rows, testworld.COURSE_MOTIONS)
    summary = worst(rows)
    failed = [f"{row['pairing']}/{row['run']}" for row in rows if not row["ok"]]
    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as handle:
        json.dump({"exe": args.exe, "relay": os.path.abspath(args.relay), "legs": run_demo.LEGS,
                   "profiles": run_demo.PROFILES, "summary": summary, "rows": rows, "results": results}, handle,
                  indent=1, default=str)
    print(f"\n{summary.get('passed', 0)}/{len(rows)} runs passed" + (f"; failed: {failed}" if failed else "")
          + f"; reliable events {summary.get('reliable_events')}, order violations "
          f"{summary.get('reliable_violations')}; relay violations {summary.get('relay_violations')}; "
          f"final relay clock: max |error| {summary.get('max_abs_clock_error_ms')} ms, max gap "
          f"{summary.get('max_clock_gap_ms')} ms; C++ over the run: max |error| "
          f"{summary.get('cpp_clock_max_over_run_ms')} ms, max instance gap "
          f"{summary.get('cpp_instance_gap_max_over_run_ms')} ms")
    return 1 if failed or not rows else 0


if __name__ == "__main__":
    sys.exit(main())
