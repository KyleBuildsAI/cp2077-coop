"""Policy connected to the real CB77 codec, membership and reliable delivery path."""
import copy
import unittest

from test_relay import Client as BaseClient, Harness, M, player_body, proto
from coopnet import authority as a
from coopnet.reliability import RELIABLE_WINDOW


def script(channel, text):
    return proto.SCRIPT_MSG.encode({"channel": channel, "flags": 0, "text": text})


class Client(BaseClient):
    # The legacy test helper queues inside assert; keep this experiment's -O
    # regression meaningful by preserving the send side effect in optimized mode.
    def send(self, unreliable=(), reliable=()):
        for kind, destination, body in reliable:
            if not self.conn.queue_reliable(kind, destination, body, self.h.now):
                raise AssertionError("test client reliable queue full")
        for packet in self.conn.build_packets(self.h.now, list(unreliable), force=True):
            self.h.deliver(self.address, packet)


class AuthorityRelayTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness("--entity-authority-test", "--room-size", "3")
        self.host = Client(self.h, proto.Role.HOST, minor=1)
        self.joiner = Client(self.h, proto.Role.JOINER, minor=1)
        self.assertTrue(self.host.join())
        self.assertTrue(self.joiner.join())
        self.host.poll(), self.joiner.poll()
        self.send(self.host, "C3A1|BEGIN|" + "1" * 32)
        self.send(self.joiner, f"C3A1|JOIN|{self.policy.epoch}|" + "2" * 32)
        self.send(self.joiner, f"C3A1|READY|{self.policy.epoch}|{'2' * 32}|0")

    def tearDown(self):
        self.h.close()

    @property
    def policy(self):
        return self.h.relay.rooms["r1"].authority

    def send(self, client, text, channel=a.CONTROL):
        message = (M.SCRIPT_MSG, proto.PEER_RELAY, script(channel, text))
        client.send(reliable=[message] if channel == a.CONTROL else [],
                    unreliable=[message] if channel == a.POSE else [])
        self.host.poll(), self.joiner.poll()

    def command(self, client, verb, *args, event=None):
        member = self.policy.members[client.peer_id]
        event = member.event + 1 if event is None else event
        self.send(client, "|".join(map(str, ["C3A1", verb, self.policy.epoch, member.nonce, event, *args])))

    def spawn(self):
        self.command(self.host, "SPAWN", 1, 0, "test.vehicle", 10, 20, 3, 0)

    def test_real_codec_policy_routes_intent_and_commit_from_relay_identity(self):
        self.spawn()
        self.command(self.joiner, "REQUEST", 1, 1, "enter")
        request = self.policy.requests[self.joiner.peer_id]
        self.assertEqual(self.policy.vehicle.driver, 0)
        self.command(self.host, "GRANT", 1, 1, self.joiner.peer_id, request.event, request.generation)
        self.assertEqual(self.policy.vehicle.driver, self.joiner.peer_id)
        scripts = [m for m in self.joiner.received if m[0] == M.SCRIPT_MSG]
        self.assertTrue(scripts)
        self.assertTrue(all(m[1] == proto.PEER_RELAY and m[2] for m in scripts))
        self.assertTrue(any("|STATE|" in m[3]["text"] for m in scripts))

    def test_foreign_room_old_membership_and_host_loss(self):
        self.spawn()
        other = Client(self.h, proto.Role.HOST, room="other", minor=1)
        self.assertTrue(other.join())
        before = copy.deepcopy(self.policy.__dict__)
        self.send(other, f"C3A1|DESPAWN|{self.policy.epoch}|{'1' * 32}|2|1|1")
        self.assertEqual(self.policy.__dict__, before)
        old_token = self.joiner.conn.token
        peer = self.h.relay.peers_by_token[old_token]
        self.h.relay.remove_peer(peer, proto.DisconnectReason.QUIT, self.h.now)
        self.send(self.joiner, f"C3A1|REQUEST|{self.policy.epoch}|{'2' * 32}|1|1|1|enter")
        self.assertFalse(self.policy.requests)
        host_peer = self.h.relay.peers_by_token[self.host.conn.token]
        self.h.relay.remove_peer(host_peer, proto.DisconnectReason.QUIT, self.h.now)
        self.assertEqual(self.policy.epoch, "")
        self.assertIsNone(self.policy.vehicle)

    def test_full_reliable_window_cannot_commit_or_report_success(self):
        self.spawn()
        receiver = self.h.relay.peers_by_token[self.joiner.conn.token]
        filler = script(20, "unrelated test traffic")
        while receiver.conn.queue_reliable(M.SCRIPT_MSG, 0, filler, self.h.now):
            pass
        self.assertGreaterEqual(len(receiver.conn.rel_pending), 1)
        self.assertLessEqual(len(receiver.conn.rel_pending), RELIABLE_WINDOW)
        self.host.received.clear()
        self.command(self.host, "DESPAWN", 1, 1)
        # Transaction aborted. Disconnecting the slow joiner is allowed cleanup,
        # but the attempted host event must remain uncommitted and retryable.
        self.assertIsNotNone(self.policy.vehicle)
        self.assertEqual(self.policy.members[self.host.peer_id].event, 1)
        self.assertNotIn(receiver.token, self.h.relay.peers_by_token)
        self.assertFalse(any("|ok|despawn|" in m[3]["text"] for m in self.host.received if m[0] == M.SCRIPT_MSG))
        self.assertEqual(self.h.relay.counters["authority_overflow"], 1)

    def test_existing_player_stream_and_npc_channels_are_unchanged(self):
        self.host.received.clear()
        self.joiner.send([(M.PLAYER_SNAPSHOT, proto.PEER_BROADCAST, player_body())],
                         [(M.SCRIPT_MSG, self.host.peer_id, script(20, "NT1|123|D|1"))])
        messages = self.host.poll()
        self.assertTrue(any(m[0] == M.PLAYER_SNAPSHOT for m in messages))
        self.assertTrue(any(m[0] == M.SCRIPT_MSG and m[1] == self.joiner.peer_id
                            and m[3]["text"] == "NT1|123|D|1" for m in messages))

    def test_unreliable_experiment_has_one_latest_slot_without_removing_other_traffic(self):
        receiver = self.h.relay.peers_by_token[self.joiner.conn.token]
        unrelated = (M.SCRIPT_MSG, self.host.peer_id, script(3, "some other sender"))
        npc = (M.SCRIPT_MSG, self.host.peer_id, script(2, "NT1|123|S|1"))
        receiver.pending.extend((unrelated, npc))
        identity = self.h.relay.authority_identity(receiver)
        for sequence in range(100):
            self.assertTrue(self.h.relay.authority_effects(receiver.room,
                [a.Effect(identity, a.POSE, f"C3A1|POSE|test|{sequence}")], self.h.now))
        self.assertIn(unrelated, receiver.pending)
        self.assertIn(npc, receiver.pending)
        experimental = [entry for entry in receiver.pending if entry[0] == M.SCRIPT_MSG and entry[1] == 0]
        self.assertEqual(len(experimental), 1)
        self.assertEqual(proto.SCRIPT_MSG.decode(experimental[0][2])["text"], "C3A1|POSE|test|99")

    def test_default_off_preserves_script_passthrough_even_for_reserved_prefix(self):
        normal = Harness()
        try:
            host, joiner = Client(normal, proto.Role.HOST, minor=1), Client(normal, proto.Role.JOINER, minor=1)
            self.assertTrue(host.join())
            self.assertTrue(joiner.join())
            host.poll(), joiner.poll()
            text = "C3A1|BEGIN|" + "1" * 32
            host.send(reliable=[(M.SCRIPT_MSG, proto.PEER_BROADCAST, script(21, text))])
            messages = [m for m in joiner.poll() if m[0] == M.SCRIPT_MSG]
            self.assertEqual([(m[1], m[3]["text"]) for m in messages], [(host.peer_id, text)])
            self.assertIsNone(normal.relay.rooms["r1"].authority)
        finally:
            normal.close()


if __name__ == "__main__":
    unittest.main()
