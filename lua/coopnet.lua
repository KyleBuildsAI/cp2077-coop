-- CET-side helper for the CP2077CoopNet RED4ext plugin.
-- Copy next to a mod's init.lua and load it with: local CoopNet = require("coopnet")
--
-- The plugin's natives are global RTTI functions, so CET exposes them on the Game table:
--   Game.Net_Connect(host, port) / Game.Net_ConnectRoom(host, port, room) -> bool
--   Game.Net_ConnectV2(host, port, room, key, role) -> bool   (0.2.0-alpha.3+)
--   Game.Net_Send(channel, payload) / Game.Net_SendTo(peer, channel, payload) -> bool
--   Game.Net_Poll() -> "" or "<sender>|<channel>|<payload>"   (FIFO, one message per call)
--   Game.Net_Stats() -> JSON string, Game.Net_LocalId() -> int, Game.Net_Disconnect()
--   Game.Net_NowMs() -> number: ms since the Unix epoch (UTC) with a sub-ms fraction
--   Game.Net_Version() -> "CP2077CoopNet <major.minor.patch>[-<prerelease>] proto <wire>"
--   Game.Net_PushPlayer(x, y, z, yaw, pitch, vx, vy, vz, moveState, flags, health) -> bool
--   Game.Net_SampleRemote(peer) -> "" or "<mode> <x> <y> <z> <yaw> <pitch> <vx> <vy> <vz> <move> <flags> <health> <delayMs> <aheadMs>"
--
-- From 0.2.0-alpha.3 the plugin speaks protocol v2 to relay_v2.py (default port 11778), which serves
-- Jakub's v1 CP1 clients on the same port. Transport events (sender 0, channel 0) include
-- "welcome <id> <role>", "peer_join <id> <role>[ legacy]", "peer_leave <id> <reason>",
-- "no_answer" (no reply to the handshake within 1.5 s: the signal to fall back to v1),
-- "rejected <reason> <text>", "relay_lost", "error <text>" and "disconnected".
--
-- All module state lives in the CoopNet table (LuaJIT allows at most 60 upvalues per function).

local CoopNet = {}

CoopNet.SNAPSHOT = 1 -- unreliable, newest wins: an older message than one already delivered is dropped
CoopNet.EVENT = 16   -- reliable + ordered: resent until acknowledged, delivered exactly once
CoopNet.MAX_PAYLOAD = 1000 -- bytes of UTF-8 without NUL
CoopNet.DEFAULT_PORT = 11778

-- Net_ConnectV2 roles.
CoopNet.ROLE = { any = 0, host = 1, joiner = 2, spectator = 3 }

-- Net_PushPlayer move states and flag bits (protocol v2 MoveState and PlayerFlag).
CoopNet.MOVE = {
    idle = 0, walk = 1, run = 2, sprint = 3, crouchIdle = 4, crouchMove = 5, jump = 6, fall = 7,
    slide = 8, swim = 9, vehicle = 10, dead = 11, ladder = 12, dodge = 13,
}
CoopNet.FLAG = {
    crouch = 1, weaponDrawn = 2, aiming = 4, firing = 8, inVehicle = 16, driving = 32, sprinting = 64,
    reloading = 128, dead = 4096, inCombat = 8192, legacy = 16384, teleported = 32768,
}
CoopNet.WEAPON_CLASS_SHIFT = 8 -- weapon class in flag bits 8..11

-- True when Game.<name> resolves (the plugin loaded and registered that native).
function CoopNet.hasNative(name)
    local ok, native = pcall(function()
        return Game[name]
    end)
    return ok and native ~= nil
end

function CoopNet.available()
    return CoopNet.hasNative("Net_Poll")
end

-- A role name ("host", "joiner", ...) or number as the Int32 Net_ConnectV2 takes; nil if unknown.
function CoopNet.roleId(role)
    if role == nil then
        return CoopNet.ROLE.any
    end
    if type(role) == "number" then
        return (role >= 0 and role <= 3 and role == math.floor(role)) and role or nil
    end
    return CoopNet.ROLE[role]
end

-- connect(host, port[, room[, key[, role]]]). With a key or a role it needs Net_ConnectV2
-- (0.2.0-alpha.3+) and returns false, "Net_ConnectV2 missing" on an older plugin.
function CoopNet.connect(host, port, room, key, role)
    if key ~= nil or role ~= nil then
        return CoopNet.connectV2(host, port, room, key, role)
    end
    if room ~= nil and room ~= "" then
        return Game.Net_ConnectRoom(host, port, room)
    end
    return Game.Net_Connect(host, port)
end

-- Joins room `room` (default "default") of the relay with password `key` (default "") as `role`
-- (a CoopNet.ROLE name or number, default any). Returns false plus a reason for bad arguments.
function CoopNet.connectV2(host, port, room, key, role)
    if not CoopNet.hasNative("Net_ConnectV2") then
        return false, "Net_ConnectV2 missing"
    end
    local roleId = CoopNet.roleId(role)
    if roleId == nil then
        return false, "unknown role " .. tostring(role)
    end
    if room == nil or room == "" then
        room = "default"
    end
    return Game.Net_ConnectV2(host, port or CoopNet.DEFAULT_PORT, room, key or "", roleId)
end

function CoopNet.disconnect()
    Game.Net_Disconnect()
end

function CoopNet.send(channel, payload)
    return Game.Net_Send(channel, payload)
end

function CoopNet.sendTo(peer, channel, payload)
    return Game.Net_SendTo(peer, channel, payload)
end

