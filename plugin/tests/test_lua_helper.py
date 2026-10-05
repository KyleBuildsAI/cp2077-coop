"""Runs lua/coopnet.lua under LuaJIT 2.1 (the CET runtime) against a mocked Game table.

Requires the `lupa` package (pip install lupa). Exit code 0 = all checks passed.

The mocked Game.Net_Version() returns the version this checkout builds (CMakeLists.txt VERSION and
COOPNET_WIRE_MAJOR/MINOR from src/core/Version.hpp). Unknown Game.<name> lookups raise, which is
the harshest thing CET could do for a native that is not registered.
"""
import os
import re
import sys

try:
    from lupa import luajit21 as lupa_runtime
except ImportError:  # older lupa builds only ship the default runtime
    import lupa as lupa_runtime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE = os.path.join(ROOT, "lua", "coopnet.lua")


def expected_version():
    with open(os.path.join(ROOT, "CMakeLists.txt"), encoding="utf-8") as handle:
        cmake = handle.read()
    semver = re.search(r"project\(CP2077CoopNet\s+VERSION\s+(\d+\.\d+\.\d+)", cmake).group(1)
    kind = re.search(r'set\(COOPNET_PRERELEASE_TYPE\s+"([a-z]*)"\)', cmake).group(1)
    number = re.search(r"set\(COOPNET_PRERELEASE_NUMBER\s+(\d+)\)", cmake).group(1)
    if kind:
        semver += f"-{kind}.{number}"
    with open(os.path.join(ROOT, "src", "core", "Version.hpp"), encoding="utf-8") as handle:
        header = handle.read()
    major = int(re.search(r"#define COOPNET_WIRE_MAJOR (\d+)", header).group(1))
    minor = int(re.search(r"#define COOPNET_WIRE_MINOR (\d+)", header).group(1))
    return semver, major, minor


