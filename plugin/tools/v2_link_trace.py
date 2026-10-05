"""Trace replay between the C++ v2 Connection (src/v2/V2Reliability) and relay/coopnet/reliability.py.

    python tools/v2_link_trace.py run --exe build/Release/coopnet_v2_reliability_tests.exe

1. Runs reliability.py's Connection through the scenarios of the relay's tests/test_reliability.py
   (two connections over lossy pipes at 0/10/30/45 % loss with duplicates and reordering, bursts
   that fill the window, sequence wrap-around), a reordering-heavy link and hand-written edge
   cases, and writes every call and its result to build/golden/link_trace.txt.
2. Has coopnet_v2_reliability_tests replay the calls on the C++ Connection ("trace <file>"). The
   C++ side must produce byte-identical datagrams, the same deliveries and verdicts, and the same
   state after every step: sequences, ack bits, queues, every counter, and SRTT/RTTVAR/RTO and the
   timestamps to the last bit.

Trace format, one record per line, fields separated by single spaces, times as Python float.hex():

    SCENARIO <name> ... END
    NEW <id> <token> <max_packet>
    START <id> <next_seq> <rel_next> <rel_expected>        (first use of a connection)
    QUEUE <id> <now> <type> <peer> <body|-> <1 queued|0 window full|E rejected>
    DUE <id> <now> <0|1>
    BUILD <id> <now> <force> <n> (<type> <peer> <body|->){n}, then OUT <datagram> lines, BUILT <count>
    RECV <id> <now> <seq> <ack> <ack_bits> <size> <body|->, then GOT <type> <peer> <rel> <body|->
         lines and RECVD <count>, or RECVERR
    STATE <id> <srtt|-> <rttvar|-> <rto> <next_seq> <remote_seq> <recv_bits> <ack_pending>
          <rel_next> <rel_expected> <pending> <sent> <buffered> <last_send|-> <last_recv|-> <stats...>

The relay checkout is found through --relay, COOPNET_RELAY_DIR or the sibling folder ../relay.
Other subcommand: generate <out>.
"""
from __future__ import annotations

import argparse
import heapq
import os
import random
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
proto = None
reliability = None

STAT_FIELDS = ("packets_sent", "packets_received", "packets_acked", "packets_lost", "duplicates", "too_old",
               "bytes_sent", "bytes_received", "reliable_sent", "reliable_resent", "reliable_delivered",
               "reliable_duplicates", "reliable_out_of_window", "recv_span", "rtt_samples", "rtt_skipped")


def load_relay(relay_dir: str) -> None:
    global proto, reliability
    if not os.path.isfile(os.path.join(relay_dir, "coopnet", "reliability.py")):
        raise SystemExit(f"relay checkout not found at {relay_dir} (use --relay or COOPNET_RELAY_DIR)")
    sys.path.insert(0, relay_dir)
    from coopnet import proto as proto_module  # noqa: E402
    from coopnet import reliability as reliability_module  # noqa: E402
    proto = proto_module
    reliability = reliability_module
    if not hasattr(reliability.LinkStats(), "rtt_skipped"):
        raise SystemExit("relay reliability.py predates the RTT rule (rtt_skipped); update the relay checkout")


def default_relay() -> str:
    return os.environ.get("COOPNET_RELAY_DIR") or os.path.join(os.path.dirname(ROOT), "relay")


def hexb(data: bytes) -> str:
    return data.hex() if data else "-"


def fhex(value) -> str:
    return "-" if value is None else float(value).hex()


# ---------------------------------------------------------------------------
# a Connection that writes every call to the trace
# ---------------------------------------------------------------------------

