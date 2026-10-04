"""Robustness of init.lua against missing redscript, failed spawns and stale world state.

R1  redscript not compiled: no error per packet, one log line, join still works
R2  spawn deferred (entity system not ready): retried at most once per second
R3  spawn request lost (old Void remote.reds): requested again after 3 s
R4  entity registered but never appears: stale entry deleted, then spawned;
    GetTagged polled ~10 times per second while waiting, not every frame
R5  host time and weather reach the joiner while its avatar never spawns
R6  host goes silent: the joiner's clock is not rewound to the frozen host
    time; the first fresh packet after the host returns applies at once
R7  time across midnight: shortest signed step in the log, no correction
    inside the tolerance; state.reds sets absolute seconds (not HMS)
R8  weather the game refuses is retried every 5 s (not marked applied);
    an out-of-range weather index (corrupt packet) is ignored
R9  forced weather is released when the host goes silent, when the host's
    weather stays unknown, and when this player becomes host; re-applied
    when known host weather comes back
R10 fists and arm cyberware send weapon class None (Mantis Blades: Blade),
    and the avatar holsters for None/Other instead of drawing a pistol

Usage: python test_robustness.py path/to/init.lua
"""
import os
import re
import struct
import sys

from lupa.luajit21 import LuaRuntime

import coop_sim30 as sim
import test_two_players as harness
import test_live_bugs as live

FRAME_DT = 1.0 / 60.0
SEND_INTERVAL = 1.0 / 30.0
TYPE_STRIDE = 512
FLAG_HOST = 256
HOST_POS = (30.0, 12.0, 0.0)

# CET logs an error thrown by onUpdate and calls it again next frame
ERROR_TRAP = r"""
updateErrors = 0
updateErrorText = nil
rawUpdate = events.onUpdate
local update = rawUpdate
function findUpvalue(fn, wanted)
    local index = 1
    while true do
        local name, value = debug.getupvalue(fn, index)
        if name == nil then return nil end
        if name == wanted then return value end
        index = index + 1
    end
end
events.onUpdate = function(delta)
    local ok, err = pcall(update, delta)
    if not ok then
        updateErrors = updateErrors + 1
        updateErrorText = updateErrorText or tostring(err)
    end
end
"""

# redscript compiles all-or-nothing: one broken .reds = no CP2077Coop_* method
NO_REDSCRIPT = r"""
for name in pairs(player) do
    if string.sub(name, 1, 11) == "CP2077Coop_" then
        player[name] = nil
    end
end
"""


# spawn calls and GetTagged polls; the remote.reds IsPopulated guard as a flag
SPAWN_MOCK = r"""
spawnCalls = {}
taggedCalls = 0
populated = false
despawns = 0
function spawnEntity()
    spawnAt = { t = simTime + 0.3, x = 1.0, y = 2.5, z = 0 }
end
Game.GetDynamicEntitySystem = function()
    return { GetTagged = function()
        taggedCalls = taggedCalls + 1
        if npc then return { npc } end
        return {}
    end }
end
function player:CP2077Coop_DespawnRemote()
    populated = false
    despawns = despawns + 1
end
function player:CP2077Coop_SpawnRemoteTest()
    spawnCalls[#spawnCalls + 1] = simTime
    return spawnBehaviour(#spawnCalls)
end
"""

# R2: entity system not ready for the first 4 s
SPAWN_DEFERRED = r"""
function spawnBehaviour(call)
    if simTime < 4.0 then return false end
    spawnEntity()
    return true
end
"""

# R3: old remote.reds (returns nothing); the first request is silently dropped
SPAWN_LOST = r"""
function spawnBehaviour(call)
    if call > 1 then spawnEntity() end
    return nil
end
"""

# R4: the first entry never streams in; IsPopulated stays true until deleted
SPAWN_STUCK = r"""
function spawnBehaviour(call)
    if populated then return true end
    populated = true
    if despawns > 0 then spawnEntity() end
    return true
end
"""


# R5: the avatar entry exists but never streams in (no retry fixes it here)
SPAWN_NEVER = r"""
function spawnBehaviour(call)
    return true
end
"""

# joiner clock / weather; weatherResult decides what SetWeather returns
WORLD_MOCK = r"""
localMinutes = 600
timeCalls = {}
weatherCalls = {}
releaseCalls = {}
function weatherResult(index) return true end
function player:CP2077Coop_GetTimeOfDayMinutes() return localMinutes end
function player:CP2077Coop_SetTimeOfDayMinutes(minutes)
    timeCalls[#timeCalls + 1] = { simTime, minutes }
    localMinutes = minutes
end
function player:CP2077Coop_SetWeatherIndex(index)
    weatherCalls[#weatherCalls + 1] = { simTime, index }
    return weatherResult(index)
end
function player:CP2077Coop_ReleaseWeather()
    releaseCalls[#releaseCalls + 1] = simTime
    return true
end
"""