HARNESS = r"""
local queue = { "0|0|welcome 2 host", "0|0|peer_join 1 joiner", "1|1|snap|x=1.5|y=2", "1|16|evt|a|b", "garbage", "1|17|" }
local sent = {}
local calls = {}
local clock = { now = 1791098604679.125, step = 0.25 }
local poses = {
    [1] = "interpolated -1234.568 0.000 20.250 359.99 -3.50 6.13 -0.00 0.00 2 16390 230 133.3 -20.1",
    [3] = "early 1 2",
}
local natives = {
    Net_Poll = function()
        if #queue == 0 then return "" end
        return table.remove(queue, 1)
    end,
    Net_Send = function(channel, payload)
        sent[#sent + 1] = channel .. ":" .. payload
        return true
    end,
    Net_SendTo = function(peer, channel, payload) return peer == 1 end,
    Net_Connect = function(host, port) return host == "127.0.0.1" and port == 11778 end,
    Net_ConnectRoom = function(host, port, room) return room == "night-city" end,
    Net_ConnectV2 = function(host, port, room, key, role)
        calls[#calls + 1] = table.concat({ "v2", host, tostring(port), room, key, tostring(role) }, "|")
        return true
    end,
    Net_Disconnect = function() end,
    Net_LocalId = function() return 2 end,
    Net_Stats = function() return '{"state":"connected","id":2}' end,
    Net_NowMs = function()
        clock.now = clock.now + clock.step
        return clock.now
    end,
    Net_Version = function() return VERSION_STRING end,
    Net_PushPlayer = function(x, y, z, yaw, pitch, vx, vy, vz, move, flags, health)
        calls[#calls + 1] = table.concat({ "push", x, y, z, yaw, pitch, vx, vy, vz, move, flags, health }, "|")
        return true
    end,
    Net_SampleRemote = function(peer) return poses[peer] or "" end,
}
Game = setmetatable({}, { __index = function(_, name)
    local native = natives[name]
    if native == nil then error("no native " .. tostring(name)) end
    return native
end })
json = { decode = function(text) if text:find('"connected"') then return { state = "connected", id = 2 } end error("bad") end }

local CoopNet = dofile(MODULE_PATH)
local results = {}
local function check(name, condition) results[#results + 1] = { name, condition and true or false } end

check("available", CoopNet.available())
check("connect (0.1.x path)", CoopNet.connect("127.0.0.1", 11778))
check("connect room (0.1.x path)", CoopNet.connect("10.0.0.1", 1, "night-city"))
check("send", CoopNet.send(CoopNet.EVENT, "door|open") and sent[1] == "16:door|open")
check("sendTo", CoopNet.sendTo(1, CoopNet.SNAPSHOT, "x"))
check("localId", CoopNet.localId() == 2)
local stats = CoopNet.stats()
check("stats decoded", stats ~= nil and stats.state == "connected")

local got = {}
local first = CoopNet.poll(function(sender, channel, payload) got[#got + 1] = sender .. "/" .. channel .. "/" .. payload end, 3)
check("poll respects max", #got == 3)
check("poll returns handled count", first == 3)
local rest = CoopNet.poll(function(sender, channel, payload) got[#got + 1] = sender .. "/" .. channel .. "/" .. payload end)
check("poll order + parsing", got[1] == "0/0/welcome 2 host" and got[2] == "0/0/peer_join 1 joiner"
    and got[3] == "1/1/snap|x=1.5|y=2" and got[4] == "1/16/evt|a|b")
check("malformed skipped, empty payload kept", #got == 5 and got[5] == "1/17/" and rest == 2)

-- Phase 1 natives.
check("hasNative Net_NowMs / Net_Version", CoopNet.hasNative("Net_NowMs") and CoopNet.hasNative("Net_Version"))
check("hasNative false for a missing native (lookup raises)", CoopNet.hasNative("Net_DoesNotExist") == false)
local t1 = CoopNet.nowMs()
local t2 = CoopNet.nowMs()
check("nowMs is a plain number (not cdata)", type(t1) == "number" and type(t2) == "number")
check("nowMs advances", t2 > t1 and t1 > 1.7e12)
check("nowMs keeps the sub-ms fraction", t1 ~= math.floor(t1))
check("nowMs formats with %.3f", string.format("%.3f", 1791098604679.1414) == "1791098604679.141")
local version = CoopNet.version()
check("version string", version == VERSION_STRING)
local info = CoopNet.parseVersion(version)
check("parseVersion fields", info ~= nil and info.name == "CP2077CoopNet" and info.semver == EXPECTED_SEMVER
    and info.proto == EXPECTED_PROTO and info.protoMinor == EXPECTED_PROTO_MINOR and type(info.major) == "number")
check("parseVersion rejects junk", CoopNet.parseVersion("CP2077CoopNet 0.1 proto 1") == nil
    and CoopNet.parseVersion(nil) == nil and CoopNet.parseVersion("") == nil
    and CoopNet.parseVersion("CP2077CoopNet 0.2.0- proto 1") == nil
    and CoopNet.parseVersion("CP2077CoopNet 0.2.0x proto 1") == nil
    and CoopNet.parseVersion("CP2077CoopNet 0.2.0 proto 2.1.3") == nil
    and CoopNet.parseVersion("CP2077CoopNet 0.2.0 proto x") == nil)
local release = CoopNet.parseVersion("CP2077CoopNet 0.1.2 proto 1")
check("parseVersion of a release (0.1.x strings still parse)", release ~= nil and release.semver == "0.1.2"
    and release.prerelease == nil and release.patch == 2 and release.proto == 1 and release.protoMinor == nil)
local alpha = CoopNet.parseVersion("CP2077CoopNet 0.2.0-alpha.1 proto 1")
check("parseVersion of a CPN2 pre-release", alpha ~= nil and alpha.semver == "0.2.0-alpha.1"
    and alpha.prerelease == "alpha.1" and alpha.major == 0 and alpha.minor == 2 and alpha.patch == 0 and alpha.proto == 1)
local v2 = CoopNet.parseVersion("CP2077CoopNet 0.2.0-alpha.3 proto 2.1")
check("parseVersion of a protocol v2 build", v2 ~= nil and v2.prerelease == "alpha.3" and v2.proto == 2
    and v2.protoMinor == 1)

queue[#queue + 1] = "1|1|late"
local handled, elapsed = CoopNet.pollTimed(function() end)
check("pollTimed returns count and elapsed ms", handled == 1 and type(elapsed) == "number"
    and math.abs(elapsed - clock.step) < 1e-9)
local none, idle = CoopNet.pollTimed(function() end)
check("pollTimed on an empty queue", none == 0 and idle >= 0)

-- Protocol v2 (0.2.0-alpha.3).
check("connectV2 with a role name", CoopNet.connectV2("127.0.0.1", 11778, "bench", "pw", "host")
    and calls[#calls] == "v2|127.0.0.1|11778|bench|pw|1")
check("connect with a key goes through Net_ConnectV2", CoopNet.connect("relay", nil, nil, "", "joiner")
    and calls[#calls] == "v2|relay|11778|default||2")
check("connect with a numeric role", CoopNet.connect("relay", 11779, "r1", nil, 3)
    and calls[#calls] == "v2|relay|11779|r1||3")
local refused, why = CoopNet.connectV2("relay", 11778, "r", "", "captain")
check("unknown role refused", refused == false and why == "unknown role captain")
check("roleId", CoopNet.roleId(nil) == 0 and CoopNet.roleId("spectator") == 3 and CoopNet.roleId(2) == 2
    and CoopNet.roleId(4) == nil and CoopNet.roleId(1.5) == nil)
check("pushPlayer passes every field in order", CoopNet.pushPlayer({ x = 1.5, y = -2, z = 3, yaw = 90, pitch = -10,
    vx = 6, vy = 0.5, vz = -1, move = CoopNet.MOVE.sprint, flags = CoopNet.FLAG.weaponDrawn + CoopNet.FLAG.sprinting,
    health = 200 }) and calls[#calls] == "push|1.5|-2|3|90|-10|6|0.5|-1|3|66|200")
check("pushPlayer defaults", CoopNet.pushPlayer({ x = 1, y = 2, z = 3 })
    and calls[#calls] == "push|1|2|3|0|0|0|0|0|0|0|255")
local pose = CoopNet.sampleRemote(1)
check("sampleRemote parses the pose", pose ~= nil and pose.mode == "interpolated" and pose.x == -1234.568
    and pose.z == 20.25 and pose.yaw == 359.99 and pose.vx == 6.13 and pose.moveState == 2
    and pose.flags == 16390 and pose.health == 230 and pose.delayMs == 133.3 and pose.aheadMs == -20.1)
check("sampleRemote of a silent peer is nil", CoopNet.sampleRemote(2) == nil)
check("parsePose rejects a short string", CoopNet.sampleRemote(3) == nil and CoopNet.parsePose(nil) == nil
    and CoopNet.parsePose("interpolated a b c d e f g h i j k l m") == nil)
check("teleported flag bit", bit.band(CoopNet.FLAG.teleported, 0x8000) == 0x8000)
return results
"""


def main():
    semver, proto_major, proto_minor = expected_version()
    lua = lupa_runtime.LuaRuntime(unpack_returned_tuples=True)
    print(f"runtime: {lua.eval('jit and jit.version or _VERSION')}")
    lua.globals().MODULE_PATH = MODULE
    lua.globals().VERSION_STRING = f"CP2077CoopNet {semver} proto {proto_major}.{proto_minor}"
    lua.globals().EXPECTED_SEMVER = semver
    lua.globals().EXPECTED_PROTO = proto_major
    lua.globals().EXPECTED_PROTO_MINOR = proto_minor
    print(f"mocked Net_Version(): {lua.globals().VERSION_STRING}")
    results = lua.execute(HARNESS)
    failures = 0
    for index in range(1, len(results) + 1):
        name, passed = results[index][1], results[index][2]
        print(f"  {'ok  ' if passed else 'FAIL'} {name}")
        failures += 0 if passed else 1
    print("LUA HELPER PASS" if failures == 0 else f"LUA HELPER FAIL ({failures})")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
