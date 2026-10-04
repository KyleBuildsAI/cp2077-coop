-- CoopNetCheck: Phase 1 go/no-go probe for the CP2077CoopNet RED4ext plugin.
--
-- Standalone CET mod (does not touch CP2077Coop). Once gameplay is loaded it:
--   * checks the plugin natives are visible to CET (Game.Net_*)
--   * connects to the CP2077CoopNet relay at 127.0.0.1:11779
--   * sends 30 Hz unreliable probes (channel 1) and 2 Hz reliable probes (channel 16)
--   * drains Net_Poll every frame and measures loss, reliable ordering and poll cost
--   * writes everything to coopnet_check.log in this folder (flushed per line)

local Check = {
    LOG = "coopnet_check.log",
    HOST = "127.0.0.1",
    PORT = 11779,
    UNRELIABLE = 1,
    RELIABLE = 16,
    SEND_INTERVAL = 1.0 / 30.0,
    RELIABLE_INTERVAL = 0.5,
    REPORT_INTERVAL = 5.0,
    MAX_POLL_PER_FRAME = 200,

    state = "waiting",
    sendTimer = 0.0,
    reliableTimer = 0.0,
    reportTimer = 0.0,
    sentUnreliable = 0,
    sentReliable = 0,
    receivedUnreliable = 0,
    receivedReliable = 0,
    peerMaxUnreliable = 0,
    peerFirstUnreliable = nil,
    lastReliableSeq = 0,
    reliableOrderErrors = 0,
    reliableDuplicates = 0,
    pollFrames = 0,
    pollCostSum = 0.0,
    pollCostMax = 0.0,
    events = 0
}


function Check.log(line)
    local file = io.open(Check.LOG, "a")
    if file ~= nil then
        file:write(os.date("%H:%M:%S ") .. line .. "\n")
        file:close()
    end
    print("[CoopNetCheck] " .. line)
end


function Check.gameplayLoaded()
    local player = Game.GetPlayer()
    if player == nil or not player:IsAttached() then
        return false
    end
    local requests = Game.GetSystemRequestsHandler()
    return requests == nil or not requests:IsPreGame()
end


function Check.handle(message)
    local sender, channel, payload = string.match(message, "^(%d+)|(%d+)|(.*)$")
    if sender == nil then
        Check.log("unparsed message: " .. message)
        return
    end
    channel = tonumber(channel)

    if channel == 0 then
        Check.events = Check.events + 1
        Check.log("transport event from " .. sender .. ": " .. payload)
        return
    end

    local kind, seq = string.match(payload, "^(%a) (%d+)")
    seq = tonumber(seq)

    if channel == Check.UNRELIABLE and kind == "U" then
        Check.receivedUnreliable = Check.receivedUnreliable + 1
        Check.peerFirstUnreliable = Check.peerFirstUnreliable or seq
        Check.peerMaxUnreliable = math.max(Check.peerMaxUnreliable, seq)
    elseif channel == Check.RELIABLE and kind == "R" then
        Check.receivedReliable = Check.receivedReliable + 1
        if seq == Check.lastReliableSeq then
            Check.reliableDuplicates = Check.reliableDuplicates + 1
        elseif Check.lastReliableSeq > 0 and seq ~= Check.lastReliableSeq + 1 then
            Check.reliableOrderErrors = Check.reliableOrderErrors + 1
            Check.log(string.format("reliable out of order: got %d after %d", seq, Check.lastReliableSeq))
        end
        Check.lastReliableSeq = seq
    end
end


function Check.poll()
    local started = os.clock()
    local count = 0
    while count < Check.MAX_POLL_PER_FRAME do
        local message = Game.Net_Poll()
        if message == nil or message == "" then
            break
        end
        count = count + 1
        Check.handle(message)
    end
    local cost = (os.clock() - started) * 1000.0
    Check.pollFrames = Check.pollFrames + 1
    Check.pollCostSum = Check.pollCostSum + cost
    Check.pollCostMax = math.max(Check.pollCostMax, cost)
