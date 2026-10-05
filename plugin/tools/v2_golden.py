"""Golden vectors between the C++ v2 codec (src/v2) and relay/coopnet/proto.py, in both directions.

    python tools/v2_golden.py run --exe build/Release/coopnet_v2_golden.exe

1. Checks that src/v2/coop_proto_v2.h is a byte-identical copy of relay/include/coop_proto_v2.h.
2. Writes build/golden/py_vectors.txt with proto.py: datagrams of every packet and message type
   (proto.py encoders), datagrams proto.py must reject, random mutations judged by proto.py,
   quantizer and hash vectors, the message table and constants, and delta snapshot scenarios run
   through snapshot.DeltaEncoder/DeltaDecoder.
3. Has the C++ side decode, describe and re-encode all of it byte for byte (exe check).
4. Has the C++ side encode its own vectors (exe emit) and checks every one here with proto.py:
   decoded values, verdicts on mutations, re-encoding, hashes, quantizers and delta scenarios.

The relay checkout is found through --relay, the COOPNET_RELAY_DIR environment variable, or the
sibling folder ../relay. Other subcommands: generate <out>, check-cpp <in>, check-header.
Python 3.12+ is expected: sum() over floats changed in 3.12 and pack_quat uses it.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import random
import struct
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEADER_COPY = os.path.join(ROOT, "src", "v2", "coop_proto_v2.h")
proto = None
snapshot = None


def load_relay(relay_dir: str) -> None:
    global proto, snapshot
    if not os.path.isfile(os.path.join(relay_dir, "coopnet", "proto.py")):
        raise SystemExit(f"relay checkout not found at {relay_dir} (use --relay or COOPNET_RELAY_DIR)")
    sys.path.insert(0, relay_dir)
    from coopnet import proto as proto_module  # noqa: E402
    from coopnet import snapshot as snapshot_module  # noqa: E402
    proto = proto_module
    snapshot = snapshot_module


def default_relay() -> str:
    return os.environ.get("COOPNET_RELAY_DIR") or os.path.join(os.path.dirname(ROOT), "relay")


# ---------------------------------------------------------------------------
# canonical description (must match src/v2/V2Describe.cpp character for character)
# ---------------------------------------------------------------------------

def f32_bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def fmt(value, is_float: bool) -> str:
    return f"f:{f32_bits(value):08x}" if is_float else str(int(value))


def describe_fixed(codec, values: dict, prefix: str = "") -> list:
    parts = []
    for field in codec.fields:
        parts.append(f"{prefix}{field}={fmt(values[field], field in codec.float_fields)}")
    if codec.text is not None:
        parts.append(f"{prefix}{codec.text}={values[codec.text].encode('utf-8').hex()}")
    return parts


def describe_record(record: dict) -> str:
    token = f"rec:{record['net_id']}"
    if record.get("remove"):
        return token + ":remove"
    if "spawn" in record:
        spawn = record["spawn"]
        token += (f":spawn={spawn['kind']},{spawn['spawn_flags']},{spawn['attitude']},{spawn['record']},"
                  f"{spawn['appearance']}")
    for key in ("pos", "pos_delta", "yaw", "quat", "vel", "state", "target", "weapon", "world_id"):
        if key in record:
            value = record[key]
            text = ",".join(str(v) for v in value) if isinstance(value, (tuple, list)) else str(value)
            token += f":{key}={text}"
    return token


def describe_body(mtype: int, values: dict) -> str:
    if mtype == proto.MsgType.PLAYER_SNAPSHOT:
        parts = ["PLAYER_SNAPSHOT"] + describe_fixed(proto.PLAYER_BASE, values)
        if values["vehicle"] is None:
            parts.append("vehicle=none")
        else:
            parts += describe_fixed(proto.VEHICLE_BLOCK, values["vehicle"], "vehicle.")
        return " ".join(parts)
    if mtype == proto.MsgType.ENTITY_SNAPSHOT:
        parts = ["ENTITY_SNAPSHOT", f"tick={values['tick']}", f"baseline={values['baseline']}",
                 f"sample_time={values['sample_time']}", f"count={len(values['records'])}"]
        parts += [describe_record(record) for record in values["records"]]
        return " ".join(parts)
    codec = proto.SPECS[mtype].codec
    return " ".join([codec.name] + describe_fixed(codec, values))


def describe_join(info: dict) -> str:
    parts = [f"{field}={int(info[field])}" for field in proto.JOIN_FIELDS]
    parts.append(f"room={info['room'].encode('utf-8').hex()}")
    parts.append(f"name={info['name'].encode('utf-8').hex()}")
    return " ".join(parts)


def full_decode(data: bytes) -> list:
    """Every layer of a datagram, described. Raises proto.ProtocolError where proto.py rejects."""
    ptype, token, seq, ack, bits, body = proto.decode_packet(data)
    lines = [f"packet type={ptype} token={token:016x} seq={seq} ack={ack} ack_bits={bits:08x}"]
    if ptype == proto.PacketType.HELLO:
        info = proto.decode_hello(body)
        _, offset = proto.decode_join(body)
        lines.append(f"hello {describe_join(info)} padding={len(body) - offset}")
    elif ptype == proto.PacketType.CHALLENGE:
        proto.decode_challenge(body)
        minor_min, minor_max, reserved, cookie = proto.CHALLENGE_BODY.unpack(body)
        lines.append(f"challenge minor_min={minor_min} minor_max={minor_max} reserved={reserved} cookie={cookie.hex()}")
    elif ptype == proto.PacketType.AUTH:
        info, cookie, key_hash = proto.decode_auth(body)
        lines.append(f"auth {describe_join(info)} cookie={cookie.hex()} key_hash={key_hash.hex()}")
    elif ptype == proto.PacketType.WELCOME:
        values = proto.decode_welcome(body)
        lines.append("welcome " + " ".join(f"{field}={values[field]}" for field in proto.WELCOME_FIELDS))
    elif ptype == proto.PacketType.REJECT:
        reason, minor_min, minor_max, _ = proto.decode_reject(body)
        raw = body[proto.REJECT_FIXED.size:proto.REJECT_FIXED.size + body[3]]
        lines.append(f"reject reason={reason} minor_min={minor_min} minor_max={minor_max} text={raw.hex()}")
    elif ptype == proto.PacketType.DATA:
        for mtype, peer, rel_seq, payload in proto.decode_messages(body):
            values = proto.decode_body(mtype, payload)
            spec = proto.SPECS[mtype]
            delivery = "ok" if proto.delivery_ok(spec, rel_seq is not None, values) else "bad"
            rel = "-" if rel_seq is None else str(rel_seq)
            lines.append(f"msg type={mtype} peer={peer} rel={rel} delivery={delivery} "
                         f"minor={proto.required_minor(mtype, values)} {describe_body(mtype, values)}")
    elif ptype == proto.PacketType.DISCONNECT:
        lines.append(f"disconnect reason={proto.decode_disconnect(body)}")
    return lines


def python_reencode(data: bytes) -> bytes:
    """Encodes a decoded datagram again with proto.py's encoders."""
    ptype, token, seq, ack, bits, body = proto.decode_packet(data)
    if ptype == proto.PacketType.HELLO:
        return proto.encode_hello(proto.decode_hello(body))
    if ptype == proto.PacketType.AUTH:
        return proto.encode_auth(*proto.decode_auth(body))
    if ptype == proto.PacketType.CHALLENGE:
        minor_min, minor_max, reserved, cookie = proto.CHALLENGE_BODY.unpack(body)
        if (minor_min, minor_max, reserved) == (proto.MIN_SUPPORTED_MINOR, proto.PROTO_MINOR, 0):
            return proto.encode_challenge(cookie)
        return proto.encode_packet(proto.PacketType.CHALLENGE, body=proto.CHALLENGE_BODY.pack(
            minor_min, minor_max, reserved, cookie))
    if ptype == proto.PacketType.WELCOME:
        return proto.encode_welcome(proto.decode_welcome(body))
    if ptype == proto.PacketType.REJECT:
        reason, minor_min, minor_max, text = proto.decode_reject(body)
        raw = body[proto.REJECT_FIXED.size:proto.REJECT_FIXED.size + body[3]]
        if (minor_min, minor_max) == (proto.MIN_SUPPORTED_MINOR, proto.PROTO_MINOR) and \
                text.encode("utf-8") == raw and len(raw) <= 64:
            return proto.encode_reject(reason, text)
        return proto.encode_packet(proto.PacketType.REJECT,
                                   body=proto.REJECT_FIXED.pack(reason, minor_min, minor_max, len(raw)) + raw)
    if ptype == proto.PacketType.DISCONNECT:
        return proto.encode_disconnect(token, proto.decode_disconnect(body))
    payload = b"".join(proto.encode_message(mtype, peer, proto.encode_body(mtype, proto.decode_body(mtype, part)),
                                            rel_seq)
                       for mtype, peer, rel_seq, part in proto.decode_messages(body))
    return proto.encode_packet(ptype, token, seq, ack, bits, payload)


# ---------------------------------------------------------------------------
# random valid values (proto.py field names)
# ---------------------------------------------------------------------------

STRICT_POOLS = ((0x20, 0x7E, 70), (0x410, 0x44F, 12), (0x4E00, 0x4EFF, 10), (0x1F600, 0x1F64F, 8))
SCRIPT_CONTROLS = (0x01, 0x09, 0x0A, 0x0D, 0x1B, 0x1F, 0x7F, 0x85, 0x9F, 0x2028, 0x2029)
ROOM_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"


