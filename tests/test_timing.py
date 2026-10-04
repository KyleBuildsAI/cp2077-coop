"""Packet timing and ordering: two real init.lua instances, each at its own frame rate.

The DLL is modelled as in the disassembly: PushPlayerState only writes the local
slot and bumps the local sequence; a network thread sends the newest slot every
~2 ms, so several pushes in one frame go out as ONE packet (a sequence gap).
The receiving DLL keeps the packet that arrived last. Link: 115 ms + 0..20 ms,
1% loss (like the live relay test).

T1  remote speed is right at any sender fps (25/40/45/50/60, jittered, hitches)
T2  sequence numbers used by combat packets are not counted as movement time
T3  a stale DLL packet is not replayed after a local reload
T4  peer restart after a short session is detected; a held late packet is not
T5  torn read of the DLL slot (packet lands mid-read) is skipped
T6  joiner's test bot is anchored after the join teleport; its vehicle phase
    does not block "Teleport to host"
T7  joiner spawns the avatar after the join teleport, next to the host
T8  no global variable reads/writes (a local declared below its user) in the
    code a 10 s session runs, load and onInit included
T9  the same for every function in init.lua, from the bytecode (also code that
    only runs on a role switch, a reset or shutdown)

Usage: python test_timing.py path/to/init.lua
"""
import math
import os
import random
import re
import sys

from lupa.luajit21 import LuaRuntime

import coop_sim30 as sim
import test_two_players as harness
import test_live_bugs as live

SEND_INTERVAL = 1.0 / 30.0
FAST_SPEED = 8.0
LATENCY = 0.115
JITTER = 0.020
LOSS = 0.01
COMBAT_MARKER_Y = 9999.0

FIND_UPVALUE = r"""
function findUpvalue(fn, wanted)
    local index = 1
    while true do
        local name, value = debug.getupvalue(fn, index)
        if name == nil then return nil end
        if name == wanted then return value end
        index = index + 1
    end
end
"""

LOAD_MOCK = r"""
preGame = false
Game.GetSystemRequestsHandler = function()
    return { IsPreGame = function() return preGame end }
end
"""

# where the local player stood when the avatar spawn was requested
SPAWN_WRAP = r"""
spawnFrom = false
local spawn = player.CP2077Coop_SpawnRemoteTest
function player:CP2077Coop_SpawnRemoteTest(...)
    spawnFrom = { x = playerPos.x, y = playerPos.y }
    return spawn(self, ...)
end
"""

# global reads/writes made by init.lua itself (mocks use globals on purpose)
GLOBAL_TRAP = r"""
globalHits = {}
local function fromMod()
    local info = debug.getinfo(3, "S")
    return info ~= nil and info.source == "=init.lua"
end
setmetatable(_G, {
    __index = function(_, name)
        if fromMod() then globalHits["read " .. tostring(name)] = true end
        return nil
    end,
    __newindex = function(t, name, value)
        if fromMod() then globalHits["write " .. tostring(name)] = true end
        rawset(t, name, value)
    end,
})
"""


def upvalue(lua, name):
    return lua.eval("findUpvalue")(lua.globals().events["onUpdate"], name)


def make_peer(role, position, trap=False):
    # role.txt / testpattern.txt in the working directory would override the test setup
    for leftover in ("role.txt", "testpattern.txt"):
        if os.path.exists(leftover):
            os.remove(leftover)
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().simTime = 0.0
    lua.execute(sim.MOCK)
    lua.execute(harness.IMGUI_MOCK)
    lua.execute(harness.PLAYER_EXTRA.replace("PX", str(position[0])).replace("PY", str(position[1])))
    lua.execute(live.AI_TELEPORT_MOCK)
    lua.execute(FIND_UPVALUE + LOAD_MOCK + SPAWN_WRAP)
    source = open(harness.SCRIPT, encoding="utf-8").read()
    if role == "joiner":
        source, count = re.subn(r"(?m)^local IS_HOST = true", "local IS_HOST = false", source)
        assert count == 1
    if trap:
        # before the load, so the top level and onInit are watched too; mocks are
        # already defined and the trap only reports accesses made from init.lua
        lua.execute(GLOBAL_TRAP)
    lua.eval("function(source) assert(load(source, '=init.lua'))() end")(source)
    lua.globals().events["onInit"]()
    return lua


