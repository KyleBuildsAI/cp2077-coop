import math
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from coopnet import proto  # noqa: E402
from coopnet.snapshot import (DeltaDecoder, DeltaEncoder, InterestManager, apply_record, make_record,  # noqa: E402
                              quantize, view_hash)

CODEC = proto.EntitySnapshotCodec()


def world_states(tick, alive, rng_seed=0):
    states = {}
    for net_id in alive:
        kind = proto.EntityKind.VEHICLE if net_id % 5 == 0 else proto.EntityKind.CROWD_NPC
        angle = tick * 0.05 + net_id
        radius = 40.0 if kind == proto.EntityKind.VEHICLE else 3.0
        pos = (-1400 + net_id * 3 + radius * math.cos(angle), 200 + radius * math.sin(angle), 20.0)
        vel = (-radius * 0.5 * math.sin(angle), radius * 0.5 * math.cos(angle), 0.0)
        rotation = (0.0, 0.0, math.sin(angle / 2), math.cos(angle / 2)) if kind == proto.EntityKind.VEHICLE \
            else math.degrees(angle) % 360
        moving = net_id % 7 != 0
        states[net_id] = quantize(kind, 1, 1, 1000 + kind, net_id, pos if moving else (-1400 + net_id, 200, 20),
                                  rotation if moving else ((0.0, 0.0, 0.0, 1.0) if kind == proto.EntityKind.VEHICLE
                                                           else 0.0),
                                  vel if moving else (0, 0, 0),
                                  1 if moving else 0, 0, 255)
    return states


class DeltaTests(unittest.TestCase):
    def run_link(self, loss, ack_loss, ticks=300, budget=1000, seed=1, entities=80):
        rng = random.Random(seed)
        encoder, decoder = DeltaEncoder(budget), DeltaDecoder()
        in_flight = []
        alive = set(range(1, entities + 1))
        next_id = entities + 1
        decoded_ticks = 0
        for tick in range(1, ticks + 1):
            if tick % 20 == 0:
                for _ in range(3):
                    alive.discard(rng.choice(sorted(alive)))
                    alive.add(next_id)
                    next_id += 1
            states = world_states(tick, alive)
            body, sent_tick, baseline, view = encoder.encode(tick * 100, states)
            self.assertLessEqual(len(body), budget)
            if rng.random() >= loss:
                in_flight.append((tick + rng.randint(1, 4), body))
            last_tick = tick == ticks
            for arrival in sorted(item for item in in_flight if item[0] <= tick or last_tick):
                in_flight.remove(arrival)
                snapshot = CODEC.decode(arrival[1])
                result = decoder.apply(snapshot)
                if result is not None:
                    decoded_ticks += 1
                    self.assertEqual(view_hash(result), view_hash(encoder.history[snapshot["tick"]]))
                    if rng.random() >= ack_loss:
                        encoder.on_ack(decoder.latest)
        return encoder, decoder, decoded_ticks, alive

    def test_views_match_exactly_under_loss_and_reordering(self):
        for loss, ack_loss in ((0.0, 0.0), (0.2, 0.2), (0.5, 0.5)):
            encoder, decoder, decoded, _ = self.run_link(loss, ack_loss)
            self.assertGreater(decoded, 100)
            self.assertEqual(decoder.stats["inconsistent"], 0)

    def test_converges_to_truth_when_loss_stops(self):
        encoder, decoder, _, alive = self.run_link(0.0, 0.0, ticks=120, budget=1200, entities=40)
        final = world_states(120, alive)
        latest = decoder.history[decoder.latest]
        self.assertEqual(set(latest), set(final))
        self.assertEqual(view_hash(latest), view_hash(final))

    def test_compression_and_no_starvation(self):
        encoder, decoder, _, alive = self.run_link(0.1, 0.1, ticks=200, budget=400, entities=60)
        self.assertGreater(encoder.stats["full_bytes"] / encoder.stats["bytes"], 2.0)
        latest = decoder.history[decoder.latest]
        self.assertGreaterEqual(len(set(latest) & alive), len(alive) - 3)

    def test_make_record_minimal(self):
        old = quantize(proto.EntityKind.CROWD_NPC, 1, 1, 7, 8, (10.0, 20.0, 1.0), 90.0, (1.0, 0.0, 0.0))
        same = quantize(proto.EntityKind.CROWD_NPC, 1, 1, 7, 8, (10.0, 20.0, 1.0), 90.0, (1.0, 0.0, 0.0))
        self.assertIsNone(make_record(5, old, same))
        moved = quantize(proto.EntityKind.CROWD_NPC, 1, 1, 7, 8, (10.1, 20.0, 1.0), 90.0, (1.0, 0.0, 0.0))
        record = make_record(5, old, moved)
        self.assertEqual(record, {"net_id": 5, "pos_delta": (100, 0, 0)})
        self.assertEqual(CODEC.record_size(record), 9)
        far = quantize(proto.EntityKind.CROWD_NPC, 1, 1, 7, 8, (100.0, 20.0, 1.0), 90.0, (1.0, 0.0, 0.0))
        self.assertIn("pos", make_record(5, old, far))
        self.assertEqual(apply_record(old, make_record(5, old, far)), far)
        respawned = quantize(proto.EntityKind.CROWD_NPC, 1, 1, 7, 9, (10.0, 20.0, 1.0), 90.0, (1.0, 0.0, 0.0))
        self.assertIn("spawn", make_record(5, old, respawned))

    def test_decoder_rejects_unknown_baseline_and_inconsistent_delta(self):
        decoder = DeltaDecoder()
        self.assertIsNone(decoder.apply({"tick": 5, "baseline": 3, "sample_time": 0, "records": []}))
        self.assertEqual(decoder.stats["missing_baseline"], 1)
        self.assertIsNone(decoder.apply({"tick": 6, "baseline": 0, "sample_time": 0,
                                         "records": [{"net_id": 9, "pos_delta": (1, 1, 1)}]}))
        self.assertEqual(decoder.stats["inconsistent"], 1)