def calls(table):
    return [tuple(entry.values()) for entry in table.values()]


def float32(value):
    return struct.unpack("f", struct.pack("f", value))[0]


def make(role, position=(-900.0, 400.0), extra=""):
    for leftover in ("role.txt", "testpattern.txt"):
        if os.path.exists(leftover):
            os.remove(leftover)
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().simTime = 0.0
    lua.execute(sim.MOCK)
    lua.execute(harness.IMGUI_MOCK)
    lua.execute(harness.PLAYER_EXTRA.replace("PX", str(position[0])).replace("PY", str(position[1])))
    lua.execute(live.AI_TELEPORT_MOCK)
    lua.execute(extra)
    source = open(harness.SCRIPT, encoding="utf-8").read()
    if role == "joiner":
        source, count = re.subn(r"(?m)^local IS_HOST = true", "local IS_HOST = false", source)
        assert count == 1
    lua.eval("function(source) assert(load(source, '=init.lua'))() end")(source)
    lua.globals().events["onInit"]()
    lua.execute(ERROR_TRAP)
    return lua


def logs(lua):
    return list(lua.globals().logs.values())


class Feed:
    """Drives one init.lua at 60 fps with packets from a scripted peer (30 Hz)."""

    def __init__(self, lua, peer_is_host=True):
        self.lua = lua
        self.g = lua.globals()
        self.peer_is_host = peer_is_host
        self.t = 0.0
        self.next_send = 0.0
        self.sequence = 0
        self.slot = 0
        self.silent = False
        self.minutes = 10 * 60
        self.weather = 2
        self.flags = 0

    def payload(self):
        self.slot += 1
        if not self.peer_is_host:
            return self.flags
        if self.slot % 4 == 0:
            return 1 * TYPE_STRIDE + self.minutes // 3
        if self.slot % 4 == 2:
            return 2 * TYPE_STRIDE + self.weather + 1
        return self.flags + FLAG_HOST

    def send(self):
        payload = self.payload()
        self.sequence += 1
        net = self.g.net
        net.has = True
        net.seq = self.sequence
        net.x, net.y, net.z = HOST_POS
        net.fx, net.fy = 0.0, float32(1.0 + payload)

    def run(self, seconds, on_frame=None):
        end = self.t + seconds
        while self.t < end:
            self.g.simTime = self.t
            if not self.silent and self.t >= self.next_send:
                self.next_send += SEND_INTERVAL
                self.send()
            self.g.tickSpawn()
            self.g.events["onUpdate"](FRAME_DT)
            self.lua.eval("stepNpc")(FRAME_DT)
            self.lua.eval("stepAi")()
            if on_frame is not None:
                on_frame(self.t)
            self.t += FRAME_DT


# ------------------------------------------------------------------ R1

def test_no_redscript():
    ok = True
    for role in ("joiner", "host"):
        lua = make(role, extra=NO_REDSCRIPT)
        feed = Feed(lua, peer_is_host=(role == "joiner"))
        feed.run(10.0)
        lua.globals().events["onDraw"]()
        g = lua.globals()
        lines = logs(lua)
        reported = [l for l in lines if "redscript not compiled" in l]
        spawned = [l for l in lines if "remote spawn requested" in l]
        joined = [l for l in lines if "WORLD SYNC OK" in l]
        print(f"  {role}: onUpdate errors {g.updateErrors} ({g.updateErrorText}), "
              f"'not compiled' lines {len(reported)}, spawn requests {len(spawned)}, join {joined[:1]}")
        ok = ok and g.updateErrors == 0 and len(reported) == 1 and not spawned
        if role == "joiner":
            ok = ok and bool(joined)
    return ok


# ------------------------------------------------------------------ R2-R4

def spawn_run(behaviour, seconds):
    lua = make("joiner", extra=SPAWN_MOCK + behaviour)
    g = lua.globals()
    state = lua.eval("findUpvalue")(g.rawUpdate, "S")
    acquired = {}

    def on_frame(t):
        if "t" not in acquired and state.remoteHandle is not None:
            acquired["t"] = t

    Feed(lua).run(seconds, on_frame)
    calls = list(g.spawnCalls.values())
    lines = logs(lua)
    return {
        "g": g,
        "calls": calls,
        "acquired": acquired.get("t"),
        "deferred": [l for l in lines if "spawn deferred" in l],
        "again": [l for l in lines if "requesting again" in l],
        "errors": g.updateErrors,
        "error_text": g.updateErrorText,
    }


