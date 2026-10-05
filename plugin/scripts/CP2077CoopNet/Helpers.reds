// Convenience wrappers around the Net_* natives: message parsing, channel ids, protocol v2 roles,
// player flag bits, pose parsing for Net_SampleRemote, and a self test.

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

// ---- protocol v2 ---------------------------------------------------------------------------------

// The relay_v2.py port (it serves v1 CP1 clients on the same port).
public static func CoopNet_DefaultPort() -> Int32 {
    return 11778;
}

// Roles for Net_ConnectV2. Any: host when the room has none yet, joiner otherwise.
public static func CoopNet_RoleAny() -> Int32 {
    return 0;
}

public static func CoopNet_RoleHost() -> Int32 {
    return 1;
}

public static func CoopNet_RoleJoiner() -> Int32 {
    return 2;
}

public static func CoopNet_RoleSpectator() -> Int32 {
    return 3;
}

// Player flag bits for Net_PushPlayer and CoopNetPose.flags (protocol v2 PlayerFlag). The weapon
// class sits in bits 8..11; DRIVING (32) needs a vehicle block and is not supported yet.
public static func CoopNet_FlagCrouch() -> Int32 {
    return 1;
}

public static func CoopNet_FlagWeaponDrawn() -> Int32 {
    return 2;
}

public static func CoopNet_FlagAiming() -> Int32 {
    return 4;
}

public static func CoopNet_FlagFiring() -> Int32 {
    return 8;
}

public static func CoopNet_FlagInVehicle() -> Int32 {
    return 16;
}

public static func CoopNet_FlagSprinting() -> Int32 {
    return 64;
}

public static func CoopNet_FlagReloading() -> Int32 {
    return 128;
}

public static func CoopNet_FlagDead() -> Int32 {
    return 4096;
}

public static func CoopNet_FlagInCombat() -> Int32 {
    return 8192;
}

// Set on the first snapshot after a teleport: the receiver does not interpolate across it.
public static func CoopNet_FlagTeleported() -> Int32 {
    return 32768;
}

// Net_PushPlayer with game vectors. W components are ignored.
public static func CoopNet_PushPlayer(position: Vector4, yaw: Float, pitch: Float, velocity: Vector4, moveState: Int32, flags: Int32, health: Int32) -> Bool {
    return Net_PushPlayer(position.X, position.Y, position.Z, yaw, pitch, velocity.X, velocity.Y, velocity.Z, moveState, flags, health);
}

// A remote player at render time, from Net_SampleRemote.
public struct CoopNetPose {
    public let mode: String; // interpolated, extrapolated (past the newest sample), held (extrapolation capped), early
    public let x: Float;
    public let y: Float;
    public let z: Float;
    public let yaw: Float;   // degrees, 0..360
    public let pitch: Float; // degrees
    public let vx: Float;    // m/s
    public let vy: Float;
    public let vz: Float;
    public let moveState: Int32;
    public let flags: Int32;
    public let health: Int32;
    public let delayMs: Float; // how far in the past this is drawn
    public let aheadMs: Float; // past the newest sample (> 0 while extrapolating)
}

// Parses a Net_SampleRemote string. False for "" (nothing received from that player yet) or
// malformed input.
public static func CoopNet_ParsePose(raw: String, out pose: CoopNetPose) -> Bool {
    let fields = StrSplit(raw, " ");
    if ArraySize(fields) != 14 {
        return false;
    }
    pose.mode = fields[0];
    pose.x = StringToFloat(fields[1]);
    pose.y = StringToFloat(fields[2]);
    pose.z = StringToFloat(fields[3]);
    pose.yaw = StringToFloat(fields[4]);
    pose.pitch = StringToFloat(fields[5]);
    pose.vx = StringToFloat(fields[6]);
    pose.vy = StringToFloat(fields[7]);
    pose.vz = StringToFloat(fields[8]);
    pose.moveState = StringToInt(fields[9]);
    pose.flags = StringToInt(fields[10]);
    pose.health = StringToInt(fields[11]);
    pose.delayMs = StringToFloat(fields[12]);
    pose.aheadMs = StringToFloat(fields[13]);
    return true;
}

// The remote player <peer> at render time. Call once per frame and peer.
public static func CoopNet_SampleRemote(peer: Int32, out pose: CoopNetPose) -> Bool {
    return CoopNet_ParsePose(Net_SampleRemote(peer), pose);
}

// Calls Net_Version, Net_NowMs and Net_SampleRemote from redscript and parses a fixed pose, which
// proves the natives resolve and the string helpers work on the redscript side too.
// From the CET console: print(Game.CoopNet_SelfTest())
public static func CoopNet_SelfTest() -> String {
    let first = Net_NowMs();
    let second = Net_NowMs();
    let clockOk = first > 1700000000000.0d && second >= first;
    let verdict = clockOk ? "clock ok" : "clock BAD";
    let pose: CoopNetPose;
    let parsed = CoopNet_ParsePose("interpolated -1234.500 20.250 7.000 90.00 -3.50 6.00 0.00 0.00 2 2 230 120.0 -15.5", pose);
    let poseOk = parsed && pose.x == -1234.5 && pose.y == 20.25 && pose.moveState == 2 && pose.health == 230 && pose.aheadMs == -15.5;
    let sampleVerdict = poseOk ? "pose parse ok" : "pose parse BAD";
    let remote = Net_SampleRemote(1);
    return "redscript ok: " + Net_Version() + ", Net_NowMs=" + ToString(second) + ", " + verdict + ", " + sampleVerdict + ", Net_SampleRemote(1)='" + remote + "'";
}
