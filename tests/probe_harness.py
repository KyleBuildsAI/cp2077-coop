"""Shared helpers for the NetProbe tests.

* ProbeInstance: one LuaJIT 2.1 state (lupa) that loads lua/netprobe.lua with the documented
  require("netprobe") line and a mocked CET environment (Game.GetPlayer, Game.Net_*, print).
* SimNet / SimEndpoint: an in-memory link on a virtual clock with latency, jitter and loss,
  modelled on CP2077CoopNet: unreliable channels drop, reliable channels resend lost attempts
  after an RTO and deliver in order exactly once. Fault injection breaks the reliable channel on
  purpose so the probe's detectors can be tested.
* BotPath: Bot-like movement (walk / run / sprint / idle / fast on an 8 m circle) giving the
  true pose for any time.
"""
import heapq
import math
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LUA_DIR = os.path.join(ROOT, "lua")
PYDEPS = os.path.join(ROOT, ".pydeps")
TOOLS = os.path.join(ROOT, "tools")

for extra in (PYDEPS, TOOLS):
    if os.path.isdir(extra) and extra not in sys.path:
        sys.path.insert(0, extra)

from lupa.luajit21 import LuaRuntime  # noqa: E402  (path set up above)

EPOCH_MS = 1_790_000_000_000.0  # realistic Net_NowMs magnitude (Sept 2026)
MAX_PAYLOAD = 1180

# Lua side of the mock: Game.Net_* forward to the Python BACKEND object (or are left out to model a
# missing plugin), Game.GetPlayer returns a player whose pose comes from the POSE table.
MOCK_GAME = r"""
local backend, natives = ...
POSE = { x = 0.0, y = 0.0, z = 0.0, fx = 0.0, fy = 1.0 }
PRINTS = {}
print = function(...)
    local parts = {}
    for i = 1, select("#", ...) do parts[#parts + 1] = tostring(select(i, ...)) end
    PRINTS[#PRINTS + 1] = table.concat(parts, " ")
end
local player = {
    GetWorldPosition = function(self) return { x = POSE.x, y = POSE.y, z = POSE.z, w = 1.0 } end,
    GetWorldForward = function(self) return { x = POSE.fx, y = POSE.fy, z = 0.0, w = 0.0 } end,
}
Game = { GetPlayer = function() return player end }
local wrappers = {
    Net_Connect = function(host, port) return backend.connect(host, port, "") end,
    Net_ConnectRoom = function(host, port, room) return backend.connect(host, port, room) end,
    Net_Disconnect = function() backend.disconnect() end,
    Net_Send = function(channel, payload) return backend.send(channel, payload) end,
    Net_Poll = function() return backend.poll() end,
    Net_Stats = function() return "{}" end,
    Net_LocalId = function() return backend.local_id() end,
    Net_NowMs = function() return backend.now_ms() end,
    Net_Version = function() return "CP2077CoopNet 0.1.1 proto 1" end,
}
for name in string.gmatch(natives, "[%w_]+") do
    Game[name] = wrappers[name]
end
"""

# FFI backend: Game.Net_* call the real coopnet::Transport through tests/shim (coopnet_shim.dll).
FFI_GAME = r"""
local shimPath = ...
local ffi = require("ffi")
ffi.cdef[[
void* cnshim_create(void);
void cnshim_destroy(void* handle);
int cnshim_connect(void* handle, const char* host, int port, const char* room);
void cnshim_disconnect(void* handle);
int cnshim_send(void* handle, int channel, const char* payload, int length);
int cnshim_poll(void* handle, char* buffer, int capacity);
int cnshim_local_id(void* handle);
double cnshim_now_ms(void);
]]
local shim = ffi.load(shimPath)
local handle = shim.cnshim_create()
local buffer = ffi.new("char[4096]")
SHIM_HANDLE = handle
Game.Net_Connect = function(host, port) return shim.cnshim_connect(handle, host, port, "") == 1 end
Game.Net_ConnectRoom = function(host, port, room) return shim.cnshim_connect(handle, host, port, room) == 1 end
Game.Net_Disconnect = function() shim.cnshim_disconnect(handle) end
Game.Net_Send = function(channel, payload) return shim.cnshim_send(handle, channel, payload, #payload) == 1 end
Game.Net_Poll = function()
    local length = shim.cnshim_poll(handle, buffer, 4096)
    if length < 0 then return "" end
    return ffi.string(buffer, length)
end
Game.Net_LocalId = function() return shim.cnshim_local_id(handle) end
Game.Net_NowMs = function() return shim.cnshim_now_ms() end
Game.Net_Version = function() return "coopnet_shim (dllproto core)" end
SHIM_DESTROY = function()
    shim.cnshim_disconnect(handle)
    shim.cnshim_destroy(handle)
end
SHIM_NOW = function() return shim.cnshim_now_ms() end
"""

