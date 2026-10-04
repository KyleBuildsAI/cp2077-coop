"""tools/sync_audit.py on synthetic probe logs with a known display error.

The subject moves along +x at 5 m/s with 10 Hz AUDIT samples. The viewer's AUDIT lines carry a
received pose built from the subject's true track plus a chosen offset, so every expected error is
known in closed form.

Run: python tests/test_sync_audit.py
"""
import json
import math
import os
import random
import shutil
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import sync_audit  # noqa: E402

OUT = os.path.join(ROOT, "out", "synthetic")
T0 = 1_790_000_000_000.0
SPEED = 5.0  # m/s along +x


def true_pose(t, yaw=90.0):
    return (SPEED * (t - T0) / 1000.0, 10.0, 2.0, yaw)


def pose_text(pose):
    return ",".join(f"{value:.6f}" for value in pose)


def session_line(role, clock="Net_NowMs", t=T0):
    return (f"SESSION t={t:.3f} probe=0.1.0 role={role} sid=1 clock={clock} plugin=test relay=127.0.0.1:11779 "
            f"room=default uch=9 rch=25 send_hz=30 audit_hz=10")


def stats_line(t, final=1, loss=1.0, expected=9000, poll=0.03, r_viol=0, r_rx=300, r_sent=300, lat_p50=100.0,
               u_ooo=0):
    lost = round(expected * loss / 100.0)
    return (f"STATS t={t:.3f} final={final} id=1 peer=2 frames=36000 u_sent=9000 u_fail=0 r_sent={r_sent} r_fail=0 "
            f"u_rx={expected - lost} u_exp={expected} u_lost={lost} loss_pct={loss:.3f} u_ooo={u_ooo} u_last={expected} "
            f"r_rx={r_rx} r_exp={r_rx + r_viol} r_dup={r_viol} r_gap=0 r_missing=0 r_viol={r_viol} r_last={r_rx} "
            f"peer_u_sent=9000 peer_r_sent=300 lat_n={expected - lost} lat_min=95.000 lat_p50={lat_p50:.1f} "
            f"lat_p95=110.5 lat_max=130.000 lat_neg=0 rlat_n=300 rlat_p50=100.5 rlat_p95=112.5 rlat_max=400.000 "
            f"poll_frames=36000 poll_avg_ms={poll:.5f} poll_max_ms=1.000 poll_slow=10 poll_msgs=9300 "
            f"poll_max_msgs=3 poll_capped=0 audit_lines=3000 errors=0")


def subject_audits(duration_ms=20000.0, state=lambda t: "walk", gap=None, yaw=90.0):
    lines = []
    t = T0
    while t <= T0 + duration_ms + 1e-6:
        if gap is None or not gap[0] < t < gap[1]:
            lines.append(f"AUDIT t={t:.3f} me={pose_text(true_pose(t, yaw))} spd={SPEED:.2f} st={state(t)} "
                         f"peer=- rx=-")
        t += 100.0
    return lines


def viewer_audit(t, rx, sent, recv, seq, drawn=None):
    line = (f"AUDIT t={t:.3f} me={pose_text((0.0, 0.0, 0.0, 0.0))} spd=0.00 st=idle peer=1 rx={pose_text(rx)} "
            f"rxs={sent:.3f} rxr={recv:.3f} rxq={seq} rxst=walk")
    if drawn is not None:
        line += f" drawn={pose_text(drawn)}"
    return line


