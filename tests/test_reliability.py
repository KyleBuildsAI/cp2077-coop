import heapq
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from coopnet import proto  # noqa: E402
from coopnet.reliability import RELIABLE_WINDOW, Connection, seq_diff  # noqa: E402


class LossyPipe:
    """In-memory link: loss, duplication and reordering via random delay."""

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


def deliver(conn, now, packet, sink):
    _, _, seq, ack, ack_bits, body = proto.decode_packet(packet)
    for mtype, peer, reliable, payload in conn.on_packet(now, seq, ack, ack_bits, body):
        sink.append((mtype, reliable, payload))


DRAIN_LIMIT_S = 30.0


def simulate(loss, dup, seconds=30.0, messages=600, seed=1, start_seq=1, start_rel=0, burst_every=0,
             factory=Connection):
    """Two connections over lossy pipes. New reliable messages stop 8 s before ``seconds``.

    The run continues past ``seconds`` (at most DRAIN_LIMIT_S) while either side still has
    unacknowledged reliable messages: at 45 % loss each way a message queued just before the
    cutoff can need more than 8 s, because the resend interval backs off to 8 x RTO.
    """
    rng = random.Random(seed)
    a, b = factory(token=1), factory(token=1)
    for conn in (a, b):
        conn.next_seq = start_seq
        conn.rel_next = start_rel
        conn.rel_expected = start_rel
    ab = LossyPipe(rng, loss, dup, 0.04, 0.03)
    ba = LossyPipe(rng, loss, dup, 0.04, 0.03)
    sent = {"a": [], "b": []}
    got = {"a": [], "b": []}
    now = 0.0
    step = 1.0 / 120.0
    counter = 0
    tick = 0
    while now < seconds or ((a.rel_pending or b.rel_pending) and now < seconds + DRAIN_LIMIT_S):
        tick += 1
        for name, conn, pipe in (("a", a, ab), ("b", b, ba)):
            unreliable = []
            if tick % 4 == 0:
                unreliable.append((0x10, 0xFF, b"snap%d" % tick))
            if now < seconds - 8 and len(sent[name]) < messages and rng.random() < 0.3:
                count = 25 if burst_every and tick % burst_every == 0 else 1
                for _ in range(count):
                    counter += 1
                    body = b"evt-%d" % counter
                    if conn.queue_reliable(0x20, 0xFF, body, now):
                        sent[name].append(body)
            if unreliable or conn.reliable_due(now) or (conn.ack_pending and tick % 4 == 0):
                for packet in conn.build_packets(now, unreliable, force=conn.ack_pending):
                    pipe.push(now, packet)
        for packet in ab.pop(now):
            deliver(b, now, packet, got["b"])
        for packet in ba.pop(now):
            deliver(a, now, packet, got["a"])
        now += step
    reliable_a = [payload for _, reliable, payload in got["a"] if reliable]
    reliable_b = [payload for _, reliable, payload in got["b"] if reliable]
    return a, b, sent, reliable_a, reliable_b, got


