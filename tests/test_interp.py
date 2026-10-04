import math
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from coopnet.interp import ClockSync, InterpBuffer, LatestSlotFollower, distance  # noqa: E402


class ClockSyncTests(unittest.TestCase):
    def exchange(self, sync, true_offset, local_send, up_ms, down_ms, relay_hold_ms=0.2):
        t0 = local_send
        t1 = t0 + true_offset + up_ms
        t2 = t1 + relay_hold_ms
        t3 = t2 - true_offset + down_ms
        return sync.on_response(t0, t1, t2, t3)

    def test_symmetric_jittered_link(self):
        rng = random.Random(1)
        sync = ClockSync()
        for index in range(40):
            self.exchange(sync, 5000.0, index * 100.0, 80 + rng.random() * 30, 80 + rng.random() * 30)
        self.assertAlmostEqual(sync.offset_ms, 5000.0, delta=8.0)
        self.assertTrue(sync.synced)

    def test_asymmetry_bias_is_half_the_difference(self):
        sync = ClockSync()
        for index in range(20):
            self.exchange(sync, -300.0, index * 100.0, 20.0, 80.0)
        self.assertAlmostEqual(sync.offset_ms, -300.0 - 30.0, delta=0.5)

    def test_slew_after_warmup_and_step_on_jump(self):
        sync = ClockSync()
        for index in range(ClockSync.WINDOW):
            self.exchange(sync, 0.0, index * 100.0, 10.0, 10.0)
        base = sync.offset_ms
        for index in range(ClockSync.WINDOW):
            self.exchange(sync, 10.0, 2000 + index * 100.0, 5.0, 5.0)
        self.assertLessEqual(abs(sync.offset_ms - base - 10.0), 0.5)
        stepped = False
        for index in range(ClockSync.WINDOW):
            stepped |= self.exchange(sync, 500.0, 5000 + index * 100.0, 1.0, 1.0)
        self.assertTrue(stepped)
        self.assertAlmostEqual(sync.offset_ms, 500.0, delta=1.0)


class InterpTests(unittest.TestCase):
    def feed(self, buffer, rng, seconds, interval_ms, transit_ms, jitter_ms, loss, speed=6.0):
        arrivals = []
        t = 0.0
        while t < seconds * 1000:
            if rng.random() >= loss:
                arrivals.append((t + transit_ms + rng.random() * jitter_ms, t))
            t += interval_ms
        arrivals.sort()
        return arrivals

    def test_interpolation_error_small_with_jitter_and_loss(self):
        rng = random.Random(2)
        buffer = InterpBuffer(**InterpBuffer.PLAYER)
        arrivals = self.feed(buffer, rng, 10, 1000 / 30, 120, 40, 0.1)
        radius, omega = 10.0, 0.6

        def truth(t_ms):
            angle = omega * t_ms / 1000
            return (radius * math.cos(angle), radius * math.sin(angle), 0.0)

        def velocity(t_ms):
            angle = omega * t_ms / 1000
            return (-radius * omega * math.sin(angle), radius * omega * math.cos(angle), 0.0)

        errors = []
        index = 0
        now = 0.0
        while now < 10000:
            while index < len(arrivals) and arrivals[index][0] <= now:
                sample_t = arrivals[index][1]
                buffer.push(sample_t, arrivals[index][0], truth(sample_t), velocity(sample_t))
                index += 1
            if buffer.samples and now > 1000:
                render_t = buffer.render_time(now, 16.7)
                pos, _, _ = buffer.sample_at(render_t)
                errors.append(distance(pos, truth(render_t)))
            now += 16.7
        errors.sort()
        self.assertLess(errors[int(0.95 * len(errors))], 0.01)
        delay = buffer.target_delay_ms()
        self.assertGreaterEqual(delay, 100.0)
        self.assertLessEqual(delay, 150.0)

    def test_extrapolation_is_bounded(self):
        buffer = InterpBuffer(**InterpBuffer.PLAYER)
        buffer.push(0.0, 50.0, (0.0, 0.0, 0.0), (10.0, 0.0, 0.0))
        buffer.push(100.0, 150.0, (1.0, 0.0, 0.0), (10.0, 0.0, 0.0))
        pos, _, mode = buffer.sample_at(200.0)
        self.assertEqual(mode, "extrapolated")
        self.assertAlmostEqual(pos[0], 2.0)
        pos, _, mode = buffer.sample_at(1000.0)
        self.assertEqual(mode, "held")
        self.assertAlmostEqual(pos[0], 1.0 + 10.0 * 0.25)

    def test_teleport_clears_history_and_late_samples_dropped(self):
        buffer = InterpBuffer(**InterpBuffer.PLAYER)
        for t in range(0, 300, 33):
            buffer.push(float(t), t + 50.0, (t / 100.0, 0.0, 0.0))
        buffer.push(300.0, 350.0, (500.0, 500.0, 0.0), teleported=True)
        self.assertEqual(len(buffer.samples), 1)
        buffer.push(10.0, 360.0, (0.0, 0.0, 0.0))
        self.assertEqual(buffer.counts["late"], 1)

    def test_playout_snaps_on_large_discontinuity(self):
        buffer = InterpBuffer(**InterpBuffer.PLAYER)
        for t in range(0, 1000, 33):
            buffer.push(float(t), t + 60.0, (0.0, 0.0, 0.0))
        first = buffer.render_time(1100.0, 16.7)
        buffer.reset_timing()
        for t in range(1000, 2000, 33):
            buffer.push(float(t), t + 900.0, (0.0, 0.0, 0.0))
        second = buffer.render_time(2900.0, 16.7)
        self.assertAlmostEqual(1100.0 - first, 160.0, delta=1.0)
        self.assertAlmostEqual(2900.0 - second, 1000.0, delta=1.0)

    def test_latest_slot_follower(self):
        follower = LatestSlotFollower(follow_rate=10.0)
        follower.on_sample(5, (10.0, 0.0, 0.0))
        follower.on_sample(4, (99.0, 0.0, 0.0))
        self.assertEqual(follower.frame(0.016), (10.0, 0.0, 0.0))
        follower.on_sample(6, (20.0, 0.0, 0.0))
        self.assertAlmostEqual(follower.frame(0.05)[0], 15.0)


if __name__ == "__main__":
    unittest.main()
