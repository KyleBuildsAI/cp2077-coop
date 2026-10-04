-- NetProbe: transport probe for the CP2077CoopNet RED4ext plugin (Phase 1 of the total sync plan).
--
-- Install: copy this file next to the mod's init.lua (CET resolves require() relative to the mod
-- folder), then in init.lua:
--
--   local NetProbe = require("netprobe")
--   registerForEvent("onInit", function()
--       NetProbe.init({ role = IS_HOST and "host" or "joiner" })  -- relay 127.0.0.1:11779 by default
--   end)
--   registerForEvent("onUpdate", function(delta) NetProbe.update(delta) end)
--   registerForEvent("onShutdown", function() NetProbe.shutdown() end)
--
-- If the file stays in a subfolder (mods\<mod>\lua\netprobe.lua) use require("lua/netprobe").
--
-- What it does while the plugin is present:
--   * connects to the relay (Game.Net_Connect / Net_ConnectRoom),
--   * sends a 30 Hz unreliable probe (seq, Net_NowMs, true x/y/z/yaw, movement state) and a 1 Hz
--     reliable probe (reliable seq plus how many probes it has sent),
--   * drains Game.Net_Poll every frame (bounded) and measures unreliable loss, reliable
--     delivery/order violations, one-way latency (both bench instances share one clock through
--     Net_NowMs) and the drain cost per frame (os.clock delta),
--   * writes AUDIT lines at 10 Hz (own true pose + latest received remote pose with its send time)
--     and STATS lines at 1 Hz into probe_audit_<role>.log in the mod folder, flushed per line.
--     CET print() goes to the buffered scripting.log, so data never relies on print.
-- Without Game.Net_Connect it logs once and every call is a no-op.
--
-- LuaJIT 2.1 allows at most 60 upvalues per function: every function here only closes over the
-- NetProbe table, and all runtime state lives in NetProbe.s.

local NetProbe = {
    VERSION = "0.1.0",
    PROTOCOL = "NP1",
    s = nil,        -- runtime state, created by init()
    warned = {},    -- one-shot log keys
}

NetProbe.DEFAULTS = {
    enabled = true,          -- false: init() only remembers the config, update() does nothing
    connect = true,          -- false: reuse a connection someone else opened
    ownPoll = true,          -- false: the caller drains Net_Poll and forwards via handleMessage()
    relayHost = "127.0.0.1",
    relayPort = 11779,
    room = "",               -- "" uses Net_Connect (relay room "default"), else Net_ConnectRoom
    role = nil,              -- "host" / "joiner"; nil reads role.txt, falls back to "local"
    unreliableChannel = 9,   -- 1..15 unreliable + sequenced
    reliableChannel = 25,    -- 16..31 reliable + ordered
    sendHz = 30,
    reliableIntervalMs = 1000,
    audit = true,            -- AUDIT lines on/off (STATS and EVENT lines are always written)
    auditHz = 10,
    auditDir = "",           -- prefix for the log file; "" = mod folder (CET io sandbox root)
    auditAppend = false,     -- false truncates the log at init
    statsIntervalMs = 1000,
    maxPollPerFrame = 64,    -- bound on Net_Poll calls per frame
    connectTimeoutMs = 1500, -- logs once if no welcome arrives in time
    poseProvider = nil,      -- function() -> x, y, z, yawDeg; default: player position + forward yaw
    stateProvider = nil,     -- function(horizontalSpeed) -> string; default: speed buckets
    drawnProvider = nil,     -- function() -> x, y, z, yawDeg of the avatar drawn for the remote player
    onOtherMessage = nil,    -- function(sender, channel, payload) for non-probe traffic (ownPoll only)
    cpuClock = nil,          -- function() -> seconds; default os.clock
    log = nil,               -- function(text); default print
}

NetProbe.LATENCY_BINS = 5000 -- 1 ms histogram bins, the last one collects everything above

-- ---------------------------------------------------------------------------------------------
-- helpers
-- ---------------------------------------------------------------------------------------------

function NetProbe.log(text)
    local s = NetProbe.s
    local sink = s ~= nil and s.cfg.log or nil
    if sink ~= nil then
        sink("[NetProbe] " .. text)
    else
        print("[NetProbe] " .. text)
    end
end

function NetProbe.logOnce(key, text)
    if NetProbe.warned[key] then
        return
    end
    NetProbe.warned[key] = true
    NetProbe.log(text)
