"""Reference implementation of the CP2077Coop NPC sync text format v1.

Mirrors npc_common.reds byte for byte (quantization, anchor rounding, chunking)
so message sizes, bandwidth and decoding can be tested without the game, and so
a relay or monitor can read the traffic.

Messages (fields split by one space; NS1 entries by ';', entry fields by ','):

    NS1 <seq> <hostMs> <anchorX> <anchorY> <anchorZ> <part> <parts> <entries>   unreliable, 10 Hz
        entry = netId,dxCm,dyCm,dzCm,yaw16,vxCms,vyCms,moveState,flags,health,target
    NB1 <netId> <kind> <spawnFlags> <attitude> <recHash> <recLen> <appearance> <worldId> <xCm> <yCm> <zCm> <yaw16>
    NU1 <netId> <reason>              reason: 0 out of range, 1 despawned
    ND1 <netId> <killer> <flags>      flags: 1 ragdoll, 2 skip death animation
    NI1 <seq> <cxM> <cyM> <czM> <radiusM> <netId;netId;...>                       unreliable, 1 Hz
    NH1 <netId> <damageX100> <attackType> <hitFlags>                               joiner -> host
    PD1 <attacker> <damageX100> <attackType>                                       host -> joiner

Enum values are those of relay/coopnet/proto.py (EntityKind, EntityFlag,
SpawnFlag, MoveState); flags above 0xFF and SpawnFlag 0x10 are text extensions.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

MAX_PAYLOAD_BYTES = 1100        # npc_common.reds CP2077CoopNpc_MaxPayloadBytes
HEADER_RESERVE = 65             # bytes reserved for the NS1 header when chunking
CPN2_HEADER_BYTES = 20          # src/core/Protocol.hpp kHeaderSize
UDP_IPV4_OVERHEAD = 28
CPN2_MAX_PAYLOAD = 1180         # kMaxDatagramSize 1200 - kHeaderSize 20

# proto.py EntityKind
KIND_CROWD_NPC, KIND_COMBAT_NPC, KIND_QUEST_NPC, KIND_VEHICLE = 1, 2, 3, 4
# proto.py MoveState (subset used by NPCs)
MOVE_IDLE, MOVE_WALK, MOVE_RUN, MOVE_SPRINT, MOVE_CROUCH_IDLE, MOVE_CROUCH_MOVE = 0, 1, 2, 3, 4, 5
MOVE_VEHICLE, MOVE_DEAD = 10, 11
# proto.py EntityFlag + text extensions
FLAG_DEAD, FLAG_COMBAT, FLAG_WEAPON, FLAG_CROUCHED, FLAG_RAGDOLL, FLAG_HOSTILE = 1, 2, 4, 8, 16, 32
FLAG_DEFEATED, FLAG_WORKSPOT, FLAG_MOUNTED, FLAG_TELEPORTED = 256, 512, 1024, 2048
# SpawnFlag
SPAWN_CROWD, SPAWN_TRAFFIC, SPAWN_QUEST, SPAWN_PERSISTENT, SPAWN_STATIC_ID = 1, 2, 4, 8, 16
TARGET_NONE, TARGET_JOINER, TARGET_HOST = 0, 65534, 65535
DYNAMIC_ID_UPPER_BOUND = 0xFFFFFF  # RED4ext ent::EntityID::DynamicUpperBound


def round_f(value: float) -> int:
    """RoundF in the game (C roundf: halves away from zero)."""
    return int(math.floor(abs(value) + 0.5)) * (1 if value >= 0 else -1)


def floor_f(value: float) -> int:
    return int(math.floor(value))


def metres_to_cm(value: float) -> int:
    return round_f(value * 100.0)


def cm_to_metres(value: int) -> float:
    return value / 100.0


def yaw_to_u16(yaw: float) -> int:
    turns = yaw / 360.0
    turns -= floor_f(turns)
    return round_f(turns * 65536.0) % 65536


def u16_to_yaw(value: int) -> float:
    yaw = value * 360.0 / 65536.0
    return yaw - 360.0 if yaw > 180.0 else yaw


def health_to_byte(health: float) -> int:
    return max(0, min(255, round_f(health * 255.0)))


def is_static_id(entity_hash: int) -> bool:
    return entity_hash > DYNAMIC_ID_UPPER_BOUND


@dataclass
class NpcState:
    net_id: int
    position: tuple            # metres (x, y, z)
    yaw: float = 0.0           # degrees
    velocity: tuple = (0.0, 0.0)
    move_state: int = MOVE_IDLE
    flags: int = 0
    health: float = 1.0
    target: int = TARGET_NONE
    # bind-only fields
    kind: int = KIND_CROWD_NPC
    spawn_flags: int = 0
    attitude: int = 1
    record_hash: int = 0
    record_length: int = 0
    appearance: int = 0
    world_id: int = 0


def encode_entry(state: NpcState, anchor: tuple) -> str:
    x, y, z = state.position
    ax, ay, az = anchor
    return ",".join(str(v) for v in (
        state.net_id,
        metres_to_cm(x - ax), metres_to_cm(y - ay), metres_to_cm(z - az),
        yaw_to_u16(state.yaw),
        metres_to_cm(state.velocity[0]), metres_to_cm(state.velocity[1]),
        state.move_state, state.flags, health_to_byte(state.health), state.target,
    ))


def encode_snapshot(states: list, anchor_in: tuple, seq: int, host_ms: int) -> list:
    """Same chunking as CP2077CoopNpc_EncodeSnapshot."""
    anchor = tuple(float(round_f(v)) for v in anchor_in)
    chunks, current = [], ""
    for state in states:
        entry = encode_entry(state, anchor)
        if current and len(current) + len(entry) + HEADER_RESERVE > MAX_PAYLOAD_BYTES:
            chunks.append(current)
            current = ""
        current = current + ";" + entry if current else entry
    if current or not chunks:
        chunks.append(current)
    header = f"{seq} {host_ms} {round_f(anchor[0])} {round_f(anchor[1])} {round_f(anchor[2])}"
    return [f"NS1 {header} {part} {len(chunks)} {chunk}" for part, chunk in enumerate(chunks)]


def decode_snapshot(message: str):
    """Same as CP2077CoopNpc_DecodeSnapshot: returns (seq, part, parts, states) or None."""
    fields = message.split(" ")
    if len(fields) < 8 or fields[0] != "NS1":
        return None
    seq = int(fields[1])
    anchor = (float(int(fields[3])), float(int(fields[4])), float(int(fields[5])))
    part, parts = int(fields[6]), int(fields[7])
    states = []
    if len(fields) >= 9 and fields[8]:
        for text in fields[8].split(";"):
            values = text.split(",")
            if len(values) != 11:
                continue
            v = [int(item) for item in values]
            if v[0] <= 0:
                continue
            states.append(NpcState(
                net_id=v[0],
                position=(anchor[0] + cm_to_metres(v[1]), anchor[1] + cm_to_metres(v[2]), anchor[2] + cm_to_metres(v[3])),
                yaw=u16_to_yaw(v[4]),
                velocity=(cm_to_metres(v[5]), cm_to_metres(v[6])),
                move_state=v[7], flags=v[8], health=v[9] / 255.0, target=v[10],
            ))
    return seq, part, parts, states


def encode_bind(state: NpcState) -> str:
    x, y, z = state.position
    return " ".join(str(v) for v in (
        "NB1", state.net_id, state.kind, state.spawn_flags, state.attitude,
        state.record_hash, state.record_length, state.appearance, state.world_id,
        metres_to_cm(x), metres_to_cm(y), metres_to_cm(z), yaw_to_u16(state.yaw),
    ))


def decode_bind(message: str) -> NpcState | None:
    fields = message.split(" ")
    if len(fields) != 13 or fields[0] != "NB1":
        return None
    v = [int(item) for item in fields[1:]]
    return NpcState(
        net_id=v[0], kind=v[1], spawn_flags=v[2], attitude=v[3], record_hash=v[4], record_length=v[5],
        appearance=v[6], world_id=v[7], position=(cm_to_metres(v[8]), cm_to_metres(v[9]), cm_to_metres(v[10])),
        yaw=u16_to_yaw(v[11]),
    )


def lua_record_parts(message: str) -> tuple:
    """What the joiner's Lua extracts from NB1 to build TweakDBID.new(hash, length)."""
    fields = message.split(" ")
    return int(fields[5]), int(fields[6])


def wire_bytes(payload: str) -> int:
    """Bytes on the wire for one CPN2 datagram carrying this payload."""
    return len(payload.encode("ascii")) + CPN2_HEADER_BYTES + UDP_IPV4_OVERHEAD


@dataclass
class HostThrottle:
    """Python twin of CP2077CoopNpcHost.NeedsSend (idle NPCs resent every 0.5 s)."""
    idle_resend: float = 0.5
    last_sent: dict = field(default_factory=dict)   # net_id -> (time, position, flags)

    def needs_send(self, state: NpcState, now: float) -> bool:
        if state.move_state not in (MOVE_IDLE, MOVE_CROUCH_IDLE):
            return True
        previous = self.last_sent.get(state.net_id)
        if previous is None:
            return True
        time, position, flags = previous
        if flags != state.flags or math.dist(position, state.position) > 0.05:
            return True
        return now - time >= self.idle_resend

    def select(self, states: list, now: float) -> list:
        chosen = [s for s in states if self.needs_send(s, now)]
        for s in chosen:
            self.last_sent[s.net_id] = (now, s.position, s.flags)
        return chosen
