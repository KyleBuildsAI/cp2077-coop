"""Scores real display error from two NetProbe audit logs (probe_audit_host.log + probe_audit_joiner.log).

Both bench instances run on one PC and stamp every line with Game.Net_NowMs(), so their clocks
agree. For each direction ("joiner sees host" and "host sees joiner") and each AUDIT line of the
viewer at time t, the error is the distance between the pose the viewer received (rx=) or drew
(drawn=, when init.lua supplies it) for the subject and the subject's own true pose (me=) at
t - delay. The subject's true track is interpolated linearly between its 10 Hz AUDIT samples.
The delay is the render delay; by default it is the measured median one-way latency of that
direction, so the number is "how far off is the shown pose compared to where the subject was one
latency ago". Error against the subject's pose at t itself (delay 0) and an integrity check
(received pose vs. the subject's true pose at the send timestamp, which should be ~0) are
reported too.

Loss, reliable-channel and poll-cost numbers come from the probes' STATS lines. The Phase 1 exit
verdict checks: probe loss ~= simulated relay loss, Net_Poll drain < 0.1 ms/frame on both sides,
and no reliable delivery/order violations.

Usage:
    python tools/sync_audit.py probe_audit_host.log probe_audit_joiner.log
    python tools/sync_audit.py host.log joiner.log --sim-loss-pct 1 --delay-ms 120 --json report.json
Exit code: 0 report printed (1 with --strict when the verdict fails), 2 unusable input.
"""
import argparse
import bisect
import json
import math
import sys
from dataclasses import dataclass, field

DEFAULT_SIM_LOSS_PCT = 1.0
DEFAULT_LOSS_TOL_PCT = 1.0
DEFAULT_POLL_LIMIT_MS = 0.1
DEFAULT_MAX_GAP_MS = 500.0
V1_MISSED_PCT = 16.5  # receiver-side miss rate of CP2077Coop.dll measured on the 22:50 bench


class AuditError(Exception):
    """Raised when a log cannot be used for scoring."""


@dataclass
class AuditLine:
    t: float
    me: tuple
    speed: float | None
    state: str | None
    peer: int | None = None
    rx: tuple | None = None
    rx_sent: float | None = None
    rx_recv: float | None = None
    rx_seq: int | None = None
    rx_state: str | None = None
    drawn: tuple | None = None


@dataclass
class Session:
    path: str
    header: dict
    audits: list = field(default_factory=list)
    stats: list = field(default_factory=list)
    events: list = field(default_factory=list)

    @property
    def role(self):
        return self.header.get("role", "?")

    @property
    def final_stats(self):
        """The final STATS line, or the latest one if the probe never shut down cleanly."""
        for stats in reversed(self.stats):
            if stats.get("final") == "1":
                return stats
        return self.stats[-1] if self.stats else {}


# ------------------------------------------------------------------------------------------------
# parsing
# ------------------------------------------------------------------------------------------------

def parse_fields(text):
    """Splits 'key=value key=value ...' into a dict (EVENT text= keeps the rest of the line)."""
    fields = {}
    tokens = text.split(" ")
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if "=" in token:
            key, value = token.split("=", 1)
            if key == "text":
                fields[key] = " ".join([value] + tokens[index + 1:])
                break
            fields[key] = value
        index += 1
    return fields


def parse_pose(value):
    if value is None or value == "-":
        return None
    parts = value.split(",")
    if len(parts) != 4:
        raise ValueError(f"bad pose {value!r}")
    return tuple(float(part) for part in parts)


def optional_float(value):
    if value is None or value == "-":
        return None
    return float(value)


def parse_audit(fields):
    rx = parse_pose(fields.get("rx"))
    peer = fields.get("peer")
    return AuditLine(
        t=float(fields["t"]),
        me=parse_pose(fields["me"]),
        speed=optional_float(fields.get("spd")),
        state=fields.get("st"),
        peer=int(peer) if peer not in (None, "-") else None,
        rx=rx,
        rx_sent=optional_float(fields.get("rxs")) if rx else None,
        rx_recv=optional_float(fields.get("rxr")) if rx else None,
        rx_seq=int(fields["rxq"]) if rx and "rxq" in fields else None,
        rx_state=fields.get("rxst") if rx else None,
        drawn=parse_pose(fields.get("drawn")),
    )


