"""v1 client emulator: what Jakub's CP2077Coop.dll + init.lua put on the wire.

Sends CP1 at 30 Hz along a 5 m circle with the init.lua payload rotation
(flags, a ping every second), and records every WELCOME / RP1 it gets back:
positions, decoded flags, time/weather payloads, pong round trips.
Used by run_demo.py to prove that the v2 relay keeps the old DLL working
(v1 pool) and that the LEGACY_BRIDGE room translates both ways.
"""
from __future__ import annotations

import argparse
import json
import math
import socket
import sys
import time

from coopnet import legacy

clock = time.perf_counter
SEND_INTERVAL_S = 1.0 / 30.0
PING_INTERVAL_S = 1.0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="v1 (CP1/RP1) client emulator")
    parser.add_argument("--relay-host", default="127.0.0.1")
    parser.add_argument("--relay-port", type=int, default=11778)
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--center", type=float, nargs=2, default=(-1400.0, 200.0))
    parser.add_argument("--radius", type=float, default=5.0)
    parser.add_argument("--flags", type=int, default=legacy.V1_WEAPON_DRAWN, help="v1 FLAGS payload value")
    parser.add_argument("--report", default=None)
    return parser.parse_args(argv)


def run(args) -> dict:
    relay = (socket.gethostbyname(args.relay_host), args.relay_port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if hasattr(socket, "SIO_UDP_CONNRESET"):
        sock.ioctl(socket.SIO_UDP_CONNRESET, False)
    sock.bind(("0.0.0.0", 0))
    sock.setblocking(False)
    start = clock()
    seq = 0
    slot = 0
    next_send = start
    last_ping = start - PING_INTERVAL_S
    ping_token = 0
    ping_sent_at = {}
    report = {"welcome_ids": [], "sent": 0, "rp1": {}, "malformed": 0, "pong_rtt_ms": [], "args": vars(args)}
    while clock() - start < args.duration:
        now = clock()
        if now >= next_send:
            next_send += SEND_INTERVAL_S
            seq += 1
            slot += 1
            angle = (now - start) * 0.8
            x = args.center[0] + args.radius * math.cos(angle)
            y = args.center[1] + args.radius * math.sin(angle)
            if now - last_ping >= PING_INTERVAL_S:
                last_ping = now
                ping_token = (ping_token + 1) % legacy.TYPE_STRIDE
                ping_sent_at[ping_token] = now
                payload = legacy.TYPE_PING * legacy.TYPE_STRIDE + ping_token
            else:
                payload = legacy.TYPE_FLAGS * legacy.TYPE_STRIDE + args.flags
            fx, fy = legacy.encode_forward(-math.sin(angle), math.cos(angle), payload)
            sock.sendto(legacy.format_cp1(seq, x, y, 22.0, 1.0, fx, fy), relay)
            report["sent"] += 1
        while True:
            try:
                data, address = sock.recvfrom(2048)
            except BlockingIOError:
                break
            except ConnectionResetError:
                continue
            handle(data, report, ping_sent_at, clock())
        time.sleep(0.0005)
    sock.close()
    for entry in report["rp1"].values():
        entry["flags_values"] = sorted(entry["flags_values"])
        entry["time_values"] = sorted(entry["time_values"])
        entry["weather_values"] = sorted(entry["weather_values"])
    report["welcome_ids"] = sorted(set(report["welcome_ids"]))
    return report


def handle(data: bytes, report: dict, ping_sent_at: dict, now: float) -> None:
    text = data.decode("ascii", errors="replace").strip()
    parts = text.split(",")
    if parts[0] == "WELCOME" and len(parts) == 2:
        report["welcome_ids"].append(int(parts[1]))
        return
    if parts[0] != "RP1" or len(parts) != 9:
        report["malformed"] += 1
        return
    try:
        sender, seq = int(parts[1]), int(parts[2])
        x, y, z, w, fx, fy = (float(value) for value in parts[3:])
    except ValueError:
        report["malformed"] += 1
        return
    entry = report["rp1"].setdefault(str(sender), {
        "count": 0, "last_seq": 0, "regressions": 0, "flags_values": set(), "time_values": set(),
        "weather_values": set(), "pongs": 0, "positions": []})
    entry["count"] += 1
    if seq <= entry["last_seq"]:
        entry["regressions"] += 1
    entry["last_seq"] = max(entry["last_seq"], seq)
    if len(entry["positions"]) < 400:
        entry["positions"].append([round(x, 3), round(y, 3), round(z, 3)])
    _, _, payload = legacy.decode_forward(fx, fy)
    if payload is None:
        return
    kind, value = legacy.split_payload(payload)
    if kind == legacy.TYPE_FLAGS:
        entry["flags_values"].add(value)
    elif kind == legacy.TYPE_TIME:
        entry["time_values"].add(value)
    elif kind == legacy.TYPE_WEATHER:
        entry["weather_values"].add(value)
    elif kind == legacy.TYPE_PONG:
        entry["pongs"] += 1
        sent_at = ping_sent_at.pop(value, None)
        if sent_at is not None:
            report["pong_rtt_ms"].append(round((now - sent_at) * 1000.0, 2))


def main(argv=None) -> int:
    args = parse_args(argv)
    report = run(args)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=1)
    print(f"[v1] sent={report['sent']} welcome={report['welcome_ids']} "
          f"rp1 from={ {k: v['count'] for k, v in report['rp1'].items()} }")
    return 0


if __name__ == "__main__":
    sys.exit(main())
