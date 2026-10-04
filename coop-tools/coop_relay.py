"""Local CP2077 Coop relay server for testing two game instances on one PC.

Speaks the same protocol as the public relay:
  client -> relay : CP1,<seq>,<x>,<y>,<z>,<w>,<fx>,<fy>
  relay -> client : WELCOME,<id>                      (reply to every CP1, ignored by the DLL)
  relay -> others : RP1,<id>,<seq>,<x>,<y>,<z>,<w>,<fx>,<fy>

Can simulate a real-world link (latency, jitter, loss) so a single-PC test
behaves like Los Angeles <-> Warsaw <-> Russia.

Usage:
    python coop-tools/coop_relay.py                          # clean local link
    python coop-tools/coop_relay.py --latency-ms 120 --jitter-ms 20 --loss-pct 1
    python coop-tools/coop_relay.py --dump                   # print every packet

Point each instance at it with: python coop-tools/use_server.py local
"""
import argparse
import heapq
import os
import random
import socket
import sys
import time

CLIENT_TIMEOUT_SECONDS = 10.0
STATS_INTERVAL_SECONDS = 5.0


class Client:
    def __init__(self, client_id, address, now):
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

    def label(self):
        return f"#{self.client_id} {self.address[0]}:{self.address[1]}"


def parse_args():
    parser = argparse.ArgumentParser(description="Local CP2077 Coop relay")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (use 0.0.0.0 to accept LAN players)")
    parser.add_argument("--port", type=int, default=11778)
    parser.add_argument("--latency-ms", type=float, default=0.0, help="one-way delay added to every forwarded packet")
    parser.add_argument("--jitter-ms", type=float, default=0.0, help="random extra delay 0..jitter per packet")
    parser.add_argument("--loss-pct", type=float, default=0.0, help="percent of forwarded packets dropped")
    parser.add_argument("--dump", action="store_true", help="print every packet")
    parser.add_argument("--log", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "coop_relay.log"))
    parser.add_argument("--seed", type=int, default=None, help="random seed for reproducible jitter/loss")
    return parser.parse_args()


def log_line(handle, text):
    stamped = f"{time.strftime('%H:%M:%S')} {text}"
    print(stamped)
    handle.write(stamped + "\n")
    handle.flush()


def parse_cp1(text):
    """Returns (sequence, payload_fields) or None for malformed packets."""
    parts = text.strip().split(",")
    if len(parts) != 8 or parts[0] != "CP1":
        return None
    try:
        sequence = int(parts[1])
        [float(value) for value in parts[2:]]
    except ValueError:
        return None
    return sequence, parts[2:]


def track_sequence(client, sequence):
    if client.last_sequence is not None:
        if sequence > client.last_sequence + 1:
            client.gaps += sequence - client.last_sequence - 1
        elif sequence <= client.last_sequence:
            client.out_of_order += 1
            return
    client.last_sequence = sequence


def print_stats(handle, clients, elapsed, args):
    link = f"link: +{args.latency_ms:.0f}ms ±{args.jitter_ms:.0f}ms loss {args.loss_pct:.1f}%"
    log_line(handle, f"[STATS] clients={len(clients)} {link}")
    for client in clients.values():
        rate = client.window_received / elapsed if elapsed > 0 else 0.0
        log_line(
            handle,
            f"[STATS]   {client.label():<24} in={rate:5.1f}/s total={client.received} "
            f"gaps={client.gaps} late={client.out_of_order} fwd={client.forwarded} dropped={client.dropped} "
            f"idle={time.monotonic() - client.last_seen:.1f}s",
        )
        client.window_received = 0


def run(args):
    rng = random.Random(args.seed)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.port))
    sock.settimeout(0.002)

    clients = {}
    next_id = 1
    outgoing = []  # heap of (send_at, counter, data, address)
    counter = 0
    last_stats = time.monotonic()

    with open(args.log, "a", encoding="utf-8") as handle:
        log_line(handle, f"relay listening on {args.host}:{args.port}  latency={args.latency_ms}ms jitter={args.jitter_ms}ms loss={args.loss_pct}%")
        while True:
            now = time.monotonic()

            while outgoing and outgoing[0][0] <= now:
                _, _, data, address = heapq.heappop(outgoing)
                try:
                    sock.sendto(data, address)
                except OSError as error:
                    log_line(handle, f"send to {address} failed: {error}")

            try:
                data, address = sock.recvfrom(2048)
            except socket.timeout:
                data = None
            except ConnectionResetError:
                # Windows reports ICMP port-unreachable from a closed client as a reset; ignore
                data = None

            if data is not None:
                text = data.decode("ascii", errors="replace")
                parsed = parse_cp1(text)
                if parsed is None:
                    log_line(handle, f"ignored malformed packet from {address}: {text[:80]!r}")
                else:
                    sequence, fields = parsed
                    client = clients.get(address)
                    if client is None:
                        client = Client(next_id, address, now)
                        clients[address] = client
                        next_id += 1
                        log_line(handle, f"EVENT client joined {client.label()}")
                    client.last_seen = now
                    client.received += 1
                    client.window_received += 1
                    track_sequence(client, sequence)

                    sock.sendto(f"WELCOME,{client.client_id}".encode(), address)

                    forward = f"RP1,{client.client_id},{sequence}," + ",".join(fields)
                    if args.dump:
                        print(f"{client.label()} -> {forward}")
                    for other in clients.values():
                        if other is client:
                            continue
                        if rng.random() * 100.0 < args.loss_pct:
                            client.dropped += 1
                            continue
                        delay = (args.latency_ms + rng.random() * args.jitter_ms) / 1000.0
                        counter += 1
                        heapq.heappush(outgoing, (now + delay, counter, forward.encode(), other.address))
                        client.forwarded += 1

            for address in [a for a, c in clients.items() if now - c.last_seen > CLIENT_TIMEOUT_SECONDS]:
                log_line(handle, f"EVENT client timed out {clients[address].label()}")
                del clients[address]

            if now - last_stats >= STATS_INTERVAL_SECONDS:
                print_stats(handle, clients, now - last_stats, args)
                last_stats = now


if __name__ == "__main__":
    try:
        run(parse_args())
    except KeyboardInterrupt:
        print("\nrelay stopped")
        sys.exit(0)
