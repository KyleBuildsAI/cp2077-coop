"""Tests for the NPC sync text format v1 reference codec and the probe diff tool.

Run: python -m pytest npcsync/tests -q   (from the net2 folder)
"""
from __future__ import annotations

import math
import os
import random
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

import npc_codec as codec  # noqa: E402
import npc_probe_diff as probe  # noqa: E402

RELAY_DIR = os.path.join(HERE, "..", "..", "relay")


def angle_error(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def make_scene(rng: random.Random, count: int = 40, walkers: int = 8, runners: int = 2):
    anchor = (-1203.4, 1542.7, 12.2)
    states = []
    for index in range(count):
        heading = rng.uniform(0.0, 360.0)
        if index < runners:
            speed, move, flags = 4.0, codec.MOVE_RUN, codec.FLAG_COMBAT | codec.FLAG_WEAPON | codec.FLAG_HOSTILE
        elif index < runners + walkers:
            speed, move, flags = 1.4, codec.MOVE_WALK, 0
        else:
            speed, move, flags = 0.0, codec.MOVE_IDLE, codec.FLAG_WORKSPOT if index % 3 == 0 else 0
        vx = -math.sin(math.radians(heading)) * speed
        vy = math.cos(math.radians(heading)) * speed
        states.append(codec.NpcState(
            net_id=index + 1,
            position=(anchor[0] + rng.uniform(-60, 60), anchor[1] + rng.uniform(-60, 60), anchor[2] + rng.uniform(-3, 8)),
            yaw=heading, velocity=(vx, vy), move_state=move, flags=flags,
            health=rng.uniform(0.2, 1.0), target=codec.TARGET_JOINER if flags & codec.FLAG_COMBAT else 0,
        ))
    return anchor, states


def advance(states, dt: float):
    for s in states:
        s.position = (s.position[0] + s.velocity[0] * dt, s.position[1] + s.velocity[1] * dt, s.position[2])


# ---------------------------------------------------------------------------
# quantization
# ---------------------------------------------------------------------------

def test_round_matches_game_roundf():
    assert codec.round_f(2.5) == 3
    assert codec.round_f(-2.5) == -3
    assert codec.round_f(0.49) == 0
    assert codec.round_f(-0.51) == -1


def test_yaw_quantization_and_wrap():
    for yaw in (0.0, 0.004, 90.0, 179.99, 180.0, -180.0, -90.0, 359.999, 720.5, -725.25):
        decoded = codec.u16_to_yaw(codec.yaw_to_u16(yaw))
        assert angle_error(decoded, yaw) <= 360.0 / 65536.0 / 2.0 + 1e-9
        assert -180.0 < decoded <= 180.0
    assert codec.yaw_to_u16(359.9999) == 0


def test_snapshot_roundtrip_error_bounds():
    rng = random.Random(7)
    anchor = (812.6, -2304.4, 41.5)
    states = [codec.NpcState(
        net_id=i + 1,
        position=(anchor[0] + rng.uniform(-300, 300), anchor[1] + rng.uniform(-300, 300), anchor[2] + rng.uniform(-50, 50)),
        yaw=rng.uniform(-720, 720), velocity=(rng.uniform(-12, 12), rng.uniform(-12, 12)),
        move_state=rng.choice([0, 1, 2, 3, 4, 5]), flags=rng.randrange(0, 4096), health=rng.random(),
        target=rng.choice([0, 7, codec.TARGET_HOST, codec.TARGET_JOINER]),
    ) for i in range(200)]
    messages = codec.encode_snapshot(states, anchor, seq=65535, host_ms=1999999999)
    decoded = []
    for message in messages:
        seq, part, parts, chunk = codec.decode_snapshot(message)
        assert seq == 65535 and parts == len(messages) and 0 <= part < parts
        decoded.extend(chunk)
    assert [s.net_id for s in decoded] == [s.net_id for s in states]
    for original, copy in zip(states, decoded):
        assert math.dist(original.position, copy.position) <= math.sqrt(3) * 0.005 + 1e-6
        assert angle_error(original.yaw, copy.yaw) <= 360.0 / 65536.0 / 2.0 + 1e-6
        assert abs(original.velocity[0] - copy.velocity[0]) <= 0.005 + 1e-9
        assert abs(original.velocity[1] - copy.velocity[1]) <= 0.005 + 1e-9
        assert abs(original.health - copy.health) <= 0.5 / 255.0 + 1e-9
        assert (copy.move_state, copy.flags, copy.target) == (original.move_state, original.flags, original.target)


# ---------------------------------------------------------------------------
# sizes
# ---------------------------------------------------------------------------

def test_worst_case_entries_fit_one_datagram_each_chunk():
    worst = codec.NpcState(net_id=65533, position=(-327.67, -327.67, -327.67), yaw=359.0,
                           velocity=(-327.67, -327.67), move_state=11, flags=4095, health=1.0, target=65535)
    entry = codec.encode_entry(worst, (0.0, 0.0, 0.0))
    # 64 chars is the longest possible entry; the 65-byte header reserve covers the longest NS1 header (46)
    assert len(entry) == 64
    longest_header = "NS1 65535 1999999999 -99999 -99999 -9999 15 16 "
    assert len(longest_header) < codec.HEADER_RESERVE
    messages = codec.encode_snapshot([worst] * 40, (-99999.4, -99999.4, -9999.4), seq=65535, host_ms=1999999999)
    for message in messages:
        assert len(message) <= codec.MAX_PAYLOAD_BYTES <= codec.CPN2_MAX_PAYLOAD
    assert len(messages) == 3


def test_empty_snapshot_is_one_message():
    messages = codec.encode_snapshot([], (1.0, 2.0, 3.0), 1, 0)
    assert len(messages) == 1
    assert codec.decode_snapshot(messages[0])[3] == []


def test_bind_roundtrip_and_lua_fields():
    state = codec.NpcState(net_id=42, position=(-1203.37, 1542.21, 12.05), yaw=-35.0, kind=codec.KIND_QUEST_NPC,
                           spawn_flags=codec.SPAWN_STATIC_ID | codec.SPAWN_PERSISTENT, attitude=2,
                           record_hash=0xDEADBEEF, record_length=27, appearance=0xFEDCBA9876543210,
                           world_id=0x9A3C51E27D10F4B2)
    message = codec.encode_bind(state)
    assert len(message) < 200
    back = codec.decode_bind(message)
    assert (back.net_id, back.kind, back.spawn_flags, back.attitude) == (42, codec.KIND_QUEST_NPC, 24, 2)
    assert (back.record_hash, back.record_length, back.appearance, back.world_id) == (
        0xDEADBEEF, 27, 0xFEDCBA9876543210, 0x9A3C51E27D10F4B2)
    assert codec.lua_record_parts(message) == (0xDEADBEEF, 27)
    # Lua numbers are doubles: the record parts must stay below 2^53, the 64-bit ids are left to redscript
    assert all(value < 2 ** 53 for value in codec.lua_record_parts(message))
    assert math.dist(back.position, state.position) < 0.01


def test_static_id_rule_matches_red4ext_bound():
    assert not codec.is_static_id(0x00FFFFFF)
    assert codec.is_static_id(0x01000000)


# ---------------------------------------------------------------------------
# bandwidth: text v1 with the host's idle throttle, vs the binary v2 delta codec
# ---------------------------------------------------------------------------

def simulate_text_v1(seconds: float = 10.0, hz: int = 10, seed: int = 3):
    rng = random.Random(seed)
    anchor, states = make_scene(rng)
    throttle = codec.HostThrottle()
    total, datagrams, max_parts = 0, 0, 0
    ticks = int(seconds * hz)
    for tick in range(ticks):
        now = tick / hz
        chosen = throttle.select(states, now)
        messages = codec.encode_snapshot(chosen, anchor, tick % 65536, int(now * 1000))
        max_parts = max(max_parts, len(messages))
        total += sum(codec.wire_bytes(m) for m in messages)
        datagrams += len(messages)
        advance(states, 1.0 / hz)
    return total / seconds, datagrams / seconds, max_parts


def test_text_v1_bandwidth_typical_scene(capsys):
    bytes_per_s, datagrams_per_s, max_parts = simulate_text_v1()
    with capsys.disabled():
        print(f"\n[text v1] 40 NPCs (8 walking, 2 running, 30 idle), 10 Hz: "
              f"{bytes_per_s / 1024:.2f} KiB/s = {bytes_per_s * 8 / 1000:.1f} kbit/s, "
              f"{datagrams_per_s:.1f} datagrams/s, max {max_parts} parts per snapshot")
    assert bytes_per_s < 12 * 1024
    assert max_parts <= 2


def test_text_v1_all_moving_upper_bound(capsys):
    rng = random.Random(5)
    anchor, states = make_scene(rng, count=40, walkers=30, runners=10)
    messages = codec.encode_snapshot(states, anchor, 1, 0)
    per_snapshot = sum(codec.wire_bytes(m) for m in messages)
    with capsys.disabled():
        print(f"[text v1] worst realistic tick (40 moving): {per_snapshot} B in {len(messages)} datagrams "
              f"-> {per_snapshot * 10 / 1024:.2f} KiB/s at 10 Hz")
    assert per_snapshot * 10 < 20 * 1024


def test_binary_v2_delta_equivalent(capsys):
    """Same scene through the sibling relay's EntitySnapshotCodec + DeltaEncoder (if present)."""
    if not os.path.isdir(os.path.join(RELAY_DIR, "coopnet")):
        pytest.skip("relay/coopnet not present")
    sys.path.insert(0, RELAY_DIR)
    try:
        from coopnet import proto, snapshot  # type: ignore
    except Exception as error:  # the sibling module may be mid-edit
        pytest.skip(f"relay/coopnet not importable: {error}")

    rng = random.Random(3)
    anchor, states = make_scene(rng)
    encoder = snapshot.DeltaEncoder()
    hz, seconds, rtt_ticks = 10, 10.0, 3
    total = 0
    for tick in range(int(seconds * hz)):
        entity_states = {
            s.net_id: snapshot.quantize(proto.EntityKind.CROWD_NPC, 0, 1, 0x1234567812, 0x42, s.position, s.yaw,
                                        (s.velocity[0], s.velocity[1], 0.0), s.move_state, s.flags & 0xFF,
                                        codec.health_to_byte(s.health), s.target if s.target < 65534 else 0)
            for s in states
        }
        body, encoded_tick, _, _ = encoder.encode(tick * 100, entity_states)
        total += len(body) + 20 + 4 + codec.UDP_IPV4_OVERHEAD
        if encoded_tick > rtt_ticks:
            encoder.on_ack(encoded_tick - rtt_ticks)
        advance(states, 1.0 / hz)
    text_bytes, _, _ = simulate_text_v1()
    with capsys.disabled():
        print(f"[binary v2 delta] same scene, acks {rtt_ticks} ticks late: {total / seconds / 1024:.2f} KiB/s "
              f"(text v1: {text_bytes / 1024:.2f} KiB/s)")
    assert total / seconds < text_bytes


# ---------------------------------------------------------------------------
# probe diff
# ---------------------------------------------------------------------------

def probe_line(hash_, static, crowd, rec, app, x, y, z, dead=0):
    return f"PROBE,N,{hash_},{static},{crowd},{rec},20,{app},{int(x * 100)},{int(y * 100)},{int(z * 100)},{dead}"


def test_probe_diff_counts_mirrors_and_proxies():
    host = [
        "[CP2077Coop] PROBE_BEGIN,5,0,0,0",
        probe_line(0x9A3C51E27D10F4B2, 1, 0, 111, 501, 10, 10, 0),
        probe_line(0x1111111111111111, 1, 0, 222, 502, 20, 5, 0),
        probe_line(0x2222222222222222, 1, 0, 333, 503, 30, 5, 0),
        probe_line(0x00001234, 0, 1, 444, 504, 1, 1, 0),
        probe_line(0x00001235, 0, 1, 444, 505, 2, 1, 0),
        "PROBE_END",
    ]
    joiner = [
        probe_line(0x9A3C51E27D10F4B2, 1, 0, 111, 501, 10.5, 10, 0),
        probe_line(0x1111111111111111, 1, 0, 222, 999, 23, 5, 0),
        probe_line(0x3333333333333333, 1, 0, 666, 506, 40, 5, 0),
        probe_line(0x00001234, 0, 1, 777, 507, 50, 50, 0),
    ]
    result = probe.compare(probe.parse_probe(host), probe.parse_probe(joiner))
    assert result["host_static"] == 3 and result["joiner_static"] == 3
    assert result["shared_static"] == 2
    assert result["host_only_static"] == 1 and result["joiner_only_static"] == 1
    assert result["appearance_mismatch"] == 1 and result["record_mismatch"] == 0
    assert result["shared_dynamic"] == 1          # same small counter value, different NPC
    assert result["host_needs_proxy"] == 3        # 1 host-only static + 2 crowd
    assert result["host_crowd"] == 2
    assert 0.4 < result["mirror_distance_p50_m"] < 3.1


def test_probe_diff_cli(tmp_path):
    host_file = tmp_path / "host.log"
    joiner_file = tmp_path / "joiner.log"
    host_file.write_text(probe_line(0x9A3C51E27D10F4B2, 1, 0, 1, 2, 0, 0, 0) + "\n", encoding="utf-8")
    joiner_file.write_text("nothing here\n", encoding="utf-8")
    assert probe.main([str(host_file), str(joiner_file)]) == 2
    joiner_file.write_text(probe_line(0x9A3C51E27D10F4B2, 1, 0, 1, 2, 0, 0, 0) + "\n", encoding="utf-8")
    assert probe.main([str(host_file), str(joiner_file)]) == 0
