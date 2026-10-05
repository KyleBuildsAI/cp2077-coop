import math
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from coopnet import proto  # noqa: E402

JOIN = {"minor": 0, "role": 1, "join_flags": 0, "caps": proto.ALL_CAPS, "game_build": proto.game_build_id("2.31a"),
        "mod_major": 0, "mod_minor": 2, "mod_patch": 0, "mod_hash": 0x1122334455667788, "mod_count": 5,
        "client_nonce": 42, "resume_token": 0, "room": "night-city_1", "name": "V Сильверхенд"}

SAMPLES = {
    proto.MsgType.TIME_REQ: {"t0": 123456},
    proto.MsgType.TIME_RESP: {"t0": 1, "t1": 2, "t2": 3},
    proto.MsgType.PEER_JOINED: {"peer_id": 2, "role": 2, "minor": 0, "peer_flags": 0, "caps": 7, "mod_hash": 9,
                                "mod_count": 3, "mod_major": 0, "mod_minor": 2, "mod_patch": 1, "name": "joiner"},
    proto.MsgType.PEER_LEFT: {"peer_id": 2, "reason": 2},
    proto.MsgType.LINK_STATS: {"peer_id": 1, "reserved": 0, "rtt_ms": 45, "loss_in_permille": 10,
                               "loss_out_permille": 20},
    proto.MsgType.SNAPSHOT_ACK: {"tick": 99},
    proto.MsgType.FIRE_FX: {"time_ms": 5, "ox": 1, "oy": 2, "oz": 3, "dx": 0, "dy": 32767, "dz": 0,
                            "weapon_class": 4, "shots": 3},
    proto.MsgType.EQUIP: {"slot": 1, "weapon_class": 4, "flags": 0, "item_record": 0xDEADBEEF, "appearance": 7,
                          "ammo": 30},
    proto.MsgType.VEHICLE_ENTER: {"vehicle_net": 0x8001, "seat": 0, "flags": 0, "record": 1, "appearance": 2,
                                  "x": -1450.5, "y": 180.25, "z": 22.0, "quat": proto.pack_quat(0, 0, 0, 1)},
    proto.MsgType.VEHICLE_EXIT: {"vehicle_net": 0x8001, "seat": 0, "flags": 0, "x": 1.0, "y": 2.0, "z": 3.0,
                                 "yaw": 1000},
    proto.MsgType.HIT: {"target_net": 12, "target_kind": 0, "hit_zone": 2, "attack": 1, "flags": 1, "damage": 33.5,
                        "weapon_record": 5, "rx": 0, "ry": 0, "rz": 150, "time_ms": 77},
    proto.MsgType.DEATH: {"target_net": 12, "target_kind": 0, "cause": 1, "killer_net": 2, "killer_kind": 1,
                          "flags": 0, "time_ms": 78},
    proto.MsgType.TIME_WEATHER: {"game_seconds": 43200, "time_scale_x100": 100, "flags": 0, "weather_id": 3,
                                 "weather_record": 0xABC, "transition_s": 10},
    proto.MsgType.CHAT: {"channel": 0, "text": "привет, choom"},
    proto.MsgType.TELEPORT_REQ: {"req_id": 1, "mode": 0, "reserved": 0},
    proto.MsgType.TELEPORT_RESP: {"req_id": 1, "accepted": 1, "reserved": 0, "x": 1.0, "y": 2.0, "z": 3.0,
                                  "yaw": 5},
    proto.MsgType.WORLD_FACT: {"fact_hash": 0xFFFFFFFFFFFFFFFF, "value": -5},
    proto.MsgType.MOD_LIST: {"chunk": 0, "chunks": 2, "text": "Codeware@1.18.0;redscript@0.5.27"},
    proto.MsgType.SESSION_CONFIG: {"npc_radius_m": 100, "vehicle_radius_m": 200, "entity_hz": 10,
                                   "joiner_population": 0, "flags": 0},
    proto.MsgType.SCRIPT_MSG: {"channel": 17, "flags": 0, "text": 'evt|weapon|draw\t{"ammo": 30}\nпривет'},
}