class Peer:
    """One game instance: its own frame clock, local DLL slot and network thread."""

    def __init__(self, name, lua, fps, rng, frame_jitter=0.0, hitches=(), path=None):
        self.name = name
        self.lua = lua
        self.g = lua.globals()
        self.dt = 1.0 / fps
        self.rng = rng
        self.frame_jitter = frame_jitter
        self.hitches = dict(hitches)  # frame start time -> frame length
        self.path = path
        self.next_frame = 0.0
        self.last_frame = 0.0
        self.local_sequence = 0
        self.inbox = []  # (arrive, order, sequence, packet)
        self.silent = False
        self.combat_rate = 0.0
        self.sent = []  # (t, sequence, packet, arrive or None) put on the wire

    def frame_length(self, t):
        for start, length in list(self.hitches.items()):
            if t >= start:
                del self.hitches[start]
                return length
        if self.frame_jitter:
            return self.dt * (1.0 + self.rng.uniform(-self.frame_jitter, self.frame_jitter))
        return self.dt

    def deliver(self, t):
        due = sorted(p for p in self.inbox if p[0] <= t)
        self.inbox = [p for p in self.inbox if p[0] > t]
        net = self.g.net
        for _, _, sequence, packet in due:  # last arrival wins (DLL slot)
            net.has = True
            net.seq = sequence
            net.x, net.y, net.z, net.fx, net.fy = packet

    def frame(self, t, peer):
        delta = t - self.last_frame if t > 0 else self.dt
        self.last_frame = t
        self.g.simTime = t
        if self.path is not None:
            x, y = self.path(t)
            self.g.playerPos.x, self.g.playerPos.y = x, y
        self.deliver(t)
        self.g.tickSpawn()
        self.g.events["onUpdate"](delta)
        self.lua.eval("stepNpc")(delta)
        self.lua.eval("stepAi")()
        outbox = self.g.outbox
        pushes = [tuple(outbox[i][k] for k in range(1, 6)) for i in range(1, len(outbox) + 1)]
        self.lua.execute("outbox = {}")
        if self.silent:
            return
        # combat.reds pushes a hit through the same slot at some moment of the
        # frame. Within 2 ms of the mod's pushes (one network-thread wake) the
        # later push overwrites the earlier one: a sequence number goes unseen.
        bursts = []
        if self.combat_rate and self.rng.random() < self.combat_rate * delta:
            hit = (5.0, 5.0, 0.0, 42.0, COMBAT_MARKER_Y)
            offset = self.rng.uniform(-0.5, 0.5) * delta
            if offset < -0.002:
                bursts.append((t + offset, [hit]))
            elif offset < 0.0:
                pushes.insert(0, hit)
            elif offset < 0.002:
                pushes.append(hit)
            else:
                bursts.append((t, pushes))
                pushes = []
                bursts.append((t + offset, [hit]))
        if pushes:
            bursts.append((t, pushes))
        for at, burst in sorted(bursts, key=lambda b: b[0]):
            self.send_burst(at, burst, peer)

    def send_burst(self, t, pushes, peer):
        if not pushes:
            return
        self.local_sequence += len(pushes)
        # the network thread wakes every ~2 ms: it sends the newest push of
        # the burst; rarely it wakes mid-burst and sends an earlier one too
        on_wire = [(self.local_sequence, pushes[-1])]
        if len(pushes) > 1 and self.rng.random() < 0.05:
            index = self.rng.randrange(len(pushes) - 1)
            on_wire.insert(0, (self.local_sequence - (len(pushes) - 1 - index), pushes[index]))
        for order, (sequence, packet) in enumerate(on_wire):
            if self.rng.random() < LOSS:
                self.sent.append((t, sequence, packet, None))
                continue
            arrive = t + LATENCY + self.rng.random() * JITTER
            self.sent.append((t, sequence, packet, arrive))
            peer.inbox.append((arrive, order, sequence, packet))


