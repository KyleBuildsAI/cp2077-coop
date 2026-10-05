import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client_v2 import SeqSpan  # noqa: E402


class SeqSpanTests(unittest.TestCase):
    def test_empty_and_single(self):
        span = SeqSpan()
        self.assertEqual(span.span, 0)
        span.add(7)
        self.assertEqual(span.span, 1)

    def test_losses_inside_the_span(self):
        span = SeqSpan()
        received = [seq for seq in range(1, 1001) if seq % 10 != 0]
        for seq in received:
            span.add(seq)
        self.assertEqual(span.span, 1000 - 1)  # 1..999 (1000 itself was lost)
        self.assertEqual(span.span - len(received), 99)

    def test_reordered_older_sequence_extends_the_start(self):
        span = SeqSpan()
        for seq in (5, 6, 8, 4, 7):
            span.add(seq)
        self.assertEqual(span.span, 5)

    def test_more_than_half_the_sequence_space(self):
        """A 30-minute run at 30 Hz sends 54,000 snapshots: past 32,768 a plain serial comparison
        against the first sequence flips and the span went negative (loss -152 %)."""
        span = SeqSpan()
        for seq in range(1, 54001):
            span.add(seq)
        self.assertEqual(span.span, 54000)

    def test_across_the_16_bit_wrap(self):
        span = SeqSpan()
        sequence = 65000
        for _ in range(140000):
            span.add(sequence)
            sequence = (sequence + 1) & 0xFFFF
        self.assertEqual(span.span, 140000)


if __name__ == "__main__":
    unittest.main()
