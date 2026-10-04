-- Phase 3 transport adapter. No CET event handlers are registered here.
-- The caller owns lifecycle/update and MUST disable CoopNetCheck/NetProbe before
-- opting in: CP2077CoopNet has one connection and one Net_Poll inbox per game.
--
-- Default v1 performs no native calls. v2/auto select this path while probing;
-- auto falls back only before activation, for missing plugin/no answer/no peer.
-- Explicit relay rejection (bad key, role conflict, etc.) never downgrades.
-- After activation reconnects remain on v2, so an old v1 slot cannot revive a peer.
--
-- Net_SampleRemote has no raw sequence/time API. Channel 1 therefore carries a
-- small raw movement envelope for join/marker/packet accounting, while native
-- snapshots provide the independently sampled render target. Do not feed the
-- rendered pose back into v1 prediction or count it as a received packet.
-- Channel 16 carries legacy extra payloads reliably; channel 30 is app HELLO.
-- Vehicle pose/index preserve the existing cosmetic stand-in, not shared physics.
-- Combat/NPC authority is not implemented by this module.
local Net = {}
Net.__index = Net
Net.MOVEMENT_CHANNEL, Net.EXTRA_CHANNEL, Net.HELLO_CHANNEL = 1, 16, 30
Net.INTERVAL, Net.STALE_AFTER, Net.MAX_QUEUE = 1 / 30, 1.5, 256
-- Optional controlled-NPC extension. These are reserved and never polled by another module.
Net.EXTENSION_RELIABLE, Net.EXTENSION_POSE, Net.MAX_EXTENSIONS = 20, 2, 64
Net.serial = 0

local function finite(n)
    return type(n) == "number" and n == n and n > -math.huge and n < math.huge
end

local function integer(n, lo, hi)
    return finite(n) and n == math.floor(n) and n >= lo and n <= hi
end

