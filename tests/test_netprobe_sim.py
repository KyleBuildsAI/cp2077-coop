"""NetProbe under LuaJIT 2.1 (lupa) with a mocked Game.Net_* on an in-memory lossy link.

Two probe instances (host + joiner) run on a virtual clock through SimNet, which simulates
latency, jitter and loss the way CP2077CoopNet + coopnet_relay.py behave. The audit files they
write are real probe output (kept in out/sim/) and are scored with tools/sync_audit.py.

Run: python tests/test_netprobe_sim.py   (needs lupa; run_tests.ps1 installs it into .pydeps)
"""
import heapq
import math
import os
import random
import shutil
import subprocess
import sys
import unittest

import probe_harness as harness
import sync_audit

OUT = os.path.join(harness.ROOT, "out", "sim")
FRAME_MS = 1000.0 / 60.0
ORACLE_DELAY_MS = 150.0


def fresh_dir(name):
    path = os.path.join(OUT, name)
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path)
    return path.replace("\\", "/") + "/"


def run_session(net, seconds, audit_dir, seed=5, joiner_drawn_oracle=False, frame_jitter=True, **config):
    """Runs host + joiner probes for `seconds` of virtual time; returns (host, joiner, paths)."""
    host = harness.ProbeInstance(net.endpoint())
    joiner = harness.ProbeInstance(net.endpoint())
    host_path = harness.BotPath(0.0, 0.0, start_ms=net.now_ms)
    joiner_path = harness.BotPath(40.0, 10.0, start_ms=net.now_ms, phase_offset_s=7.0)
    joiner_config = dict(config)
    if joiner_drawn_oracle:
        def drawn():
            # where a perfect avatar would be drawn with a ORACLE_DELAY_MS render delay
            x, y, z, forward_x, forward_y = host_path.pose(net.now_ms - ORACLE_DELAY_MS)
            return x, y, z, harness.yaw_from_forward(forward_x, forward_y)
        joiner_config["drawnProvider"] = drawn
        joiner_config["renderDelayProvider"] = lambda: ORACLE_DELAY_MS
    assert host.init(role="host", auditDir=audit_dir, **config)
    assert joiner.init(role="joiner", auditDir=audit_dir, **joiner_config)
    rng = random.Random(seed)
    elapsed = 0.0
    while elapsed < seconds * 1000.0:
        step = FRAME_MS * (rng.uniform(0.85, 1.2) if frame_jitter else 1.0)
        net.advance(step)
        elapsed += step
        for instance, path in ((host, host_path), (joiner, joiner_path)):
            instance.set_pose(*path.pose(net.now_ms))
            instance.update(step / 1000.0)
    host.shutdown()
    joiner.shutdown()
    return host, joiner, (audit_dir + "probe_audit_host.log", audit_dir + "probe_audit_joiner.log")


def expected_lost(net, source_id, target_id, stats):
    """Probes the link dropped inside the window [first received seq, last received seq]."""
    last = int(stats["u_last"])
    first = last - int(stats["u_exp"]) + 1
    dropped = net.dropped_probe_seqs.get((source_id, target_id), [])
    return sum(1 for seq in dropped if first <= seq <= last)