class ReliabilityTests(unittest.TestCase):
    def test_exactly_once_in_order_under_heavy_loss(self):
        for seed, loss, dup in ((1, 0.0, 0.0), (2, 0.1, 0.05), (3, 0.3, 0.1), (4, 0.45, 0.2)):
            a, b, sent, reliable_a, reliable_b, _ = simulate(loss, dup, seed=seed)
            self.assertGreater(len(sent["a"]), 300)
            self.assertEqual(reliable_b, sent["a"], f"a->b loss={loss}")
            self.assertEqual(reliable_a, sent["b"], f"b->a loss={loss}")
            self.assertFalse(a.rel_pending)
            self.assertFalse(b.rel_pending)

    def test_bursts_fill_window_without_loss_of_order(self):
        a, b, sent, reliable_a, reliable_b, _ = simulate(0.2, 0.05, seed=5, burst_every=90, messages=2000)
        self.assertEqual(reliable_b, sent["a"])
        self.assertEqual(reliable_a, sent["b"])

    def test_sequence_wraparound(self):
        a, b, sent, reliable_a, reliable_b, _ = simulate(0.2, 0.05, seed=6, start_seq=65400, start_rel=65300)
        self.assertEqual(reliable_b, sent["a"])
        self.assertEqual(reliable_a, sent["b"])
        self.assertLess(a.next_seq, 65400)  # wrapped
        self.assertNotEqual(a.next_seq, 0)

    def test_unreliable_are_never_retransmitted(self):
        _, b, _, _, _, got = simulate(0.3, 0.0, seed=7)
        snaps = [payload for mtype, reliable, payload in got["b"] if not reliable]
        self.assertEqual(len(snaps), len(set(snaps)))  # packet-level dedupe removes duplicates

    def test_window_limit(self):
        conn = Connection()
        for index in range(RELIABLE_WINDOW):
            self.assertTrue(conn.queue_reliable(0x20, 0, b"x", 0.0))
        self.assertFalse(conn.queue_reliable(0x20, 0, b"x", 0.0))

    def test_rtt_estimate(self):
        a, b = Connection(1), Connection(1)
        now = 0.0
        for _ in range(100):
            for packet in a.build_packets(now, [(0x10, 0, b"x")]):
                _, _, seq, ack, bits, body = proto.decode_packet(packet)
                b.on_packet(now + 0.05, seq, ack, bits, body)
            for packet in b.build_packets(now + 0.05, [(0x10, 0, b"y")]):
                _, _, seq, ack, bits, body = proto.decode_packet(packet)
                a.on_packet(now + 0.1, seq, ack, bits, body)
            now += 0.1
        self.assertAlmostEqual(a.srtt, 0.1, delta=0.005)
        self.assertGreaterEqual(a.rto, 0.1)

    def test_packets_hold_at_most_96_messages(self):
        conn = Connection(token=1)
        for _ in range(200):
            self.assertTrue(conn.queue_reliable(0x20, 0, b"", 0.0))
        packets = conn.build_packets(0.0, [(0x10, 0, b"") for _ in range(50)])
        counts = [len(proto.decode_messages(proto.decode_packet(packet)[5])) for packet in packets]
        self.assertEqual(sum(counts), 250)
        self.assertLessEqual(max(counts), proto.MAX_MESSAGES_PER_PACKET)
        self.assertEqual(len(packets), 3)

    def test_rtt_ignores_acks_after_a_gap(self):
        a, b = Connection(1), Connection(1)

        def a_to_b(now):
            for packet in a.build_packets(now, [(0x10, 0, b"x")]):
                _, _, seq, ack, bits, body = proto.decode_packet(packet)
                b.on_packet(now + 0.05, seq, ack, bits, body)

        def b_packets(now):
            return [proto.decode_packet(packet) for packet in b.build_packets(now, [(0x10, 0, b"y")])]

        now = 0.0
        for _ in range(10):  # clean exchanges at 100 ms RTT
            a_to_b(now)
            for _, _, seq, ack, bits, body in b_packets(now + 0.05):
                a.on_packet(now + 0.1, seq, ack, bits, body)
            now += 0.1
        self.assertAlmostEqual(a.srtt, 0.1, delta=0.001)
        samples, skipped = a.stats.rtt_samples, a.stats.rtt_skipped
        self.assertEqual(skipped, 1)  # the very first packet from b: nothing to compare it with
        a_to_b(now)
        lost = b_packets(now + 0.05)  # the packet acking a's newest one is lost
        self.assertEqual(len(lost), 1)
        late = b_packets(now + 1.0)   # the next one acks it again, 1 s later
        for _, _, seq, ack, bits, body in late:
            a.on_packet(now + 1.05, seq, ack, bits, body)
        self.assertEqual(a.stats.rtt_samples, samples)
        self.assertEqual(a.stats.rtt_skipped, skipped + 1)
        self.assertAlmostEqual(a.srtt, 0.1, delta=0.001)  # a 1.05 s sample would have pulled it to 0.22 s
        self.assertFalse(a.sent)

    def test_no_inflated_rtt_samples_under_loss(self):
        # In simulate() a packet takes at most 70 ms each way plus one 8.3 ms tick to be popped,
        # and the peer answers within 4 ticks, so no true round trip exceeds about 190 ms. Before
        # the rule, acks that got through after a gap produced samples up to 333 ms at 45 % loss.
        class Recording(Connection):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.rtt_log = []

            def _update_rtt(self, sample):
                self.rtt_log.append(sample)
                super()._update_rtt(sample)

        for seed, loss, dup in ((1, 0.0, 0.0), (2, 0.1, 0.05), (3, 0.3, 0.1), (4, 0.45, 0.2)):
            a, b, *_ = simulate(loss, dup, seed=seed, factory=Recording)
            for conn in (a, b):
                self.assertGreater(len(conn.rtt_log), 100, f"loss={loss}")
                self.assertLessEqual(max(conn.rtt_log), 0.19, f"loss={loss}")
                self.assertEqual(len(conn.rtt_log), conn.stats.rtt_samples)
                if loss:
                    self.assertGreater(conn.stats.rtt_skipped, len(conn.rtt_log) // 10)

    def test_ack_bits(self):
        conn = Connection()
        for seq in (1, 2, 4, 7):
            conn.on_packet(0.0, seq, 0, 0, b"")
        self.assertEqual(conn.remote_seq, 7)
        # bit i = seq 6 - i: bit0 seq6 no, bit1 seq5 no, bit2 seq4 yes, bit3 seq3 no, bit4 seq2 yes, bit5 seq1 yes
        self.assertEqual(conn.recv_bits & 0b111111, 0b110100)
        self.assertEqual(conn.on_packet(0.0, 4, 0, 0, b""), [])
        self.assertEqual(conn.stats.duplicates, 1)

    def test_seq_zero_reserved_and_bad_framing(self):
        conn = Connection()
        with self.assertRaises(proto.ProtocolError):
            conn.on_packet(0.0, 0, 0, 0, b"")
        with self.assertRaises(proto.ProtocolError):
            conn.on_packet(0.0, 1, 0, 0, b"\x10\x00\xff\x00")
        self.assertEqual(conn.remote_seq, 0)

    def test_seq_diff(self):
        self.assertEqual(seq_diff(1, 65535), 2)
        self.assertEqual(seq_diff(65535, 1), -2)
        self.assertEqual(seq_diff(100, 90), 10)


if __name__ == "__main__":
    unittest.main()