def rand_text(rng: random.Random, max_bytes: int, script: bool = False) -> str:
    target = rng.randint(0, max(0, max_bytes))
    chars = []
    size = 0
    for _ in range(4000):
        if size >= target:
            break
        if script and rng.random() < 0.1:
            code = rng.choice(SCRIPT_CONTROLS)
        else:
            pick = rng.random() * 100
            for low, high, weight in STRICT_POOLS:
                if pick < weight:
                    code = rng.randint(low, high)
                    break
                pick -= weight
            else:
                code = 0x41
        encoded = len(chr(code).encode("utf-8"))
        if size + encoded > max_bytes:
            continue
        chars.append(chr(code))
        size += encoded
    return "".join(chars)


def f32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def coordinate(rng: random.Random, limit: float) -> float:
    if rng.random() < 0.1:
        below = struct.unpack("<f", struct.pack("<I", f32_bits(limit) - 1))[0]
        return rng.choice([0.0, -0.0, limit, -limit, f32(1e-40), f32(-1e-40), below])
    return max(-limit, min(limit, f32(rng.uniform(-limit, limit))))


def u(rng, bits):
    return rng.getrandbits(bits)


def s(rng, bits):
    return rng.getrandbits(bits) - (1 << (bits - 1))


def rand_join(rng: random.Random) -> dict:
    return {"minor": u(rng, 8), "role": rng.randint(0, 3), "join_flags": u(rng, 16), "caps": u(rng, 32),
            "game_build": u(rng, 32), "mod_major": u(rng, 16), "mod_minor": u(rng, 16), "mod_patch": u(rng, 16),
            "mod_hash": u(rng, 64), "mod_count": u(rng, 16), "client_nonce": u(rng, 64),
            "resume_token": 0 if rng.random() < 0.5 else u(rng, 64),
            "room": "".join(rng.choice(ROOM_ALPHABET) for _ in range(rng.randint(1, 32))),
            "name": rand_text(rng, proto.MAX_NAME_BYTES)}


def rand_pos(rng: random.Random) -> tuple:
    def axis(limit):
        if rng.random() < 0.05:
            return limit if rng.random() < 0.5 else -limit
        return rng.randint(-limit, limit)
    return (axis(20000000), axis(20000000), axis(5000000))


def rand_record(rng: random.Random, net_id: int) -> dict:
    record = {"net_id": net_id}
    if rng.random() < 0.08:
        record["remove"] = True
        return record
    if rng.random() < 0.25:
        record["spawn"] = {"kind": rng.randint(1, 5), "spawn_flags": u(rng, 8), "attitude": u(rng, 8),
                           "record": u(rng, 64), "appearance": u(rng, 64)}
        record["pos"] = rand_pos(rng)
        if rng.random() < 0.5:
            record["yaw"] = u(rng, 16)
        else:
            record["quat"] = u(rng, 32)
    else:
        choice = rng.randrange(3)
        if choice == 1:
            record["pos"] = rand_pos(rng)
        elif choice == 2:
            record["pos_delta"] = (s(rng, 16), s(rng, 16), s(rng, 16))
        choice = rng.randrange(3)
        if choice == 1:
            record["yaw"] = u(rng, 16)
        elif choice == 2:
            record["quat"] = u(rng, 32)
    if rng.random() < 0.4:
        record["vel"] = (s(rng, 16), s(rng, 16), s(rng, 16))
    if rng.random() < 0.3:
        record["state"] = (rng.randrange(proto.MOVE_STATE_COUNT), u(rng, 8), u(rng, 8))
    if rng.random() < 0.15:
        record["target"] = u(rng, 16)
    if rng.random() < 0.1:
        record["weapon"] = u(rng, 64)
    if rng.random() < 0.15:
        record["world_id"] = u(rng, 64)
    if len(record) == 1:
        record["yaw"] = u(rng, 16)
    return record


def rand_entity(rng: random.Random, budget: int) -> dict:
    tick = rng.randint(1, 0xFFFFFFFF)
    baseline = rng.randint(1, tick - 1) if tick > 1 and rng.random() < 0.6 else 0
    many = rng.random() < 0.1
    wanted = rng.randint(100, 255) if many else rng.randrange(30)
    used = proto.ENTITY_HEADER.size
    records, ids = [], set()
    for _ in range(wanted * 3):
        if len(records) >= wanted:
            break
        net_id = len(records) + 1 if many else rng.randint(1, 0xFFFF)
        if net_id in ids:
            continue
        ids.add(net_id)
        record = {"net_id": net_id, "yaw": u(rng, 16)} if many else rand_record(rng, net_id)
        size = proto.EntitySnapshotCodec.record_size(record)
        if used + size > budget:
            break
        used += size
        records.append(record)
    return {"tick": tick, "baseline": baseline, "sample_time": u(rng, 32), "records": records}


def rand_vehicle(rng: random.Random) -> dict:
    return {"vehicle_net": rng.randint(1, 0xFFFF), "px": coordinate(rng, 20000.0), "py": coordinate(rng, 20000.0),
            "pz": coordinate(rng, 5000.0), "quat": u(rng, 32), "lvx": s(rng, 16), "lvy": s(rng, 16),
            "lvz": s(rng, 16), "avx": s(rng, 16), "avy": s(rng, 16), "avz": s(rng, 16),
            "steer": rng.randint(-100, 100), "throttle": rng.randint(0, 100), "brake": rng.randint(0, 100),
            "vflags": u(rng, 8)}


def rand_body(rng: random.Random, mtype: int, budget: int = 1174) -> dict:
    M = proto.MsgType
    if mtype == M.TIME_REQ:
        return {"t0": u(rng, 32)}
    if mtype == M.TIME_RESP:
        return {"t0": u(rng, 32), "t1": u(rng, 32), "t2": u(rng, 32)}
    if mtype == M.PEER_JOINED:
        return {"peer_id": u(rng, 8), "role": rng.randint(0, 3), "minor": u(rng, 8), "peer_flags": u(rng, 8),
                "caps": u(rng, 32), "mod_hash": u(rng, 64), "mod_count": u(rng, 16), "mod_major": u(rng, 16),
                "mod_minor": u(rng, 16), "mod_patch": u(rng, 16), "name": rand_text(rng, proto.MAX_NAME_BYTES)}
    if mtype == M.PEER_LEFT:
        return {"peer_id": u(rng, 8), "reason": u(rng, 8)}
    if mtype == M.LINK_STATS:
        return {"peer_id": u(rng, 8), "reserved": u(rng, 8), "rtt_ms": u(rng, 16), "loss_in_permille": u(rng, 16),
                "loss_out_permille": u(rng, 16)}
    if mtype == M.PLAYER_SNAPSHOT:
        values = {"snap_seq": u(rng, 16), "sample_time": u(rng, 32), "x": coordinate(rng, 20000.0),
                  "y": coordinate(rng, 20000.0), "z": coordinate(rng, 5000.0), "yaw": u(rng, 16),
                  "pitch": rng.randint(-9000, 9000), "vx": s(rng, 16), "vy": s(rng, 16), "vz": s(rng, 16),
                  "move_state": rng.randrange(proto.MOVE_STATE_COUNT), "health": u(rng, 8),
                  "flags": u(rng, 16) & ~int(proto.PlayerFlag.DRIVING), "vehicle": None}
        if budget >= 66 and rng.random() < 0.4:
            values["flags"] |= int(proto.PlayerFlag.DRIVING)
            values["vehicle"] = rand_vehicle(rng)
        return values
    if mtype == M.ENTITY_SNAPSHOT:
        return rand_entity(rng, min(budget, proto.MAX_MESSAGE_BODY))
    if mtype == M.SNAPSHOT_ACK:
        return {"tick": u(rng, 32)}
    if mtype == M.FIRE_FX:
        return {"time_ms": u(rng, 32), "ox": s(rng, 32), "oy": s(rng, 32), "oz": s(rng, 32), "dx": s(rng, 16),
                "dy": s(rng, 16), "dz": s(rng, 16), "weapon_class": u(rng, 8), "shots": u(rng, 8)}
    if mtype == M.EQUIP:
        return {"slot": rng.randrange(8), "weapon_class": rng.randrange(16), "flags": u(rng, 16),
                "item_record": u(rng, 64), "appearance": u(rng, 64), "ammo": u(rng, 16)}
    if mtype == M.VEHICLE_ENTER:
        return {"vehicle_net": u(rng, 16), "seat": u(rng, 8), "flags": u(rng, 8), "record": u(rng, 64),
                "appearance": u(rng, 64), "x": coordinate(rng, 20000.0), "y": coordinate(rng, 20000.0),
                "z": coordinate(rng, 5000.0), "quat": u(rng, 32)}
    if mtype == M.VEHICLE_EXIT:
        return {"vehicle_net": u(rng, 16), "seat": u(rng, 8), "flags": u(rng, 8), "x": coordinate(rng, 20000.0),
                "y": coordinate(rng, 20000.0), "z": coordinate(rng, 5000.0), "yaw": u(rng, 16)}
    if mtype == M.HIT:
        return {"target_net": rng.randint(1, 0xFFFF), "target_kind": rng.randrange(2), "hit_zone": rng.randrange(16),
                "attack": u(rng, 8), "flags": u(rng, 8),
                "damage": rng.choice([0.0, -0.0, 1.0e6, 33.5, f32(rng.uniform(0.0, 1.0e6))]),
                "weapon_record": u(rng, 64), "rx": s(rng, 16), "ry": s(rng, 16), "rz": s(rng, 16),
                "time_ms": u(rng, 32)}
    if mtype == M.DEATH:
        return {"target_net": u(rng, 16), "target_kind": rng.randrange(2), "cause": u(rng, 8), "killer_net": u(rng, 16),
                "killer_kind": rng.randrange(3), "flags": u(rng, 8), "time_ms": u(rng, 32)}
    if mtype == M.TIME_WEATHER:
        return {"game_seconds": u(rng, 32), "time_scale_x100": rng.randint(0, 10000), "flags": u(rng, 8),
                "weather_id": rng.randrange(32), "weather_record": u(rng, 64), "transition_s": u(rng, 16)}
    if mtype == M.CHAT:
        return {"channel": u(rng, 8), "text": rand_text(rng, min(proto.MAX_CHAT_BYTES, budget - 2))}
    if mtype == M.TELEPORT_REQ:
        return {"req_id": u(rng, 16), "mode": rng.randrange(2), "reserved": u(rng, 8)}
    if mtype == M.TELEPORT_RESP:
        return {"req_id": u(rng, 16), "accepted": u(rng, 8), "reserved": u(rng, 8), "x": coordinate(rng, 20000.0),
                "y": coordinate(rng, 20000.0), "z": coordinate(rng, 5000.0), "yaw": u(rng, 16)}
    if mtype == M.WORLD_FACT:
        return {"fact_hash": u(rng, 64), "value": s(rng, 32)}
    if mtype == M.MOD_LIST:
        chunks = rng.randint(1, 16)
        return {"chunk": rng.randrange(chunks), "chunks": chunks, "text": rand_text(rng, min(255, budget - 3))}
    if mtype == M.SESSION_CONFIG:
        return {"npc_radius_m": rng.randint(10, 500), "vehicle_radius_m": rng.randint(10, 1000),
                "entity_hz": rng.randint(1, 30), "joiner_population": u(rng, 8), "flags": u(rng, 16)}
    if mtype == M.SCRIPT_MSG:
        limit = min(proto.MAX_SCRIPT_BYTES, budget - 4)
        text = "x" * limit if rng.random() < 0.05 else rand_text(rng, limit, script=True)
        return {"channel": rng.randint(1, 31), "flags": 0 if rng.random() < 0.8 else u(rng, 8), "text": text}
    raise ValueError(mtype)