class LossyLinkSession(unittest.TestCase):
    """300 s at the Phase 1 bench profile: 115 ms +20 ms jitter, 1% loss."""

    SIM_LOSS = 1.0
    SECONDS = 300

    @classmethod
    def setUpClass(cls):
        cls.net = harness.SimNet(latency_ms=115.0, jitter_ms=20.0, loss_pct=cls.SIM_LOSS, seed=2077)
        audit_dir = fresh_dir("bench_1pct")
        cls.host, cls.joiner, (cls.host_log, cls.joiner_log) = run_session(
            cls.net, cls.SECONDS, audit_dir, joiner_drawn_oracle=True)
        cls.host_stats = harness.final_stats(cls.host_log)
        cls.joiner_stats = harness.final_stats(cls.joiner_log)
        cls.report = sync_audit.analyze(cls.host_log, cls.joiner_log, sim_loss_pct=cls.SIM_LOSS)

    def test_final_stats_written(self):
        self.assertEqual(self.host_stats["final"], "1")
        self.assertEqual(self.joiner_stats["final"], "1")
        self.assertEqual(self.host_stats["errors"], "0")
        self.assertEqual(self.joiner_stats["errors"], "0")

    def test_loss_counts_exactly_what_the_link_dropped(self):
        # host is peer 1, joiner peer 2; the joiner's stats describe host -> joiner traffic
        for stats, source, target in ((self.joiner_stats, 1, 2), (self.host_stats, 2, 1)):
            lost = int(stats["u_lost"])
            self.assertEqual(lost, expected_lost(self.net, source, target, stats))
            expected = int(stats["u_exp"])
            self.assertGreater(expected, 0.98 * 30 * self.SECONDS)
            sigma = math.sqrt(0.01 * 0.99 / expected) * 100.0
            self.assertLessEqual(abs(float(stats["loss_pct"]) - self.SIM_LOSS), 4.0 * sigma)

    def test_send_and_audit_rates(self):
        for stats, log in ((self.host_stats, self.host_log), (self.joiner_stats, self.joiner_log)):
            self.assertAlmostEqual(int(stats["u_sent"]) / self.SECONDS, 30.0, delta=0.3)
            self.assertAlmostEqual(int(stats["r_sent"]) / self.SECONDS, 1.0, delta=0.02)
            audits = len(harness.read_lines(log, "AUDIT"))
            self.assertAlmostEqual(audits / self.SECONDS, 10.0, delta=0.1)
            self.assertEqual(int(stats["u_fail"]), 0)

    def test_reliable_channel_clean(self):
        for stats in (self.host_stats, self.joiner_stats):
            self.assertEqual(int(stats["r_viol"]), 0)
            self.assertEqual(int(stats["r_rx"]), int(stats["r_exp"]))
            self.assertGreaterEqual(int(stats["r_rx"]), self.SECONDS - 2)

    def test_latency_matches_link(self):
        # 115 ms + U(0, 20) jitter + waiting for the next frame's poll (0..20 ms)
        for stats in (self.host_stats, self.joiner_stats):
            self.assertGreaterEqual(float(stats["lat_min"]), 115.0)
            self.assertTrue(120.0 <= float(stats["lat_p50"]) <= 145.0, stats["lat_p50"])
            self.assertLessEqual(float(stats["lat_max"]), 115.0 + 20.0 + 25.0)
            self.assertEqual(int(stats["lat_neg"]), 0)
        for direction in self.report["directions"]:
            self.assertAlmostEqual(direction["delay_ms"], float(self.joiner_stats["lat_p50"]), delta=6.0)

    def test_poll_cost_measured_every_frame(self):
        for stats in (self.host_stats, self.joiner_stats):
            self.assertEqual(int(stats["poll_frames"]), int(stats["frames"]))
            self.assertEqual(int(stats["poll_capped"]), 0)
            self.assertLess(float(stats["poll_avg_ms"]), 0.1)

    def test_audit_integrity_and_display_error(self):
        for direction in self.report["directions"]:
            errors = direction["error_m"]
            self.assertGreater(direction["samples"], 9 * self.SECONDS)
            # The received pose is the subject's true pose at its send time. What is left is the floor of
            # interpolating the subject's ~10 Hz samples: chord sagitta at 14 m/s on the 8 m circle is
            # ~4 cm, and a speed change inside one sample interval gives up to ~0.3 m.
            self.assertLess(errors["integrity"]["p50"], 0.01)
            self.assertLess(errors["integrity"]["p95"], 0.05)
            self.assertLess(errors["integrity"]["max"], 0.3)
            # rendering one latency late removes most of the error compared to "now"
            self.assertLess(errors["rx"]["p50"], errors["rx_now"]["p50"])
            self.assertLess(errors["rx"]["p95"], 0.6)
            states = direction["by_state"]
            self.assertEqual(set(states), {"idle", "walk", "run", "sprint", "fast"})
            self.assertLess(states["idle"]["rx"]["p50"], 0.01)
            self.assertGreater(states["sprint"]["rx"]["p50"], states["walk"]["rx"]["p50"])

    def test_drawn_oracle_scores_zero_at_its_render_delay(self):
        report = sync_audit.analyze(self.host_log, self.joiner_log, delay_ms=ORACLE_DELAY_MS,
                                    sim_loss_pct=self.SIM_LOSS)
        joiner_sees_host = report["directions"][0]
        self.assertEqual((joiner_sees_host["viewer"], joiner_sees_host["subject"]), ("joiner", "host"))
        # same 10 Hz interpolation floor as the integrity check
        self.assertLess(joiner_sees_host["error_m"]["drawn"]["p50"], 0.01)
        self.assertLess(joiner_sees_host["error_m"]["drawn"]["p95"], 0.05)
        self.assertLess(joiner_sees_host["error_m"]["drawn"]["max"], 0.3)
        self.assertGreater(joiner_sees_host["error_m"]["drawn_now"]["p50"], 0.1)
        self.assertEqual(report["directions"][1]["error_m"]["drawn"]["n"], 0)

    def test_verdict_passes(self):
        failed = [check for check in self.report["verdict"]["checks"] if not check["ok"]]
        self.assertEqual(failed, [])

    def test_cli_report(self):
        result = subprocess.run(
            [sys.executable, os.path.join(harness.TOOLS, "sync_audit.py"), self.host_log, self.joiner_log,
             "--sim-loss-pct", str(self.SIM_LOSS), "--strict", "--json", os.path.join(OUT, "bench_1pct.json")],
            capture_output=True, text=True, timeout=60)
        with open(os.path.join(OUT, "bench_1pct_report.txt"), "w", encoding="utf-8") as handle:
            handle.write(result.stdout)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("VERDICT: PASS", result.stdout)
        self.assertIn("joiner sees host", result.stdout)


