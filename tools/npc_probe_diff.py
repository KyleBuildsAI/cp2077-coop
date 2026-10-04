"""Compare CP2077Coop_NpcProbe dumps taken on the host and the joiner.

Both players load the same save, stand at the same spot and run the probe
(npc_probe.reds). This tool answers the questions the NPC sync design depends on:

  * how many NPCs have static EntityIDs on each side, and how many of those
    exist on BOTH sides (mirror candidates) - the higher, the less needs proxies,
  * whether mirrored NPCs share record and appearance (appearance mismatches are
    fixed with ScheduleAppearanceChange; record mismatches would be a red flag),
  * how far apart mirrored NPCs stand (they run their own schedules until bound),
  * that no dynamic EntityID is shared by accident (they are per-session counters).

Usage:
    python npc_probe_diff.py host_log.txt joiner_log.txt
Lines not starting with PROBE are ignored, so whole CET logs can be passed in.
Exit code 0 when the dumps parse, 2 when either side has no PROBE lines.
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass

DYNAMIC_ID_UPPER_BOUND = 0xFFFFFF


@dataclass(frozen=True)
class ProbeNpc:
    kind: str
    entity_hash: int
    is_static: bool
    crowd: bool
    record_hash: int
    record_length: int
    appearance: int
    position: tuple
    dead: bool


def parse_probe(lines) -> list:
    npcs = []
    for raw in lines:
        line = raw.strip()
        start = line.find("PROBE,")
        if start < 0:
            continue
        fields = line[start:].split(",")
        if len(fields) != 12:
            continue
        try:
            npcs.append(ProbeNpc(
                kind=fields[1],
                entity_hash=int(fields[2]),
                is_static=fields[3] == "1",
                crowd=fields[4] == "1",
                record_hash=int(fields[5]),
                record_length=int(fields[6]),
                appearance=int(fields[7]),
                position=(int(fields[8]) / 100.0, int(fields[9]) / 100.0, int(fields[10]) / 100.0),
                dead=fields[11] == "1",
            ))
        except ValueError:
            continue
    return npcs


def compare(host: list, joiner: list) -> dict:
    host_by_id = {n.entity_hash: n for n in host}
    joiner_by_id = {n.entity_hash: n for n in joiner}
    host_static = {h for h, n in host_by_id.items() if n.is_static}
    joiner_static = {h for h, n in joiner_by_id.items() if n.is_static}
    shared_static = host_static & joiner_static
    shared_dynamic = {h for h in host_by_id.keys() & joiner_by_id.keys() if h not in shared_static}

    record_mismatch = [h for h in shared_static
                       if (host_by_id[h].record_hash, host_by_id[h].record_length)
                       != (joiner_by_id[h].record_hash, joiner_by_id[h].record_length)]
    appearance_mismatch = [h for h in shared_static if host_by_id[h].appearance != joiner_by_id[h].appearance]
    distances = sorted(math.dist(host_by_id[h].position, joiner_by_id[h].position) for h in shared_static)

    def percentile(values, fraction):
        if not values:
            return 0.0
        index = min(len(values) - 1, int(round(fraction * (len(values) - 1))))
        return values[index]

    host_proxies = [n for n in host if n.entity_hash not in shared_static]
    return {
        "host_total": len(host),
        "joiner_total": len(joiner),
        "host_static": len(host_static),
        "joiner_static": len(joiner_static),
        "shared_static": len(shared_static),
        "static_match_pct": 100.0 * len(shared_static) / len(host_static) if host_static else 0.0,
        "host_only_static": len(host_static - joiner_static),
        "joiner_only_static": len(joiner_static - host_static),
        "shared_dynamic": len(shared_dynamic),
        "record_mismatch": len(record_mismatch),
        "appearance_mismatch": len(appearance_mismatch),
        "mirror_distance_p50_m": percentile(distances, 0.5),
        "mirror_distance_p95_m": percentile(distances, 0.95),
        "host_needs_proxy": len(host_proxies),
        "host_crowd": sum(1 for n in host if n.crowd),
        "ids_flagged_static_but_small": sum(1 for n in host + joiner if n.is_static and n.entity_hash <= DYNAMIC_ID_UPPER_BOUND),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("host")
    parser.add_argument("joiner")
    args = parser.parse_args(argv)
    with open(args.host, encoding="utf-8", errors="replace") as handle:
        host = parse_probe(handle)
    with open(args.joiner, encoding="utf-8", errors="replace") as handle:
        joiner = parse_probe(handle)
    if not host or not joiner:
        print("no PROBE lines found in one of the files")
        return 2
    result = compare(host, joiner)
    width = max(len(key) for key in result)
    for key, value in result.items():
        print(f"{key:<{width}}  {value:.2f}" if isinstance(value, float) else f"{key:<{width}}  {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