def run_pair(a, b, seconds, on_frame=None):
    """Advance both peers frame by frame in time order."""
    while True:
        peer, other = (a, b) if a.next_frame <= b.next_frame else (b, a)
        t = peer.next_frame
        if t >= seconds:
            break
        peer.frame(t, other)
        if on_frame is not None:
            on_frame(peer, t)
        peer.next_frame = t + peer.frame_length(t)


def logs(lua):
    return list(lua.globals().logs.values())


# ------------------------------------------------------------------ T1

SPEED_PHASES = [(0.0, 2.0, 0.0), (2.0, 8.0, 4.5), (8.0, 16.0, 7.0), (16.0, 18.0, 0.0)]
STEADY = {"Run": (3.5, 7.8, 4.5), "Sprint": (9.5, 15.8, 7.0)}  # receiver time windows


def straight(t, phases=SPEED_PHASES, start=(100.0, 50.0)):
    y = start[1]
    for begin, end, speed in phases:
        if t > begin:
            y += speed * (min(t, end) - begin)
    return start[0], y


def speed_run(sender_fps, frame_jitter=0.0, hitches=(), combat_rate=0.0, seed=11):
    rng = random.Random(seed)
    sender = Peer("host", make_peer("host", (100.0, 50.0)), sender_fps, rng,
                  frame_jitter=frame_jitter, hitches=hitches, path=straight)
    sender.combat_rate = combat_rate
    receiver = Peer("joiner", make_peer("joiner", (110.0, 50.0)), 60.0, rng)
    state = upvalue(receiver.lua, "S")
    samples = {"Run": [], "Sprint": []}
    flips = {"Run": 0, "Sprint": 0}
    teleports_at = {}
    last = {"sequence": -1, "type": None}

    def on_frame(peer, t):
        if peer is not receiver:
            return
        for gait, (begin, end, _) in STEADY.items():
            if begin <= t <= end:
                teleports_at.setdefault(gait, [receiver.g.stats.teleports, None])[1] = receiver.g.stats.teleports
        if state.lastRemoteSequence == last["sequence"]:
            return
        last["sequence"] = state.lastRemoteSequence
        for gait, (begin, end, _) in STEADY.items():
            if begin <= t <= end and state.remoteMoving:
                samples[gait].append(state.remoteSpeed)
                if last["type"] is not None and state.movementType != last["type"]:
                    flips[gait] += 1
        last["type"] = state.movementType

    run_pair(sender, receiver, 17.0, on_frame)
    result = {}
    for gait, (_, _, truth) in STEADY.items():
        values = samples[gait]
        errors = [abs(v - truth) / truth for v in values]
        teleports = teleports_at.get(gait, [0, 0])
        result[gait] = {
            "n": len(values),
            "min": min(values) if values else float("nan"),
            "max": max(values) if values else float("nan"),
            "worst_pct": 100.0 * max(errors) if errors else float("nan"),
            "exact_pct": 100.0 * sum(1 for e in errors if e <= 0.02) / len(errors) if errors else 0.0,
            "fast": sum(1 for v in values if v >= FAST_SPEED),
            "flips": flips[gait],
            "teleports": teleports[1] - teleports[0],
        }
    gaps = sum(1 for first, second in zip(sender.sent, sender.sent[1:]) if second[1] > first[1] + 1)
    result["wire_gaps"] = gaps
    result["wire_packets"] = len(sender.sent)
    return result