local function fields(text)
    local out = {}
    for value in (text .. "|"):gmatch("(.-)|") do out[#out + 1] = value end
    return out
end

local function has(flags, bit)
    return math.floor(flags / bit) % 2 == 1
end

local function opposite(role)
    return role == "host" and "joiner" or "host"
end

local function epochValid(epoch)
    return type(epoch) == "string" and #epoch > 0 and #epoch <= 40 and epoch:match("^%d+$") ~= nil
end

function Net.toNativeFlags(flags, teleported)
    -- Legacy weapon class is bits 5..7; v2 bits 5/6/7 mean driving/sprint/reload.
    return flags % 32 + (math.floor(flags / 32) % 8) * 256 + (teleported and 32768 or 0)
end

function Net.toLegacyFlags(flags, role)
    return flags % 32 + (math.floor(flags / 256) % 8) * 32 + (role == "host" and 256 or 0)
end

function Net.new(options)
    options = options or {}
    local self = setmetatable({}, Net)
    self.natives = options.natives or Game
    self.log = options.log
    self.requested = options.mode or "v1"
    self.host, self.port = options.host or "127.0.0.1", options.port or 11778
    self.room, self.key = options.room or "coop", options.key or ""
    self.role, self.version = options.role or "host", options.version or "unknown"
    self.probeDisabled = options.probeDisabled == true
    self.noAnswerAfter, self.noPeerAfter = options.noAnswerAfter or 1.5, options.noPeerAfter or 5.0
    self.now, self.generation, self.nextPushAt, self.nextHelloAt = 0, 0, 0, 0
    self.mode, self.reason = "v1", "default v1"
    self.packets, self.payloads, self.peers = {}, {}, {}
    self.ownsNative, self.started, self.selectedV2 = false, false, self.requested ~= "v1"
    if self.selectedV2 then self.mode, self.reason = "probing", "v2 requested" end
    self.everReady, self.ready, self.stopped = false, false, false
    self.extraSequence = 0
    self.extensionsEnabled = options.extensions == true
    self.extensionGeneration, self.extensionInbox = 0, {}
    Net.serial = Net.serial + 1
    self.session = options.sessionEpoch
    if not epochValid(self.session) then
        self.session = string.format("%.0f%d", os.time() * 1000, Net.serial)
    end
    return self
end

function Net:call(name, ...)
    local args = {...}
    return pcall(function() return self.natives[name](unpack(args)) end)
end

function Net:note(reason)
    self.reason = reason
    if self.log then pcall(self.log, "[CP2077Coop] transport: " .. reason) end
end

function Net:clearPeer(reason)
    self.peer, self.remoteSession, self.remoteSequence, self.remoteExtraSequence = nil, nil, nil, nil
    self.latest, self.lastPacketAt, self.cachedSample = nil, nil, nil
    self.packets, self.payloads = {}, {}
    self.ready, self.localReady, self.remoteHello = false, false, false
    self.generation = self.generation + 1
    self:resetExtensionContext()
    if reason then self:note(reason) end
end

function Net:disconnectNative()
    if self.ownsNative then self:call("Net_Disconnect") end
    self.ownsNative = false
end

function Net:fallback(reason)
    self:disconnectNative()
    self:clearPeer(reason)
    self.mode, self.selectedV2 = "v1", false
end

function Net:fail(reason)
    self:disconnectNative()
    self:clearPeer(reason)
    self.mode = "error"
    -- Keep the native path selected: errors are not permission to join the public v1 pool.
    self.selectedV2 = true
end

function Net:start()
    self.started = true
    if self.requested == "v1" then return end
    self.selectedV2, self.mode = true, "probing"
    if self.requested ~= "v2" and self.requested ~= "auto" then
        self:fail("invalid transport mode")
        return
    end
    if not self.probeDisabled then
        self:fail("disable CoopNetCheck/NetProbe before enabling v2 gameplay")
        return
    end
    if self.role ~= "host" and self.role ~= "joiner" then
        self:fail("invalid player role")
        return
    end
    for _, name in ipairs({"Net_ConnectV2", "Net_Disconnect", "Net_Poll", "Net_SendTo",
                            "Net_PushPlayer", "Net_SampleRemote"}) do
        local ok, native = pcall(function() return self.natives[name] end)
        if not ok or native == nil then
            if self.requested == "auto" then self:fallback("v2 native missing: " .. name)
            else self:fail("v2 native missing: " .. name) end
            return
        end
    end
    local ok, accepted = self:call("Net_ConnectV2", self.host, self.port, self.room, self.key,
                                  self.role == "host" and 1 or 2)
    if not ok or not accepted then
        self:fail("v2 connection request failed")
        return
    end
    self.ownsNative = true
    self.startedAt = self.now
    self:note("waiting for v2 relay and compatible peer")
end

function Net:isV2()
    return self.selectedV2 and not self.stopped
end

function Net:status()
    return { mode = self.mode, reason = self.reason, peer = self.peer, epoch = self.generation,
             ready = self.ready, requested = self.requested, session = self.session,
             extensionsEnabled = self.extensionsEnabled, extensionFault = self.extensionFault }
end

-- Extensions have their own generation so disable/re-enable cannot reuse an old sender closure.
-- A queue API keeps arbitrary extension callbacks out of the transport's single polling loop.
function Net:resetExtensionContext()
    self.extensionInbox = {}
    self.extensionGeneration = self.extensionGeneration + 1
end

function Net:enableExtensions(enabled)
    enabled = enabled == true
    if enabled ~= self.extensionsEnabled then
        self.extensionsEnabled = enabled
        self.extensionFault = nil
        self:resetExtensionContext()
    end
    return self.extensionsEnabled
end

function Net:extensionContext()
    if not self.extensionsEnabled or self.stopped or not self.ownsNative or not self.ready
        or not self.peer or not self.remoteHello or not self.remoteSession then return nil end
    return { generation = self.generation, extensionGeneration = self.extensionGeneration,
             peer = self.peer, localRole = self.role, localSession = self.session,
             remoteSession = self.remoteSession }
end

function Net:extensionContextMatches(context)
    local current = self:extensionContext()
    if type(context) ~= "table" or not current then return false end
    for key, value in pairs(current) do
        if context[key] ~= value then return false end
    end
    return true
end

local function extensionPayload(channel, payload)
    return (channel == Net.EXTENSION_RELIABLE or channel == Net.EXTENSION_POSE)
        and type(payload) == "string" and #payload > 0 and #payload <= 256
        and not payload:find("[^ -~]")
end

function Net:sendExtension(context, channel, payload)
    if not self:extensionContextMatches(context) or not extensionPayload(channel, payload) then return false end
    return self:send(channel, payload)
end

function Net:takeExtension(context)
    if not self:extensionContextMatches(context) or #self.extensionInbox == 0 then return nil end
    return table.remove(self.extensionInbox, 1)
end

function Net:queueExtension(sender, channel, payload)
    local context = self:extensionContext()
    if not context or not extensionPayload(channel, payload) then return end
    if #self.extensionInbox >= Net.MAX_EXTENSIONS then
        -- Native reliable delivery already accepted this message. Silently dropping lifecycle
        -- events would hide a consistency failure. Disable only the experiment; its owner sees
        -- a missing context/fault and must shutdown its temporary actor immediately.
        self.extensionsEnabled = false
        self.extensionFault = "extension inbox overflow"
        self:resetExtensionContext()
        return
    end
    self.extensionInbox[#self.extensionInbox + 1] = {
        sender = sender, channel = channel, reliable = channel == Net.EXTENSION_RELIABLE,
        payload = payload, context = context,
    }
end

function Net:stop()
    self:disconnectNative()
    self:clearPeer("stopped")
    self.stopped, self.mode, self.selectedV2 = true, "stopped", false
end

function Net:send(channel, text)
    if not self.peer or not self.ownsNative then return false end
    local ok, accepted = self:call("Net_SendTo", self.peer, channel, text)
    return ok and accepted == true
end

function Net:hello()
    local version = tostring(self.version):gsub("[^%w._+-]", "_"):sub(1, 80)
    return self:send(Net.HELLO_CHANNEL, "C3H1|" .. self.session .. "|" .. self.role .. "|" .. version)
end

function Net:activate()
    if self.peer and self.remoteHello and self.localReady then
        if not self.ready then self:note("v2 ready with peer " .. self.peer) end
        self.ready, self.everReady, self.mode = true, true, "v2"
    end
end

function Net:onEvent(text)
    local id, role, suffix = text:match("^peer_join (%d+) (%a+)(.*)$")
    if id then
        id = tonumber(id)
        if integer(id, 1, 254) and role == opposite(self.role) and suffix == "" then
            if self.peer == nil or self.peer == id then
                self:clearPeer("v2 peer joined")
                self.peer, self.peerRole = id, role
                self.nextHelloAt = self.now
            end
        end
        return
    end
    id = tonumber(text:match("^peer_leave (%d+) "))
    if id and id == self.peer then self:clearPeer("v2 peer left"); return end
    if text:match("^welcome %d+ ") then
        self.welcomedAt = self.now
        return
    end
    if text == "relay_lost" or text:match("^relay_disconnect ") then
        self:clearPeer("v2 relay reconnecting")
        return
    end
    if text == "no_answer" and self.requested == "auto" and not self.everReady then
        self:fallback("v2 relay gave no answer")
    elseif text:match("^rejected ") or text:match("^error ") then
        self:fail("v2 " .. text)
    end
end

function Net:queue(queue, item)
    if #queue >= Net.MAX_QUEUE then
        self:fail("v2 receive queue full")
        return false
    end
    queue[#queue + 1] = item
    return true
end

function Net:onScript(sender, channel, text)
    if sender ~= self.peer then return end
    local f = fields(text)
    if channel == Net.HELLO_CHANNEL and #f == 4 and f[1] == "C3H1" and epochValid(f[2])
        and f[3] == self.peerRole and #f[4] > 0 and #f[4] <= 80 then
        if self.remoteSession ~= f[2] then
            self.remoteSequence, self.remoteExtraSequence = nil, nil
            self.packets, self.payloads = {}, {}
            self.latest, self.lastPacketAt, self.cachedSample = nil, nil, nil
            self.generation = self.generation + 1
            self:resetExtensionContext()
        end
        self.remoteSession, self.remoteHello = f[2], true
        self:activate()
        return
    end
    -- NT1 uses a separately negotiated HOST harness epoch, including joiner acknowledgments.
    -- It therefore cannot pass the C3 sender-session check below. The extension coordinator must
    -- validate its offer/ACK against BOTH sessions in this captured receive context, then
    -- pass NT1 only to the harness bound to the resulting host epoch. Never infer sender from NT1.
    if channel == Net.EXTENSION_RELIABLE or channel == Net.EXTENSION_POSE then
        self:queueExtension(sender, channel, text)
        return
    end
    if not self.remoteHello or f[2] ~= self.remoteSession then return end
    if channel == Net.EXTRA_CHANNEL and #f == 4 and f[1] == "C3E1" then
        local sequence, payload = tonumber(f[3]), tonumber(f[4])
        if integer(sequence, 1, 2147483647) and integer(payload, 512, 4095)
            and (not self.remoteExtraSequence or sequence > self.remoteExtraSequence) then
            self.remoteExtraSequence = sequence
            self:queue(self.payloads, {payload = payload, peer = sender})
        end
        return
    end
    if channel ~= Net.MOVEMENT_CHANNEL or #f ~= 18 or f[1] ~= "C3M1" then return end
    local sequence = tonumber(f[3])
    if not integer(sequence, 1, 2147483647) or (self.remoteSequence and sequence <= self.remoteSequence) then return end
    if f[4] ~= "player" and f[4] ~= "bot" and f[4] ~= "vehicle" then return end
    local p = {sequence = sequence, source = f[4], receivedAt = self.now, peer = sender}
    local names = {"x", "y", "z", "fx", "fy", "vx", "vy", "vz", "flags", "moveState", "health", "pitch", "vehicleIndex", "teleport"}
    for i, name in ipairs(names) do
        p[name] = tonumber(f[i + 4])
        if not finite(p[name]) then return end
    end
    if math.abs(p.x) > 20000 or math.abs(p.y) > 20000 or math.abs(p.z) > 5000
        or math.abs(p.vx) > 327 or math.abs(p.vy) > 327 or math.abs(p.vz) > 327
        or math.abs(p.pitch) > 90 or not integer(p.flags, 0, 511)
        or not integer(p.moveState, 0, 13) or not integer(p.health, 0, 255)
        or not integer(p.vehicleIndex, -1, 511) or not integer(p.teleport, 0, 1) then return end
    local length = math.sqrt(p.fx * p.fx + p.fy * p.fy)
    if length < 0.5 or length > 1.5 then return end
    p.fx, p.fy = p.fx / length, p.fy / length
    p.flags = p.flags % 256 + (self.peerRole == "host" and 256 or 0)
    p.reset, p.teleported = self.remoteSequence == nil, p.teleport == 1
    if p.vehicleIndex == -1 then p.vehicleIndex = nil end
    self.remoteSequence, self.latest, self.lastPacketAt = sequence, p, self.now
    self:queue(self.packets, p)
end

function Net:update(delta)
    if self.stopped then return end
    if finite(delta) and delta > 0 then self.now = self.now + delta end
    self.cachedSample = nil
    if not self.started then self:start() end
    if not self.ownsNative then return end
    for _ = 1, Net.MAX_QUEUE do
        local ok, raw = self:call("Net_Poll")
        if not ok then self:fail("Net_Poll failed"); return end
        if raw == nil or raw == "" then break end
        if type(raw) == "string" then
            local sender, channel, text = raw:match("^(%d+)|(%d+)|(.*)$")
            sender, channel = tonumber(sender), tonumber(channel)
            if sender == 0 and channel == 0 then self:onEvent(text)
            elseif sender and channel then self:onScript(sender, channel, text) end
        end
        if not self.ownsNative then return end
    end
    if self.peer and self.now >= self.nextHelloAt then
        self:hello()
        self.nextHelloAt = self.now + 1.0
    end
    if self.requested == "auto" and not self.everReady then
        if not self.welcomedAt and self.now - self.startedAt >= self.noAnswerAfter then
            self:fallback("v2 relay timeout")
        elseif self.welcomedAt and self.now - self.welcomedAt >= self.noPeerAfter then
            self:fallback("no compatible v2 peer")
        end
    end
end

function Net:push(state)
    if not self.ownsNative or type(state) ~= "table" then return false end
    local x, y, z = state.x, state.y, state.z
    local fx, fy = state.fx or state.forwardX or 0, state.fy or state.forwardY or 1
    local flags = state.flags or state.legacyFlags or 0
    if not finite(x) or not finite(y) or not finite(z) or not finite(fx) or not finite(fy)
        or math.abs(x) > 20000 or math.abs(y) > 20000 or math.abs(z) > 5000
        or not integer(flags, 0, 511) then return false end
    flags = flags % 256 + (self.role == "host" and 256 or 0)
    local source = state.source or "player"
    if source ~= "player" and source ~= "bot" and source ~= "vehicle" then return false end
    local length = math.sqrt(fx * fx + fy * fy)
    if length < 0.001 then fx, fy = 0, 1 else fx, fy = fx / length, fy / length end
    local vx, vy, vz = state.vx, state.vy, state.vz
    local teleported = state.teleported == true
    local prev = self.previous
    if prev and self.now > prev.t then
        local dt = self.now - prev.t
        local dx, dy, dz = x - prev.x, y - prev.y, z - prev.z
        if source ~= prev.source or math.sqrt(dx*dx + dy*dy + dz*dz) > math.max(6, 120 * dt) then
            teleported = true
        elseif vx == nil and vy == nil and vz == nil then
            vx, vy, vz = dx / dt, dy / dt, dz / dt
        end
    end
    self.previous = {x = x, y = y, z = z, t = self.now, source = source}
    if teleported then vx, vy, vz = 0, 0, 0 end
    self.pendingTeleport = self.pendingTeleport or teleported
    vx, vy, vz = vx or 0, vy or 0, vz or 0
    for _, v in ipairs({vx, vy, vz}) do if not finite(v) or math.abs(v) > 327 then return false end end
    local move = state.moveState or (has(flags, 16) and 10 or 0)
    local health, pitch, vehicle = state.health or 255, state.pitch or 0, state.vehicleIndex or -1
    if not integer(move, 0, 13) or not integer(health, 0, 255) or not finite(pitch)
        or math.abs(pitch) > 90 or not integer(vehicle, -1, 511) then return false end

    -- Legacy payload schedulers may run at a different cadence. Send extras independently of
    -- the movement gate, once per local frame, without spending a native snapshot slot.
    local payload = state.payload
    if self.remoteHello and integer(payload, 512, 4095)
        and not (self.lastExtraAt == self.now and self.lastExtraPayload == payload) then
        self.extraSequence = self.extraSequence + 1
        if self:send(Net.EXTRA_CHANNEL, string.format("C3E1|%s|%d|%d", self.session, self.extraSequence, payload)) then
            self.lastExtraAt, self.lastExtraPayload = self.now, payload
        end
    end
    if self.now + 0.000001 < self.nextPushAt then return false end
    local sequence = math.floor(self.now / Net.INTERVAL + 0.000001) + 1
    self.nextPushAt = sequence * Net.INTERVAL
    local yaw = math.deg(math.atan2(-fx, fy))
    teleported = self.pendingTeleport == true
    local ok, accepted = self:call("Net_PushPlayer", x, y, z, yaw, pitch, vx, vy, vz, move,
                                  Net.toNativeFlags(flags, teleported), health)
    if not ok then self:fail("Net_PushPlayer failed"); return false end
    self.localReady = accepted == true
    if accepted then self.pendingTeleport = false end
    self:activate()
    if not self.ready then return false end
    local raw = string.format("C3M1|%s|%d|%s|%.4f|%.4f|%.4f|%.6f|%.6f|%.4f|%.4f|%.4f|%d|%d|%d|%.2f|%d|%d",
        self.session, sequence, source, x, y, z, fx, fy, vx, vy, vz, flags, move, health, pitch, vehicle,
        teleported and 1 or 0)
    return self:send(Net.MOVEMENT_CHANNEL, raw)
end

function Net:takePacket()
    if #self.packets == 0 then return nil end
    return table.remove(self.packets, 1)
end

function Net:takePayload()
    if #self.payloads == 0 then return nil end
    return table.remove(self.payloads, 1)
end

function Net:sample()
    if not self.ready or not self.peer or not self.latest or not self.lastPacketAt
        or self.now - self.lastPacketAt > Net.STALE_AFTER then return nil end
    if self.cachedSample then return self.cachedSample end
    local ok, raw = self:call("Net_SampleRemote", self.peer)
    if not ok or type(raw) ~= "string" or raw == "" then return nil end
    local f = {}
    for token in raw:gmatch("%S+") do f[#f + 1] = token end
    if #f ~= 14 or not ({interpolated=true, extrapolated=true, held=true, early=true})[f[1]] then return nil end
    local p = {mode=f[1]}
    local names = {"x", "y", "z", "yaw", "pitch", "vx", "vy", "vz", "moveState", "nativeFlags", "health", "delayMs", "aheadMs"}
    for i, name in ipairs(names) do
        p[name] = tonumber(f[i+1])
        if not finite(p[name]) then return nil end
    end
    if not integer(p.nativeFlags, 0, 65535) or p.delayMs + p.aheadMs > Net.STALE_AFTER * 1000 then return nil end
    p.fx, p.fy = -math.sin(math.rad(p.yaw)), math.cos(math.rad(p.yaw))
    p.flags = Net.toLegacyFlags(p.nativeFlags, self.peerRole)
    p.teleported = has(p.nativeFlags, 32768)
    p.source, p.vehicleIndex, p.peer = self.latest.source, self.latest.vehicleIndex, self.peer
    self.cachedSample = p
    return p
end

return Net
