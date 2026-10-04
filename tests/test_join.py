"""The joiner's teleport to the host, against a game that is slow to take a teleport.

Live test 2026-10-04: the join teleport ran ~8 s after the save loaded, called
Teleport 8 times in 2 s and the player never moved (WORLD SYNC FAILED 14.42 ->
12.31 -> 9.44 m while the host bot walked on), then gave up.

The mock TeleportationFacility drops every Teleport call during the first
tpIgnoreFor s after the load and moves the player tpApplyDelay s after an
accepted call, on a later frame. A newer call replaces one still queued.

J1  slow game, host sprinting: the join waits until the joiner has been in the
    game for 4 s, calls Teleport once per attempt, measures the result against the
    point it teleported to (the host is 4+ m further by then) and joins on attempt
    2. The previous logic (OLD_REVISION, read with git) gives up without moving.
J2  teleports never apply: 3 attempts, each logged with the measured result and
    "position did not change", growing pauses (2 s, 4 s), one GAVE UP, no more
    Teleport calls, avatar spawned after. "Teleport to host" then runs the same
    path: one call, joined on attempt 1/3.
J3  settle gate: a vehicle, a scene and a position jump (game still placing the
    player) each restart the 4 s; the first Teleport comes 4 s after the last one.
    While the player sits in a car the avatar spawns at once, as before.
J4  a teleport the game applies after the 2.5 s wait still counts: joined during
    the pause, no second call.

Usage: python test_join.py path/to/init.lua
"""
import math
import os
import re
import subprocess
import sys

from lupa.luajit21 import LuaRuntime

import coop_sim30 as sim
import test_two_players as harness
import test_live_bugs as live

FRAME_DT = 1.0 / 60.0
LOAD_AT = 1.0
FLAG_HOST = 256
FLAG_IN_VEHICLE = 16

# the mod's join settings (Sync.JOIN_* and JOIN_OFFSET in init.lua)
SETTLE = 4.0
APPLY_TIMEOUT = 2.5
RETRY_DELAYS = (2.0, 4.0)
TOLERANCE = 2.5
JOIN_OFFSET = 1.75

# the last commit with the old join loop (8 Teleport calls in 2 s per attempt)
OLD_REVISION = "30077d1b47646a2ca5f95482a2443165852fe1cc"
SCRIPT_IN_REPO = "bin/x64/plugins/cyber_engine_tweaks/mods/CP2077Coop/init.lua"

HOST_START = (40.0, 25.0)
JOINER_START = (30.0, 15.0)  # ~14 m from the host, like the live test

GAME_MOCK = r"""
-- loading screen until the test ends it (loadedAt = that moment)
preGame = true
loadedAt = nil
Game.GetSystemRequestsHandler = function()
    return { IsPreGame = function() return preGame end }
end

-- the game right after a save load: Teleport calls in the first tpIgnoreFor s
-- after the load are dropped; an accepted one moves the player tpApplyDelay s
-- later, on a later frame; a newer call replaces one still queued
tpIgnoreFor = 8.0
tpApplyDelay = 0.6
tpCalls = {}
tpPending = nil
Game.GetTeleportationFacility = function()
    return { Teleport = function(_, entity, pos, rotation)
        if type(rotation) ~= "table" or rotation._type ~= "EulerAngles" then
            error("Function 'Teleport' parameter 3 must be EulerAngles.")
        end
        local accepted = loadedAt ~= nil and simTime - loadedAt >= tpIgnoreFor
        tpCalls[#tpCalls + 1] = { t = simTime, x = pos.x, y = pos.y, z = pos.z, accepted = accepted }
        if accepted then
            tpPending = { at = simTime + tpApplyDelay, x = pos.x, y = pos.y, z = pos.z, yaw = rotation.yaw }
        end
    end }
end
function stepTeleport()
    if tpPending ~= nil and simTime >= tpPending.at then
        playerPos.x, playerPos.y, playerPos.z = tpPending.x, tpPending.y, tpPending.z
        playerYaw = tpPending.yaw
        tpPending = nil
    end
end

-- state.reds CP2077Coop_GetSceneTier: 1 = free gameplay, 3+ = a scene holds the player
sceneTier = 1
function player:CP2077Coop_GetSceneTier() return sceneTier end

-- where the local player stood when the avatar spawn was requested
spawnRequests = {}
local spawn = player.CP2077Coop_SpawnRemoteTest
function player:CP2077Coop_SpawnRemoteTest(...)
    spawnRequests[#spawnRequests + 1] = { t = simTime, x = playerPos.x, y = playerPos.y }
    return spawn(self, ...)
end
"""