def describe(label, result):
    run, sprint = result["Run"], result["Sprint"]
    print(f"  {label:28} run 4.5: {run['min']:.2f}-{run['max']:.2f} m/s (worst {run['worst_pct']:.1f}%) "
          f"sprint 7.0: {sprint['min']:.2f}-{sprint['max']:.2f} (worst {sprint['worst_pct']:.1f}%), "
          f">=8 m/s {run['fast'] + sprint['fast']}, gait flips {run['flips'] + sprint['flips']}, "
          f"teleports {run['teleports'] + sprint['teleports']}, wire gaps {result['wire_gaps']}/{result['wire_packets']}")


def speed_ok(result, tolerance_pct=2.0):
    return all(
        result[g]["n"] > 50 and result[g]["worst_pct"] <= tolerance_pct and result[g]["fast"] == 0 and result[g]["flips"] == 0
        for g in ("Run", "Sprint")
    )


def test_speed_any_sender_fps():
    ok = True
    for fps in (25, 40, 45, 50, 60, 144, 20):
        result = speed_run(fps)
        describe(f"sender {fps} fps", result)
        ok = ok and speed_ok(result)
    result = speed_run(60, frame_jitter=0.15)
    describe("sender 60 fps +-15% jitter", result)
    ok = ok and speed_ok(result)
    result = speed_run(45, frame_jitter=0.25)
    describe("sender 45 fps +-25% jitter", result)
    ok = ok and speed_ok(result)
    # 250 ms and 900 ms hitches mid-sprint: the gap carries the time
    result = speed_run(60, hitches=((11.0, 0.25), (13.0, 0.90)))
    describe("sender 60 fps, 0.25+0.9 s hitch", result)
    ok = ok and speed_ok(result)
    return ok


# ------------------------------------------------------------------ T2

def test_combat_sequences_not_movement_time():
    # ~6 hits/s while running and sprinting. A hit that reaches the receiver
    # must not count as movement time; a hit overwritten in the sender's DLL
    # slot is invisible (its number reads as one extra tick: speed low, never
    # a false dash).
    ok = True
    for fps, seed in ((60, 4), (45, 6)):
        result = speed_run(fps, combat_rate=6.0, seed=seed)
        describe(f"sender {fps} fps + 6 hits/s", result)
        print(f"    packets within 2%: run {result['Run']['exact_pct']:.0f}%, sprint {result['Sprint']['exact_pct']:.0f}%; "
              f"highest {max(result['Run']['max'], result['Sprint']['max']):.2f} m/s")
        ok = ok and all(
            result[g]["fast"] == 0 and result[g]["exact_pct"] >= 85.0 and result[g]["max"] <= STEADY[g][2] * 1.02
            for g in ("Run", "Sprint")
        )
    return ok


# ------------------------------------------------------------------ T3

def reload_scenario(host_alive):
    rng = random.Random(5)
    host = Peer("host", make_peer("host", (100.0, 50.0)), 60.0, rng)
    joiner = Peer("joiner", make_peer("joiner", (-900.0, 400.0)), 60.0, rng)
    run_pair(host, joiner, 6.0)
    host.silent = not host_alive
    run_pair(host, joiner, 12.0)
    # joiner loads a save somewhere else: sync OFF for 1 s, then ON
    mark = len(logs(joiner.lua))
    joiner.g.playerPos.x, joiner.g.playerPos.y = -2000.0, 3000.0
    joiner.g.preGame = True
    run_pair(host, joiner, 13.0)
    joiner.g.preGame = False
    run_pair(host, joiner, 18.0)
    return [l for l in logs(joiner.lua)[mark:]], joiner


