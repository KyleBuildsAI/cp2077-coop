"""Relay for the CP2077CoopNet plugin (wire protocol v1, see src/core/Protocol.hpp).

The relay assigns peer ids, groups clients into rooms, forwards frames between peers of the
same room and publishes the room's member list. Reliability (acks, resends, ordering) is
end-to-end between the two game clients; the relay never looks inside peer payloads.

It can simulate a long-distance link on the forwarded (peer-to-peer) traffic, e.g. the
Russia <-> Warsaw <-> Los Angeles path: --latency-ms 160 --jitter-ms 15 --loss-pct 1

Usage:
    python tools/coopnet_relay.py                              # 127.0.0.1:11779
    python tools/coopnet_relay.py --host 0.0.0.0 --port 11779  # reachable from the internet/LAN
    python tools/coopnet_relay.py --latency-ms 160 --jitter-ms 15 --loss-pct 2 --seed 1
"""
import argparse
import heapq
import random
import select
import socket
import struct
import sys
import time

MAGIC = 0x324E5043
PROTOCOL_VERSION = 1
HEADER = struct.Struct("<IBBHHHHIH")  # magic version channel sender target sequence ack ackBits payloadSize
MAX_DATAGRAM = 1200

RELAY_ID = 0
BROADCAST_ID = 0xFFFF
CONTROL_CHANNEL = 0

OP_HELLO = 1
OP_WELCOME = 2
OP_PEERS = 3
OP_PING = 4
OP_PONG = 5
OP_BYE = 6
OP_REJECT = 8

CLIENT_TIMEOUT_SECONDS = 10.0
PEERS_INTERVAL_SECONDS = 1.0
STATS_INTERVAL_SECONDS = 5.0


class Client:
    def __init__(self, client_id, address, nonce, room, now):
        self.client_id = client_id
        self.address = address
        self.nonce = nonce
        self.room = room
        self.last_seen = now
        self.frames_in = 0
        self.frames_forwarded = 0
        self.frames_dropped = 0

    def label(self):
        return f"#{self.client_id} {self.address[0]}:{self.address[1]} room={self.room}"


def encode_frame(channel, sender, target, payload, sequence=0, ack=0, ack_bits=0):
    return HEADER.pack(MAGIC, PROTOCOL_VERSION, channel, sender, target, sequence, ack, ack_bits, len(payload)) + payload


def decode_frame(data):
    """Returns (header_fields, payload) or None when the datagram is not a valid v1 frame."""
    if len(data) < HEADER.size or len(data) > MAX_DATAGRAM:
        return None
    fields = HEADER.unpack_from(data)
    magic, version, payload_size = fields[0], fields[1], fields[8]
    if magic != MAGIC or version != PROTOCOL_VERSION or payload_size != len(data) - HEADER.size:
        return None
    return fields, data[HEADER.size:]


