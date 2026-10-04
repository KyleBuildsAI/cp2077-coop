import os
import random
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import relay_v2  # noqa: E402
from coopnet import legacy, proto  # noqa: E402
from coopnet.reliability import Connection  # noqa: E402

M = proto.MsgType


class Harness:
    """A real Relay object driven in-process with fake addresses and a fake clock."""

    def __init__(self, *extra):
        self.relay = relay_v2.Relay(relay_v2.parse_args(["--port", "0", "--quiet", *extra]))
        self.outbox = []
        self.relay._sendto = lambda data, address: self.outbox.append((address, data))
        self.now = self.relay.start + 1.0

    def deliver(self, address, data):
        self.relay.handle_datagram(data, address, self.now)

    def advance(self, seconds, step=0.05):
        end = self.now + seconds
        while self.now < end:
            self.now += step
            self.relay.tick(self.now)

    def take(self, address):
        mine = [data for addr, data in self.outbox if addr == address]
        self.outbox = [(addr, data) for addr, data in self.outbox if addr != address]
        return mine

    def close(self):
        self.relay.sock.close()


class Client:
    counter = 0

    def __init__(self, harness, role=proto.Role.ANY, room="r1", password="pw", join_flags=0, mod_hash=1, name="c"):
        Client.counter += 1
        self.h = harness
        self.address = (f"198.51.100.{Client.counter % 250 + 1}", 40000 + Client.counter)
        self.info = {"minor": 0, "role": int(role), "join_flags": join_flags, "caps": proto.ALL_CAPS,
                     "game_build": 7, "mod_major": 0, "mod_minor": 2, "mod_patch": 0, "mod_hash": mod_hash,
                     "mod_count": 3, "client_nonce": 1000 + Client.counter, "resume_token": 0, "room": room,
                     "name": name}
        self.password = password
        self.conn = None
        self.peer_id = None
        self.role = None
        self.reject = None
        self.received = []

    def join(self):
        self.h.deliver(self.address, proto.encode_hello(self.info))
        replies = self.h.take(self.address)
        assert len(replies) == 1, replies
        ptype, _, _, _, _, body = proto.decode_packet(replies[0])
        if ptype == proto.PacketType.REJECT:
            self.reject = proto.decode_reject(body)
            return False
        cookie = proto.decode_challenge(body)[2]
        self.auth = proto.encode_auth(self.info, cookie, proto.room_key_hash(self.info["room"], self.password))
        self.h.deliver(self.address, self.auth)
        return self.handle_join_reply()

    def handle_join_reply(self):
        datagrams = self.h.take(self.address)
        for index, data in enumerate(datagrams):
            ptype, token, seq, ack, bits, body = proto.decode_packet(data)
            if ptype == proto.PacketType.WELCOME:
                welcome = proto.decode_welcome(body)
                self.peer_id, self.role = welcome["peer_id"], welcome["role"]
                self.conn = Connection(welcome["token"])
                self.welcome = welcome
                # DATA that arrived together with the WELCOME belongs to the session
                self.h.outbox += [(self.address, later) for later in datagrams[index + 1:]]
                return True
            if ptype == proto.PacketType.REJECT:
                self.reject = proto.decode_reject(body)
                return False
        return False

    def send(self, unreliable=(), reliable=()):
        for mtype, dest, body in reliable:
            assert self.conn.queue_reliable(mtype, dest, body, self.h.now)
        for packet in self.conn.build_packets(self.h.now, list(unreliable), force=True):
            self.h.deliver(self.address, packet)

    def poll(self):
        messages = []
        for data in self.h.take(self.address):
            ptype, token, seq, ack, bits, body = proto.decode_packet(data)
            if ptype == proto.PacketType.DISCONNECT:
                messages.append(("DISCONNECT", body[0], None, None))
                continue
            if ptype != proto.PacketType.DATA:
                continue
            for mtype, src, reliable, payload in self.conn.on_packet(self.h.now, seq, ack, bits, body):
                messages.append((mtype, src, reliable, proto.decode_body(mtype, payload)))
        self.received += messages
        return messages


def player_body(x=-1450.0, seq=1):
    return proto.PlayerSnapshotCodec().encode({
        "snap_seq": seq, "sample_time": 0, "x": x, "y": 180.0, "z": 22.0, "yaw": 0, "pitch": 0, "vx": 0, "vy": 0,
        "vz": 0, "move_state": 0, "health": 255, "flags": int(proto.PlayerFlag.WEAPON_DRAWN), "vehicle": None})


