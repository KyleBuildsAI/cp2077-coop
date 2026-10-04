"""Token buckets used by the relay for per-connection and per-IP limits."""
from __future__ import annotations


class TokenBucket:
    def __init__(self, rate: float, burst: float, now: float):
        self.rate = rate
        self.burst = burst
        self.tokens = burst
        self.stamp = now

    def take(self, now: float, amount: float = 1.0) -> bool:
        elapsed = max(0.0, now - self.stamp)
        self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
        self.stamp = now
        if self.tokens >= amount:
            self.tokens -= amount
            return True
        return False


class Limits:
    """Default budgets. A 30 Hz player + 10 Hz entity host needs ~45 pkt/s and ~15 KB/s."""

    PACKETS_PER_S = 120.0
    PACKETS_BURST = 240.0
    BYTES_PER_S = 64000.0
    BYTES_BURST = 128000.0
    RELIABLE_PER_S = 60.0
    RELIABLE_BURST = 120.0
    CHAT_PER_S = 2.0
    CHAT_BURST = 5.0
    TELEPORT_PER_S = 1.0
    TELEPORT_BURST = 3.0
    HANDSHAKES_PER_IP_PER_S = 4.0
    HANDSHAKES_PER_IP_BURST = 8.0
    VIOLATIONS_BEFORE_KICK = 200
    VIOLATION_WINDOW_S = 10.0
