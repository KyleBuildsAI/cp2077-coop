"""Entity snapshots: quantized state, delta compression against acked baselines,
interest management and a priority accumulator.

Delta model (Quake 3 / Fiedler style, end to end between host and receiver):

* The host numbers snapshots with ``tick`` (10 Hz) and remembers, per tick,
  the *receiver view* it produced: what a receiver holding that snapshot
  reconstructs, including entities whose updates were deferred.
* Each new snapshot is encoded against the newest tick the receiver confirmed
  with SNAPSHOT_ACK (``baseline``), or against nothing (``baseline = 0``).
  Entities absent from the record list are unchanged since the baseline;
  removals are explicit. A lost snapshot therefore never breaks later ones,
  and quantization never accumulates drift because deltas are taken between
  already-quantized states.
* A byte budget (default 1000 bytes) bounds every snapshot; entities are sent
  in priority-accumulator order and unsent changes simply wait.
* ``world_id`` (protocol minor 1, ``X_WORLD_ID``) is the static EntityID hash of
  a placed NPC, 0 for dynamic ones. It is part of the identity, so it travels
  with the spawn block and a change re-sends the spawn.
"""
from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass

from . import proto

HISTORY_TICKS = 64
DEFAULT_BUDGET = 1000
DEFAULT_VEL = (0, 0, 0)
DEFAULT_STATE = (0, 0, 255)
CODEC = proto.EntitySnapshotCodec()


@dataclass(frozen=True)
class EntityState:
    kind: int
    spawn_flags: int
    attitude: int
    record: int
    appearance: int
    pos: tuple          # millimetres
    rot: int            # yaw u16, or smallest-three quaternion u32 for vehicles
    vel: tuple = DEFAULT_VEL     # cm/s
    state: tuple = DEFAULT_STATE  # move_state, flags, health
    target: int = 0
    weapon: int = 0
    world_id: int = 0   # u64 static world id (minor 1), 0 = dynamic entity

    @property
    def uses_quat(self) -> bool:
        return self.kind == proto.EntityKind.VEHICLE

    def identity(self) -> tuple:
        return self.kind, self.spawn_flags, self.attitude, self.record, self.appearance, self.world_id


def quantize(kind, spawn_flags, attitude, record, appearance, pos_m, rotation, vel_mps,
             move_state=0, flags=0, health=255, target=0, weapon=0, world_id=0) -> EntityState:
    """``rotation`` is yaw degrees, or an (x, y, z, w) quaternion for vehicles."""
    if kind == proto.EntityKind.VEHICLE:
        rot = proto.pack_quat(*rotation)
    else:
        rot = proto.yaw_to_u16(rotation)
    return EntityState(kind, spawn_flags, attitude, record, appearance,
                       tuple(proto.meters_to_mm(v) for v in pos_m), rot,
                       tuple(proto.velocity_to_cms(v) for v in vel_mps),
                       (move_state, flags, health), target, weapon, world_id)


def make_record(net_id: int, old: EntityState | None, new: EntityState):
    """Smallest record turning ``old`` (baseline view) into ``new``; None when unchanged."""
    record = {"net_id": net_id}
    rot_key = "quat" if new.uses_quat else "yaw"
    if old is None or old.identity() != new.identity():
        record["spawn"] = {"kind": new.kind, "spawn_flags": new.spawn_flags, "attitude": new.attitude,
                           "record": new.record, "appearance": new.appearance}
        record["pos"] = new.pos
        record[rot_key] = new.rot
        if new.vel != DEFAULT_VEL:
            record["vel"] = new.vel
        if new.state != DEFAULT_STATE:
            record["state"] = new.state
        if new.target:
            record["target"] = new.target
        if new.weapon:
            record["weapon"] = new.weapon
        if new.world_id:
            record["world_id"] = new.world_id
        return record
    if new.pos != old.pos:
        delta = tuple(n - o for n, o in zip(new.pos, old.pos))
        if all(-32768 <= d <= 32767 for d in delta):
            record["pos_delta"] = delta
        else:
            record["pos"] = new.pos
    if new.rot != old.rot:
        record[rot_key] = new.rot
    if new.vel != old.vel:
        record["vel"] = new.vel
    if new.state != old.state:
        record["state"] = new.state
    if new.target != old.target:
        record["target"] = new.target
    if new.weapon != old.weapon:
        record["weapon"] = new.weapon
    return record if len(record) > 1 else None