class RelayTestCase(unittest.TestCase):
    def setUp(self):
        self.h = Harness()

    def tearDown(self):
        self.h.close()

    def pair(self, **kwargs):
        host = Client(self.h, proto.Role.HOST, **kwargs)
        joiner = Client(self.h, proto.Role.JOINER, **kwargs)
        self.assertTrue(host.join())
        self.assertTrue(joiner.join())
        host.poll()
        joiner.poll()
        return host, joiner


class HandshakeTests(RelayTestCase):
    def test_join_flow_and_peer_announcements(self):
        host, joiner = self.pair()
        self.assertEqual((host.peer_id, host.role), (1, proto.Role.HOST))
        self.assertEqual((joiner.peer_id, joiner.role), (2, proto.Role.JOINER))
        self.assertIn((M.PEER_JOINED, 0), [(m[0], m[1]) for m in host.received])
        joined = [m[3] for m in joiner.received if m[0] == M.PEER_JOINED]
        self.assertEqual(joined[0]["peer_id"], 1)
        self.assertEqual(joined[0]["role"], proto.Role.HOST)

    def test_unpadded_hello_gets_no_reply(self):
        client = Client(self.h)
        self.h.deliver(client.address, proto.encode_packet(proto.PacketType.HELLO, body=proto.encode_join(client.info)))
        self.assertEqual(self.h.take(client.address), [])

    def test_reject_reasons(self):
        host = Client(self.h, proto.Role.HOST, join_flags=int(proto.JoinFlag.STRICT_MODS), mod_hash=5)
        self.assertTrue(host.join())
        cases = [
            (Client(self.h, proto.Role.JOINER, password="nope", mod_hash=5), proto.RejectReason.BAD_KEY),
            (Client(self.h, proto.Role.HOST, mod_hash=5), proto.RejectReason.ROLE_TAKEN),
            (Client(self.h, proto.Role.JOINER, mod_hash=6), proto.RejectReason.MOD_MISMATCH),
        ]
        for client, reason in cases:
            self.assertFalse(client.join())
            self.assertEqual(client.reject[0], reason)
        self.assertTrue(Client(self.h, proto.Role.JOINER, mod_hash=5).join())
        full = Client(self.h, proto.Role.JOINER, mod_hash=5)
        self.assertFalse(full.join())
        self.assertEqual(full.reject[0], proto.RejectReason.ROOM_FULL)

    def test_cookie_forged_or_expired(self):
        client = Client(self.h)
        forged = proto.encode_auth(client.info, bytes(16), proto.room_key_hash("r1", "pw"))
        self.h.deliver(client.address, forged)
        self.assertFalse(client.handle_join_reply())
        self.assertEqual(client.reject[0], proto.RejectReason.BAD_COOKIE)
        late = Client(self.h)
        self.h.deliver(late.address, proto.encode_hello(late.info))
        cookie = proto.decode_challenge(proto.decode_packet(self.h.take(late.address)[0])[5])[2]
        self.h.now += 15.0
        self.h.deliver(late.address, proto.encode_auth(late.info, cookie, proto.room_key_hash("r1", "pw")))
        self.assertFalse(late.handle_join_reply())
        self.assertEqual(late.reject[0], proto.RejectReason.BAD_COOKIE)

    def test_cookie_bound_to_address(self):
        client = Client(self.h)
        self.h.deliver(client.address, proto.encode_hello(client.info))
        cookie = proto.decode_challenge(proto.decode_packet(self.h.take(client.address)[0])[5])[2]
        thief = ("203.0.113.9", 5555)
        self.h.deliver(thief, proto.encode_auth(client.info, cookie, proto.room_key_hash("r1", "pw")))
        reply = proto.decode_packet(self.h.take(thief)[0])
        self.assertEqual(reply[0], proto.PacketType.REJECT)

    def test_future_major_version_rejected_with_range(self):
        client = Client(self.h)
        hello = bytearray(proto.encode_hello(client.info))
        hello[2] = 3
        self.h.deliver(client.address, bytes(hello))
        ptype, _, _, _, _, body = proto.decode_packet(self.h.take(client.address)[0])
        self.assertEqual(ptype, proto.PacketType.REJECT)
        reason, minor_min, minor_max, text = proto.decode_reject(body)
        self.assertEqual(reason, proto.RejectReason.VERSION)
        self.assertEqual((minor_min, minor_max), (proto.MIN_SUPPORTED_MINOR, proto.PROTO_MINOR))

    def test_duplicate_auth_is_idempotent(self):
        client = Client(self.h, proto.Role.HOST)
        self.assertTrue(client.join())
        token = client.conn.token
        self.h.deliver(client.address, client.auth)
        self.assertTrue(client.handle_join_reply())
        self.assertEqual(client.conn.token, token)
        self.assertEqual(len(self.h.relay.peers_by_token), 1)

    def test_handshake_flood_limited_per_ip(self):
        client = Client(self.h)
        for _ in range(50):
            self.h.deliver(client.address, proto.encode_hello(client.info))
        self.assertLessEqual(len(self.h.take(client.address)), 9)
        self.assertGreater(self.h.relay.counters["handshake_limited"], 0)