end

-- Returns Game[name] when the native resolves, nil otherwise (CET may throw on unknown names).
function NetProbe.native(name)
    if Game == nil then
        return nil
    end
    local ok, value = pcall(function()
        return Game[name]
    end)
    if ok then
        return value
    end
    return nil
end

function NetProbe.mergeConfig(user)
    local cfg = {}
    for key, value in pairs(NetProbe.DEFAULTS) do
        cfg[key] = value
    end
    if type(user) == "table" then
        for key, value in pairs(user) do
            cfg[key] = value
        end
    end
    return cfg
end

function NetProbe.newHistogram()
    return { bins = {}, n = 0, sum = 0.0, min = nil, max = nil, negative = 0 }
end

function NetProbe.histAdd(hist, value)
    local bin = math.floor(value)
    if bin < 0 then
        hist.negative = hist.negative + 1
        bin = 0
    elseif bin >= NetProbe.LATENCY_BINS then
        bin = NetProbe.LATENCY_BINS - 1
    end
    hist.bins[bin] = (hist.bins[bin] or 0) + 1
    hist.n = hist.n + 1
    hist.sum = hist.sum + value
    if hist.min == nil or value < hist.min then
        hist.min = value
    end
    if hist.max == nil or value > hist.max then
        hist.max = value
    end
end

-- Percentile (0..1) from the 1 ms histogram, reported as the bin centre; nil when empty.
function NetProbe.histPercentile(hist, fraction)
    if hist.n == 0 then
        return nil
    end
    local rank = math.max(1, math.ceil(fraction * hist.n))
    local seen = 0
    for bin = 0, NetProbe.LATENCY_BINS - 1 do
        seen = seen + (hist.bins[bin] or 0)
        if seen >= rank then
            return bin + 0.5
        end
    end
    return hist.max
end

function NetProbe.fmtNumber(value, pattern)
    if value == nil then
        return "-"
    end
    return string.format(pattern, value)
end

function NetProbe.newPeer(id, sid)
    return {
        id = id,
        sid = sid,
        firstSeq = nil,
        lastSeq = nil,
        uRx = 0,
        uOutOfOrder = 0,
        rFirst = nil,
        rLast = nil,
        rRx = 0,
        rDup = 0,
        rGap = 0,
        rMissing = 0,
        peerUSent = 0,
        peerRSent = 0,
        lat = NetProbe.newHistogram(),
        rlat = NetProbe.newHistogram(),
        pose = nil, -- latest received pose
    }
end

-- ---------------------------------------------------------------------------------------------
-- lifecycle
-- ---------------------------------------------------------------------------------------------

-- Creates the state and connects. Returns true when the probe is active.
function NetProbe.init(userConfig)
    local cfg = NetProbe.mergeConfig(userConfig)
    local s = {
        cfg = cfg,
        active = false,
        disabledReason = nil,
        clockSource = "fallback",
        fallbackMs = os.time() * 1000.0,
        cpuClock = cfg.cpuClock or os.clock,
        sid = 0,
        role = cfg.role,
        localId = 0,
        welcomed = false,
        startMs = 0,
        file = nil,
        filePath = nil,
        nextSendMs = 0,
        nextReliableMs = 0,
        nextAuditMs = 0,
        nextStatsMs = 0,
        useq = 0,
        rseq = 0,
        uFail = 0,
        rFail = 0,
        frames = 0,
        errors = 0,
        pose = nil,      -- latest own pose {x, y, z, yaw, t}
        speedRef = nil,  -- pose used for the speed estimate
        speed = 0.0,
        state = "idle",
        peers = {},
        primary = nil,
        poll = { frames = 0, sumMs = 0.0, maxMs = 0.0, msgs = 0, maxMsgs = 0, capped = 0, slowFrames = 0 },
        auditLines = 0,
    }
    NetProbe.s = s

    if not cfg.enabled then
        s.disabledReason = "disabled by config"
        return false
    end
    if NetProbe.native("Net_Connect") == nil or NetProbe.native("Net_Send") == nil
        or NetProbe.native("Net_Poll") == nil then
        s.disabledReason = "CP2077CoopNet natives missing"
        NetProbe.logOnce("missing", "Game.Net_Connect/Net_Send/Net_Poll not found (CP2077CoopNet.dll not loaded); probe disabled")
        return false
    end

    if NetProbe.native("Net_NowMs") ~= nil then
        s.clockSource = "Net_NowMs"
    else
        NetProbe.logOnce("noclock", "Game.Net_NowMs missing; using a local fallback clock (latency is not comparable)")
    end
    s.startMs = NetProbe.nowMs()
    s.sid = math.floor(s.startMs) % 1000000000
    s.nextSendMs = s.startMs
    s.nextReliableMs = s.startMs
    s.nextAuditMs = s.startMs
    s.nextStatsMs = s.startMs + cfg.statsIntervalMs
    s.role = s.role or NetProbe.readRoleFile() or "local"
    s.active = true

    NetProbe.openAudit()
    local plugin = "?"
    local versionNative = NetProbe.native("Net_Version")
    if versionNative ~= nil then
        local ok, value = pcall(versionNative)
        if ok and value ~= nil then
            plugin = tostring(value):gsub("[%s|=]", "_") -- "CP2077CoopNet 0.1.1 proto 1" -> one token
        end
    end
    NetProbe.write(string.format(
        "SESSION t=%.3f probe=%s role=%s sid=%d clock=%s plugin=%s relay=%s:%d room=%s uch=%d rch=%d send_hz=%s audit_hz=%s",
        s.startMs, NetProbe.VERSION, s.role, s.sid, s.clockSource, plugin, cfg.relayHost, cfg.relayPort,
        cfg.room == "" and "default" or cfg.room, cfg.unreliableChannel, cfg.reliableChannel,
        tostring(cfg.sendHz), tostring(cfg.auditHz)))

    if cfg.connect then
        NetProbe.connect()
    end
    return true
