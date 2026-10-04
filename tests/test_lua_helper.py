"""Runs lua/coopnet.lua under LuaJIT 2.1 (the CET runtime) against a mocked Game table.

Requires the `lupa` package (pip install lupa). Exit code 0 = all checks passed.

The mocked Game.Net_Version() returns the version this checkout builds (CMakeLists.txt VERSION and
COOPNET_PROTOCOL_VERSION from src/core/Protocol.hpp). Unknown Game.<name> lookups raise, which is
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
    with open(os.path.join(ROOT, "src", "core", "Protocol.hpp"), encoding="utf-8") as handle:
        proto = re.search(r"#define COOPNET_PROTOCOL_VERSION (\d+)", handle.read()).group(1)
    return semver, int(proto)


HARNESS = r"""
local queue = { "0|0|welcome 2", "0|0|peer_join 1", "1|1|snap|x=1.5|y=2", "1|16|evt|a|b", "garbage", "1|17|" }
local sent = {}
local clock = { now = 1791098604679.125, step = 0.25 }
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
    Net_Connect = function(host, port) return host == "127.0.0.1" and port == 11779 end,
    Net_ConnectRoom = function(host, port, room) return room == "night-city" end,
    Net_Disconnect = function() end,
    Net_LocalId = function() return 2 end,
    Net_Stats = function() return '{"state":"connected","id":2}' end,
    Net_NowMs = function()
        clock.now = clock.now + clock.step
        return clock.now
    end,
    Net_Version = function() return VERSION_STRING end,
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
check("connect", CoopNet.connect("127.0.0.1", 11779))
check("connect room", CoopNet.connect("10.0.0.1", 1, "night-city"))
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
check("poll order + parsing", got[1] == "0/0/welcome 2" and got[2] == "0/0/peer_join 1"
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
    and info.proto == EXPECTED_PROTO and type(info.major) == "number")
check("parseVersion rejects junk", CoopNet.parseVersion("CP2077CoopNet 0.1 proto 1") == nil
    and CoopNet.parseVersion(nil) == nil and CoopNet.parseVersion("") == nil
    and CoopNet.parseVersion("CP2077CoopNet 0.2.0- proto 1") == nil
    and CoopNet.parseVersion("CP2077CoopNet 0.2.0x proto 1") == nil)
local release = CoopNet.parseVersion("CP2077CoopNet 0.1.2 proto 1")
check("parseVersion of a release (0.1.x strings still parse)", release ~= nil and release.semver == "0.1.2"
    and release.prerelease == nil and release.patch == 2 and release.proto == 1)
local alpha = CoopNet.parseVersion("CP2077CoopNet 0.2.0-alpha.1 proto 1")
check("parseVersion of a pre-release", alpha ~= nil and alpha.semver == "0.2.0-alpha.1"
    and alpha.prerelease == "alpha.1" and alpha.major == 0 and alpha.minor == 2 and alpha.patch == 0)

queue[#queue + 1] = "1|1|late"
local handled, elapsed = CoopNet.pollTimed(function() end)
check("pollTimed returns count and elapsed ms", handled == 1 and type(elapsed) == "number"
    and math.abs(elapsed - clock.step) < 1e-9)
local none, idle = CoopNet.pollTimed(function() end)
check("pollTimed on an empty queue", none == 0 and idle >= 0)
return results
"""


def main():
    semver, proto = expected_version()
    lua = lupa_runtime.LuaRuntime(unpack_returned_tuples=True)
    print(f"runtime: {lua.eval('jit and jit.version or _VERSION')}")
    lua.globals().MODULE_PATH = MODULE
    lua.globals().VERSION_STRING = f"CP2077CoopNet {semver} proto {proto}"
    lua.globals().EXPECTED_SEMVER = semver
    lua.globals().EXPECTED_PROTO = proto
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
