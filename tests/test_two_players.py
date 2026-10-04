"""Two init.lua instances (host + joiner) connected through a simulated relay.

Latency model: joiner (Los Angeles) <-> Warsaw relay ~93 ms one way,
host (Russia) <-> Warsaw ~25 ms one way, plus jitter.
"""
import atexit
import math
import os
import random
import re
import shutil
import sys
import tempfile

from lupa.luajit21 import LuaRuntime

import coop_sim30 as sim

SCRIPT = os.path.abspath(sys.argv[1])


def private_workdir():
    """role.txt / monitor_status.txt are relative to the working directory, so never share one.

    run_all.ps1 passes a fresh folder per test in COOP_TEST_WORKDIR; a standalone run
    gets its own temp folder, removed again at exit.
    """
    workdir = os.environ.get("COOP_TEST_WORKDIR")
    if workdir:
        os.makedirs(workdir, exist_ok=True)
        return workdir
    workdir = tempfile.mkdtemp(prefix="cooptest_")

    def remove():
        os.chdir(tempfile.gettempdir())  # Windows cannot delete the current directory
        try:
            shutil.rmtree(workdir)
        except OSError as error:
            print(f"note: could not remove {workdir}: {error}", file=sys.stderr)

    atexit.register(remove)
    return workdir


os.chdir(private_workdir())
FRAME_DT = 1 / 60
LEG_MS = {"host": 25.0, "joiner": 93.0}
JITTER_MS = 15.0

IMGUI_MOCK = r"""
drawCalls = 0
ImGuiCond = { FirstUseEver = 4, Once = 2 }
ImGuiWindowFlags = { AlwaysAutoResize = 64 }
clickButton = nil
ImGui = {
    SetNextWindowPos = function() end,
    SetNextWindowSize = function() end,
    Begin = function() return true end,
    End = function() end,
    Text = function(s) assert(type(s) == "string", "Text needs string") end,
    TextColored = function(r, g, b, a, s) assert(type(s) == "string", "TextColored needs string: " .. tostring(s)) drawCalls = drawCalls + 1 end,
    SameLine = function() end,
    Separator = function() end,
    Button = function(label) return label == clickButton end,
}
hotkeys = {}
function registerHotkey(id, label, fn) hotkeys[id] = fn end
"""

PLAYER_EXTRA = r"""
playerPos = { x = PX, y = PY, z = 0 }
teleportWorks = true
function player:GetWorldPosition() return { x = playerPos.x, y = playerPos.y, z = playerPos.z, w = 1 } end
function player:GetWorldOrientation() return { _type = "Quaternion" } end
playerYaw = nil
Game.GetTeleportationFacility = function()
    return { Teleport = function(_, _, pos, rotation)
        -- like the game: parameter 3 must be EulerAngles (a Quaternion fails)
        if type(rotation) ~= "table" or rotation._type ~= "EulerAngles" then
            error("Function 'Teleport' parameter 3 must be EulerAngles.")
        end
        if teleportWorks then
            playerPos.x, playerPos.y, playerPos.z = pos.x, pos.y, pos.z
            playerYaw = rotation.yaw
        end
    end }
end
localFlags = 0
function player:CP2077Coop_GetStateFlags() return localFlags end
function player:CP2077Coop_GetTimeOfDayMinutes() return 600 end
function player:CP2077Coop_GetWeatherIndex() return 1 end
function player:CP2077Coop_SetTimeOfDayMinutes(m) end
function player:CP2077Coop_SetWeatherIndex(i) end
function player:CP2077Coop_ApplyRemoteStance(c) end
function player:CP2077Coop_ApplyRemoteWeapon(cls, drawn) end
outbox = {}
Game.CP2077Coop_PushPlayerState = function(x, y, z, w, fx, fy) outbox[#outbox + 1] = { x, y, z, fx, fy } end
"""


def make_instance(role, position, teleport_works=True):
    if os.path.exists("role.txt"):
        os.remove("role.txt")
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().simTime = 0.0
    lua.execute(sim.MOCK)
    lua.execute(IMGUI_MOCK)
    lua.execute(PLAYER_EXTRA.replace("PX", str(position[0])).replace("PY", str(position[1])))
    lua.globals().teleportWorks = teleport_works
    source = open(SCRIPT, encoding="utf-8").read()
    if role == "joiner":
        source, count = re.subn(r"(?m)^local IS_HOST = true", "local IS_HOST = false", source)
        assert count == 1
    lua.execute(source)
    lua.globals().events["onInit"]()
    return lua


