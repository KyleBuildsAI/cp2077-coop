// Convenience wrappers around the Net_* natives.

public struct CoopNetMessage {
    public let sender: Int32;
    public let channel: Int32;
    public let payload: String;
}

// Default channel for world snapshots (unreliable, newest wins).
public static func CoopNet_SnapshotChannel() -> Int32 {
    return 1;
}

// Default channel for gameplay events (reliable, ordered).
public static func CoopNet_EventChannel() -> Int32 {
    return 16;
}

public static func CoopNet_IsReliableChannel(channel: Int32) -> Bool {
    return channel >= 16 && channel <= 31;
}

// Splits "<sender>|<channel>|<payload>". Returns false for "" (empty queue) or malformed input.
public static func CoopNet_ParseMessage(raw: String, out message: CoopNetMessage) -> Bool {
    let senderText: String;
    let rest: String;
    if !StrSplitFirst(raw, "|", senderText, rest) {
        return false;
    }
    let channelText: String;
    let payload: String;
    if !StrSplitFirst(rest, "|", channelText, payload) {
        return false;
    }
    message.sender = StringToInt(senderText);
    message.channel = StringToInt(channelText);
    message.payload = payload;
    return true;
}

// Drains up to maxMessages queued messages in arrival order (call once per frame).
public static func CoopNet_PollAll(maxMessages: Int32) -> array<CoopNetMessage> {
    let messages: array<CoopNetMessage>;
    let count = 0;
    while count < maxMessages {
        let raw = Net_Poll();
        if StrLen(raw) == 0 {
            break;
        }
        let message: CoopNetMessage;
        if CoopNet_ParseMessage(raw, message) {
            ArrayPush(messages, message);
        }
        count += 1;
    }
    return messages;
}

// Milliseconds elapsed since a Net_NowMs() stamp, as a Float (fine for spans; a Float cannot hold
// the absolute epoch value precisely, so keep absolute stamps as Double). Double literals need "d".
public static func CoopNet_ElapsedMs(startMs: Double) -> Float {
    return Cast<Float>(Net_NowMs() - startMs);
}

// Calls Net_Version and Net_NowMs from redscript, which proves the natives resolve on the
// redscript side too. From the CET console: print(Game.CoopNet_SelfTest())
public static func CoopNet_SelfTest() -> String {
    let first = Net_NowMs();
    let second = Net_NowMs();
    let clockOk = first > 1700000000000.0d && second >= first;
    let verdict = clockOk ? "clock ok" : "clock BAD";
    return "redscript ok: " + Net_Version() + ", Net_NowMs=" + ToString(second) + ", " + verdict;
}
