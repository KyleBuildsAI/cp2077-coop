"""Tests for the gameplay/world state channel in CP2077Coop init.lua.

1. Round trip: every payload value through float32 + "%.6f" (DLL + relay path).
2. Behaviour: a scripted host drives a joiner's init.lua; assert the joiner
   applies crouch, weapon, time and weather to the avatar/world.
"""
import math
import re
import random
import struct
import sys

from lupa.luajit21 import LuaRuntime

import coop_sim30 as sim

SCRIPT = sys.argv[1]
TYPE_STRIDE = 512


def float32(value):
    return struct.unpack("f", struct.pack("f", value))[0]


def wire(value):
    """DLL stores float32, prints %.6f, receiver parses back into float32."""
    return float32(float(f"{float32(value):.6f}"))


def load_script(is_host):
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().simTime = 0.0
    lua.execute(sim.MOCK)
    source = open(SCRIPT, encoding="utf-8").read()
    if not is_host:
        source, count = re.subn(r"(?m)^local IS_HOST = true", "local IS_HOST = false", source)
        assert count == 1, "IS_HOST line not found"
    lua.execute(source)
    return lua


def test_round_trip():
    lua = load_script(is_host=True)
    # Sync is local to the chunk; rebuild the same two functions from the file text
    source = open(SCRIPT, encoding="utf-8").read()
    start = source.index("function Sync.encodeForward")
    end = source.index("function Sync.receivePayload")
    lua.execute("Sync = {}\n" + source[start:end])
    encode = lua.eval("Sync.encodeForward")
    decode = lua.eval("Sync.decodeForward")

    rng = random.Random(5)
    failures = 0
    worst_angle = 0.0
    for payload in range(0, 8 * TYPE_STRIDE):
        for _ in range(8):
            angle = rng.uniform(0, 2 * math.pi)
            fx, fy = math.sin(angle), math.cos(angle)
            sx, sy = encode(fx, fy, payload)
            dx, dy, got = decode(wire(sx), wire(sy))
            if got != payload:
                failures += 1
            worst_angle = max(worst_angle, math.degrees(abs(math.atan2(dx * fy - dy * fx, dx * fx + dy * fy))))
    print(f"round trip: {8 * TYPE_STRIDE * 8} cases, payload errors={failures}, worst direction error={worst_angle:.5f} deg")
    return failures == 0 and worst_angle < 0.01


def test_joiner_behaviour():
    lua = load_script(is_host=False)
    lua.execute(r"""
        calls = { stance = {}, weapon = {}, time = {}, weather = {} }
        localMinutes = 600
        playerPos = { x = 0, y = 0, z = 0 }
        function player:GetWorldPosition() return { x = playerPos.x, y = playerPos.y, z = playerPos.z, w = 1 } end
        function player:GetWorldOrientation() return {} end
        Game.GetTeleportationFacility = function()
            return { Teleport = function(_, _, pos) playerPos.x, playerPos.y, playerPos.z = pos.x, pos.y, pos.z end }
        end
        function player:CP2077Coop_GetStateFlags() return 0 end
        function player:CP2077Coop_GetTimeOfDayMinutes() return localMinutes end
        function player:CP2077Coop_GetWeatherIndex() return 0 end
        function player:CP2077Coop_SetTimeOfDayMinutes(m) calls.time[#calls.time + 1] = m; localMinutes = m end
        function player:CP2077Coop_SetWeatherIndex(i) calls.weather[#calls.weather + 1] = i end
        function player:CP2077Coop_ApplyRemoteStance(c) calls.stance[#calls.stance + 1] = c end
        function player:CP2077Coop_ApplyRemoteWeapon(cls, drawn) calls.weapon[#calls.weapon + 1] = { cls, drawn } end
    """)
    g = lua.globals()
    on_update = g.events["onUpdate"]
    step_npc = lua.eval("stepNpc")

    host_minutes = 22 * 60 + 15  # 22:15
    host_weather = 5             # pollution

    def host_flags(t):
        if t < 4:
            return 0                                  # standing, unarmed
        if t < 7:
            return 1                                  # crouching
        if t < 10:
            return 2 + 2 * 32                         # rifle drawn
        return 2 + 4 + 1 * 32                         # pistol drawn + aiming

    seq = 0
    next_send = 0.0
    host_slot = 0
    frame_dt = 1 / 60
    t = 0.0
    while t < 13.0:
        g.simTime = t
        if t >= next_send:
            next_send += 1 / 30
            seq += 1
            host_slot += 1
            if host_slot % 2 == 0:
                if host_slot % 4 == 0:
                    payload = 1 * TYPE_STRIDE + host_minutes // 3
                else:
                    payload = 2 * TYPE_STRIDE + host_weather + 1
            else:
                payload = host_flags(t)
            scale = 1 + payload
            net = g.net
            net.has = True
            net.seq = seq
            net.x, net.y, net.z = 30.0 + t * 0.2, 12.0, 0.0
            net.fx, net.fy = wire(0.0 * scale), wire(1.0 * scale)
        g.tickSpawn()
        on_update(frame_dt)
        step_npc(frame_dt)
        t += frame_dt

    calls = g.calls
    stance = list(calls.stance.values())
    weapon = [tuple(v.values()) for v in calls.weapon.values()]
    time_calls = list(calls.time.values())
    weather_calls = list(calls.weather.values())
    print("stance calls :", stance)
    print("weapon calls :", weapon)
    print("time calls   :", time_calls)
    print("weather calls:", weather_calls)
    print("logs         :", [l for l in g.logs.values() if "synced" in l or "ERROR" in l or "disabled" in l])

    ok = True
    ok &= stance == [False, True, False]
    ok &= weapon == [(0, False), (2, True), (1, True)]
    ok &= time_calls == [host_minutes]
    ok &= weather_calls == [host_weather]
    return ok