def reliable_for(mtype: int, values: dict) -> bool:
    spec = proto.SPECS[mtype]
    if spec.reliable is None:
        return proto.script_channel_reliable(values["channel"])
    return spec.reliable


def data_packet(messages, token=0, seq=0, ack=0, bits=0) -> bytes:
    payload = b"".join(proto.encode_message(mtype, peer, body, rel_seq) for mtype, peer, rel_seq, body in messages)
    return proto.encode_packet(proto.PacketType.DATA, token, seq, ack, bits, payload)


def rand_data_packet(rng: random.Random) -> bytes:
    many = rng.random() < 0.04
    wanted = proto.MAX_MESSAGES_PER_PACKET if many else rng.randrange(7)
    messages, used = [], 0
    types = list(proto.SPECS)
    for _ in range(wanted):
        mtype = proto.MsgType.SNAPSHOT_ACK if many else rng.choice(types)
        room = proto.MAX_PACKET - proto.PACKET_HEADER.size - used
        if room <= 6:
            break
        values = rand_body(rng, mtype, room - 6)
        body = proto.encode_body(mtype, values)
        reliable = reliable_for(mtype, values)
        if rng.random() < 0.05:
            reliable = not reliable
        size = proto.message_size(len(body), reliable)
        if size > room:
            break
        messages.append((mtype, u(rng, 8), u(rng, 16) if reliable else None, body))
        used += size
    return data_packet(messages, u(rng, 64), u(rng, 16), u(rng, 16), u(rng, 32))


def rand_handshake(rng: random.Random, pick: int) -> bytes:
    if pick < 8:
        return proto.encode_hello(rand_join(rng))
    if pick < 12:
        return proto.encode_auth(rand_join(rng), rng.randbytes(16), rng.randbytes(16))
    if pick < 14:
        return proto.encode_challenge(rng.randbytes(16))
    if pick < 16:
        return proto.encode_packet(proto.PacketType.CHALLENGE, body=proto.CHALLENGE_BODY.pack(
            u(rng, 8), u(rng, 8), u(rng, 16), rng.randbytes(16)))
    if pick < 20:
        return proto.encode_welcome({"minor": u(rng, 8), "peer_id": u(rng, 8), "role": u(rng, 8),
                                     "room_flags": u(rng, 8), "token": u(rng, 64), "relay_time_ms": u(rng, 32),
                                     "player_hz": u(rng, 8), "entity_hz": u(rng, 8), "max_packet": u(rng, 16),
                                     "room_caps": u(rng, 32)})
    if pick < 24:
        return proto.encode_reject(u(rng, 8), rand_text(rng, 64))
    return proto.encode_disconnect(u(rng, 64), u(rng, 8))


def rand_datagram(rng: random.Random) -> bytes:
    pick = rng.randrange(100)
    return rand_handshake(rng, pick) if pick < 28 else rand_data_packet(rng)


def mutate(rng: random.Random, data: bytes) -> bytes:
    data = bytearray(data)
    for _ in range(rng.randint(1, 4)):
        op = rng.randrange(10)
        if not data:
            data.append(u(rng, 8))
            continue
        at = rng.randrange(len(data))
        if op == 0:
            data[at] ^= 1 << rng.randrange(8)
        elif op == 1:
            data[at] = u(rng, 8)
        elif op == 2:
            del data[at:]
        elif op == 3:
            data += rng.randbytes(rng.randint(1, 16))
        elif op == 4:
            data[at:at] = bytes([u(rng, 8)]) * rng.randint(1, 8)
        elif op == 5:
            del data[at:at + rng.randint(1, 16)]
        elif op == 6:
            data[at] = rng.choice((0x00, 0x01, 0x7F, 0x80, 0xFE, 0xFF))
        elif op == 7:
            if len(data) >= 24:
                data[20 + rng.randrange(4)] = u(rng, 8)
        elif op == 8:
            if len(data) >= 4:
                slot = rng.randrange(len(data) - 3)
                data[slot:slot + 4] = struct.pack("<I", rng.choice(
                    (0x7FC00000, 0x7F800000, 0xFF800000, 0x46A00000, 0x469C4000)))
        elif len(data) > 4:
            data[2] = 2 if rng.random() < 0.5 else rng.randrange(4)
            data[3] = rng.randrange(9)
    return bytes(data[:1400])


# ---------------------------------------------------------------------------
# handcrafted datagrams proto.py must reject
# ---------------------------------------------------------------------------

def raw_message(mtype: int, body: bytes, reliable: bool | None = None) -> bytes:
    if reliable is None:
        spec = proto.SPECS.get(mtype)
        reliable = bool(spec and spec.reliable)
    return data_packet([(mtype, 0xFF, 1 if reliable else None, body)])


