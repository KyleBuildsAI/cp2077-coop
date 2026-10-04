-- Single controlled test actor; no CET registration, native connection or polling.
-- Integration must provide the authenticated peer and host session epoch from the
-- sole transport owner. Nothing runs unless enabled=true is explicitly supplied.
local TestNpc = {}
TestNpc.__index = TestNpc
TestNpc.VERSION = "0.1.2"
-- Reserved by the main adapter's optional extension dispatcher.
TestNpc.RELIABLE_CHANNEL, TestNpc.POSE_CHANNEL = 20, 2
TestNpc.INTERVAL, TestNpc.RETRY, TestNpc.STALE, TestNpc.SPAWN_TIMEOUT = 0.1, 0.25, 3, 5
TestNpc.PLACEMENT_TOLERANCE = 2.0
TestNpc.MOVE_TIMEOUT, TestNpc.MOVE_DISTANCE, TestNpc.MOVE_YAW = 2.0, 0.02, 0.5

local function finite(n)
    return type(n) == "number" and n == n and n > -math.huge and n < math.huge
end
local function integer(n, min, max)
    return finite(n) and n == math.floor(n) and n >= min and n <= max
end
local function epoch(value)
    return type(value) == "string" and #value > 0 and #value <= 40 and value:match("^%d+$")
end
local function validPose(p)
    return type(p) == "table" and finite(p.x) and math.abs(p.x) <= 100000
        and finite(p.y) and math.abs(p.y) <= 100000 and finite(p.z) and math.abs(p.z) <= 100000
        and finite(p.yaw) and math.abs(p.yaw) <= 360