def test_host_sends_world_state():
    lua = load_script(is_host=True)
    lua.execute(r"""
        sent = {}
        Game.CP2077Coop_PushPlayerState = function(x, y, z, w, fx, fy) sent[#sent + 1] = { fx, fy } end
        function player:CP2077Coop_GetStateFlags() return 1 + 2 + 3 * 32 end
        function player:CP2077Coop_GetTimeOfDayMinutes() return 7 * 60 + 30 end
        function player:CP2077Coop_GetWeatherIndex() return 2 end
    """)
    g = lua.globals()
    on_update = g.events["onUpdate"]

    def run_frames(first, count):
        for frame in range(first, first + count):
            g.simTime = frame / 60
            on_update(1 / 60)
        payloads = [round(math.hypot(wire(packet[1]), wire(packet[2])) - 1) for packet in g.sent.values()]
        lua.execute("sent = {}")
        return payloads

    flags_with_role = 1 + 2 + 3 * 32 + 256
    # alone: the partner has not shown it decodes payloads (a v0.0.26 partner compares
    # raw forward vectors), so only flags (constant length) and the 1 Hz ping go out
    alone = run_frames(0, 60)
    alone_kinds = sorted(set(alone))
    alone_ok = set(p for p in alone_kinds if p // TYPE_STRIDE != 3) == {flags_with_role} and len(alone) > 20
    print("host payloads before the partner's first packet:", alone_kinds)

    # the partner's ping proves it decodes payloads: the full schedule follows
    net = g.net
    ping = 3 * TYPE_STRIDE + 7
    net.has, net.seq = True, 1
    net.x, net.y, net.z = 5.0, 5.0, 0.0
    net.fx, net.fy = wire(0.0), wire(1.0 + ping)
    kinds = sorted(set(run_frames(60, 60)))
    print("host payloads after the partner's ping:", kinds)
    required = {flags_with_role, TYPE_STRIDE + (7 * 60 + 30) // 3, 2 * TYPE_STRIDE + 3, 4 * TYPE_STRIDE + 7}
    pings = [p for p in kinds if p // TYPE_STRIDE == 3]
    unexpected = [p for p in kinds if p not in required and p // TYPE_STRIDE not in (3, 6, 7)]
    print("pings sent:", len(pings), "unexpected:", unexpected)
    return alone_ok and required.issubset(kinds) and len(pings) >= 1 and not unexpected


if __name__ == "__main__":
    results = {
        "round trip": test_round_trip(),
        "host sends world state": test_host_sends_world_state(),
        "joiner applies state": test_joiner_behaviour(),
    }
    for name, passed in results.items():
        print(f"{'PASS' if passed else 'FAIL'}  {name}")
    sys.exit(0 if all(results.values()) else 1)