def describe(label, run):
    calls = ", ".join(f"{t:.2f}" for t in run["calls"][:8])
    acquired = "never" if run["acquired"] is None else f"{run['acquired']:.2f} s"
    print(f"  {label}: spawn calls {len(run['calls'])} at [{calls}], avatar acquired {acquired}, "
          f"deferred logs {len(run['deferred'])}, retry logs {len(run['again'])}, errors {run['errors']} {run['error_text'] or ''}")


def test_spawn_deferred():
    run = spawn_run(SPAWN_DEFERRED, 8.0)
    describe("not ready until 4 s", run)
    calls = run["calls"]
    early = [t for t in calls if t < 4.0]
    spaced = all(b - a >= 0.99 for a, b in zip(calls, calls[1:]))
    return (
        run["errors"] == 0
        and early and len(early) <= 5 and spaced
        and len(run["deferred"]) == 1
        and run["acquired"] is not None and run["acquired"] < 5.5
    )


def test_spawn_lost():
    run = spawn_run(SPAWN_LOST, 10.0)
    describe("first request lost", run)
    calls = run["calls"]
    return (
        run["errors"] == 0
        and len(calls) == 2 and 2.9 <= calls[1] - calls[0] <= 3.6
        and len(run["again"]) == 1 and not run["deferred"]
        and run["acquired"] is not None
    )


def test_spawn_stuck():
    run = spawn_run(SPAWN_STUCK, 16.0)
    describe("first entry never appears", run)
    g = run["g"]
    calls = run["calls"]
    waited = (run["acquired"] or 16.0) - calls[0]
    poll_rate = g.taggedCalls / waited
    print(f"  stale entries deleted {g.despawns}; GetTagged {g.taggedCalls}x in {waited:.1f} s = {poll_rate:.1f}/s")
    return (
        run["errors"] == 0
        and g.despawns == 1 and len(calls) == 3
        and run["acquired"] is not None
        and poll_rate <= 11.0
    )


# ------------------------------------------------------------------ R5

def test_world_state_without_avatar():
    lua = make("joiner", extra=SPAWN_MOCK + SPAWN_NEVER + WORLD_MOCK)
    g = lua.globals()
    state = lua.eval("findUpvalue")(g.rawUpdate, "S")
    feed = Feed(lua)
    feed.minutes = 22 * 60 + 15
    feed.weather = 5
    feed.run(4.0)
    time_calls = calls(g.timeCalls)
    weather_calls = calls(g.weatherCalls)
    print(f"  avatar handle {state.remoteHandle}; time sets {[(round(t, 2), m) for t, m in time_calls]}, "
          f"weather sets {[(round(t, 2), i) for t, i in weather_calls]}, errors {g.updateErrors}")
    return (
        g.updateErrors == 0
        and state.remoteHandle is None
        and [m for _, m in time_calls] == [feed.minutes] and time_calls[0][0] < 1.0
        and [i for _, i in weather_calls] == [feed.weather] and weather_calls[0][0] < 1.0
    )


# ------------------------------------------------------------------ R6

GAME_MINUTES_PER_SECOND = 8.0 / 60.0  # default time scale: 1 real s = 8 game s


def test_stale_host_time():
    whole_minutes = "function player:CP2077Coop_GetTimeOfDayMinutes() return math.floor(localMinutes) % 1440 end"
    lua = make("joiner", extra=WORLD_MOCK + whole_minutes)
    g = lua.globals()
    feed = Feed(lua)
    host = {"start": 630.0}  # host 10:30, joiner 10:00

    def tick(t):
        g.localMinutes = g.localMinutes + FRAME_DT * GAME_MINUTES_PER_SECOND
        feed.minutes = int(host["start"] + t * GAME_MINUTES_PER_SECOND) % 1440

    feed.minutes = int(host["start"])
    feed.run(5.0, tick)
    feed.silent = True       # host quits / crashes; the DLL keeps its last packet
    feed.run(70.0, tick)
    host["start"] += 360.0   # back after sleeping 6 h
    feed.silent = False
    feed.run(3.0, tick)
    sets = [(round(t, 2), m) for t, m in calls(g.timeCalls)]
    during_silence = [s for s in sets if 5.5 <= s[0] < 75.0]
    print(f"  time sets {sets}; while the host was silent: {during_silence}; errors {g.updateErrors}")
    return (
        g.updateErrors == 0
        and len(sets) == 2
        and sets[0][0] < 1.0
        and not during_silence
        and 75.0 <= sets[1][0] < 75.5
    )


