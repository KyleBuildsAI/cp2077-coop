"""Avatar control: how the remote avatar walks, turns and settles.

A1  straight run / sprint: the endpoint moving forward is not "drift", so no
    cancel/resend storm (each resend makes the AI stop and start again)
A2  steady run: the catch-up gear does not flap Run <-> Sprint
A3  a real turn still re-steers the avatar quickly
A4  after backpedalling / strafing the avatar turns back to the remote's facing
A5  hard corrections (far error, dash) keep the remote's facing: the teleport
    gets the remote forward vector, not rotation 0.0 (world north)
A6  settle: spot just out of reach -> a few tries, then it stops and turns
A7  settle: spot far out of reach -> exactly one teleport, no retry loop
A8  settle: reachable spot -> walks all the way (the idle rotate waits for
    it) without teleport, then faces the remote
A9  remote.reds: teleport rotation comes from the forward vector
A10 long sprint (12 s at 7 m/s on the movement sim's slow 0.15 rad/s curve): at
    most 2 hard corrections and 20 AIMoveTo (the snap-and-resend storm that a
    forward re-steer brings back: 5 and 48 at 4a51c9d). The measured avatar lag
    staying under 4 m is tracked as KNOWN until the bench gives the NPC's real
    Sprint speed (with the mock's 7 m/s NPC the lag ratchets to ~4.6 m)

The mock AI keeps a yaw: AIMoveTo turns the NPC along its path, AIRotateTo
turns it to the target point, a teleport sets its rotation argument.

Usage: python test_avatar.py path/to/init.lua
"""
import math
import os
import re
import sys

import coop_sim30 as sim
import test_live_bugs as live

YAW_MOCK = r"""
npcYaw = 0.0
teleportLog = {}
moveLog = {}
rotateLog = {}
wallX = nil
noClimb = false
function headingOf(fx, fy) return math.deg(math.atan2(-fx, fy)) end

function player:CP2077Coop_MoveRemoteTest(x, y, z, fx, fy)
    stats.teleports = stats.teleports + 1
    -- the old script took only x, y, z and always sent rotation 0.0
    local yaw = 0.0
    if fx ~= nil and fy ~= nil then
        if math.sqrt(fx * fx + fy * fy) > 0.01 then yaw = headingOf(fx, fy) else yaw = nil end
    end
    teleportLog[#teleportLog + 1] = { t = simTime, yaw = yaw or false }
    if npc == nil or teleportsIgnored then return end
    if pendingTeleport ~= nil then
        pendingTeleport.x, pendingTeleport.y, pendingTeleport.z = x, y, z
        pendingTeleport.yaw = yaw
    else
        pendingTeleport = { at = simTime + TELE_LAT, x = x, y = y, z = z, yaw = yaw }
    end
end

local stepAiBase = stepAi
local yawWrapped = false
function stepAi()
    local pending = pendingTeleport
    local due = pending ~= nil and simTime >= pending.at
    stepAiBase()
    if due and pending.yaw ~= nil then npcYaw = pending.yaw end
    if npc ~= nil and not yawWrapped then
        yawWrapped = true
        local controller = npc:GetAIControllerComponent()
        local send = controller.SendCommand
        function controller:SendCommand(command)
            if command._type == "handle:AIRotateToCommand" then
                local p = command.target.wp.v
                rotateLog[#rotateLog + 1] = simTime
                npcYaw = headingOf(p.x - npc.x, p.y - npc.y)
            elseif command._type == "handle:AIMoveToCommand" then
                local p = command.movementTarget.wp.v
                moveLog[#moveLog + 1] = { t = simTime, kind = command.movementType, x = p.x, y = p.y }
            end
            return send(self, command)
        end
    end
end

local stepNpcBase = stepNpc
function stepNpc(dt)
    if npc == nil then return stepNpcBase(dt) end
    local x, y, z = npc.x, npc.y, npc.z
    stepNpcBase(dt)
    -- walkable area ends at wallX (navmesh edge, car hood, low wall);
    -- teleports are not limited by it
    if wallX ~= nil and x <= wallX and npc.x > wallX then
        npc.x = wallX
        npc.target = nil
    end
    if noClimb then npc.z = z end
    local dx, dy = npc.x - x, npc.y - y
    if dx * dx + dy * dy > 1e-8 then npcYaw = headingOf(dx, dy) end
end
"""