end

function NetProbe.readRoleFile()
    local ok, role = pcall(function()
        local file = io.open("role.txt", "r")
        if file == nil then
            return nil
        end
        local line = file:read("*l")
        file:close()
        return line
    end)
    if ok and (role == "host" or role == "joiner") then
        return role
    end
    return nil
end

function NetProbe.connect()
    local s = NetProbe.s
    local cfg = s.cfg
    local ok, result
    if cfg.room ~= nil and cfg.room ~= "" and NetProbe.native("Net_ConnectRoom") ~= nil then
        ok, result = pcall(Game.Net_ConnectRoom, cfg.relayHost, cfg.relayPort, cfg.room)
    else
        ok, result = pcall(Game.Net_Connect, cfg.relayHost, cfg.relayPort)
    end
    s.connectAtMs = NetProbe.nowMs()
    NetProbe.event(string.format("connect %s:%d -> %s", cfg.relayHost, cfg.relayPort, tostring(ok and result)))
    if not ok or not result then
        NetProbe.logOnce("connectfail", "Net_Connect refused: " .. tostring(result))
    end
end

function NetProbe.openAudit()
    local s = NetProbe.s
    s.filePath = s.cfg.auditDir .. "probe_audit_" .. s.role .. ".log"
    local ok, file = pcall(io.open, s.filePath, s.cfg.auditAppend and "a" or "w")
    if ok and file ~= nil then
        s.file = file
    else
        NetProbe.logOnce("auditfile", "cannot open " .. s.filePath .. "; audit disabled")
    end
end

function NetProbe.write(line)
    local file = NetProbe.s.file
    if file == nil then
        return
    end
    file:write(line, "\n")
    file:flush()
end

function NetProbe.event(text)
    NetProbe.write(string.format("EVENT t=%.3f text=%s", NetProbe.nowMs(), text))
end

-- Final STATS line and file close. Safe to call more than once.
function NetProbe.shutdown()
    local s = NetProbe.s
    if s == nil or not s.active then
        return
    end
    NetProbe.writeStats(true)
    s.active = false
    if s.file ~= nil then
        s.file:close()
        s.file = nil
    end
end

function NetProbe.setAudit(enabled)
    if NetProbe.s ~= nil then
        NetProbe.s.cfg.audit = enabled and true or false
    end
end

function NetProbe.isActive()
    return NetProbe.s ~= nil and NetProbe.s.active
end

-- ---------------------------------------------------------------------------------------------
-- clock and pose
-- ---------------------------------------------------------------------------------------------

function NetProbe.nowMs()
    local s = NetProbe.s
    if s ~= nil and s.clockSource == "Net_NowMs" then
        local value = tonumber(Game.Net_NowMs())
        if value ~= nil then
            return value
        end
    end
    return s ~= nil and s.fallbackMs or os.time() * 1000.0