# ------------------------------------------------------------------ R7

STATE_REDS = os.path.join(
    os.path.dirname(os.path.abspath(harness.SCRIPT)),
    "..", "..", "..", "..", "..", "..", "r6", "scripts", "CP2077Coop", "state.reds",
)


def midnight_case(joiner_minutes, host_minutes):
    lua = make("joiner", extra=WORLD_MOCK)
    g = lua.globals()
    g.localMinutes = joiner_minutes
    feed = Feed(lua)
    feed.minutes = host_minutes
    feed.run(1.0)
    synced = [l for l in logs(lua) if "time synced" in l]
    return [m for _, m in calls(g.timeCalls)], synced


def test_time_across_midnight():
    ok = True
    for joiner, host, expect_set, expect_log in (
        (23 * 60 + 57, 6, True, "(+9 min)"),        # 23:57 -> 00:06
        (3, 23 * 60 + 54, True, "(-9 min)"),        # 00:03 -> 23:54
        (23 * 60 + 58, 0, False, None),             # 23:58 vs 00:00: inside tolerance
        (22 * 60, 4 * 60, True, "(+360 min)"),      # host slept 22:00 -> 04:00
    ):
        sets, synced = midnight_case(joiner, host)
        print(f"  joiner {joiner // 60:02d}:{joiner % 60:02d} host {host // 60:02d}:{host % 60:02d}: sets {sets} {synced[:1]}")
        if expect_set:
            ok = ok and sets == [host] and len(synced) == 1 and synced[0].endswith(expect_log)
        else:
            ok = ok and not sets
    source = open(os.path.normpath(STATE_REDS), encoding="utf-8").read()
    start = source.index("func CP2077Coop_SetTimeOfDayMinutes")
    body = source[start:source.index("\n}", start)]
    absolute = "SetGameTimeBySeconds" in body and "SetGameTimeByHMS" not in body and "delta -= 1440" in body
    print(f"  state.reds sets the clock by absolute seconds with a signed step: {absolute}")
    return ok and absolute


# ------------------------------------------------------------------ R8 / R9

def weather_feed(refuse_until=0.0):
    extra = WORLD_MOCK + f"function weatherResult(index) return simTime >= {refuse_until} end"
    lua = make("joiner", extra=extra)
    feed = Feed(lua)
    feed.weather = 4
    return lua, lua.globals(), feed


def weather_log(lua):
    return [l.split("] ", 1)[1] for l in logs(lua) if "weather" in l]


def test_weather_refused_and_corrupt():
    lua, g, feed = weather_feed(refuse_until=7.0)
    feed.run(12.0)
    sets = [(round(t, 2), i) for t, i in calls(g.weatherCalls)]
    log = weather_log(lua)
    print(f"  refused until 7 s: SetWeather calls {sets}; log {log}")
    gaps = [b[0] - a[0] for a, b in zip(sets, sets[1:])]
    refused_ok = (
        len(sets) == 3 and all(4.9 <= gap <= 5.3 for gap in gaps)
        and sum("refused" in l for l in log) == 1 and sum("synced" in l for l in log) == 1
    )

    # a weather packet with index 436 (value 437) is a damaged packet
    lua, g, feed = weather_feed()
    feed.run(1.0)
    feed.weather = 436
    feed.run(1.0)
    sets = [i for _, i in calls(g.weatherCalls)]
    sync = lua.eval("findUpvalue")(g.rawUpdate, "Sync")
    print(f"  corrupt index 436: SetWeather calls {sets}, remote weather still {sync.remoteWeather}")
    corrupt_ok = sets == [4] and sync.remoteWeather == 4
    return g.updateErrors == 0 and refused_ok and corrupt_ok