def apply_record(old: EntityState | None, record: dict) -> EntityState:
    if "spawn" in record:
        spawn = record["spawn"]
        rot = record.get("quat", record.get("yaw"))
        return EntityState(spawn["kind"], spawn["spawn_flags"], spawn["attitude"], spawn["record"],
                           spawn["appearance"], tuple(record["pos"]), rot,
                           tuple(record.get("vel", DEFAULT_VEL)), tuple(record.get("state", DEFAULT_STATE)),
                           record.get("target", 0), record.get("weapon", 0), record.get("world_id", 0))
    if old is None:
        raise proto.ProtocolError(f"delta for unknown entity {record['net_id']}")
    pos = old.pos
    if "pos" in record:
        pos = tuple(record["pos"])
    elif "pos_delta" in record:
        pos = tuple(o + d for o, d in zip(old.pos, record["pos_delta"]))
    rot = old.rot
    if "yaw" in record or "quat" in record:
        if ("quat" in record) != old.uses_quat:
            raise proto.ProtocolError("rotation type does not match entity kind")
        rot = record.get("quat", record.get("yaw"))
    return EntityState(old.kind, old.spawn_flags, old.attitude, old.record, old.appearance, pos, rot,
                       tuple(record.get("vel", old.vel)), tuple(record.get("state", old.state)),
                       record.get("target", old.target), record.get("weapon", old.weapon),
                       record.get("world_id", old.world_id))


VIEW_HASH_LAYOUT = struct.Struct("<HBBBQQiiiIhhhBBBHQQ")


def view_hash(view: dict) -> str:
    """Canonical digest of a receiver view (plain integers, so enum vs int never matters)."""
    digest = hashlib.sha256()
    for net_id in sorted(view):
        s = view[net_id]
        digest.update(VIEW_HASH_LAYOUT.pack(net_id, int(s.kind), int(s.spawn_flags), int(s.attitude), int(s.record),
                                            int(s.appearance), *(int(v) for v in s.pos), int(s.rot),
                                            *(int(v) for v in s.vel), *(int(v) for v in s.state), int(s.target),
                                            int(s.weapon), int(s.world_id)))
    return digest.hexdigest()[:16]


class DeltaEncoder:
    def __init__(self, budget_bytes: int = DEFAULT_BUDGET):
        self.budget = budget_bytes
        self.tick = 0
        self.acked = 0
        self.history: dict[int, dict] = {}
        self.priority: dict[int, float] = {}
        self.stats = {"snapshots": 0, "bytes": 0, "full_bytes": 0, "records": 0, "deferred": 0,
                      "full_snapshots": 0, "removals": 0, "spawns": 0}

    def on_ack(self, tick: int) -> None:
        if self.acked < tick <= self.tick and tick in self.history:
            self.acked = tick

    def encode(self, sample_time: int, states: dict, weights: dict | None = None):
        """states: {net_id: EntityState} currently relevant to the receiver.

        Returns (body, tick, baseline, view).
        """
        self.tick += 1
        baseline = self.acked if self.acked in self.history else 0
        base_view = self.history.get(baseline, {}) if baseline else {}
        view = dict(base_view)
        records = []
        used = proto.ENTITY_HEADER.size
        for net_id in base_view:
            if net_id not in states:
                records.append({"net_id": net_id, "remove": True})
                used += CODEC.record_size(records[-1])
                del view[net_id]
                self.stats["removals"] += 1
        weights = weights or {}
        for net_id in states:
            self.priority[net_id] = self.priority.get(net_id, 0.0) + weights.get(net_id, 1.0)
        for net_id in [n for n in self.priority if n not in states]:
            del self.priority[net_id]
        for net_id in sorted(states, key=lambda n: (-self.priority[n], n)):
            record = make_record(net_id, base_view.get(net_id), states[net_id])
            if record is None:
                self.priority[net_id] = 0.0
                continue
            size = CODEC.record_size(record)
            if used + size > self.budget or len(records) >= proto.MAX_ENTITY_RECORDS:
                self.stats["deferred"] += 1
                continue
            records.append(record)
            used += size
            view[net_id] = states[net_id]
            self.priority[net_id] = 0.0
            if "spawn" in record:
                self.stats["spawns"] += 1
        body = CODEC.encode({"tick": self.tick, "baseline": baseline, "sample_time": sample_time & 0xFFFFFFFF,
                             "records": records})
        self.history[self.tick] = view
        for old_tick in [t for t in self.history if t <= self.tick - HISTORY_TICKS and t != self.acked]:
            del self.history[old_tick]
        self.stats["snapshots"] += 1
        self.stats["bytes"] += len(body)
        self.stats["records"] += len(records)
        self.stats["full_bytes"] += self.full_size(states)
        if baseline == 0:
            self.stats["full_snapshots"] += 1
        return body, self.tick, baseline, view

    @staticmethod
    def full_size(states: dict) -> int:
        """Bytes the same entities would cost without delta compression (for stats)."""
        return proto.ENTITY_HEADER.size + sum(CODEC.record_size(make_record(n, None, s)) for n, s in states.items())