class RoutingTests(RelayTestCase):
    def test_snapshot_forwarded_with_source(self):
        host, joiner = self.pair()
        joiner.send([(M.PLAYER_SNAPSHOT, proto.PEER_BROADCAST, player_body())])
        got = [m for m in host.poll() if m[0] == M.PLAYER_SNAPSHOT]
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][1], joiner.peer_id)
        self.assertFalse(got[0][2])

    def test_host_only_and_relay_only_types_enforced(self):
        host, joiner = self.pair()
        entity = proto.EntitySnapshotCodec().encode({"tick": 1, "baseline": 0, "sample_time": 0, "records": []})
        weather = proto.TIME_WEATHER.encode({"game_seconds": 1, "time_scale_x100": 100, "flags": 0,
                                             "weather_id": 1, "weather_record": 0, "transition_s": 1})
        joined = proto.PEER_LEFT.encode({"peer_id": 1, "reason": 1})
        joiner.send([(M.ENTITY_SNAPSHOT, 0xFF, entity)], [(M.TIME_WEATHER, 0xFF, weather), (M.PEER_LEFT, 0, joined)])
        joiner.send([(M.EQUIP, 0xFF, proto.EQUIP.encode({"slot": 0, "weapon_class": 1, "flags": 0,
                                                         "item_record": 1, "appearance": 1, "ammo": 1}))])
        self.assertEqual([m for m in host.poll() if m[0] in (M.ENTITY_SNAPSHOT, M.TIME_WEATHER, M.PEER_LEFT, M.EQUIP)], [])
        self.assertEqual(self.h.relay.counters["violations"], 4)
        host.send([(M.ENTITY_SNAPSHOT, 0xFF, entity)], [(M.TIME_WEATHER, 0xFF, weather)])
        self.assertEqual(sorted(m[0] for m in joiner.poll() if m[1] == host.peer_id),
                         [M.ENTITY_SNAPSHOT, M.TIME_WEATHER])

    def test_invalid_values_not_forwarded(self):
        host, joiner = self.pair()
        nan = bytearray(player_body())
        struct.pack_into("<f", nan, 6, float("nan"))
        far = player_body(x=19999.0)
        far = far[:6] + struct.pack("<f", 25000.0) + far[10:]
        joiner.send([(M.PLAYER_SNAPSHOT, 0xFF, bytes(nan)), (M.PLAYER_SNAPSHOT, 0xFF, far)])
        self.assertEqual([m for m in host.poll() if m[0] == M.PLAYER_SNAPSHOT], [])
        self.assertEqual(self.h.relay.counters["violations"], 2)

    def test_hit_routing(self):
        host, joiner = self.pair()
        hit = {"target_net": 5, "target_kind": 0, "hit_zone": 1, "attack": 1, "flags": 0, "damage": 10.0,
               "weapon_record": 1, "rx": 0, "ry": 0, "rz": 0, "time_ms": 1}
        joiner.send(reliable=[(M.HIT, 0xFF, proto.HIT.encode(hit))])
        self.assertEqual([m[3]["target_net"] for m in host.poll() if m[0] == M.HIT], [5])
        host.send(reliable=[(M.HIT, 0xFF, proto.HIT.encode(dict(hit, target_kind=1, target_net=joiner.peer_id)))])
        self.assertEqual([m[3]["target_kind"] for m in joiner.poll() if m[0] == M.HIT], [1])

    def test_snapshot_ack_goes_to_host_only(self):
        host, joiner = self.pair()
        third = Client(self.h, proto.Role.JOINER)
        self.h.relay.rooms["r1"].max_size = 3
        self.assertTrue(third.join())
        host.poll(), joiner.poll(), third.poll()
        joiner.send([(M.SNAPSHOT_ACK, 0xFF, proto.SNAPSHOT_ACK.encode({"tick": 9}))])
        self.assertEqual([m[3]["tick"] for m in host.poll() if m[0] == M.SNAPSHOT_ACK], [9])
        self.assertEqual([m for m in third.poll() if m[0] == M.SNAPSHOT_ACK], [])

    def test_time_sync_reply(self):
        host, _ = self.pair()
        host.send([(M.TIME_REQ, 0, proto.TIME_REQ.encode({"t0": 4242}))])
        replies = [m[3] for m in host.poll() if m[0] == M.TIME_RESP]
        self.assertEqual(replies[0]["t0"], 4242)
        self.assertEqual(replies[0]["t1"], self.h.relay.relay_ms(self.h.now))

    def test_late_joiner_gets_cached_state(self):
        host = Client(self.h, proto.Role.HOST)
        self.assertTrue(host.join())
        weather = proto.TIME_WEATHER.encode({"game_seconds": 50000, "time_scale_x100": 100, "flags": 0,
                                             "weather_id": 4, "weather_record": 9, "transition_s": 5})
        equip = proto.EQUIP.encode({"slot": 0, "weapon_class": 4, "flags": 0, "item_record": 77, "appearance": 1,
                                    "ammo": 30})
        enter = proto.VEHICLE_ENTER.encode({"vehicle_net": 0x8001, "seat": 0, "flags": 0, "record": 5,
                                            "appearance": 6, "x": 1.0, "y": 2.0, "z": 3.0, "quat": 0})
        host.send(reliable=[(M.TIME_WEATHER, 0xFF, weather), (M.EQUIP, 0xFF, equip), (M.VEHICLE_ENTER, 0xFF, enter)])
        joiner = Client(self.h, proto.Role.JOINER)
        self.assertTrue(joiner.join())
        got = [(m[0], m[1]) for m in joiner.poll() if m[2]]
        self.assertEqual(got[0], (M.PEER_JOINED, 0))
        self.assertIn((M.TIME_WEATHER, host.peer_id), got)
        self.assertIn((M.EQUIP, host.peer_id), got)
        self.assertIn((M.VEHICLE_ENTER, host.peer_id), got)

    def test_chat_rate_limited(self):
        host, joiner = self.pair()
        chats = [(M.CHAT, 0xFF, proto.CHAT.encode({"channel": 0, "text": f"spam {i}"})) for i in range(20)]
        joiner.send(reliable=chats)
        self.assertEqual(len([m for m in host.poll() if m[0] == M.CHAT]), 5)
        self.assertEqual(self.h.relay.counters["rate_dropped"], 15)

    def test_unknown_token_ignored_and_rebind(self):
        host, joiner = self.pair()
        bogus = proto.encode_packet(proto.PacketType.DATA, 0xDEADBEEF, 1, 0, 0, b"")
        self.h.deliver(("192.0.2.1", 1), bogus)
        self.assertEqual(self.h.take(("192.0.2.1", 1)), [])
        self.assertEqual(self.h.relay.counters["unknown_token"], 1)
        old_address = joiner.address
        joiner.address = ("198.51.100.200", 61000)
        joiner.send([(M.PLAYER_SNAPSHOT, 0xFF, player_body())])
        self.assertEqual(self.h.relay.peers_by_token[joiner.conn.token].address, joiner.address)
        host.send([(M.PLAYER_SNAPSHOT, 0xFF, player_body())])
        self.assertTrue([m for m in joiner.poll() if m[0] == M.PLAYER_SNAPSHOT])
        stale = proto.encode_packet(proto.PacketType.DATA, joiner.conn.token, 1, 0, 0, b"")
        self.h.deliver(old_address, stale)
        self.assertEqual(self.h.relay.peers_by_token[joiner.conn.token].address, joiner.address)

    def test_abusive_client_is_kicked(self):
        host, joiner = self.pair()
        garbage = (M.PLAYER_SNAPSHOT, 0xFF, b"\x00" * 5)
        for _ in range(30):
            joiner.send([garbage] * 10)
        self.assertNotIn(joiner.conn.token, self.h.relay.peers_by_token)
        self.assertIn(("DISCONNECT", proto.DisconnectReason.KICKED, None, None), joiner.poll())
        self.assertIn(M.PEER_LEFT, [m[0] for m in host.poll()])

    def test_timeouts_and_room_cleanup(self):
        host, joiner = self.pair()
        self.h.advance(relay_v2.PEER_TIMEOUT_S + 0.5)
        self.assertEqual(self.h.relay.peers_by_token, {})
        self.h.advance(relay_v2.ROOM_LINGER_S + 1.0, step=0.5)
        self.assertEqual(self.h.relay.rooms, {})

    def test_relay_resends_unacked_reliable(self):
        host, joiner = self.pair()
        host.send(reliable=[(M.CHAT, 0xFF, proto.CHAT.encode({"channel": 0, "text": "hello"}))])
        self.h.take(joiner.address)  # lost on the way to the joiner
        self.h.advance(1.0)
        self.assertEqual([m[3]["text"] for m in joiner.poll() if m[0] == M.CHAT], ["hello"])


