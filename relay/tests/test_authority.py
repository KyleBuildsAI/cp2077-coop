import copy
import itertools
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from coopnet import authority as a


class PolicyTests(unittest.TestCase):
    def setUp(self):
        epochs = itertools.count(100)
        self.p = a.Authority(lambda: f"{next(epochs):032x}")
        self.h = a.Identity(1, 101, True)
        self.j = a.Identity(2, 202, False)
        self.now = 10.0
        self.run_command(self.h, "C3A1|BEGIN|" + "1" * 32)
        self.join(self.j, "2" * 32)

    def run_command(self, identity, text, channel=a.CONTROL, verdict="accepted"):
        before = copy.deepcopy(self.p.__dict__)
        plan = self.p.plan(identity, channel, text, self.now, int(self.now * 1000))
        self.assertEqual(plan.verdict, verdict, (text, plan.effects))
        if verdict == "rejected":
            self.assertEqual(self.p.__dict__, before)
            self.assertIs(plan.state, self.p)
        self.p = plan.state
        return plan

    def join(self, identity, nonce):
        self.run_command(identity, f"C3A1|JOIN|{self.p.epoch}|{nonce}")
        self.command(identity, "READY", [self.p.revision], special=True)

    def command(self, who, verb, args, event=None, special=False, verdict="accepted"):
        member = self.p.members[who.peer]
        fields = ["C3A1", verb, self.p.epoch, member.nonce]
        if not special:
            fields.append(member.event + 1 if event is None else event)
        fields.extend(args)
        return self.run_command(who, "|".join(map(str, fields)), verdict=verdict)

    def spawn(self, entity=1):
        return self.command(self.h, "SPAWN", [entity, self.p.revision, "test.vehicle", 10, 20, 3, 0])

    def request(self, who=None, action="enter"):
        who = who or self.j
        return self.command(who, "REQUEST", [self.p.vehicle.entity, self.p.revision, action])

    def grant(self, who=None, verb="GRANT", verdict="accepted"):
        who = who or self.j
        request = self.p.requests[who.peer]
        return self.command(self.h, verb, [self.p.vehicle.entity, self.p.revision,
                                           who.peer, request.event, request.generation], verdict=verdict)

    def test_host_only_commit_and_correlated_enter_exit(self):
        self.spawn()
        self.command(self.j, "SPAWN", [2, 1, "test.vehicle", 0, 0, 0, 0], verdict="rejected")
        intent = self.request()
        self.assertEqual(self.p.vehicle.driver, 0)
        self.assertEqual(intent.effects[0].target, self.h)
        self.assertIn("|INTENT|", intent.effects[0].text)
        self.grant()
        self.assertEqual((self.p.vehicle.driver, self.p.vehicle.seat_generation), (2, 1))
        self.command(self.h, "DESPAWN", [1, self.p.revision], verdict="rejected")
        self.request(action="exit")
        self.grant(verb="RELEASE")
        self.assertEqual((self.p.vehicle.driver, self.p.vehicle.seat_generation), (0, 2))

    def test_discovery_offers_epoch_but_does_not_admit(self):
        third = a.Identity(3, 303, False)
        before = set(self.p.members)
        offer = self.run_command(third, "C3A1|DISCOVER|" + "3" * 32, verdict="offer")
        self.assertEqual(set(self.p.members), before)
        self.assertEqual(offer.effects[0].target, third)
        self.assertIn(f"C3A1|OFFER|{self.p.epoch}|{'3' * 32}|", offer.effects[0].text)

    def test_racing_requests_cannot_double_grant(self):
        third = a.Identity(3, 303, False)
        self.join(third, "3" * 32)
        self.spawn()
        self.request(self.j)
        self.request(third)
        old = self.p.requests[third.peer]
        self.grant()
        self.command(self.h, "GRANT", [1, self.p.revision, third.peer, old.event, old.generation], verdict="rejected")
        self.assertEqual(self.p.vehicle.driver, self.j.peer)

    def test_baseline_revision_race_requires_refresh_before_ready(self):
        third = a.Identity(3, 303, False)
        self.run_command(third, f"C3A1|JOIN|{self.p.epoch}|" + "3" * 32)
        self.spawn()
        self.command(third, "READY", [0], special=True, verdict="refresh")
        self.assertFalse(self.p.members[3].ready)
        self.command(third, "REQUEST", [1, 1, "enter"], verdict="rejected")
        self.command(third, "READY", [1], special=True)
        self.request(third)

    def test_ordering_duplicates_and_rejected_command_retry(self):
        self.command(self.h, "SPAWN", [1, 0, "car", 0, 0, 0, 0], event=2, verdict="rejected")
        self.spawn()
        self.command(self.h, "SPAWN", [1, 0, "car", 0, 0, 0, 0], event=1, verdict="duplicate")
        self.assertEqual((self.p.last_entity, self.p.revision, self.p.members[1].event), (1, 1, 1))
        self.command(self.h, "DESPAWN", [1, 0], verdict="rejected")
        self.command(self.h, "DESPAWN", [1, 1])
        self.command(self.h, "SPAWN", [1, 2, "car", 0, 0, 0, 0], verdict="rejected")
        self.spawn(2)

    def test_membership_generation_prevents_approval_reuse_after_rejoin(self):
        self.spawn()
        self.request()
        old = self.p.requests[2]
        self.p.remove(self.j)
        replacement = a.Identity(2, 909, False)  # deliberately reused peer ID and app nonce
        self.join(replacement, "2" * 32)
        self.request(replacement)
        self.command(self.h, "GRANT", [1, self.p.revision, 2, old.event, old.generation], verdict="rejected")
        self.grant(replacement)
        self.assertEqual(self.p.vehicle.driver, 2)

    def test_request_expiry_blocks_approval_without_mutation(self):
        self.spawn()
        self.request()
        self.now += a.REQUEST_TIMEOUT
        self.grant(verdict="rejected")
        self.p.expire(self.now)
        self.assertFalse(self.p.requests)
        self.request()
        self.grant()

    def test_host_nonce_rotation_and_old_epoch_replay(self):
        self.spawn()
        old_epoch = self.p.epoch
        original = copy.deepcopy(self.p.vehicle)
        self.run_command(self.h, "C3A1|BEGIN|" + "1" * 32, verdict="duplicate")
        self.assertEqual(self.p.vehicle, original)
        self.run_command(self.h, "C3A1|BEGIN|" + "4" * 32)
        self.assertNotEqual(self.p.epoch, old_epoch)
        self.assertIsNone(self.p.vehicle)
        self.run_command(self.h, "C3A1|BEGIN|" + "1" * 32, verdict="rejected")
        self.run_command(self.j, f"C3A1|JOIN|{old_epoch}|" + "5" * 32, verdict="rejected")

    def test_driver_disconnect_revokes_lease_and_host_loss_closes_epoch(self):
        self.spawn()
        self.request()
        self.grant()
        generation = self.p.vehicle.seat_generation
        effects = self.p.remove(self.j)
        self.assertEqual(self.p.vehicle.driver, 0)
        self.assertGreater(self.p.vehicle.seat_generation, generation)
        self.assertTrue(effects)
        self.p.remove(self.h)
        self.assertEqual(self.p.epoch, "")
        self.assertIsNone(self.p.vehicle)

    def test_bounds_invalid_input_identity_and_clock_are_nonmutating(self):
        for text in ("C3A1|BEGIN|x", "C3A1|" + "x" * 513, "C3A1|BEGIN|" + "1" * 31 + "\n"):
            self.run_command(self.h, text, verdict="rejected")
        self.command(self.h, "SPAWN", [1, 0, "car", "nan", 0, 0, 0], verdict="rejected")
        self.command(self.h, "SPAWN", [1, 0, "car", 20001, 0, 0, 0], verdict="rejected")
        self.command(self.h, "SPAWN", ["01", 0, "car", 0, 0, 0, 0], verdict="rejected")
        impostor = a.Identity(1, 999, True)
        self.run_command(impostor, "C3A1|BEGIN|" + "7" * 32, verdict="rejected")
        self.now -= 1
        self.run_command(self.h, "C3A1|BEGIN|" + "7" * 32, verdict="rejected")

    def test_pose_freshness_wrap_authority_and_no_control_revision_change(self):
        self.spawn()
        def send(who, sequence, stamp, x, verdict="accepted"):
            nonce = self.p.members[who.peer].nonce
            return self.run_command(who, f"C3A1|POSE|{self.p.epoch}|{nonce}|1|{sequence}|{stamp}|{x}|20|3|0",
                                    a.POSE, verdict)
        send(self.h, a.MAX_ID, 10000, 11)
        self.now += .1
        send(self.h, 0, 10100, 12)
        self.now += .1
        send(self.h, 0, 10200, 13, "rejected")
        send(self.h, 1, 7000, 13, "rejected")
        send(self.h, 1, 11000, 13, "rejected")
        send(self.j, 1, 10200, 13, "rejected")
        send(self.h, 1, 10200, "inf", "rejected")
        self.assertEqual((self.p.vehicle.transform[0], self.p.revision), (12, 1))

    def test_pose_age_wrap_and_rate_bound(self):
        self.spawn()
        member = self.p.members[1]
        def make(seq, stamp):
            return f"C3A1|POSE|{self.p.epoch}|{member.nonce}|1|{seq}|{stamp}|1|2|3|4"
        plan = self.p.plan(self.h, a.POSE, make(1, a.MAX_ID - 49), self.now, 50)
        self.assertEqual(plan.verdict, "accepted")  # sample 100 ms before u32 wrap
        self.p = plan.state
        plan = self.p.plan(self.h, a.POSE, make(2, 50), self.now + .01, 60)
        self.assertEqual(plan.verdict, "rejected")

    def test_app_reload_old_nonce_and_capacity_bounds(self):
        self.spawn()
        self.request()
        self.grant()
        self.run_command(self.j, f"C3A1|JOIN|{self.p.epoch}|" + "6" * 32)
        self.assertEqual(self.p.vehicle.driver, 0)
        self.assertFalse(self.p.members[2].ready)
        self.run_command(self.j, f"C3A1|JOIN|{self.p.epoch}|" + "2" * 32, verdict="rejected")
        for peer in range(3, 9):
            self.join(a.Identity(peer, peer * 101, False), f"{peer:032x}")
        self.run_command(a.Identity(9, 909, False), f"C3A1|JOIN|{self.p.epoch}|" + "9" * 32, verdict="rejected")
        self.assertEqual(len(self.p.members), 8)

    def test_epoch_rotation_history_is_bounded_without_replay_eviction(self):
        for index in range(a.MAX_ROTATIONS - 1):
            self.run_command(self.h, f"C3A1|BEGIN|{1000 + index:032x}")
        epoch = self.p.epoch
        self.run_command(self.h, "C3A1|BEGIN|" + "f" * 32, verdict="rejected")
        self.assertEqual(len(self.p.host_nonces), a.MAX_ROTATIONS)
        self.assertEqual(self.p.epoch, epoch)


if __name__ == "__main__":
    unittest.main()