def test_weather_released():
    ok = True

    # host goes silent for 20 s, then comes back
    lua, g, feed = weather_feed()
    feed.run(3.0)
    feed.silent = True
    feed.run(20.0)
    released_at = [round(t, 2) for t in g.releaseCalls.values()]
    feed.silent = False
    feed.run(2.0)
    sets = [(round(t, 2), i) for t, i in calls(g.weatherCalls)]
    print(f"  host silent 3-23 s: released at {released_at}, SetWeather calls {sets}")
    ok = ok and len(released_at) == 1 and 17.5 <= released_at[0] <= 18.5 and len(sets) == 2 and sets[1][0] >= 23.0

    # host weather unknown (quest weather outside the list) for 20 s
    lua, g, feed = weather_feed()
    feed.run(3.0)
    feed.weather = -1
    feed.run(20.0)
    released_at = [round(t, 2) for t in g.releaseCalls.values()]
    print(f"  host weather unknown from 3 s: released at {released_at}")
    ok = ok and len(released_at) == 1 and 17.5 <= released_at[0] <= 18.5

    # this player switches to HOST in the panel
    lua, g, feed = weather_feed()
    feed.run(3.0)
    g.clickButton = "Switch to HOST"
    g.events["onDraw"]()
    g.clickButton = None
    feed.run(1.0)
    released_at = [round(t, 2) for t in g.releaseCalls.values()]
    print(f"  switched to host at 3 s: released at {released_at}; errors {g.updateErrors}")
    ok = ok and len(released_at) == 1 and released_at[0] < 3.1 and g.updateErrors == 0
    if os.path.exists("role.txt"):
        os.remove("role.txt")
    return ok


# ------------------------------------------------------------------ R10

def weapon_classes(source):
    """Item type -> weapon class from CP2077Coop_ClassifyWeapon's switch."""
    start = source.index("func CP2077Coop_ClassifyWeapon")
    body = source[start:source.index("\n}", start)]
    classes, pending = {}, []
    for line in body.splitlines():
        case = re.search(r"case gamedataItemType\.(\w+):", line)
        if case:
            pending.append(case.group(1))
        result = re.search(r"return CP2077CoopWeaponClass\.(\w+);", line)
        if result:
            for item_type in pending:
                classes[item_type] = result.group(1)
            pending = []
    return classes


def test_fists_and_cyberware():
    source = open(os.path.normpath(STATE_REDS), encoding="utf-8").read()
    classes = weapon_classes(source)
    wanted = {
        "Wea_Fists": "None", "Cyb_StrongArms": "None", "Cyb_NanoWires": "None", "Cyb_Launcher": "None",
        "Cyb_MantisBlades": "Blade", "Wea_Katana": "Blade", "Wea_Handgun": "Pistol", "Wea_AssaultRifle": "Rifle",
    }
    got = {item: classes.get(item, "Other (default)") for item in wanted}
    print(f"  classified: {got}")
    start = source.index("func CP2077Coop_ApplyRemoteWeapon")
    body = source[start:source.index("\n}", start)]
    holster = re.search(r"if !drawn \|\| !hasProp \{\s*let holster = new AIUnequipCommand", body) is not None
    no_prop = "CP2077CoopWeaponClass.None" in body and "CP2077CoopWeaponClass.Other" in body
    print(f"  ApplyRemoteWeapon holsters for None/Other: {holster and no_prop}")

    # Lua side: fists drawn (class 0 + drawn bit) reaches ApplyRemoteWeapon(0, true)
    lua = make("joiner", extra="weaponCalls = {}\nfunction player:CP2077Coop_ApplyRemoteWeapon(c, d) weaponCalls[#weaponCalls + 1] = { c, d } end")
    feed = Feed(lua)
    feed.run(2.0)
    feed.flags = 2  # weapon drawn, class None (fists)
    feed.run(1.0)
    weapon = [tuple(v.values()) for v in lua.globals().weaponCalls.values()]
    print(f"  avatar weapon calls: {weapon}")
    return got == wanted and holster and no_prop and weapon[-1:] == [(0, True)]


if __name__ == "__main__":
    tests = {
        "R1 redscript not compiled: no error per packet, one log line, join still works": test_no_redscript,
        "R2 spawn deferred while the entity system is not ready, at most 1 call/s": test_spawn_deferred,
        "R3 lost spawn request is requested again after 3 s": test_spawn_lost,
        "R4 stuck entity entry deleted and respawned; GetTagged polled ~10/s": test_spawn_stuck,
        "R5 host time and weather applied while the avatar is missing": test_world_state_without_avatar,
        "R6 silent host: joiner clock not rewound, returning host applied at once": test_stale_host_time,
        "R7 time across midnight: shortest signed step, absolute seconds in state.reds": test_time_across_midnight,
        "R8 refused weather retried every 5 s; corrupt weather index ignored": test_weather_refused_and_corrupt,
        "R9 forced weather released (host silent / unknown / role host), re-applied later": test_weather_released,
        "R10 fists / arm cyberware: no weapon class, avatar holsters instead of a pistol": test_fists_and_cyberware,
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