end

function NetProbe.defaultPose()
    local player = Game.GetPlayer()
    if player == nil then
        return nil
    end
    local position = player:GetWorldPosition()
    local forward = player:GetWorldForward()
    -- same convention as init.lua Sync.yawFromForward: (0, 1) = 0 deg, counter-clockwise positive
    return position.x, position.y, position.z, math.deg(math.atan2(-forward.x, forward.y))
end

function NetProbe.defaultState(speed)
    if speed < 0.3 then
        return "idle"
    elseif speed < 3.0 then
        return "walk"
    elseif speed < 5.75 then
        return "run"
    elseif speed < 10.0 then
        return "sprint"
    end
    return "fast"
end

-- Samples the own true pose (only on frames that send or audit) and keeps a 100 ms speed estimate.
function NetProbe.samplePose(now)
    local s = NetProbe.s
    local provider = s.cfg.poseProvider or NetProbe.defaultPose
    local ok, x, y, z, yaw = pcall(provider)
    if not ok or x == nil then
        return
    end
    local pose = s.pose or {}
    pose.x, pose.y, pose.z, pose.yaw, pose.t = x, y, z, yaw or 0.0, now
    s.pose = pose
    local ref = s.speedRef
    if ref == nil then
        s.speedRef = { x = x, y = y, t = now }
    elseif now - ref.t >= 100.0 then
        local dx, dy = x - ref.x, y - ref.y
        s.speed = math.sqrt(dx * dx + dy * dy) / ((now - ref.t) / 1000.0)
        ref.x, ref.y, ref.t = x, y, now
    end
    local stateProvider = s.cfg.stateProvider or NetProbe.defaultState
    local stateOk, state = pcall(stateProvider, s.speed)
    if stateOk and state ~= nil then
        s.state = tostring(state):gsub("[|%s]", "_")
    end
end

-- ---------------------------------------------------------------------------------------------
-- per frame
-- ---------------------------------------------------------------------------------------------

-- Call once per frame from onUpdate. Never throws.
function NetProbe.update(delta)
    local s = NetProbe.s
    if s == nil or not s.active then
        return
    end
    local ok, err = pcall(NetProbe.step, delta or 0.0)
    if not ok then
        s.errors = s.errors + 1
        if s.errors <= 5 or s.errors % 100 == 0 then
            NetProbe.log("update error #" .. s.errors .. ": " .. tostring(err))
        end
    end
end

function NetProbe.step(delta)
    local s = NetProbe.s
    local cfg = s.cfg
    s.fallbackMs = s.fallbackMs + delta * 1000.0
    s.frames = s.frames + 1
    if cfg.ownPoll then
        NetProbe.drain()
    end
    local now = NetProbe.nowMs()
    if not s.welcomed and s.connectAtMs ~= nil and now - s.connectAtMs > cfg.connectTimeoutMs then
        NetProbe.logOnce("noanswer", string.format("no relay answer from %s:%d after %d ms",
            cfg.relayHost, cfg.relayPort, cfg.connectTimeoutMs))
    end
    local auditDue = cfg.audit and now >= s.nextAuditMs
    if now >= s.nextSendMs or auditDue then
        NetProbe.samplePose(now)
    end
    if s.pose ~= nil and now >= s.nextSendMs then
        NetProbe.sendProbe(now)
        s.nextSendMs = NetProbe.nextDue(s.nextSendMs, 1000.0 / cfg.sendHz, now)
    end
    if now >= s.nextReliableMs then
        NetProbe.sendReliable(now)
        s.nextReliableMs = NetProbe.nextDue(s.nextReliableMs, cfg.reliableIntervalMs, now)
    end
    if auditDue then
        NetProbe.writeAudit(now)
        s.nextAuditMs = NetProbe.nextDue(s.nextAuditMs, 1000.0 / cfg.auditHz, now)
    end
    if now >= s.nextStatsMs then
        NetProbe.writeStats(false)
        s.nextStatsMs = NetProbe.nextDue(s.nextStatsMs, cfg.statsIntervalMs, now)
    end
end

-- Fixed-rate schedule that resynchronises after a stall instead of bursting.
function NetProbe.nextDue(due, period, now)
    local nextTime = due + period
    if nextTime <= now then
        nextTime = now + period
    end
    return nextTime
end