def test_stale_packet_not_replayed():
    after, joiner = reload_scenario(host_alive=False)
    replay = [l for l in after if "WORLD SYNC ->" in l or "remote spawn requested" in l or "-> OK" in l]
    position = (joiner.g.playerPos.x, joiner.g.playerPos.y)
    print(f"  host gone, joiner reloads at (-2000, 3000): replayed={replay[:2]} joiner now at {position}")
    gone_ok = not replay and position == (-2000.0, 3000.0)
    after, joiner = reload_scenario(host_alive=True)
    rejoined = [l for l in after if "WORLD SYNC OK" in l]
    spawned = [l for l in after if "remote spawn requested" in l]
    print(f"  host alive, joiner reloads: rejoined={rejoined[:1]} spawned={len(spawned)}")
    return gone_ok and bool(rejoined) and len(spawned) == 1


# ------------------------------------------------------------------ T4

def restart_scenario(silence):
    rng = random.Random(8)
    host = Peer("host", make_peer("host", (100.0, 50.0)), 60.0, rng)
    joiner = Peer("joiner", make_peer("joiner", (-900.0, 400.0)), 60.0, rng)
    run_pair(host, joiner, 5.0)  # ~150 packets: below SEQUENCE_RESET_GAP
    old_sequence = host.local_sequence
    host.silent = True
    run_pair(host, joiner, 5.0 + silence)
    # the host game restarts: fresh Lua, DLL sequence from 0, same place
    restarted = Peer("host", make_peer("host", (100.0, 60.0)), 60.0, rng)
    restarted.next_frame = joiner.next_frame
    restarted.last_frame = joiner.next_frame
    state = upvalue(joiner.lua, "S")
    accepted = {}

    def on_frame(peer, t):
        if peer is joiner and "t" not in accepted and 0 <= state.lastRemoteSequence < old_sequence - 50:
            accepted["t"] = t

    run_pair(restarted, joiner, 5.0 + silence + 4.0, on_frame)
    first_arrival = min(arrive for _, _, _, arrive in restarted.sent if arrive is not None)
    delay = accepted.get("t", 99.0) - first_arrival
    restart_logs = [l for l in logs(joiner.lua) if "peer restart detected" in l]
    return delay, old_sequence, restart_logs


def test_restart_detected():
    ok = True
    for silence, limit in ((12.0, 0.1), (0.6, 0.6)):
        delay, old_sequence, restart_logs = restart_scenario(silence)
        print(f"  restart after {old_sequence} packets and {silence:.1f} s silence: new session accepted "
              f"{delay * 1000:.0f} ms after its first packet arrived; {restart_logs[:1]}")
        ok = ok and delay <= limit and len(restart_logs) == 1

    # a late packet held in the DLL slot over a silence is NOT a restart
    lua = make_peer("host", (0.0, 0.0))
    g = lua.globals()
    state = upvalue(lua, "S")
    t = 0.0
    for sequence in range(1, 301):
        g.net.has, g.net.seq, g.net.x, g.net.y, g.net.fx, g.net.fy = True, sequence, 5.0, 5.0 + sequence * 0.1, 0.0, 1.0
        g.simTime = t
        g.events["onUpdate"](SEND_INTERVAL)
        t += SEND_INTERVAL
    g.net.seq = 299  # straggler lands last, then the stream stops
    for _ in range(int(4.0 / SEND_INTERVAL)):
        g.simTime = t
        g.events["onUpdate"](SEND_INTERVAL)
        t += SEND_INTERVAL
    straggler = [l for l in logs(lua) if "peer restart detected" in l]
    print(f"  late packet 299 after 300, then 4 s silence: last accepted {state.lastRemoteSequence}, restart logs {straggler}")
    return ok and state.lastRemoteSequence == 300 and not straggler


# ------------------------------------------------------------------ T5