def invalid_vectors() -> list:
    M = proto.MsgType
    P = proto.PacketType
    join = {"minor": 1, "role": 1, "join_flags": 0, "caps": proto.ALL_CAPS, "game_build": proto.game_build_id("2.31a"),
            "mod_major": 0, "mod_minor": 2, "mod_patch": 0, "mod_hash": 1, "mod_count": 1, "client_nonce": 42,
            "resume_token": 0, "room": "night-city", "name": "V"}
    join_body = proto.encode_join(join)

    def join_bytes(room: bytes, name: bytes, role: int = 1) -> bytes:
        fixed = proto.JOIN_FIXED.pack(*(role if field == "role" else join[field] for field in proto.JOIN_FIELDS))
        return fixed + bytes([len(room)]) + room + bytes([len(name)]) + name

    def hello_body(body: bytes) -> bytes:
        return body + bytes(max(0, proto.HELLO_MIN_PACKET - 20 - len(body)))

    player = proto.PlayerSnapshotCodec().encode({
        "snap_seq": 1, "sample_time": 0, "x": 1.0, "y": 2.0, "z": 3.0, "yaw": 0, "pitch": 0, "vx": 0, "vy": 0,
        "vz": 0, "move_state": 0, "health": 255, "flags": 0, "vehicle": None})
    vehicle = {"vehicle_net": 1, "px": 0.0, "py": 0.0, "pz": 0.0, "quat": 0, "lvx": 0, "lvy": 0, "lvz": 0,
               "avx": 0, "avy": 0, "avz": 0, "steer": 0, "throttle": 0, "brake": 0, "vflags": 0}

    def player_with(**changes) -> bytes:
        values = dict(zip(proto.PLAYER_BASE.fields, proto.PLAYER_BASE.struct.unpack(player)))
        values.update(changes)
        return proto.PLAYER_BASE.struct.pack(*(values[field] for field in proto.PLAYER_BASE.fields))

    def vehicle_with(**changes) -> bytes:
        values = dict(vehicle, **changes)
        base = player_with(flags=int(proto.PlayerFlag.DRIVING))
        return base + proto.VEHICLE_BLOCK.struct.pack(*(values[field] for field in proto.VEHICLE_BLOCK.fields))

    def fixed(codec, **values) -> bytes:
        return codec.struct.pack(*(values[field] for field in codec.fields))

    hit = dict(target_net=1, target_kind=0, hit_zone=0, attack=0, flags=0, damage=1.0, weapon_record=0, rx=0,
               ry=0, rz=0, time_ms=0)
    weather = dict(game_seconds=0, time_scale_x100=100, flags=0, weather_id=0, weather_record=0, transition_s=0)
    session = dict(npc_radius_m=100, vehicle_radius_m=200, entity_hz=10, joiner_population=0, flags=0)

    def entity(records: bytes, count: int, tick: int = 2, baseline: int = 0, extra: bytes = b"") -> bytes:
        return proto.ENTITY_HEADER.pack(tick, baseline, 0, count) + records + extra

    def rec(net_id: int, mask: int, ext: int | None = None, payload: bytes = b"") -> bytes:
        return struct.pack("<HB", net_id, mask) + (bytes([ext]) if ext is not None else b"") + payload

    spawn = proto.SPAWN_BLOCK.pack(1, 0, 0, 0, 1, 1)
    pos = proto.POS_BLOCK.pack(0, 0, 0)
    cases = [
        ("empty", b""),
        ("three-bytes", b"\xcb\x77\x02"),
        ("bad-magic", b"XX" + bytes(30)),
        ("unknown-packet-type", proto.MAGIC + bytes([2, 99]) + bytes(16)),
        ("packet-type-zero", proto.MAGIC + bytes([2, 0]) + bytes(16)),
        ("too-large", proto.MAGIC + bytes([2, 6]) + bytes(1197)),
        ("future-major", proto.MAGIC + bytes([3, 1]) + bytes(40)),
        ("truncated-header", proto.MAGIC + bytes([2, 6]) + bytes(15)),
        ("hello-unpadded", proto.encode_packet(P.HELLO, body=join_body)),
        ("hello-dirty-padding", proto.encode_packet(P.HELLO, body=hello_body(join_body)[:-1] + b"\x01")),
        ("hello-bad-role", proto.encode_packet(P.HELLO, body=hello_body(join_bytes(b"night-city", b"V", role=4)))),
        ("hello-room-space", proto.encode_packet(P.HELLO, body=hello_body(join_bytes(b"bad room", b"V")))),
        ("hello-room-empty", proto.encode_packet(P.HELLO, body=hello_body(join_bytes(b"", b"V")))),
        ("hello-room-33", proto.encode_packet(P.HELLO, body=hello_body(join_bytes(b"a" * 33, b"V")))),
        ("hello-room-cyrillic", proto.encode_packet(P.HELLO, body=hello_body(join_bytes("ком".encode(), b"V")))),
        ("hello-name-escape", proto.encode_packet(P.HELLO, body=hello_body(join_bytes(b"r", b"evil\x1b[31m")))),
        ("hello-name-25", proto.encode_packet(P.HELLO, body=hello_body(join_bytes(b"r", b"n" * 25)))),
        ("hello-name-bad-utf8", proto.encode_packet(P.HELLO, body=hello_body(join_bytes(b"r", b"\xc3\x28")))),
        ("hello-name-truncated", proto.encode_packet(P.HELLO, body=join_bytes(b"r", b"V")[:-1])),
        ("auth-short-trailer", proto.encode_packet(P.AUTH, body=join_body + bytes(31))),
        ("auth-long-trailer", proto.encode_packet(P.AUTH, body=join_body + bytes(33))),
        ("challenge-19", proto.encode_packet(P.CHALLENGE, body=bytes(19))),
        ("challenge-21", proto.encode_packet(P.CHALLENGE, body=bytes(21))),
        ("welcome-23", proto.encode_packet(P.WELCOME, body=bytes(23))),
        ("welcome-25", proto.encode_packet(P.WELCOME, body=bytes(25))),
        ("reject-3", proto.encode_packet(P.REJECT, body=bytes(3))),
        ("disconnect-0", proto.encode_packet(P.DISCONNECT)),
        ("disconnect-2", proto.encode_packet(P.DISCONNECT, body=b"\x01\x02")),
        ("frame-truncated-header", proto.encode_packet(P.DATA, body=b"\x12\xff\x04")),
        ("frame-reliable-no-seq", proto.encode_packet(P.DATA, body=b"\xa0\xff\x00\x00\x01")),
        ("frame-length-beyond", proto.encode_packet(P.DATA, body=b"\x12\xff\x05\x00" + bytes(4))),
        ("frame-97-messages", proto.encode_packet(P.DATA, body=(b"\x12\xff\x04\x00" + bytes(4)) * 97)),
        ("unknown-message-type", raw_message(0x7E, b"")),
        ("time-req-5", raw_message(M.TIME_REQ, bytes(5))),
        ("time-req-3", raw_message(M.TIME_REQ, bytes(3))),
        ("player-nan", raw_message(M.PLAYER_SNAPSHOT, player_with(x=float("nan")))),
        ("player-inf", raw_message(M.PLAYER_SNAPSHOT, player_with(y=float("inf")))),
        ("player-x-20001", raw_message(M.PLAYER_SNAPSHOT, player_with(x=20001.0))),
        ("player-z-5001", raw_message(M.PLAYER_SNAPSHOT, player_with(z=-5001.0))),
        ("player-move-14", raw_message(M.PLAYER_SNAPSHOT, player_with(move_state=14))),
        ("player-pitch-9001", raw_message(M.PLAYER_SNAPSHOT, player_with(pitch=9001))),
        ("player-pitch--9001", raw_message(M.PLAYER_SNAPSHOT, player_with(pitch=-9001))),
        ("player-driving-no-block", raw_message(M.PLAYER_SNAPSHOT, player_with(flags=int(proto.PlayerFlag.DRIVING)))),
        ("player-block-not-driving", raw_message(M.PLAYER_SNAPSHOT, player + bytes(34))),
        ("vehicle-net-0", raw_message(M.PLAYER_SNAPSHOT, vehicle_with(vehicle_net=0))),
        ("vehicle-steer-101", raw_message(M.PLAYER_SNAPSHOT, vehicle_with(steer=101))),
        ("vehicle-steer--101", raw_message(M.PLAYER_SNAPSHOT, vehicle_with(steer=-101))),
        ("vehicle-throttle-101", raw_message(M.PLAYER_SNAPSHOT, vehicle_with(throttle=101))),
        ("vehicle-brake-101", raw_message(M.PLAYER_SNAPSHOT, vehicle_with(brake=101))),
        ("vehicle-pz-nan", raw_message(M.PLAYER_SNAPSHOT, vehicle_with(pz=float("nan")))),
        ("hit-kind-2", raw_message(M.HIT, fixed(proto.HIT, **dict(hit, target_kind=2)))),
        ("hit-damage-negative", raw_message(M.HIT, fixed(proto.HIT, **dict(hit, damage=-1.0)))),
        ("hit-damage-huge", raw_message(M.HIT, fixed(proto.HIT, **dict(hit, damage=1.5e6)))),
        ("hit-damage-nan", raw_message(M.HIT, fixed(proto.HIT, **dict(hit, damage=float("nan"))))),
        ("hit-zone-16", raw_message(M.HIT, fixed(proto.HIT, **dict(hit, hit_zone=16)))),
        ("hit-target-0", raw_message(M.HIT, fixed(proto.HIT, **dict(hit, target_net=0)))),
        ("death-killer-3", raw_message(M.DEATH, struct.pack("<HBBHBBI", 1, 0, 0, 0, 3, 0, 0))),
        ("death-target-kind-2", raw_message(M.DEATH, struct.pack("<HBBHBBI", 1, 2, 0, 0, 0, 0, 0))),
        ("weather-32", raw_message(M.TIME_WEATHER, fixed(proto.TIME_WEATHER, **dict(weather, weather_id=32)))),
        ("weather-scale", raw_message(M.TIME_WEATHER, fixed(proto.TIME_WEATHER, **dict(weather, time_scale_x100=10001)))),
        ("teleport-mode-2", raw_message(M.TELEPORT_REQ, struct.pack("<HBB", 1, 2, 0))),
        ("teleport-resp-z", raw_message(M.TELEPORT_RESP, struct.pack("<HBBfffH", 1, 1, 0, 0.0, 0.0, 5001.0, 0))),
        ("vehicle-enter-inf", raw_message(M.VEHICLE_ENTER, struct.pack("<HBBQQfffI", 1, 0, 0, 1, 2, float("inf"), 0, 0, 0))),
        ("vehicle-exit-far", raw_message(M.VEHICLE_EXIT, struct.pack("<HBBfffH", 1, 0, 0, 0.0, 20001.0, 0.0, 0))),
        ("equip-slot-8", raw_message(M.EQUIP, struct.pack("<BBHQQH", 8, 1, 0, 1, 1, 1))),
        ("equip-class-16", raw_message(M.EQUIP, struct.pack("<BBHQQH", 0, 16, 0, 1, 1, 1))),
        ("session-npc-9", raw_message(M.SESSION_CONFIG, fixed(proto.SESSION_CONFIG, **dict(session, npc_radius_m=9)))),
        ("session-npc-501", raw_message(M.SESSION_CONFIG, fixed(proto.SESSION_CONFIG, **dict(session, npc_radius_m=501)))),
        ("session-vehicle-1001", raw_message(M.SESSION_CONFIG, fixed(proto.SESSION_CONFIG, **dict(session, vehicle_radius_m=1001)))),
        ("session-hz-0", raw_message(M.SESSION_CONFIG, fixed(proto.SESSION_CONFIG, **dict(session, entity_hz=0)))),
        ("session-hz-31", raw_message(M.SESSION_CONFIG, fixed(proto.SESSION_CONFIG, **dict(session, entity_hz=31)))),
        ("modlist-chunk-eq", raw_message(M.MOD_LIST, b"\x02\x02\x03a@1")),
        ("modlist-chunks-17", raw_message(M.MOD_LIST, b"\x00\x11\x03a@1")),
        ("modlist-trailing", raw_message(M.MOD_LIST, b"\x00\x01\xff" + b"m" * 256)),
        ("chat-201", raw_message(M.CHAT, b"\x00" + bytes([201]) + b"x" * 201)),
        ("chat-newline", raw_message(M.CHAT, b"\x00\x0aline\nbreak")),
        ("chat-bell", raw_message(M.CHAT, b"\x00\x05bell\x07")),
        ("chat-bad-utf8", raw_message(M.CHAT, b"\x00\x02o\xff")),
        ("chat-overlong", raw_message(M.CHAT, b"\x00\x02\xc0\xaf")),
        ("chat-surrogate", raw_message(M.CHAT, b"\x00\x03\xed\xa0\x80")),
        ("chat-u2028", raw_message(M.CHAT, b"\x00\x03\xe2\x80\xa8")),
        ("chat-u0085", raw_message(M.CHAT, b"\x00\x02\xc2\x85")),
        ("chat-missing-length", raw_message(M.CHAT, b"\x00")),
        ("chat-truncated-text", raw_message(M.CHAT, b"\x00\x05abc")),
        ("script-1001", raw_message(M.SCRIPT_MSG, b"\x10\x00" + struct.pack("<H", 1001) + b"x" * 1001, True)),
        ("script-nul", raw_message(M.SCRIPT_MSG, b"\x10\x00\x03\x00a\x00b", True)),
        ("script-channel-0", raw_message(M.SCRIPT_MSG, b"\x00\x00\x01\x00a", False)),
        ("script-channel-32", raw_message(M.SCRIPT_MSG, b"\x20\x00\x01\x00a", True)),
        ("script-bad-utf8", raw_message(M.SCRIPT_MSG, b"\x01\x00\x02\x00\xc3\x28", False)),
        ("script-above-u10ffff", raw_message(M.SCRIPT_MSG, b"\x01\x00\x04\x00\xf4\x90\x80\x80", False)),
        ("script-length-1-byte", raw_message(M.SCRIPT_MSG, b"\x01\x00\x01", False)),
        ("script-truncated-text", raw_message(M.SCRIPT_MSG, b"\x01\x00\x05\x00abc", False)),
        ("script-trailing", raw_message(M.SCRIPT_MSG, b"\x01\x00\x01\x00ab", False)),
        ("peer-joined-role-4", raw_message(M.PEER_JOINED, struct.pack("<BBBBIQHHHH", 2, 4, 0, 0, 0, 0, 0, 0, 0, 0) + b"\x01j")),
        ("peer-joined-name-25", raw_message(M.PEER_JOINED, struct.pack("<BBBBIQHHHH", 2, 1, 0, 0, 0, 0, 0, 0, 0, 0) + b"\x19" + b"n" * 25)),
        ("entity-net-id-0", raw_message(M.ENTITY_SNAPSHOT, entity(rec(0, proto.M_YAW, payload=b"\x01\x00"), 1))),
        ("entity-pos-and-delta", raw_message(M.ENTITY_SNAPSHOT, entity(rec(1, proto.M_POS | proto.M_POS_DELTA, payload=pos + bytes(6)), 1))),
        ("entity-yaw-and-quat", raw_message(M.ENTITY_SNAPSHOT, entity(rec(1, proto.M_YAW | proto.M_QUAT, payload=bytes(6)), 1))),
        ("entity-spawn-no-pos", raw_message(M.ENTITY_SNAPSHOT, entity(rec(1, proto.M_SPAWN | proto.M_YAW, payload=spawn + bytes(2)), 1))),
        ("entity-spawn-no-rotation", raw_message(M.ENTITY_SNAPSHOT, entity(rec(1, proto.M_SPAWN | proto.M_POS, payload=spawn + pos), 1))),
        ("entity-kind-0", raw_message(M.ENTITY_SNAPSHOT, entity(rec(1, proto.M_SPAWN | proto.M_POS | proto.M_YAW, payload=proto.SPAWN_BLOCK.pack(0, 0, 0, 0, 1, 1) + pos + bytes(2)), 1))),
        ("entity-kind-6", raw_message(M.ENTITY_SNAPSHOT, entity(rec(1, proto.M_SPAWN | proto.M_POS | proto.M_YAW, payload=proto.SPAWN_BLOCK.pack(6, 0, 0, 0, 1, 1) + pos + bytes(2)), 1))),
        ("entity-x-out", raw_message(M.ENTITY_SNAPSHOT, entity(rec(1, proto.M_POS, payload=proto.POS_BLOCK.pack(20000001, 0, 0)), 1))),
        ("entity-z-out", raw_message(M.ENTITY_SNAPSHOT, entity(rec(1, proto.M_POS, payload=proto.POS_BLOCK.pack(0, 0, -5000001)), 1))),
        ("entity-move-14", raw_message(M.ENTITY_SNAPSHOT, entity(rec(1, proto.M_STATE, payload=b"\x0e\x00\x00"), 1))),
        ("entity-empty-record", raw_message(M.ENTITY_SNAPSHOT, entity(rec(1, 0), 1))),
        ("entity-ext-zero", raw_message(M.ENTITY_SNAPSHOT, entity(rec(1, proto.M_EXT, 0), 1))),
        ("entity-ext-unknown", raw_message(M.ENTITY_SNAPSHOT, entity(rec(1, proto.M_EXT, 0x10, bytes(8)), 1))),
        ("entity-remove-with-field", raw_message(M.ENTITY_SNAPSHOT, entity(rec(1, proto.M_EXT | proto.M_YAW, proto.X_REMOVE, bytes(2)), 1))),
        ("entity-remove-with-world-id", raw_message(M.ENTITY_SNAPSHOT, entity(rec(1, proto.M_EXT, proto.X_REMOVE | proto.X_WORLD_ID, bytes(8)), 1))),
        ("entity-world-id-truncated", raw_message(M.ENTITY_SNAPSHOT, entity(rec(1, proto.M_EXT, proto.X_WORLD_ID, bytes(7)), 1))),
        ("entity-duplicate", raw_message(M.ENTITY_SNAPSHOT, entity(rec(5, proto.M_YAW, payload=b"\x01\x00") * 2, 2))),
        ("entity-baseline-equal", raw_message(M.ENTITY_SNAPSHOT, entity(b"", 0, tick=2, baseline=2))),
        ("entity-baseline-later", raw_message(M.ENTITY_SNAPSHOT, entity(b"", 0, tick=2, baseline=3))),
        ("entity-count-256", raw_message(M.ENTITY_SNAPSHOT, entity(b"", 256))),
        ("entity-count-short", raw_message(M.ENTITY_SNAPSHOT, entity(rec(1, proto.M_YAW, payload=b"\x01\x00"), 2))),
        ("entity-trailing", raw_message(M.ENTITY_SNAPSHOT, entity(rec(1, proto.M_YAW, payload=b"\x01\x00"), 1, extra=b"\x00"))),
        ("entity-truncated-record", raw_message(M.ENTITY_SNAPSHOT, entity(rec(1, proto.M_VEL, payload=bytes(5)), 1))),
        ("entity-truncated-header", raw_message(M.ENTITY_SNAPSHOT, bytes(13))),
    ]
    return cases