-- Drains up to maxPollPerFrame messages and records the cost (os.clock delta) of the whole drain.
function NetProbe.drain()
    local s = NetProbe.s
    local poll = s.poll
    local limit = s.cfg.maxPollPerFrame
    local started = s.cpuClock()
    local count = 0
    for _ = 1, limit do
        local raw = Game.Net_Poll()
        if raw == nil or raw == "" then
            break
        end
        count = count + 1
        local sender, channel, payload = raw:match("^(%d+)|(%d+)|(.*)$")
        if sender ~= nil then
            NetProbe.dispatch(tonumber(sender), tonumber(channel), payload)
        end
    end
    local costMs = (s.cpuClock() - started) * 1000.0
    poll.frames = poll.frames + 1
    poll.sumMs = poll.sumMs + costMs
    poll.msgs = poll.msgs + count
    if costMs > poll.maxMs then
        poll.maxMs = costMs
    end
    if costMs >= 0.1 then
        poll.slowFrames = poll.slowFrames + 1
    end
    if count > poll.maxMsgs then
        poll.maxMsgs = count
    end
    if count >= limit then
        poll.capped = poll.capped + 1
    end
end

function NetProbe.dispatch(sender, channel, payload)
    if NetProbe.handleMessage(sender, channel, payload) then
        return
    end
    local other = NetProbe.s.cfg.onOtherMessage
    if other ~= nil then
        other(sender, channel, payload)
    end
end

-- Feeds one polled message to the probe. Returns true when the probe consumed it.
-- Use it with ownPoll = false when init.lua already drains Net_Poll.
function NetProbe.handleMessage(sender, channel, payload)
    local s = NetProbe.s
    if s == nil or not s.active or payload == nil then
        return false
    end
    if channel == 0 and sender == 0 then
        NetProbe.onTransportEvent(payload)
        return false -- transport events are also useful to other consumers
    end
    if channel ~= s.cfg.unreliableChannel and channel ~= s.cfg.reliableChannel then
        return false
    end
    local kind, sid, seq, sent, rest = payload:match("^NP1|(%a)|(%d+)|(%d+)|([^|]+)|(.*)$")
    if kind == nil then
        return false
    end
    local now = NetProbe.nowMs()
    local peer = NetProbe.peerFor(sender, sid)
    if kind == "u" then
        NetProbe.onProbe(peer, tonumber(seq), tonumber(sent), rest, now)
    elseif kind == "r" then
        NetProbe.onReliable(peer, tonumber(seq), tonumber(sent), rest, now)
    end
    return true
end

function NetProbe.onTransportEvent(text)
    local s = NetProbe.s
    local welcomeId = text:match("^welcome (%d+)")
    if welcomeId ~= nil then
        s.localId = tonumber(welcomeId)
        s.welcomed = true
    end
    NetProbe.event(text)
end

function NetProbe.peerFor(sender, sid)
    local s = NetProbe.s
    local peer = s.peers[sender]
    if peer == nil then
        peer = NetProbe.newPeer(sender, sid)
        s.peers[sender] = peer
        NetProbe.event(string.format("probe_peer %d sid=%s", sender, sid))
    elseif peer.sid ~= sid then
        NetProbe.event(string.format("probe_peer_restart %d sid=%s->%s", sender, peer.sid, sid))
        peer = NetProbe.newPeer(sender, sid)
        s.peers[sender] = peer
    end
    if s.primary == nil or sender < s.primary then
        s.primary = sender
    end
    return peer
end

function NetProbe.onProbe(peer, seq, sentMs, rest, now)
    local x, y, z, yaw, state = rest:match("^([^|]+)|([^|]+)|([^|]+)|([^|]+)|(.*)$")
    if x == nil or seq == nil or sentMs == nil then
        return
    end
    if peer.lastSeq ~= nil and seq <= peer.lastSeq then
        -- the plugin drops stale unreliable messages, so this should stay 0
        peer.uOutOfOrder = peer.uOutOfOrder + 1
        return
    end
    if peer.firstSeq == nil then
        peer.firstSeq = seq
    end
    peer.lastSeq = seq
    peer.uRx = peer.uRx + 1
    NetProbe.histAdd(peer.lat, now - sentMs)
    local pose = peer.pose or {}
    pose.x, pose.y, pose.z, pose.yaw = tonumber(x), tonumber(y), tonumber(z), tonumber(yaw)
    pose.sentMs, pose.recvMs, pose.seq, pose.state = sentMs, now, seq, state ~= "" and state or "-"
    peer.pose = pose