class Tracer:
    def __init__(self):
        self.lines = []
        self.next_id = 0

    def connection_class(self):
        tracer = self
        base = reliability.Connection

        class Traced(base):
            def __init__(self, token: int = 0, max_packet: int = proto.MAX_PACKET):
                super().__init__(token, max_packet)
                self.trace_id = tracer.next_id
                tracer.next_id += 1
                self.trace_started = False
                tracer.lines.append(f"NEW {self.trace_id} {token} {max_packet}")

            def _start(self):
                if not self.trace_started:
                    self.trace_started = True
                    tracer.lines.append(f"START {self.trace_id} {self.next_seq} {self.rel_next} {self.rel_expected}")

            def _state(self):
                stats = self.stats
                tracer.lines.append(
                    f"STATE {self.trace_id} {fhex(self.srtt)} {fhex(self.rttvar)} {fhex(self.rto)} {self.next_seq} "
                    f"{self.remote_seq} {self.recv_bits} {int(self.ack_pending)} {self.rel_next} {self.rel_expected} "
                    f"{len(self.rel_pending)} {len(self.sent)} {len(self.rel_buffer)} {fhex(self.last_send)} "
                    f"{fhex(self.last_recv)} " + " ".join(str(getattr(stats, name)) for name in STAT_FIELDS))

            def queue_reliable(self, mtype, peer, body, now):
                self._start()
                try:
                    result = super().queue_reliable(mtype, peer, body, now)
                except proto.ProtocolError:
                    tracer.lines.append(f"QUEUE {self.trace_id} {fhex(now)} {mtype} {peer} {hexb(body)} E")
                    raise
                tracer.lines.append(f"QUEUE {self.trace_id} {fhex(now)} {mtype} {peer} {hexb(body)} {int(result)}")
                return result

            def reliable_due(self, now):
                self._start()
                result = super().reliable_due(now)
                tracer.lines.append(f"DUE {self.trace_id} {fhex(now)} {int(result)}")
                return result

            def build_packets(self, now, unreliable=(), force=False):
                self._start()
                unreliable = list(unreliable)
                packets = super().build_packets(now, unreliable, force)
                tracer.lines.append(f"BUILD {self.trace_id} {fhex(now)} {int(bool(force))} {len(unreliable)}"
                                    + "".join(f" {mtype} {peer} {hexb(body)}" for mtype, peer, body in unreliable))
                tracer.lines.extend(f"OUT {packet.hex()}" for packet in packets)
                tracer.lines.append(f"BUILT {len(packets)}")
                self._state()
                return packets

            def on_packet(self, now, seq, ack, ack_bits, body, size=0):
                self._start()
                head = f"RECV {self.trace_id} {fhex(now)} {seq} {ack} {ack_bits} {size} {hexb(body)}"
                try:
                    delivered = super().on_packet(now, seq, ack, ack_bits, body, size)
                except proto.ProtocolError:
                    tracer.lines.extend([head, "RECVERR"])
                    self._state()
                    raise
                tracer.lines.append(head)
                tracer.lines.extend(f"GOT {mtype} {peer} {int(rel)} {hexb(payload)}"
                                    for mtype, peer, rel, payload in delivered)
                tracer.lines.append(f"RECVD {len(delivered)}")
                self._state()
                return delivered

        return Traced


# ---------------------------------------------------------------------------
# scenarios
# ---------------------------------------------------------------------------

class LossyPipe:
    """tests/test_reliability.py's in-memory link: loss, duplication, reordering via random delay."""

    def __init__(self, rng, loss, dup, base_delay, jitter):
        self.rng = rng
        self.loss = loss
        self.dup = dup
        self.base_delay = base_delay
        self.jitter = jitter
        self.queue = []
        self.counter = 0

    def push(self, now, packet):
        if self.rng.random() < self.loss:
            return
        for _ in range(2 if self.rng.random() < self.dup else 1):
            self.counter += 1
            heapq.heappush(self.queue, (now + self.base_delay + self.rng.random() * self.jitter, self.counter, packet))

    def pop(self, now):
        out = []
        while self.queue and self.queue[0][0] <= now:
            out.append(heapq.heappop(self.queue)[2])
        return out


def deliver(conn, now, packet):
    _, _, seq, ack, ack_bits, body = proto.decode_packet(packet)
    conn.on_packet(now, seq, ack, ack_bits, body)


