"""Opt-in, headless single-vehicle authority policy over the existing v2 wire.

Design adapted from Bukczyk/CP2077-Coop SessionRegistry at 20eb125: authenticated
member binding, host-only committed state, intent/result separation, epochs,
mutation-free rejection and ordered event IDs. No source or CPS1 codec is copied.
This module owns metadata, not Cyberpunk entities, mounting, AI or physics.
"""
from __future__ import annotations

import copy
import math
import re
import secrets
from dataclasses import dataclass, field

PREFIX = "C3A1"
CONTROL, POSE = 21, 3
MAX_TEXT, MAX_ROTATIONS = 512, 64
MAX_ID, MAX_EVENT = 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF
REQUEST_TIMEOUT = 3.0
NONCE = re.compile(r"[0-9a-f]{32}\Z")
RECORD = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")


class Rejected(ValueError):
    pass


def number(text: str, maximum=MAX_ID, minimum=0) -> int:
    if not text.isascii() or not text.isdecimal() or len(text) > 20:
        raise Rejected("number")
    value = int(text)
    if str(value) != text or not minimum <= value <= maximum:
        raise Rejected("number")
    return value


def pose(fields: list[str]) -> tuple[float, float, float, float]:
    if len(fields) != 4:
        raise Rejected("shape")
    try:
        values = tuple(float(value) for value in fields)
    except ValueError:
        raise Rejected("pose") from None
    if any(not math.isfinite(value) or abs(value) > limit
           for value, limit in zip(values, (20000, 20000, 5000, 180))):
        raise Rejected("pose")
    return values


def newer(candidate: int, previous: int) -> bool:
    distance = (candidate - previous) & MAX_ID
    return 0 < distance < 0x80000000


@dataclass(frozen=True)
class Identity:
    peer: int
    token: int  # relay-owned membership identity; never serialized in this protocol
    host: bool


@dataclass
class Member:
    identity: Identity
    nonce: str
    generation: int
    ready: bool = False
    event: int = 0
    used_nonces: set[str] = field(default_factory=set)


@dataclass
class Request:
    identity: Identity
    nonce: str
    generation: int
    event: int
    action: str
    revision: int
    expires: float


@dataclass
class Vehicle:
    entity: int
    record: str
    transform: tuple[float, float, float, float]
    driver: int = 0
    seat_generation: int = 0
    sequence: int | None = None
    sample_ms: int = 0
    last_pose: float | None = None


@dataclass(frozen=True)
class Effect:
    target: Identity
    channel: int
    text: str


@dataclass
class Plan:
    state: "Authority"
    effects: list[Effect]
    verdict: str