class LegacyTests(RelayTestCase):
    def cp1(self, address, seq, payload, x=-1400.0):
        fx, fy = legacy.encode_forward(0.0, 1.0, payload)
        self.h.deliver(address, legacy.format_cp1(seq, x, 200.0, 22.0, 1.0, fx, fy))

    def test_v1_clients_unchanged(self):
        a, b = ("192.0.2.10", 1000), ("192.0.2.11", 1001)
        self.cp1(a, 1, 2)
        self.cp1(b, 1, 3)
        self.cp1(a, 2, 2)
        self.assertIn(b"WELCOME,1", self.h.take(a))
        to_b = self.h.take(b)
        self.assertIn(b"WELCOME,2", to_b)
        rp1 = [d for d in to_b if d.startswith(b"RP1,1,2,")]
        self.assertEqual(len(rp1), 1)
        self.h.deliver(a, b"CP1,garbage")
        self.h.deliver(a, b"\x00\x01")
        self.assertEqual(self.h.relay.counters["malformed_v1"], 1)
        self.assertEqual(self.h.relay.counters["unknown_datagrams"], 1)

    def test_bridge_both_directions(self):
        host = Client(self.h, proto.Role.HOST, room="legacy", join_flags=int(proto.JoinFlag.LEGACY_BRIDGE))
        self.assertTrue(host.join())
        v1 = ("192.0.2.20", 2000)
        flags = legacy.V1_WEAPON_DRAWN | legacy.V1_CROUCH
        self.cp1(v1, 1, legacy.TYPE_FLAGS * legacy.TYPE_STRIDE + flags)
        self.cp1(v1, 2, legacy.TYPE_PING * legacy.TYPE_STRIDE + 77)
        got = host.poll()
        joined = [m[3] for m in got if m[0] == M.PEER_JOINED]
        self.assertEqual(joined[0]["peer_flags"] & relay_v2.PEER_FLAG_LEGACY, relay_v2.PEER_FLAG_LEGACY)
        snaps = [m for m in got if m[0] == M.PLAYER_SNAPSHOT]
        self.assertEqual(len(snaps), 2)
        self.assertGreaterEqual(snaps[0][1], relay_v2.LEGACY_PEER_BASE)
        self.assertTrue(snaps[0][3]["flags"] & proto.PlayerFlag.LEGACY)
        self.assertTrue(snaps[0][3]["flags"] & proto.PlayerFlag.CROUCH)
        self.h.take(v1)
        host.send([(M.PLAYER_SNAPSHOT, 0xFF, player_body())])
        host.send([(M.PLAYER_SNAPSHOT, 0xFF, player_body(seq=2))])
        lines = [d.decode() for d in self.h.take(v1) if d.startswith(b"RP1")]
        self.assertEqual(len(lines), 2)
        payloads = []
        for line in lines:
            parts = line.split(",")
            payloads.append(legacy.decode_forward(float(parts[7]), float(parts[8]))[2])
        self.assertEqual(payloads[0], legacy.TYPE_PONG * legacy.TYPE_STRIDE + 77)
        self.assertEqual(payloads[1], legacy.TYPE_FLAGS * legacy.TYPE_STRIDE + legacy.V1_WEAPON_DRAWN + legacy.V1_HOST)
        self.assertEqual(lines[0].split(",")[3], "-1450.000000")


class FuzzTests(RelayTestCase):
    def test_random_datagrams_never_crash(self):
        host, joiner = self.pair()
        rng = random.Random(5)
        valid = joiner.conn.build_packets(self.h.now, [(M.PLAYER_SNAPSHOT, 0xFF, player_body())], force=True)[0]
        for _ in range(4000):
            choice = rng.random()
            if choice < 0.3:
                data = bytes(rng.randrange(256) for _ in range(rng.randint(0, 120)))
            elif choice < 0.6:
                data = proto.MAGIC + bytes([2, rng.randrange(1, 8)]) + bytes(rng.randrange(256) for _ in range(rng.randint(0, 300)))
            else:
                mutated = bytearray(valid)
                for _ in range(rng.randint(1, 6)):
                    mutated[rng.randrange(4, len(mutated))] = rng.randrange(256)
                data = bytes(mutated)
            self.h.deliver((f"203.0.113.{rng.randrange(1, 9)}", rng.randrange(1, 65535)), data)
        self.h.advance(0.5)
        self.assertIn(host.conn.token, self.h.relay.peers_by_token)


if __name__ == "__main__":
    unittest.main()
