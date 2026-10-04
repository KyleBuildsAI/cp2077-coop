-- CET-side helper for the CP2077CoopNet RED4ext plugin.
-- Copy next to a mod's init.lua and load it with: local CoopNet = require("coopnet")
--
-- The plugin's natives are global RTTI functions, so CET exposes them on the Game table:
--   Game.Net_Connect(host, port) / Game.Net_ConnectRoom(host, port, room) -> bool
--   Game.Net_Send(channel, payload) / Game.Net_SendTo(peer, channel, payload) -> bool
--   Game.Net_Poll() -> "" or "<sender>|<channel>|<payload>"   (FIFO, one message per call)
--   Game.Net_Stats() -> JSON string, Game.Net_LocalId() -> int, Game.Net_Disconnect()
--   Game.Net_NowMs() -> number: ms since the Unix epoch (UTC) with a sub-ms fraction
--   Game.Net_Version() -> "CP2077CoopNet <major.minor.patch>[-<prerelease>] proto <n>"
--
-- All module state lives in the CoopNet table (LuaJIT allows at most 60 upvalues per function).

local CoopNet = {}

CoopNet.SNAPSHOT = 1 -- unreliable + sequenced: stale snapshots are dropped, newest wins
CoopNet.EVENT = 16   -- reliable + ordered: resent until acknowledged, delivered exactly once
CoopNet.MAX_PAYLOAD = 1180

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

function CoopNet.connect(host, port, room)
    if room ~= nil and room ~= "" then
        return Game.Net_ConnectRoom(host, port, room)
    end
    return Game.Net_Connect(host, port)
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

-- "CP2077CoopNet 0.2.0-alpha.1 proto 1"
function CoopNet.version()
    return Game.Net_Version()
end

-- Parses a Net_Version() string into { name, semver, major, minor, patch, prerelease, proto }, or
-- nil. prerelease is e.g. "alpha.1", or nil for a release; semver includes it ("0.2.0-alpha.1").
function CoopNet.parseVersion(text)
    if type(text) ~= "string" then
        return nil
    end
    local name, major, minor, patch, prerelease, proto =
        text:match("^(%S+) (%d+)%.(%d+)%.(%d+)%-([%w%.]+) proto (%d+)$")
    if name == nil then
        name, major, minor, patch, proto = text:match("^(%S+) (%d+)%.(%d+)%.(%d+) proto (%d+)$")
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
        proto = tonumber(proto),
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
-- e.g. "welcome 2", "peer_join 3", "peer_leave 3 left", "relay_lost", "error ...".
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

return CoopNet
