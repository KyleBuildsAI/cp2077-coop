"""Offline simulation of CP2077Coop init.lua (CET bridge) under LuaJIT.

Mocks the CET/game API, drives a scripted remote-player path over a lossy,
jittery network, models the remote NPC as a lagging AI follower, and reports
how closely the NPC tracks the real remote player.
"""
import math
import random
import sys

from lupa.luajit21 import LuaRuntime

FRAME_DT = 1.0 / 60.0
SIM_SECONDS = 30.0
SEND_INTERVAL = 0.05
ONE_WAY_LATENCY = 0.095
JITTER = 0.030
LOSS = 0.01

MOCK = r"""
events = {}
logs = {}
function registerForEvent(name, fn) events[name] = fn end
function registerHotkey() end
function print(msg) logs[#logs + 1] = tostring(msg) end
Vector4 = { new = function(x, y, z, w) return { x = x, y = y, z = z, w = w } end }
CName = { new = function(s) return s end }

function NewObject(kind)
    local o = { _type = kind }
    function o:SetVector4(_, v) self.v = v end
    function o:SetWorldPosition(_, wp) self.wp = wp end
    return o
end

-- remote NPC (the avatar we are syncing)
npc = nil
stats = { moveCommands = 0, teleports = 0, rotates = 0 }
AI_DELAY = 0.10
SPEEDS = { Walk = 2.0, Run = 4.5, Sprint = 7.0 }

local function makeNpc(x, y, z)
    local n = { x = x, y = y, z = z, target = nil, pending = nil, pendingAt = 0 }
    local ctrl = {}
    function ctrl:SendCommand(cmd)
        if cmd._type == "handle:AIMoveToCommand" then
            stats.moveCommands = stats.moveCommands + 1
            n.pending = { v = cmd.movementTarget.wp.v, kind = cmd.movementType }
            n.pendingAt = simTime + AI_DELAY
        elseif cmd._type == "handle:AIRotateToCommand" then
            stats.rotates = stats.rotates + 1
        end
    end
    function ctrl:StopExecutingCommand(cmd) n.target = nil end
    function ctrl:CancelCommand(cmd) n.target = nil end
    function n:GetAIControllerComponent() return ctrl end
    function n:GetWorldPosition() return { x = n.x, y = n.y, z = n.z, w = 1 } end
    return n
end

function stepNpc(dt)
    if npc == nil then return end
    if npc.pending and simTime >= npc.pendingAt then
        npc.target = npc.pending
        npc.pending = nil
    end
    local t = npc.target
    if t == nil then return end
    local dx, dy, dz = t.v.x - npc.x, t.v.y - npc.y, t.v.z - npc.z
    local d = math.sqrt(dx * dx + dy * dy + dz * dz)
    local step = (SPEEDS[t.kind] or 2.0) * dt
    if d <= step or d < 0.05 then
        npc.x, npc.y, npc.z = t.v.x, t.v.y, t.v.z
        npc.target = nil
    else
        npc.x = npc.x + dx / d * step
        npc.y = npc.y + dy / d * step
        npc.z = npc.z + dz / d * step
    end
end

spawnAt = nil
net = { has = false, x = 0, y = 0, z = 0, fx = 0, fy = 1, seq = 0 }

player = {}
function player:IsAttached() return true end
function player:GetWorldPosition() return { x = 0, y = 0, z = 0, w = 1 } end
function player:GetWorldForward() return { x = 0, y = 1, z = 0, w = 0 } end
function player:CP2077Coop_SpawnRemoteTest(x, y, z)
    -- old script spawns in front of local player; new one passes coords
    spawnAt = { t = simTime + 0.3, x = (x or 0) + 1.0, y = (y or 2.5), z = z or 0 }
end
function player:CP2077Coop_MoveRemoteTest(x, y, z)
    stats.teleports = stats.teleports + 1
    if npc then npc.x, npc.y, npc.z = x, y, z; npc.target = nil; npc.pending = nil end
end

Game = {
    GetPlayer = function() return player end,
    GetSystemRequestsHandler = function() return { IsPreGame = function() return false end } end,
    GetDynamicEntitySystem = function()
        return { GetTagged = function() if npc then return { npc } end return {} end }
    end,
    CP2077Coop_PushPlayerState = function() end,
    CP2077Coop_HasRemotePlayer = function() return net.has end,
    CP2077Coop_GetRemoteSequence = function() return net.seq end,
    CP2077Coop_GetRemoteX = function() return net.x end,
    CP2077Coop_GetRemoteY = function() return net.y end,
    CP2077Coop_GetRemoteZ = function() return net.z end,
    CP2077Coop_GetRemoteForwardX = function() return net.fx end,
    CP2077Coop_GetRemoteForwardY = function() return net.fy end,
}

function tickSpawn()
    if spawnAt and npc == nil and simTime >= spawnAt.t then
        npc = makeNpc(spawnAt.x, spawnAt.y, spawnAt.z)
    end
end
"""