def handcrafted_valid() -> list:
    """Edge cases proto.py must accept (canonical: produced by proto.py's encoders)."""
    M = proto.MsgType
    cases = []
    acks = [(M.SNAPSHOT_ACK, 0xFF, None, proto.SNAPSHOT_ACK.encode({"tick": index})) for index in range(96)]
    cases.append(("data-96-messages", data_packet(acks, 1, 2, 3, 4)))
    cases.append(("data-empty", data_packet([], 0xFFFFFFFFFFFFFFFF, 0xFFFF, 0xFFFF, 0xFFFFFFFF)))
    script = proto.SCRIPT_MSG.encode({"channel": 31, "flags": 0, "text": "y" * 1000})
    cases.append(("script-1000", data_packet([(M.SCRIPT_MSG, 2, 65535, script)])))
    script = proto.SCRIPT_MSG.encode({"channel": 1, "flags": 0xFF, "text": "evt\t{\"a\":1}\n\x1b\x7f ж"})
    cases.append(("script-controls", data_packet([(M.SCRIPT_MSG, 0xFF, None, script)])))
    script = proto.SCRIPT_MSG.encode({"channel": 16, "flags": 0, "text": ""})
    cases.append(("script-empty-on-reliable-sent-unreliable", data_packet([(M.SCRIPT_MSG, 1, None, script)])))
    crowd = proto.EntitySnapshotCodec().encode({"tick": 0xFFFFFFFF, "baseline": 0xFFFFFFFE, "sample_time": 7, "records": [
        {"net_id": net_id, "yaw": net_id} for net_id in range(1, 231)]})
    cases.append(("entity-230-records", data_packet([(M.ENTITY_SNAPSHOT, 0xFF, None, crowd)])))
    edges = proto.EntitySnapshotCodec().encode({"tick": 2, "baseline": 1, "sample_time": 0, "records": [
        {"net_id": 0xFFFF, "spawn": {"kind": 5, "spawn_flags": 255, "attitude": 255, "record": 2**64 - 1,
                                     "appearance": 2**64 - 1},
         "pos": (20000000, -20000000, 5000000), "quat": 2**32 - 1, "vel": (-32768, 32767, 0), "state": (13, 255, 0),
         "target": 0xFFFF, "weapon": 2**64 - 1, "world_id": 2**64 - 1},
        {"net_id": 1, "world_id": 0},
        {"net_id": 2, "pos_delta": (-32768, 32767, 0), "target": 0},
        {"net_id": 3, "remove": True}]})
    cases.append(("entity-edges", data_packet([(M.ENTITY_SNAPSHOT, 0xFF, None, edges)])))
    player = proto.PlayerSnapshotCodec().encode({
        "snap_seq": 65535, "sample_time": 2**32 - 1, "x": 20000.0, "y": -20000.0, "z": -5000.0, "yaw": 65535,
        "pitch": -9000, "vx": -32768, "vy": 32767, "vz": 0, "move_state": 13, "health": 0,
        "flags": 0xFFFF, "vehicle": {"vehicle_net": 65535, "px": -0.0, "py": f32(1e-40), "pz": 5000.0,
                                     "quat": 0, "lvx": 0, "lvy": 0, "lvz": 0, "avx": 0, "avy": 0, "avz": 0,
                                     "steer": -100, "throttle": 100, "brake": 100, "vflags": 255}})
    cases.append(("player-edges", data_packet([(M.PLAYER_SNAPSHOT, 0xFF, None, player)])))
    hit = proto.HIT.encode({"target_net": 0xFFFF, "target_kind": 1, "hit_zone": 15, "attack": 255, "flags": 255,
                            "damage": -0.0, "weapon_record": 0, "rx": 0, "ry": 0, "rz": 0, "time_ms": 0})
    cases.append(("hit-negative-zero", data_packet([(M.HIT, 0xFF, 7, hit)])))
    chat = proto.CHAT.encode({"channel": 255, "text": "привет, choom 🙂" + "x" * 176})
    cases.append(("chat-200-bytes-mixed", data_packet([(M.CHAT, 0xFF, 0, chat)])))
    join = {"minor": 1, "role": 2, "join_flags": 3, "caps": proto.ALL_CAPS, "game_build": proto.game_build_id("2.31a"),
            "mod_major": 0, "mod_minor": 2, "mod_patch": 0, "mod_hash": 0x1122334455667788, "mod_count": 5,
            "client_nonce": 42, "resume_token": 7, "room": "night-city_1", "name": "V Сильверхенд"}
    cases.append(("hello-sample", proto.encode_hello(join)))
    cases.append(("hello-long-name", proto.encode_hello(dict(join, room="a" * 32, name="ж" * 12))))
    cases.append(("auth-sample", proto.encode_auth(join, bytes(range(16)), proto.room_key_hash("night-city_1", "pw"))))
    cases.append(("challenge-sample", proto.encode_challenge(bytes(range(16)))))
    cases.append(("reject-version", proto.encode_reject(proto.RejectReason.VERSION, "relay speaks v2.1")))
    cases.append(("reject-long-text", proto.encode_reject(proto.RejectReason.BAD_KEY, "x" * 200)))
    cases.append(("disconnect-kicked", proto.encode_disconnect(2**63 + 5, proto.DisconnectReason.KICKED)))
    return cases