PLAYER = {"snap_seq": 7, "sample_time": 1000, "x": -1450.0, "y": 180.0, "z": 22.0, "yaw": 16384, "pitch": -350,
          "vx": 600, "vy": 0, "vz": 0, "move_state": 2, "health": 230, "flags": 0x0402, "vehicle": None}
VEHICLE = {"vehicle_net": 0x8002, "px": -1450.0, "py": 180.0, "pz": 22.0, "quat": proto.pack_quat(0, 0, 0.38, 0.92),
           "lvx": 1400, "lvy": 0, "lvz": 0, "avx": 0, "avy": 0, "avz": 120, "steer": -20, "throttle": 70,
           "brake": 0, "vflags": 1}

# Documented sizes (bytes) - the C header asserts the same numbers.
FIXED_SIZES = {
    "TIME_REQ": 4, "TIME_RESP": 12, "PEER_JOINED": 24, "PEER_LEFT": 2, "LINK_STATS": 8, "PLAYER_SNAPSHOT": 32,
    "VehicleBlock": 34, "SNAPSHOT_ACK": 4, "FIRE_FX": 24, "EQUIP": 22, "VEHICLE_ENTER": 36, "VEHICLE_EXIT": 18,
    "HIT": 28, "DEATH": 12, "TIME_WEATHER": 18, "CHAT": 1, "TELEPORT_REQ": 4, "TELEPORT_RESP": 18, "WORLD_FACT": 12,
    "MOD_LIST": 2, "SESSION_CONFIG": 8, "SCRIPT_MSG": 2,
}


def entity_body():
    return proto.EntitySnapshotCodec().encode({"tick": 10, "baseline": 8, "sample_time": 5000, "records": [
        {"net_id": 1, "spawn": {"kind": 4, "spawn_flags": 2, "attitude": 1, "record": 9, "appearance": 1},
         "pos": (-1450000, 180000, 22000), "quat": proto.pack_quat(0, 0, 0, 1), "vel": (1200, 0, 0)},
        {"net_id": 2, "pos_delta": (120, -40, 0), "yaw": 300, "state": (1, 2, 200)},
        {"net_id": 3, "remove": True},
        {"net_id": 4, "target": 0xFF02, "weapon": 77},
        {"net_id": 6, "spawn": {"kind": 3, "spawn_flags": 0, "attitude": 1, "record": 10, "appearance": 2},
         "pos": (-1449000, 181000, 22000), "yaw": 900, "world_id": 0x8F00112233445566},
        {"net_id": 7, "world_id": 0x0000000001000001},
    ]})


class HeaderTests(unittest.TestCase):
    def test_packet_roundtrip(self):
        packet = proto.encode_packet(proto.PacketType.DATA, 0x1234, 5, 4, 0xF0F0F0F0, b"xyz")
        self.assertEqual(len(packet), proto.PACKET_HEADER.size + 3)
        self.assertEqual(proto.PACKET_HEADER.size, 20)
        self.assertEqual(proto.decode_packet(packet), (6, 0x1234, 5, 4, 0xF0F0F0F0, b"xyz"))

    def test_magic_never_collides_with_v1_text(self):
        for text in (b"CP1,1,0,0,0,1,0,1", b"RP1,1,1,0,0,0,1,0,1", b"WELCOME,1"):
            self.assertFalse(proto.is_v2(text))
        self.assertGreaterEqual(proto.MAGIC[0], 0x80)  # never the first byte of an ASCII datagram

    def test_rejects(self):
        with self.assertRaises(proto.ProtocolError):
            proto.decode_packet(b"\xcb\x77\x02")
        with self.assertRaises(proto.ProtocolError):
            proto.decode_packet(b"XX" + bytes(30))
        with self.assertRaises(proto.ProtocolError):
            proto.decode_packet(proto.MAGIC + bytes([2, 99]) + bytes(16))
        with self.assertRaises(proto.ProtocolError):
            proto.decode_packet(proto.MAGIC + bytes(1300))
        with self.assertRaises(proto.VersionMismatch) as caught:
            proto.decode_packet(proto.MAGIC + bytes([3, 1]) + bytes(40))
        self.assertEqual(caught.exception.major, 3)