def load_sessions(path):
    """Returns every SESSION block of a probe log, oldest first."""
    sessions = []
    current = None
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for number, raw in enumerate(handle, 1):
            line = raw.strip()
            if not line:
                continue
            kind, _, rest = line.partition(" ")
            try:
                if kind == "SESSION":
                    current = Session(path=path, header=parse_fields(rest))
                    sessions.append(current)
                    continue
                if current is None:
                    continue
                fields = parse_fields(rest)
                if kind == "AUDIT":
                    current.audits.append(parse_audit(fields))
                elif kind == "STATS":
                    current.stats.append(fields)
                elif kind == "EVENT":
                    current.events.append(fields)
            except (KeyError, ValueError) as error:
                print(f"warning: {path}:{number}: skipped malformed {kind} line ({error})", file=sys.stderr)
    return sessions


def load_session(path, index=-1):
    sessions = load_sessions(path)
    if not sessions:
        raise AuditError(f"{path}: no SESSION line (not a NetProbe audit log?)")
    try:
        session = sessions[index]
    except IndexError as error:
        raise AuditError(f"{path}: has {len(sessions)} session(s), index {index} out of range") from error
    session.audits.sort(key=lambda audit: audit.t)
    return session


# ------------------------------------------------------------------------------------------------
# math
# ------------------------------------------------------------------------------------------------

def percentile(values, fraction):
    """Linear interpolation between closest ranks (the numpy 'linear' default)."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def summarize(values):
    if not values:
        return {"n": 0, "p50": None, "p90": None, "p95": None, "max": None, "mean": None}
    return {
        "n": len(values),
        "p50": percentile(values, 0.50),
        "p90": percentile(values, 0.90),
        "p95": percentile(values, 0.95),
        "max": max(values),
        "mean": sum(values) / len(values),
    }


def distance(first, second):
    return math.sqrt(sum((first[axis] - second[axis]) ** 2 for axis in range(3)))


def yaw_difference(first, second):
    return abs((first - second + 180.0) % 360.0 - 180.0)


class Track:
    """The subject's true pose over time, linearly interpolated between its AUDIT samples."""

    def __init__(self, audits, max_gap_ms):
        self.times = [audit.t for audit in audits]
        self.audits = audits
        self.max_gap_ms = max_gap_ms

    def sample(self, t):
        """Returns ((x, y, z, yaw), state) at time t, or None outside the track or across a gap."""
        if not self.times or t < self.times[0] or t > self.times[-1]:
            return None
        index = bisect.bisect_right(self.times, t)
        if index >= len(self.times):
            last = self.audits[-1]
            return last.me, last.state
        before, after = self.audits[index - 1], self.audits[index]
        span = after.t - before.t
        if span > self.max_gap_ms:
            return None
        weight = 0.0 if span <= 0 else (t - before.t) / span
        position = tuple(before.me[axis] + (after.me[axis] - before.me[axis]) * weight for axis in range(3))
        yaw_step = (after.me[3] - before.me[3] + 180.0) % 360.0 - 180.0
        state = before.state if weight < 0.5 else after.state
        return position + (before.me[3] + yaw_step * weight,), state


# ------------------------------------------------------------------------------------------------
# scoring
# ------------------------------------------------------------------------------------------------

def measured_latencies(viewer):
    """One-way latency samples (recv - send) of distinct received probes in the viewer's AUDIT lines."""
    seen = set()
    samples = []
    for audit in viewer.audits:
        if audit.rx is None or audit.rx_seq in seen:
            continue
        seen.add(audit.rx_seq)
        samples.append(audit.rx_recv - audit.rx_sent)
    return samples