end

function NetProbe.onReliable(peer, rseq, sentMs, rest, now)
    local uSent, rSent = rest:match("^(%d+)|(%d+)")
    if rseq == nil or sentMs == nil then
        return
    end
    peer.peerUSent = tonumber(uSent) or peer.peerUSent
    peer.peerRSent = tonumber(rSent) or peer.peerRSent
    if peer.rLast == nil then
        peer.rFirst = rseq
    elseif rseq <= peer.rLast then
        peer.rDup = peer.rDup + 1
        NetProbe.event(string.format("reliable_dup peer=%d seq=%d last=%d", peer.id, rseq, peer.rLast))
        return
    elseif rseq ~= peer.rLast + 1 then
        peer.rGap = peer.rGap + 1
        peer.rMissing = peer.rMissing + (rseq - peer.rLast - 1)
        NetProbe.event(string.format("reliable_gap peer=%d seq=%d last=%d", peer.id, rseq, peer.rLast))
    end
    peer.rLast = rseq
    peer.rRx = peer.rRx + 1
    NetProbe.histAdd(peer.rlat, now - sentMs)
end

function NetProbe.sendProbe(now)
    local s = NetProbe.s
    local pose = s.pose
    s.useq = s.useq + 1
    local payload = string.format("NP1|u|%d|%d|%.3f|%.3f|%.3f|%.3f|%.2f|%s",
        s.sid, s.useq, now, pose.x, pose.y, pose.z, pose.yaw, s.state)
    local ok, sent = pcall(Game.Net_Send, s.cfg.unreliableChannel, payload)
    if not ok or not sent then
        s.useq = s.useq - 1 -- refused locally (no peers yet, outbox full): not a network loss
        s.uFail = s.uFail + 1
    end
end

function NetProbe.sendReliable(now)
    local s = NetProbe.s
    s.rseq = s.rseq + 1
    local payload = string.format("NP1|r|%d|%d|%.3f|%d|%d", s.sid, s.rseq, now, s.useq, s.rseq)
    local ok, sent = pcall(Game.Net_Send, s.cfg.reliableChannel, payload)
    if not ok or not sent then
        s.rseq = s.rseq - 1
        s.rFail = s.rFail + 1
    end
end

-- ---------------------------------------------------------------------------------------------
-- log lines
-- ---------------------------------------------------------------------------------------------

function NetProbe.formatPose(x, y, z, yaw)
    return string.format("%.3f,%.3f,%.3f,%.2f", x, y, z, yaw or 0.0)
end