class Authority:
    """One bounded policy per relay room. Plan first; commit only after queue preflight."""

    def __init__(self, epoch_factory=None):
        self.epoch_factory = epoch_factory or (lambda: secrets.token_hex(16))
        self.epoch = ""
        self.host: Identity | None = None
        self.host_nonces: set[str] = set()
        self.members: dict[int, Member] = {}
        self.requests: dict[int, Request] = {}  # at most one per admitted member
        self.vehicle: Vehicle | None = None
        self.revision = 0
        self.last_entity = 0
        self.next_member = 1
        self.last_now: float | None = None

    def plan(self, identity: Identity, channel: int, text: str, now: float, relay_ms: int) -> Plan:
        state = copy.deepcopy(self)
        try:
            if not math.isfinite(now) or (self.last_now is not None and now < self.last_now):
                raise Rejected("clock")
            if not isinstance(text, str) or len(text) > MAX_TEXT or any(ord(c) < 32 or ord(c) > 126 for c in text):
                raise Rejected("text")
            fields = text.split("|")
            if len(fields) < 2 or fields[0] != PREFIX or channel not in (CONTROL, POSE):
                raise Rejected("shape")
            effects, verdict = state._apply(identity, channel, fields[1:], now, relay_ms)
            state.last_now = now
            return Plan(state, effects, verdict)
        except Rejected as error:
            # No policy state, including event watermarks or liveness, is committed.
            member = self.members.get(identity.peer)
            if member and member.identity == identity:
                effect = self._result(member, 0, "reject", str(error))
                return Plan(self, [effect], "rejected")
            return Plan(self, [], "rejected")

    def _effect(self, member: Member, kind: str, *values, channel=CONTROL) -> Effect:
        text = "|".join(map(str, (PREFIX, kind, self.epoch, member.nonce, *values)))
        assert len(text) <= MAX_TEXT and text.isascii()
        return Effect(member.identity, channel, text)

    def _result(self, member: Member, event: int, verdict: str, reason: str) -> Effect:
        return self._effect(member, "RESULT", event, verdict, reason, self.revision)

    def _state(self, member: Member) -> Effect:
        vehicle = self.vehicle
        if vehicle:
            values = (vehicle.entity, vehicle.record, *(format(v, ".9g") for v in vehicle.transform),
                      vehicle.driver, vehicle.seat_generation,
                      vehicle.sequence if vehicle.sequence is not None else 0, vehicle.sample_ms)
        else:
            values = (0, "-", 0, 0, 0, 0, 0, 0, 0, 0)
        return self._effect(member, "STATE", self.revision, self.host.peer if self.host else 0,
                            member.generation, *values)

    def _states(self) -> list[Effect]:
        return [self._state(member) for member in self.members.values()]

    def _bump(self):
        if self.revision == MAX_ID:
            raise Rejected("epoch_exhausted")
        self.revision += 1

    def _member(self, identity: Identity, epoch: str, nonce: str) -> Member:
        member = self.members.get(identity.peer)
        if not self.epoch or epoch != self.epoch:
            raise Rejected("epoch")
        if not member or member.identity != identity or member.nonce != nonce:
            raise Rejected("membership")
        return member

    def _apply(self, identity: Identity, channel: int, fields: list[str], now: float, relay_ms: int):
        verb = fields[0]
        if verb == "DISCOVER":
            if channel != CONTROL or len(fields) != 2 or identity.host or not NONCE.fullmatch(fields[1]):
                raise Rejected("discover")
            if not self.epoch:
                raise Rejected("no_epoch")
            # Offer carries only public experiment context, bound to the caller's
            # activation challenge. It does not register or admit the member.
            candidate = Member(identity, fields[1], 0)
            return [self._effect(candidate, "OFFER", self.revision, self.host.peer)], "offer"
        if verb == "BEGIN":
            if channel != CONTROL or len(fields) != 2 or not identity.host or not NONCE.fullmatch(fields[1]):
                raise Rejected("begin")
            nonce = fields[1]
            if self.host and self.host != identity:
                raise Rejected("host")
            current = self.members.get(identity.peer)
            if current and current.nonce == nonce:
                return [self._state(current)], "duplicate"
            if nonce in self.host_nonces:
                raise Rejected("old_nonce")
            if len(self.host_nonces) >= MAX_ROTATIONS:
                raise Rejected("rotation_limit")
            effects = [self._effect(m, "END", "reset") for m in self.members.values()]
            self.host_nonces.add(nonce)
            self.epoch, self.host = self.epoch_factory(), identity
            if not NONCE.fullmatch(self.epoch):
                raise Rejected("epoch_factory")
            self.members = {identity.peer: Member(identity, nonce, 1, True)}
            self.requests.clear()
            self.vehicle, self.revision, self.last_entity = None, 0, 0
            self.next_member = 1
            effects.append(self._state(self.members[identity.peer]))
            return effects, "accepted"
        if verb == "JOIN":
            if channel != CONTROL or len(fields) != 3 or identity.host or not NONCE.fullmatch(fields[2]):
                raise Rejected("join")
            if not self.epoch or fields[1] != self.epoch:
                raise Rejected("epoch")
            member = self.members.get(identity.peer)
            if member and member.identity != identity:
                raise Rejected("membership")
            if member and member.nonce == fields[2]:
                return [self._state(member)], "duplicate"
            used = member.used_nonces | {member.nonce} if member else set()
            if fields[2] in used:
                raise Rejected("old_nonce")
            if len(used) >= MAX_ROTATIONS:
                raise Rejected("rotation_limit")
            # The relay limits room membership; enforce an independent bound too.
            if not member and len(self.members) >= 8:
                raise Rejected("members_full")
            if self.next_member == MAX_ID:
                raise Rejected("epoch_exhausted")
            self.requests.pop(identity.peer, None)
            self._clear_driver(identity.peer)
            self.next_member += 1
            self.members[identity.peer] = Member(identity, fields[2], self.next_member, used_nonces=used)
            return self._states(), "accepted"
        if len(fields) < 4:
            raise Rejected("shape")
        member = self._member(identity, fields[1], fields[2])
        if verb == "READY":
            if channel != CONTROL or len(fields) != 4:
                raise Rejected("shape")
            revision = number(fields[3])
            if revision != self.revision:
                # A baseline raced a committed control change. Refresh without admitting.
                return [self._state(member)], "refresh"
            member.ready = True
            return [self._effect(member, "READY_OK", self.revision)], "accepted"
        if not member.ready:
            raise Rejected("not_ready")
        if channel == POSE:
            return self._pose(identity, member, fields, now, relay_ms)
        event = number(fields[3], MAX_EVENT, 1)
        # Known IDs never reapply a command; replies contain the current baseline.
        args = fields[4:]
        expected = {"SPAWN": 7, "DESPAWN": 2, "REQUEST": 3, "GRANT": 5,
                    "RELEASE": 5, "REVOKE": 2}
        if verb not in expected or len(args) != expected[verb]:
            raise Rejected("shape")
        if verb != "REQUEST" and not identity.host:
            raise Rejected("authority")
        if event <= member.event:
            return [self._result(member, event, "duplicate", "already_accepted"), self._state(member)], "duplicate"
        if event != member.event + 1:
            raise Rejected("event_gap")
        entity, revision = number(args[0], minimum=1), number(args[1])
        if revision != self.revision:
            raise Rejected("revision")
        effects = []
        if verb == "SPAWN":
            if self.vehicle or entity <= self.last_entity or not RECORD.fullmatch(args[2]):
                raise Rejected("spawn")
            value = pose(args[3:])
            self._bump()
            self.vehicle = Vehicle(entity, args[2], value)
            self.last_entity = entity
        else:
            if not self.vehicle or self.vehicle.entity != entity:
                raise Rejected("entity")
            if verb == "REQUEST":
                action = args[2]
                if action not in ("enter", "exit"):
                    raise Rejected("action")
                pending = self.requests.get(identity.peer)
                if pending and pending.expires > now:
                    raise Rejected("request_pending")
                if action == "enter" and self.vehicle.driver:
                    raise Rejected("occupied")
                if action == "exit" and self.vehicle.driver != identity.peer:
                    raise Rejected("not_driver")
                self.requests[identity.peer] = Request(identity, member.nonce, member.generation, event, action,
                                                       self.revision, now + REQUEST_TIMEOUT)
                host = self.members[self.host.peer]
                effects.append(self._effect(host, "INTENT", identity.peer, event, member.generation,
                                            action, entity, self.revision))
            elif verb in ("GRANT", "RELEASE"):
                requester, request_event = number(args[2], minimum=1), number(args[3], MAX_EVENT, 1)
                request_generation = number(args[4], minimum=1)
                request = self.requests.get(requester)
                owner = self.members.get(requester)
                if not request or not owner or request.identity != owner.identity or request.nonce != owner.nonce \
                        or request.generation != owner.generation or request.generation != request_generation \
                        or request.event != request_event or request.expires <= now or request.revision != self.revision:
                    raise Rejected("request")
                action = "enter" if verb == "GRANT" else "exit"
                if request.action != action or (verb == "GRANT" and self.vehicle.driver) \
                        or (verb == "RELEASE" and self.vehicle.driver != requester):
                    raise Rejected("seat")
                self._bump()
                self.vehicle.driver = requester if verb == "GRANT" else 0
                self.vehicle.seat_generation += 1
                self.requests.clear()  # all other requests refer to the previous revision
            elif verb == "REVOKE":
                if not self.vehicle.driver:
                    raise Rejected("empty_seat")
                self._bump()
                self.vehicle.driver = 0
                self.vehicle.seat_generation += 1
                self.requests.clear()
            else:  # DESPAWN
                if self.vehicle.driver:
                    raise Rejected("occupied")
                self._bump()
                self.vehicle = None
                self.requests.clear()
        member.event = event
        effects.append(self._result(member, event, "ok", verb.lower()))
        if verb != "REQUEST":
            effects.extend(self._states())
        return effects, "accepted"

    def _pose(self, identity, member, fields, now, relay_ms):
        if fields[0] != "POSE" or len(fields) != 10 or not identity.host:
            raise Rejected("authority")
        entity = number(fields[3], minimum=1)
        sequence, sample_ms = number(fields[4]), number(fields[5])
        value = pose(fields[6:])
        vehicle = self.vehicle
        if not vehicle or vehicle.entity != entity:
            raise Rejected("entity")
        if vehicle.sequence is not None and not newer(sequence, vehicle.sequence):
            raise Rejected("stale_sequence")
        age = ((relay_ms - sample_ms + 0x80000000) & MAX_ID) - 0x80000000
        if age < -250 or age > 2000:
            raise Rejected("sample_time")
        if vehicle.last_pose is not None and now - vehicle.last_pose < 0.099999:
            raise Rejected("pose_rate")
        vehicle.transform, vehicle.sequence, vehicle.sample_ms, vehicle.last_pose = value, sequence, sample_ms, now
        return [self._effect(m, "POSE", entity, sequence, sample_ms,
                             *(format(v, ".9g") for v in value), channel=POSE)
                for m in self.members.values() if m.ready and m.identity != identity], "accepted"

    def _clear_driver(self, peer: int):
        if self.vehicle and self.vehicle.driver == peer:
            self._bump()
            self.vehicle.driver = 0
            self.vehicle.seat_generation += 1
            self.requests.clear()

    def remove(self, identity: Identity) -> list[Effect]:
        """Membership teardown is unconditional, including after output overload."""
        member = self.members.get(identity.peer)
        if not member or member.identity != identity:
            return []
        if identity == self.host:
            effects = [self._effect(m, "END", "host_left") for m in self.members.values() if m.identity != identity]
            self.__init__(self.epoch_factory)
            return effects
        self.members.pop(identity.peer)
        self.requests.pop(identity.peer, None)
        # Revocation must remain possible at counter exhaustion: close the experiment.
        if self.revision == MAX_ID and self.vehicle and self.vehicle.driver == identity.peer:
            effects = [self._effect(m, "END", "epoch_exhausted") for m in self.members.values()]
            self.__init__(self.epoch_factory)
            return effects
        self._clear_driver(identity.peer)
        return self._states()

    def expire(self, now: float):
        self.requests = {peer: request for peer, request in self.requests.items() if request.expires > now}