class DeltaDecoder:
    def __init__(self):
        self.history: dict[int, dict] = {}
        self.latest = 0
        self.stats = {"decoded": 0, "duplicate": 0, "missing_baseline": 0, "inconsistent": 0, "stale": 0}

    def apply(self, snapshot: dict):
        """Returns the reconstructed view {net_id: EntityState} or None if unusable."""
        tick = snapshot["tick"]
        baseline = snapshot["baseline"]
        if tick in self.history:
            self.stats["duplicate"] += 1
            return None
        if tick <= self.latest - HISTORY_TICKS:
            self.stats["stale"] += 1
            return None
        if baseline == 0:
            base = {}
        elif baseline in self.history:
            base = self.history[baseline]
        else:
            self.stats["missing_baseline"] += 1
            return None
        view = dict(base)
        try:
            for record in snapshot["records"]:
                net_id = record["net_id"]
                if record.get("remove"):
                    view.pop(net_id, None)
                    continue
                state = apply_record(base.get(net_id), record)
                check = [proto.mm_to_meters(v) for v in state.pos]
                proto.check_world_coordinates(*check, what="entity")
                view[net_id] = state
        except proto.ProtocolError:
            self.stats["inconsistent"] += 1
            return None
        self.history[tick] = view
        self.latest = max(self.latest, tick)
        for old_tick in [t for t in self.history if t <= self.latest - HISTORY_TICKS]:
            del self.history[old_tick]
        self.stats["decoded"] += 1
        return view


class InterestManager:
    """Which host entities a receiver gets, and how urgently (priority weights)."""

    KIND_WEIGHT = {
        proto.EntityKind.CROWD_NPC: 1.0,
        proto.EntityKind.COMBAT_NPC: 3.0,
        proto.EntityKind.QUEST_NPC: 2.0,
        proto.EntityKind.VEHICLE: 2.0,
        proto.EntityKind.DEVICE: 0.5,
    }

    def __init__(self, npc_radius_m: float = 100.0, vehicle_radius_m: float = 200.0,
                 hysteresis_m: float = 15.0, max_entities: int = 128):
        self.npc_radius = npc_radius_m
        self.vehicle_radius = vehicle_radius_m
        self.hysteresis = hysteresis_m
        self.max_entities = max_entities
        self.relevant: set[int] = set()

    def update(self, viewer_pos, entities: dict):
        """entities: {net_id: (kind, pos_m, always_relevant)}. Returns (net_ids, weights)."""
        candidates = []
        for net_id, (kind, pos, forced) in entities.items():
            dist = math.dist(viewer_pos, pos) if viewer_pos is not None else 0.0
            radius = self.vehicle_radius if kind == proto.EntityKind.VEHICLE else self.npc_radius
            if net_id in self.relevant:
                radius += self.hysteresis
            if forced or dist <= radius:
                weight = self.KIND_WEIGHT.get(kind, 1.0) * (1.0 + 20.0 / (20.0 + dist))
                candidates.append((0 if forced else 1, dist, net_id, weight))
        candidates.sort()
        chosen = candidates[:self.max_entities]
        self.relevant = {net_id for _, _, net_id, _ in chosen}
        return self.relevant, {net_id: weight for _, _, net_id, weight in chosen}
