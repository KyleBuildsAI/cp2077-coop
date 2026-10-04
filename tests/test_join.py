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
    2. The previous logic (init.lua from commit 30077d1, kept as
    tests/fixtures/init_30077d1.lua) gives up without moving.
J2  teleports never apply: 3 attempts, each logged with the measured result and
    "position did not change", growing pauses (2 s, 4 s), one GAVE UP, no more
    Teleport calls, avatar spawned after. "Teleport to host" then runs the same
    path: one call, joined on attempt 1/3.
J3  settle gate: a vehicle, a scene and a position jump (game still placing the
    player) each restart the 4 s; the first Teleport comes 4 s after the last one.
    While the player sits in a car the avatar spawns at once, as before.
J4  a teleport the game applies after the 2.5 s wait still counts: joined during
    the pause, no second call.
J5  the panel's "Join" row says what the join is doing in every phase (and the
    "Avatar" row that the spawn waits for it); the host's panel has no Join row.
J6  the host's own position must be steady too: the joiner is settled before the
    host's first packet, the host's first second is from before the game placed it
    (60 m away). The first Teleport waits for 4 s of steady host packets and aims
    at the placed host.
J7  the host jumps 60 m during an attempt: landing at the old point is a failed
    attempt ("host jumped"), the next one aims at the new spot and joins.

