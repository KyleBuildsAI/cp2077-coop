"""A combat packet (forwardY=9999) must damage the NPC nearest the hit, not move the avatar or corrupt state."""
import os
import sys

import test_two_players as harness

FRAME_DT = harness.FRAME_DT

# Targeting, god mode and stat pool systems for Combat.findNearestNPCAt / applyRemoteHit.
# The scene sits around the hit at (90, 0, 0); only "target" is a valid nearest match.
COMBAT_MOCK = r"""
stancesApplied = 0
function player:CP2077Coop_ApplyRemoteStance(c) stancesApplied = stancesApplied + 1 end

gameGodModeType = { Invulnerable = "Invulnerable" }
tsqQueries = {}
statCalls = {}
ragdollEvents = 0

local function makeEntity(id, x, y, z, isNpc, isDead)
    local entity = { id = id, x = x, y = y, z = z }
    function entity:IsNPC() return isNpc end
    function entity:IsDead() return isDead end
    function entity:GetEntityID() return id end
    function entity:GetWorldPosition() return { x = self.x, y = self.y, z = self.z, w = 1 } end
    function entity:QueueEvent(event) ragdollEvents = ragdollEvents + 1 end
    return entity
end

sceneEntities = {
    makeEntity("dead", 90.2, 0, 0, true, true),     -- closest, but dead
    makeEntity("car", 90.1, 0, 0, false, false),    -- closer, but not an NPC
    makeEntity("target", 91.0, 0, 0, true, false),  -- valid, 1.0 m: the expected match
    makeEntity("second", 92.5, 0, 0, true, false),  -- valid, 2.5 m, listed later on purpose
    makeEntity("far", 95.0, 0, 0, true, false),     -- outside MATCH_RADIUS (4 m)
}

local function partOf(entity)
    return { GetComponent = function() return { GetEntity = function() return entity end } end }
end

Game["TSQ_NPC;"] = function()
    local query = { maxDistance = 0 }
    tsqQueries[#tsqQueries + 1] = query
    return query
end
Game.GetTargetingSystem = function()
    return { GetTargetParts = function(_, source, query)
        local parts = {}
        for _, entity in ipairs(sceneEntities) do parts[#parts + 1] = partOf(entity) end
        -- the remote avatar is an NPC too; the handler must skip it by handle
        if npc ~= nil then
            npc.IsNPC = function() return true end
            npc.IsDead = function() return false end
            npc.GetEntityID = function() return "avatar" end
            parts[#parts + 1] = partOf(npc)
        end
        return true, parts  -- CET: bool result, then the out array
    end }
end
Game.GetGodModeSystem = function()
    return { HasGodMode = function(_, id, mode) return false end }
end
Game.GetStatPoolsSystem = function()
    return { RequestChangingStatPoolValue = function(_, id, pool, delta, instigator)
        statCalls[#statCalls + 1] = { id = id, pool = pool, delta = delta, byPlayer = (instigator == player) }
    end }
end
Game.CreateForceRagdollEvent = function(reason) return { reason = reason } end
"""


def event_lines():
    """coop_events.log in the working folder (Diag.EVENTS_FILE is relative, like in CET)."""
    if not os.path.exists("coop_events.log"):
        return []
    with open("coop_events.log", encoding="utf-8", errors="replace") as handle:
        return handle.read().splitlines()