def write_log(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return path


def add(pose, offset):
    return tuple(pose[axis] + offset[axis] for axis in range(3)) + (pose[3] + (offset[3] if len(offset) > 3 else 0.0),)


class Scenario:
    """subject = host, viewer = joiner; the joiner sees the host with known errors."""

    def __init__(self, name):
        self.dir = os.path.join(OUT, name)
        shutil.rmtree(self.dir, ignore_errors=True)
        self.host_lines = [session_line("host")]
        self.joiner_lines = [session_line("joiner")]

    def write(self, host_stats=None, joiner_stats=None):
        end = T0 + 20000.0
        host = write_log(os.path.join(self.dir, "probe_audit_host.log"),
                         self.host_lines + [host_stats or stats_line(end)])
        joiner = write_log(os.path.join(self.dir, "probe_audit_joiner.log"),
                           self.joiner_lines + [joiner_stats or stats_line(end)])
        return host, joiner


def constant_offset_scenario(name, offset=(0.0, 0.5, 0.0), latency=100.0, staleness=120.0, drawn_offset=None,
                             count=150):
    scenario = Scenario(name)
    scenario.host_lines += subject_audits()
    for index in range(count):
        t = T0 + 37.0 + index * 100.0
        sent = t - staleness
        drawn = add(true_pose(t - staleness), drawn_offset) if drawn_offset else None
        scenario.joiner_lines.append(viewer_audit(t, add(true_pose(sent), offset), sent, sent + latency, index + 1,
                                                  drawn))
    return scenario


def run_cli(*args):
    return subprocess.run([sys.executable, os.path.join(ROOT, "tools", "sync_audit.py"), *args],
                          capture_output=True, text=True, timeout=60)


class DisplayErrorTests(unittest.TestCase):
    def test_constant_offset_with_measured_delay(self):
        host, joiner = constant_offset_scenario("constant").write()
        report = sync_audit.analyze(host, joiner)
        joiner_sees_host = report["directions"][0]
        self.assertEqual((joiner_sees_host["viewer"], joiner_sees_host["subject"]), ("joiner", "host"))
        self.assertAlmostEqual(joiner_sees_host["delay_ms"], 100.0, places=6)
        self.assertAlmostEqual(joiner_sees_host["latency_ms"]["p50"], 100.0, places=6)
        # rendered 120 ms late but scored against 100 ms ago: offset (0, 0.5) plus 5 m/s * 20 ms along x
        expected = math.hypot(0.5, SPEED * 0.020)
        rx = joiner_sees_host["error_m"]["rx"]
        for key in ("p50", "p90", "p95", "max"):
            self.assertAlmostEqual(rx[key], expected, places=5)
        self.assertAlmostEqual(joiner_sees_host["error_m"]["integrity"]["max"], 0.5, places=5)
        self.assertAlmostEqual(joiner_sees_host["error_m"]["rx_now"]["p50"], math.hypot(0.5, SPEED * 0.120), places=5)
        self.assertAlmostEqual(joiner_sees_host["staleness_ms"]["p50"], 120.0, places=6)
        # the first sample is too early for t - delay to fall on the subject's track
        self.assertEqual(joiner_sees_host["samples"] + joiner_sees_host["skipped"], 150)
        self.assertEqual(joiner_sees_host["skipped"], 1)

    def test_explicit_delay(self):
        host, joiner = constant_offset_scenario("explicit_delay").write()
        report = sync_audit.analyze(host, joiner, delay_ms=120.0)
        rx = report["directions"][0]["error_m"]["rx"]
        self.assertAlmostEqual(rx["p50"], 0.5, places=5)
        self.assertAlmostEqual(rx["max"], 0.5, places=5)

    def test_known_error_distribution(self):
        scenario = Scenario("distribution")
        scenario.host_lines += subject_audits()
        errors = [index * 0.01 for index in range(100)]
        random.Random(4).shuffle(errors)
        for index, error in enumerate(errors):
            t = T0 + 200.0 + index * 100.0
            sent = t - 120.0
            scenario.joiner_lines.append(viewer_audit(t, add(true_pose(sent), (0.0, 0.0, error)), sent, sent + 80.0,
                                                      index + 1))
        host, joiner = scenario.write()
        rx = sync_audit.analyze(host, joiner, delay_ms=120.0)["directions"][0]["error_m"]["rx"]
        # linear interpolation between closest ranks on 0.00 .. 0.99
        self.assertEqual(rx["n"], 100)
        self.assertAlmostEqual(rx["p50"], 0.495, places=6)
        self.assertAlmostEqual(rx["p90"], 0.891, places=6)
        self.assertAlmostEqual(rx["p95"], 0.9405, places=6)
        self.assertAlmostEqual(rx["max"], 0.99, places=6)
        try:
            import numpy
        except ImportError:
            return
        for key, fraction in (("p50", 50), ("p90", 90), ("p95", 95)):
            self.assertAlmostEqual(rx[key], float(numpy.percentile(errors, fraction)), places=6)

    def test_per_state_breakdown(self):
        boundary = T0 + 10000.0
        delay = 120.0
        scenario = Scenario("states")
        scenario.host_lines += subject_audits(state=lambda t: "walk" if t < boundary else "sprint")
        seq = 0
        for index in range(195):
            t = T0 + 150.0 + index * 100.0
            target = t - delay
            if abs(target - boundary) < 100.0:
                continue
            seq += 1
            offset = 0.2 if target < boundary else 2.0
            scenario.joiner_lines.append(viewer_audit(t, add(true_pose(target), (offset, 0.0, 0.0)), target,
                                                      target + 90.0, seq))
        host, joiner = scenario.write()
        states = sync_audit.analyze(host, joiner, delay_ms=delay)["directions"][0]["by_state"]
        self.assertEqual(sorted(states), ["sprint", "walk"])
        self.assertAlmostEqual(states["walk"]["rx"]["p50"], 0.2, places=5)
        self.assertAlmostEqual(states["walk"]["rx"]["max"], 0.2, places=5)
        self.assertAlmostEqual(states["sprint"]["rx"]["p95"], 2.0, places=5)
        self.assertEqual(states["walk"]["rx"]["n"] + states["sprint"]["rx"]["n"], seq)

    def test_drawn_pose(self):
        host, joiner = constant_offset_scenario("drawn", drawn_offset=(1.0, 0.0, 0.0)).write()
        direction = sync_audit.analyze(host, joiner, delay_ms=120.0)["directions"][0]
        self.assertAlmostEqual(direction["error_m"]["drawn"]["p50"], 1.0, places=5)
        # drawn 1 m ahead of where the subject was 120 ms ago, which is 0.6 m behind it now
        self.assertAlmostEqual(direction["error_m"]["drawn_now"]["p50"], 0.4, places=5)
        self.assertAlmostEqual(direction["by_state"]["walk"]["drawn"]["p50"], 1.0, places=5)

    def test_yaw_wraps(self):
        scenario = Scenario("yaw")
        scenario.host_lines += subject_audits(yaw=179.5)
        for index in range(50):
            t = T0 + 500.0 + index * 100.0
            # -179.5 is 1 degree from 179.5 across the +-180 seam, not 359
            scenario.joiner_lines.append(viewer_audit(t, true_pose(t - 100.0, yaw=-179.5), t - 100.0, t, index + 1))
        host, joiner = scenario.write()
        yaw = sync_audit.analyze(host, joiner, delay_ms=100.0)["directions"][0]["yaw_error_deg"]
        self.assertAlmostEqual(yaw["p50"], 1.0, places=5)
        self.assertAlmostEqual(yaw["max"], 1.0, places=5)

    def test_track_gap_is_not_interpolated(self):
        scenario = Scenario("gap")
        scenario.host_lines += subject_audits(gap=(T0 + 5000.0, T0 + 7000.0))
        for index in range(100):
            t = T0 + 200.0 + index * 100.0
            scenario.joiner_lines.append(viewer_audit(t, true_pose(t - 100.0), t - 100.0, t, index + 1))
        host, joiner = scenario.write()
        direction = sync_audit.analyze(host, joiner, delay_ms=100.0)["directions"][0]
        # t - 100 inside (5000, 7000) cannot be scored, nor can t itself for the "now" error
        skipped = [index for index in range(100)
                   if 4900.0 < 200.0 + index * 100.0 - 100.0 < 7000.0 or 4900.0 < 200.0 + index * 100.0 < 7000.0]
        self.assertEqual(direction["skipped"], len(skipped))
        self.assertAlmostEqual(direction["error_m"]["rx"]["max"], 0.0, places=6)


class LinkAndVerdictTests(unittest.TestCase):
    def verdict_for(self, name, **stats):
        scenario = constant_offset_scenario(name, count=20)
        end = T0 + 20000.0
        host, joiner = scenario.write(host_stats=stats_line(end), joiner_stats=stats_line(end, **stats))
        return sync_audit.analyze(host, joiner, sim_loss_pct=1.0)

    def joiner_checks(self, report):
        return {check["check"]: check["ok"] for check in report["verdict"]["checks"]
                if check["check"] in ("loss joiner <- host", "poll cost joiner", "reliable order joiner <- host")}

    def test_bench_like_numbers_pass(self):
        report = self.verdict_for("verdict_pass", loss=1.2)
        self.assertTrue(report["verdict"]["ok"], report["verdict"])
        link = report["links"][0]
        self.assertEqual((link["viewer"], link["subject"]), ("joiner", "host"))
        self.assertAlmostEqual(link["loss_pct"], 1.2)
        self.assertEqual(link["r_undelivered_at_end"], 0.0)

    def test_v1_like_loss_fails(self):
        checks = self.joiner_checks(self.verdict_for("verdict_v1_loss", loss=16.5))
        self.assertEqual(checks, {"loss joiner <- host": False, "poll cost joiner": True,
                                  "reliable order joiner <- host": True})

    def test_slow_poll_fails(self):
        checks = self.joiner_checks(self.verdict_for("verdict_poll", poll=0.2))
        self.assertFalse(checks["poll cost joiner"])
        self.assertTrue(checks["loss joiner <- host"])

    def test_reliable_violation_fails(self):
        checks = self.joiner_checks(self.verdict_for("verdict_reliable", r_viol=1))
        self.assertFalse(checks["reliable order joiner <- host"])
        checks = self.joiner_checks(self.verdict_for("verdict_unreliable_order", u_ooo=2))
        self.assertFalse(checks["reliable order joiner <- host"])

    def test_no_probes_received_fails(self):
        scenario = constant_offset_scenario("verdict_empty", count=5)
        end = T0 + 20000.0
        empty = stats_line(end).replace("loss_pct=1.000", "loss_pct=-")
        host, joiner = scenario.write(joiner_stats=empty)
        report = sync_audit.analyze(host, joiner)
        failed = [check for check in report["verdict"]["checks"] if not check["ok"]]
        self.assertEqual([check["detail"] for check in failed], ["no probes received"])

    def test_loss_tolerance(self):
        self.assertAlmostEqual(sync_audit.loss_tolerance(1.0, 9000, 1.0), 1.0)
        self.assertAlmostEqual(sync_audit.loss_tolerance(5.0, 100, 1.0), 3 * math.sqrt(0.05 * 0.95 / 100) * 100)
        self.assertAlmostEqual(sync_audit.loss_tolerance(1.0, 0, 0.5), 0.5)

    def test_clock_warning(self):
        scenario = constant_offset_scenario("fallback_clock", count=10)
        scenario.host_lines[0] = session_line("host", clock="fallback")
        host, joiner = scenario.write()
        report = sync_audit.analyze(host, joiner)
        self.assertTrue(any("clock=fallback" in warning for warning in report["warnings"]))

    def test_last_session_is_used(self):
        scenario = constant_offset_scenario("sessions")
        old = [session_line("host", t=T0 - 900000.0),
               f"AUDIT t={T0 - 899000.0:.3f} me=0,0,0,0 spd=0.00 st=idle peer=- rx=-",
               stats_line(T0 - 800000.0, loss=50.0)]
        scenario.host_lines = old + scenario.host_lines
        host, joiner = scenario.write()
        report = sync_audit.analyze(host, joiner)
        self.assertEqual(report["directions"][0]["samples"], 149)
        self.assertAlmostEqual(report["links"][1]["loss_pct"], 1.0)
        with self.assertRaises(sync_audit.AuditError):
            sync_audit.analyze(host, joiner, session_index=0)  # the old host session does not overlap

    def test_malformed_lines_are_skipped(self):
        scenario = constant_offset_scenario("malformed", count=10)
        scenario.joiner_lines.insert(3, "AUDIT t=notanumber me=1,2,3,4")
        scenario.joiner_lines.insert(4, "AUDIT t=1 me=1,2,3")
        scenario.joiner_lines.insert(5, "random garbage line")
        host, joiner = scenario.write()
        report = sync_audit.analyze(host, joiner)
        self.assertEqual(report["directions"][0]["samples"] + report["directions"][0]["skipped"], 10)


class CliTests(unittest.TestCase):
    def test_exit_codes_and_json(self):
        host, joiner = constant_offset_scenario("cli").write()
        json_path = os.path.join(OUT, "cli", "report.json")
        result = run_cli(host, joiner, "--strict", "--json", json_path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("VERDICT: PASS", result.stdout)
        self.assertIn("== joiner sees host", result.stdout)
        self.assertIn("state walk (rx)", result.stdout)
        with open(json_path, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertTrue(data["verdict"]["ok"])

        failing = run_cli(host, joiner, "--sim-loss-pct", "20", "--loss-tol-pct", "0.5", "--strict")
        self.assertEqual(failing.returncode, 1)
        self.assertIn("VERDICT: FAIL", failing.stdout)
        lenient = run_cli(host, joiner, "--sim-loss-pct", "20", "--loss-tol-pct", "0.5")
        self.assertEqual(lenient.returncode, 0)

    def test_unusable_input(self):
        missing = run_cli(os.path.join(OUT, "nope.log"), os.path.join(OUT, "nope2.log"))
        self.assertEqual(missing.returncode, 2)
        not_probe = write_log(os.path.join(OUT, "cli_bad", "x.log"), ["hello", "world"])
        result = run_cli(not_probe, not_probe)
        self.assertEqual(result.returncode, 2)
        self.assertIn("no SESSION line", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