# ---------------------------------------------------------------------------
# hash and quantizer vectors
# ---------------------------------------------------------------------------

def hex_or_dash(data: bytes) -> str:
    return data.hex() if data else "-"


def from_hex(text: str) -> bytes:
    return b"" if text == "-" else bytes.fromhex(text)


def hash_lines(rng: random.Random) -> list:
    lines = []
    for index in range(40):
        size = (0, 1, 3, 55, 56, 63, 64, 65, 119, 120, 127, 128, 200, 1000)[index % 14]
        data = rng.randbytes(size)
        key = rng.randbytes(rng.randrange(140))
        lines.append(f"HASH sha256 {hex_or_dash(data)} {hashlib.sha256(data).hexdigest()}")
        lines.append(f"HASH hmac {hex_or_dash(key)} {hex_or_dash(data)} {hmac.new(key, data, hashlib.sha256).hexdigest()}")
    for version in ("2.31a", "2.31", "", "2.2", "1.63_hotfix", "Сборка 7"):
        raw = version.encode("utf-8")
        lines.append(f"HASH build {hex_or_dash(raw)} {proto.game_build_id(version)}")
    for _ in range(20):
        room = "".join(rng.choice(ROOM_ALPHABET) for _ in range(rng.randint(1, 32)))
        password = rand_text(rng, 40, script=True)
        lines.append(f"HASH room {room.encode().hex()} {hex_or_dash(password.encode('utf-8'))} "
                     f"{proto.room_key_hash(room, password).hex()}")
    names = ("Codeware", "  redscript", "TweakXL\t", "ArchiveXL", "cet", "RED4ext\x1c", "CP2077 Coop", "a b", "Z",
             "\x1fspaced\x0b")
    for _ in range(20):
        entries = [(rng.choice(names), f"{rng.randrange(3)}.{rng.randrange(30)}" + (" " if rng.random() < 0.3 else ""))
                   for _ in range(rng.randrange(6))]
        parts = " ".join(f"{name.encode().hex()} {version.encode().hex()}" for name, version in entries)
        lines.append(f"HASH mods {len(entries)} {parts} {proto.mod_list_hash(entries)}".replace("  ", " "))
    hosts = ("203.0.113.5", "127.0.0.1", "::1", "2001:db8::42", "198.51.100.200")
    for _ in range(20):
        secret = rng.randbytes(32)
        host = rng.choice(hosts)
        port = rng.randint(1, 65535)
        nonce = u(rng, 64)
        issued = 0xFFFFFFF0 + rng.randrange(16) if rng.random() < 0.2 else u(rng, 32)
        cookie = proto.make_cookie(secret, (host, port), nonce, issued)
        lines.append(f"HASH cookie {secret.hex()} {host.encode().hex()} {port} {nonce} {issued} {cookie.hex()}")
        now = (issued + rng.randint(-3, 14)) & 0xFFFFFFFF
        presented = bytearray(cookie)
        if rng.random() < 0.2:
            presented[rng.randrange(16)] ^= 1
        verdict = proto.check_cookie(secret, (host, port), nonce, bytes(presented), now)
        lines.append(f"HASH cookiecheck {secret.hex()} {host.encode().hex()} {port} {nonce} {bytes(presented).hex()} "
                     f"{now} {1 if verdict else 0}")
    return lines


