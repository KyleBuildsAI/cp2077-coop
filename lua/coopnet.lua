-- CET-side helper for the CP2077CoopNet RED4ext plugin.
-- Copy next to a mod's init.lua and load it with: local CoopNet = require("coopnet")
--
-- The plugin's natives are global RTTI functions, so CET exposes them on the Game table:
--   Game.Net_Connect(host, port) / Game.Net_ConnectRoom(host, port, room) -> bool
--   Game.Net_Send(channel, payload) / Game.Net_SendTo(peer, channel, payload) -> bool
--   Game.Net_Poll() -> "" or "<sender>|<channel>|<payload>"   (FIFO, one message per call)
--   Game.Net_Stats() -> JSON string, Game.Net_LocalId() -> int, Game.Net_Disconnect()

local CoopNet = {}

CoopNet.SNAPSHOT = 1 -- unreliable + sequenced: stale snapshots are dropped, newest wins
CoopNet.EVENT = 16   -- reliable + ordered: resent until acknowledged, delivered exactly once
CoopNet.MAX_PAYLOAD = 1180

function CoopNet.available()
    local ok, native = pcall(function()
        return Game.Net_Poll
    end)
    return ok and native ~= nil
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

-- Returns the decoded stats table, or nil plus the raw string if it could not be decoded.
function CoopNet.stats()
    local raw = Game.Net_Stats()
    local ok, decoded = pcall(json.decode, raw)
    if ok and type(decoded) == "table" then
        return decoded
    end
    return nil, raw
end

-- Drains up to maxMessages queued messages (default 256) in arrival order.
-- handler(sender, channel, payload); transport events use sender 0 / channel 0,
-- e.g. "welcome 2", "peer_join 3", "peer_leave 3 left", "relay_lost", "error ...".
function CoopNet.poll(handler, maxMessages)
    local limit = maxMessages or 256
    for _ = 1, limit do
        local raw = Game.Net_Poll()
        if raw == nil or raw == "" then
            return
        end
        local sender, channel, payload = raw:match("^(%d+)|(%d+)|(.*)$")
        if sender ~= nil then
            handler(tonumber(sender), tonumber(channel), payload)
        end
    end
end

return CoopNet