class InterestTests(unittest.TestCase):
    def test_radius_hysteresis_and_priority(self):
        interest = InterestManager(npc_radius_m=100, vehicle_radius_m=200, hysteresis_m=15)
        entities = {1: (proto.EntityKind.CROWD_NPC, (95.0, 0.0, 0.0), False),
                    2: (proto.EntityKind.CROWD_NPC, (110.0, 0.0, 0.0), False),
                    3: (proto.EntityKind.VEHICLE, (190.0, 0.0, 0.0), False),
                    4: (proto.EntityKind.COMBAT_NPC, (400.0, 0.0, 0.0), True)}
        relevant, weights = interest.update((0.0, 0.0, 0.0), entities)
        self.assertEqual(relevant, {1, 3, 4})
        entities[1] = (proto.EntityKind.CROWD_NPC, (110.0, 0.0, 0.0), False)
        relevant, _ = interest.update((0.0, 0.0, 0.0), entities)
        self.assertIn(1, relevant)       # stays thanks to hysteresis
        self.assertNotIn(2, relevant)    # never entered
        entities[1] = (proto.EntityKind.CROWD_NPC, (120.0, 0.0, 0.0), False)
        relevant, _ = interest.update((0.0, 0.0, 0.0), entities)
        self.assertNotIn(1, relevant)
        near = InterestManager().update((0, 0, 0), {1: (proto.EntityKind.CROWD_NPC, (5.0, 0, 0), False),
                                                    2: (proto.EntityKind.CROWD_NPC, (90.0, 0, 0), False)})[1]
        self.assertGreater(near[1], near[2])

    def test_cap(self):
        interest = InterestManager(max_entities=10)
        entities = {n: (proto.EntityKind.CROWD_NPC, (float(n), 0.0, 0.0), False) for n in range(1, 50)}
        relevant, _ = interest.update((0.0, 0.0, 0.0), entities)
        self.assertEqual(relevant, set(range(1, 11)))


if __name__ == "__main__":
    unittest.main()