def test_torn_read_skipped():
    lua = make_peer("joiner", (0.0, 0.0))
    g = lua.globals()
    sync = upvalue(lua, "Sync")
    diag = upvalue(lua, "Diag")
    state = upvalue(lua, "S")

    def encode(fx, fy, payload):
        return fx * (1.0 + payload), fy * (1.0 + payload)

    # packet N: TIME 10:00 (type 1, value 200); packet N+1: FLAGS 0 (host bit)
    time_packet = encode(0.6, 0.8, 1 * 512 + 200)
    flags_packet = encode(0.8, -0.6, 0 * 512 + 256)
    t = 0.0
    for sequence in range(1, 31):
        packet = time_packet if sequence % 2 else flags_packet
        g.net.has, g.net.seq = True, sequence
        g.net.x, g.net.y, g.net.z, g.net.fx, g.net.fy = 1.0, 2.0, 0.0, packet[0], packet[1]
        g.simTime = t
        g.events["onUpdate"](SEND_INTERVAL)
        t += SEND_INTERVAL
    minutes_before = sync.remoteTimeMinutes
    # packet 32 (TIME 10:00) lands between the reads of forward X and forward Y
    g.net.seq = 31
    g.net.fx, g.net.fy = flags_packet
    lua.execute(r"""
        local readX = Game.CP2077Coop_GetRemoteForwardX
        tornOnce = true
        Game.CP2077Coop_GetRemoteForwardX = function()
            local value = readX()
            if tornOnce then
                tornOnce = false
                net.seq = 32
                net.fx, net.fy = TIME_FX, TIME_FY
            end
            return value
        end
    """.replace("TIME_FX", repr(time_packet[0])).replace("TIME_FY", repr(time_packet[1])))
    g.simTime = t
    g.events["onUpdate"](SEND_INTERVAL)
    torn_frame_sequence = state.lastRemoteSequence
    minutes_torn = sync.remoteTimeMinutes
    g.events["onUpdate"](SEND_INTERVAL)
    print(f"  torn frame: accepted seq {torn_frame_sequence} (30 before), torn reads {diag.tornReads}, "
          f"host time {minutes_before}->{minutes_torn}; next frame accepted seq {state.lastRemoteSequence}, "
          f"time {sync.remoteTimeMinutes}")
    return (
        torn_frame_sequence == 30
        and diag.tornReads == 1
        and minutes_torn == minutes_before == 600
        and state.lastRemoteSequence == 32
        and sync.remoteTimeMinutes == 600
    )


# ------------------------------------------------------------------ T6 / T7

def join_session(bot_on_joiner):
    rng = random.Random(21)
    host = Peer("host", make_peer("host", (100.0, 50.0)), 60.0, rng)
    joiner = Peer("joiner", make_peer("joiner", (-900.0, 400.0)), 60.0, rng)
    joined = {}

    def on_frame(peer, t):
        if peer is joiner and "t" not in joined and any("WORLD SYNC OK" in l for l in logs(joiner.lua)):
            joined["t"] = t

    if bot_on_joiner:
        joiner.g.clickButton = "Start test pattern"
        joiner.g.events["onDraw"]()
        joiner.g.clickButton = None
    run_pair(host, joiner, 6.0, on_frame)
    spawn_from = joiner.g.spawnFrom
    spawn_positions = [(spawn_from.x, spawn_from.y)] if spawn_from else []
    return host, joiner, joined.get("t"), spawn_positions