class HeavyLossSession(unittest.TestCase):
    """120 s at 5% loss: the probe still matches the link and the reliable channel stays ordered."""

    def test_five_percent(self):
        net = harness.SimNet(latency_ms=160.0, jitter_ms=30.0, loss_pct=5.0, seed=99)
        _, _, (host_log, joiner_log) = run_session(net, 120, fresh_dir("heavy_5pct"), seed=8)
        for log, source, target in ((joiner_log, 1, 2), (host_log, 2, 1)):
            stats = harness.final_stats(log)
            self.assertEqual(int(stats["u_lost"]), expected_lost(net, source, target, stats))
            self.assertEqual(int(stats["r_viol"]), 0)
            self.assertEqual(int(stats["r_rx"]), int(stats["r_exp"]))
            # lost reliable attempts are resent after an RTO, so the reliable tail is longer
            self.assertGreater(float(stats["rlat_max"]), float(stats["lat_max"]) + 100.0)
        report = sync_audit.analyze(host_log, joiner_log, sim_loss_pct=5.0)
        self.assertTrue(report["verdict"]["ok"], report["verdict"])
        wrong = sync_audit.analyze(host_log, joiner_log, sim_loss_pct=0.0, loss_tol_pct=0.5)
        self.assertFalse(wrong["verdict"]["ok"])


class ReliableFaults(unittest.TestCase):
    """A broken reliable channel must show up as violations and fail the verdict."""

    def run_fault(self, name, fault):
        net = harness.SimNet(latency_ms=40.0, jitter_ms=5.0, loss_pct=0.0, seed=3, faults={fault: 4})
        _, _, (host_log, joiner_log) = run_session(net, 8, fresh_dir(name))
        stats = harness.final_stats(joiner_log)  # host -> joiner carries the fault (host sends first)
        report = sync_audit.analyze(host_log, joiner_log, sim_loss_pct=0.0)
        reliable = [check for check in report["verdict"]["checks"] if check["check"].startswith("reliable")]
        return stats, reliable, harness.read_lines(joiner_log, "EVENT")

    def test_duplicate(self):
        stats, reliable, events = self.run_fault("fault_dup", "reliable_dup")
        self.assertEqual(int(stats["r_dup"]), 1)
        self.assertEqual(int(stats["r_viol"]), 1)
        self.assertFalse(reliable[0]["ok"])
        self.assertTrue(any("reliable_dup" in line for line in events))

    def test_drop(self):
        stats, reliable, events = self.run_fault("fault_drop", "reliable_drop")
        self.assertEqual(int(stats["r_gap"]), 1)
        self.assertEqual(int(stats["r_missing"]), 1)
        self.assertFalse(reliable[0]["ok"])
        self.assertTrue(any("reliable_gap" in line for line in events))

    def test_reorder(self):
        stats, reliable, _ = self.run_fault("fault_late", "reliable_late")
        self.assertEqual(int(stats["r_gap"]), 1)  # the next message overtakes the late one ...
        self.assertEqual(int(stats["r_dup"]), 1)  # ... which then arrives behind it
        self.assertFalse(reliable[0]["ok"])


