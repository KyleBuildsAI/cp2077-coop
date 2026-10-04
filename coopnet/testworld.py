"""Deterministic demo world for the test clients.

Both test clients build the same entity table from the same seed, so the
joiner can evaluate the host's ground truth at any render time and measure
how far its interpolated NPCs, vehicles and the remote player really are
from where the host had them. World time is seconds since the host's epoch,
which the host announces with a WORLD_FACT (fact_hash = EPOCH_FACT).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

from . import proto
from .legacy import yaw_from_forward

ORIGIN = (-1450.0, 180.0, 22.0)
EPOCH_FACT = 0xC0F7_0000_0000_0001
HOST_RADIUS_M = 10.0
HOST_SPEED_MPS = 6.0
JOINER_AXES_M = (140.0, 90.0)
JOINER_PERIOD_S = 50.0
JOINER_CENTER_OFFSET = (40.0, 0.0)
RECORD_BASE = 0x5EED_0000_0000_0000


@dataclass(frozen=True)
class EntitySpec:
    net_id: int
    kind: int
    center: tuple
    radius: float
    speed: float
    phase: float
    spawn_at: float
    despawn_at: float | None
    death_at: float | None
    flags: int

    @property
    def record(self) -> int:
        return RECORD_BASE + self.kind

    @property
    def appearance(self) -> int:
        return self.net_id


def build_entities(seed: int = 7) -> list:
    rng = random.Random(seed)
    specs = []

    def add(kind, center, radius, speed, spawn_at=0.0, despawn_at=None, death_at=None, flags=0):
        specs.append(EntitySpec(len(specs) + 1, kind, center, radius, speed, rng.uniform(0, 2 * math.pi),
                                spawn_at, despawn_at, death_at, flags))

    for _ in range(46):
        center = (ORIGIN[0] + rng.uniform(-180, 180), ORIGIN[1] + rng.uniform(-180, 180), ORIGIN[2])
        speed = 0.0 if rng.random() < 0.3 else rng.uniform(1.0, 1.7)
        add(proto.EntityKind.CROWD_NPC, center, rng.uniform(1.5, 6.0), speed)
    combat_flags = int(proto.EntityFlag.COMBAT | proto.EntityFlag.WEAPON_DRAWN | proto.EntityFlag.HOSTILE)
    for index in range(10):
        center = (ORIGIN[0] + 25 + rng.uniform(-15, 15), ORIGIN[1] + 30 + rng.uniform(-15, 15), ORIGIN[2])
        death_at = (6.0, 9.0, 12.0)[index] if index < 3 else None
        add(proto.EntityKind.COMBAT_NPC, center, rng.uniform(3.0, 5.0), 2.5,
            death_at=death_at, despawn_at=None if death_at is None else death_at + 4.0, flags=combat_flags)
    for _ in range(12):
        center = (ORIGIN[0] + rng.uniform(-60, 60), ORIGIN[1] + rng.uniform(-60, 60), ORIGIN[2])
        add(proto.EntityKind.VEHICLE, center, rng.uniform(40.0, 130.0), rng.uniform(8.0, 14.0),
            flags=int(proto.EntityFlag.LIGHTS))
    for wave in range(8):
        for _ in range(2):
            center = (ORIGIN[0] + rng.uniform(-80, 80), ORIGIN[1] + rng.uniform(-80, 80), ORIGIN[2])
            start = 3.0 + 3.0 * wave
            add(proto.EntityKind.CROWD_NPC, center, rng.uniform(2.0, 5.0), rng.uniform(1.0, 1.6),
                spawn_at=start, despawn_at=start + 6.0)
    return specs


def alive(spec: EntitySpec, t: float) -> bool:
    return t >= spec.spawn_at and (spec.despawn_at is None or t < spec.despawn_at)


def _yaw_quat(yaw_degrees: float):
    half = math.radians(yaw_degrees) / 2.0
    return 0.0, 0.0, math.sin(half), math.cos(half)


def entity_truth(spec: EntitySpec, t: float) -> dict:
    dead = spec.death_at is not None and t >= spec.death_at
    t_move = min(t, spec.death_at) if spec.death_at is not None else t
    omega = spec.speed / spec.radius
    angle = spec.phase + omega * t_move
    pos = (spec.center[0] + spec.radius * math.cos(angle), spec.center[1] + spec.radius * math.sin(angle),
           spec.center[2])
    if spec.speed > 0.0 and not dead:
        vel = (-spec.radius * omega * math.sin(angle), spec.radius * omega * math.cos(angle), 0.0)
        yaw = yaw_from_forward(vel[0], vel[1])
    else:
        vel = (0.0, 0.0, 0.0)
        yaw = math.degrees(spec.phase) % 360.0
    flags = spec.flags | (int(proto.EntityFlag.DEAD) if dead else 0)
    if dead:
        move = proto.MoveState.DEAD
    elif spec.kind == proto.EntityKind.VEHICLE:
        move = proto.MoveState.VEHICLE
    elif spec.speed > 2.0:
        move = proto.MoveState.RUN
    elif spec.speed > 0.0:
        move = proto.MoveState.WALK
    else:
        move = proto.MoveState.IDLE
    return {"pos": pos, "vel": vel, "yaw": yaw, "quat": _yaw_quat(yaw), "move": int(move), "flags": flags,
            "health": 0 if dead else 255}


def player_truth(role: int, t: float) -> dict:
    """t = relay time in seconds. Host runs a 10 m circle, the joiner drives an ellipse."""
    if role == proto.Role.HOST:
        omega = HOST_SPEED_MPS / HOST_RADIUS_M
        angle = omega * t
        pos = (ORIGIN[0] + HOST_RADIUS_M * math.cos(angle), ORIGIN[1] + HOST_RADIUS_M * math.sin(angle), ORIGIN[2])
        vel = (-HOST_RADIUS_M * omega * math.sin(angle), HOST_RADIUS_M * omega * math.cos(angle), 0.0)
        driving = False
    else:
        omega = 2.0 * math.pi / JOINER_PERIOD_S
        angle = omega * t
        a, b = JOINER_AXES_M
        center = (ORIGIN[0] + JOINER_CENTER_OFFSET[0], ORIGIN[1] + JOINER_CENTER_OFFSET[1])
        pos = (center[0] + a * math.cos(angle), center[1] + b * math.sin(angle), ORIGIN[2] + 2.0 * math.sin(2 * angle))
        vel = (-a * omega * math.sin(angle), b * omega * math.cos(angle), 4.0 * omega * math.cos(2 * angle))
        driving = True
    yaw = yaw_from_forward(vel[0], vel[1])
    return {"pos": pos, "vel": vel, "yaw": yaw, "quat": _yaw_quat(yaw), "driving": driving}