def simulate(factory, loss, dup, seconds=30.0, messages=600, seed=1, start_seq=1, start_rel=0, burst_every=0,
             base_delay=0.04, jitter=0.03, drain_limit=30.0):
    """The loop of test_reliability.simulate(), with the link delays as parameters."""
    rng = random.Random(seed)
    a, b = factory(token=1), factory(token=1)
    for conn in (a, b):
        conn.next_seq = start_seq
        conn.rel_next = start_rel
        conn.rel_expected = start_rel
    ab = LossyPipe(rng, loss, dup, base_delay, jitter)
    ba = LossyPipe(rng, loss, dup, base_delay, jitter)
    sent = {"a": 0, "b": 0}
    now = 0.0
    step = 1.0 / 120.0
    counter = 0
    tick = 0
    while now < seconds or ((a.rel_pending or b.rel_pending) and now < seconds + drain_limit):
        tick += 1
        for name, conn, pipe in (("a", a, ab), ("b", b, ba)):
            unreliable = []
            if tick % 4 == 0:
                unreliable.append((0x10, 0xFF, b"snap%d" % tick))
            if now < seconds - 8 and sent[name] < messages and rng.random() < 0.3:
                count = 25 if burst_every and tick % burst_every == 0 else 1
                for _ in range(count):
                    counter += 1
                    if conn.queue_reliable(0x20, 0xFF, b"evt-%d" % counter, now):
                        sent[name] += 1
            if unreliable or conn.reliable_due(now) or (conn.ack_pending and tick % 4 == 0):
                for packet in conn.build_packets(now, unreliable, force=conn.ack_pending):
                    pipe.push(now, packet)
        for packet in ab.pop(now):
            deliver(b, now, packet)
        for packet in ba.pop(now):
            deliver(a, now, packet)
        now += step


def expect_error(call) -> None:
    try:
        call()
    except proto.ProtocolError:
        return
    raise AssertionError("reliability.py accepted an input it should reject")


def exchange(sender, receiver, now, later, unreliable=(), force=False):
    for packet in sender.build_packets(now, unreliable, force):
        deliver(receiver, later, packet)


def edge_cases(factory) -> None:
    """Hand-written cases the random scenarios rarely or never reach."""
    # Window limit, oversized and maximum bodies, packing by size and by message count.
    a, b = factory(token=7), factory(token=7)
    for index in range(reliability.RELIABLE_WINDOW + 1):
        a.queue_reliable(0x20, 0xFF, bytes([index & 0xFF]) * (index % 5), 0.0)
    expect_error(lambda: a.queue_reliable(0x20, 0, b"x" * (proto.MAX_MESSAGE_BODY + 1), 0.0))
    exchange(a, b, 0.0, 0.05, [(0x10, 0, b"")] * 40)
    exchange(b, a, 0.05, 0.1, force=True)
    c, d = factory(token=9, max_packet=600), factory(token=9)
    c.queue_reliable(0x21, 2, b"\xab" * proto.MAX_MESSAGE_BODY, 1.0)
    c.queue_reliable(0x21, 2, b"\xcd" * 500, 1.0)
    exchange(c, d, 1.0, 1.02, [(0x11, 3, b"\xef" * 300), (0x12, 4, b"")])
    exchange(d, c, 1.02, 1.04, force=True)
    exchange(c, d, 1.04, 1.06, force=True)  # nothing due: ack-only packet

    # Ack bits, duplicates, packets too old to judge, sequence 0 and bad framing.
    e = factory(token=3)
    for seq in (1, 2, 4, 7):
        e.on_packet(0.0, seq, 0, 0, b"")
    e.on_packet(0.0, 4, 0, 0, b"")
    e.on_packet(0.0, 60, 0, 0, b"")
    e.on_packet(0.0, 20, 0, 0, b"")
    e.on_packet(0.0, 27, 0, 0, b"")
    expect_error(lambda: e.on_packet(0.0, 0, 0, 0, b""))
    expect_error(lambda: e.on_packet(0.0, 61, 0, 0, b"\x10\x00\xff\x00"))
    expect_error(lambda: e.on_packet(0.0, 61, 0, 0, b"\x90\x00\x00\x00\x01"))
    e.on_packet(0.0, 61, 0, 0, bytes.fromhex("10010000") * 96)
    expect_error(lambda: e.on_packet(0.0, 62, 0, 0, bytes.fromhex("10010000") * 97))

    # RTT: clean exchanges, then an ack that arrives after a lost packet (no sample), then bogus
    # acks half the sequence space away: the first declares only the newer half of the history
    # lost (signed distances wrap), the second all of it.
    f, g = factory(token=5), factory(token=5)
    now = 0.0
    for _ in range(10):
        exchange(f, g, now, now + 0.05, [(0x10, 0, b"x")])
        exchange(g, f, now + 0.05, now + 0.1, [(0x10, 0, b"y")])
        now += 0.1
    exchange(f, g, now, now + 0.05, [(0x10, 0, b"x")])
    g.build_packets(now + 0.05, [(0x10, 0, b"lost")])
    exchange(g, f, now + 1.0, now + 1.05, [(0x10, 0, b"late")])
    for index in range(5):
        f.queue_reliable(0x20, 0xFF, b"r%d" % index, now + 1.1)
        f.build_packets(now + 1.1 + index * 0.01, [(0x10, 0, b"z")])
    oldest = next(iter(f.sent))
    f.on_packet(now + 1.2, g.next_seq, (oldest + 32770) % 65536, 0, b"")
    f.on_packet(now + 1.3, reliability.next_packet_seq(g.next_seq), (f.next_seq + 30000) % 65536, 0xFFFFFFFF, b"")

    # Reliable receive: out of order, duplicate, behind the window and beyond it.
    h = factory(token=11)
    seq = 1
    for rel_seq, body in ((2, b"c"), (0, b"a"), (2, b"c"), (1, b"b"), (0, b"a"), (300, b"far"), (3, b"d")):
        h.on_packet(2.0, seq, 0, 0, proto.encode_message(0x20, 1, body, rel_seq))
        seq += 1