ALL_NATIVES = "Net_Connect Net_ConnectRoom Net_Disconnect Net_Send Net_Poll Net_Stats Net_LocalId Net_NowMs Net_Version"


def lua_table(lua, mapping):
    table = lua.table()
    for key, value in mapping.items():
        table[key] = value
    return table


class ProbeInstance:
    """One CET-like Lua state with NetProbe loaded via require("netprobe")."""

    def __init__(self, backend=None, natives=ALL_NATIVES, shim_path=None, game=True):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        lua_dir = LUA_DIR.replace("\\", "/")
        self.lua.execute(f'package.path = "{lua_dir}/?.lua;" .. package.path')
        self.lua.execute(MOCK_GAME, backend, natives if backend is not None else "")
        if shim_path is not None:
            self.lua.execute(FFI_GAME, shim_path.replace("\\", "/"))
        if not game:
            self.lua.execute("Game = nil")
        self.probe = self.lua.execute('return require("netprobe")')
        self.pose = self.lua.globals().POSE

    def init(self, **config):
        return self.probe.init(lua_table(self.lua, config))

    def set_pose(self, x, y, z, forward_x, forward_y):
        pose = self.pose
        pose["x"], pose["y"], pose["z"], pose["fx"], pose["fy"] = x, y, z, forward_x, forward_y

    def update(self, delta_seconds):
        self.probe.update(delta_seconds)

    def shutdown(self):
        self.probe.shutdown()

    def prints(self):
        prints = self.lua.globals().PRINTS
        return [prints[index] for index in range(1, len(prints) + 1)]

    def state(self):
        return self.probe.s


# ------------------------------------------------------------------------------------------------
# simulated link
# ------------------------------------------------------------------------------------------------

class SimNet:
    def __init__(self, latency_ms=115.0, jitter_ms=20.0, loss_pct=1.0, seed=1, rto_ms=None, faults=None):
        self.latency_ms = latency_ms
        self.jitter_ms = jitter_ms
        self.loss = loss_pct / 100.0
        self.rng = random.Random(seed)
        self.rto_ms = rto_ms if rto_ms is not None else max(60.0, 2.0 * (latency_ms + jitter_ms) + 20.0)
        self.faults = dict(faults or {})
        self.now_ms = EPOCH_MS
        self.endpoints = []
        self.counter = 0
        self.last_delivery = {}   # (src, dst, kind) -> latest delivery time (FIFO per direction)
        self.dropped_probe_seqs = {}  # (src, dst) -> unreliable NP1 seqs dropped by the link
        self.reliable_sent = {}   # (src, dst) -> reliable messages accepted

    def endpoint(self):
        endpoint = SimEndpoint(self)
        self.endpoints.append(endpoint)
        return endpoint

    def advance(self, milliseconds):
        self.now_ms += milliseconds

    def connected(self):
        return [endpoint for endpoint in self.endpoints if endpoint.peer_id]

    def deliver(self, source, target, channel, payload, deliver_at):
        self.counter += 1
        heapq.heappush(target.inbox, (deliver_at, self.counter, f"{source.peer_id}|{channel}|{payload}"))

    def transmit(self, source, target, channel, payload):
        key = (source.peer_id, target.peer_id)
        if channel < 16:
            if self.rng.random() < self.loss:
                if payload.startswith("NP1|u|"):
                    self.dropped_probe_seqs.setdefault(key, []).append(int(payload.split("|")[3]))
                return
            arrival = self.now_ms + self.latency_ms + self.rng.random() * self.jitter_ms
            fifo = key + ("u",)
            arrival = max(arrival, self.last_delivery.get(fifo, 0.0))
            self.last_delivery[fifo] = arrival
            self.deliver(source, target, channel, payload, arrival)
            return
        # reliable: every lost attempt costs one RTO, delivery stays in order and exactly once
        count = self.reliable_sent.get(key, 0) + 1
        self.reliable_sent[key] = count
        if self.faults.get("reliable_drop") == count:
            return
        attempt_time = self.now_ms
        while self.rng.random() < self.loss:
            attempt_time += self.rto_ms
        arrival = attempt_time + self.latency_ms + self.rng.random() * self.jitter_ms
        fifo = key + ("r",)
        if self.faults.get("reliable_late") == count:
            arrival += 1500.0  # overtaken by the next message: a broken ordering guarantee
        else:
            arrival = max(arrival, self.last_delivery.get(fifo, 0.0))
            self.last_delivery[fifo] = arrival
        self.deliver(source, target, channel, payload, arrival)
        if self.faults.get("reliable_dup") == count:
            self.deliver(source, target, channel, payload, arrival + 5.0)