class CountingEndpoint(harness.SimEndpoint):
    """Advances a fake CPU clock by 20 us per Net_Poll call."""

    def __init__(self, net):
        super().__init__(net)
        self.cpu_seconds = 0.0

    def poll(self):
        self.cpu_seconds += 0.00002
        return super().poll()


class ProbeUnitTests(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_dir(self._testMethodName)

    def test_missing_net_connect_is_a_noop_that_logs_once(self):
        probe = harness.ProbeInstance(backend=None)  # Game exists, Net_* natives do not
        self.assertFalse(probe.init(role="host", auditDir=self.dir))
        for _ in range(200):
            probe.update(0.016)
        probe.shutdown()
        self.assertFalse(probe.init(role="host", auditDir=self.dir))
        probe.update(0.016)
        prints = probe.prints()
        self.assertEqual(len(prints), 1, prints)
        self.assertIn("Net_Connect", prints[0])
        self.assertEqual(os.listdir(self.dir), [])
        self.assertIn("off (CP2077CoopNet natives missing)", probe.probe.summary())

    def test_missing_game_table(self):
        probe = harness.ProbeInstance(backend=None, game=False)
        self.assertFalse(probe.init(role="host", auditDir=self.dir))
        probe.update(0.016)
        self.assertEqual(len(probe.prints()), 1)

    def test_only_connect_present_is_still_disabled(self):
        net = harness.SimNet(loss_pct=0.0)
        probe = harness.ProbeInstance(net.endpoint(), natives="Net_Connect")
        self.assertFalse(probe.init(role="host", auditDir=self.dir))
        self.assertEqual(len(probe.prints()), 1)

    def test_missing_clock_uses_fallback(self):
        net = harness.SimNet(loss_pct=0.0)
        probe = harness.ProbeInstance(net.endpoint(), natives="Net_Connect Net_Send Net_Poll")
        self.assertTrue(probe.init(role="host", auditDir=self.dir))
        for _ in range(10):
            probe.update(0.1)
        probe.shutdown()
        session = harness.read_lines(self.dir + "probe_audit_host.log", "SESSION")[0]
        self.assertIn("clock=fallback", session)
        self.assertIn("plugin=?", session)
        self.assertEqual(len(probe.prints()), 1)
        self.assertIn("Net_NowMs", probe.prints()[0])

    def test_upvalue_limit(self):
        probe = harness.ProbeInstance(harness.SimNet().endpoint())
        nups = probe.lua.execute("""
            local probe = require("netprobe")
            local worst, worstName, count = 0, "", 0
            for name, value in pairs(probe) do
                if type(value) == "function" then
                    count = count + 1
                    local ups = debug.getinfo(value, "u").nups
                    if ups > worst then worst, worstName = ups, name end
                end
            end
            return worst, worstName, count
        """)
        worst, name, count = nups
        self.assertGreater(count, 30)
        self.assertLessEqual(worst, 1, f"{name} has {worst} upvalues")
        # the whole chunk compiles under LuaJIT 2.1, which rejects functions with more than 60 upvalues
        chunk, error = probe.lua.execute("local chunk, err = loadfile((...)); return chunk ~= nil, err",
                                         os.path.join(harness.LUA_DIR, "netprobe.lua"))
        self.assertTrue(chunk, error)

    def test_poll_is_bounded_per_frame(self):
        net = harness.SimNet(loss_pct=0.0)
        endpoint = net.endpoint()
        probe = harness.ProbeInstance(endpoint)
        self.assertTrue(probe.init(role="host", auditDir=self.dir, maxPollPerFrame=64))
        for seq in range(1, 501):
            net.counter += 1
            heapq.heappush(endpoint.inbox, (0.0, net.counter, f"7|9|NP1|u|42|{seq}|{net.now_ms:.3f}|1|2|3|90|walk"))
        probe.update(0.016)
        poll = probe.state().poll
        self.assertEqual(poll.maxMsgs, 64)
        self.assertEqual(poll.capped, 1)
        for _ in range(8):
            net.advance(16.0)
            probe.update(0.016)
        peer = probe.state().peers[7]
        self.assertEqual(peer.uRx, 500)
        self.assertEqual(peer.lastSeq, 500)
        self.assertEqual(probe.state().poll.msgs, 500 + 1)  # + the welcome event

    def test_poll_cost_uses_the_cpu_clock_delta(self):
        net = harness.SimNet(loss_pct=0.0)
        endpoint = CountingEndpoint(net)
        net.endpoints.append(endpoint)
        probe = harness.ProbeInstance(endpoint)
        self.assertTrue(probe.init(role="host", auditDir=self.dir, cpuClock=lambda: endpoint.cpu_seconds))
        probe.update(0.016)  # drains the welcome: 2 polls
        for _ in range(99):
            net.advance(16.0)
            probe.update(0.016)  # empty inbox: 1 poll
        poll = probe.state().poll
        self.assertEqual(poll.frames, 100)
        self.assertAlmostEqual(poll.sumMs, (2 + 99) * 0.02, places=9)
        probe.shutdown()
        stats = harness.final_stats(self.dir + "probe_audit_host.log")
        self.assertEqual(stats["poll_avg_ms"], "0.02020")

    def test_audit_toggle(self):
        net = harness.SimNet(loss_pct=0.0)
        probe = harness.ProbeInstance(net.endpoint())
        self.assertTrue(probe.init(role="host", auditDir=self.dir))
        path = harness.BotPath(0, 0, start_ms=net.now_ms)

        def run(seconds):
            for _ in range(int(seconds * 60)):
                net.advance(FRAME_MS)
                probe.set_pose(*path.pose(net.now_ms))
                probe.update(FRAME_MS / 1000.0)
            return len(harness.read_lines(self.dir + "probe_audit_host.log", "AUDIT"))

        first = run(2.0)
        probe.probe.setAudit(False)
        stats_before = len(harness.read_lines(self.dir + "probe_audit_host.log", "STATS"))
        second = run(2.0)
        stats_after = len(harness.read_lines(self.dir + "probe_audit_host.log", "STATS"))
        probe.probe.setAudit(True)
        third = run(1.0)
        self.assertAlmostEqual(first, 20, delta=1)
        self.assertEqual(second, first)
        self.assertAlmostEqual(stats_after - stats_before, 2, delta=1)
        self.assertAlmostEqual(third - second, 10, delta=1)

    def test_external_poll_with_handle_message(self):
        net = harness.SimNet(loss_pct=0.0)
        endpoint = net.endpoint()
        probe = harness.ProbeInstance(endpoint)
        self.assertTrue(probe.init(role="host", auditDir=self.dir, ownPoll=False))
        for _ in range(5):
            probe.update(0.016)
        self.assertEqual(endpoint.polls, 0)
        handle = probe.probe.handleMessage
        self.assertTrue(handle(3, 9, f"NP1|u|5|1|{net.now_ms - 100:.3f}|1|2|3|45|run"))
        self.assertTrue(handle(3, 25, f"NP1|r|5|1|{net.now_ms - 100:.3f}|1|1"))
        self.assertFalse(handle(3, 1, "snapshot|whatever"))
        self.assertFalse(handle(3, 9, "not a probe"))
        self.assertFalse(handle(0, 0, "peer_join 3"))  # transport events stay available to others
        peer = probe.state().peers[3]
        self.assertEqual((peer.uRx, peer.rRx), (1, 1))
        self.assertAlmostEqual(peer.pose.yaw, 45.0)
        self.assertTrue(probe.probe.recordDrain(0.05, 3, False))
        self.assertEqual(probe.state().poll.frames, 1)
        self.assertEqual(probe.state().poll.sumMs, 0.05)

    def test_other_messages_are_forwarded(self):
        net = harness.SimNet(loss_pct=0.0)
        endpoint = net.endpoint()
        probe = harness.ProbeInstance(endpoint)
        received = []
        self.assertTrue(probe.init(role="host", auditDir=self.dir,
                                   onOtherMessage=lambda sender, channel, payload: received.append(
                                       (int(sender), int(channel), payload))))
        net.counter += 1
        heapq.heappush(endpoint.inbox, (0.0, net.counter, "2|1|snap|x=1"))
        probe.update(0.016)
        net.advance(5.0)
        probe.update(0.016)
        self.assertIn((2, 1, "snap|x=1"), received)
        self.assertIn((0, 0, "welcome 1"), received)

    def test_connect_room_and_relay_config(self):
        net = harness.SimNet(loss_pct=0.0)
        endpoint = net.endpoint()
        probe = harness.ProbeInstance(endpoint)
        self.assertTrue(probe.init(role="joiner", auditDir=self.dir, relayHost="10.0.0.5", relayPort=12000,
                                   room="night"))
        self.assertEqual(endpoint.connect_calls, [("10.0.0.5", 12000, "night")])
        second = harness.ProbeInstance(net.endpoint())
        self.assertTrue(second.init(role="other", auditDir=self.dir, connect=False))
        self.assertEqual(net.endpoints[1].connect_calls, [])
        session = harness.read_lines(self.dir + "probe_audit_joiner.log", "SESSION")[0]
        self.assertIn("relay=10.0.0.5:12000 room=night", session)
        self.assertIn("plugin=CP2077CoopNet_0.1.1_proto_1", session)

    def test_default_role_and_no_connect_answer_logged_once(self):
        net = harness.SimNet(loss_pct=0.0)

        class SilentEndpoint(harness.SimEndpoint):
            def connect(self, host, port, room):
                self.connect_calls.append((host, port, room))
                return True  # the relay never answers

        endpoint = SilentEndpoint(net)
        probe = harness.ProbeInstance(endpoint)
        self.assertTrue(probe.init(auditDir=self.dir))
        for _ in range(200):
            net.advance(FRAME_MS)
            probe.update(FRAME_MS / 1000.0)
        self.assertTrue(os.path.exists(self.dir + "probe_audit_local.log"))
        prints = probe.prints()
        self.assertEqual(len(prints), 1, prints)
        self.assertIn("no relay answer", prints[0])
        self.assertGreater(probe.state().uFail, 0)  # sends are refused while nobody is connected

    def test_update_never_throws(self):
        net = harness.SimNet(loss_pct=0.0)

        class BrokenEndpoint(harness.SimEndpoint):
            def poll(self):
                raise RuntimeError("native exploded")

        endpoint = BrokenEndpoint(net)
        net.endpoints.append(endpoint)
        probe = harness.ProbeInstance(endpoint)
        self.assertTrue(probe.init(role="host", auditDir=self.dir))
        for _ in range(300):
            probe.update(0.016)
        self.assertEqual(probe.state().errors, 300)
        self.assertEqual(len(probe.prints()), 5 + 3)  # first five, then every 100th

    def test_peer_restart_resets_counters(self):
        net = harness.SimNet(latency_ms=20.0, jitter_ms=0.0, loss_pct=0.0)
        host = harness.ProbeInstance(net.endpoint())
        joiner_endpoint = net.endpoint()
        joiner = harness.ProbeInstance(joiner_endpoint)
        self.assertTrue(host.init(role="host", auditDir=self.dir))
        self.assertTrue(joiner.init(role="joiner", auditDir=self.dir))

        def run(frames):
            for _ in range(frames):
                net.advance(FRAME_MS)
                for instance in (host, joiner):
                    instance.set_pose(1.0, 2.0, 3.0, 0.0, 1.0)
                    instance.update(FRAME_MS / 1000.0)

        run(120)
        joiner.shutdown()
        restarted = harness.ProbeInstance(joiner_endpoint)  # same peer id, new session id
        self.assertTrue(restarted.init(role="joiner2", auditDir=self.dir, connect=False))
        joiner = restarted
        run(120)
        host.shutdown()
        events = harness.read_lines(self.dir + "probe_audit_host.log", "EVENT")
        self.assertTrue(any("probe_peer_restart 2" in line for line in events), events)
        stats = harness.final_stats(self.dir + "probe_audit_host.log")
        self.assertEqual(int(stats["u_lost"]), 0)
        self.assertEqual(int(stats["u_ooo"]), 0)

    def test_peer_departure_and_relay_loss_reselect_primary(self):
        net = harness.SimNet(loss_pct=0)
        probe = harness.ProbeInstance(net.endpoint())
        probe.init(role="host", auditDir=self.dir, ownPoll=False)
        for peer in (1, 3):
            probe.probe.handleMessage(peer, 9, f"NP1|u|5|1|{net.now_ms}|1|2|3|45|run")
        self.assertEqual(probe.state().primary, 1)
        probe.probe.handleMessage(0, 0, "peer_leave 1 timeout")
        self.assertIsNone(probe.state().peers[1])
        self.assertEqual(probe.state().primary, 3)
        probe.probe.handleMessage(0, 0, "relay_lost timeout")
        self.assertIsNone(probe.state().primary)
        probe.probe.handleMessage(2, 9, f"NP1|u|6|1|{net.now_ms}|4|5|6|90|walk")
        self.assertEqual(probe.state().primary, 2)
        self.assertEqual(probe.state().peers[2].pose.x, 4)
        probe.shutdown()
        events = harness.read_lines(self.dir + "probe_audit_host.log", "EVENT")
        self.assertTrue(any("primary_peer 3" in line for line in events))
        self.assertEqual(int(harness.final_stats(self.dir + "probe_audit_host.log")["peer_resets"]), 2)

    def test_reinit_appends_complete_previous_session(self):
        net = harness.SimNet(loss_pct=0)
        probe = harness.ProbeInstance(net.endpoint())
        probe.init(role="host", auditDir=self.dir)
        probe.update(0.016)
        net.advance(2000)
        probe.init(role="host", auditDir=self.dir)
        probe.update(0.016)
        probe.shutdown()
        sessions = sync_audit.load_sessions(self.dir + "probe_audit_host.log")
        self.assertEqual(len(sessions), 2)
        self.assertTrue(all(s.final_stats["final"] == "1" for s in sessions))
        self.assertTrue(all(s.audits for s in sessions))

    def test_precise_clock_and_external_drain_cost(self):
        net = harness.SimNet(loss_pct=0)
        endpoint = CountingEndpoint(net)
        net.endpoints.append(endpoint)
        endpoint.now_ms = lambda: harness.EPOCH_MS + endpoint.cpu_seconds * 1000
        probe = harness.ProbeInstance(endpoint)
        probe.init(role="host", auditDir=self.dir)
        probe.update(0.016)
        self.assertAlmostEqual(probe.state().poll.sumMs, endpoint.cpu_seconds * 1000, delta=0.001)
        probe.probe.recordDrain(0.2, 5, False)
        self.assertEqual(probe.state().poll.slowFrames, 1)
        self.assertFalse(probe.probe.recordDrain(float("nan"), 1, False))
        self.assertEqual(probe.state().poll.frames, 2)
        probe.shutdown()
        stats = harness.final_stats(self.dir + "probe_audit_host.log")
        self.assertGreater(float(stats["poll_p99_ms"]), 0.19)

    def test_poll_tail_overflow_never_underreports_a_large_stall(self):
        probe = harness.ProbeInstance(harness.SimNet().endpoint())
        probe.init(role="host", auditDir=self.dir, ownPoll=False)
        probe.probe.recordDrain(50, 64, True)
        probe.shutdown()
        stats = harness.final_stats(self.dir + "probe_audit_host.log")
        self.assertEqual(float(stats["poll_p99_ms"]), 50)

    def test_render_delay_is_written_with_pose(self):
        net = harness.SimNet(loss_pct=0)
        probe = harness.ProbeInstance(net.endpoint())
        probe.init(role="host", auditDir=self.dir, drawnProvider=lambda: (1, 2, 3, 45),
                   renderDelayProvider=lambda: 175.25)
        probe.update(0.016)
        probe.shutdown()
        audits = harness.read_lines(self.dir + "probe_audit_host.log", "AUDIT")
        self.assertIn("rd=175.250", audits[0])
        self.assertIn("drawn=1.000,2.000,3.000,45.00", audits[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
