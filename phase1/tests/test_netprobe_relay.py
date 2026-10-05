"""NetProbe through the real transport stack, in real time.

    LuaJIT (lupa) netprobe.lua -> Game.Net_* via LuaJIT FFI -> coopnet_shim.dll
    (the dllproto coopnet::Transport the plugin's natives call) -> UDP ->
    dllproto/tools/coopnet_relay.py with simulated latency, jitter and loss -> the other instance

Two probe instances (host + joiner) run at 60 fps on the wall clock. Their audit files land in
out/relay/<profile>/ and are scored with tools/sync_audit.py.

Environment:
    COOPNET_SHIM            path to coopnet_shim.dll (default build/shim/Release/coopnet_shim.dll)
    COOPNET_RELAY           path to coopnet_relay.py (default ../dllproto/tools/coopnet_relay.py)
    NETPROBE_RELAY_SECONDS  seconds per profile (default 30)
    NETPROBE_RELAY_PORT     first UDP port to use (default 11797; the bench relay keeps 11779)

Run: python tests/test_netprobe_relay.py   (build the shim first, run_tests.ps1 does both)
"""
import os
import shutil
import socket
import subprocess
import sys
import time
import unittest

import probe_harness as harness
import sync_audit

SHIM = os.environ.get("COOPNET_SHIM", os.path.join(harness.ROOT, "build", "shim", "Release", "coopnet_shim.dll"))
RELAY = os.environ.get("COOPNET_RELAY",
                       os.path.join(os.path.dirname(harness.ROOT), "dllproto", "tools", "coopnet_relay.py"))
SECONDS = float(os.environ.get("NETPROBE_RELAY_SECONDS", "30"))
FIRST_PORT = int(os.environ.get("NETPROBE_RELAY_PORT", "11797"))
OUT = os.path.join(harness.ROOT, "out", "relay")
FRAME_SECONDS = 1.0 / 60.0


def port_is_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def free_port(start):
    for port in range(start, start + 50):
        if port_is_free(port):
            return port
    raise RuntimeError(f"no free UDP port in {start}..{start + 49}")