class HandshakeTests(unittest.TestCase):
    def test_hello_is_padded_and_challenge_cannot_amplify(self):
        hello = proto.encode_hello(JOIN)
        self.assertGreaterEqual(len(hello), proto.HELLO_MIN_PACKET)
        challenge = proto.encode_challenge(bytes(16))
        self.assertEqual(len(challenge), 40)
        self.assertLess(len(challenge), len(hello))
        reject = proto.encode_reject(proto.RejectReason.BAD_KEY, "x" * 200)
        self.assertLess(len(reject), len(hello))
        info = proto.decode_hello(proto.decode_packet(hello)[5])
        self.assertEqual(info, JOIN)

    def test_short_hello_rejected(self):
        body = proto.encode_join(JOIN)
        with self.assertRaises(proto.ProtocolError):
            proto.decode_hello(body)

    def test_auth_welcome_reject_roundtrip(self):
        auth = proto.encode_auth(JOIN, bytes(range(16)), proto.room_key_hash("r", "p"))
        info, cookie, key_hash = proto.decode_auth(proto.decode_packet(auth)[5])
        self.assertEqual(info, JOIN)
        self.assertEqual(cookie, bytes(range(16)))
        self.assertEqual(key_hash, proto.room_key_hash("r", "p"))
        fields = {"minor": 0, "peer_id": 3, "role": 2, "room_flags": 1, "token": 2**63 + 5, "relay_time_ms": 99,
                  "player_hz": 30, "entity_hz": 10, "max_packet": 1200, "room_caps": 0x1FF}
        welcome = proto.encode_welcome(fields)
        self.assertEqual(len(welcome), 44)
        self.assertEqual(proto.decode_welcome(proto.decode_packet(welcome)[5]), fields)
        reject = proto.encode_reject(proto.RejectReason.VERSION, "relay speaks v2.0")
        self.assertEqual(proto.decode_reject(proto.decode_packet(reject)[5]),
                         (1, proto.MIN_SUPPORTED_MINOR, proto.PROTO_MINOR, "relay speaks v2.0"))

    def test_bad_room_and_name(self):
        for room in ("", "a" * 33, "bad room", "комната"):
            with self.assertRaises(proto.ProtocolError):
                proto.encode_join(dict(JOIN, room=room))
        with self.assertRaises(proto.ProtocolError):
            proto.encode_join(dict(JOIN, name="evil\x1b[31m"))

    def test_cookie(self):
        secret = os.urandom(32)
        address = ("203.0.113.5", 50000)
        cookie = proto.make_cookie(secret, address, 77, 1000)
        self.assertTrue(proto.check_cookie(secret, address, 77, cookie, 1005))
        self.assertFalse(proto.check_cookie(secret, address, 77, cookie, 1011))
        self.assertFalse(proto.check_cookie(secret, ("203.0.113.6", 50000), 77, cookie, 1001))
        self.assertFalse(proto.check_cookie(secret, address, 78, cookie, 1001))
        self.assertFalse(proto.check_cookie(os.urandom(32), address, 77, cookie, 1001))

    def test_identity_hashes(self):
        mods = [("Codeware", "1.18.0"), ("redscript", "0.5.27")]
        self.assertEqual(proto.mod_list_hash(mods), proto.mod_list_hash(list(reversed(mods))))
        self.assertEqual(proto.mod_list_hash(mods), proto.mod_list_hash([("codeware", "1.18.0"), ("REDSCRIPT", "0.5.27")]))
        self.assertNotEqual(proto.mod_list_hash(mods), proto.mod_list_hash([("Codeware", "1.17.0"), mods[1]]))
        self.assertNotEqual(proto.room_key_hash("a", "b"), proto.room_key_hash("a", "c"))
        self.assertEqual(len(proto.room_key_hash("a", "b")), 16)