def quant_lines(rng: random.Random) -> list:
    lines = []
    specials = [0.0, -0.0, 0.0005, 0.0015, 0.0025, -0.0025, 1450.0005, -1450.0005, 1e12, -1e12, 2147483.6475,
                360.0, -360.0, 720.0, -1e-20, 1e-20, 359.99999, 89.995, 90.005, -90.005, 327.675, -327.685]
    for value in specials:
        lines.append(f"QUANT mm {value.hex()} {proto.meters_to_mm(value)}")
        lines.append(f"QUANT vel {value.hex()} {proto.velocity_to_cms(value)}")
        lines.append(f"QUANT yaw {value.hex()} {proto.yaw_to_u16(value)}")
        lines.append(f"QUANT pitch {value.hex()} {proto.pitch_to_i16(value)}")
    for index in range(400):
        wide = rng.uniform(-30000.0, 30000.0)
        half = (int(wide // 1) + 0.5)
        mm_value = half / 1000.0 if index % 4 == 0 else wide
        vel_value = half / 100.0 if index % 4 == 0 else wide / 50.0
        yaw_value = (wide // 1) * 45.0 if index % 5 == 0 else wide / 20.0
        lines.append(f"QUANT mm {mm_value.hex()} {proto.meters_to_mm(mm_value)}")
        lines.append(f"QUANT vel {vel_value.hex()} {proto.velocity_to_cms(vel_value)}")
        lines.append(f"QUANT yaw {yaw_value.hex()} {proto.yaw_to_u16(yaw_value)}")
        lines.append(f"QUANT pitch {(wide / 200.0).hex()} {proto.pitch_to_i16(wide / 200.0)}")
        quat = [rng.uniform(-1.0, 1.0) for _ in range(4)]
        if index % 7 == 0:
            quat[rng.randrange(4)] = 0.0
        if index % 50 == 0:
            quat = [0.0, 0.0, 0.0, 0.0] if index % 100 == 0 else [float("nan"), 0.0, 0.0, 1.0]
        lines.append("QUANT quat " + " ".join(v.hex() for v in quat) + f" {proto.pack_quat(*quat)}")
    return lines


# ---------------------------------------------------------------------------
# delta scenarios
# ---------------------------------------------------------------------------

class PyWorld:
    """Moving entities in metres/degrees, quantized with snapshot.quantize every tick."""

    def __init__(self, rng: random.Random, count: int):
        self.rng = rng
        self.next_id = 1
        self.entities = {}
        for _ in range(count):
            self.add()

    def add(self) -> None:
        rng = self.rng
        kind = rng.randint(1, 5)
        self.entities[self.next_id] = {
            "kind": kind, "flags": rng.randrange(16), "attitude": rng.randrange(3), "record": 1000 + kind,
            "appearance": u(rng, 64), "pos": [rng.uniform(-1500, -1300), rng.uniform(100, 300), rng.uniform(10, 40)],
            "yaw": rng.uniform(-720, 720), "quat": [rng.uniform(-1, 1) for _ in range(4)], "vel": [0.0, 0.0, 0.0],
            "move": 0, "eflags": 0, "health": 255, "target": 0, "weapon": 0,
            "world_id": ((0x8F << 56) | self.next_id) if kind == 3 else 0}
        self.next_id += 1

    def step(self, tick: int) -> None:
        rng = self.rng
        if tick % 20 == 0 and len(self.entities) > 3:
            for net_id in rng.sample(sorted(self.entities), 3):
                del self.entities[net_id]
            for _ in range(3):
                self.add()
        for entity in self.entities.values():
            if rng.random() < 0.3:
                continue
            if rng.random() < 0.02:
                entity["pos"][0] += rng.uniform(40.0, 90.0)
            for axis in range(3):
                entity["pos"][axis] += rng.uniform(-0.8, 0.8)
            if rng.random() < 0.5:
                entity["yaw"] += rng.uniform(-30, 30)
                entity["quat"] = [rng.uniform(-1, 1) for _ in range(4)]
            if rng.random() < 0.4:
                entity["vel"] = [rng.uniform(-9, 9), rng.uniform(-9, 9), 0.0]
            if rng.random() < 0.1:
                entity["move"], entity["eflags"], entity["health"] = rng.randrange(14), u(rng, 8), u(rng, 8)
            if rng.random() < 0.05:
                entity["target"] = 0 if rng.random() < 0.3 else rng.randint(1, 0xFFFF)
            if rng.random() < 0.03:
                entity["weapon"] = 0 if rng.random() < 0.3 else u(rng, 64)
            if rng.random() < 0.01:
                entity["attitude"] = rng.randrange(3)

    def states(self) -> dict:
        result = {}
        for net_id, e in self.entities.items():
            rotation = e["quat"] if e["kind"] == proto.EntityKind.VEHICLE else e["yaw"]
            result[net_id] = snapshot.quantize(e["kind"], e["flags"], e["attitude"], e["record"], e["appearance"],
                                               e["pos"], rotation, e["vel"], e["move"], e["eflags"], e["health"],
                                               e["target"], e["weapon"], e["world_id"])
        return result


def state_line(net_id: int, st) -> str:
    return (f"S {net_id} {int(st.kind)} {st.spawn_flags} {st.attitude} {st.record} {st.appearance} "
            f"{st.pos[0]} {st.pos[1]} {st.pos[2]} {st.rot} {st.vel[0]} {st.vel[1]} {st.vel[2]} "
            f"{st.state[0]} {st.state[1]} {st.state[2]} {st.target} {st.weapon} {st.world_id}")


def encoder_stats_line(stats: dict) -> str:
    return "ESTATS " + " ".join(str(stats[key]) for key in ("snapshots", "bytes", "full_bytes", "records", "deferred",
                                                             "full_snapshots", "removals", "spawns"))


def decoder_stats_line(stats: dict) -> str:
    return "DSTATS " + " ".join(str(stats[key]) for key in ("decoded", "duplicate", "missing_baseline",
                                                             "inconsistent", "stale"))


def delta_lines(name: str, seed: int, entities: int, budget: int, ticks: int, loss: float) -> list:
    rng = random.Random(seed)
    world = PyWorld(rng, entities)
    encoder = snapshot.DeltaEncoder(budget)
    decoder = snapshot.DeltaDecoder()
    codec = proto.EntitySnapshotCodec()
    lines = [f"DELTA {name} {budget}"]
    in_flight, acks_due, bodies = [], [], {}
    order = 0
    for now in range(1, ticks + 1):
        world.step(now)
        due = sorted(item for item in acks_due if item[0] <= now)
        acks_due = [item for item in acks_due if item[0] > now]
        acks = [tick for _, _, tick in due]
        for tick in acks:
            encoder.on_ack(tick)
        sample_time = now * 100
        lines.append(f"TICK {sample_time} {len(acks)}" + "".join(f" {tick}" for tick in acks))
        states = world.states()
        weights = {}
        for net_id in states:
            if rng.random() < 0.5:
                weights[net_id] = rng.choice((0.5, 1.0, 1.25, 2.0, 3.0, 0.1, 1.1, rng.uniform(0.1, 3.0)))
        lines += [f"W {net_id} {weight.hex()}" for net_id, weight in sorted(weights.items())]
        lines += [state_line(net_id, st) for net_id, st in states.items()]
        body, tick, baseline, view = encoder.encode(sample_time, states, weights)
        lines.append(f"OUT {tick} {baseline} {snapshot.view_hash(view)} {body.hex()}")
        bodies[tick] = body
        if rng.random() >= loss:
            in_flight.append((now + rng.randint(1, 4), order, tick))
            order += 1
        if rng.random() < 0.03:
            in_flight.append((now + rng.randint(1, 8), order, tick))
            order += 1
        if now % 50 == 0 and tick > 70:
            in_flight.append((now + 1, order, tick - 70))
            order += 1
        arrived = sorted(item for item in in_flight if item[0] <= now or now == ticks)
        in_flight = [item for item in in_flight if not (item[0] <= now or now == ticks)]
        for _, _, tick in arrived:
            view = decoder.apply(codec.decode(bodies[tick]))
            lines.append(f"APPLY {tick} {snapshot.view_hash(view) if view is not None else 'none'}")
            if view is not None and rng.random() >= loss:
                acks_due.append((now + rng.randint(1, 3), order, tick))
                order += 1
    lines += [encoder_stats_line(encoder.stats), decoder_stats_line(decoder.stats), "ENDDELTA"]
    return lines


# ---------------------------------------------------------------------------
# generate (proto.py -> file)
# ---------------------------------------------------------------------------

def spec_lines() -> list:
    lines = []
    for mtype, spec in sorted(proto.SPECS.items()):
        delivery = "by_channel" if spec.reliable is None else ("reliable" if spec.reliable else "unreliable")
        lines.append(f"SPEC {int(mtype)} {spec.mtype.name} {delivery} {spec.sender.value} {spec.route.value} "
                     f"{spec.min_minor}")
    return lines


def python_constants() -> dict:
    return {"PROTO_MAJOR": proto.PROTO_MAJOR, "PROTO_MINOR": proto.PROTO_MINOR,
            "MIN_SUPPORTED_MINOR": proto.MIN_SUPPORTED_MINOR, "MAX_PACKET": proto.MAX_PACKET,
            "PACKET_HEADER": proto.PACKET_HEADER.size, "MESSAGE_HEADER": proto.MESSAGE_HEADER.size,
            "MAX_MESSAGE_BODY": proto.MAX_MESSAGE_BODY, "MAX_MESSAGES_PER_PACKET": proto.MAX_MESSAGES_PER_PACKET,
            "HELLO_MIN_PACKET": proto.HELLO_MIN_PACKET, "COOKIE_SIZE": proto.COOKIE_SIZE,
            "KEY_HASH_SIZE": proto.KEY_HASH_SIZE, "COOKIE_MAX_AGE_S": proto.COOKIE_MAX_AGE_S,
            "MAX_NAME_BYTES": proto.MAX_NAME_BYTES, "MAX_ROOM_BYTES": proto.MAX_ROOM_BYTES,
            "MAX_CHAT_BYTES": proto.MAX_CHAT_BYTES, "MAX_SCRIPT_BYTES": proto.MAX_SCRIPT_BYTES,
            "MAX_ENTITY_RECORDS": proto.MAX_ENTITY_RECORDS, "MOVE_STATE_COUNT": proto.MOVE_STATE_COUNT,
            "HISTORY_TICKS": snapshot.HISTORY_TICKS, "DEFAULT_BUDGET": snapshot.DEFAULT_BUDGET}


def valid_lines(name: str, canonical: bool, data: bytes, described: list) -> list:
    return [f"VALID {name} {1 if canonical else 0} {hex_or_dash(data)}"] + [f"D {line}" for line in described] + ["END"]


def generate(path: str, seed: int) -> dict:
    rng = random.Random(seed)
    lines = [f"# proto.py vectors (tools/v2_golden.py generate, seed {seed}, protocol 2.{proto.PROTO_MINOR})"]
    lines += spec_lines()
    lines += [f"CONST {name} {value}" for name, value in python_constants().items()]
    lines += hash_lines(rng)
    lines += quant_lines(rng)
    counts = {"valid": 0, "mutated_valid": 0, "invalid": 0, "handcrafted_invalid": 0}
    canonical = []
    for name, data in handcrafted_valid():
        canonical.append((name, data))
    for mtype in sorted(proto.SPECS):
        for repeat in range(20):
            values = rand_body(rng, mtype)
            body = proto.encode_body(mtype, values)
            reliable = reliable_for(mtype, values)
            data = data_packet([(mtype, u(rng, 8), u(rng, 16) if reliable else None, body)],
                               u(rng, 64), u(rng, 16), u(rng, 16), u(rng, 32))
            canonical.append((f"py-{proto.MsgType(mtype).name}-{repeat}", data))
    for index in range(600):
        canonical.append((f"py-random-{index}", rand_datagram(rng)))
    for name, data in canonical:
        lines += valid_lines(name, True, data, full_decode(data))
        counts["valid"] += 1
    for name, data in invalid_vectors():
        try:
            full_decode(data)
        except proto.ProtocolError:
            lines.append(f"INVALID {name} {hex_or_dash(data)}")
            counts["handcrafted_invalid"] += 1
            continue
        raise SystemExit(f"generator bug: handcrafted invalid vector {name} is accepted by proto.py")
    for index in range(2400):
        name, data = canonical[rng.randrange(len(canonical))]
        mutated = mutate(rng, data)
        label = f"py-mutation-{index}-of-{name}"
        try:
            described = full_decode(mutated)
        except proto.ProtocolError:
            lines.append(f"INVALID {label} {hex_or_dash(mutated)}")
            counts["invalid"] += 1
            continue
        lines += valid_lines(label, False, mutated, described)
        counts["mutated_valid"] += 1
    lines += delta_lines("py-delta-lossy", seed + 1, 60, 600, 260, 0.15)
    lines += delta_lines("py-delta-clean", seed + 2, 30, 1000, 120, 0.0)
    lines += delta_lines("py-delta-tight", seed + 3, 120, 300, 150, 0.3)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    return counts


# ---------------------------------------------------------------------------
# check-cpp (C++ file -> proto.py)
# ---------------------------------------------------------------------------

class Results:
    def __init__(self):
        self.passed = {}
        self.failures = 0

    def ok(self, kind: str, count: int = 1) -> None:
        self.passed[kind] = self.passed.get(kind, 0) + count

    def fail(self, kind: str, what: str) -> None:
        self.failures += 1
        if self.failures <= 40:
            print(f"  FAIL {kind}: {what}")

    def expect(self, condition: bool, kind: str, what: str) -> None:
        if condition:
            self.ok(kind)
        else:
            self.fail(kind, what)


def check_hash(results: Results, tokens: list) -> None:
    kind = tokens[1]
    if kind == "sha256":
        results.expect(hashlib.sha256(from_hex(tokens[2])).hexdigest() == tokens[3], "hash sha256", tokens[2][:32])
    elif kind == "hmac":
        mac = hmac.new(from_hex(tokens[2]), from_hex(tokens[3]), hashlib.sha256).hexdigest()
        results.expect(mac == tokens[4], "hash hmac", tokens[2][:32])
    elif kind == "build":
        results.expect(proto.game_build_id(from_hex(tokens[2]).decode("utf-8")) == int(tokens[3]), "hash build", tokens[2])
    elif kind == "room":
        key = proto.room_key_hash(from_hex(tokens[2]).decode("utf-8"), from_hex(tokens[3]).decode("utf-8"))
        results.expect(key.hex() == tokens[4], "hash room", tokens[2])
    elif kind == "mods":
        count = int(tokens[2])
        entries = [(from_hex(tokens[3 + 2 * i]).decode("utf-8"), from_hex(tokens[4 + 2 * i]).decode("utf-8"))
                   for i in range(count)]
        results.expect(proto.mod_list_hash(entries) == int(tokens[3 + 2 * count]), "hash mods", f"{count} entries")
    elif kind == "cookie":
        cookie = proto.make_cookie(bytes.fromhex(tokens[2]), (bytes.fromhex(tokens[3]).decode(), int(tokens[4])),
                                   int(tokens[5]), int(tokens[6]))
        results.expect(cookie.hex() == tokens[7], "hash cookie", tokens[7])
    elif kind == "cookiecheck":
        verdict = proto.check_cookie(bytes.fromhex(tokens[2]), (bytes.fromhex(tokens[3]).decode(), int(tokens[4])),
                                     int(tokens[5]), bytes.fromhex(tokens[6]), int(tokens[7]))
        results.expect(verdict == (tokens[8] == "1"), "hash cookiecheck", tokens[6])
    else:
        results.fail("hash", f"unknown kind {kind}")


def check_quant(results: Results, tokens: list) -> None:
    kind = tokens[1]
    if kind == "quat":
        got = proto.pack_quat(*(float.fromhex(v) for v in tokens[2:6]))
        expected = int(tokens[6])
    else:
        value = float.fromhex(tokens[2])
        expected = int(tokens[3])
        got = {"mm": proto.meters_to_mm, "vel": proto.velocity_to_cms, "yaw": proto.yaw_to_u16,
               "pitch": proto.pitch_to_i16}[kind](value)
    results.expect(got == expected, f"quant {kind}", f"{tokens[2:]}: Python {got}, C++ {expected}")


def check_delta(results: Results, head: list, lines, index: int) -> int:
    name = head[1]
    encoder = snapshot.DeltaEncoder(int(head[2]))
    decoder = snapshot.DeltaDecoder()
    codec = proto.EntitySnapshotCodec()
    acks, sample_time, states, weights, bodies = [], 0, {}, {}, {}
    ok = True
    outs = applies = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        tokens = line.split()
        if not tokens:
            continue
        kind = tokens[0]
        if kind == "ENDDELTA":
            break
        if kind == "TICK":
            sample_time = int(tokens[1])
            acks = [int(t) for t in tokens[3:3 + int(tokens[2])]]
            states, weights = {}, {}
        elif kind == "W":
            weights[int(tokens[1])] = float.fromhex(tokens[2])
        elif kind == "S":
            v = [int(t) for t in tokens[1:]]
            states[v[0]] = snapshot.EntityState(v[1], v[2], v[3], v[4], v[5], (v[6], v[7], v[8]), v[9],
                                                (v[10], v[11], v[12]), (v[13], v[14], v[15]), v[16], v[17], v[18])
        elif kind == "OUT":
            for tick in acks:
                encoder.on_ack(tick)
            body, tick, baseline, view = encoder.encode(sample_time, states, weights)
            same = (str(tick), str(baseline), snapshot.view_hash(view), body.hex()) == tuple(tokens[1:5])
            if not same and ok:
                results.fail("delta encode", f"{name} tick {tokens[1]}: Python baseline {baseline} "
                                             f"view {snapshot.view_hash(view)} body {body.hex()[:120]}\n"
                                             f"      C++ baseline {tokens[2]} view {tokens[3]} body {tokens[4][:120]}")
                ok = False
            bodies[int(tokens[1])] = bytes.fromhex(tokens[4])
            outs += 1
        elif kind == "APPLY":
            view = decoder.apply(codec.decode(bodies[int(tokens[1])]))
            got = snapshot.view_hash(view) if view is not None else "none"
            if got != tokens[2] and ok:
                results.fail("delta decode", f"{name} apply {tokens[1]}: Python {got}, C++ {tokens[2]}")
                ok = False
            applies += 1
        elif kind == "ESTATS":
            if encoder_stats_line(encoder.stats) != line and ok:
                results.fail("delta stats", f"{name}: Python {encoder_stats_line(encoder.stats)}, C++ {line}")
                ok = False
        elif kind == "DSTATS":
            if decoder_stats_line(decoder.stats) != line and ok:
                results.fail("delta stats", f"{name}: Python {decoder_stats_line(decoder.stats)}, C++ {line}")
                ok = False
    if ok:
        results.ok("delta scenario")
        results.ok("delta snapshots byte-for-byte", outs)
        results.ok("delta applications", applies)
    return index


def check_cpp(path: str) -> Results:
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    results = Results()
    specs_seen = 0
    constants = python_constants()
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        tokens = line.split()
        if not tokens or tokens[0].startswith("#"):
            continue
        kind = tokens[0]
        if kind == "SPEC":
            specs_seen += 1
            mine = {line_spec.split()[1]: line_spec for line_spec in spec_lines()}
            results.expect(mine.get(tokens[1]) == line, "spec", line)
        elif kind == "CONST":
            results.expect(constants.get(tokens[1]) == int(tokens[2]), "const", line)
        elif kind == "HASH":
            check_hash(results, tokens)
        elif kind == "QUANT":
            check_quant(results, tokens)
        elif kind == "VALID":
            data = from_hex(tokens[3])
            expected = []
            while index < len(lines) and lines[index] != "END":
                expected.append(lines[index][2:])
                index += 1
            index += 1
            try:
                described = full_decode(data)
            except proto.ProtocolError as error:
                results.fail("valid decode", f"{tokens[1]}: proto.py rejects ({error}), C++ accepts")
                continue
            if described != expected:
                for mine, theirs in zip(described + ["<none>"] * len(expected), expected + ["<none>"] * len(described)):
                    if mine != theirs:
                        results.fail("valid describe", f"{tokens[1]}\n      Python: {mine[:400]}\n      C++   : {theirs[:400]}")
                        break
                continue
            results.ok("valid decode+describe")
            if tokens[2] == "1":
                again = python_reencode(data)
                results.expect(again == data, "valid re-encode byte-for-byte", f"{tokens[1]}: {again.hex()[:200]}")
        elif kind == "INVALID":
            try:
                full_decode(from_hex(tokens[2]))
            except proto.ProtocolError:
                results.ok("invalid rejected")
                continue
            results.fail("invalid rejected", f"{tokens[1]}: proto.py accepts, C++ rejects")
        elif kind == "DELTA":
            index = check_delta(results, tokens, lines, index)
        else:
            results.fail("format", f"unknown line {kind}")
    results.expect(specs_seen == len(proto.SPECS), "spec table size", f"{specs_seen} C++ specs vs {len(proto.SPECS)}")
    return results


def check_header(relay_dir: str) -> bool:
    original = os.path.join(relay_dir, "include", "coop_proto_v2.h")
    with open(original, "rb") as handle:
        theirs = handle.read()
    with open(HEADER_COPY, "rb") as handle:
        mine = handle.read()
    same = theirs.replace(b"\r\n", b"\n") == mine.replace(b"\r\n", b"\n")
    digest = hashlib.sha256(mine.replace(b"\r\n", b"\n")).hexdigest()[:16]
    print(f"header copy src/v2/coop_proto_v2.h {'identical to' if same else 'DIFFERS FROM'} {original} "
          f"(sha256 {digest}, line endings normalised)")
    return same


def print_results(title: str, results: Results) -> None:
    for kind, count in sorted(results.passed.items()):
        print(f"  {kind:<36} {count:>6}")
    total = sum(results.passed.values())
    print(f"{title}: {total} passed, {results.failures} failed")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--relay", default=default_relay(), help="relay checkout (has coopnet/proto.py)")
    parser.add_argument("--seed", type=int, default=20261004)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="every step against the built coopnet_v2_golden.exe")
    run.add_argument("--exe", required=True)
    run.add_argument("--out", default=os.path.join(ROOT, "build", "golden"))
    sub.add_parser("check-header")
    generate_parser = sub.add_parser("generate")
    generate_parser.add_argument("path")
    check_parser = sub.add_parser("check-cpp")
    check_parser.add_argument("path")
    args = parser.parse_args(argv)
    load_relay(os.path.abspath(args.relay))
    if sys.version_info < (3, 12):
        print("warning: Python < 3.12 sums floats differently; pack_quat vectors may differ in rare cases")
    print(f"proto.py: {os.path.join(os.path.abspath(args.relay), 'coopnet', 'proto.py')} "
          f"(protocol {proto.PROTO_MAJOR}.{proto.PROTO_MINOR}), Python {sys.version.split()[0]}")
    if args.command == "check-header":
        return 0 if check_header(args.relay) else 1
    if args.command == "generate":
        print(generate(args.path, args.seed))
        return 0
    if args.command == "check-cpp":
        results = check_cpp(args.path)
        print_results("golden check (C++ -> proto.py)", results)
        return 0 if results.failures == 0 else 1
    started = time.perf_counter()
    exe = os.path.abspath(args.exe)
    if not os.path.isfile(exe):
        raise SystemExit(f"{exe} not found; build the coopnet_v2_golden target first")
    ok = check_header(args.relay)
    py_path = os.path.join(args.out, "py_vectors.txt")
    cpp_path = os.path.join(args.out, "cpp_vectors.txt")
    counts = generate(py_path, args.seed)
    print(f"proto.py wrote {py_path}: {counts['valid']} valid datagrams, {counts['handcrafted_invalid']} handcrafted "
          f"invalid, {counts['mutated_valid']} accepted and {counts['invalid']} rejected mutations, 3 delta scenarios")
    check = subprocess.run([exe, "check", py_path], capture_output=True, text=True)
    print(check.stdout.rstrip())
    ok = ok and check.returncode == 0
    emit = subprocess.run([exe, "emit", cpp_path, str(args.seed)], capture_output=True, text=True)
    print(emit.stdout.rstrip())
    if emit.returncode != 0:
        return 1
    results = check_cpp(cpp_path)
    print_results("golden check (C++ -> proto.py)", results)
    ok = ok and results.failures == 0
    print(f"V2 GOLDEN {'PASS' if ok else 'FAIL'} in {time.perf_counter() - started:.1f} s")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