def test_bot_anchored_after_join():
    host, joiner, joined_at, _ = join_session(bot_on_joiner=True)
    after = [p for t, _, p, _ in joiner.sent if joined_at is not None and t > joined_at + 0.05]
    far = max(math.hypot(p[0] - 100.0, p[1] - 50.0) for p in after) if after else 99.0
    joiner_logs = logs(joiner.lua)
    anchored = [i for i, l in enumerate(joiner_logs) if "BOT anchored" in l]
    synced = [i for i, l in enumerate(joiner_logs) if "WORLD SYNC OK" in l]
    print(f"  joiner bot: joined at {joined_at}, sent positions after join max {far:.1f} m from host, "
          f"anchored after sync={bool(anchored) and bool(synced) and anchored[0] > synced[0]}")
    ok = far < 25.0 and anchored and synced and anchored[0] > synced[0]

    # bot "vehicle" phase (flag 16 in the payload) must not block "Teleport to host"
    bot = upvalue(joiner.lua, "Bot")
    bot.time = 34.5
    run_pair(host, joiner, 6.5)
    in_vehicle_phase = bot.phaseName() == "vehicle"
    mark = len(logs(joiner.lua))
    joiner.g.clickButton = "Teleport to host"
    joiner.g.events["onDraw"]()
    joiner.g.clickButton = None
    run_pair(host, joiner, 7.5)
    resync = [l for l in logs(joiner.lua)[mark:] if "WORLD SYNC ->" in l]
    print(f"  bot phase {bot.phaseName()} (was vehicle={in_vehicle_phase}): manual teleport -> {resync[:1]}")
    return bool(ok) and in_vehicle_phase and bool(resync)


def test_spawn_after_join():
    _, joiner, joined_at, spawn_positions = join_session(bot_on_joiner=False)
    joiner_logs = logs(joiner.lua)
    spawn = [i for i, l in enumerate(joiner_logs) if "remote spawn requested" in l]
    synced = [i for i, l in enumerate(joiner_logs) if "WORLD SYNC OK" in l]
    where = spawn_positions[0] if spawn_positions else (99999.0, 99999.0)
    near = math.hypot(where[0] - 100.0, where[1] - 50.0)
    print(f"  joiner spawn requested {len(spawn)}x, after WORLD SYNC OK={bool(spawn) and bool(synced) and spawn[0] > synced[0]}, "
          f"local player {near:.1f} m from host when spawning")
    return len(spawn) == 1 and bool(synced) and spawn[0] > synced[0] and near < 3.0


# ------------------------------------------------------------------ T8

def test_no_global_access():
    rng = random.Random(2)
    host = Peer("host", make_peer("host", (100.0, 50.0), trap=True), 45.0, rng, path=straight)
    joiner = Peer("joiner", make_peer("joiner", (-900.0, 400.0), trap=True), 60.0, rng)
    host.g.clickButton = "Start test pattern"
    host.g.events["onDraw"]()
    host.g.clickButton = None
    run_pair(host, joiner, 10.0)
    for peer in (host, joiner):
        peer.g.events["onDraw"]()
    hits = sorted(set(host.g.globalHits.keys()) | set(joiner.g.globalHits.keys()))
    upvalues = host.lua.eval(
        "function(f) local i = 0 while debug.getupvalue(f, i + 1) do i = i + 1 end return i end"
    )(host.g.events["onUpdate"])
    print(f"  unexpected global accesses: {hits}; onUpdate upvalues {upvalues} (LuaJIT limit 60)")
    return not hits and upvalues < 60


# ------------------------------------------------------------------ T9

# Every global read (GGET) and write (GSET) in every function of a chunk, read from
# LuaJIT's bytecode without running anything. The opcode numbers come from two probes,
# so the scan follows the VM instead of hard-coding them.
STATIC_GLOBAL_SCAN = r"""
return function(source, chunkName)
    local util = require("jit.util")
    local function opcodeOf(code)
        local probe = assert(loadstring(code))
        local pc = 1
        while true do
            local ins = util.funcbc(probe, pc)
            if ins == nil then return nil end
            if util.funck(probe, -bit.rshift(ins, 16) - 1) == "probe_global_name" then
                return bit.band(ins, 0xff)
            end
            pc = pc + 1
        end
    end
    local GGET = assert(opcodeOf("return probe_global_name"), "no GGET opcode found")
    local GSET = assert(opcodeOf("probe_global_name = 1"), "no GSET opcode found")
    local hits = {}
    local function walk(fn)
        local pc = 1
        while true do
            local ins = util.funcbc(fn, pc)
            if ins == nil then break end
            local op = bit.band(ins, 0xff)
            if op == GGET or op == GSET then
                hits[#hits + 1] = {
                    kind = op == GGET and "read" or "write",
                    name = tostring(util.funck(fn, -bit.rshift(ins, 16) - 1)),
                    line = util.funcinfo(fn, pc).currentline,
                }
            end
            pc = pc + 1
        end
        local index = -1
        while true do
            local constant = util.funck(fn, index)
            if constant == nil then break end
            if type(constant) == "proto" then walk(constant) end
            index = index - 1
        end
    end
    walk(assert(loadstring(source, chunkName)))
    return hits
end
"""

