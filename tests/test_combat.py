"""A combat packet (forwardY=9999) must go to the combat handler, not move the avatar or corrupt state."""
import sys

import test_two_players as harness

FRAME_DT = harness.FRAME_DT


def main():
    joiner = harness.make_instance("joiner", (0.0, 0.0))
    g = joiner.globals()
    joiner.execute(r"""
        stancesApplied = 0
        function player:CP2077Coop_ApplyRemoteStance(c) stancesApplied = stancesApplied + 1 end
    """)
    t = 0.0
    seq = 0

    def deliver(x, y, z, fx, fy):
        nonlocal seq
        seq += 1
        g.net.has = True
        g.net.seq = seq
        g.net.x, g.net.y, g.net.z, g.net.fx, g.net.fy = x, y, z, fx, fy

    # host standing at (10, 0) sending flags=257 (host + crouch) for 3 s
    while t < 3.0:
        g.simTime = t
        deliver(10.0, 0.0, 0.0, 0.0, 1.0 * (1 + 257))
        g.tickSpawn()
        g.events["onUpdate"](FRAME_DT)
        joiner.eval("stepNpc")(FRAME_DT)
        t += FRAME_DT

    npc = g.npc
    before = (npc.x, npc.y)
    # combat hit on an NPC 80 m away, damage 42
    g.simTime = t
    deliver(90.0, 0.0, 0.0, 42.0, 9999.0)
    g.events["onUpdate"](FRAME_DT)
    joiner.eval("stepNpc")(FRAME_DT)
    t += FRAME_DT
    # movement resumes
    for _ in range(30):
        g.simTime = t
        deliver(10.0, 0.0, 0.0, 0.0, 1.0 * (1 + 257))
        g.events["onUpdate"](FRAME_DT)
        joiner.eval("stepNpc")(FRAME_DT)
        t += FRAME_DT

    after = (npc.x, npc.y)
    logs = harness.logs(joiner)
    combat_logs = [l for l in logs if "COMBAT" in l]
    stats_line = joiner.eval("nil")
    moved = abs(after[0] - before[0]) + abs(after[1] - before[1])
    print("combat logs:", combat_logs)
    print(f"avatar before {before} after {after} moved {moved:.3f} m; teleports {g.stats.teleports}")
    checks = {
        "combat handler ran (scan error expected in mock, no crash)": any("COMBAT" in l for l in combat_logs),
        "avatar did not jump to the hit position": moved < 0.5 and after[0] < 50,
        "crouch state not reset by the hit packet": g.stancesApplied == 1,
    }
    for name, passed in checks.items():
        print(f"{'PASS' if passed else 'FAIL'}  {name}")
    return all(checks.values())


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
