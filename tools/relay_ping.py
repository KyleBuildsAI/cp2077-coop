"""Measure round-trip time to a v2 relay without joining a room.

Sends a padded HELLO and times the stateless CHALLENGE reply, so it creates
no session on the relay. Run it from each player's PC against every
candidate relay (e.g. Frankfurt, Warsaw, Ashburn) and pick the one with the
lowest sum (or lowest maximum) of the two players' RTTs.

It refuses to probe v1 relays: a CP1 probe would show up as a phantom player
in other people's games.

Usage: python tools/relay_ping.py 203.0.113.10:11778 [--count 20]
"""
from __future__ import annotations

import argparse
import os
import secrets
import socket
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from coopnet import proto  # noqa: E402

INTERVAL_S = 0.5  # stays under the relay's 4 handshakes/s per IP budget


def probe(address, count: int, timeout: float) -> list:
    info = {"minor": proto.PROTO_MINOR, "role": 0, "join_flags": 0, "caps": 0, "game_build": 0, "mod_major": 0,
            "mod_minor": 0, "mod_patch": 0, "mod_hash": 0, "mod_count": 0, "client_nonce": 0, "resume_token": 0,
            "room": "ping", "name": "ping"}
    family = socket.AF_INET6 if ":" in address[0] else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    samples = []
    for _ in range(count):
        info["client_nonce"] = secrets.randbits(64)
        started = time.perf_counter()
        sock.sendto(proto.encode_hello(info), address)
        try:
            while True:
                data, _ = sock.recvfrom(2048)
                ptype = proto.decode_packet(data)[0]
                if ptype in (proto.PacketType.CHALLENGE, proto.PacketType.REJECT):
                    samples.append((time.perf_counter() - started) * 1000.0)
                    break
        except (socket.timeout, ConnectionResetError, proto.ProtocolError):
            samples.append(None)  # no reply, ICMP port unreachable (Windows reports a reset), or garbage
        time.sleep(INTERVAL_S)
    sock.close()
    return samples


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="RTT to a CP2077 Coop v2 relay")
    parser.add_argument("relay", help="host:port")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=1.0)
    args = parser.parse_args(argv)
    host, _, port = args.relay.rpartition(":")
    address = (socket.getaddrinfo(host.strip("[]"), int(port), proto=socket.IPPROTO_UDP)[0][4][0], int(port))
    samples = probe(address, args.count, args.timeout)
    good = [s for s in samples if s is not None]
    lost = len(samples) - len(good)
    if not good:
        print(f"{args.relay}: no reply ({lost}/{len(samples)} lost) - not a v2 relay, blocked, or down")
        return 1
    good.sort()
    print(f"{args.relay}: rtt min {good[0]:.1f} ms, median {statistics.median(good):.1f} ms, "
          f"p90 {good[int(0.9 * (len(good) - 1))]:.1f} ms, jitter (stdev) {statistics.pstdev(good):.1f} ms, "
          f"lost {lost}/{len(samples)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