function CoopNet.localId()
    return Game.Net_LocalId()
end

-- Wall-clock milliseconds since the Unix epoch (UTC) as a plain Lua number with a sub-millisecond
-- fraction. Both game instances on one PC share this clock, so log stamps compare directly.
-- Use string.format("%.3f", CoopNet.nowMs()) to log it, math.floor() for whole milliseconds.
function CoopNet.nowMs()
    return Game.Net_NowMs()
end

-- "CP2077CoopNet 0.2.0-alpha.3 proto 2.1"
function CoopNet.version()
    return Game.Net_Version()
end

-- Parses a Net_Version() string into { name, semver, major, minor, patch, prerelease, proto,
-- protoMinor }, or nil. prerelease is e.g. "alpha.3", or nil for a release; semver includes it
-- ("0.2.0-alpha.3"). proto is the wire protocol's major number (1 for the CPN2 builds up to
-- 0.2.0-alpha.2, 2 from alpha.3) and protoMinor its minor, nil for the old "proto 1" strings.
function CoopNet.parseVersion(text)
    if type(text) ~= "string" then
        return nil
    end
    local head, wire = text:match("^(.-) proto (%d+[%.%d]*)$")
    if head == nil then
        return nil
    end
    local protoMajor, protoMinor = wire:match("^(%d+)$"), nil
    if protoMajor == nil then
        protoMajor, protoMinor = wire:match("^(%d+)%.(%d+)$")
        if protoMajor == nil then
            return nil
        end
    end
    local name, major, minor, patch, prerelease = head:match("^(%S+) (%d+)%.(%d+)%.(%d+)%-([%w%.]+)$")
    if name == nil then
        name, major, minor, patch = head:match("^(%S+) (%d+)%.(%d+)%.(%d+)$")
    end
    if name == nil then
        return nil
    end
    local semver = major .. "." .. minor .. "." .. patch
    return {
        name = name,
        semver = prerelease and (semver .. "-" .. prerelease) or semver,
        major = tonumber(major),
        minor = tonumber(minor),
        patch = tonumber(patch),
        prerelease = prerelease,
        proto = tonumber(protoMajor),
        protoMinor = protoMinor and tonumber(protoMinor) or nil,
    }
end

-- Returns the decoded stats table, or nil plus the raw string if it could not be decoded.
function CoopNet.stats()
    local raw = Game.Net_Stats()
    local ok, decoded = pcall(json.decode, raw)
    if ok and type(decoded) == "table" then
        return decoded
    end
    return nil, raw
end

-- Drains up to maxMessages queued messages (default 256) in arrival order and returns how many
-- were handled. handler(sender, channel, payload); transport events use sender 0 / channel 0,
-- e.g. "welcome 2 host", "peer_join 3 joiner", "peer_leave 3 quit", "relay_lost", "error ...".
function CoopNet.poll(handler, maxMessages)
    local limit = maxMessages or 256
    local handled = 0
    for _ = 1, limit do
        local raw = Game.Net_Poll()
        if raw == nil or raw == "" then
            return handled
        end
        local sender, channel, payload = raw:match("^(%d+)|(%d+)|(.*)$")
        if sender ~= nil then
            handler(tonumber(sender), tonumber(channel), payload)
            handled = handled + 1
        end
    end
    return handled
end

-- Same as poll, and also returns how long the whole drain took in milliseconds (Net_NowMs),
-- handler time included. Phase 1 target: under 0.1 ms per frame with a no-op handler.
function CoopNet.pollTimed(handler, maxMessages)
    local started = Game.Net_NowMs()
    local handled = CoopNet.poll(handler, maxMessages)
    return handled, Game.Net_NowMs() - started
end

-- Sends the local player for the next 30 Hz snapshot. state = { x, y, z, yaw, pitch, vx, vy, vz,
-- move, flags, health }; missing fields are 0 (health 255). Returns false until the session is up
-- and the relay clock is synced, or for an invalid state (e.g. outside the world, DRIVING flag).
function CoopNet.pushPlayer(state)
    return Game.Net_PushPlayer(state.x or 0, state.y or 0, state.z or 0, state.yaw or 0, state.pitch or 0,
        state.vx or 0, state.vy or 0, state.vz or 0, state.move or 0, state.flags or 0, state.health or 255)
end

local POSE_FIELDS = { "x", "y", "z", "yaw", "pitch", "vx", "vy", "vz", "moveState", "flags", "health", "delayMs",
    "aheadMs" }
CoopNet.POSE_FIELDS = POSE_FIELDS

-- Parses a Net_SampleRemote string into { mode, x, y, z, yaw, pitch, vx, vy, vz, moveState, flags,
-- health, delayMs, aheadMs }, or nil for "" (nothing received from that player yet) or junk.
function CoopNet.parsePose(text)
    if type(text) ~= "string" or text == "" then
        return nil
    end
    local fields = {}
    for token in text:gmatch("%S+") do
        fields[#fields + 1] = token
    end
    if #fields ~= #POSE_FIELDS + 1 then
        return nil
    end
    local pose = { mode = fields[1] }
    for index, name in ipairs(POSE_FIELDS) do
        local value = tonumber(fields[index + 1])
        if value == nil then
            return nil
        end
        pose[name] = value
    end
    return pose
end

-- The remote player `peer` at render time (100-150 ms in the past, extrapolated over gaps), or
-- nil. Call once per frame and peer.
function CoopNet.sampleRemote(peer)
    return CoopNet.parsePose(Game.Net_SampleRemote(peer))
end

return CoopNet
