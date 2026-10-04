"""Protocol v2 test client: plays the host or the joiner against relay_v2.py.

It exercises everything the game DLL will do, with a deterministic world so
the results can be checked exactly:

* handshake (HELLO / CHALLENGE / AUTH / WELCOME), clock sync with the relay
* 30 Hz PLAYER_SNAPSHOT (the joiner "drives", so it sends the VehicleBlock)
* host: 10 Hz delta-compressed ENTITY_SNAPSHOT of ~80 NPCs and vehicles with
  interest management around the joiner; joiner: SNAPSHOT_ACK
* scripted reliable events (equip, vehicle enter/exit, hit, death,
  time/weather, chat, teleport request/response, world facts, mod list)
  including a 30-message burst
* rendering at 60 Hz through the interpolation buffer, measuring the error
  against the sender's ground truth, plus the v1 "latest packet" model
* a drain at the end: events stop DRAIN_S before --duration and a final
  "<name> done" chat marks the end of each reliable stream. After --duration
  the client stops sending snapshots and rendering, keeps acking and resending,
  and quits once the relay has acked all of its events and the other player's
  marker has arrived (everything before it has then arrived too), or after
  LINGER_MAX_S. A short run can therefore never cut off a stream that is still
  being repaired, and a reliable violation means a real loss or reordering.

Every link impairment is simulated in-process on the uplink and downlink.
The run ends with a JSON report consumed by run_demo.py.
"""
from __future__ import annotations

import argparse
import json
import math
import secrets
import socket
import sys
import time

from coopnet import proto, testworld
from coopnet.interp import ClockSync, InterpBuffer, LatestSlotFollower, distance
from coopnet.linksim import LinkSim
from coopnet.reliability import Connection
from coopnet.snapshot import DeltaDecoder, DeltaEncoder, InterestManager, quantize, view_hash

clock = time.perf_counter

GAME_VERSION = "2.31a"
MOD_VERSION = (0, 2, 0)
DEMO_MODS = [("CP2077Coop", "0.2.0"), ("Codeware", "1.18.0"), ("redscript", "0.5.27"),
             ("RED4ext", "1.27.0"), ("cyber_engine_tweaks", "1.35.0")]
PLAYER_INTERVAL_S = 1.0 / 30.0
FRAME_INTERVAL_S = 1.0 / 60.0
ENTITY_EVERY_N_PLAYER_TICKS = 3
EVENT_INTERVAL_S = 0.15
CHAT_INTERVAL_S = 1.2
BURST_SIZE = 30
DRAIN_S = 4.0
LINGER_MAX_S = 20.0
ACK_FLUSH_S = 0.05
DONE_SUFFIX = " done"
CLOCK_ESTIMATOR = "lowest-rtt"  # interp.py ClockSync: midpoint of the lowest-RTT exchange in a window
PLAYER_TARGET_BASE = 0xFF00
HANDSHAKE_TIMEOUT_S = 10.0


def summarize(values) -> dict:
    if not values:
        return {"count": 0}
    ordered = sorted(values)

    def pick(fraction):
        return ordered[min(len(ordered) - 1, int(fraction * (len(ordered) - 1) + 0.5))]

    return {"count": len(ordered), "mean": sum(ordered) / len(ordered), "p50": pick(0.5), "p95": pick(0.95),
            "p99": pick(0.99), "max": ordered[-1]}


def accelerations(track) -> list:
    """|second derivative| of a (time_s, pos) track, for jitter/glitch measurement."""
    result = []
    for index in range(1, len(track) - 1):
        (t0, p0), (t1, p1), (t2, p2) = track[index - 1], track[index], track[index + 1]
        if t1 - t0 <= 0 or t2 - t1 <= 0:
            continue
        v0 = [(b - a) / (t1 - t0) for a, b in zip(p0, p1)]
        v1 = [(b - a) / (t2 - t1) for a, b in zip(p1, p2)]
        accel = [2.0 * (b - a) / (t2 - t0) for a, b in zip(v0, v1)]
        result.append(math.sqrt(sum(a * a for a in accel)))
    return result


class Endpoint:
    """UDP socket to the relay plus simulated uplink/downlink impairment."""

    def __init__(self, relay, uplink: LinkSim, downlink: LinkSim):
        self.relay = (socket.gethostbyname(relay[0]), relay[1])
        self.uplink = uplink
        self.downlink = downlink
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if hasattr(socket, "SIO_UDP_CONNRESET"):
            self.sock.ioctl(socket.SIO_UDP_CONNRESET, False)
        self.sock.bind(("0.0.0.0", 0))
        self.sock.setblocking(False)
        self.bytes_sent = 0
        self.bytes_received = 0
        self.foreign = 0

    def send(self, now: float, data: bytes) -> None:
        self.bytes_sent += len(data)
        if self.uplink.active:
            self.uplink.submit(now, data, self.relay)
        else:
            self._sendto(data)

    def _sendto(self, data: bytes) -> None:
        try:
            self.sock.sendto(data, self.relay)
        except OSError as error:
            print(f"send failed: {error}", file=sys.stderr)

    def pump(self, now: float) -> list:
        for data, _ in self.uplink.pop_due(now):
            self._sendto(data)
        arrived = []
        while True:
            try:
                data, address = self.sock.recvfrom(2048)
            except BlockingIOError:
                break
            except ConnectionResetError:
                continue
            if address != self.relay:
                self.foreign += 1
                continue
            self.bytes_received += len(data)
            if self.downlink.active:
                self.downlink.submit(now, data, address)
            else:
                arrived.append(data)
        arrived += [data for data, _ in self.downlink.pop_due(now)]
        return arrived


