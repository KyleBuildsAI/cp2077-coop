"""Mod comparison over the state channel: host 30 mods, joiner 28, 25 shared."""
import os
import random
import re
import sys

import test_two_players as harness

FRAME_DT = harness.FRAME_DT
shared = [f"archive/shared_mod_{i}" for i in range(25)]
host_only = ["cet/HostOnlyA", "redscript/HostOnlyB", "red4ext/HostOnlyC", "archive/HostOnlyD", "tweak/HostOnlyE"]
joiner_only = ["cet/JoinerOnlyA", "archive/JoinerOnlyB", "redmod/JoinerOnlyC"]


def write_list(names):
    with open("modlist.txt", "w", encoding="utf-8") as handle:
        handle.write("".join(n + "\n" for n in names))


def main():
    write_list(shared + host_only)
    host = harness.make_instance("host", (100.0, 50.0))
    write_list(shared + joiner_only)
    joiner = harness.make_instance("joiner", (-900.0, 400.0))
    os.remove("modlist.txt")

    instances = {"host": host, "joiner": joiner}
    peers = {"host": "joiner", "joiner": "host"}
    in_flight, seq, rng = [], {"host": 0, "joiner": 0}, random.Random(4)
    t = 0.0
    while t < 40.0:
        for name, lua in instances.items():
            g = lua.globals()
            g.simTime = t
            due = sorted(p for p in in_flight if p[1] == name and p[0] <= t)
            in_flight[:] = [p for p in in_flight if not (p[1] == name and p[0] <= t)]
            for _, _, pseq, packet in due:
                g.net.has = True
                g.net.seq = pseq
                g.net.x, g.net.y, g.net.z, g.net.fx, g.net.fy = packet
            g.tickSpawn()
            g.events["onUpdate"](FRAME_DT)
            lua.eval("stepNpc")(FRAME_DT)
            if t > 39.9:
                g.events["onDraw"]()
            outbox = g.outbox
            for index in range(1, len(outbox) + 1):
                p = outbox[index]
                seq[name] += 1
                if rng.random() < 0.02:  # 2% loss
                    continue
                delay = (harness.LEG_MS[name] + harness.LEG_MS[peers[name]] + rng.random() * harness.JITTER_MS) / 1000.0
                in_flight.append((t + delay, peers[name], seq[name], (p[1], p[2], p[3], p[4], p[5])))
            lua.execute("outbox = {}")
        t += FRAME_DT

    results = {}
    for name, lua in instances.items():
        logs = harness.logs(lua)
        event = next((l for l in logs if "mods compared" in l), "")
        only = [l.split("only you: ", 1)[1] for l in logs if "MOD only you:" in l]
        stats = harness.last_stats(lua)
        print(f"{name:6} {event.split('EVENT ', 1)[-1]}")
        print(f"       only-you names: {only}")
        print(f"       stats: mods_you={harness.stat(stats, 'mods_you')} mods_partner={harness.stat(stats, 'mods_partner')} mods_shared={harness.stat(stats, 'mods_shared')}")
        results[name] = (event, sorted(only))

    checks = {
        "host sees 25 shared, 5 only-you, 3 only-partner": "you=30 partner=28 shared=25 only_you=5 only_partner=3" in results["host"][0],
        "joiner sees 25 shared, 3 only-you, 5 only-partner": "you=28 partner=30 shared=25 only_you=3 only_partner=5" in results["joiner"][0],
        "host names its own extra mods": results["host"][1] == sorted(host_only),
        "joiner names its own extra mods": results["joiner"][1] == sorted(joiner_only),
    }
    for name, passed in checks.items():
        print(f"{'PASS' if passed else 'FAIL'}  {name}")
    return all(checks.values())


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