# Real globals of CET 1.37 / LuaJIT 2.1 (not the test mocks, so a mock cannot hide a
# local that is declared below its user and therefore read as a nil global).
ALLOWED_GLOBALS = {
    # Lua / LuaJIT
    "assert", "bit", "error", "getmetatable", "io", "ipairs", "math", "next", "os", "pairs", "pcall",
    "print", "select", "setmetatable", "string", "table", "tonumber", "tostring", "type", "unpack", "xpcall",
    # CET
    "registerForEvent", "registerHotkey", "registerInput", "Game", "GetMod", "GetSingleton", "IsDefined",
    "NewObject", "Observe", "ObserveAfter", "Override", "ImGui", "ImGuiCond", "ImGuiWindowFlags", "ImGuiCol",
    "ImGuiStyleVar", "Vector4", "EulerAngles", "Quaternion", "CName", "TweakDB", "TweakDBID", "EngineTime",
    "gameGodModeType",
}
# a forward-local bug the scan must report (the runtime trap only sees code that runs)
PLANTED_FORWARD_LOCAL = "local function onlyAtInit() return declaredBelow end\nlocal declaredBelow = 1\nreturn onlyAtInit\n"


def scan_globals(lua, source, chunk_name):
    hits = lua.execute(STATIC_GLOBAL_SCAN)(source, chunk_name)
    return [(hits[i].kind, hits[i].name, hits[i].line) for i in range(1, len(hits) + 1)]


def test_static_global_scan():
    lua = LuaRuntime(unpack_returned_tuples=True)
    planted = scan_globals(lua, PLANTED_FORWARD_LOCAL, "=planted")
    hits = scan_globals(lua, open(harness.SCRIPT, encoding="utf-8").read(), "=init.lua")
    writes = [f"write {name} @{line}" for kind, name, line in hits if kind == "write"]
    unknown = [f"read {name} @{line}" for kind, name, line in hits if kind == "read" and name not in ALLOWED_GLOBALS]
    read_names = sorted({name for kind, name, _ in hits if kind == "read"})
    print(f"  planted forward local found: {planted}")
    print(f"  init.lua: {len(hits)} global accesses in all functions, names {read_names}")
    print(f"  writes {writes}; reads outside the CET/Lua allow-list {unknown}")
    return planted == [("read", "declaredBelow", 1)] and len(hits) > 0 and not writes and not unknown


if __name__ == "__main__":
    tests = {
        "T1 remote speed right at any sender fps (25/40/45/50/60/144/20, jitter, hitches)": test_speed_any_sender_fps,
        "T2 combat packets' sequence numbers never make the remote read faster": test_combat_sequences_not_movement_time,
        "T3 stale DLL packet not replayed after a local reload": test_stale_packet_not_replayed,
        "T4 short-session peer restart detected; held late packet is not": test_restart_detected,
        "T5 torn read of the DLL slot skipped": test_torn_read_skipped,
        "T6 joiner bot anchored after join; bot vehicle phase does not block teleport": test_bot_anchored_after_join,
        "T7 joiner spawns the avatar after the join teleport": test_spawn_after_join,
        "T8 no global access, onUpdate under the upvalue limit": test_no_global_access,
        "T9 static bytecode scan: no global writes, reads only CET/Lua globals, in every function": test_static_global_scan,
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