def score_direction(viewer, subject, delay_ms=None, max_gap_ms=DEFAULT_MAX_GAP_MS):
    """Display error of the subject as seen by the viewer."""
    latencies = measured_latencies(viewer)
    latency_p50 = percentile(latencies, 0.5)
    if delay_ms is None:
        delay_ms = latency_p50 if latency_p50 is not None else 0.0
    track = Track(subject.audits, max_gap_ms)
    errors = {"rx": [], "rx_now": [], "drawn": [], "drawn_now": [], "integrity": [], "yaw": [], "staleness": []}
    by_state = {}
    skipped = 0
    for audit in viewer.audits:
        if audit.rx is None:
            continue
        delayed = track.sample(audit.t - delay_ms)
        current = track.sample(audit.t)
        if delayed is None or current is None:
            skipped += 1
            continue
        true_pose, state = delayed
        error = distance(audit.rx, true_pose)
        errors["rx"].append(error)
        errors["rx_now"].append(distance(audit.rx, current[0]))
        errors["yaw"].append(yaw_difference(audit.rx[3], true_pose[3]))
        errors["staleness"].append(audit.t - audit.rx_sent)
        at_send = track.sample(audit.rx_sent)
        if at_send is not None:
            errors["integrity"].append(distance(audit.rx, at_send[0]))
        state = state or audit.rx_state or "unknown"
        bucket = by_state.setdefault(state, {"rx": [], "drawn": []})
        bucket["rx"].append(error)
        if audit.drawn is not None:
            drawn_error = distance(audit.drawn, true_pose)
            errors["drawn"].append(drawn_error)
            errors["drawn_now"].append(distance(audit.drawn, current[0]))
            bucket["drawn"].append(drawn_error)
    return {
        "viewer": viewer.role,
        "subject": subject.role,
        "delay_ms": delay_ms,
        "latency_ms": summarize(latencies),
        "samples": len(errors["rx"]),
        "skipped": skipped,
        "error_m": {name: summarize(values) for name, values in errors.items() if name not in ("yaw", "staleness")},
        "yaw_error_deg": summarize(errors["yaw"]),
        "staleness_ms": summarize(errors["staleness"]),
        "by_state": {
            state: {"rx": summarize(bucket["rx"]), "drawn": summarize(bucket["drawn"])}
            for state, bucket in sorted(by_state.items())
        },
    }


def stat_number(stats, key):
    value = stats.get(key)
    if value in (None, "-"):
        return None
    return float(value)


def link_report(viewer, subject):
    """Loss / reliable / latency numbers the viewer's probe measured for the subject's traffic."""
    mine = viewer.final_stats
    theirs = subject.final_stats
    r_sent_by_subject = stat_number(theirs, "r_sent")
    r_last = stat_number(mine, "r_last")
    return {
        "viewer": viewer.role,
        "subject": subject.role,
        "u_rx": stat_number(mine, "u_rx"),
        "u_expected": stat_number(mine, "u_exp"),
        "u_lost": stat_number(mine, "u_lost"),
        "loss_pct": stat_number(mine, "loss_pct"),
        "u_out_of_order": stat_number(mine, "u_ooo"),
        "subject_u_sent": stat_number(theirs, "u_sent"),
        "r_rx": stat_number(mine, "r_rx"),
        "r_expected": stat_number(mine, "r_exp"),
        "r_dup": stat_number(mine, "r_dup"),
        "r_gap": stat_number(mine, "r_gap"),
        "r_violations": stat_number(mine, "r_viol"),
        "r_undelivered_at_end": (r_sent_by_subject - r_last)
        if r_sent_by_subject is not None and r_last is not None else None,
        "latency_p50_ms": stat_number(mine, "lat_p50"),
        "latency_p95_ms": stat_number(mine, "lat_p95"),
        "latency_max_ms": stat_number(mine, "lat_max"),
        "reliable_latency_p50_ms": stat_number(mine, "rlat_p50"),
        "reliable_latency_p95_ms": stat_number(mine, "rlat_p95"),
        "poll_frames": stat_number(mine, "poll_frames"),
        "poll_avg_ms": stat_number(mine, "poll_avg_ms"),
        "poll_max_ms": stat_number(mine, "poll_max_ms"),
        "poll_capped": stat_number(mine, "poll_capped"),
        "final": mine.get("final") == "1",
    }


def loss_tolerance(sim_loss_pct, expected, tolerance_pct):
    """Allowed |measured - simulated| loss in percentage points: max(fixed tolerance, 3 sigma binomial)."""
    if not expected:
        return tolerance_pct
    probability = min(max(sim_loss_pct / 100.0, 0.0), 1.0)
    sigma_pct = math.sqrt(probability * (1.0 - probability) / expected) * 100.0
    return max(tolerance_pct, 3.0 * sigma_pct)