def main():
    joiner = harness.make_instance("joiner", (0.0, 0.0))
    g = joiner.globals()
    joiner.execute(COMBAT_MOCK)
    t = 0.0
    seq = 0

    def deliver(x, y, z, fx, fy):
        nonlocal seq
        seq += 1
        g.net.has = True
        g.net.seq = seq
        g.net.x, g.net.y, g.net.z, g.net.fx, g.net.fy = x, y, z, fx, fy

    def frame(packet):
        nonlocal t
        g.simTime = t
        deliver(*packet)
        g.tickSpawn()
        g.events["onUpdate"](FRAME_DT)
        joiner.eval("stepNpc")(FRAME_DT)
        t += FRAME_DT

    move = (10.0, 0.0, 0.0, 0.0, 1.0 * (1 + 257))  # host at (10, 0), flags=257 (host + crouch)

    # 6 s of movement (the joiner's avatar spawns after the join teleport, which waits 4 s)
    while t < 6.0:
        frame(move)

    npc = g.npc
    acquired = any("remote entity acquired" in l for l in harness.logs(joiner))
    before = (npc.x, npc.y)
    # hit 1: NPC 80 m from the avatar, damage 42
    frame((90.0, 0.0, 0.0, 42.0, 9999.0))
    # hit 2: at the avatar itself; the avatar is the only candidate and must be skipped
    frame((npc.x, npc.y, npc.z, 30.0, 9999.0))
    for _ in range(30):
        frame(move)

    after = (npc.x, npc.y)
    combat_logs = [l for l in harness.logs(joiner) if "COMBAT" in l]
    calls = [g.statCalls[i] for i in range(1, len(g.statCalls) + 1)]
    scan_radii = [g.tsqQueries[i].maxDistance for i in range(1, len(g.tsqQueries) + 1)]
    ragdolls = g.ragdollEvents

    # an SMG burst: 30 hits, each applied; per-hit lines go to print() only, so
    # coop_events.log (opened and closed per line) is not written per hit
    events_before = event_lines()
    printed_before = sum("COMBAT HIT applied" in l for l in harness.logs(joiner))
    for _ in range(30):
        frame((90.0, 0.0, 0.0, 42.0, 9999.0))
        frame(move)
    events_after = event_lines()
    printed_after = sum("COMBAT HIT applied" in l for l in harness.logs(joiner))
    burst_in_file = [l for l in events_after if "COMBAT HIT" in l]
    print(f"  30-hit burst: coop_events.log {len(events_before)} -> {len(events_after)} lines, "
          f"COMBAT HIT lines in it {len(burst_in_file)}; printed 'HIT applied' {printed_before} -> {printed_after}")

    # a broken targeting API errors on every hit: the file gets the first error only
    joiner.execute('Game["TSQ_NPC;"] = nil')
    for _ in range(5):
        frame((90.0, 0.0, 0.0, 42.0, 9999.0))
    scan_errors_file = sum("COMBAT scan error" in l for l in event_lines())
    scan_errors_printed = sum("COMBAT scan error" in l for l in harness.logs(joiner))
    print(f"  5 hits with no targeting query: scan errors in coop_events.log {scan_errors_file}, printed {scan_errors_printed}")
    moved = abs(after[0] - before[0]) + abs(after[1] - before[1])
    print("combat logs:", combat_logs)
    print("stat pool calls:", [(c.id, c.pool, c.delta) for c in calls])
    print(f"avatar before {before} after {after} moved {moved:.3f} m; teleports {g.stats.teleports}")
    checks = {
        "remote avatar handle acquired before the hits": acquired,
        "no scan or damage error logged": not any("error" in l for l in combat_logs),
        "scan uses SCAN_RADIUS 220 m": scan_radii == [220.0, 220.0],
        "hit 1 matched the nearest live NPC (1.00 m)": any(
            "COMBAT HIT applied dmg=42.00 match=1.00m" in l for l in combat_logs),
        "exactly one damage request: target, Health, -42, instigator = player": len(calls) == 1
            and calls[0].id == "target" and calls[0].pool == "Health"
            and calls[0].delta == -42.0 and calls[0].byPlayer,
        "hit 2 skipped the remote avatar (no NPC match)": any(
            "COMBAT HIT no NPC match" in l and "dmg=30.00" in l for l in combat_logs),
        "hit reaction queued on the target": ragdolls == 1,
        "avatar did not jump to the hit position": moved < 0.5 and after[0] < 50,
        "crouch state not reset by the hit packet": g.stancesApplied == 1,
        "30-hit burst printed per hit, coop_events.log not written per hit": printed_after - printed_before == 30
            and not burst_in_file and len(events_after) - len(events_before) <= 1,
        "repeated scan error: first one in coop_events.log, every one printed": scan_errors_file == 1
            and scan_errors_printed == 5,
    }
    for name, passed in checks.items():
        print(f"{'PASS' if passed else 'FAIL'}  {name}")
    return all(checks.values())


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
