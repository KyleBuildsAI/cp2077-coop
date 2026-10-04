"""Compiles include/coop_proto_v2.h with MSVC and checks it against coopnet/proto.py.

For every wire struct it compares sizeof and each field's offset and size with
the Python struct layout, then has the compiled probe decode a PlayerSnapshot
(+ VehicleBlock) that Python encoded, to prove both sides read the same values.

Usage: python tools/check_c_header.py [--vcvars PATH]
"""
from __future__ import annotations

import argparse
import os
import re
import struct
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from coopnet import proto  # noqa: E402

DEFAULT_VCVARS = r"C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
CODEC_STRUCTS = {
    "TIME_REQ": "TimeReq", "TIME_RESP": "TimeResp", "PEER_JOINED": "PeerJoined", "PEER_LEFT": "PeerLeft",
    "LINK_STATS": "LinkStats", "PLAYER_SNAPSHOT": "PlayerSnapshot", "VehicleBlock": "VehicleBlock",
    "SNAPSHOT_ACK": "SnapshotAck", "FIRE_FX": "FireFx", "EQUIP": "Equip", "VEHICLE_ENTER": "VehicleEnter",
    "VEHICLE_EXIT": "VehicleExit", "HIT": "Hit", "DEATH": "Death", "TIME_WEATHER": "TimeWeather",
    "CHAT": "ChatFixed", "TELEPORT_REQ": "TeleportReq", "TELEPORT_RESP": "TeleportResp",
    "WORLD_FACT": "WorldFact", "MOD_LIST": "ModListFixed", "SESSION_CONFIG": "SessionConfig",
}
SAMPLE = {"snap_seq": 513, "sample_time": 123456789, "x": -1450.25, "y": 180.5, "z": 22.125, "yaw": 40000,
          "pitch": -350, "vx": 600, "vy": -200, "vz": 5, "move_state": 10, "health": 230,
          "flags": int(proto.PlayerFlag.IN_VEHICLE | proto.PlayerFlag.DRIVING), "vehicle": {
              "vehicle_net": 0x8002, "px": -1450.25, "py": 180.5, "pz": 22.125, "quat": 0xC0FFEE11,
              "lvx": 1400, "lvy": -3, "lvz": 0, "avx": 1, "avy": -2, "avz": 120, "steer": -20, "throttle": 70,
              "brake": 3, "vflags": 1}}


def layout(fmt: str, names) -> list:
    names = list(names)
    offset = 0
    result = []
    for count, code in re.findall(r"(\d*)([xcbB?hHiIlLqQefds])", fmt.lstrip("<")):
        size = struct.calcsize("<" + count + code)
        result.append((names.pop(0), offset, size))
        offset += size
    if names:
        raise ValueError(f"unused names {names} for {fmt}")
    return result


def table() -> list:
    rows = [
        ("PacketHeader", proto.PACKET_HEADER, "magic major ptype token seq ack ack_bits"),
        ("MessageHeader", proto.MESSAGE_HEADER, "type peer length"),
        ("JoinFixed", proto.JOIN_FIXED, " ".join(proto.JOIN_FIELDS)),
        ("Challenge", proto.CHALLENGE_BODY, "minor_min minor_max reserved cookie"),
        ("Welcome", proto.WELCOME_BODY, " ".join(proto.WELCOME_FIELDS)),
        ("RejectFixed", proto.REJECT_FIXED, "reason minor_min minor_max text_len"),
        ("EntitySnapshotHeader", proto.ENTITY_HEADER, "tick baseline sample_time count"),
        ("EntitySpawn", proto.SPAWN_BLOCK, "kind spawn_flags attitude reserved record appearance"),
    ]
    codecs = [spec.codec for spec in proto.SPECS.values() if isinstance(spec.codec, proto.FixedCodec)]
    codecs += [proto.PLAYER_BASE, proto.VEHICLE_BLOCK]
    seen = set()
    for codec in codecs:
        if codec.name in seen:
            continue
        seen.add(codec.name)
        rows.append((CODEC_STRUCTS[codec.name], codec.struct, " ".join(codec.fields)))
    return [(name, layout_struct.size, layout(layout_struct.format, fields.split())) for name, layout_struct, fields in rows]