class SimEndpoint:
    def __init__(self, net):
        self.net = net
        self.peer_id = 0
        self.inbox = []
        self.connect_calls = []
        self.sends = 0
        self.polls = 0

    # Game.Net_* backends (called from Lua)
    def connect(self, host, port, room):
        self.connect_calls.append((host, int(port), room))
        others = self.net.connected()
        self.peer_id = max([endpoint.peer_id for endpoint in others] + [0]) + 1
        now = self.net.now_ms
        self.net.counter += 1
        heapq.heappush(self.inbox, (now + 1.0, self.net.counter, f"0|0|welcome {self.peer_id}"))
        for other in others:
            self.net.counter += 1
            heapq.heappush(self.inbox, (now + 1.0, self.net.counter, f"0|0|peer_join {other.peer_id}"))
            self.net.counter += 1
            heapq.heappush(other.inbox, (now + 1.0, self.net.counter, f"0|0|peer_join {self.peer_id}"))
        return True

    def disconnect(self):
        self.peer_id = 0

    def send(self, channel, payload):
        channel = int(channel)
        others = [endpoint for endpoint in self.net.connected() if endpoint is not self]
        if not self.peer_id or not others or not 1 <= channel <= 31 or len(payload) > MAX_PAYLOAD:
            return False
        self.sends += 1
        for other in others:
            self.net.transmit(self, other, channel, payload)
        return True

    def poll(self):
        self.polls += 1
        if self.inbox and self.inbox[0][0] <= self.net.now_ms:
            return heapq.heappop(self.inbox)[2]
        return ""

    def local_id(self):
        return self.peer_id

    def now_ms(self):
        return self.net.now_ms


# ------------------------------------------------------------------------------------------------
# movement
# ------------------------------------------------------------------------------------------------

class BotPath:
    """Runs around a circle with Bot-like phases; pose(t_ms) -> (x, y, z, forward_x, forward_y)."""

    PHASES = (("walk", 6.0, 1.8), ("run", 6.0, 4.5), ("sprint", 4.0, 7.0), ("idle", 3.0, 0.0),
              ("fast", 4.0, 14.0))

    def __init__(self, center_x, center_y, radius=8.0, start_ms=EPOCH_MS, phase_offset_s=0.0, z=0.5):
        self.center = (center_x, center_y)
        self.radius = radius
        self.start_ms = start_ms
        self.offset = phase_offset_s
        self.z = z
        self.cycle = sum(duration for _, duration, _ in self.PHASES)
        self.cycle_distance = sum(duration * speed for _, duration, speed in self.PHASES)

    def distance_at(self, seconds):
        cycles, remainder = divmod(seconds, self.cycle)
        distance = cycles * self.cycle_distance
        for _, duration, speed in self.PHASES:
            step = min(remainder, duration)
            distance += step * speed
            remainder -= step
            if remainder <= 0:
                break
        return distance

    def phase_at(self, seconds):
        remainder = seconds % self.cycle
        for name, duration, _ in self.PHASES:
            if remainder < duration:
                return name
            remainder -= duration
        return self.PHASES[-1][0]

    def pose(self, t_ms):
        seconds = (t_ms - self.start_ms) / 1000.0 + self.offset
        angle = self.distance_at(seconds) / self.radius
        x = self.center[0] + self.radius * math.cos(angle)
        y = self.center[1] + self.radius * math.sin(angle)
        # counter-clockwise motion: forward is the tangent (-sin, cos)
        return x, y, self.z, -math.sin(angle), math.cos(angle)


def yaw_from_forward(forward_x, forward_y):
    return math.degrees(math.atan2(-forward_x, forward_y))


def read_lines(path, kind):
    with open(path, "r", encoding="utf-8") as handle:
        return [line.rstrip("\n") for line in handle if line.startswith(kind + " ")]


def parse_stats(line):
    fields = {}
    for token in line.split(" ")[1:]:
        if "=" in token:
            key, value = token.split("=", 1)
            fields[key] = value
    return fields


def final_stats(path):
    lines = read_lines(path, "STATS")
    if not lines:
        raise AssertionError(f"{path} has no STATS line")
    return parse_stats(lines[-1])