Usage: python test_join.py path/to/init.lua
"""
import hashlib
import math
import os
import re
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

# the last commit with the old join loop (8 Teleport calls in 2 s per attempt), kept
# as a fixture so shallow clones, ZIP downloads and a squash merge still run J1
OLD_REVISION = "30077d1b47646a2ca5f95482a2443165852fe1cc"
OLD_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "init_30077d1.lua")
OLD_BLOB = "e8e39966214acf2ae1c91fe900cb8ab4e0f150ff"  # git rev-parse 30077d1:<init.lua>

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
    """init.lua before the fix, from the fixture; its git blob hash proves it is unchanged."""
    with open(OLD_FIXTURE, "rb") as handle:
        data = handle.read().replace(b"\r\n", b"\n")  # core.autocrlf=true checkouts
    blob = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
    if blob != OLD_BLOB:
        raise RuntimeError(f"{OLD_FIXTURE} is not init.lua from {OLD_REVISION[:7]} "
                           f"(blob {blob[:7]}, want {OLD_BLOB[:7]})")
    return data.decode("utf-8")


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

    print(f"  new logic: {'ok' if new_ok else 'FAILED'}")

    # the old half runs on its own, so a broken fixture cannot hide the new-logic verdict
    try:
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
    except Exception as error:  # reported, and J1 fails: the fixture is in the repo
        print(f"  old logic: EXCEPTION {error!r}")
        old_failed = False
    print(f"  old logic gives up: {old_failed}")
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


# ------------------------------------------------------------------ J5

LEVEL_COLORS = {(0.4, 0.9, 0.45): "good", (1.0, 0.8, 0.25): "warn", (1.0, 0.35, 0.35): "bad", (0.8, 0.8, 0.8): "neutral"}

PANEL_CAPTURE = r"""
panelRows = {}
local lastLabel = ""
ImGui.Text = function(s) lastLabel = s end
ImGui.TextColored = function(r, g, b, a, s) panelRows[#panelRows + 1] = { lastLabel, s, r, g, b } end
"""


def panel_rows(lua):
    """{label: (value, level)}; Diag.row draws ImGui.Text(label), then TextColored(value)."""
    lua.execute(PANEL_CAPTURE)
    lua.globals().events["onDraw"]()
    rows = lua.globals().panelRows
    result = {}
    for index in range(1, len(rows) + 1):
        label, value, red, green, blue = (rows[index][key] for key in range(1, 6))
        result[label] = (value, LEVEL_COLORS.get((round(red, 2), round(green, 2), round(blue, 2)), "?"))
    return result


def test_panel_rows():
    seen = []

    def sample(session, t, at, label):
        if not any(name == label for name, _ in seen) and t >= at:
            rows = panel_rows(session.lua)
            seen.append((label, (rows.get("Join"), rows.get("Avatar"))))

    # game ignores teleports for 7 s after the load: attempt 1 fails, attempt 2 joins
    slow = Session(new_source(), standing_host, ignore_for=7.0)
    plan = ((3.0, "settle"), (6.0, "teleport"), (8.0, "retry"), (11.5, "done"))
    slow.run(12.0, lambda s, t: [sample(s, t, at, label) for at, label in plan])

    # never applies; the player sits in a car for a moment first
    def car(session, t):
        session.g.localFlags = FLAG_IN_VEHICLE if 1.5 <= t < 2.0 else 0
        sample(session, t, 1.8, "vehicle")
        sample(session, t, 23.0, "gave_up")

    stuck = Session(new_source(), standing_host, ignore_for=1e9)
    stuck.run(23.5, car)

    rows = dict(seen)
    for label, (join, avatar) in seen:
        print(f"  {label:9} Join: {join}   Avatar: {avatar}")
    want = {
        "settle": (r"^waiting for the game to settle \([0-9.]+ / 4 s\)$", "warn"),
        "teleport": (r"^attempt 1/3: waiting for the game to move you \([0-9.]+ / 2\.5 s\)$", "warn"),
        "retry": (r"^attempt 1/3 failed \(position did not change\), next in [0-9.]+ s$", "warn"),
        "done": (r"^next to the host \(attempt 2/3, 0\.0 m from the teleport point\)$", "good"),
        "vehicle": (r"^waiting: you are in a vehicle$", "warn"),
        "gave_up": (r"^gave up after 3 attempts \(position did not change\) - press 'Teleport to host'$", "bad"),
    }
    join_ok = all(
        rows.get(label) and rows[label][0] and re.match(pattern, rows[label][0][0]) and rows[label][0][1] == level
        for label, (pattern, level) in want.items()
    )
    avatar_ok = rows["settle"][1][0] == "after the join teleport" and rows["done"][1][0] != "after the join teleport"
    host_rows = panel_rows(harness.make_instance("host", HOST_START))
    print(f"  host panel has a Join row: {'Join' in host_rows}")
    return join_ok and avatar_ok and "Join" not in host_rows


# ------------------------------------------------------------------ J6 + J7

HOST_PLACED = (HOST_START[0] + 60.0, HOST_START[1] + 0.0)  # where the game really puts the host


def jumping_host(jump_at):
    """Host stands at HOST_START, then the game moves it 60 m (load placement, fast travel)."""
    def path(t):
        x, y = HOST_PLACED if t >= jump_at() else HOST_START
        return x, y, 0.0, 0.0, 1.0, FLAG_HOST
    return path


def test_host_loads_late():
    # the joiner is settled long before the host's first packet (host still loading);
    # the host's first second of packets is from before the game placed it
    host_from, jump = 7.0, 8.0
    session = Session(new_source(), jumping_host(lambda: jump), ignore_for=0.0, apply_delay=0.6)

    def host_online(s, t):
        s.remote.silent_from = None if t >= host_from else 0.0

    session.run(16.0, host_online)
    calls = session.calls()
    joined = session.lines("WORLD SYNC OK")
    jumped = session.lines("host position jumped")
    placed = side_point(HOST_PLACED[0], HOST_PLACED[1], 0.0, 1.0)
    first = calls[0] if calls else None
    aim = math.hypot(first["x"] - placed[0], first["y"] - placed[1]) if first else None
    px, py = session.player()
    end_miss = math.hypot(px - placed[0], py - placed[1])
    print(f"  host silent until {host_from:.0f} s, jumps 60 m at {jump:.0f} s (joiner settled at {LOAD_AT + SETTLE:.0f} s):")
    show(session, JOIN_LINES + ("host position jumped",))
    print(f"    first Teleport at {first['t'] if first else None}, {aim if aim is None else round(aim, 2)} m from the placed "
          f"host's side point; player ends {end_miss:.2f} m from it")
    return (
        bool(jumped) and first is not None
        and first["t"] >= jump + SETTLE - 0.1 and aim <= 0.1
        and len(joined) == 1 and "attempt=1/3" in joined[0][1]
        and end_miss <= TOLERANCE
        and session.g.updateErrors == 0
    )


def test_host_jumps_during_attempt():
    # the host fast-travels 0.3 s after our Teleport call: landing at the old point is no join
    marks = {}

    def jump_at():
        return marks["call"] + 0.3 if "call" in marks else 1e9

    session = Session(new_source(), jumping_host(jump_at), ignore_for=0.0, apply_delay=0.6)

    def watch(s, t):
        if "call" not in marks and s.calls():
            marks["call"] = s.calls()[0]["t"]

    session.run(18.0, watch)
    calls = session.calls()
    failed = session.lines("WORLD SYNC FAILED")
    joined = session.lines("WORLD SYNC OK")
    placed = side_point(HOST_PLACED[0], HOST_PLACED[1], 0.0, 1.0)
    second = calls[1] if len(calls) > 1 else None
    aim = math.hypot(second["x"] - placed[0], second["y"] - placed[1]) if second else None
    px, py = session.player()
    end_miss = math.hypot(px - placed[0], py - placed[1])
    print("  host jumps 60 m 0.3 s after the first Teleport call (applied 0.6 s after it):")
    show(session, JOIN_LINES + ("host position jumped",))
    print(f"    calls at {[round(c['t'], 2) for c in calls]}; second aims {aim if aim is None else round(aim, 2)} m "
          f"from the placed host's side point; player ends {end_miss:.2f} m from it")
    return (
        len(failed) == 1 and "host jumped" in failed[0][1]
        and second is not None and second["t"] - calls[0]["t"] >= max(RETRY_DELAYS[0], SETTLE)
        and aim <= 0.1
        and len(joined) == 1 and "attempt=2/3" in joined[0][1]
        and end_miss <= TOLERANCE
        and session.g.updateErrors == 0
    )


if __name__ == "__main__":
    tests = {
        "J1 slow game: settle, one Teleport per attempt, measured at the teleport point, joins; old logic gives up": test_slow_game_joins,
        "J2 teleports never apply: 3 logged attempts, growing pauses, clean give-up; 'Teleport to host' same path": test_never_applies_then_manual,
        "J3 car, scene and position jump restart the 4 s settle; avatar spawns while in the car": test_settle_gate,
        "J4 a teleport applied after the 2.5 s wait counts, no second call": test_late_apply_counts,
        "J5 panel 'Join' row through settle, teleport, retry, done, vehicle, gave up; host has none": test_panel_rows,
        "J6 host still loading (silent, then a 60 m placement jump): no join at the stale point, joins the placed host": test_host_loads_late,
        "J7 host jumps during the attempt: landing at the old point fails the attempt, the next one joins the new spot": test_host_jumps_during_attempt,
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