YAW_NORTH, YAW_EAST = 0.0, -90.0


def heading(fx, fy):
    return math.degrees(math.atan2(-fx, fy))


def angle_diff(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


def receiver(extra=""):
    lua = live.make_receiver("host", extra=YAW_MOCK + extra)
    return lua


def session(path, seconds, extra=""):
    lua = receiver(extra)
    remote = live.ScriptedRemote(lua, path, seed=11)
    track = []

    def on_frame(t):
        npc = lua.globals().npc
        if npc is not None:
            truth = path(t)
            track.append((t, npc.x, npc.y, npc.z, truth[0], truth[1], lua.globals().npcYaw))

    live.run(lua, remote, seconds, 60, on_frame=on_frame)
    return lua, track


def moves(lua, t0=0.0, t1=1e9):
    return [(m.t, m.kind, m.x, m.y) for m in lua.globals().moveLog.values() if t0 <= m.t < t1]


def rotates(lua, t0=0.0, t1=1e9):
    return [t for t in lua.globals().rotateLog.values() if t0 <= t < t1]


def teleports(lua, t0=0.0, t1=1e9):
    return [(e.t, e.yaw) for e in lua.globals().teleportLog.values() if t0 <= e.t < t1]


def straight(speed, start=2.0):
    def path(t):
        return (10.0 + speed * max(0.0, t - start), 5.0, 0.0, 1.0, 0.0, 0)
    return path


# ------------------------------------------------------------------ A1 / A2

def test_straight_line_no_resend_storm():
    ok = True
    for speed, limit in ((4.5, 16), (7.0, 14)):
        lua, track = session(straight(speed), 12.0)
        steady = moves(lua, 5.0, 12.0)
        errors = [math.hypot(x - tx, y - ty) for t, x, y, z, tx, ty, _ in track if t >= 5.0]
        mean = sum(errors) / len(errors)
        print(f"  {speed} m/s straight: AIMoveTo in 5-12 s = {len(steady)} (limit {limit}), mean error {mean:.2f} m")
        ok = ok and len(steady) <= limit and mean < 3.5
    return ok


def test_catch_up_gear_does_not_flap():
    # speeds where the avatar's gait can close the gap (mock Walk 2.0, Run 4.5):
    # without hysteresis the gear changed on almost every AIMoveTo (about 22 in 7 s)
    ok = True
    for speed in (2.0, 4.0):
        lua, _ = session(straight(speed), 12.0)
        kinds = [kind for _, kind, _, _ in moves(lua, 5.0, 12.0)]
        flips = sum(1 for a, b in zip(kinds, kinds[1:]) if a != b)
        print(f"  {speed} m/s straight: gears {''.join(k[0] for k in kinds)} -> {flips} gear changes (limit 8)")
        ok = ok and flips <= 8
    return ok


# ------------------------------------------------------------------ A3

def test_turn_still_resteers():
    turn_at, speed = 5.0, 4.5

    def path(t):
        if t < turn_at:
            return (10.0 + speed * max(0.0, t - 2.0), 5.0, 0.0, 1.0, 0.0, 0)
        corner = 10.0 + speed * (turn_at - 2.0)
        return (corner, 5.0 + speed * (t - turn_at), 0.0, 0.0, 1.0, 0)

    lua, track = session(path, 8.0)
    corner_y = 5.0
    north = [m for m in moves(lua, turn_at, turn_at + 0.8) if m[3] > corner_y + 1.0]
    late = [math.hypot(x - tx, y - ty) for t, x, y, z, tx, ty, _ in track if turn_at + 1.5 <= t < 8.0]
    print(f"  turn north at {turn_at} s: AIMoveTo to the north within 0.8 s = {len(north)}, "
          f"max error 1.5-3 s after = {max(late):.2f} m")
    return len(north) >= 1 and max(late) < 3.0


# ------------------------------------------------------------------ A4

def test_faces_remote_after_backpedal_and_strafe():
    ok = True

    def backpedal(t):
        y = 5.0 - 3.0 * max(0.0, min(t, 5.0) - 3.0)
        return (10.0, y, 0.0, 0.0, 1.0, 0)

    def strafe(t):
        x = 10.0 + 3.0 * max(0.0, min(t, 5.0) - 3.0)
        return (x, 5.0, 0.0, 0.0, 1.0, 0)

    for name, path in (("backpedal south", backpedal), ("strafe east", strafe)):
        lua, _ = session(path, 8.0)
        yaw = lua.globals().npcYaw
        after = rotates(lua, 5.0, 8.0)
        print(f"  {name} facing north: rotates after stop = {len(after)}, avatar yaw at end = {yaw:.0f} (want 0)")
        ok = ok and angle_diff(yaw, YAW_NORTH) < 10.0
    return ok


# ------------------------------------------------------------------ A5

def test_teleport_keeps_remote_facing():
    # far correction: the remote is moved 20 m (elevator, fast travel) while facing east
    def jump(t):
        y = 5.0 if t < 4.0 else 25.0
        return (10.0, y, 0.0, 1.0, 0.0, 0)

    lua, _ = session(jump, 7.0)
    far = teleports(lua, 4.0, 7.0)
    far_ok = bool(far) and all(yaw is not False and angle_diff(yaw, YAW_EAST) < 1.0 for _, yaw in far)
    end_yaw = lua.globals().npcYaw
    print(f"  far correction: teleport yaws {[round(y, 1) if y is not False else None for _, y in far]}, "
          f"avatar yaw at end {end_yaw:.0f} (want -90)")

    # dash east at 15 m/s while facing east, then stand
    def dash(t):
        x = 10.0 + 15.0 * max(0.0, min(t, 4.4) - 4.0)
        return (x, 5.0, 0.0, 1.0, 0.0, 0)

    lua2, _ = session(dash, 6.0)
    during = teleports(lua2, 4.0, 4.8)
    dash_ok = len(during) >= 3 and all(yaw is not False and angle_diff(yaw, YAW_EAST) < 1.0 for _, yaw in during)
    dash_yaw = lua2.globals().npcYaw
    print(f"  dash: {len(during)} follow teleports, yaws {sorted({round(y, 1) if y is not False else None for _, y in during}, key=str)}, "
          f"avatar yaw at end {dash_yaw:.0f} (want -90)")
    return far_ok and dash_ok and angle_diff(end_yaw, YAW_EAST) < 10.0 and angle_diff(dash_yaw, YAW_EAST) < 10.0


# ------------------------------------------------------------------ A6 / A7 / A8

def walk_and_stand(t, turn_at=8.0):
    # walk east 3 m/s from x=10 to x=20, stand facing north, turn east at turn_at
    x = min(20.0, 10.0 + 3.0 * max(0.0, t - 2.0))
    fx, fy = (0.0, 1.0) if t < turn_at else (1.0, 0.0)
    return (x, 5.0, 0.0, fx, fy, 0)


def test_settle_unreachable_near():
    lua, track = session(walk_and_stand, 14.0, extra="wallX = 19.4")
    idle_moves = moves(lua, 6.5, 14.0)
    turned = rotates(lua, 8.0, 14.0)
    tele = teleports(lua, 5.5, 14.0)
    yaw = lua.globals().npcYaw
    gave_up = [l for l in live.logs(lua) if "cannot reach" in l]
    print(f"  wall 0.6 m short: idle AIMoveTo = {len(idle_moves)} (limit 4), rotates after turn = {len(turned)}, "
          f"teleports = {len(tele)}, yaw at end = {yaw:.0f} (want -90), log={gave_up[:1]}")
    return len(idle_moves) <= 4 and len(turned) >= 1 and not tele and angle_diff(yaw, YAW_EAST) < 10.0


def test_settle_unreachable_far():
    lua, track = session(walk_and_stand, 14.0, extra="wallX = 17.0")
    idle_moves = moves(lua, 6.5, 14.0)
    tele = teleports(lua, 5.5, 14.0)
    npc = lua.globals().npc
    error = math.hypot(npc.x - 20.0, npc.y - 5.0)
    yaw = lua.globals().npcYaw
    print(f"  wall 3 m short: idle AIMoveTo = {len(idle_moves)}, teleports = {len(tele)} (want 1), "
          f"final error = {error:.2f} m, yaw at end = {yaw:.0f} (want -90)")
    return len(tele) == 1 and len(idle_moves) <= 5 and error < 0.35 and angle_diff(yaw, YAW_EAST) < 10.0


def test_settle_reachable():
    lua, track = session(walk_and_stand, 12.0)
    tele = teleports(lua, 5.5, 12.0)
    npc = lua.globals().npc
    error = math.hypot(npc.x - 20.0, npc.y - 5.0)
    idle_moves = moves(lua, 6.0, 12.0)
    yaw = lua.globals().npcYaw
    # the idle rotate must not cancel the last part of the walk (it stopped up to 0.35 m short)
    print(f"  reachable: final error = {error:.2f} m (limit 0.10), idle teleports = {len(tele)}, "
          f"idle AIMoveTo = {len(idle_moves)}, yaw at end = {yaw:.0f} (want -90)")
    return error < 0.10 and not tele and len(idle_moves) <= 3 and angle_diff(yaw, YAW_EAST) < 10.0


# ------------------------------------------------------------------ A9

def test_reds_teleport_rotation():
    package = os.path.abspath(os.path.join(os.path.dirname(live.harness.SCRIPT), *[".."] * 6))
    source = open(os.path.join(package, "r6", "scripts", "CP2077Coop", "remote.reds"), encoding="utf-8").read()
    method = re.search(r"func CP2077Coop_MoveRemoteTest\((.*?)\)\s*->\s*Void\s*\{(.*?)\n\}", source, re.S)
    if method is None:
        print("  CP2077Coop_MoveRemoteTest not found")
        return False
    params = re.findall(r"(\w+)\s*:\s*Float", method.group(1))
    body = method.group(2)
    uses_forward = "forwardX" in body and "forwardY" in body and re.search(r"command\.rotation\s*=\s*Vector4\.Heading", body)
    hard_zero = re.search(r"command\.rotation\s*=\s*0\.0", body)
    print(f"  remote.reds params={params}, rotation from forward={bool(uses_forward)}, rotation 0.0={bool(hard_zero)}")
    return params == ["x", "y", "z", "forwardX", "forwardY"] and bool(uses_forward) and not hard_zero


# ------------------------------------------------------------------ A10

LONG_SPRINT = [(0.0, 3.0, 0.0), (3.0, 15.0, 7.0), (15.0, 17.0, 0.0)]


def long_sprint():
    result = sim.run(live.harness.SCRIPT, seed=1, phases=LONG_SPRINT, seconds=17.0)
    part = sim.segment(result, 3.0, 15.0)
    print(f"  12 s sprint at 7 m/s: hard corrections {part['hard']}, AIMoveTo {part['moveCommands']}, teleports "
          f"{part['teleports']}, error mean {part['mean']:.2f} m max {part['max']:.2f} m, measured lag max "
          f"{part['lag_max']:.2f} m")
    return part


SPRINT_RESULT = {}


def sprint_part():
    if "part" not in SPRINT_RESULT:
        SPRINT_RESULT["part"] = long_sprint()
    return SPRINT_RESULT["part"]


def test_long_sprint_no_storm():
    part = sprint_part()
    return part["hard"] <= 2 and part["moveCommands"] <= 20


def test_long_sprint_lag():
    return sprint_part()["lag_max"] < 4.0


if __name__ == "__main__":
    tests = {
        "A1 straight run/sprint: no AIMoveTo resend storm": test_straight_line_no_resend_storm,
        "A2 steady run: catch-up gear does not flap": test_catch_up_gear_does_not_flap,
        "A3 a real turn still re-steers quickly": test_turn_still_resteers,
        "A4 avatar faces the remote again after backpedal / strafe": test_faces_remote_after_backpedal_and_strafe,
        "A5 teleports keep the remote's facing (far correction, dash)": test_teleport_keeps_remote_facing,
        "A6 settle: spot just out of reach -> stops retrying and turns": test_settle_unreachable_near,
        "A7 settle: spot far out of reach -> exactly one teleport": test_settle_unreachable_far,
        "A8 settle: reachable spot reached exactly, no teleport, then faces the remote": test_settle_reachable,
        "A9 remote.reds teleport rotation from the forward vector": test_reds_teleport_rotation,
        "A10 12 s sprint: at most 2 hard corrections and 20 AIMoveTo": test_long_sprint_no_storm,
        "A10 12 s sprint: measured avatar lag under 4 m [KNOWN: sprint catch-up, needs bench NPC speed]": test_long_sprint_lag,
    }
    results = {}
    for name, test in tests.items():
        print(f"-- {name}")
        try:
            results[name] = test()
        except Exception as error:  # report and keep going
            print(f"  EXCEPTION {error!r}")
            results[name] = False
    for name, passed in results.items():
        print(f"{'PASS' if passed else 'FAIL'}  {name}")
    sys.exit(0 if all(results.values()) else 1)