class MessageTests(unittest.TestCase):
    def test_all_messages_roundtrip(self):
        for mtype, values in SAMPLES.items():
            body = proto.encode_body(mtype, values)
            decoded = proto.decode_body(mtype, body)
            for key, value in values.items():
                if isinstance(value, float):
                    self.assertAlmostEqual(decoded[key], value, places=4, msg=f"{mtype.name}.{key}")
                else:
                    self.assertEqual(decoded[key], value, msg=f"{mtype.name}.{key}")

    def test_documented_sizes(self):
        codecs = [spec.codec for spec in proto.SPECS.values() if isinstance(spec.codec, proto.FixedCodec)]
        codecs += [proto.PLAYER_BASE, proto.VEHICLE_BLOCK]
        for codec in codecs:
            self.assertEqual(codec.fixed_size, FIXED_SIZES[codec.name], codec.name)
        self.assertEqual(proto.ENTITY_HEADER.size, 14)
        self.assertEqual(proto.SPAWN_BLOCK.size, 20)
        self.assertEqual(proto.MESSAGE_HEADER.size, 4)

    def test_player_snapshot_with_and_without_vehicle(self):
        codec = proto.PlayerSnapshotCodec()
        self.assertEqual(len(codec.encode(PLAYER)), 32)
        driving = dict(PLAYER, flags=PLAYER["flags"] | proto.PlayerFlag.DRIVING, vehicle=VEHICLE)
        body = codec.encode(driving)
        self.assertEqual(len(body), 66)
        decoded = codec.decode(body)
        self.assertEqual(decoded["vehicle"]["steer"], -20)
        with self.assertRaises(proto.ProtocolError):
            codec.decode(body[:-1])
        with self.assertRaises(proto.ProtocolError):
            codec.decode(codec.encode(PLAYER) + bytes(34))

    def test_player_validation(self):
        codec = proto.PlayerSnapshotCodec()
        for bad in ({"x": float("nan")}, {"y": float("inf")}, {"x": 1e6}, {"z": -6000.0}, {"move_state": 99},
                    {"pitch": 9001}):
            with self.assertRaises(proto.ProtocolError, msg=str(bad)):
                codec.encode(dict(PLAYER, **bad))

    def test_text_validation(self):
        for text in ("x" * 201, "line\nbreak", "bell\x07", " "):
            with self.assertRaises(proto.ProtocolError, msg=repr(text)):
                proto.CHAT.encode({"channel": 0, "text": text})
        body = bytearray(proto.CHAT.encode({"channel": 0, "text": "ok"}))
        body[-1] = 0xFF
        with self.assertRaises(proto.ProtocolError):
            proto.CHAT.decode(bytes(body))

    def test_entity_snapshot_roundtrip_and_rules(self):
        codec = proto.EntitySnapshotCodec()
        decoded = codec.decode(entity_body())
        self.assertEqual(len(decoded["records"]), 6)
        self.assertTrue(decoded["records"][2]["remove"])
        self.assertEqual(decoded["records"][3]["target"], 0xFF02)
        bad_records = [
            {"net_id": 0, "yaw": 1},
            {"net_id": 1, "pos": (0, 0, 0), "pos_delta": (1, 1, 1)},
            {"net_id": 1, "yaw": 1, "quat": 2},
            {"net_id": 1, "spawn": {"kind": 1, "spawn_flags": 0, "attitude": 0, "record": 1, "appearance": 1},
             "yaw": 5},
            {"net_id": 1, "spawn": {"kind": 9, "spawn_flags": 0, "attitude": 0, "record": 1, "appearance": 1},
             "pos": (0, 0, 0), "yaw": 5},
            {"net_id": 1, "pos": (2**31 - 1, 0, 0)},
            {"net_id": 1, "state": (99, 0, 0)},
            {"net_id": 1},
        ]
        for record in bad_records:
            with self.assertRaises(proto.ProtocolError, msg=str(record)):
                codec.encode({"tick": 2, "baseline": 0, "sample_time": 0, "records": [record]})
        with self.assertRaises(proto.ProtocolError):
            codec.encode({"tick": 2, "baseline": 0, "sample_time": 0,
                          "records": [{"net_id": 5, "yaw": 1}, {"net_id": 5, "yaw": 2}]})
        with self.assertRaises(proto.ProtocolError):
            codec.encode({"tick": 2, "baseline": 2, "sample_time": 0, "records": []})
        removal = bytearray(codec.encode({"tick": 2, "baseline": 0, "sample_time": 0,
                                          "records": [{"net_id": 5, "remove": True}]}))
        removal[proto.ENTITY_HEADER.size + 2] |= proto.M_YAW
        with self.assertRaises(proto.ProtocolError):
            codec.decode(bytes(removal))

    def test_script_msg(self):
        codec = proto.SCRIPT_MSG
        longest = codec.encode({"channel": 1, "flags": 0, "text": "x" * proto.MAX_SCRIPT_BYTES})
        self.assertEqual(len(longest), 2 + 2 + 1000)
        self.assertEqual(codec.decode(longest)["text"], "x" * 1000)
        multibyte = codec.encode({"channel": 31, "flags": 0x80, "text": "ж" * 500})
        self.assertEqual(codec.decode(multibyte)["flags"], 0x80)
        for bad in ({"text": "x" * 1001}, {"text": "ж" * 501}, {"text": "nul\x00inside"}, {"channel": 0},
                    {"channel": 32}):
            with self.assertRaises(proto.ProtocolError, msg=str(bad)[:40]):
                codec.encode(dict({"channel": 1, "flags": 0, "text": "ok"}, **bad))
        body = bytearray(codec.encode({"channel": 1, "flags": 0, "text": "ok"}))
        for mutated in (bytes(body[:-1]), bytes(body) + b"!", bytes(body[:3]), bytes(body[:2]),
                        bytes(body[:-2]) + b"\xc3\x28"):
            with self.assertRaises(proto.ProtocolError, msg=mutated.hex()):
                codec.decode(mutated)
        spec = proto.SPECS[proto.MsgType.SCRIPT_MSG]
        self.assertIsNone(spec.reliable)
        self.assertEqual(spec.min_minor, 1)
        for channel in (1, 15):
            self.assertTrue(proto.delivery_ok(spec, False, {"channel": channel}))
            self.assertFalse(proto.delivery_ok(spec, True, {"channel": channel}))
        for channel in (16, 31):
            self.assertTrue(proto.delivery_ok(spec, True, {"channel": channel}))
            self.assertFalse(proto.delivery_ok(spec, False, {"channel": channel}))
        self.assertTrue(proto.delivery_ok(proto.SPECS[proto.MsgType.CHAT], True, {}))
        self.assertFalse(proto.delivery_ok(proto.SPECS[proto.MsgType.CHAT], False, {}))

    def test_world_id_extension(self):
        codec = proto.EntitySnapshotCodec()
        decoded = codec.decode(entity_body())
        by_id = {record["net_id"]: record for record in decoded["records"]}
        self.assertEqual(by_id[6]["world_id"], 0x8F00112233445566)
        self.assertEqual(by_id[7], {"net_id": 7, "world_id": 0x0000000001000001})
        self.assertEqual(codec.record_size(by_id[7]), 3 + 1 + 8)
        self.assertEqual(len(codec.encode({"tick": 1, "baseline": 0, "sample_time": 0, "records": [by_id[7]]})),
                         proto.ENTITY_HEADER.size + 12)
        self.assertEqual(proto.required_minor(proto.MsgType.ENTITY_SNAPSHOT, decoded), 1)
        plain = codec.decode(codec.encode({"tick": 1, "baseline": 0, "sample_time": 0,
                                           "records": [{"net_id": 2, "yaw": 1}]}))
        self.assertEqual(proto.required_minor(proto.MsgType.ENTITY_SNAPSHOT, plain), 0)
        self.assertEqual(proto.required_minor(proto.MsgType.SCRIPT_MSG, {}), 1)
        self.assertEqual(proto.required_minor(proto.MsgType.CHAT, {}), 0)
        with self.assertRaises(proto.ProtocolError):
            codec.encode({"tick": 1, "baseline": 0, "sample_time": 0,
                          "records": [{"net_id": 2, "remove": True, "world_id": 5}]})
        unknown_ext = bytearray(codec.encode({"tick": 1, "baseline": 0, "sample_time": 0, "records": [by_id[7]]}))
        unknown_ext[proto.ENTITY_HEADER.size + 3] |= 0x10
        with self.assertRaises(proto.ProtocolError):
            codec.decode(bytes(unknown_ext))

    def test_disconnect_and_minor(self):
        self.assertEqual(proto.PROTO_MINOR, 1)
        packet = proto.encode_disconnect(77, proto.DisconnectReason.KICKED)
        ptype, token, _, _, _, body = proto.decode_packet(packet)
        self.assertEqual((ptype, token, proto.decode_disconnect(body)), (proto.PacketType.DISCONNECT, 77, 3))
        for body in (b"", b"\x01\x02"):
            with self.assertRaises(proto.ProtocolError):
                proto.decode_disconnect(body)

    def test_quaternion_precision(self):
        rng = random.Random(3)
        worst = 0.0
        for _ in range(2000):
            q = [rng.gauss(0, 1) for _ in range(4)]
            norm = math.sqrt(sum(c * c for c in q))
            q = [c / norm for c in q]
            r = proto.unpack_quat(proto.pack_quat(*q))
            dot = abs(sum(a * b for a, b in zip(q, r)))
            worst = max(worst, math.degrees(2 * math.acos(min(1.0, dot))))
        self.assertLess(worst, 0.25)

    def test_quantizers(self):
        self.assertEqual(proto.yaw_to_u16(360.0), 0)
        self.assertAlmostEqual(proto.u16_to_yaw(proto.yaw_to_u16(123.4)), 123.4, places=2)
        self.assertEqual(proto.velocity_to_cms(1e9), 32767)
        self.assertEqual(proto.meters_to_mm(-1450.0005), -1450000)