# CET logs an error thrown by onUpdate and calls it again next frame
ERROR_TRAP = r"""
updateErrors = 0
updateErrorText = nil
local update = events.onUpdate
events.onUpdate = function(delta)
    local ok, err = pcall(update, delta)
    if not ok then
        updateErrors = updateErrors + 1
        updateErrorText = updateErrorText or tostring(err)
    end
end
"""


def new_source():
    return open(harness.SCRIPT, encoding="utf-8").read()


def old_source():
    """init.lua before the fix, from git (any clone of the repo has it)."""
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    result = subprocess.run(
        ["git", "-C", tests_dir, "show", f"{OLD_REVISION}:{SCRIPT_IN_REPO}"],
        capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(f"git show {OLD_REVISION[:7]} failed: {result.stderr.strip()}")
    return result.stdout


def sprinting_host(t):
    """Host sprints along +X at 6 m/s, looking along +X (role bit set)."""
    return HOST_START[0] + 6.0 * t, HOST_START[1], 0.0, 1.0, 0.0, FLAG_HOST


def standing_host(t):
    """Host stands still, looking along +Y."""
    return HOST_START[0], HOST_START[1], 0.0, 0.0, 1.0, FLAG_HOST


def side_point(x, y, forward_x, forward_y):
    """Where the joiner is sent: JOIN_OFFSET to the host's left."""
    length = math.hypot(forward_x, forward_y)
    return x - forward_y / length * JOIN_OFFSET, y + forward_x / length * JOIN_OFFSET


class Session:
    """One joiner init.lua at 60 fps; the host is a scripted peer (30 Hz, 115 ms)."""

    def __init__(self, source, path, ignore_for=8.0, apply_delay=0.6, seed=4):
        for leftover in ("role.txt", "testpattern.txt"):
            if os.path.exists(leftover):
                os.remove(leftover)
        lua = LuaRuntime(unpack_returned_tuples=True)
        g = lua.globals()
        g.simTime = 0.0
        lua.execute(sim.MOCK)
        lua.execute(harness.IMGUI_MOCK)
        lua.execute(harness.PLAYER_EXTRA.replace("PX", str(JOINER_START[0])).replace("PY", str(JOINER_START[1])))
        lua.execute(live.AI_TELEPORT_MOCK)
        lua.execute(GAME_MOCK)
        source, count = re.subn(r"(?m)^local IS_HOST = true", "local IS_HOST = false", source)
        assert count == 1
        lua.eval("function(source) assert(load(source, '=init.lua'))() end")(source)
        g.events["onInit"]()
        lua.execute(ERROR_TRAP)
        g.tpIgnoreFor = ignore_for
        g.tpApplyDelay = apply_delay
        self.lua, self.g, self.path = lua, g, path
        self.remote = live.ScriptedRemote(lua, path, loss=0.0, seed=seed)
        self.step_npc = lua.eval("stepNpc")
        self.step_ai = lua.eval("stepAi")
        self.step_teleport = lua.eval("stepTeleport")
        self.t = 0.0
        self.stamped = []  # (t, line) for every log line, stamped with its frame time

    def run(self, until, on_frame=None):
        g = self.g
        while self.t < until:
            t = self.t
            g.simTime = t
            if g.preGame and t >= LOAD_AT:
                g.preGame = False
                g.loadedAt = t
            if on_frame is not None:
                on_frame(self, t)
            self.remote.tick(t)
            g.tickSpawn()
            g.events["onUpdate"](FRAME_DT)
            self.step_npc(FRAME_DT)
            self.step_ai()
            self.step_teleport()
            self.remote.read_outbox(t)
            logs = g.logs
            for index in range(len(self.stamped) + 1, len(logs) + 1):
                self.stamped.append((t, logs[index]))
            self.t += FRAME_DT

    def click(self, label):
        self.g.clickButton = label
        self.g.events["onDraw"]()
        self.g.clickButton = None

    def lines(self, text):
        return [(t, line) for t, line in self.stamped if text in line]

    def calls(self):
        table = self.g.tpCalls
        return [{key: table[index][key] for key in ("t", "x", "y", "accepted")} for index in range(1, len(table) + 1)]

    def spawns(self):
        table = self.g.spawnRequests
        return [(table[index].t, table[index].x, table[index].y) for index in range(1, len(table) + 1)]

    def player(self):
        return self.g.playerPos.x, self.g.playerPos.y


def show(session, texts, indent="    "):
    for t, line in session.stamped:
        if any(text in line for text in texts):
            print(f"{indent}{t:6.2f}  {line.replace('[CP2077Coop] ', '')}")


JOIN_LINES = ("WORLD SYNC", "remote spawn requested", "manual teleport")


# ------------------------------------------------------------------ J1

def test_slow_game_joins():
    marks = {}

    def watch(session, t):
        if "ok" not in marks and session.lines("WORLD SYNC OK"):
            x, y, _, forward_x, forward_y, _ = sprinting_host(t)
            marks["ok"] = (t, session.player(), side_point(x, y, forward_x, forward_y))

    new = Session(new_source(), sprinting_host)
    new.run(22.0, watch)
    calls = new.calls()
    attempts = new.lines("P2 WORLD SYNC ->")
    failed = new.lines("WORLD SYNC FAILED")
    joined = new.lines("WORLD SYNC OK")
    print(f"  new logic: load at {LOAD_AT:.2f} s, game ignores Teleport for 8 s, applies it 0.6 s after the call; host sprints 6 m/s")
    show(new, JOIN_LINES)
    call_times = [round(c["t"], 2) for c in calls]
    gaps = [b - a for a, b in zip(call_times, call_times[1:])]
    print(f"    {len(calls)} Teleport calls at {call_times[:8]} accepted {[c['accepted'] for c in calls[:8]]}")
    new_ok = bool(joined) and len(joined) == 1 and not new.lines("GAVE UP") and new.g.updateErrors == 0
    if new_ok:
        ok_t, (px, py), host_point = marks["ok"]
        last = calls[-1]
        miss = math.hypot(px - last["x"], py - last["y"])
        host_moved = math.hypot(px - host_point[0], py - host_point[1])
        spawn = new.spawns()
        spawn_near = bool(spawn) and spawn[0][0] > ok_t and math.hypot(spawn[0][1] - last["x"], spawn[0][2] - last["y"]) <= TOLERANCE
        print(f"    at OK ({ok_t:.2f} s): player {miss:.2f} m from the teleport point, {host_moved:.2f} m from the "
              f"host's side point now (the host moved on); avatar spawn after OK next to the player: {spawn_near}")
        new_ok = (
            calls[0]["t"] >= LOAD_AT + SETTLE
            and len(calls) == len(attempts) <= 3
            and all(gap >= APPLY_TIMEOUT for gap in gaps)
            and calls[-1]["accepted"] and not any(c["accepted"] for c in calls[:-1])
            and f"attempt={len(attempts)}/3" in joined[0][1]
            and len(failed) == len(attempts) - 1
            and all("moved=0.00" in line and "position did not change" in line for _, line in failed)
            and miss <= TOLERANCE < host_moved
            and spawn_near
        )

    old = Session(old_source(), sprinting_host)
    old.run(22.0)
    old_calls = old.calls()
    old_attempts = max(1, len(old.lines("WORLD SYNC FAILED")))
    moved = math.hypot(old.player()[0] - JOINER_START[0], old.player()[1] - JOINER_START[1])
    gave_up = old.lines("GAVE UP")
    print(f"  old logic ({OLD_REVISION[:7]}), same game:")
    show(old, ("WORLD SYNC FAILED", "GAVE UP", "WORLD SYNC OK"))
    print(f"    {len(old_calls)} Teleport calls ({len(old_calls) / old_attempts:.0f} per attempt), "
          f"first {old_calls[0]['t']:.2f} s, player moved {moved:.2f} m")
    old_failed = bool(gave_up) and not old.lines("WORLD SYNC OK") and moved < 0.5 and len(old_calls) >= 3 * 6
    return new_ok and old_failed


# ------------------------------------------------------------------ J2

def test_never_applies_then_manual():
    session = Session(new_source(), standing_host, ignore_for=1e9)
    session.run(30.0)
    calls = session.calls()
    failed = session.lines("WORLD SYNC FAILED")
    gave_up = session.lines("GAVE UP")
    print("  game never applies a teleport:")
    show(session, JOIN_LINES)
    times = [c["t"] for c in calls]
    gaps = [b - a for a, b in zip(times, times[1:])]
    print(f"    {len(times)} Teleport calls at {[round(t, 2) for t in times[:8]]}, gaps {[round(gap, 2) for gap in gaps[:8]]} "
          f"(want {APPLY_TIMEOUT} + {RETRY_DELAYS[0]}, {APPLY_TIMEOUT} + {RETRY_DELAYS[1]})")
    spawn = session.spawns()
    gave_up_at = gave_up[0][0] if gave_up else 99.0
    give_up_ok = (
        len(calls) == 3
        and len(failed) == 3
        and all("moved=0.00" in line and "position did not change" in line for _, line in failed)
        and [f"attempt={n}/3" in line for n, (_, line) in enumerate(failed, 1)] == [True] * 3
        and len(gave_up) == 1
        and max(times) < gave_up_at
        and APPLY_TIMEOUT + RETRY_DELAYS[0] <= gaps[0] < APPLY_TIMEOUT + RETRY_DELAYS[0] + 0.2
        and APPLY_TIMEOUT + RETRY_DELAYS[1] <= gaps[1] < APPLY_TIMEOUT + RETRY_DELAYS[1] + 0.2
        and bool(spawn) and spawn[0][0] >= gave_up_at
        and session.g.updateErrors == 0
    )

    # the game takes teleports again; the panel button runs the same path
    session.g.tpIgnoreFor = 0.0
    mark = len(session.stamped)
    session.click("Teleport to host")
    session.run(34.0)
    manual_calls = session.calls()[3:]
    joined = [line for _, line in session.stamped[mark:] if "WORLD SYNC OK" in line]
    point = side_point(*standing_host(0.0)[:2], *standing_host(0.0)[3:5])
    miss = math.hypot(session.player()[0] - point[0], session.player()[1] - point[1])
    print("  then 'Teleport to host' with a working game:")
    for t, line in session.stamped[mark:]:
        if any(text in line for text in JOIN_LINES):
            print(f"    {t:6.2f}  {line.replace('[CP2077Coop] ', '')}")
    manual_ok = len(manual_calls) == 1 and len(joined) == 1 and "attempt=1/3" in joined[0] and miss <= TOLERANCE
    return give_up_ok and manual_ok


# ------------------------------------------------------------------ J3

VEHICLE = (2.0, 3.5)
SCENE = (4.5, 5.5)
JUMP_AT = 6.5


def test_settle_gate():
    jumped = {}

    def script(session, t):
        g = session.g
        g.localFlags = FLAG_IN_VEHICLE if VEHICLE[0] <= t < VEHICLE[1] else 0
        g.sceneTier = 4 if SCENE[0] <= t < SCENE[1] else 1
        if t >= JUMP_AT and not jumped:
            jumped["t"] = t
            g.playerPos.x = g.playerPos.x + 60.0  # the game places the player after a load

    session = Session(new_source(), standing_host, ignore_for=0.0, apply_delay=0.3)
    session.run(14.0, script)
    calls = session.calls()
    waits = session.lines("WORLD SYNC waiting")
    print(f"  in a car {VEHICLE[0]}-{VEHICLE[1]} s, scene tier 4 {SCENE[0]}-{SCENE[1]} s, position jumps 60 m at {JUMP_AT} s:")
    show(session, JOIN_LINES)
    first = calls[0]["t"] if calls else 99.0
    spawn = session.spawns()
    spawn_in_car = bool(spawn) and VEHICLE[0] <= spawn[0][0] < VEHICLE[1] + 0.1
    print(f"    first Teleport at {first:.2f} s (want {JUMP_AT + SETTLE:.2f}-{JUMP_AT + SETTLE + 0.2:.2f}), "
          f"avatar spawn requested while in the car: {spawn_in_car}")
    reasons = [line for _, line in waits]
    return (
        len(calls) == 1
        and JUMP_AT + SETTLE <= first < JUMP_AT + SETTLE + 0.2
        and any("in a vehicle" in line for line in reasons)
        and any("in a scene" in line for line in reasons)
        and any("position jumped" in line for line in reasons)
        and len(session.lines("WORLD SYNC OK")) == 1
        and spawn_in_car
        and session.g.updateErrors == 0
    )


# ------------------------------------------------------------------ J4

def test_late_apply_counts():
    session = Session(new_source(), standing_host, ignore_for=0.0, apply_delay=3.2)
    session.run(14.0)
    calls = session.calls()
    failed = session.lines("WORLD SYNC FAILED")
    joined = session.lines("WORLD SYNC OK")
    print("  game applies the teleport 3.2 s after the call (wait is 2.5 s):")
    show(session, JOIN_LINES)
    return (
        len(calls) == 1
        and len(failed) == 1 and "position did not change" in failed[0][1]
        and len(joined) == 1 and "applied late" in joined[0][1]
        and abs(joined[0][0] - (calls[0]["t"] + 3.2)) < 0.05
        and not session.lines("GAVE UP")
        and session.g.updateErrors == 0
    )


if __name__ == "__main__":
    tests = {
        "J1 slow game: settle, one Teleport per attempt, measured at the teleport point, joins; old logic gives up": test_slow_game_joins,
        "J2 teleports never apply: 3 logged attempts, growing pauses, clean give-up; 'Teleport to host' same path": test_never_applies_then_manual,
        "J3 car, scene and position jump restart the 4 s settle; avatar spawns while in the car": test_settle_gate,
        "J4 a teleport applied after the 2.5 s wait counts, no second call": test_late_apply_counts,
    }
    results = {}
    for name, test in tests.items():
        print(f"-- {name}")
        try:
            results[name] = bool(test())
        except Exception as error:  # report and keep going
            print(f"  EXCEPTION {error!r}")
            results[name] = False
    for name, passed in results.items():
        print(f"{'PASS' if passed else 'FAIL'}  {name}")
    sys.exit(0 if all(results.values()) else 1)
