"""Network impairment simulator: one-way latency, jitter, random loss, duplication.

Used on the relay's outgoing side (like coop_relay.py) and on each test client's
uplink and downlink, so one PC can reproduce Russia <-> Warsaw <-> Los Angeles.
Jitter is uniform in [0, jitter_ms], which also reorders packets naturally.
"""
from __future__ import annotations

import heapq
import random


class LinkSim:
    def __init__(self, latency_ms: float = 0.0, jitter_ms: float = 0.0, loss_pct: float = 0.0,
                 dup_pct: float = 0.0, seed: int | None = None):
        self.latency_ms = latency_ms
        self.jitter_ms = jitter_ms
        self.loss_pct = loss_pct
        self.dup_pct = dup_pct
        self.rng = random.Random(seed)
        self.queue = []
        self.counter = 0
        self.submitted = 0
        self.dropped = 0
        self.duplicated = 0

    @property
    def active(self) -> bool:
        return any((self.latency_ms, self.jitter_ms, self.loss_pct, self.dup_pct))

    def submit(self, now: float, data: bytes, address) -> None:
        self.submitted += 1
        if self.rng.random() * 100.0 < self.loss_pct:
            self.dropped += 1
            return
        copies = 1
        if self.rng.random() * 100.0 < self.dup_pct:
            copies = 2
            self.duplicated += 1
        for _ in range(copies):
            delay = (self.latency_ms + self.rng.random() * self.jitter_ms) / 1000.0
            self.counter += 1
            heapq.heappush(self.queue, (now + delay, self.counter, data, address))

    def pop_due(self, now: float) -> list:
        due = []
        while self.queue and self.queue[0][0] <= now:
            _, _, data, address = heapq.heappop(self.queue)
            due.append((data, address))
        return due

    def next_due(self):
        return self.queue[0][0] if self.queue else None

    def describe(self) -> str:
        return (f"+{self.latency_ms:.0f}ms jitter {self.jitter_ms:.0f}ms "
                f"loss {self.loss_pct:.1f}% dup {self.dup_pct:.1f}%")

    def as_dict(self) -> dict:
        return {"latency_ms": self.latency_ms, "jitter_ms": self.jitter_ms, "loss_pct": self.loss_pct,
                "dup_pct": self.dup_pct, "submitted": self.submitted, "dropped": self.dropped,
                "duplicated": self.duplicated}