def run_session(seconds, joiner_role="joiner", joiner_teleport_works=True, seed=3):
    rng = random.Random(seed)
    host = make_instance("host", (100.0, 50.0))
    joiner = make_instance(joiner_role, (-900.0, 400.0), teleport_works=joiner_teleport_works)
    instances = {"host": host, "joiner": joiner}
    peers = {"host": "joiner", "joiner": "host"}
    in_flight = []  # (arrive_time, to, seq, packet)
    seq = {"host": 0, "joiner": 0}
    t = 0.0
    while t < seconds:
        for name, lua in instances.items():
            g = lua.globals()
            g.simTime = t
            # deliver packets due for this instance (DLL keeps latest only)
            due = sorted(p for p in in_flight if p[1] == name and p[0] <= t)
            in_flight[:] = [p for p in in_flight if not (p[1] == name and p[0] <= t)]
            for _, _, pseq, packet in due:
                g.net.has = True
                g.net.seq = pseq
                g.net.x, g.net.y, g.net.z, g.net.fx, g.net.fy = packet
            g.tickSpawn()
            g.events["onUpdate"](FRAME_DT)
            lua.eval("stepNpc")(FRAME_DT)
            if rng.random() < 0.05:
                g.events["onDraw"]()
            # collect what this instance sent
            outbox = g.outbox
            for index in range(1, len(outbox) + 1):
                p = outbox[index]
                seq[name] += 1
                delay = (LEG_MS[name] + LEG_MS[peers[name]] + rng.random() * JITTER_MS) / 1000.0
                in_flight.append((t + delay, peers[name], seq[name], (p[1], p[2], p[3], p[4], p[5])))
            lua.execute("outbox = {}")
        t += FRAME_DT
    return host, joiner


def logs(lua):
    return list(lua.globals().logs.values())


def last_stats(lua):
    lines = [l for l in logs(lua) if "[STATS]" in l]
    return lines[-1] if lines else ""


def stat(line, key):
    match = re.search(rf"{key}=(\S+)", line)
    return match.group(1) if match else None


def test_normal_session():
    host, joiner = run_session(20.0)
    expected_rtt = 2 * (LEG_MS["host"] + LEG_MS["joiner"])
    results = {}
    for name, lua in (("host", host), ("joiner", joiner)):
        line = last_stats(lua)
        print(f"{name:6} {line}")
        rtt = float(stat(line, "rtt_ms"))
        results[name] = (
            stat(line, "state") == "OK"
            and stat(line, "conflict") == "false"
            and stat(line, "peer_old") == "false"
            and expected_rtt <= rtt <= expected_rtt + 140
            and float(stat(line, "pps_in")) >= 20
        )
    joined = [l for l in logs(joiner) if "WORLD SYNC OK" in l]
    draws = joiner.globals().drawCalls
    print(f"expected RTT floor {expected_rtt:.0f} ms; joiner world sync: {joined[:1]}; panel rows drawn: {draws}")
    return all(results.values()) and bool(joined) and draws > 0


def test_role_conflict_detected():
    host, other_host = run_session(6.0, joiner_role="host")
    line = last_stats(other_host)
    print("both host ->", stat(line, "conflict"))
    return stat(line, "conflict") == "true"


def test_join_gives_up():
    # 4 s settle, then 3 attempts of 5 s with 2 s and 4 s pauses: done by ~25 s
    host, joiner = run_session(30.0, joiner_teleport_works=False)
    failed = [l for l in logs(joiner) if "WORLD SYNC FAILED" in l]
    gave_up = [l for l in logs(joiner) if "GAVE UP" in l]
    print(f"failed attempts logged: {len(failed)}, gave up: {bool(gave_up)}")
    return len(failed) == 3 and len(gave_up) == 1


def test_connection_lost_event():
    host, joiner = run_session(8.0)
    g = joiner.globals()
    t = 8.0
    for _ in range(int(7 / FRAME_DT)):  # host goes silent for 7 s
        g.simTime = t
        g.events["onUpdate"](FRAME_DT)
        t += FRAME_DT
    events = [l for l in logs(joiner) if "EVENT connection" in l]
    print("connection events:", events)
    return any("OK -> STALE" in e for e in events) and any("STALE -> LOST" in e for e in events)


def test_panel_buttons():
    host, joiner = run_session(3.0)
    g = joiner.globals()
    g.clickButton = "Switch to HOST"
    g.events["onDraw"]()
    g.clickButton = None
    g.events["onUpdate"](FRAME_DT)
    switched = any("role changed to HOST" in l for l in logs(joiner))
    g.hotkeys["cp2077coop_toggle_panel"]()
    before = g.drawCalls
    g.events["onDraw"]()
    hidden = g.drawCalls == before
    print(f"role switch logged: {switched}, hotkey hides panel: {hidden}")
    return switched and hidden


if __name__ == "__main__":
    tests = {
        "normal session (RTT, rates, join, panel)": test_normal_session,
        "role conflict detected": test_role_conflict_detected,
        "join teleport gives up after 3 attempts": test_join_gives_up,
        "connection STALE/LOST events": test_connection_lost_event,
        "panel buttons and hotkey": test_panel_buttons,
    }
    results = {}
    for name, fn in tests.items():
        try:
            results[name] = fn()
        except Exception as error:  # report and keep going
            print(f"{name}: EXCEPTION {error}")
            results[name] = False
    for name, passed in results.items():
        print(f"{'PASS' if passed else 'FAIL'}  {name}")
    sys.exit(0 if all(results.values()) else 1)
