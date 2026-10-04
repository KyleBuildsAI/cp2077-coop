"""Runs lua/coopnet.lua under LuaJIT 2.1 (the CET runtime) against a mocked Game table.

Requires the `lupa` package (pip install lupa). Exit code 0 = all checks passed.
"""
import os
import sys

try:
    from lupa import luajit21 as lupa_runtime
except ImportError:  # older lupa builds only ship the default runtime
    import lupa as lupa_runtime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE = os.path.join(ROOT, "lua", "coopnet.lua")

HARNESS = r"""
local queue = { "0|0|welcome 2", "0|0|peer_join 1", "1|1|snap|x=1.5|y=2", "1|16|evt|a|b", "garbage", "1|17|" }
local sent = {}
Game = {
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
}
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
CoopNet.poll(function(sender, channel, payload) got[#got + 1] = sender .. "/" .. channel .. "/" .. payload end, 3)
check("poll respects max", #got == 3)
CoopNet.poll(function(sender, channel, payload) got[#got + 1] = sender .. "/" .. channel .. "/" .. payload end)
check("poll order + parsing", got[1] == "0/0/welcome 2" and got[2] == "0/0/peer_join 1"
    and got[3] == "1/1/snap|x=1.5|y=2" and got[4] == "1/16/evt|a|b")
check("malformed skipped, empty payload kept", #got == 5 and got[5] == "1/17/")
return results
"""


def main():
    lua = lupa_runtime.LuaRuntime(unpack_returned_tuples=True)
    print(f"runtime: {lua.eval('jit and jit.version or _VERSION')}")
    lua.globals().MODULE_PATH = MODULE
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