# (start, end, speed m/s) segments; heading turns slowly so the path curves
PHASES = [
    (0.0, 3.0, 0.0),
    (3.0, 10.0, 4.5),
    (10.0, 13.0, 1.8),
    (13.0, 15.0, 7.0),
    (15.0, 17.0, 0.0),
    (17.0, 27.0, 15.0),
    (27.0, 30.0, 0.0),
]
START_X, START_Y = 40.0, 25.0  # remote starts far from local player


def speed_at(t):
    for start, end, speed in PHASES:
        if start <= t < end:
            return speed
    return 0.0


def build_truth():
    """Precompute the remote player's true path at frame resolution."""
    samples = []
    x, y, heading = START_X, START_Y, 0.0
    t = 0.0
    while t <= SIM_SECONDS + 1.0:
        speed = speed_at(t)
        heading += 0.15 * FRAME_DT
        fx, fy = -math.sin(heading), math.cos(heading)
        samples.append((t, x, y, 0.0, fx, fy, speed))
        x += fx * speed * FRAME_DT
        y += fy * speed * FRAME_DT
        t += FRAME_DT
    return samples


def run(script_path, seed=7):
    rng = random.Random(seed)
    lua = LuaRuntime(unpack_returned_tuples=True)
    g = lua.globals()
    g.simTime = 0.0
    lua.execute(MOCK)
    with open(script_path, encoding="utf-8") as handle:
        lua.execute(handle.read())

    truth = build_truth()
    in_flight = []  # (arrive_time, seq, x, y, z, fx, fy)
    seq = 0
    next_send = 0.0
    errors = {"idle": [], "run/walk": [], "vehicle": []}
    backwards = 0
    prev_npc = None

    on_update = g.events["onUpdate"]
    for frame, (t, x, y, z, fx, fy, speed) in enumerate(truth):
        if t > SIM_SECONDS:
            break
        g.simTime = t

        if t >= next_send:
            next_send += SEND_INTERVAL
            seq += 1
            if rng.random() > LOSS:
                arrive = t + ONE_WAY_LATENCY + rng.random() * JITTER
                in_flight.append((arrive, seq, x, y, z, fx, fy))

        # DLL keeps whatever packet arrived last (no ordering on its side)
        arrived = sorted((p for p in in_flight if p[0] <= t), key=lambda p: p[0])
        in_flight = [p for p in in_flight if p[0] > t]
        net = g.net
        for _, pseq, px, py, pz, pfx, pfy in arrived:
            net.has = True
            net.seq, net.x, net.y, net.z, net.fx, net.fy = pseq, px, py, pz, pfx, pfy

        g.tickSpawn()
        on_update(FRAME_DT)
        lua.eval("stepNpc")(FRAME_DT)

        npc = g.npc
        if npc is None or t < 1.0:
            continue
        err = math.hypot(npc.x - x, npc.y - y)
        bucket = "idle" if speed == 0 else ("vehicle" if speed > 9 else "run/walk")
        errors[bucket].append(err)

        if prev_npc and speed > 0:
            mx, my = npc.x - prev_npc[0], npc.y - prev_npc[1]
            if mx * fx + my * fy < -0.02:
                backwards += 1
        prev_npc = (npc.x, npc.y)

    stats = g.stats
    def avg(values):
        return sum(values) / len(values) if values else float("nan")
    return {
        "idle_err": avg(errors["idle"]),
        "move_err": avg(errors["run/walk"]),
        "vehicle_err": avg(errors["vehicle"]),
        "backwards": backwards,
        "teleports": stats.teleports,
        "moveCommands": stats.moveCommands,
        "log_errors": [line for line in g.logs.values() if "ERROR" in line or "FAILED" in line],
    }


if __name__ == "__main__":
    for label, path in zip(sys.argv[1::2], sys.argv[2::2]):
        results = [run(path, seed) for seed in (1, 2, 3)]
        print(f"== {label}")
        for key in ("idle_err", "move_err", "vehicle_err"):
            print(f"  {key:12} {sum(r[key] for r in results) / 3:6.2f} m (avg, 3 seeds)")
        for key in ("backwards", "teleports", "moveCommands"):
            print(f"  {key:12} {sum(r[key] for r in results) // 3:6d} (avg/run)")
        print(f"  log errors   {results[0]['log_errors'][:3]}")