function NetProbe.writeAudit(now)
    local s = NetProbe.s
    if s.file == nil or s.pose == nil then
        return
    end
    local own = s.pose
    local parts = {
        string.format("AUDIT t=%.3f me=%s spd=%.2f st=%s", now, NetProbe.formatPose(own.x, own.y, own.z, own.yaw),
            s.speed, s.state),
    }
    local peer = s.primary ~= nil and s.peers[s.primary] or nil
    if peer ~= nil and peer.pose ~= nil then
        local rx = peer.pose
        parts[#parts + 1] = string.format("peer=%d rx=%s rxs=%.3f rxr=%.3f rxq=%d rxst=%s", peer.id,
            NetProbe.formatPose(rx.x, rx.y, rx.z, rx.yaw), rx.sentMs, rx.recvMs, rx.seq, rx.state)
    else
        parts[#parts + 1] = "peer=- rx=-"
    end
    local drawn = s.cfg.drawnProvider
    if drawn ~= nil then
        local ok, x, y, z, yaw = pcall(drawn)
        if ok and x ~= nil then
            parts[#parts + 1] = "drawn=" .. NetProbe.formatPose(x, y, z, yaw)
        else
            parts[#parts + 1] = "drawn=-"
        end
    end
    NetProbe.write(table.concat(parts, " "))
    s.auditLines = s.auditLines + 1
end

-- Counters for the primary peer (or zeros when nobody has sent probes yet).
function NetProbe.peerSummary(peer)
    if peer == nil then
        return { expected = 0, lost = 0, lossPct = nil, rExpected = 0 }
    end
    local expected = 0
    if peer.firstSeq ~= nil then
        expected = peer.lastSeq - peer.firstSeq + 1
    end
    local lost = expected - peer.uRx
    local rExpected = 0
    if peer.rFirst ~= nil then
        rExpected = peer.rLast - peer.rFirst + 1
    end
    return {
        expected = expected,
        lost = lost,
        lossPct = expected > 0 and (100.0 * lost / expected) or nil,
        rExpected = rExpected,
    }
end

function NetProbe.writeStats(final)
    local s = NetProbe.s
    local now = NetProbe.nowMs()
    local peer = s.primary ~= nil and s.peers[s.primary] or nil
    local sum = NetProbe.peerSummary(peer)
    local poll = s.poll
    local hist = NetProbe.histPercentile
    local lat = peer ~= nil and peer.lat or NetProbe.newHistogram()
    local rlat = peer ~= nil and peer.rlat or NetProbe.newHistogram()
    local fmt = NetProbe.fmtNumber
    local line = string.format(
        "STATS t=%.3f final=%d id=%d peer=%s frames=%d u_sent=%d u_fail=%d r_sent=%d r_fail=%d "
            .. "u_rx=%d u_exp=%d u_lost=%d loss_pct=%s u_ooo=%d u_last=%s "
            .. "r_rx=%d r_exp=%d r_dup=%d r_gap=%d r_missing=%d r_viol=%d r_last=%s peer_u_sent=%d peer_r_sent=%d "
            .. "lat_n=%d lat_min=%s lat_p50=%s lat_p95=%s lat_max=%s lat_neg=%d "
            .. "rlat_n=%d rlat_p50=%s rlat_p95=%s rlat_max=%s "
            .. "poll_frames=%d poll_avg_ms=%s poll_max_ms=%s poll_slow=%d poll_msgs=%d poll_max_msgs=%d poll_capped=%d "
            .. "audit_lines=%d errors=%d",
        now, final and 1 or 0, s.localId, peer ~= nil and tostring(peer.id) or "-", s.frames,
        s.useq, s.uFail, s.rseq, s.rFail,
        peer ~= nil and peer.uRx or 0, sum.expected, sum.lost, fmt(sum.lossPct, "%.3f"),
        peer ~= nil and peer.uOutOfOrder or 0, fmt(peer ~= nil and peer.lastSeq or nil, "%d"),
        peer ~= nil and peer.rRx or 0, sum.rExpected, peer ~= nil and peer.rDup or 0, peer ~= nil and peer.rGap or 0,
        peer ~= nil and peer.rMissing or 0, peer ~= nil and (peer.rDup + peer.rGap) or 0,
        fmt(peer ~= nil and peer.rLast or nil, "%d"),
        peer ~= nil and peer.peerUSent or 0, peer ~= nil and peer.peerRSent or 0,
        lat.n, fmt(lat.min, "%.3f"), fmt(hist(lat, 0.5), "%.1f"), fmt(hist(lat, 0.95), "%.1f"), fmt(lat.max, "%.3f"),
        lat.negative,
        rlat.n, fmt(hist(rlat, 0.5), "%.1f"), fmt(hist(rlat, 0.95), "%.1f"), fmt(rlat.max, "%.3f"),
        poll.frames, fmt(poll.frames > 0 and poll.sumMs / poll.frames or nil, "%.5f"), fmt(poll.maxMs, "%.3f"),
        poll.slowFrames, poll.msgs, poll.maxMsgs, poll.capped,
        s.auditLines, s.errors)
    NetProbe.write(line)
end

-- One line for the coop panel / console.
function NetProbe.summary()
    local s = NetProbe.s
    if s == nil then
        return "NetProbe: not initialised"
    end
    if not s.active then
        return "NetProbe: off (" .. tostring(s.disabledReason or "stopped") .. ")"
    end
    local peer = s.primary ~= nil and s.peers[s.primary] or nil
    local sum = NetProbe.peerSummary(peer)
    local poll = s.poll
    return string.format("NetProbe %s id=%d peer=%s loss=%s%% lat_p50=%sms rel_viol=%d poll=%sms/frame",
        s.role, s.localId, peer ~= nil and tostring(peer.id) or "-", NetProbe.fmtNumber(sum.lossPct, "%.2f"),
        NetProbe.fmtNumber(peer ~= nil and NetProbe.histPercentile(peer.lat, 0.5) or nil, "%.0f"),
        peer ~= nil and (peer.rDup + peer.rGap) or 0,
        NetProbe.fmtNumber(poll.frames > 0 and poll.sumMs / poll.frames or nil, "%.4f"))
end

return NetProbe