class RelayProfile:
    """Starts the relay, runs both probes in real time, stops everything, returns the log paths."""

    def __init__(self, name, latency_ms, jitter_ms, loss_pct, seed):
        self.name = name
        self.latency_ms = latency_ms
        self.jitter_ms = jitter_ms
        self.loss_pct = loss_pct
        self.seed = seed
        self.dir = os.path.join(OUT, name)
        shutil.rmtree(self.dir, ignore_errors=True)
        os.makedirs(self.dir)
        self.audit_dir = self.dir.replace("\\", "/") + "/"

    def run(self):
        port = free_port(FIRST_PORT)
        relay_log = open(os.path.join(self.dir, "relay.log"), "w", encoding="utf-8")
        relay = subprocess.Popen(
            [sys.executable, "-u", RELAY, "--port", str(port), "--latency-ms", str(self.latency_ms),
             "--jitter-ms", str(self.jitter_ms), "--loss-pct", str(self.loss_pct), "--seed", str(self.seed)],
            stdout=relay_log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 5.0
            while port_is_free(port) and time.monotonic() < deadline:
                time.sleep(0.05)
            if relay.poll() is not None:
                raise RuntimeError(f"relay exited with {relay.returncode}, see {relay_log.name}")
            return self.drive(port)
        finally:
            relay.terminate()
            relay.wait(timeout=10)
            relay_log.close()

    def drive(self, port):
        host = harness.ProbeInstance(backend=None, shim_path=SHIM)
        joiner = harness.ProbeInstance(backend=None, shim_path=SHIM)
        now = host.lua.globals().SHIM_NOW
        start = now()
        paths = {host: harness.BotPath(0.0, 0.0, start_ms=start),
                 joiner: harness.BotPath(40.0, 10.0, start_ms=start, phase_offset_s=7.0)}
        try:
            assert host.init(role="host", auditDir=self.audit_dir, relayPort=port)
            assert joiner.init(role="joiner", auditDir=self.audit_dir, relayPort=port)
            frames = 0
            started = time.perf_counter()
            next_frame = started
            last = started
            while time.perf_counter() - started < SECONDS:
                current = time.perf_counter()
                delta, last = current - last, current
                t_ms = now()
                for instance, path in paths.items():
                    instance.set_pose(*path.pose(t_ms))
                    instance.update(delta)
                frames += 1
                next_frame += FRAME_SECONDS
                pause = next_frame - time.perf_counter()
                if pause > 0:
                    time.sleep(pause)
                else:
                    next_frame = time.perf_counter()
            host.shutdown()
            joiner.shutdown()
            self.frames = frames
            self.prints = host.prints() + joiner.prints()
        finally:
            for instance in (host, joiner):
                instance.lua.globals().SHIM_DESTROY()
        return (self.audit_dir + "probe_audit_host.log", self.audit_dir + "probe_audit_joiner.log")


class RelayChecks:
    """Mixin with the checks; each subclass sets PROFILE = (name, latency_ms, jitter_ms, loss_pct, seed)."""

    PROFILE = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(SHIM):
            raise AssertionError(f"missing {SHIM}; build tests/shim first (run_tests.ps1 does it)")
        if not os.path.isfile(RELAY):
            raise AssertionError(f"missing relay {RELAY}")
        cls.profile = RelayProfile(*cls.PROFILE)
        cls.host_log, cls.joiner_log = cls.profile.run()
        cls.stats = {"host": harness.final_stats(cls.host_log), "joiner": harness.final_stats(cls.joiner_log)}
        cls.report = sync_audit.analyze(cls.host_log, cls.joiner_log, sim_loss_pct=cls.profile.loss_pct)
        result = subprocess.run(
            [sys.executable, os.path.join(harness.TOOLS, "sync_audit.py"), cls.host_log, cls.joiner_log,
             "--sim-loss-pct", str(cls.profile.loss_pct), "--json", os.path.join(cls.profile.dir, "report.json")],
            capture_output=True, text=True, timeout=60)
        with open(os.path.join(cls.profile.dir, "report.txt"), "w", encoding="utf-8") as handle:
            handle.write(result.stdout + result.stderr)
        print(f"\n--- sync_audit, profile {cls.profile.name} ({cls.profile.frames} frames) ---\n{result.stdout}",
              file=sys.stderr)

    def test_session_ran_cleanly(self):
        for role, stats in self.stats.items():
            self.assertEqual(stats["final"], "1", role)
            self.assertEqual(stats["errors"], "0", role)
            self.assertNotEqual(stats["peer"], "-", role)
        self.assertEqual(self.profile.prints, [])
        for log in (self.host_log, self.joiner_log):
            session = harness.read_lines(log, "SESSION")[0]
            self.assertIn("clock=Net_NowMs", session)
            self.assertIn("plugin=CP2077CoopNet", session)  # Net_Version string of the compiled core
            events = " ".join(harness.read_lines(log, "EVENT"))
            self.assertIn("text=welcome", events)
            self.assertIn("text=peer_join", events)

    def test_probe_loss_matches_relay_loss(self):
        checks = [check for check in self.report["verdict"]["checks"] if check["check"].startswith("loss")]
        self.assertEqual(len(checks), 2)
        for check in checks:
            self.assertTrue(check["ok"], check)
        for stats in self.stats.values():
            self.assertGreater(int(stats["u_exp"]), 0.9 * 30 * (SECONDS - 3))
            self.assertEqual(int(stats["u_ooo"]), 0)

    def test_reliable_channel(self):
        for role, stats in self.stats.items():
            self.assertEqual(int(stats["r_viol"]), 0, role)
            self.assertEqual(int(stats["r_rx"]), int(stats["r_exp"]), role)
            self.assertGreaterEqual(int(stats["r_rx"]), SECONDS - 3, role)

    def test_latency_on_shared_clock(self):
        for role, stats in self.stats.items():
            self.assertGreaterEqual(float(stats["lat_min"]), self.profile.latency_ms, role)
            # relay delay + jitter + up to one frame until the next poll + Python relay scheduling slack
            self.assertLessEqual(float(stats["lat_p50"]), self.profile.latency_ms + self.profile.jitter_ms + 25.0,
                                 role)
            self.assertEqual(int(stats["lat_neg"]), 0, role)

    def test_poll_cost(self):
        for role, stats in self.stats.items():
            self.assertLess(float(stats["poll_avg_ms"]), 0.1, role)
            self.assertEqual(int(stats["poll_frames"]), int(stats["frames"]), role)

    def test_audit_lines_and_integrity(self):
        for log in (self.host_log, self.joiner_log):
            audits = len(harness.read_lines(log, "AUDIT"))
            self.assertAlmostEqual(audits / SECONDS, 10.0, delta=0.5)
        for direction in self.report["directions"]:
            self.assertGreater(direction["samples"], 8 * (SECONDS - 3))
            self.assertLess(direction["error_m"]["integrity"]["p50"], 0.01)
            self.assertLess(direction["error_m"]["integrity"]["p95"], 0.05)

    def test_verdict(self):
        self.assertTrue(self.report["verdict"]["ok"], self.report["verdict"])


class BenchProfile(RelayChecks, unittest.TestCase):
    """The Phase 1 bench link: 115 ms + 20 ms jitter, 1% loss."""
    PROFILE = ("bench_115ms_1pct", 115.0, 20.0, 1.0, 7)


class LossyProfile(RelayChecks, unittest.TestCase):
    """A harsher link so loss is measured on more drops: 60 ms + 10 ms jitter, 5% loss."""
    PROFILE = ("lossy_60ms_5pct", 60.0, 10.0, 5.0, 11)


if __name__ == "__main__":
    unittest.main(verbosity=2)
