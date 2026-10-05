"""Scores real display error from two NetProbe audit logs (probe_audit_host.log + probe_audit_joiner.log).

Both bench instances run on one PC and stamp every line with Game.Net_NowMs(), so their clocks
agree. For each direction ("joiner sees host" and "host sees joiner") and each AUDIT line of the
viewer at time t, the error is the distance between the pose the viewer received (rx=) or drew
(drawn=, when init.lua supplies it) for the subject and the subject's own true pose (me=) at
t - delay. The subject's true track is interpolated linearly between its 10 Hz AUDIT samples.
Received snapshots default to a median-latency reference, labeled snapshot error. Actual drawn
poses use their per-line rd (render delay), or present time with a warning when rd is absent.
An explicit --delay-ms overrides both. Error against the subject's pose at t itself and an integrity check
(received pose vs. the subject's true pose at the send timestamp, which should be ~0) are
reported too.

Loss, reliable-channel and poll-cost numbers come from the probes' STATS lines. The Phase 1 exit
verdict checks: probe loss ~= simulated relay loss, Net_Poll drain < 0.1 ms/frame on both sides,
and no reliable delivery/order violations. Delivery completeness, freshness, clean final
stats and uninterrupted sessions are required. --min-duration-s sets a soak-duration gate.

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
DEFAULT_LOSS_TOL_PCT = 0.25
DEFAULT_POLL_LIMIT_MS = 0.1
DEFAULT_MAX_GAP_MS = 500.0
DEFAULT_MAX_STALE_MS = 2000.0
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
    render_delay_ms: float | None = None


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
        render_delay_ms=optional_float(fields.get("rd")),
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
        key = (audit.peer, audit.rx_seq, audit.rx_sent)
        if audit.rx is None or key in seen:
            continue
        seen.add(key)
        samples.append(audit.rx_recv - audit.rx_sent)
    return samples


def score_direction(viewer, subject, delay_ms=None, max_gap_ms=DEFAULT_MAX_GAP_MS):
    """Display error of the subject as seen by the viewer."""
    latencies = measured_latencies(viewer)
    latency_p50 = percentile(latencies, 0.5)
    explicit_delay = delay_ms
    if delay_ms is None:
        delay_ms = latency_p50 if latency_p50 is not None else 0.0
    track = Track(subject.audits, max_gap_ms)
    errors = {"rx": [], "rx_now": [], "drawn": [], "drawn_now": [], "integrity": [], "yaw": [], "staleness": []}
    by_state = {}
    skipped = 0
    stale_skipped = 0
    drawn_delays = []
    for audit in viewer.audits:
        if audit.rx is None:
            continue
        errors["staleness"].append(audit.t - audit.rx_sent)
        if audit.t - audit.rx_sent > DEFAULT_MAX_STALE_MS:
            skipped += 1
            stale_skipped += 1
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
        at_send = track.sample(audit.rx_sent)
        if at_send is not None:
            errors["integrity"].append(distance(audit.rx, at_send[0]))
        state = state or audit.rx_state or "unknown"
        bucket = by_state.setdefault(state, {"rx": [], "drawn": [], "rx_now": [], "drawn_now": []})
        bucket["rx"].append(error)
        bucket["rx_now"].append(distance(audit.rx, current[0]))
        if audit.drawn is not None:
            drawn_delay = explicit_delay if explicit_delay is not None else (audit.render_delay_ms or 0.0)
            drawn_truth = track.sample(audit.t - drawn_delay)
            if drawn_truth is None:
                continue
            drawn_delays.append(drawn_delay)
            drawn_error = distance(audit.drawn, drawn_truth[0])
            errors["drawn"].append(drawn_error)
            errors["drawn_now"].append(distance(audit.drawn, current[0]))
            drawn_state = drawn_truth[1] or audit.rx_state or "unknown"
            drawn_bucket = by_state.setdefault(drawn_state, {"rx": [], "drawn": [], "rx_now": [], "drawn_now": []})
            drawn_bucket["drawn"].append(drawn_error)
            drawn_bucket["drawn_now"].append(distance(audit.drawn, current[0]))
    return {
        "viewer": viewer.role,
        "subject": subject.role,
        "delay_ms": delay_ms,
        "latency_ms": summarize(latencies),
        "samples": len(errors["rx"]),
        "skipped": skipped,
        "stale_skipped": stale_skipped,
        "drawn_delay_ms": summarize(drawn_delays),
        "error_m": {name: summarize(values) for name, values in errors.items() if name not in ("yaw", "staleness")},
        "yaw_error_deg": summarize(errors["yaw"]),
        "staleness_ms": summarize(errors["staleness"]),
        "by_state": {
            state: {key: summarize(values) for key, values in bucket.items()}
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
    u_last = stat_number(mine, "u_last")
    u_expected = stat_number(mine, "u_exp")
    r_expected = stat_number(mine, "r_exp")
    u_sent_by_subject = stat_number(theirs, "u_sent")
    viewer_end, subject_end = stat_number(mine, "t"), stat_number(theirs, "t")
    end = min(viewer_end, subject_end) if viewer_end is not None and subject_end is not None else None
    latest_rx = stat_number(mine, "rx_last_ms")
    if latest_rx is None:
        latest_rx = max((a.rx_sent for a in viewer.audits if a.rx_sent is not None), default=None)
    return {
        "viewer": viewer.role,
        "subject": subject.role,
        "u_rx": stat_number(mine, "u_rx"),
        "u_expected": stat_number(mine, "u_exp"),
        "u_lost": stat_number(mine, "u_lost"),
        "loss_pct": stat_number(mine, "loss_pct"),
        "u_out_of_order": stat_number(mine, "u_ooo"),
        "subject_u_sent": stat_number(theirs, "u_sent"),
        "u_tail_missing": u_sent_by_subject - u_last
        if u_sent_by_subject is not None and u_last is not None else None,
        "u_prefix_missing": u_last - u_expected if u_last is not None and u_expected is not None else None,
        "r_prefix_missing": r_last - r_expected if r_last is not None and r_expected is not None else None,
        "rx_age_ms": end - latest_rx if end is not None and latest_rx is not None else None,
        "max_rx_age_ms": max((a.t - a.rx_sent for a in viewer.audits if a.rx_sent is not None), default=0),
        "send_hz": float(subject.header.get("send_hz", 30)),
        "peer_resets": stat_number(mine, "peer_resets") or 0,
        "own_poll": viewer.header.get("ownpoll", "1") != "0",
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
        "poll_p99_ms": stat_number(mine, "poll_p99_ms"),
        "poll_capped": stat_number(mine, "poll_capped"),
        "final": mine.get("final") == "1",
    }


def loss_tolerance(sim_loss_pct, expected, tolerance_pct):
    """Allowed loss difference: max(small explicit floor, 4 sigma binomial)."""
    if not expected:
        return tolerance_pct
    probability = min(max(sim_loss_pct / 100.0, 0.0), 1.0)
    sigma_pct = math.sqrt(probability * (1.0 - probability) / expected) * 100.0
    return max(tolerance_pct, 4.0 * sigma_pct)


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
            "detail": ("external drain not measured; call NetProbe.recordDrain" if not link["own_poll"]
                       else "no frames measured") if poll is None else
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
        tail_limit = max(3.0, math.ceil(link["send_hz"] * DEFAULT_MAX_STALE_MS / 1000.0))
        checks.append({
            "check": f"delivery completeness {name}",
            "ok": link["r_undelivered_at_end"] is not None and 0 <= link["r_undelivered_at_end"] <= 3
                  and link["u_tail_missing"] is not None and 0 <= link["u_tail_missing"] <= tail_limit
                  and link["u_prefix_missing"] is not None and 0 <= link["u_prefix_missing"] <= tail_limit
                  and link["r_prefix_missing"] is not None and 0 <= link["r_prefix_missing"] <= 3
                  and link["rx_age_ms"] is not None and -500 <= link["rx_age_ms"] <= DEFAULT_MAX_STALE_MS,
            "detail": f"reliable tail={link['r_undelivered_at_end']} (limit 3), "
                      f"probe tail={link['u_tail_missing']} (limit {tail_limit}), "
                      f"missing prefix probes/reliable={link['u_prefix_missing']}/{link['r_prefix_missing']}, "
                      f"last probe age={link['rx_age_ms']} ms (limit {DEFAULT_MAX_STALE_MS})",
        })
        checks.append({"check": f"probe freshness {name}",
                       "ok": link["max_rx_age_ms"] <= DEFAULT_MAX_STALE_MS,
                       "detail": f"maximum observed probe age={link['max_rx_age_ms']:.1f} ms "
                                 f"(limit {DEFAULT_MAX_STALE_MS}); recovered stalls still count"})
        checks.append({
            "check": f"session continuity {link['viewer']}",
            "ok": link["final"] and link["peer_resets"] == 0,
            "detail": f"final STATS={link['final']}, peer resets={link['peer_resets']}; "
                      "a restarted or unfinished segment is not a completed soak",
        })
    return {"ok": all(check["ok"] for check in checks), "checks": checks}


def analyze(host_path, joiner_path, delay_ms=None, sim_loss_pct=DEFAULT_SIM_LOSS_PCT,
            loss_tol_pct=DEFAULT_LOSS_TOL_PCT, poll_limit_ms=DEFAULT_POLL_LIMIT_MS,
            max_gap_ms=DEFAULT_MAX_GAP_MS, session_index=None, min_duration_s=0):
    selected = -1 if session_index is None else session_index
    host = load_session(host_path, selected)
    joiner = load_session(joiner_path, selected)
    histories = [load_sessions(host_path), load_sessions(joiner_path)]
    warnings = []
    for session in (host, joiner):
        if session.header.get("clock") != "Net_NowMs":
            warnings.append(f"{session.path}: clock={session.header.get('clock')}; cross-instance latency and "
                            f"display error need Net_NowMs on both sides")
        if not session.audits:
            warnings.append(f"{session.path}: no AUDIT lines (audit toggle off?)")
        if not any(a.drawn is not None for a in session.audits):
            warnings.append(f"{session.path}: no drawn samples; received snapshots do not measure screen error")
        elif delay_ms is None and any(a.drawn is not None and a.render_delay_ms is None for a in session.audits):
            warnings.append(f"{session.path}: drawn samples missing rd; those samples use present time, not latency")
        if session.header.get("ownpoll") == "0" and not stat_number(session.final_stats, "poll_frames"):
            warnings.append(f"{session.path}: external drain cost not measured; call NetProbe.recordDrain")
    if host.audits and joiner.audits:
        overlap = min(host.audits[-1].t, joiner.audits[-1].t) - max(host.audits[0].t, joiner.audits[0].t)
        if overlap <= 0:
            raise AuditError("the two sessions do not overlap in time; pick matching sessions")
    directions = [
        score_direction(joiner, host, delay_ms, max_gap_ms),
        score_direction(host, joiner, delay_ms, max_gap_ms),
    ]
    links = [link_report(joiner, host), link_report(host, joiner)]
    result = verdict(links, sim_loss_pct, loss_tol_pct, poll_limit_ms)
    for session in (host, joiner):
        gaps = [right.t - left.t for left, right in zip(session.audits, session.audits[1:])]
        max_gap = max(gaps, default=0.0)
        result["checks"].append({"check": f"audit continuity {session.role}",
            "ok": len(session.audits) >= 2 and max_gap <= DEFAULT_MAX_STALE_MS,
            "detail": f"{len(session.audits)} samples, maximum gap {max_gap:.1f} ms "
                      f"(limit {DEFAULT_MAX_STALE_MS}); missing intervals cannot count as a continuous soak"})
    # Keep all earlier evidence visible and never silently call a short last session a full soak.
    counts = [len(history) for history in histories]
    if max(counts) > 1:
        warnings.append(f"logs contain {counts[0]}/{counts[1]} sessions; report scores selected segments only")
        if session_index is None:
            result["checks"].append({"check": "session selection", "ok": False,
                "detail": "multiple sessions preserved; explicitly select --session to score a segment, not the full soak"})
    end = min(stat_number(s.final_stats, "t") or 0 for s in (host, joiner))
    start = max(float(s.header.get("t", end)) for s in (host, joiner))
    session_duration_s = max(0.0, (end - start) / 1000.0)
    # Duration is backed by both observed tracks, not just distant SESSION/final STATS lines.
    if host.audits and joiner.audits:
        observed_start = max(start, host.audits[0].t, joiner.audits[0].t)
        observed_end = min(end, host.audits[-1].t, joiner.audits[-1].t)
        duration_s = max(0.0, (observed_end - observed_start) / 1000.0)
    else:
        duration_s = 0.0
    result["checks"].append({"check": "duration", "ok": duration_s >= min_duration_s,
        "detail": f"observed audit overlap {duration_s:.1f} s, required {min_duration_s:.1f} s "
                  f"(session-clock overlap {session_duration_s:.1f} s)"})
    result["ok"] = all(check["ok"] for check in result["checks"])
    return {
        "host": {"path": host_path, "header": host.header},
        "joiner": {"path": joiner_path, "header": joiner.header},
        "warnings": warnings,
        "directions": directions,
        "links": links,
        "verdict": result,
        "duration_s": duration_s,
        "session_duration_s": session_duration_s,
        "session_history": [[{"header": s.header, "stats": s.stats, "events": s.events} for s in history]
                            for history in histories],
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
              f"(snapshot reference delay {direction['delay_ms']:.1f} ms, {direction['samples']} samples, "
              f"{direction['skipped']} outside the subject's track)\n")
        write(summary_row("one-way latency ms", direction["latency_ms"], "{:.1f}") + "\n")
        write(summary_row("staleness ms (t - sent)", direction["staleness_ms"], "{:.1f}") + "\n")
        write(summary_row("snapshot error m (rx)", direction["error_m"]["rx"]) + "\n")
        write(summary_row("  vs true pose now", direction["error_m"]["rx_now"]) + "\n")
        if direction["error_m"]["drawn"]["n"]:
            write(summary_row("actual render delay ms", direction["drawn_delay_ms"], "{:.1f}") + "\n")
            write(summary_row("display error m (drawn)", direction["error_m"]["drawn"]) + "\n")
            write(summary_row("  vs true pose now", direction["error_m"]["drawn_now"]) + "\n")
        write(summary_row("yaw error deg (rx)", direction["yaw_error_deg"], "{:.1f}") + "\n")
        write(summary_row("integrity m (rx vs sent)", direction["error_m"]["integrity"]) + "\n")
        for state, buckets in direction["by_state"].items():
            write(summary_row(f"state {state} (rx)", buckets["rx"]) + "\n")
            if buckets["drawn"]["n"]:
                write(summary_row(f"state {state} (drawn)", buckets["drawn"]) + "\n")
            write(summary_row(f"state {state} rx now", buckets["rx_now"]) + "\n")
            if buckets["drawn_now"]["n"]:
                write(summary_row(f"state {state} drawn now", buckets["drawn_now"]) + "\n")
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
                        help="override both directions' reference delay; drawn defaults to per-line rd or present time")
    parser.add_argument("--sim-loss-pct", type=float, default=DEFAULT_SIM_LOSS_PCT,
                        help="loss the relay simulates (--loss-pct of coopnet_relay.py)")
    parser.add_argument("--loss-tol-pct", type=float, default=DEFAULT_LOSS_TOL_PCT,
                        help="minimum allowed |measured - simulated| loss in percentage points")
    parser.add_argument("--poll-limit-ms", type=float, default=DEFAULT_POLL_LIMIT_MS)
    parser.add_argument("--max-gap-ms", type=float, default=DEFAULT_MAX_GAP_MS,
                        help="do not interpolate the true track across larger gaps")
    parser.add_argument("--session", type=int, default=None,
                        help="explicitly select a segment in each log; multiple sessions cannot pass by default")
    parser.add_argument("--min-duration-s", type=float, default=0,
                        help="required duration for the selected overlap (use 1800 for a 30-minute soak)")
    parser.add_argument("--json", help="also write the full report as JSON")
    parser.add_argument("--strict", action="store_true", help="exit 1 when the verdict fails")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        report = analyze(args.host_log, args.joiner_log, args.delay_ms, args.sim_loss_pct, args.loss_tol_pct,
                         args.poll_limit_ms, args.max_gap_ms, args.session, args.min_duration_s)
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