class RemotePlayer:
    def __init__(self, role: int):
        self.role = role
        self.buffer = InterpBuffer(**InterpBuffer.PLAYER)
        self.follower = LatestSlotFollower()
        self.latest_seq = None
        self.first_seq = None
        self.received = 0
        self.duplicates = 0
        self.reordered = 0
        self.flags_seen = 0
        self.driving_seen = 0
        self.last = None
        self.legacy_radius_error = []


class CoopClient:
    def __init__(self, args):
        self.args = args
        self.start = clock()
        uplink = LinkSim(args.up_latency_ms, args.up_jitter_ms, args.up_loss_pct, args.up_dup_pct, args.seed)
        downlink = LinkSim(args.down_latency_ms, args.down_jitter_ms, args.down_loss_pct, args.down_dup_pct,
                           None if args.seed is None else args.seed + 1)
        self.endpoint = Endpoint((args.relay_host, args.relay_port), uplink, downlink)
        self.role_wanted = proto.Role.HOST if args.role == "host" else proto.Role.JOINER
        self.conn = None
        self.backlog = []
        self.welcome = None
        self.reject = None
        self.peer_id = None
        self.role = None
        self.clock_sync = ClockSync()
        self.peers = {}
        self.ever_seen_peers = set()
        self.link_stats = {}
        self.pending = []
        self.disconnected = None
        self.world = testworld.build_entities(args.world_seed)
        self.world_by_id = {spec.net_id: spec for spec in self.world}
        self.epoch_ms = None
        self.ready_at = None
        self.events_started = False
        self.event_counter = 0
        self.next_event = 0.0
        self.next_chat = 0.0
        self.burst_done = False
        self.done_sent = False
        self.other_done = False
        self.linger_s = 0.0
        self.linger_timed_out = False
        self.teleport_sent = False
        self.deaths_sent = set()
        self.sent_events = []
        self.received_events = []
        self.invalid_messages = 0
        self.snap_seq = 0
        self.player_ticks = 0
        self.snapshots_sent = 0
        self.remote = {}
        self.encoder = DeltaEncoder(args.entity_budget)
        self.interest = InterestManager()
        self.host_view_hashes = {}
        self.entity_bytes = []
        self.decoder = DeltaDecoder()
        self.joiner_view_hashes = {}
        self.entity_view = {}
        self.entity_buffers = {}
        self.entity_stream = InterpBuffer(**InterpBuffer.ENTITY)
        self.entity_errors = []
        self.entity_outliers = []
        self.entity_spawns = 0
        self.entity_removals = 0
        self.player_errors = []
        self.yaw_errors = []
        self.motion_errors = {}
        self.render_delays = []
        self.render_modes = {}
        self.v2_track = []
        self.v1_track = []
        self.truth_track = []
        self.frame_index = 0
        self.rendered_peer = None
        self.clock_samples = []
        self.clock_steps = 0
        self.unsynced_dropped = 0

    # ------------------------------------------------------------- utilities

    def local_ms(self, now: float) -> float:
        return (now - self.start) * 1000.0

    def relay_ms(self, now: float) -> float:
        return self.clock_sync.relay_ms(self.local_ms(now))

    def join_info(self, nonce: int) -> dict:
        return {"minor": proto.PROTO_MINOR, "role": int(self.role_wanted), "join_flags": self.args.join_flags,
                "caps": proto.ALL_CAPS, "game_build": proto.game_build_id(GAME_VERSION),
                "mod_major": MOD_VERSION[0], "mod_minor": MOD_VERSION[1], "mod_patch": MOD_VERSION[2],
                "mod_hash": proto.mod_list_hash(DEMO_MODS), "mod_count": len(DEMO_MODS),
                "client_nonce": nonce, "resume_token": 0, "room": self.args.room, "name": self.args.name}

    def other_peer(self, include_legacy: bool = False):
        """The other v2 player (ids < 200); bridged v1 players (200+) only when asked."""
        for peer_id, info in sorted(self.peers.items()):
            if peer_id != self.peer_id and (peer_id < 200 or include_legacy):
                return peer_id, info
        return None, None

    # ------------------------------------------------------------- handshake

    def handshake(self) -> bool:
        nonce = secrets.randbits(64)
        info = self.join_info(nonce)
        key_hash = proto.room_key_hash(self.args.room, self.args.password)
        cookie = None
        deadline = clock() + HANDSHAKE_TIMEOUT_S
        next_send = 0.0
        while clock() < deadline:
            now = clock()
            if now >= next_send:
                packet = proto.encode_hello(info) if cookie is None else proto.encode_auth(info, cookie, key_hash)
                self.endpoint.send(now, packet)
                next_send = now + 0.25
            arrived = self.endpoint.pump(now)
            for data in arrived:
                try:
                    ptype, token, _, _, _, body = proto.decode_packet(data)
                except proto.ProtocolError:
                    continue
                if ptype == proto.PacketType.CHALLENGE and cookie is None:
                    cookie = proto.decode_challenge(body)[2]
                    next_send = 0.0
                elif ptype == proto.PacketType.WELCOME:
                    self.backlog = [later for later in arrived[arrived.index(data) + 1:]]
                    self.welcome = proto.decode_welcome(body)
                    self.peer_id = self.welcome["peer_id"]
                    self.role = self.welcome["role"]
                    self.conn = Connection(self.welcome["token"])
                    self.welcome["handshake_ms"] = round(self.local_ms(now), 1)
                    return True
                elif ptype == proto.PacketType.REJECT:
                    reason, minor_min, minor_max, text = proto.decode_reject(body)
                    self.reject = {"reason": proto.RejectReason(reason).name, "text": text,
                                   "relay_minor": [minor_min, minor_max]}
                    return False
            time.sleep(0.0005)
        self.reject = {"reason": "TIMEOUT", "text": "no WELCOME"}
        return False

    # -------------------------------------------------------------- main loop

    def run(self) -> dict:
        if self.handshake():
            self.loop()
        return self.build_report()

    def loop(self) -> None:
        now = clock()
        end = self.start + self.args.duration
        next_player = now
        next_frame = now
        next_time_req = now
        last_frame = now
        for data in self.backlog:  # session DATA that arrived in the same batch as the WELCOME
            self.on_datagram(data, now)
        while self.disconnected is None and not self.finished(now, end):
            for data in self.endpoint.pump(now):
                self.on_datagram(data, now)
            if now >= end:
                self.linger(now)
                time.sleep(0.0005)
                now = clock()
                continue
            if now >= next_time_req:
                fast = now - self.start < 3.0
                self.pending.append((proto.MsgType.TIME_REQ, proto.PEER_RELAY,
                                     proto.TIME_REQ.encode({"t0": int(self.local_ms(now)) & 0xFFFFFFFF})))
                next_time_req = now + (0.1 if fast else 1.0)
            if now >= next_player:
                self.player_tick(now)
                next_player = max(next_player + PLAYER_INTERVAL_S, now - PLAYER_INTERVAL_S)
            if now >= next_frame:
                self.render_frame(now, now - last_frame)
                last_frame = now
                next_frame = max(next_frame + FRAME_INTERVAL_S, now - FRAME_INTERVAL_S)
            self.events_tick(now)
            if self.pending or self.conn.reliable_due(now):
                self.flush(now)
            time.sleep(0.0005)
            now = clock()
        self.linger_s = max(0.0, now - end)
        bye = proto.encode_disconnect(self.conn.token, proto.DisconnectReason.QUIT)
        self.endpoint._sendto(bye)

    def finished(self, now: float, end: float) -> bool:
        """After --duration: done once the streams are drained (see the module notes) or at the cap."""
        if now < end:
            return False
        if now >= end + LINGER_MAX_S:
            self.linger_timed_out = True
            return True
        if self.conn.rel_pending:
            return False
        other_id, _ = self.other_peer()
        return other_id is None or not self.events_started or self.other_done

    def linger(self, now: float) -> None:
        """Past --duration: no snapshots, frames or events; resend and ack until drained."""
        if self.pending or self.conn.reliable_due(now):
            self.flush(now)
        elif self.conn.ack_pending and now - (self.conn.last_send or 0.0) >= ACK_FLUSH_S:
            for packet in self.conn.build_packets(now, [], force=True):
                self.endpoint.send(now, packet)

    def flush(self, now: float) -> None:
        for packet in self.conn.build_packets(now, self.pending):
            self.endpoint.send(now, packet)
        self.pending = []

    # ---------------------------------------------------------------- receive

    def on_datagram(self, data: bytes, now: float) -> None:
        try:
            ptype, token, seq, ack, ack_bits, body = proto.decode_packet(data)
        except proto.ProtocolError:
            self.invalid_messages += 1
            return
        if ptype == proto.PacketType.DISCONNECT and token == self.conn.token:
            self.disconnected = proto.DisconnectReason(body[0]).name if body else "?"
            return
        if ptype != proto.PacketType.DATA or token != self.conn.token:
            return
        try:
            delivered = self.conn.on_packet(now, seq, ack, ack_bits, body, len(data))
        except proto.ProtocolError:
            self.invalid_messages += 1
            return
        for mtype, src, reliable, payload in delivered:
            try:
                values = proto.decode_body(mtype, payload)
            except proto.ProtocolError:
                self.invalid_messages += 1
                continue
            if reliable and src != proto.PEER_RELAY:
                self.received_events.append([src, mtype, payload.hex()])
            self.on_message(mtype, src, values, now)

    def on_message(self, mtype: int, src: int, values: dict, now: float) -> None:
        if mtype == proto.MsgType.TIME_RESP:
            t3 = self.local_ms(now)
            t0 = values["t0"]
            if self.clock_sync.on_response(t0, values["t1"], values["t2"], t3):
                self.clock_steps += 1
                for remote in self.remote.values():
                    remote.buffer.reset_timing()
                self.entity_stream.reset_timing()
            self.clock_samples.append([round(t3, 1), round(self.clock_sync.offset_ms, 2),
                                       round(self.clock_sync.rtt_ms, 2)])
        elif mtype == proto.MsgType.PEER_JOINED:
            self.peers[values["peer_id"]] = values
            self.ever_seen_peers.add(values["peer_id"])
            if values["peer_id"] in self.remote:
                self.remote[values["peer_id"]].role = values["role"]
        elif mtype == proto.MsgType.PEER_LEFT:
            self.peers.pop(values["peer_id"], None)
        elif mtype == proto.MsgType.CHAT and src == self.other_peer()[0] and values["text"].endswith(DONE_SUFFIX):
            self.other_done = True
        elif mtype == proto.MsgType.LINK_STATS:
            self.link_stats[values["peer_id"]] = values
        elif mtype == proto.MsgType.PLAYER_SNAPSHOT:
            self.on_player_snapshot(src, values, now)
        elif mtype == proto.MsgType.ENTITY_SNAPSHOT:
            self.on_entity_snapshot(values, now)
        elif mtype == proto.MsgType.SNAPSHOT_ACK:
            self.encoder.on_ack(values["tick"])
        elif mtype == proto.MsgType.WORLD_FACT and values["fact_hash"] == testworld.EPOCH_FACT:
            self.epoch_ms = values["value"]
        elif mtype == proto.MsgType.TELEPORT_REQ and self.role == proto.Role.HOST:
            truth = testworld.player_truth(proto.Role.HOST, self.relay_ms(now) / 1000.0, self.args.player_path)
            x, y, z = truth["pos"]
            self.send_event(now, proto.MsgType.TELEPORT_RESP, src, {
                "req_id": values["req_id"], "accepted": 1, "reserved": 0, "x": x, "y": y, "z": z,
                "yaw": proto.yaw_to_u16(truth["yaw"])})

    def on_player_snapshot(self, src: int, values: dict, now: float) -> None:
        if not self.clock_sync.synced:
            self.unsynced_dropped += 1
            return
        info = self.peers.get(src, {})
        remote = self.remote.get(src)
        if remote is None:
            remote = self.remote[src] = RemotePlayer(info.get("role", proto.Role.JOINER))
        seq = values["snap_seq"]
        if remote.latest_seq is not None:
            distance_seq = ((seq - remote.latest_seq + 32768) % 65536) - 32768
            if distance_seq == 0:
                remote.duplicates += 1
                return
            if distance_seq < 0:
                remote.reordered += 1
        if remote.latest_seq is None or ((seq - remote.latest_seq) % 65536) < 32768:
            remote.latest_seq = seq
            remote.last = values
        if remote.first_seq is None or ((remote.first_seq - seq) % 65536) < 32768:
            remote.first_seq = seq
        remote.received += 1
        remote.flags_seen |= values["flags"]
        if values["vehicle"] is not None:
            remote.driving_seen += 1
        pos = (values["x"], values["y"], values["z"])
        vel = tuple(proto.cms_to_velocity(values[k]) for k in ("vx", "vy", "vz"))
        legacy = bool(values["flags"] & proto.PlayerFlag.LEGACY)
        if legacy:
            remote.legacy_radius_error.append(abs(math.dist(pos[:2], self.args.legacy_center) - self.args.legacy_radius))
        remote.buffer.push(values["sample_time"], self.relay_ms(now), pos, vel, proto.u16_to_yaw(values["yaw"]),
                           teleported=bool(values["flags"] & proto.PlayerFlag.TELEPORTED), has_vel=not legacy)
        remote.follower.on_sample(seq, pos)

    def on_entity_snapshot(self, values: dict, now: float) -> None:
        if not self.clock_sync.synced:
            self.unsynced_dropped += 1
            return
        view = self.decoder.apply(values)
        if view is None:
            return
        tick = values["tick"]
        self.joiner_view_hashes[tick] = view_hash(view)
        arrival = self.relay_ms(now)
        self.entity_stream.push(values["sample_time"], arrival, (0.0, 0.0, 0.0))
        for record in values["records"]:
            net_id = record["net_id"]
            if record.get("remove"):
                continue
            if any(key in record for key in ("spawn", "pos", "pos_delta", "vel", "yaw", "quat")):
                state = view.get(net_id)
                if state is None:
                    continue
                buffer = self.entity_buffers.get(net_id)
                if buffer is None or "spawn" in record:
                    buffer = self.entity_buffers[net_id] = InterpBuffer(**InterpBuffer.ENTITY)
                buffer.push(values["sample_time"], arrival, tuple(proto.mm_to_meters(v) for v in state.pos),
                            tuple(proto.cms_to_velocity(v) for v in state.vel))
        if tick == self.decoder.latest:
            for net_id in set(self.entity_view) - set(view):
                self.entity_buffers.pop(net_id, None)
                self.entity_removals += 1
            self.entity_spawns += len(set(view) - set(self.entity_view))
            self.entity_view = view

    # ------------------------------------------------------------------ send

    def player_tick(self, now: float) -> None:
        if not self.clock_sync.synced:
            return  # sample_time would be meaningless before the relay clock is known
        self.player_ticks += 1
        relay_ms = self.relay_ms(now)
        truth = testworld.player_truth(self.role, relay_ms / 1000.0, self.args.player_path)
        x, y, z = truth["pos"]
        flags = int(proto.PlayerFlag.WEAPON_DRAWN) | (4 << proto.WEAPON_CLASS_SHIFT)
        move = proto.MoveState(truth.get("move", proto.MoveState.RUN))
        if move == proto.MoveState.SPRINT:
            flags |= int(proto.PlayerFlag.SPRINTING)
        vehicle = None
        if truth["driving"]:
            flags = int(proto.PlayerFlag.IN_VEHICLE | proto.PlayerFlag.DRIVING)
            move = proto.MoveState.VEHICLE
            lv = [proto.velocity_to_cms(v) for v in truth["vel"]]
            vehicle = {"vehicle_net": 0x8000 + self.peer_id, "px": x, "py": y, "pz": z,
                       "quat": proto.pack_quat(*truth["quat"]), "lvx": lv[0], "lvy": lv[1], "lvz": lv[2],
                       "avx": 0, "avy": 0, "avz": 126, "steer": 12, "throttle": 60, "brake": 0, "vflags": 1}
        self.snap_seq = (self.snap_seq + 1) & 0xFFFF
        body = proto.PlayerSnapshotCodec().encode({
            "snap_seq": self.snap_seq, "sample_time": int(relay_ms) & 0xFFFFFFFF, "x": x, "y": y, "z": z,
            "yaw": proto.yaw_to_u16(truth["yaw"]), "pitch": proto.pitch_to_i16(-3.5),
            "vx": proto.velocity_to_cms(truth["vel"][0]), "vy": proto.velocity_to_cms(truth["vel"][1]),
            "vz": proto.velocity_to_cms(truth["vel"][2]), "move_state": int(move), "health": 230,
            "flags": flags, "vehicle": vehicle})
        self.pending.append((proto.MsgType.PLAYER_SNAPSHOT, proto.PEER_BROADCAST, body))
        self.snapshots_sent += 1
        if self.role == proto.Role.HOST and self.epoch_ms is not None \
                and self.player_ticks % ENTITY_EVERY_N_PLAYER_TICKS == 0:
            self.pending.append((proto.MsgType.ENTITY_SNAPSHOT, proto.PEER_BROADCAST, self.entity_snapshot(relay_ms)))
        if self.role != proto.Role.HOST and self.decoder.latest:
            self.pending.append((proto.MsgType.SNAPSHOT_ACK, proto.PEER_BROADCAST,
                                 proto.SNAPSHOT_ACK.encode({"tick": self.decoder.latest})))
        self.flush(now)

    def entity_snapshot(self, relay_ms: float) -> bytes:
        t = (relay_ms - self.epoch_ms) / 1000.0
        truths = {spec.net_id: (spec, testworld.entity_truth(spec, t)) for spec in self.world if testworld.alive(spec, t)}
        viewer = None
        other_id, _ = self.other_peer()
        if other_id in self.remote and self.remote[other_id].last is not None:
            last = self.remote[other_id].last
            viewer = (last["x"], last["y"], last["z"])
        candidates = {net_id: (spec.kind, truth["pos"], spec.kind == proto.EntityKind.COMBAT_NPC)
                      for net_id, (spec, truth) in truths.items()}
        relevant, weights = self.interest.update(viewer or testworld.ORIGIN, candidates)
        states = {}
        for net_id in relevant:
            spec, truth = truths[net_id]
            combat = spec.kind == proto.EntityKind.COMBAT_NPC
            rotation = truth["quat"] if spec.kind == proto.EntityKind.VEHICLE else truth["yaw"]
            spawn_flags = {proto.EntityKind.CROWD_NPC: proto.SpawnFlag.CROWD,
                           proto.EntityKind.VEHICLE: proto.SpawnFlag.TRAFFIC}.get(spec.kind, 0)
            states[net_id] = quantize(spec.kind, int(spawn_flags), 2 if combat else 1, spec.record, spec.appearance,
                                      truth["pos"], rotation, truth["vel"], truth["move"], truth["flags"],
                                      truth["health"], target=PLAYER_TARGET_BASE + (other_id or 0) if combat else 0,
                                      weapon=0xA11CE if combat else 0)
        body, tick, _, view = self.encoder.encode(int(relay_ms) & 0xFFFFFFFF, states, weights)
        self.host_view_hashes[tick] = view_hash(view)
        self.entity_bytes.append(len(body))
        return body

    # ---------------------------------------------------------------- events

    def send_event(self, now: float, mtype: int, dest: int, values: dict) -> None:
        body = proto.encode_body(mtype, values)
        if not self.conn.queue_reliable(mtype, dest, body, now):
            raise RuntimeError("reliable window full")
        self.sent_events.append([int(mtype), dest, body.hex()])

    def events_tick(self, now: float) -> None:
        other_id, _ = self.other_peer(include_legacy=True)
        if self.ready_at is None:
            if other_id is not None and self.clock_sync.synced:
                self.ready_at = now + 0.3
            return
        if self.events_started and not self.done_sent and now > self.start + self.args.duration - DRAIN_S:
            self.done_sent = True  # the last event of this stream
            self.send_event(now, proto.MsgType.CHAT, proto.PEER_BROADCAST,
                            {"channel": 0, "text": f"{self.args.name}{DONE_SUFFIX}"})
        if now < self.ready_at or now > self.start + self.args.duration - DRAIN_S or other_id is None:
            return
        relay_ms = self.relay_ms(now)
        if not self.events_started:
            self.events_started = True
            self.next_event = now
            self.next_chat = now + 0.5
            if self.role == proto.Role.HOST:
                self.epoch_ms = int(relay_ms)
                self.send_event(now, proto.MsgType.WORLD_FACT, proto.PEER_BROADCAST,
                                {"fact_hash": testworld.EPOCH_FACT, "value": self.epoch_ms})
                self.send_event(now, proto.MsgType.SESSION_CONFIG, proto.PEER_BROADCAST, {
                    "npc_radius_m": 100, "vehicle_radius_m": 200, "entity_hz": 10, "joiner_population": 0,
                    "flags": 0})
        if not self.burst_done and now >= self.ready_at + 2.0:
            self.burst_done = True
            for _ in range(BURST_SIZE):
                self.send_event(now, proto.MsgType.EQUIP, proto.PEER_BROADCAST, self.equip_values())
        if self.role != proto.Role.HOST and not self.teleport_sent and now >= self.ready_at + 4.0:
            self.teleport_sent = True
            self.send_event(now, proto.MsgType.TELEPORT_REQ, other_id, {"req_id": 7, "mode": 0, "reserved": 0})
        if self.role == proto.Role.HOST and self.epoch_ms is not None:
            world_t = (relay_ms - self.epoch_ms) / 1000.0
            for spec in self.world:
                if spec.death_at is not None and world_t >= spec.death_at and spec.net_id not in self.deaths_sent:
                    self.deaths_sent.add(spec.net_id)
                    self.send_event(now, proto.MsgType.DEATH, proto.PEER_BROADCAST, {
                        "target_net": spec.net_id, "target_kind": 0, "cause": 1, "killer_net": other_id,
                        "killer_kind": 1, "flags": 0, "time_ms": int(relay_ms) & 0xFFFFFFFF})
        if now >= self.next_chat:
            self.next_chat += CHAT_INTERVAL_S
            self.event_counter += 1
            self.send_event(now, proto.MsgType.CHAT, proto.PEER_BROADCAST, {
                "channel": 0, "text": f"{self.args.name} chat #{self.event_counter} привет"})
        if now >= self.next_event:
            self.next_event += EVENT_INTERVAL_S
            self.scripted_event(now, other_id, relay_ms)

    def equip_values(self) -> dict:
        self.event_counter += 1
        k = self.event_counter
        return {"slot": k % 3, "weapon_class": 1 + k % 10, "flags": 0, "item_record": 0xA000_0000 + k,
                "appearance": k, "ammo": k & 0xFFFF}

    def scripted_event(self, now: float, other_id: int, relay_ms: float) -> None:
        truth = testworld.player_truth(self.role, relay_ms / 1000.0, self.args.player_path)
        x, y, z = truth["pos"]
        step = self.event_counter % 6
        if step == 0:
            self.send_event(now, proto.MsgType.EQUIP, proto.PEER_BROADCAST, self.equip_values())
            return
        self.event_counter += 1
        k = self.event_counter
        if step == 1 and self.role == proto.Role.HOST:
            self.send_event(now, proto.MsgType.TIME_WEATHER, proto.PEER_BROADCAST, {
                "game_seconds": 43200 + 60 * k, "time_scale_x100": 100, "flags": 0, "weather_id": k % 8,
                "weather_record": 0xB000 + k % 8, "transition_s": 10})
        elif step == 1:
            self.send_event(now, proto.MsgType.MOD_LIST, proto.PEER_BROADCAST, {
                "chunk": 0, "chunks": 1, "text": ";".join(f"{n}@{v}" for n, v in DEMO_MODS)[:250]})
        elif step == 2:
            if self.role == proto.Role.HOST:
                target, kind = other_id, 1
            else:
                target, kind = 1 + k % len(self.world), 0
            self.send_event(now, proto.MsgType.HIT, proto.PEER_BROADCAST, {
                "target_net": target, "target_kind": kind, "hit_zone": k % 16, "attack": 1, "flags": 0,
                "damage": 12.5 + k, "weapon_record": 0xA000_0000 + k, "rx": 0, "ry": 0, "rz": 120,
                "time_ms": int(relay_ms) & 0xFFFFFFFF})
        elif step == 3:
            self.send_event(now, proto.MsgType.VEHICLE_ENTER, proto.PEER_BROADCAST, {
                "vehicle_net": 0x8000 + self.peer_id, "seat": 0, "flags": 0, "record": 0xC000_0000 + k,
                "appearance": k, "x": x, "y": y, "z": z, "quat": proto.pack_quat(*truth["quat"])})
        elif step == 4:
            self.send_event(now, proto.MsgType.VEHICLE_EXIT, proto.PEER_BROADCAST, {
                "vehicle_net": 0x8000 + self.peer_id, "seat": 0, "flags": 0, "x": x, "y": y, "z": z,
                "yaw": proto.yaw_to_u16(truth["yaw"])})
        elif self.role == proto.Role.HOST:
            self.send_event(now, proto.MsgType.WORLD_FACT, proto.PEER_BROADCAST,
                            {"fact_hash": 0xFAC7_0000 + k % 64, "value": k})
        else:
            self.send_event(now, proto.MsgType.EQUIP, proto.PEER_BROADCAST, self.equip_values())

    # ---------------------------------------------------------------- render

    def render_frame(self, now: float, dt_s: float) -> None:
        self.frame_index += 1
        relay_ms = self.relay_ms(now)
        dt_ms = max(1.0, dt_s * 1000.0)
        other_id, _ = self.other_peer()
        remote = self.remote.get(other_id)
        if remote is not None and remote.buffer.samples and self.events_started:
            self.rendered_peer = other_id
            render_t = remote.buffer.render_time(relay_ms, dt_ms)
            pos, yaw, mode = remote.buffer.sample_at(render_t)
            truth_state = testworld.player_truth(remote.role, render_t / 1000.0, self.args.player_path)
            truth = truth_state["pos"]
            error = distance(pos, truth)
            yaw_error = abs(((yaw - truth_state["yaw"] + 180.0) % 360.0) - 180.0)
            self.player_errors.append(error)
            self.yaw_errors.append(yaw_error)
            by_motion = self.motion_errors.setdefault(truth_state["motion"], ([], []))
            by_motion[0].append(error)
            by_motion[1].append(yaw_error)
            self.render_delays.append(relay_ms - render_t)
            self.render_modes[mode] = self.render_modes.get(mode, 0) + 1
            self.v2_track.append((now, pos))
            follower_pos = remote.follower.frame(dt_s)
            if follower_pos is not None:
                self.v1_track.append((now, follower_pos, relay_ms))
            self.truth_track.append((now, truth))
        if self.role != proto.Role.HOST and self.epoch_ms is not None and self.frame_index % 3 == 0:
            self.render_entities(relay_ms, dt_ms * 3.0)

    def render_entities(self, relay_ms: float, dt_ms: float) -> None:
        if not self.entity_stream.samples:
            return
        render_t = self.entity_stream.render_time(relay_ms, dt_ms)
        world_t = (render_t - self.epoch_ms) / 1000.0
        for net_id in self.entity_view:
            buffer = self.entity_buffers.get(net_id)
            spec = self.world_by_id.get(net_id)
            if buffer is None or spec is None or not testworld.alive(spec, world_t):
                continue
            sample = buffer.sample_at(render_t)
            if sample is None:
                continue
            truth = testworld.entity_truth(spec, world_t)
            error = distance(sample[0], truth["pos"])
            self.entity_errors.append(error)
            if error > 0.25:
                newest = buffer.times[-1] if buffer.times else render_t
                self.entity_outliers.append([net_id, int(spec.kind), round(world_t, 2), round(error, 3), sample[2],
                                             len(buffer.times), round(render_t - newest, 1)])

    # ---------------------------------------------------------------- report

    def v1_best_delay(self) -> dict:
        """Constant delay that best aligns the v1 model with the truth, and its error."""
        if len(self.v1_track) < 30 or self.role is None:
            return {}
        remote = self.remote.get(self.rendered_peer)
        if remote is None:
            return {}
        best = None
        for delay in range(0, 505, 5):
            errors = [distance(pos, testworld.player_truth(remote.role, (relay_ms - delay) / 1000.0,
                                                           self.args.player_path)["pos"])
                      for _, pos, relay_ms in self.v1_track[::2]]
            rms = math.sqrt(sum(e * e for e in errors) / len(errors))
            if best is None or rms < best[1]:
                best = (delay, rms, errors)
        return {"best_delay_ms": best[0], "rms_error_m": best[1], "error": summarize(best[2])}

    def build_report(self) -> dict:
        report = {
            "args": vars(self.args), "implementation": "python client_v2.py", "start_perf": self.start,
            "welcome": self.welcome, "reject": self.reject,
            "peer_id": self.peer_id, "role": None if self.role is None else proto.Role(self.role).name,
            "disconnected": self.disconnected, "peers_seen": sorted(self.ever_seen_peers),
        }
        if self.conn is None:
            return report
        remotes = {}
        for src, remote in self.remote.items():
            span = 0 if remote.first_seq is None else (remote.latest_seq - remote.first_seq) % 65536 + 1
            remotes[str(src)] = {"received": remote.received, "duplicates": remote.duplicates,
                                 "reordered": remote.reordered, "first_seq": remote.first_seq,
                                 "latest_seq": remote.latest_seq, "seq_span": span, "flags_seen": remote.flags_seen,
                                 "driving_seen": remote.driving_seen, "buffer": remote.buffer.counts,
                                 "legacy_radius_error": summarize(remote.legacy_radius_error)}
        v2_accel = accelerations(self.v2_track)
        v1_accel = accelerations([(t, p) for t, p, _ in self.v1_track])
        truth_accel = accelerations(self.truth_track)
        report.update({
            "clock": {"offset_ms": self.clock_sync.offset_ms, "rtt_ms": self.clock_sync.rtt_ms,
                      "estimator": CLOCK_ESTIMATOR, "steps": self.clock_steps, "samples": self.clock_samples[-5:]},
            "unsynced_dropped": self.unsynced_dropped,
            "link": self.conn.stats.as_dict(), "rtt_to_relay_ms": self.conn.rtt_ms(),
            "uplink": self.endpoint.uplink.as_dict(), "downlink": self.endpoint.downlink.as_dict(),
            "link_stats_from_relay": {str(k): v for k, v in self.link_stats.items()},
            "invalid_messages": self.invalid_messages,
            "snapshots_sent": self.snapshots_sent, "remote_players": remotes,
            "sent_events": self.sent_events, "received_events": self.received_events,
            "events_started": self.events_started,
            "drain": {"done_sent": self.done_sent, "other_done": self.other_done, "linger_s": round(self.linger_s, 3),
                      "timed_out": self.linger_timed_out, "pending_reliable": len(self.conn.rel_pending)},
            "entity": {
                "encoder": self.encoder.stats, "bytes": summarize(self.entity_bytes),
                "host_view_hashes": {str(k): v for k, v in self.host_view_hashes.items()},
                "decoder": self.decoder.stats,
                "joiner_view_hashes": {str(k): v for k, v in self.joiner_view_hashes.items()},
                "spawns_seen": self.entity_spawns, "removals_seen": self.entity_removals,
                "view_size_end": len(self.entity_view),
                "alignment_error_m": summarize(self.entity_errors),
                "outliers_over_25cm": len(self.entity_outliers),
                "outlier_examples": sorted(self.entity_outliers, key=lambda o: -o[3])[:12],
                "stream_delay_target_ms": self.entity_stream.target_delay_ms(),
            },
            "render": {
                "player_error_m": summarize(self.player_errors),
                "yaw_error_deg": summarize(self.yaw_errors),
                "by_motion": {motion: {"error_m": summarize(errors), "yaw_error_deg": summarize(yaw_errors)}
                              for motion, (errors, yaw_errors) in sorted(self.motion_errors.items())},
                "render_delay_ms": summarize(self.render_delays),
                "modes": self.render_modes,
                "accel_v2_mps2": summarize(v2_accel),
                "accel_v1_model_mps2": summarize(v1_accel),
                "accel_truth_mps2": summarize(truth_accel),
                "v1_model": self.v1_best_delay(),
            },
        })
        return report


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="CP2077 Coop protocol v2 test client")
    parser.add_argument("--relay-host", default="127.0.0.1")
    parser.add_argument("--relay-port", type=int, default=11778)
    parser.add_argument("--role", choices=("host", "joiner"), required=True)
    parser.add_argument("--room", default="demo")
    parser.add_argument("--password", default="")
    parser.add_argument("--name", default=None)
    parser.add_argument("--join-flags", type=int, default=0)
    parser.add_argument("--duration", type=float, default=20.0)
    parser.add_argument("--entity-budget", type=int, default=1000)
    parser.add_argument("--world-seed", type=int, default=7)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--legacy-center", type=float, nargs=2, default=(-1400.0, 200.0))
    parser.add_argument("--legacy-radius", type=float, default=5.0)
    parser.add_argument("--player-path", choices=("demo", "course"), default="demo",
                        help="demo: host runs a circle, joiner drives; course: both run the scripted course")
    for leg in ("up", "down"):
        parser.add_argument(f"--{leg}-latency-ms", type=float, default=0.0)
        parser.add_argument(f"--{leg}-jitter-ms", type=float, default=0.0)
        parser.add_argument(f"--{leg}-loss-pct", type=float, default=0.0)
        parser.add_argument(f"--{leg}-dup-pct", type=float, default=0.0)
    parser.add_argument("--report", default=None, help="write the JSON report here")
    args = parser.parse_args(argv)
    if args.name is None:
        args.name = args.role
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    report = CoopClient(args).run()
    text = json.dumps(report, indent=1, default=str)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as handle:
            handle.write(text)
    status = "ok" if report.get("welcome") else f"rejected: {report.get('reject')}"
    print(f"[{args.role}] {status}; events sent={len(report.get('sent_events', []))} "
          f"received={len(report.get('received_events', []))}")
    return 0 if report.get("welcome") else 2


if __name__ == "__main__":
    sys.exit(main())