class Relay:
    def __init__(self, args):
        self.args = args
        self.rng = random.Random(args.seed)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((args.host, args.port))
        self.sock.setblocking(False)
        self.clients = {}  # address -> Client
        self.rooms = {}    # room name -> {client_id: Client}
        self.outgoing = []  # heap of (send_at, counter, data, address)
        self.last_send_at = {}  # address -> latest scheduled delivery (FIFO mode)
        self.counter = 0
        self.bad_frames = 0
        self.unknown_frames = 0
        self.spoofed_frames = 0

    # ---- helpers -------------------------------------------------------------------------

    def log(self, text):
        print(f"{time.strftime('%H:%M:%S')} {text}", flush=True)

    def send_now(self, data, address):
        try:
            self.sock.sendto(data, address)
        except OSError as error:
            self.log(f"send to {address} failed: {error}")

    def send_control(self, address, body):
        self.send_now(encode_frame(CONTROL_CHANNEL, RELAY_ID, BROADCAST_ID, body), address)

    def schedule(self, data, address, now, client):
        if self.rng.random() * 100.0 < self.args.loss_pct:
            client.frames_dropped += 1
            return
        delay = (self.args.latency_ms + self.rng.random() * self.args.jitter_ms) / 1000.0
        send_at = now + delay
        if not self.args.reorder:
            # Real paths jitter but rarely reorder: keep per-destination FIFO order.
            send_at = max(send_at, self.last_send_at.get(address, 0.0))
            self.last_send_at[address] = send_at
        if send_at <= now:
            self.send_now(data, address)
        else:
            self.counter += 1
            heapq.heappush(self.outgoing, (send_at, self.counter, data, address))
        client.frames_forwarded += 1

    def next_free_id(self, room_members):
        candidate = 1
        while candidate in room_members:
            candidate += 1
        return candidate

    def peers_body(self, room):
        members = self.rooms.get(room, {})
        body = struct.pack("<BH", OP_PEERS, len(members))
        for client_id, client in sorted(members.items()):
            body += struct.pack("<HI", client_id, client.nonce)
        return body

    def publish_room(self, room):
        body = self.peers_body(room)
        for client in self.rooms.get(room, {}).values():
            self.send_control(client.address, body)

    def remove_client(self, client, reason):
        self.clients.pop(client.address, None)
        members = self.rooms.get(client.room, {})
        members.pop(client.client_id, None)
        if not members:
            self.rooms.pop(client.room, None)
        self.log(f"EVENT client left {client.label()} ({reason})")
        self.publish_room(client.room)

    # ---- protocol ------------------------------------------------------------------------

    def handle_hello(self, address, payload, now):
        if len(payload) < 6:
            self.bad_frames += 1
            return
        nonce = struct.unpack_from("<I", payload, 1)[0]
        room_length = payload[5]
        room = payload[6:6 + room_length].decode("utf-8", errors="replace") or "default"

        existing = self.clients.get(address)
        if existing is not None and existing.nonce == nonce:
            existing.last_seen = now
            self.send_control(address, struct.pack("<BHI", OP_WELCOME, existing.client_id, nonce))
            self.send_control(address, self.peers_body(existing.room))
            return
        if existing is not None:
            self.remove_client(existing, "re-hello with new session")

        members = self.rooms.setdefault(room, {})
        if len(members) >= self.args.max_room:
            reason = b"room_full"
            self.send_control(address, struct.pack("<BB", OP_REJECT, len(reason)) + reason)
            if not members:
                self.rooms.pop(room, None)
            return
        client = Client(self.next_free_id(members), address, nonce, room, now)
        members[client.client_id] = client
        self.clients[address] = client
        self.log(f"EVENT client joined {client.label()} nonce={nonce:08x}")
        self.send_control(address, struct.pack("<BHI", OP_WELCOME, client.client_id, nonce))
        self.publish_room(room)

    def handle_datagram(self, data, address, now):
        decoded = decode_frame(data)
        if decoded is None:
            self.bad_frames += 1
            if len(data) >= 5 and struct.unpack_from("<I", data)[0] == MAGIC and data[4] != PROTOCOL_VERSION:
                # A client speaking another protocol version gets told instead of hanging in "connecting".
                reason = f"protocol_version {data[4]} unsupported, relay speaks {PROTOCOL_VERSION}".encode()
                self.send_control(address, struct.pack("<BB", OP_REJECT, len(reason)) + reason)
            return
        (_, _, channel, sender, target, _, _, _, _), payload = decoded

        if target == RELAY_ID:
            if channel != CONTROL_CHANNEL or not payload:
                self.bad_frames += 1
                return
            op = payload[0]
            if op == OP_HELLO:
                self.handle_hello(address, payload, now)
                return
            client = self.clients.get(address)
            if client is None:
                self.unknown_frames += 1
                return
            client.last_seen = now
            if op == OP_PING:
                self.send_control(address, bytes([OP_PONG]) + payload[1:])
            elif op == OP_BYE:
                self.remove_client(client, "bye")
            return

        client = self.clients.get(address)
        if client is None:
            self.unknown_frames += 1
            return
        if sender != client.client_id:
            self.spoofed_frames += 1
            return
        client.last_seen = now
        client.frames_in += 1
        members = self.rooms.get(client.room, {})
        if self.args.dump:
            self.log(f"{client.label()} -> {target} ch={channel} {len(payload)}B")
        if target == BROADCAST_ID:
            for other in members.values():
                if other is not client:
                    self.schedule(data, other.address, now, client)
            return
        other = members.get(target)
        if other is not None and other is not client:
            self.schedule(data, other.address, now, client)

    # ---- main loop -----------------------------------------------------------------------

    def run(self):
        self.log(
            f"coopnet relay v{PROTOCOL_VERSION} on {self.args.host}:{self.args.port} "
            f"latency={self.args.latency_ms}ms jitter={self.args.jitter_ms}ms loss={self.args.loss_pct}% "
            f"reorder={self.args.reorder}"
        )
        last_peers = time.monotonic()
        last_stats = time.monotonic()
        while True:
            now = time.monotonic()
            while self.outgoing and self.outgoing[0][0] <= now:
                _, _, data, address = heapq.heappop(self.outgoing)
                self.send_now(data, address)

            timeout = 0.05
            if self.outgoing:
                timeout = max(0.0, min(timeout, self.outgoing[0][0] - now))
            readable, _, _ = select.select([self.sock], [], [], timeout)
            if readable:
                for _ in range(256):
                    try:
                        data, address = self.sock.recvfrom(2048)
                    except BlockingIOError:
                        break
                    except ConnectionResetError:
                        continue  # ICMP port unreachable from a client that went away
                    self.handle_datagram(data, address, time.monotonic())

            now = time.monotonic()
            for client in [c for c in self.clients.values() if now - c.last_seen > CLIENT_TIMEOUT_SECONDS]:
                self.remove_client(client, "timeout")
            if now - last_peers >= PEERS_INTERVAL_SECONDS:
                for room in list(self.rooms):
                    self.publish_room(room)
                last_peers = now
            if now - last_stats >= STATS_INTERVAL_SECONDS:
                self.print_stats()
                last_stats = now

    def print_stats(self):
        self.log(
            f"[STATS] clients={len(self.clients)} rooms={len(self.rooms)} bad={self.bad_frames} "
            f"unknown={self.unknown_frames} spoofed={self.spoofed_frames}"
        )
        for client in self.clients.values():
            self.log(
                f"[STATS]   {client.label():<36} in={client.frames_in} fwd={client.frames_forwarded} "
                f"simDropped={client.frames_dropped} idle={time.monotonic() - client.last_seen:.1f}s"
            )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="CP2077CoopNet relay (protocol v1)")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (0.0.0.0 for remote players)")
    parser.add_argument("--port", type=int, default=11779)
    parser.add_argument("--latency-ms", type=float, default=0.0, help="one-way delay added to forwarded frames")
    parser.add_argument("--jitter-ms", type=float, default=0.0, help="extra uniform random delay per frame")
    parser.add_argument("--loss-pct", type=float, default=0.0, help="percent of forwarded frames dropped")
    parser.add_argument("--reorder", action="store_true",
                        help="let jitter reorder frames (default keeps per-destination FIFO order)")
    parser.add_argument("--max-room", type=int, default=8, help="players per room")
    parser.add_argument("--seed", type=int, default=None, help="random seed for reproducible loss/jitter")
    parser.add_argument("--dump", action="store_true", help="log every forwarded frame")
    return parser.parse_args(argv)


if __name__ == "__main__":
    try:
        Relay(parse_args()).run()
    except KeyboardInterrupt:
        print("relay stopped")
        sys.exit(0)