end
local function copy(p) return {x=p.x, y=p.y, z=p.z, yaw=p.yaw} end
local function tokens(message)
    local values = {}
    for value in (message .. "|"):gmatch("(.-)|") do values[#values+1] = value end
    return values
end
local function poseText(p)
    return string.format("%.3f|%.3f|%.3f|%.3f", p.x, p.y, p.z, p.yaw)
end

-- entity: spawn(pose)->bool (request accepted); read()->actual pose/nil;
-- move(pose)->bool; clear(). send(reliable, message)->true only on acceptance.
function TestNpc.new(options)
    options = options or {}
    local self = setmetatable({}, TestNpc)
    self.enabled = options.enabled == true
    self.role, self.epoch, self.peer = options.role, options.epoch, options.peer
    self.entity, self.send, self.log = options.entity, options.send, options.log
    self.now, self.serial, self.tombstone = 0, 0, 0
    self.sent, self.received, self.rejected, self.expired = 0, 0, 0, 0
    self.spawnFailures = 0
    self.moveRequests, self.moveExpiries = 0, 0
    if self.enabled then
        assert(self.role == "host" or self.role == "joiner", "explicit role required")
        assert(epoch(self.epoch), "host session epoch required")
        assert(integer(self.peer, 1, 4294967295), "authenticated peer required")
        assert(type(self.send) == "function" and type(self.entity) == "table", "callbacks required")
        for _, name in ipairs({"spawn", "read", "move", "clear"}) do
            assert(type(self.entity[name]) == "function", "entity callback " .. name .. " required")
        end
        self.entity.clear() -- discard only this harness's temporary tag after reload
    end
    return self
end

function TestNpc:emit(reliable, kind, id, tail)
    local message = "NT1|" .. self.epoch .. "|" .. kind .. "|" .. id .. (tail and "|" .. tail or "")
    if self.send(reliable, message) == true then
        self.sent = self.sent + 1
        return true
    end
    return false
end

function TestNpc:spawn(pose)
    if not self.enabled or self.role ~= "host" or self.actor or not validPose(pose) then return false end
    self.serial = self.serial + 1
    if self.serial > 2147483647 then return false end -- require new session rather than wrap IDs
    self.actor = {id=self.serial, target=copy(pose), started=self.now, nextRetry=0, nextState=0, seq=0, awaitClear=true}
    return true
end

function TestNpc:move(pose)
    if not self.enabled or self.role ~= "host" or not self.actor or self.actor.stopping
        or not validPose(pose) then return false end
    self.actor.target = copy(pose)
    return true
end

function TestNpc:stop()
    if not self.enabled or self.role ~= "host" or not self.actor then return false end
    self.entity.clear()
    self.localClearPending = true
    self.actor.stopping, self.actor.nextRetry = true, 0
    return true
end

function TestNpc:present()
    if self.entity.exists then return self.entity.exists() == true end
    return self.entity.read() ~= nil
end

function TestNpc:reject()
    self.rejected = self.rejected + 1
    return false
end

function TestNpc:note(kind, a, p)
    if self.log then
        self.log("[NPC TEST] " .. kind .. " role=" .. self.role .. " epoch=" .. self.epoch .. " id=" .. a.id ..
            (p and string.format(" x=%.3f y=%.3f z=%.3f yaw=%.3f", p.x, p.y, p.z, p.yaw) or ""))
    end
end

function TestNpc:failSpawn(reason)
    local a = self.actor
    if not a then return end
    self.spawnFailures, self.lastFailure = self.spawnFailures + 1, reason
    self:note("spawn_failed reason=" .. reason, a)
    self.entity.clear()
    self.localClearPending = true
    if self.role == "host" then self:stop()
    else self.tombstone, self.actor = math.max(self.tombstone, a.id), nil end
end

function TestNpc:updateMove(a, actual)
    if a.movePending then
        local state = self.entity.moveState and self.entity.moveState() or -1
        if state >= 0 and state <= 2 then
            if not a.moveCancelRequested and self.now-a.moveStarted >= self.MOVE_TIMEOUT then
                if self.entity.cancelMove then self.entity.cancelMove() end
                a.moveCancelRequested = true
                self.moveExpiries = self.moveExpiries + 1
                self:note("move_cancel_requested reason=pending_timeout", a)
            end
            return -- never overlap even if cancellation is delayed or ineffective
        end
        a.movePending, a.moveCancelRequested = false, false
    end
    if self.now < (a.nextMove or 0) then return end
    local dx,dy,dz = actual.x-a.target.x, actual.y-a.target.y, actual.z-a.target.z
    local yaw = (actual.yaw-a.target.yaw+180)%360-180
    if dx*dx+dy*dy+dz*dz <= self.MOVE_DISTANCE*self.MOVE_DISTANCE and math.abs(yaw) <= self.MOVE_YAW then return end
    a.nextMove = self.now + self.INTERVAL
    if self.entity.move(a.target) == true then
        self.moveRequests = self.moveRequests + 1
        a.movePending, a.moveStarted = self.entity.moveState ~= nil, self.now
    end
end

function TestNpc:receive(sender, reliable, message)
    if not self.enabled then return false end
    if sender ~= self.peer or type(reliable) ~= "boolean" or type(message) ~= "string" or #message > 256 then return self:reject() end
    local f = tokens(message)
    local kind, id = f[3], tonumber(f[4])
    if f[1] ~= "NT1" or f[2] ~= self.epoch or not integer(id, 1, 2147483647) then return self:reject() end
    local a = self.actor
    if self.role == "host" then
        if not reliable or #f ~= 4 or not a or a.id ~= id then return self:reject() end
        if kind == "A" and not a.stopping then
            a.ack = true
        elseif kind == "X" and a.stopping then
            self.actor = nil
        else return self:reject() end
    elseif kind == "B" then
        local p = {x=tonumber(f[5]), y=tonumber(f[6]), z=tonumber(f[7]), yaw=tonumber(f[8])}
        if not reliable or #f ~= 8 or not validPose(p) or id <= self.tombstone then return self:reject() end
        if a and id < a.id then return self:reject() end
        if not a or id > a.id then
            self.entity.clear()
            if a then self.tombstone = math.max(self.tombstone, a.id) end
            a = {id=id, target=p, started=self.now, last=self.now, seq=0, nextAck=0, awaitClear=true}
            self.actor = a
        end
        a.last = self.now
        a.nextAck = 0 -- retry ACK for duplicate bind without replacing or rewinding actor
    elseif kind == "D" then
        if not reliable or #f ~= 4 then return self:reject() end
        self.tombstone = math.max(self.tombstone, id)
        self.deleteNeedsClear = not a or a.id <= id
        if self.deleteNeedsClear then self.entity.clear(); self.actor = nil end
        self.pendingDeleteAck = id
    elseif kind == "S" then
        local seq = tonumber(f[5])
        local p = {x=tonumber(f[6]), y=tonumber(f[7]), z=tonumber(f[8]), yaw=tonumber(f[9])}
        if reliable or #f ~= 9 or not a or id ~= a.id or not integer(seq, 1, 2147483647)
            or seq <= a.seq or not validPose(p) then return self:reject() end
        a.seq, a.target, a.last = seq, p, self.now
    else return self:reject() end
    self.received = self.received + 1
    return true
end

function TestNpc:update(dt)
    if not self.enabled or not finite(dt) or dt < 0 then return end
    self.now = self.now + dt
    if self.localClearPending then
        self.entity.clear()
        if not self:present() then self.localClearPending = false end
    end
    if self.pendingDeleteAck then
        if self.deleteNeedsClear then self.entity.clear() end
        if (not self.deleteNeedsClear or not self:present()) and self:emit(true, "X", self.pendingDeleteAck) then
            self.pendingDeleteAck, self.deleteNeedsClear = nil, false
        end
    end
    local a = self.actor
    if not a then return end
    if a.stopping then
        if self.now >= a.nextRetry then self:emit(true, "D", a.id); a.nextRetry = self.now + self.RETRY end
        return
    end
    if self.role == "host" and not a.ack and self.now - a.started > self.SPAWN_TIMEOUT * 2 then
        self:failSpawn("peer placement acknowledgement timeout")
        return
    end
    if self.role == "joiner" and self.now - a.last > self.STALE then
        self.entity.clear()
        self.localClearPending = true
        self.tombstone, self.actor, self.expired = math.max(self.tombstone, a.id), nil, self.expired + 1
        return
    end
    -- DeleteTagged is asynchronous in the engine. Never mistake the previous
    -- incarnation for the newly bound actor. The bridge tracks owned IDs even
    -- after Codeware drops a tag, until population/attachment cleanup completes.
    if a.awaitClear then
        self.entity.clear()
        if self:present() then
            if self.now - a.started > self.SPAWN_TIMEOUT then self:failSpawn("previous actor cleanup timeout") end
            return
        end
        a.awaitClear = false
    end
    local actual = self.entity.read()
    if not a.placed or not validPose(actual) then
        if self.now - a.started > self.SPAWN_TIMEOUT then
            self:failSpawn(a.requested and "initial attachment/placement timeout" or "spawn request timeout")
            return
        end
        if not a.requested and self.now >= (a.nextSpawn or 0) then
            a.requested = self.entity.spawn(a.target) == true
            if a.requested then a.spawnPose = copy(a.target); self:note("spawn_requested", a, a.spawnPose) end
            a.nextSpawn = self.now + self.RETRY
        end
        -- GetTagged can expose a registered NPC with a finite (0,0,0) transform
        -- before the engine has attached/placed it. Sending movement then can
        -- interfere with placement. Only the measured spawn location proves this
        -- incarnation ready; newer network targets do not redefine that location.
        if not a.requested or not validPose(actual) then a.waitReason = "attachment"; return end
        local dx, dy, dz = actual.x-a.spawnPose.x, actual.y-a.spawnPose.y, actual.z-a.spawnPose.z
        a.placementError = math.sqrt(dx*dx+dy*dy+dz*dz)
        if a.placementError > self.PLACEMENT_TOLERANCE then a.waitReason = "initial placement"; return end
        a.placed, a.waitReason = true, nil
        self:note("spawn_placed", a, actual)
    end
    -- This slice mirrors transforms only. No autonomous AI, navmesh animation,
    -- combat, health or quest-state synchronization is promised by this call.
    self:updateMove(a, actual)
    actual = self.entity.read()
    if not validPose(actual) then return end
    if self.role == "host" then
        if not a.ack and self.now >= a.nextRetry then
            if self:emit(true, "B", a.id, poseText(actual)) and not a.bindLogged then
                a.bindLogged = true; self:note("first_bind_sent", a, actual)
            end
            a.nextRetry = self.now + self.RETRY
        end
        if self.now >= a.nextState then
            a.seq = a.seq + 1
            if a.seq > 2147483647 then self:stop(); return end
            self:emit(false, "S", a.id, a.seq .. "|" .. poseText(actual))
            a.nextState = self.now + self.INTERVAL
        end
    elseif self.now >= a.nextAck then
        if self:emit(true, "A", a.id) then a.nextAck = math.huge else a.nextAck = self.now + self.RETRY end
    end
end

function TestNpc:shutdown()
    if not self.enabled then return end
    if self.role == "host" and self.actor then self:emit(true, "D", self.actor.id) end
    self.entity.clear()
    self.actor, self.pendingDeleteAck, self.enabled = nil, nil, false
end

-- Optional CET entity bridge. Caller supplies the current PlayerPuppet each call,
-- so this module retains no game-object references over session boundaries.
function TestNpc.cetEntity(playerProvider)
    return {
        spawn=function(p)
            local player = playerProvider()
            return player and player:CP2077Coop_TestNpcSpawn(p.x, p.y, p.z, p.yaw) or false
        end,
        move=function(p)
            local player = playerProvider()
            return player and player:CP2077Coop_TestNpcMove(p.x, p.y, p.z, p.yaw) or false
        end,
        moveState=function()
            local player = playerProvider()
            return player and player:CP2077Coop_TestNpcMoveState() or -1
        end,
        cancelMove=function()
            local player = playerProvider()
            if player then player:CP2077Coop_TestNpcCancelMove() end
        end,
        read=function()
            local player = playerProvider()
            local actor = player and player:CP2077Coop_TestNpcGet()
            if not actor or not actor:IsAttached() or actor:IsDead() then return nil end
            local p = actor:GetWorldPosition()
            return {x=p.x, y=p.y, z=p.z, yaw=actor:GetWorldYaw()}
        end,
        exists=function()
            local player = playerProvider()
            return player ~= nil and player:CP2077Coop_TestNpcExists() == true
        end,
        clear=function()
            local player = playerProvider()
            if player then player:CP2077Coop_TestNpcClear() end
        end,
    }
end

return TestNpc
