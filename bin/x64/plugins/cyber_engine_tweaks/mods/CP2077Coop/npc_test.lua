-- Optional single-actor integration. No event registration, socket or Net_Poll.
-- Both endpoints must opt in; bind both app sessions and a fresh nonce from each
-- side so old offers cannot activate after reconnect with unchanged app epochs.
local Harness = require("testnpc")
local Npc = {}
Npc.__index = Npc
Npc.serial = 0

local function epoch(value)
    return type(value) == "string" and #value > 0 and #value <= 40 and value:match("^[1-9]%d*$") ~= nil
end
local function newer(a, b)
    return not b or #a > #b or (#a == #b and a > b)
end
local function equal(a, b)
    if not a or not b then return false end
    for _, key in ipairs({"generation", "extensionGeneration", "peer", "localRole", "localSession", "remoteSession"}) do
        if a[key] ~= b[key] then return false end
    end
    return true
end
local function tokens(payload)
    local result = {}
    for value in (payload .. "|"):gmatch("(.-)|") do result[#result+1] = value end
    return result
end

function Npc.new(options)
    options = options or {}
    local self = setmetatable({enabled=options.enabled == true, state="off", now=0, nextLog=0}, Npc)
    self.transport, self.entity, self.log = options.transport, options.entity, options.log
    self.epochFactory = options.epochFactory
    if self.enabled then
        assert(self.transport and self.entity and type(self.epochFactory) == "function", "NPC extension dependencies required")
        self.transport:enableExtensions(true)
        self:note("waiting for v2 peer")
    end
    return self
end

function Npc:note(state)
    if state == self.state then return end
    self.state = state
    if self.log then self.log("[NPC TEST] " .. state) end
end

function Npc:nonce()
    local value = self.epochFactory()
    if not epoch(value) or not newer(value, self.lastNonce) then error("NPC epoch factory must return fresh increasing decimal epochs") end
    self.lastNonce = value
    return value
end

function Npc:clear(reason)
    if self.actor then self.actor:shutdown() end
    self.actor, self.path, self.anchor = nil, nil, nil
    self.context, self.joinNonce, self.hostEpoch, self.lastHostEpoch = nil, nil, nil, nil
    self.nextHandshake = 0
    self:note(reason)
end

function Npc:sendHandshake(kind)
    local c = self.context
    if not c then return false end
    local hostSession = c.localRole == "host" and c.localSession or c.remoteSession
    local joinSession = c.localRole == "joiner" and c.localSession or c.remoteSession
    local tail = kind == "J" and self.joinNonce or (self.hostEpoch .. "|" .. self.joinNonce)
    return self.transport:sendExtension(c, 20, "C3N1|" .. kind .. "|" .. hostSession .. "|" .. joinSession .. "|" .. tail)
end

function Npc:activate()
    if self.actor then return end
    local context = self.context
    self.actor = Harness.new({enabled=true, role=context.localRole, epoch=self.hostEpoch,
        peer=context.peer, entity=self.entity,
        send=function(reliable, payload)
            return self.transport:sendExtension(context, reliable and 20 or 2, payload)
        end})
    self:note("ready: controlled NPC only")
end

function Npc:receive(packet)
    local c = self.context
    if not c or not equal(c, packet.context) or packet.sender ~= c.peer then return end
    if packet.payload:sub(1, 4) == "NT1|" then
        if self.actor and ((packet.channel == 20 and packet.reliable == true) or
            (packet.channel == 2 and packet.reliable == false)) then
            self.actor:receive(packet.sender, packet.reliable, packet.payload)
        end
        return
    end
    if packet.channel ~= 20 or packet.reliable ~= true then return end
    local f = tokens(packet.payload)
    local hostSession = c.localRole == "host" and c.localSession or c.remoteSession
    local joinSession = c.localRole == "joiner" and c.localSession or c.remoteSession
    if f[1] ~= "C3N1" or f[3] ~= hostSession or f[4] ~= joinSession then return end
    if c.localRole == "host" and f[2] == "J" and #f == 5 and epoch(f[5]) then
        if f[5] == self.joinNonce then
            if not self.actor then self.nextHandshake = 0 end
        elseif newer(f[5], self.joinNonce) then
            if self.actor then self.actor:shutdown() end
            self.actor, self.path, self.anchor = nil, nil, nil
            self.joinNonce, self.hostEpoch = f[5], self:nonce()
            self.nextHandshake = 0
            self:note("waiting for NPC offer acknowledgement")
        end
    elseif c.localRole == "host" and f[2] == "A" and #f == 6
        and f[5] == self.hostEpoch and f[6] == self.joinNonce then
        self:activate()
    elseif c.localRole == "joiner" and f[2] == "O" and #f == 6 and epoch(f[5]) and f[6] == self.joinNonce then
        if f[5] == self.hostEpoch or newer(f[5], self.lastHostEpoch) then
            if f[5] ~= self.hostEpoch then
                if self.actor then self.actor:shutdown() end
                self.actor, self.path, self.anchor = nil, nil, nil
                self.hostEpoch, self.lastHostEpoch = f[5], f[5]
            end
            self.nextHandshake = 0
            self.ackPending = true
        end
    end
end

function Npc:update(dt, frozen)
    if not self.enabled or type(dt) ~= "number" or dt ~= dt or dt < 0 or dt == math.huge then return end
    self.now = self.now + dt
    -- Faults take precedence over menu cleanup. Otherwise overflow on the same
    -- frame as a freeze could be mistaken for our deliberate temporary disable.
    local fault = self.transport:status().extensionFault
    if fault and not self.fault then
        self:clear("disabled: " .. fault)
        self.fault, self.paused = fault, false
    end
    if self.fault then return end
    if frozen then
        if self.context then
            self:clear("paused: test actor removed")
            self.transport:enableExtensions(false)
            self.paused = true
        end
        return
    elseif self.paused then
        self.paused = false
        self.transport:enableExtensions(true)
    end
    local c = self.transport:extensionContext()
    if not c then
        if self.context then self:clear("waiting for v2 peer") end
        return
    end
    if not equal(c, self.context) then
        self:clear("negotiating NPC opt-in")
        self.context = c
        self.ackPending = false
        if c.localRole == "joiner" then self.joinNonce = self:nonce() end
    end
    for _ = 1, 64 do
        local packet = self.transport:takeExtension(self.context)
        if not packet then break end
        self:receive(packet)
    end
    if self.now >= self.nextHandshake then
        if c.localRole == "host" and self.joinNonce and not self.actor then
            self:sendHandshake("O")
        elseif c.localRole == "joiner" then
            if self.ackPending then
                if self:sendHandshake("A") then self.ackPending = false; self:activate() end
            else self:sendHandshake("J") end
        end
        self.nextHandshake = self.now + 0.5
    end
    if self.actor then
        if self.path and self.actor.actor and not self.actor.actor.stopping then
            self.pathTime = self.pathTime + dt
            local phase = (self.pathTime * 1.5) % 24
            local y = phase <= 12 and phase or 24 - phase
            self.actor:move({x=self.anchor.x, y=self.anchor.y + y, z=self.anchor.z, yaw=phase <= 12 and 0 or 180})
        end
        self.actor:update(dt)
        if self.now >= self.nextLog then
            if self.log then self.log("[NPC TEST] " .. self:status().text) end
            self.nextLog = self.now + 5
        end
    end
end

function Npc:spawnNear(position)
    if not self.actor or not self.context or self.context.localRole ~= "host" then return false end
    local anchor = {x=position.x - 6, y=position.y, z=position.z, yaw=0}
    if not self.actor:spawn(anchor) then return false end
    self.anchor, self.pathTime, self.path = anchor, 0, false
    if self.log then self.log("[NPC TEST] host requested one actor in west lane") end
    return true
end

function Npc:togglePath()
    if not self.actor or not self.actor.actor or not self.anchor or self.actor.actor.stopping then return false end
    self.path = not self.path
    return true
end

function Npc:remove()
    self.path = nil
    return self.actor and self.actor:stop() or false
end

function Npc:status()
    local a = self.actor and self.actor.actor
    local p = self.actor and self.entity.read()
    local text = self.state
    if a then
        local phase = a.stopping and " removing" or (not p and " waiting for actor" or " active")
        if p and self.context.localRole == "host" and not a.ack and not a.stopping then phase = " awaiting peer actor" end
        text = text .. " | id=" .. a.id .. phase
        if p then
            local dx, dy, dz = p.x-a.target.x, p.y-a.target.y, p.z-a.target.z
            text = text .. string.format(" | %.2f %.2f %.2f target_err_m=%.2f", p.x, p.y, p.z, math.sqrt(dx*dx+dy*dy+dz*dz))
        end
    end
    if self.actor then text = text .. string.format(" | sent=%d rx=%d rejected=%d expired=%d",
        self.actor.sent, self.actor.received, self.actor.rejected, self.actor.expired) end
    return {state=self.state, ready=self.actor ~= nil, actor=a ~= nil, path=self.path == true, text=text}
end

function Npc:shutdown()
    if not self.enabled then return end
    self:clear("off")
    self.transport:enableExtensions(false)
    self.enabled = false
end

Npc.sameContext = equal
return Npc
