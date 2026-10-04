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
