"""CP2077 Coop relay, protocol v2 (with v1 CP1/RP1 coexistence on the same port).

v2 clients: stateless-cookie handshake (HELLO -> CHALLENGE -> AUTH -> WELCOME),
rooms with a password hash, host/joiner roles, per-hop reliable + unreliable
channels, per-connection rate limits, strict input validation, state cache
for late joiners, NAT rebinding by session token.

v1 clients (Jakub's CP2077Coop.dll): exactly the old behaviour (WELCOME,<id>
and RP1 forwarding between v1 clients). Optionally a v2 room named by
--legacy-room and created with the LEGACY_BRIDGE flag is bridged with them,
translating PLAYER_SNAPSHOT <-> CP1/RP1 so players can migrate one at a time.

Usage:
    python relay_v2.py                                  # 127.0.0.1:11778
    python relay_v2.py --host 0.0.0.0 --port 11778      # public VPS
    python relay_v2.py --latency-ms 80 --jitter-ms 20 --loss-pct 2
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import select
import socket
import sys
import time

from coopnet import legacy, proto
from coopnet.linksim import LinkSim
from coopnet.ratelimit import Limits, TokenBucket
from coopnet.reliability import SEQ_SPACE, Connection, seq_diff

PEER_TIMEOUT_S = 10.0
LEGACY_TIMEOUT_S = 10.0
ROOM_LINGER_S = 30.0
ACK_DELAY_S = 0.05
KEEPALIVE_S = 1.0
TICK_S = 0.005
LINK_STATS_INTERVAL_S = 1.0
STATS_INTERVAL_S = 5.0
MAX_ROOMS = 64
MAX_PEERS = 128
MAX_LEGACY = 32
MAX_FACTS = 1024
MAX_V2_PEER_ID = 199
LEGACY_PEER_BASE = 200
PLAYER_HZ = 30
ENTITY_HZ = 10
SPECTATOR_ALLOWED = {proto.MsgType.TIME_REQ, proto.MsgType.SNAPSHOT_ACK, proto.MsgType.CHAT}
PEER_FLAG_LEGACY = 0x01


class Log:
    def __init__(self, path: str | None, quiet: bool = False):
        self.handle = open(path, "a", encoding="utf-8") if path else None
        self.quiet = quiet

    def line(self, text: str) -> None:
        stamped = f"{time.strftime('%H:%M:%S')} {text}"
        if not self.quiet:
            print(stamped, flush=True)
        if self.handle:
            self.handle.write(stamped + "\n")
            self.handle.flush()

    def close(self) -> None:
        if self.handle:
            self.handle.close()


class Peer:
    def __init__(self, peer_id: int, room, role: int, token: int, address, info: dict, now: float):
        self.peer_id = peer_id
        self.room = room
        self.role = role
        self.token = token
        self.address = address
        self.info = info
        self.minor = min(info["minor"], proto.PROTO_MINOR)   # negotiated, as sent in WELCOME
        self.client_nonce = info["client_nonce"]
        self.conn = Connection(token)
        self.joined = now
        self.last_seen = now
        self.pending = []
        self.welcome = b""
        self.equip = {}
        self.vehicle = None
        self.mod_list = {}
        self.violations = []
        self.dropped_rate = 0
        self.rate_verdicts: dict[int, bool] = {}  # reliable sequence -> rate verdict at arrival
        self.legacy_seq = 0
        self.legacy_slot = 0
        self.last_player = None
        self.window = (0, 0, 0, 0)
        self.buckets = {
            "packets": TokenBucket(Limits.PACKETS_PER_S, Limits.PACKETS_BURST, now),
            "bytes": TokenBucket(Limits.BYTES_PER_S, Limits.BYTES_BURST, now),
            "reliable": TokenBucket(Limits.RELIABLE_PER_S, Limits.RELIABLE_BURST, now),
            "chat": TokenBucket(Limits.CHAT_PER_S, Limits.CHAT_BURST, now),
            "teleport": TokenBucket(Limits.TELEPORT_PER_S, Limits.TELEPORT_BURST, now),
        }

    def label(self) -> str:
        role = proto.Role(self.role).name.lower()
        return f"{self.room.name}/#{self.peer_id} {role} {self.info['name']!r} {self.address[0]}:{self.address[1]}"

    def joined_body(self) -> bytes:
        return proto.PEER_JOINED.encode({
            "peer_id": self.peer_id, "role": self.role, "minor": self.minor, "peer_flags": 0,
            "caps": self.info["caps"], "mod_hash": self.info["mod_hash"], "mod_count": self.info["mod_count"],
            "mod_major": self.info["mod_major"], "mod_minor": self.info["mod_minor"],
            "mod_patch": self.info["mod_patch"], "name": self.info["name"]})


class LegacyClient:
    def __init__(self, client_id: int, address, now: float):
        self.client_id = client_id
        self.address = address
        self.first_seen = now
        self.last_seen = now
        self.received = 0
        self.window_received = 0
        self.last_sequence = None
        self.gaps = 0
        self.out_of_order = 0
        self.forwarded = 0
        self.dropped = 0
        self.v1_flags = 0
        self.is_host = False
        self.pending_pong = None
        self.time_minutes = None
        self.weather = None
        self.announced = False

    @property
    def peer_id(self) -> int:
        return LEGACY_PEER_BASE + (self.client_id % (255 - LEGACY_PEER_BASE))

    def label(self) -> str:
        return f"v1#{self.client_id} {self.address[0]}:{self.address[1]}"

    def joined_body(self) -> bytes:
        return proto.PEER_JOINED.encode({
            "peer_id": self.peer_id, "role": proto.Role.HOST if self.is_host else proto.Role.JOINER,
            "minor": 0, "peer_flags": PEER_FLAG_LEGACY, "caps": int(proto.Cap.PLAYER), "mod_hash": 0,
            "mod_count": 0, "mod_major": 0, "mod_minor": 0, "mod_patch": 0, "name": f"v1-{self.client_id}"})


class Room:
    def __init__(self, name: str, key_hash: bytes, info: dict, max_size: int, now: float):
        self.name = name
        self.key_hash = key_hash
        self.flags = info["join_flags"]
        self.mod_hash = info["mod_hash"]
        self.game_build = info["game_build"]
        self.max_size = max_size
        self.peers: dict[int, Peer] = {}
        self.host_id = None
        self.created = now
        self.emptied_at = None
        self.time_weather = None
        self.session_config = None
        self.facts = {}

    @property
    def bridged(self) -> bool:
        return bool(self.flags & proto.JoinFlag.LEGACY_BRIDGE)

    def free_peer_id(self, preferred: int | None = None):
        if preferred and preferred not in self.peers:
            return preferred
        for peer_id in range(1, MAX_V2_PEER_ID + 1):
            if peer_id not in self.peers:
                return peer_id
        return None


class Relay:
    def __init__(self, args):
        self.args = args
        self.log = Log(args.log, args.quiet)
        self.start = time.perf_counter()
        self.secret = secrets.token_bytes(32)
        self.sim = LinkSim(args.latency_ms, args.jitter_ms, args.loss_pct, args.dup_pct, args.seed)
        family = socket.AF_INET6 if ":" in args.host else socket.AF_INET
        self.sock = socket.socket(family, socket.SOCK_DGRAM)
        if family == socket.AF_INET6:
            self.sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        if hasattr(socket, "SIO_UDP_CONNRESET"):
            self.sock.ioctl(socket.SIO_UDP_CONNRESET, False)
        self.sock.bind((args.host, args.port))
        self.sock.setblocking(False)
        self.rooms: dict[str, Room] = {}
        self.peers_by_token: dict[int, Peer] = {}
        self.peers_by_addr: dict = {}
        self.legacy: dict = {}
        self.next_legacy_id = 1
        self.handshake_buckets: dict = {}
        self.dirty: set = set()
        self.last_tick = 0.0
        self.last_link_stats = 0.0
        self.last_stats = self.start
        self.counters = {key: 0 for key in (
            "datagrams_in", "datagrams_out", "bytes_in", "bytes_out", "unknown_datagrams", "malformed_v2",
            "malformed_v1", "version_rejects", "hellos", "auths", "welcomes", "rejects", "unknown_token",
            "rebinds", "violations", "rate_dropped", "handshake_limited", "kicked", "slow_consumers",
            "minor_filtered")}
        self.reject_reasons = {}

    # ------------------------------------------------------------------ time

    def relay_ms(self, now: float) -> int:
        return int((now - self.start) * 1000.0) & 0xFFFFFFFF

    # ------------------------------------------------------------------- I/O

    def send(self, data: bytes, address, now: float, simulate: bool = True) -> None:
        self.counters["datagrams_out"] += 1
        self.counters["bytes_out"] += len(data)
        if simulate and self.sim.active:
            self.sim.submit(now, data, address)
            return
        self._sendto(data, address)

    def _sendto(self, data: bytes, address) -> None:
        try:
            self.sock.sendto(data, address)
        except OSError as error:
            self.log.line(f"send to {address} failed: {error}")

    def serve(self) -> None:
        self.log.line(f"relay v2 listening on {self.args.host}:{self.args.port} "
                      f"(v1 CP1/RP1 + v2 binary) link sim: {self.sim.describe()} "
                      f"legacy bridge room: {self.args.legacy_room!r}")
        deadline = self.start + self.args.duration if self.args.duration else None
        while deadline is None or time.perf_counter() < deadline:
            readable, _, _ = select.select([self.sock], [], [], 0.002)
            if readable:
                self._drain_socket()
            now = time.perf_counter()
            for data, address in self.sim.pop_due(now):
                self._sendto(data, address)
            if now - self.last_tick >= TICK_S:
                self.tick(now)
                self.last_tick = now

    def _drain_socket(self) -> None:
        for _ in range(512):
            try:
                data, address = self.sock.recvfrom(2048)
            except BlockingIOError:
                return
            except ConnectionResetError:
                continue
            now = time.perf_counter()
            self.counters["datagrams_in"] += 1
            self.counters["bytes_in"] += len(data)
            self.handle_datagram(data, address, now)

    def handle_datagram(self, data: bytes, address, now: float) -> None:
        if proto.is_v2(data):
            self.handle_v2(data, address, now)
        elif legacy.is_cp1(data):
            self.handle_v1(data, address, now)
        else:
            self.counters["unknown_datagrams"] += 1

    # -------------------------------------------------------------- v2 entry

    def handle_v2(self, data: bytes, address, now: float) -> None:
        try:
            ptype, token, seq, ack, ack_bits, body = proto.decode_packet(data)
        except proto.VersionMismatch as error:
            self.counters["version_rejects"] += 1
            if error.ptype in (proto.PacketType.HELLO, proto.PacketType.AUTH) and self.handshake_allowed(address, now):
                self.reject(address, proto.RejectReason.VERSION, f"relay speaks v2.{proto.PROTO_MINOR}", now)
            return
        except proto.ProtocolError:
            self.counters["malformed_v2"] += 1
            return
        if ptype == proto.PacketType.DATA:
            self.on_data(address, token, seq, ack, ack_bits, body, len(data), now)
        elif ptype == proto.PacketType.HELLO:
            self.on_hello(address, body, now)
        elif ptype == proto.PacketType.AUTH:
            self.on_auth(address, body, now)
        elif ptype == proto.PacketType.DISCONNECT:
            peer = self.peers_by_token.get(token)
            if peer is not None and peer.address == address:
                self.remove_peer(peer, proto.DisconnectReason.QUIT, now, send_disconnect=False)
        else:
            self.counters["malformed_v2"] += 1

    def handshake_allowed(self, address, now: float) -> bool:
        bucket = self.handshake_buckets.get(address[0])
        if bucket is None:
            if len(self.handshake_buckets) > 4096:
                self.handshake_buckets.clear()
            bucket = TokenBucket(Limits.HANDSHAKES_PER_IP_PER_S, Limits.HANDSHAKES_PER_IP_BURST, now)
            self.handshake_buckets[address[0]] = bucket
        if bucket.take(now):
            return True
        self.counters["handshake_limited"] += 1
        return False

    def reject(self, address, reason: int, text: str, now: float) -> None:
        self.counters["rejects"] += 1
        name = proto.RejectReason(reason).name
        self.reject_reasons[name] = self.reject_reasons.get(name, 0) + 1
        self.log.line(f"EVENT reject {address[0]}:{address[1]} {name}: {text}")
        self.send(proto.encode_reject(reason, text), address, now)

    def on_hello(self, address, body: bytes, now: float) -> None:
        self.counters["hellos"] += 1
        if not self.handshake_allowed(address, now):
            return
        try:
            info = proto.decode_hello(body)
        except proto.ProtocolError:
            self.counters["malformed_v2"] += 1
            return
        if info["minor"] < proto.MIN_SUPPORTED_MINOR:
            self.reject(address, proto.RejectReason.VERSION, "client too old", now)
            return
        cookie = proto.make_cookie(self.secret, address, info["client_nonce"], int(now - self.start))
        self.send(proto.encode_challenge(cookie), address, now)

    def on_auth(self, address, body: bytes, now: float) -> None:
        self.counters["auths"] += 1
        if not self.handshake_allowed(address, now):
            return
        try:
            info, cookie, key_hash = proto.decode_auth(body)
        except proto.ProtocolError:
            self.counters["malformed_v2"] += 1
            return
        if not proto.check_cookie(self.secret, address, info["client_nonce"], cookie, int(now - self.start)):
            self.reject(address, proto.RejectReason.BAD_COOKIE, "cookie expired or forged", now)
            return
        existing = self.peers_by_addr.get(address)
        if existing is not None and existing.client_nonce == info["client_nonce"]:
            self.send(existing.welcome, address, now)  # our WELCOME was lost, AUTH retried
            return
        preferred_id = None
        resumed = self.peers_by_token.get(info["resume_token"]) if info["resume_token"] else None
        if resumed is not None and resumed.room.name == info["room"] \
                and hmac.compare_digest(resumed.room.key_hash, key_hash):
            preferred_id = resumed.peer_id
            self.remove_peer(resumed, None, now, send_disconnect=False, notify=False)
        if existing is not None and existing in self.peers_by_token.values():
            self.remove_peer(existing, proto.DisconnectReason.QUIT, now, send_disconnect=False)
        self.admit(address, info, key_hash, preferred_id, now)

    def admit(self, address, info: dict, key_hash: bytes, preferred_id, now: float) -> None:
        if len(self.peers_by_token) >= MAX_PEERS:
            self.reject(address, proto.RejectReason.SERVER_FULL, "relay full", now)
            return
        room = self.rooms.get(info["room"])
        if room is None:
            if len(self.rooms) >= MAX_ROOMS:
                self.reject(address, proto.RejectReason.SERVER_FULL, "too many rooms", now)
                return
            room = Room(info["room"], key_hash, info, self.args.room_size, now)
            self.rooms[room.name] = room
            self.log.line(f"EVENT room created {room.name!r} flags=0x{room.flags:02x}")
        elif not hmac.compare_digest(room.key_hash, key_hash):
            self.reject(address, proto.RejectReason.BAD_KEY, "wrong room password", now)
            return
        if len(room.peers) >= room.max_size:
            self.reject(address, proto.RejectReason.ROOM_FULL, f"room holds {room.max_size}", now)
            return
        role = info["role"]
        if role == proto.Role.ANY:
            role = proto.Role.JOINER if room.host_id is not None else proto.Role.HOST
        if role == proto.Role.HOST and room.host_id is not None:
            self.reject(address, proto.RejectReason.ROLE_TAKEN, "room already has a host", now)
            return
        if room.flags & proto.JoinFlag.STRICT_MODS:
            if info["mod_hash"] != room.mod_hash:
                self.reject(address, proto.RejectReason.MOD_MISMATCH, "mod list differs from room", now)
                return
            if info["game_build"] != room.game_build:
                self.reject(address, proto.RejectReason.GAME_BUILD, "game version differs from room", now)
                return
        peer_id = room.free_peer_id(preferred_id)
        if peer_id is None:
            self.reject(address, proto.RejectReason.ROOM_FULL, "no free peer id", now)
            return
        token = secrets.randbits(64) | 1
        while token in self.peers_by_token:
            token = secrets.randbits(64) | 1
        peer = Peer(peer_id, room, role, token, address, info, now)
        room.peers[peer_id] = peer
        room.emptied_at = None
        if role == proto.Role.HOST:
            room.host_id = peer_id
        self.peers_by_token[token] = peer
        self.peers_by_addr[address] = peer
        peer.welcome = proto.encode_welcome({
            "minor": peer.minor, "peer_id": peer_id, "role": role, "room_flags": room.flags, "token": token,
            "relay_time_ms": self.relay_ms(now), "player_hz": PLAYER_HZ, "entity_hz": ENTITY_HZ,
            "max_packet": proto.MAX_PACKET, "room_caps": self.room_caps(room)})
        self.counters["welcomes"] += 1
        self.send(peer.welcome, address, now)
        self.log.line(f"EVENT join {peer.label()} v2.{info['minor']} mods={info['mod_hash']:016x} "
                      f"({info['mod_count']}) build={info['game_build']:08x}")
        self.announce(peer, now)
        self.flush_dirty(now)

    def room_caps(self, room: Room) -> int:
        caps = proto.ALL_CAPS
        for peer in room.peers.values():
            caps &= peer.info["caps"]
        return caps

    def announce(self, peer: Peer, now: float) -> None:
        """PEER_JOINED both ways, then replay cached state to the newcomer."""
        room = peer.room
        mine = peer.joined_body()
        for other in room.peers.values():
            if other is peer:
                continue
            self.queue_reliable(other, proto.MsgType.PEER_JOINED, proto.PEER_RELAY, mine, now)
            self.queue_reliable(peer, proto.MsgType.PEER_JOINED, proto.PEER_RELAY, other.joined_body(), now)
        if room.bridged and room.name == self.args.legacy_room:
            for client in self.legacy.values():
                if client.announced:
                    self.queue_reliable(peer, proto.MsgType.PEER_JOINED, proto.PEER_RELAY, client.joined_body(), now)
        cached = []
        if room.session_config:
            cached.append((proto.MsgType.SESSION_CONFIG, *room.session_config))
        if room.time_weather:
            cached.append((proto.MsgType.TIME_WEATHER, *room.time_weather))
        cached += [(proto.MsgType.WORLD_FACT, *entry) for entry in room.facts.values()]
        for other in room.peers.values():
            if other is peer:
                continue
            cached += [(proto.MsgType.EQUIP, other.peer_id, body) for body in other.equip.values()]
            if other.vehicle is not None:
                cached.append((proto.MsgType.VEHICLE_ENTER, other.peer_id, other.vehicle))
            cached += [(proto.MsgType.MOD_LIST, other.peer_id, body) for _, body in sorted(other.mod_list.items())]
        for mtype, source, body in cached:
            if source in room.peers or source >= LEGACY_PEER_BASE:
                self.queue_reliable(peer, mtype, source, body, now)

    # --------------------------------------------------------------- v2 data

    def on_data(self, address, token: int, seq: int, ack: int, ack_bits: int, body: bytes,
                size: int, now: float) -> None:
        peer = self.peers_by_token.get(token)
        if peer is None:
            self.counters["unknown_token"] += 1
            return
        if peer.address != address:
            if peer.conn.remote_seq and seq_diff(seq, peer.conn.remote_seq) <= 0:
                return
            self.log.line(f"EVENT rebind {peer.label()} -> {address[0]}:{address[1]}")
            self.peers_by_addr.pop(peer.address, None)
            peer.address = address
            self.peers_by_addr[address] = peer
            self.counters["rebinds"] += 1
        if not peer.buckets["packets"].take(now) or not peer.buckets["bytes"].take(now, size):
            peer.dropped_rate += 1
            self.violation(peer, "packet/byte rate", now)
            return
        expected = peer.conn.rel_expected
        try:
            self.charge_arrivals(peer, proto.decode_messages(body), now)
            delivered = peer.conn.on_packet(now, seq, ack, ack_bits, body, size)
        except proto.ProtocolError as error:
            self.violation(peer, f"framing: {error}", now)
            return
        peer.last_seen = now
        released = 0
        for mtype, dest, reliable, payload in delivered:
            allowed = True
            if reliable:
                allowed = peer.rate_verdicts.pop((expected + released) % SEQ_SPACE, True)
                released += 1
            self.route(peer, mtype, dest, reliable, payload, now, allowed)
            if peer.token not in self.peers_by_token:
                break  # kicked while routing; still flush the PEER_LEFT to the others
        self.flush_dirty(now)

    def charge_arrivals(self, peer: Peer, messages: list, now: float) -> None:
        """Charges the reliable, chat and teleport rate limits when a reliable message first arrives.

        Reliable messages that arrive behind a lost packet wait in the connection until the gap is
        repaired and are then released together. They were acked on arrival, so charging them on
        release would drop a burst the sender paced correctly and break exactly-once delivery.
        The verdict is kept by message sequence until route() releases the message.
        """
        for mtype, _, rel_seq, _ in messages:
            if rel_seq is None or rel_seq in peer.rate_verdicts or not peer.conn.is_new_reliable(rel_seq):
                continue
            allowed = peer.buckets["reliable"].take(now)
            if allowed and mtype == proto.MsgType.CHAT:
                allowed = peer.buckets["chat"].take(now)
            if allowed and mtype == proto.MsgType.TELEPORT_REQ:
                allowed = peer.buckets["teleport"].take(now)
            peer.rate_verdicts[rel_seq] = allowed

    def violation(self, peer: Peer, what: str, now: float) -> None:
        self.counters["violations"] += 1
        peer.violations = [stamp for stamp in peer.violations if now - stamp < Limits.VIOLATION_WINDOW_S]
        peer.violations.append(now)
        if len(peer.violations) == 1:
            self.log.line(f"WARN {peer.label()} {what}")
        if len(peer.violations) > Limits.VIOLATIONS_BEFORE_KICK:
            self.counters["kicked"] += 1
            self.log.line(f"EVENT kick {peer.label()} after {len(peer.violations)} violations ({what})")
            self.remove_peer(peer, proto.DisconnectReason.KICKED, now)

    def route(self, peer: Peer, mtype: int, dest: int, reliable: bool, body: bytes, now: float,
              allowed: bool = True) -> None:
        """Validates and forwards one delivered message. ``allowed`` is the rate-limit verdict that
        charge_arrivals() gave a reliable message when it first arrived."""
        spec = proto.SPECS.get(mtype)
        if spec is None or spec.sender == proto.Sender.RELAY or (spec.reliable is not None
                                                                 and spec.reliable != reliable):
            self.violation(peer, f"message 0x{mtype:02x} not allowed from clients", now)
            return
        if spec.min_minor > peer.minor:
            self.violation(peer, f"{proto.MsgType(mtype).name} needs protocol minor {spec.min_minor}", now)
            return
        if spec.sender == proto.Sender.HOST and peer.role != proto.Role.HOST:
            self.violation(peer, f"{proto.MsgType(mtype).name} is host-only", now)
            return
        if peer.role == proto.Role.SPECTATOR and mtype not in SPECTATOR_ALLOWED:
            self.violation(peer, "spectators may not send state", now)
            return
        try:
            values = spec.codec.decode(body)
        except proto.ProtocolError as error:
            self.violation(peer, f"invalid {proto.MsgType(mtype).name}: {error}", now)
            return
        if not proto.delivery_ok(spec, reliable, values):
            self.violation(peer, f"{proto.MsgType(mtype).name}: reliable bit does not match the channel", now)
            return
        needed = proto.required_minor(mtype, values)
        if needed > peer.minor:
            self.violation(peer, f"{proto.MsgType(mtype).name} content needs protocol minor {needed}", now)
            return
        if not allowed:
            self.counters["rate_dropped"] += 1
            return
        if mtype == proto.MsgType.TIME_REQ:
            reply = proto.TIME_RESP.encode({"t0": values["t0"], "t1": self.relay_ms(now),
                                            "t2": self.relay_ms(time.perf_counter())})
            peer.pending.append((proto.MsgType.TIME_RESP, proto.PEER_RELAY, reply))
            self.dirty.add(peer)
            return
        self.cache_state(peer, mtype, values, body)
        for target in self.targets(peer, spec, dest, values):
            if target.minor < needed:
                self.counters["minor_filtered"] += 1
                continue
            if reliable:
                self.queue_reliable(target, mtype, peer.peer_id, body, now)
            else:
                target.pending.append((mtype, peer.peer_id, body))
                self.dirty.add(target)
        if mtype == proto.MsgType.PLAYER_SNAPSHOT:
            peer.last_player = values
            if peer.room.bridged and peer.room.name == self.args.legacy_room:
                self.bridge_to_legacy(peer, values, now)

    def targets(self, peer: Peer, spec, dest: int, values: dict) -> list:
        room = peer.room
        if spec.route == proto.Route.BROADCAST:
            return [other for other in room.peers.values() if other is not peer]
        if spec.route == proto.Route.HOST or (spec.route == proto.Route.HIT and values["target_kind"] == 0):
            host = room.peers.get(room.host_id) if room.host_id is not None else None
            return [host] if host is not None and host is not peer else []
        if spec.route == proto.Route.PEER and dest == proto.PEER_BROADCAST:
            return [other for other in room.peers.values() if other is not peer]
        wanted = dest if spec.route in (proto.Route.TARGET, proto.Route.PEER) else values["target_net"]
        target = room.peers.get(wanted)
        return [target] if target is not None and target is not peer else []

    def cache_state(self, peer: Peer, mtype: int, values: dict, body: bytes) -> None:
        room = peer.room
        if mtype == proto.MsgType.TIME_WEATHER:
            room.time_weather = (peer.peer_id, body)
        elif mtype == proto.MsgType.SESSION_CONFIG:
            room.session_config = (peer.peer_id, body)
        elif mtype == proto.MsgType.WORLD_FACT:
            if values["fact_hash"] in room.facts or len(room.facts) < MAX_FACTS:
                room.facts[values["fact_hash"]] = (peer.peer_id, body)
        elif mtype == proto.MsgType.EQUIP:
            peer.equip[values["slot"]] = body
        elif mtype == proto.MsgType.VEHICLE_ENTER:
            peer.vehicle = body
        elif mtype == proto.MsgType.VEHICLE_EXIT:
            peer.vehicle = None
        elif mtype == proto.MsgType.MOD_LIST:
            peer.mod_list[values["chunk"]] = body

    def queue_reliable(self, target: Peer, mtype: int, source: int, body: bytes, now: float) -> None:
        if not target.conn.queue_reliable(mtype, source, body, now):
            self.counters["slow_consumers"] += 1
            self.log.line(f"EVENT {target.label()} reliable window full, disconnecting")
            self.remove_peer(target, proto.DisconnectReason.SLOW_CONSUMER, now)
            return
        self.dirty.add(target)

    def flush_dirty(self, now: float) -> None:
        dirty, self.dirty = self.dirty, set()
        for peer in dirty:
            if peer.token in self.peers_by_token:
                self.flush(peer, now)

    def flush(self, peer: Peer, now: float, force: bool = False) -> None:
        packets = peer.conn.build_packets(now, peer.pending, force=force)
        peer.pending = []
        for packet in packets:
            self.send(packet, peer.address, now)

    def remove_peer(self, peer: Peer, reason, now: float, send_disconnect: bool = True, notify: bool = True) -> None:
        room = peer.room
        room.peers.pop(peer.peer_id, None)
        self.peers_by_token.pop(peer.token, None)
        if self.peers_by_addr.get(peer.address) is peer:
            del self.peers_by_addr[peer.address]
        self.dirty.discard(peer)
        if room.host_id == peer.peer_id:
            room.host_id = None
        if send_disconnect and reason is not None:
            self.send(proto.encode_disconnect(peer.token, reason), peer.address, now)
        if notify:
            body = proto.PEER_LEFT.encode({"peer_id": peer.peer_id, "reason": int(reason or 0)})
            for other in list(room.peers.values()):
                self.queue_reliable(other, proto.MsgType.PEER_LEFT, proto.PEER_RELAY, body, now)
            why = proto.DisconnectReason(reason).name if reason else "resumed"
            self.log.line(f"EVENT leave {peer.label()} ({why})")
        if not room.peers:
            room.emptied_at = now

    # ------------------------------------------------------------------ tick

    def tick(self, now: float) -> None:
        for peer in list(self.peers_by_token.values()):
            if now - peer.last_seen > PEER_TIMEOUT_S:
                self.remove_peer(peer, proto.DisconnectReason.TIMEOUT, now)
                continue
            conn = peer.conn
            since_send = now - (conn.last_send or 0.0)
            if peer.pending or conn.reliable_due(now) or (conn.ack_pending and since_send >= ACK_DELAY_S) \
                    or since_send >= KEEPALIVE_S:
                self.flush(peer, now, force=True)
        for name in [n for n, room in self.rooms.items()
                     if not room.peers and room.emptied_at is not None and now - room.emptied_at > ROOM_LINGER_S]:
            del self.rooms[name]
            self.log.line(f"EVENT room closed {name!r}")
        for address in [a for a, c in self.legacy.items() if now - c.last_seen > LEGACY_TIMEOUT_S]:
            self.drop_legacy(address, now)
        if now - self.last_link_stats >= LINK_STATS_INTERVAL_S:
            self.send_link_stats(now)
            self.last_link_stats = now
        if now - self.last_stats >= STATS_INTERVAL_S:
            self.print_stats(now - self.last_stats)
            self.last_stats = now
        self.flush_dirty(now)

    def send_link_stats(self, now: float) -> None:
        for room in self.rooms.values():
            bodies = []
            for peer in room.peers.values():
                stats = peer.conn.stats
                current = (stats.packets_received, stats.recv_span, stats.packets_acked, stats.packets_lost)
                previous = peer.window
                peer.window = current
                received, span = current[0] - previous[0], current[1] - previous[1]
                acked, lost = current[2] - previous[2], current[3] - previous[3]
                loss_in = 0 if span <= 0 else max(0, round(1000 * (1 - received / span)))
                loss_out = 0 if acked + lost <= 0 else round(1000 * lost / (acked + lost))
                bodies.append(proto.LINK_STATS.encode({
                    "peer_id": peer.peer_id, "reserved": 0, "rtt_ms": min(65535, round(peer.conn.rtt_ms())),
                    "loss_in_permille": min(1000, loss_in), "loss_out_permille": min(1000, loss_out)}))
            for peer in room.peers.values():
                for body in bodies:
                    peer.pending.append((proto.MsgType.LINK_STATS, proto.PEER_RELAY, body))
                self.dirty.add(peer)

    def print_stats(self, elapsed: float) -> None:
        self.log.line(f"[STATS] rooms={len(self.rooms)} peers={len(self.peers_by_token)} "
                      f"v1={len(self.legacy)} link: {self.sim.describe()} counters: "
                      + " ".join(f"{k}={v}" for k, v in self.counters.items() if v))
        for peer in self.peers_by_token.values():
            stats = peer.conn.stats
            self.log.line(
                f"[STATS]   {peer.label():<48} rtt={peer.conn.rtt_ms():5.0f}ms "
                f"in={stats.packets_received} out={stats.packets_sent} loss_in={peer.conn.loss_in():.1%} "
                f"loss_out={peer.conn.loss_out():.1%} rel_sent={stats.reliable_sent} "
                f"resent={stats.reliable_resent} rel_pending={len(peer.conn.rel_pending)} "
                f"violations={len(peer.violations)} idle={time.perf_counter() - peer.last_seen:.1f}s")
        for client in self.legacy.values():
            rate = client.window_received / elapsed if elapsed > 0 else 0.0
            self.log.line(f"[STATS]   {client.label():<48} in={rate:5.1f}/s total={client.received} "
                          f"gaps={client.gaps} late={client.out_of_order} fwd={client.forwarded} "
                          f"dropped={client.dropped}")
            client.window_received = 0
        self.write_json()

    def snapshot_json(self) -> dict:
        return {
            "uptime_s": round(time.perf_counter() - self.start, 3),
            "start_perf": self.start,
            "counters": self.counters,
            "reject_reasons": self.reject_reasons,
            "link_sim": self.sim.as_dict(),
            "rooms": {name: {"peers": sorted(room.peers), "host": room.host_id, "flags": room.flags,
                             "facts": len(room.facts), "has_time_weather": room.time_weather is not None}
                      for name, room in self.rooms.items()},
            "peers": [{"room": peer.room.name, "peer_id": peer.peer_id, "role": proto.Role(peer.role).name,
                       "name": peer.info["name"], "rtt_ms": round(peer.conn.rtt_ms(), 1),
                       "loss_in": round(peer.conn.loss_in(), 4), "loss_out": round(peer.conn.loss_out(), 4),
                       "violations": len(peer.violations), "dropped_rate": peer.dropped_rate,
                       "link": peer.conn.stats.as_dict()} for peer in self.peers_by_token.values()],
            "legacy": [{"id": c.client_id, "received": c.received, "gaps": c.gaps, "late": c.out_of_order,
                        "forwarded": c.forwarded, "dropped": c.dropped, "flags": c.v1_flags}
                       for c in self.legacy.values()],
        }

    def write_json(self) -> None:
        if not self.args.stats_json:
            return
        temp = self.args.stats_json + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(self.snapshot_json(), handle, indent=1)
        os.replace(temp, self.args.stats_json)

    # ---------------------------------------------------------------- legacy

    def handle_v1(self, data: bytes, address, now: float) -> None:
        parsed = legacy.parse_cp1(data)
        if parsed is None:
            self.counters["malformed_v1"] += 1
            return
        seq, x, y, z, w, fx, fy = parsed
        client = self.legacy.get(address)
        if client is None:
            if len(self.legacy) >= MAX_LEGACY:
                return
            client = LegacyClient(self.next_legacy_id, address, now)
            self.next_legacy_id += 1
            self.legacy[address] = client
            self.log.line(f"EVENT v1 client joined {client.label()}")
        client.last_seen = now
        client.received += 1
        client.window_received += 1
        if client.last_sequence is not None:
            if seq > client.last_sequence + 1:
                client.gaps += seq - client.last_sequence - 1
            elif seq <= client.last_sequence:
                client.out_of_order += 1
        if client.last_sequence is None or seq > client.last_sequence:
            client.last_sequence = seq
        self.send(f"WELCOME,{client.client_id}".encode(), address, now, simulate=False)
        forward = legacy.format_rp1(client.client_id, seq, x, y, z, w, fx, fy)
        for other in self.legacy.values():
            if other is not client:
                self.send(forward, other.address, now)
                client.forwarded += 1
        room = self.rooms.get(self.args.legacy_room)
        if room is not None and room.bridged:
            self.bridge_from_legacy(client, room, seq, x, y, z, fx, fy, now)

    def bridge_from_legacy(self, client: LegacyClient, room: Room, seq: int, x: float, y: float, z: float,
                           fx: float, fy: float, now: float) -> None:
        unit_x, unit_y, payload = legacy.decode_forward(fx, fy)
        if payload is not None:
            kind, value = legacy.split_payload(payload)
            if kind == legacy.TYPE_FLAGS:
                client.v1_flags = value
                client.is_host = bool(value & legacy.V1_HOST)
            elif kind == legacy.TYPE_PING:
                client.pending_pong = value
            elif kind in (legacy.TYPE_TIME, legacy.TYPE_WEATHER) and client.is_host:
                self.legacy_world_state(client, room, kind, value, now)
        if not client.announced:
            client.announced = True
            for peer in room.peers.values():
                self.queue_reliable(peer, proto.MsgType.PEER_JOINED, proto.PEER_RELAY, client.joined_body(), now)
        flags = legacy.v1_flags_to_v2(client.v1_flags)
        if client.v1_flags & legacy.V1_IN_VEHICLE:
            move = proto.MoveState.VEHICLE
        elif client.v1_flags & legacy.V1_CROUCH:
            move = proto.MoveState.CROUCH_IDLE
        else:
            move = proto.MoveState.IDLE
        try:
            body = proto.PLAYER_BASE.encode({
                "snap_seq": seq & 0xFFFF, "sample_time": self.relay_ms(now), "x": x, "y": y, "z": z,
                "yaw": proto.yaw_to_u16(legacy.yaw_from_forward(unit_x, unit_y)), "pitch": 0,
                "vx": 0, "vy": 0, "vz": 0, "move_state": int(move), "health": 255, "flags": flags})
        except proto.ProtocolError:
            self.counters["malformed_v1"] += 1
            return
        for peer in room.peers.values():
            peer.pending.append((proto.MsgType.PLAYER_SNAPSHOT, client.peer_id, body))
            self.dirty.add(peer)
        self.flush_dirty(now)

    def legacy_world_state(self, client: LegacyClient, room: Room, kind: int, value: int, now: float) -> None:
        if kind == legacy.TYPE_TIME:
            minutes = value * legacy.TIME_STEP_MINUTES
            if client.time_minutes is not None and abs(minutes - client.time_minutes) < legacy.TIME_STEP_MINUTES:
                return
            client.time_minutes = minutes
        else:
            if value - 1 == client.weather:
                return
            client.weather = value - 1
        body = proto.TIME_WEATHER.encode({
            "game_seconds": (client.time_minutes or 0) * 60, "time_scale_x100": 100, "flags": 0,
            "weather_id": max(0, min(31, client.weather or 0)), "weather_record": 0, "transition_s": 10})
        room.time_weather = (client.peer_id, body)
        for peer in room.peers.values():
            self.queue_reliable(peer, proto.MsgType.TIME_WEATHER, client.peer_id, body, now)

    def bridge_to_legacy(self, peer: Peer, values: dict, now: float) -> None:
        if not self.legacy:
            return
        is_host = peer.role == proto.Role.HOST
        yaw = proto.u16_to_yaw(values["yaw"])
        unit_x, unit_y = legacy.forward_from_yaw(yaw)
        peer.legacy_seq += 1
        peer.legacy_slot += 1
        world = None
        if is_host and peer.room.time_weather is not None and peer.room.time_weather[0] == peer.peer_id:
            world = proto.TIME_WEATHER.decode(peer.room.time_weather[1])
        for client in self.legacy.values():
            if client.pending_pong is not None:
                payload = legacy.TYPE_PONG * legacy.TYPE_STRIDE + client.pending_pong
                client.pending_pong = None
            elif world is not None and peer.legacy_slot % 4 == 0:
                minutes = (world["game_seconds"] // 60) % 1440
                payload = legacy.TYPE_TIME * legacy.TYPE_STRIDE + minutes // legacy.TIME_STEP_MINUTES
            elif world is not None and peer.legacy_slot % 4 == 2:
                payload = legacy.TYPE_WEATHER * legacy.TYPE_STRIDE + world["weather_id"] + 1
            else:
                payload = legacy.TYPE_FLAGS * legacy.TYPE_STRIDE + legacy.v2_flags_to_v1(values["flags"], is_host)
            send_x, send_y = legacy.encode_forward(unit_x, unit_y, payload)
            text = legacy.format_rp1(peer.peer_id, peer.legacy_seq, values["x"], values["y"], values["z"], 1.0,
                                     send_x, send_y)
            self.send(text, client.address, now)

    def drop_legacy(self, address, now: float) -> None:
        client = self.legacy.pop(address)
        self.log.line(f"EVENT v1 client timed out {client.label()}")
        room = self.rooms.get(self.args.legacy_room)
        if client.announced and room is not None:
            body = proto.PEER_LEFT.encode({"peer_id": client.peer_id, "reason": int(proto.DisconnectReason.TIMEOUT)})
            for peer in room.peers.values():
                self.queue_reliable(peer, proto.MsgType.PEER_LEFT, proto.PEER_RELAY, body, now)

    def shutdown(self) -> None:
        now = time.perf_counter()
        for peer in list(self.peers_by_token.values()):
            self._sendto(proto.encode_disconnect(peer.token, proto.DisconnectReason.SERVER_SHUTDOWN), peer.address)
        self.print_stats(max(1e-3, now - self.last_stats))
        self.log.line("relay stopped")
        self.log.close()
        self.sock.close()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="CP2077 Coop relay v2 (v1-compatible)")
    parser.add_argument("--host", default="127.0.0.1", help="bind address; 0.0.0.0 or :: for a public relay")
    parser.add_argument("--port", type=int, default=11778)
    parser.add_argument("--room-size", type=int, default=2, choices=range(2, 9), metavar="2-8")
    parser.add_argument("--legacy-room", default="legacy",
                        help="v2 room (created with LEGACY_BRIDGE) that v1 clients are bridged into")
    parser.add_argument("--latency-ms", type=float, default=0.0, help="simulated one-way delay on every send")
    parser.add_argument("--jitter-ms", type=float, default=0.0)
    parser.add_argument("--loss-pct", type=float, default=0.0)
    parser.add_argument("--dup-pct", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--duration", type=float, default=0.0, help="exit after N seconds (tests)")
    parser.add_argument("--stats-json", default=None, help="write machine-readable stats here")
    parser.add_argument("--log", default=None, help="append log lines to this file")
    parser.add_argument("--quiet", action="store_true", help="no console output")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    relay = Relay(parse_args(argv))
    try:
        relay.serve()
    except KeyboardInterrupt:
        pass
    finally:
        relay.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