SCENARIOS = (
    ("loss-0", dict(loss=0.0, dup=0.0, seed=1)),
    ("loss-10-dup-5", dict(loss=0.1, dup=0.05, seed=2)),
    ("loss-30-dup-10", dict(loss=0.3, dup=0.1, seed=3)),
    ("loss-45-dup-20", dict(loss=0.45, dup=0.2, seed=4)),
    ("burst-window", dict(loss=0.2, dup=0.05, seed=5, burst_every=90, messages=2000)),
    ("sequence-wrap", dict(loss=0.2, dup=0.05, seed=6, start_seq=65400, start_rel=65300)),
    ("reorder-120ms", dict(loss=0.2, dup=0.05, seed=8, jitter=0.12)),
)


def generate(out_path: str) -> dict:
    lines = []
    counts = {}
    for name, params in SCENARIOS + (("edge-cases", None),):
        tracer = Tracer()
        factory = tracer.connection_class()
        if params is None:
            edge_cases(factory)
        else:
            simulate(factory, **params)
        lines.append(f"SCENARIO {name}")
        lines.extend(tracer.lines)
        lines.append("END")
        counts[name] = sum(1 for line in tracer.lines if line.split(" ", 1)[0] in ("QUEUE", "DUE", "BUILD", "RECV"))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="ascii", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    return counts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--relay", default=default_relay(), help="relay checkout (default: %(default)s)")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="generate the trace and replay it in C++")
    run.add_argument("--exe", required=True, help="coopnet_v2_reliability_tests.exe")
    run.add_argument("--out", default=os.path.join(ROOT, "build", "golden", "link_trace.txt"))
    gen = sub.add_parser("generate", help="only write the trace")
    gen.add_argument("out")
    args = parser.parse_args(argv)
    load_relay(args.relay)
    out = args.out
    counts = generate(out)
    total = sum(counts.values())
    print(f"reliability.py trace: {len(counts)} scenarios, {total} calls -> {out}", flush=True)
    for name, count in counts.items():
        print(f"  {name:<16} {count:6d} calls", flush=True)
    if args.command == "generate":
        return 0
    result = subprocess.run([args.exe, "trace", out])
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