def verdict(links, sim_loss_pct, loss_tol_pct=DEFAULT_LOSS_TOL_PCT, poll_limit_ms=DEFAULT_POLL_LIMIT_MS):
    checks = []
    for link in links:
        name = f"{link['viewer']} <- {link['subject']}"
        loss = link["loss_pct"]
        if loss is None:
            checks.append({"check": f"loss {name}", "ok": False, "detail": "no probes received"})
        else:
            tolerance = loss_tolerance(sim_loss_pct, link["u_expected"], loss_tol_pct)
            checks.append({
                "check": f"loss {name}",
                "ok": abs(loss - sim_loss_pct) <= tolerance,
                "detail": f"{loss:.2f}% vs simulated {sim_loss_pct:.2f}% (tolerance +-{tolerance:.2f} pp, "
                          f"n={link['u_expected']:.0f}; v1 missed {V1_MISSED_PCT}%)",
            })
        poll = link["poll_avg_ms"]
        checks.append({
            "check": f"poll cost {link['viewer']}",
            "ok": poll is not None and poll < poll_limit_ms,
            "detail": "no frames measured" if poll is None else
            f"{poll:.4f} ms/frame avg over {link['poll_frames']:.0f} frames (limit {poll_limit_ms} ms, "
            f"max {link['poll_max_ms']} ms)",
        })
        violations = (link["r_violations"] or 0) + (link["u_out_of_order"] or 0)
        checks.append({
            "check": f"reliable order {name}",
            "ok": link["r_rx"] is not None and link["r_rx"] > 0 and violations == 0,
            "detail": f"{link['r_rx'] or 0:.0f} delivered of {link['r_expected'] or 0:.0f} expected, "
                      f"dup={link['r_dup'] or 0:.0f} gap={link['r_gap'] or 0:.0f} "
                      f"unreliable out-of-order={link['u_out_of_order'] or 0:.0f}",
        })
    return {"ok": all(check["ok"] for check in checks), "checks": checks}


def analyze(host_path, joiner_path, delay_ms=None, sim_loss_pct=DEFAULT_SIM_LOSS_PCT,
            loss_tol_pct=DEFAULT_LOSS_TOL_PCT, poll_limit_ms=DEFAULT_POLL_LIMIT_MS,
            max_gap_ms=DEFAULT_MAX_GAP_MS, session_index=-1):
    host = load_session(host_path, session_index)
    joiner = load_session(joiner_path, session_index)
    warnings = []
    for session in (host, joiner):
        if session.header.get("clock") != "Net_NowMs":
            warnings.append(f"{session.path}: clock={session.header.get('clock')}; cross-instance latency and "
                            f"display error need Net_NowMs on both sides")
        if not session.audits:
            warnings.append(f"{session.path}: no AUDIT lines (audit toggle off?)")
    if host.audits and joiner.audits:
        overlap = min(host.audits[-1].t, joiner.audits[-1].t) - max(host.audits[0].t, joiner.audits[0].t)
        if overlap <= 0:
            raise AuditError("the two sessions do not overlap in time; pick matching sessions")
    directions = [
        score_direction(joiner, host, delay_ms, max_gap_ms),
        score_direction(host, joiner, delay_ms, max_gap_ms),
    ]
    links = [link_report(joiner, host), link_report(host, joiner)]
    return {
        "host": {"path": host_path, "header": host.header},
        "joiner": {"path": joiner_path, "header": joiner.header},
        "warnings": warnings,
        "directions": directions,
        "links": links,
        "verdict": verdict(links, sim_loss_pct, loss_tol_pct, poll_limit_ms),
        "sim_loss_pct": sim_loss_pct,
    }


# ------------------------------------------------------------------------------------------------
# output
# ------------------------------------------------------------------------------------------------

def fmt(value, pattern="{:.3f}"):
    return "-" if value is None else pattern.format(value)


def summary_row(label, summary, pattern="{:.3f}"):
    return (f"  {label:<26} n={summary['n']:<6} p50={fmt(summary['p50'], pattern):>8} "
            f"p90={fmt(summary['p90'], pattern):>8} p95={fmt(summary['p95'], pattern):>8} "
            f"max={fmt(summary['max'], pattern):>8}")