class FuzzTests(unittest.TestCase):
    """Malformed input must only ever raise ProtocolError, never anything else."""

    def assert_safe(self, function, data):
        try:
            function(data)
        except proto.ProtocolError:
            pass

    def test_fuzz_message_bodies(self):
        rng = random.Random(11)
        valid = {mtype: proto.encode_body(mtype, values) for mtype, values in SAMPLES.items()}
        valid[proto.MsgType.PLAYER_SNAPSHOT] = proto.PlayerSnapshotCodec().encode(
            dict(PLAYER, flags=PLAYER["flags"] | proto.PlayerFlag.DRIVING, vehicle=VEHICLE))
        valid[proto.MsgType.ENTITY_SNAPSHOT] = entity_body()
        for mtype, body in valid.items():
            for _ in range(400):
                mutated = bytearray(body)
                choice = rng.random()
                if choice < 0.3 and mutated:
                    del mutated[rng.randrange(len(mutated)):]
                elif choice < 0.7 and mutated:
                    for _ in range(rng.randint(1, 4)):
                        mutated[rng.randrange(len(mutated))] = rng.randrange(256)
                else:
                    mutated += bytes(rng.randrange(256) for _ in range(rng.randint(1, 8)))
                self.assert_safe(lambda data, m=mtype: proto.decode_body(m, data), bytes(mutated))
            for _ in range(200):
                self.assert_safe(lambda data, m=mtype: proto.decode_body(m, data),
                                 bytes(rng.randrange(256) for _ in range(rng.randint(0, 64))))

    def test_fuzz_packets_and_framing(self):
        rng = random.Random(12)
        for _ in range(3000):
            payload = bytes(rng.randrange(256) for _ in range(rng.randint(0, 80)))
            data = proto.MAGIC + bytes([2, rng.randrange(9)]) + payload
            self.assert_safe(proto.decode_packet, data)
            self.assert_safe(proto.decode_messages, payload)
            self.assert_safe(proto.decode_hello, payload)
            self.assert_safe(proto.decode_auth, payload)


if __name__ == "__main__":
    unittest.main()
