"""Jakub's v1 text protocol and the init.lua forward-vector payload, for coexistence.

v1 wire format (unchanged):
  client -> relay : CP1,<seq>,<x>,<y>,<z>,<w>,<fx>,<fy>
  relay -> client : WELCOME,<id>
  relay -> others : RP1,<id>,<seq>,<x>,<y>,<z>,<w>,<fx>,<fy>

init.lua hides a 9-bit payload in the forward-vector length: |(fx, fy)| = 1 + payload,
payload = type * 512 + value (TYPE_FLAGS=0, TIME=1 (value = minutes / 3), WEATHER=2
(value = index + 1), PING=3, PONG=4, VEHICLE=5, mod list HI/LO=6/7).
FLAGS value bits: crouch 1, weapon drawn 2, aiming 4, firing 8, in vehicle 16,
weapon class * 32 (3 bits), host 256.
"""
from __future__ import annotations

import math

from . import proto

TYPE_STRIDE = 512
TYPE_FLAGS = 0
TYPE_TIME = 1
TYPE_WEATHER = 2
TYPE_PING = 3
TYPE_PONG = 4
TYPE_VEHICLE = 5
TIME_STEP_MINUTES = 3

V1_CROUCH = 1
V1_WEAPON_DRAWN = 2
V1_AIMING = 4
V1_FIRING = 8
V1_IN_VEHICLE = 16
V1_WEAPON_CLASS_MULTIPLIER = 32
V1_HOST = 256

_FLAG_MAP = (
    (V1_CROUCH, proto.PlayerFlag.CROUCH),
    (V1_WEAPON_DRAWN, proto.PlayerFlag.WEAPON_DRAWN),
    (V1_AIMING, proto.PlayerFlag.AIMING),
    (V1_FIRING, proto.PlayerFlag.FIRING),
    (V1_IN_VEHICLE, proto.PlayerFlag.IN_VEHICLE),
)


def is_cp1(data: bytes) -> bool:
    return data.startswith(b"CP1,")


def parse_cp1(data: bytes):
    """Returns (seq, x, y, z, w, fx, fy) or None for malformed packets."""
    try:
        parts = data.decode("ascii").strip().split(",")
    except UnicodeDecodeError:
        return None
    if len(parts) != 8 or parts[0] != "CP1":
        return None
    try:
        seq = int(parts[1])
        values = [float(value) for value in parts[2:]]
    except ValueError:
        return None
    if not all(math.isfinite(value) for value in values):
        return None
    return (seq, *values)


def format_rp1(client_id: int, seq: int, x: float, y: float, z: float, w: float, fx: float, fy: float) -> bytes:
    return f"RP1,{client_id},{seq},{x:.6f},{y:.6f},{z:.6f},{w:.6f},{fx:.6f},{fy:.6f}".encode()


def format_cp1(seq: int, x: float, y: float, z: float, w: float, fx: float, fy: float) -> bytes:
    return f"CP1,{seq},{x:.6f},{y:.6f},{z:.6f},{w:.6f},{fx:.6f},{fy:.6f}".encode()


def encode_forward(forward_x: float, forward_y: float, payload: int):
    scale = 1.0 + payload
    return forward_x * scale, forward_y * scale


def decode_forward(raw_x: float, raw_y: float):
    """Returns (unit_x, unit_y, payload or None), same rounding as init.lua."""
    length = math.hypot(raw_x, raw_y)
    if length < 0.5:
        return 0.0, 1.0, None
    return raw_x / length, raw_y / length, int(math.floor(length - 1.0 + 0.5))


def split_payload(payload: int):
    return payload // TYPE_STRIDE, payload % TYPE_STRIDE


def v1_flags_to_v2(value: int) -> int:
    flags = proto.PlayerFlag.LEGACY
    for v1_bit, v2_bit in _FLAG_MAP:
        if value & v1_bit:
            flags |= v2_bit
    weapon_class = (value // V1_WEAPON_CLASS_MULTIPLIER) % 8
    return int(flags) | (weapon_class << proto.WEAPON_CLASS_SHIFT)


def v2_flags_to_v1(flags: int, is_host: bool) -> int:
    value = 0
    for v1_bit, v2_bit in _FLAG_MAP:
        if flags & v2_bit:
            value |= v1_bit
    weapon_class = min(7, (flags >> proto.WEAPON_CLASS_SHIFT) & proto.WEAPON_CLASS_MASK)
    value += weapon_class * V1_WEAPON_CLASS_MULTIPLIER
    if is_host:
        value += V1_HOST
    return value


def yaw_from_forward(forward_x: float, forward_y: float) -> float:
    """Cyberpunk yaw: 0 deg faces +Y, positive turns towards -X."""
    return math.degrees(math.atan2(-forward_x, forward_y)) % 360.0


def forward_from_yaw(yaw_degrees: float):
    radians = math.radians(yaw_degrees)
    return -math.sin(radians), math.cos(radians)