def print_report(report, out=sys.stdout):
    write = out.write
    write(f"host:   {report['host']['path']} (sid={report['host']['header'].get('sid')}, "
          f"plugin={report['host']['header'].get('plugin')})\n")
    write(f"joiner: {report['joiner']['path']} (sid={report['joiner']['header'].get('sid')}, "
          f"plugin={report['joiner']['header'].get('plugin')})\n")
    for warning in report["warnings"]:
        write(f"WARNING: {warning}\n")
    for direction in report["directions"]:
        write(f"\n== {direction['viewer']} sees {direction['subject']} "
              f"(render delay {direction['delay_ms']:.1f} ms, {direction['samples']} samples, "
              f"{direction['skipped']} outside the subject's track)\n")
        write(summary_row("one-way latency ms", direction["latency_ms"], "{:.1f}") + "\n")
        write(summary_row("staleness ms (t - sent)", direction["staleness_ms"], "{:.1f}") + "\n")
        write(summary_row("display error m (rx)", direction["error_m"]["rx"]) + "\n")
        write(summary_row("  vs true pose now", direction["error_m"]["rx_now"]) + "\n")
        if direction["error_m"]["drawn"]["n"]:
            write(summary_row("display error m (drawn)", direction["error_m"]["drawn"]) + "\n")
            write(summary_row("  vs true pose now", direction["error_m"]["drawn_now"]) + "\n")
        write(summary_row("yaw error deg (rx)", direction["yaw_error_deg"], "{:.1f}") + "\n")
        write(summary_row("integrity m (rx vs sent)", direction["error_m"]["integrity"]) + "\n")
        for state, buckets in direction["by_state"].items():
            write(summary_row(f"state {state} (rx)", buckets["rx"]) + "\n")
            if buckets["drawn"]["n"]:
                write(summary_row(f"state {state} (drawn)", buckets["drawn"]) + "\n")
    write("\n== links (from the receiving probe's final STATS)\n")
    for link in report["links"]:
        write(f"  {link['viewer']} <- {link['subject']}: loss {fmt(link['loss_pct'], '{:.2f}')}% "
              f"({fmt(link['u_lost'], '{:.0f}')} of {fmt(link['u_expected'], '{:.0f}')}), "
              f"latency p50/p95/max {fmt(link['latency_p50_ms'], '{:.1f}')}/"
              f"{fmt(link['latency_p95_ms'], '{:.1f}')}/{fmt(link['latency_max_ms'], '{:.1f}')} ms, "
              f"reliable {fmt(link['r_rx'], '{:.0f}')}/{fmt(link['r_expected'], '{:.0f}')} "
              f"viol={fmt(link['r_violations'], '{:.0f}')} "
              f"rlat p50/p95 {fmt(link['reliable_latency_p50_ms'], '{:.1f}')}/"
              f"{fmt(link['reliable_latency_p95_ms'], '{:.1f}')} ms, "
              f"undelivered at end {fmt(link['r_undelivered_at_end'], '{:.0f}')}, "
              f"poll {fmt(link['poll_avg_ms'], '{:.4f}')} ms/frame (max {fmt(link['poll_max_ms'], '{:.3f}')})"
              f"{'' if link['final'] else ' [no final STATS]'}\n")
    result = report["verdict"]
    write(f"\n== Phase 1 exit criteria (simulated loss {report['sim_loss_pct']}%)\n")
    for check in result["checks"]:
        write(f"  [{'PASS' if check['ok'] else 'FAIL'}] {check['check']}: {check['detail']}\n")
    write(f"VERDICT: {'PASS' if result['ok'] else 'FAIL'}\n")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("host_log")
    parser.add_argument("joiner_log")
    parser.add_argument("--delay-ms", type=float, default=None,
                        help="render delay for the display error (default: measured median one-way latency)")
    parser.add_argument("--sim-loss-pct", type=float, default=DEFAULT_SIM_LOSS_PCT,
                        help="loss the relay simulates (--loss-pct of coopnet_relay.py)")
    parser.add_argument("--loss-tol-pct", type=float, default=DEFAULT_LOSS_TOL_PCT,
                        help="minimum allowed |measured - simulated| loss in percentage points")
    parser.add_argument("--poll-limit-ms", type=float, default=DEFAULT_POLL_LIMIT_MS)
    parser.add_argument("--max-gap-ms", type=float, default=DEFAULT_MAX_GAP_MS,
                        help="do not interpolate the true track across larger gaps")
    parser.add_argument("--session", type=int, default=-1, help="session index in each log (default: last)")
    parser.add_argument("--json", help="also write the full report as JSON")
    parser.add_argument("--strict", action="store_true", help="exit 1 when the verdict fails")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        report = analyze(args.host_log, args.joiner_log, args.delay_ms, args.sim_loss_pct, args.loss_tol_pct,
                         args.poll_limit_ms, args.max_gap_ms, args.session)
    except (AuditError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print_report(report)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
    if args.strict and not report["verdict"]["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