def probe_source(rows) -> str:
    lines = ["#include <cstdio>", "#include <cstddef>", "#include <cstring>", '#include "coop_proto_v2.h"',
             "using namespace coopv2;", "int main(int argc, char** argv) {"]
    for name, _, fields in rows:
        lines.append(f'    std::printf("S {name} %zu\\n", sizeof({name}));')
        for field, _, _ in fields:
            lines.append(f'    std::printf("F {name}.{field} %zu %zu\\n", offsetof({name}, {field}), '
                         f"sizeof({name}::{field}));")
    lines += [
        "    if (argc > 1) {",
        "        unsigned char data[256] = {}; FILE* f = nullptr;",
        '        if (fopen_s(&f, argv[1], "rb") != 0 || !f) return 2;',
        "        size_t n = fread(data, 1, sizeof(data), f); fclose(f);",
        "        if (n != sizeof(PlayerSnapshot) + sizeof(VehicleBlock)) return 3;",
        "        PlayerSnapshot p; VehicleBlock v;",
        "        std::memcpy(&p, data, sizeof(p)); std::memcpy(&v, data + sizeof(p), sizeof(v));",
        '        std::printf("V snap_seq %u\\n", p.snap_seq);',
        '        std::printf("V sample_time %u\\n", p.sample_time);',
        '        std::printf("V x %.6f\\n", p.x);',
        '        std::printf("V yaw %u\\n", p.yaw);',
        '        std::printf("V pitch %d\\n", p.pitch);',
        '        std::printf("V flags %u\\n", p.flags);',
        '        std::printf("V driving %d\\n", (p.flags & kPlayerDriving) ? 1 : 0);',
        '        std::printf("V vehicle_net %u\\n", v.vehicle_net);',
        '        std::printf("V quat %u\\n", v.quat);',
        '        std::printf("V steer %d\\n", v.steer);',
        '        std::printf("V avz %d\\n", v.avz);',
        "    }",
        "    return 0;",
        "}",
    ]
    return "\n".join(lines) + "\n"


def compile_probe(build_dir: str, vcvars: str) -> str:
    batch = os.path.join(build_dir, "build.bat")
    with open(batch, "w", encoding="ascii") as handle:
        handle.write("@echo off\r\n")
        handle.write(f'call "{vcvars}" >nul\r\n')
        handle.write(f'cl /nologo /std:c++17 /W4 /WX /EHsc /I"{os.path.join(ROOT, "include")}" probe.cpp /Fe:probe.exe\r\n')
    result = subprocess.run(["cmd", "/c", batch], cwd=build_dir, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"compile failed:\n{result.stdout}\n{result.stderr}")
    return os.path.join(build_dir, "probe.exe")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vcvars", default=DEFAULT_VCVARS)
    args = parser.parse_args(argv)
    build_dir = os.path.join(ROOT, "build", "cheader")
    os.makedirs(build_dir, exist_ok=True)
    rows = table()
    with open(os.path.join(build_dir, "probe.cpp"), "w", encoding="ascii") as handle:
        handle.write(probe_source(rows))
    probe = compile_probe(build_dir, args.vcvars)
    sample_path = os.path.join(build_dir, "sample.bin")
    with open(sample_path, "wb") as handle:
        handle.write(proto.PlayerSnapshotCodec().encode(SAMPLE))
    output = subprocess.run([probe, sample_path], capture_output=True, text=True, check=True).stdout.splitlines()
    measured = {}
    values = {}
    for line in output:
        kind, key, *rest = line.split()
        if kind == "V":
            values[key] = rest[0]
        else:
            measured[key] = tuple(int(v) for v in rest)
    problems = []
    checked = 0
    for name, size, fields in rows:
        checked += 1
        if measured.get(name) != (size,):
            problems.append(f"{name}: C sizeof {measured.get(name)} != Python {size}")
        for field, offset, field_size in fields:
            checked += 1
            if measured.get(f"{name}.{field}") != (offset, field_size):
                problems.append(f"{name}.{field}: C {measured.get(f'{name}.{field}')} != Python {(offset, field_size)}")
    expected_values = {"snap_seq": "513", "sample_time": "123456789", "x": f"{SAMPLE['x']:.6f}", "yaw": "40000",
                       "pitch": "-350", "flags": str(SAMPLE["flags"]), "driving": "1", "vehicle_net": str(0x8002),
                       "quat": str(0xC0FFEE11), "steer": "-20", "avz": "120"}
    for key, expected in expected_values.items():
        checked += 1
        if values.get(key) != expected:
            problems.append(f"decoded {key}: C {values.get(key)} != Python {expected}")
    for name, size, fields in rows:
        print(f"  {name:<22} {size:>3} bytes, {len(fields):>2} fields")
    if problems:
        print("MISMATCHES:\n  " + "\n  ".join(problems))
        return 1
    print(f"OK: {len(rows)} structs, {checked} size/offset/value checks match between C++ (MSVC) and Python")
    return 0


if __name__ == "__main__":
    sys.exit(main())
