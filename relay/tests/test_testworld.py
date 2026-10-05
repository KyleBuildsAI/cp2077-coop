import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from coopnet import proto, testworld  # noqa: E402

ROLES = (proto.Role.HOST, proto.Role.JOINER)


class CourseTests(unittest.TestCase):
    def test_loop_closes_with_the_start_heading(self):
        for role in ROLES:
            segments, period = testworld._COURSES[int(role)]
            x, y, _, _, heading = testworld.course_state(segments[-1], segments[-1].duration)
            self.assertAlmostEqual(x, segments[0].start[0], places=9)
            self.assertAlmostEqual(y, segments[0].start[1], places=9)
            self.assertAlmostEqual(math.cos(heading), math.cos(segments[0].heading), places=12)
            self.assertAlmostEqual(math.sin(heading), math.sin(segments[0].heading), places=12)
            self.assertAlmostEqual(period, 2 * sum(self.half_durations()), places=9)

    @staticmethod
    def half_durations():
        durations = []
        for _, kind, *params in testworld.COURSE_HALF:
            if kind == "line":
                durations.append(2.0 * params[0] / (params[1] + params[2]))
            elif kind == "arc":
                durations.append(math.radians(abs(params[0])) * params[1] / params[2])
            else:
                durations.append(params[0])
        return durations

    def test_position_and_velocity_are_continuous(self):
        for role in ROLES:
            segments, period = testworld._COURSES[int(role)]
            for before, after in zip(segments, segments[1:] + segments[:1]):
                end = testworld.course_state(before, before.duration)
                start = testworld.course_state(after, 0.0)
                for a, b in zip(end[:4], start[:4]):
                    self.assertAlmostEqual(a, b, places=9, msg=f"{before.motion}/{before.kind} -> {after.kind}")

    def test_truth_matches_the_finite_difference_velocity(self):
        step = 1e-4
        for role in ROLES:
            for index in range(800):
                t = index * 0.0617
                truth = testworld.course_truth(role, t)
                ahead = testworld.course_truth(role, t + step)["pos"]
                behind = testworld.course_truth(role, t - step)["pos"]
                for axis in range(2):
                    numeric = (ahead[axis] - behind[axis]) / (2 * step)
                    self.assertAlmostEqual(truth["vel"][axis], numeric, delta=2e-3)

    def test_every_motion_and_speed_class(self):
        for role in ROLES:
            seen = {}
            for index in range(4000):
                truth = testworld.course_truth(role, index * 0.01)
                speed = math.hypot(*truth["vel"][:2])
                seen.setdefault(truth["motion"], []).append(speed)
            self.assertEqual(set(seen), set(testworld.COURSE_MOTIONS))
            self.assertAlmostEqual(max(seen["walk"]), testworld.WALK_MPS, places=9)
            self.assertAlmostEqual(max(seen["run"]), testworld.RUN_MPS, places=9)
            self.assertAlmostEqual(max(seen["sprint"]), testworld.SPRINT_MPS, places=9)
            self.assertEqual(min(seen["stop"]), 0.0)

    def test_course_stays_near_the_entities_and_on_foot(self):
        for role in ROLES:
            for index in range(800):
                truth = testworld.player_truth(role, index * 0.05, "course")
                self.assertLess(math.dist(truth["pos"][:2], testworld.ORIGIN[:2]), 60.0)
                self.assertFalse(truth["driving"])
                self.assertEqual(truth["pos"][2], testworld.ORIGIN[2])

    def test_yaw_follows_the_heading_and_holds_while_standing(self):
        for role in ROLES:
            for index in range(800):
                truth = testworld.course_truth(role, index * 0.05)
                vx, vy = truth["vel"][:2]
                if math.hypot(vx, vy) > 0.1:
                    gap = ((testworld.yaw_from_forward(vx, vy) - truth["yaw"] + 180.0) % 360.0) - 180.0
                    self.assertAlmostEqual(gap, 0.0, places=6)

    def test_roles_are_in_different_parts_of_the_course(self):
        differing = sum(testworld.course_truth(proto.Role.HOST, t / 10)["motion"]
                        != testworld.course_truth(proto.Role.JOINER, t / 10)["motion"] for t in range(400))
        self.assertGreater(differing, 200)


class DemoPathTests(unittest.TestCase):
    def test_demo_path_is_the_default_and_labelled(self):
        host = testworld.player_truth(proto.Role.HOST, 3.0)
        self.assertEqual(host, testworld.player_truth(proto.Role.HOST, 3.0, "demo"))
        self.assertEqual(host["motion"], "run")
        self.assertAlmostEqual(math.hypot(*host["vel"][:2]), testworld.HOST_SPEED_MPS, places=9)
        joiner = testworld.player_truth(proto.Role.JOINER, 3.0)
        self.assertTrue(joiner["driving"])
        self.assertEqual(joiner["motion"], "drive")

    def test_unknown_path_is_rejected(self):
        with self.assertRaises(ValueError):
            testworld.player_truth(proto.Role.HOST, 1.0, "teleport")


if __name__ == "__main__":
    unittest.main()