end


function Check.report()
    local expected = 0
    if Check.peerFirstUnreliable ~= nil then
        expected = Check.peerMaxUnreliable - Check.peerFirstUnreliable + 1
    end
    local loss = expected > 0 and 100.0 * (1.0 - Check.receivedUnreliable / expected) or 0.0
    local stats = ""
    local ok, value = pcall(function() return Game.Net_Stats() end)
    if ok and value ~= nil then
        stats = string.sub(value, 1, 400)
    end
    Check.log(string.format(
        "REPORT id=%s sentU=%d sentR=%d recvU=%d/%d lossU=%.1f%% recvR=%d orderErr=%d dup=%d events=%d poll_avg_ms=%.4f poll_max_ms=%.4f stats=%s",
        tostring(Game.Net_LocalId()), Check.sentUnreliable, Check.sentReliable, Check.receivedUnreliable, expected, loss,
        Check.receivedReliable, Check.reliableOrderErrors, Check.reliableDuplicates, Check.events,
        Check.pollFrames > 0 and Check.pollCostSum / Check.pollFrames or 0.0, Check.pollCostMax, stats))
    Check.pollCostMax = 0.0
end


registerForEvent("onInit", function()
    local file = io.open(Check.LOG, "w")
    if file ~= nil then
        file:close()
    end
    local ok, visible = pcall(function() return Game.Net_Poll ~= nil and Game.Net_Connect ~= nil end)
    Check.log("onInit: CP2077CoopNet natives visible to CET = " .. tostring(ok and visible))
    if ok and visible then
        local versionOk, version = pcall(function() return Game.Net_Version() end)
        Check.log("Net_Version: " .. (versionOk and tostring(version) or "not available (plugin 0.1.0)"))
    end
end)


registerForEvent("onUpdate", function(delta)
    if Check.state == "waiting" then
        if not Check.gameplayLoaded() then
            return
        end
        if Game.Net_Connect == nil then
            Check.log("NO-GO: Game.Net_Connect missing (plugin not loaded or natives not registered)")
            Check.state = "dead"
            return
        end
        local ok, result = pcall(function() return Game.Net_Connect(Check.HOST, Check.PORT) end)
        Check.log(string.format("Net_Connect(%s, %d) -> ok=%s result=%s", Check.HOST, Check.PORT, tostring(ok), tostring(result)))
        Check.state = (ok and result) and "running" or "dead"
        return
    end

    if Check.state ~= "running" then
        return
    end

    local ok, err = pcall(function()
        Check.poll()

        Check.sendTimer = Check.sendTimer + delta
        if Check.sendTimer >= Check.SEND_INTERVAL then
            Check.sendTimer = math.min(Check.sendTimer - Check.SEND_INTERVAL, Check.SEND_INTERVAL)
            Check.sentUnreliable = Check.sentUnreliable + 1
            Game.Net_Send(Check.UNRELIABLE, "U " .. Check.sentUnreliable)
        end

        Check.reliableTimer = Check.reliableTimer + delta
        if Check.reliableTimer >= Check.RELIABLE_INTERVAL then
            Check.reliableTimer = 0.0
            Check.sentReliable = Check.sentReliable + 1
            Game.Net_Send(Check.RELIABLE, "R " .. Check.sentReliable)
        end

        Check.reportTimer = Check.reportTimer + delta
        if Check.reportTimer >= Check.REPORT_INTERVAL then
            Check.reportTimer = 0.0
            Check.report()
        end
    end)

    if not ok then
        Check.log("ERROR in probe loop: " .. tostring(err))
        Check.state = "dead"
    end
end)


registerForEvent("onShutdown", function()
    if Check.state == "running" then
        Check.report()
        pcall(function() Game.Net_Disconnect() end)
        Check.log("shutdown: disconnected")
    end
end)
