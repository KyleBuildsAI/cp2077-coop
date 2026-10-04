// Global natives registered by red4ext/plugins/CP2077CoopNet/CP2077CoopNet.dll.
// The same functions are callable from CET Lua as Game.Net_Connect(...), Game.Net_Poll(), ...
//
// The plugin speaks protocol v2 to relay_v2.py (default port 11778), which serves v1 CP1 clients on
// the same port.
//
// Channels: 1..15 unreliable, newest wins (an older message than one already delivered on that
//           channel is dropped), 16..31 reliable + ordered (resent until acknowledged).
// Payloads: at most 1000 bytes of UTF-8 without NUL.
// Net_Poll returns "" when nothing is queued, otherwise "<senderId>|<channel>|<payload>".
// Transport events arrive with sender 0 and channel 0, e.g. "0|0|welcome 2 host",
// "0|0|peer_join 1 joiner", "0|0|no_answer" (no reply to the handshake within 1.5 s).

public static native func Net_Connect(host: String, port: Int32) -> Bool
public static native func Net_ConnectRoom(host: String, port: Int32, room: String) -> Bool
// room: 1..32 of A-Z a-z 0-9 _ -; key: the room password ("" for none, only its hash is sent);
// role: 0 any (host if the room has none), 1 host, 2 joiner, 3 spectator.
public static native func Net_ConnectV2(host: String, port: Int32, room: String, key: String, role: Int32) -> Bool
public static native func Net_Disconnect() -> Void
public static native func Net_Send(channel: Int32, payload: String) -> Bool
public static native func Net_SendTo(peer: Int32, channel: Int32, payload: String) -> Bool
public static native func Net_Poll() -> String
public static native func Net_Stats() -> String
public static native func Net_LocalId() -> Int32

// Wall clock: milliseconds since the Unix epoch (UTC) from GetSystemTimePreciseAsFileTime, with a
// sub-millisecond fraction. Double rather than Int64 because CET turns Int64 into LuaJIT cdata.
// Two game instances on one PC share this clock, so their log stamps can be compared directly.
public static native func Net_NowMs() -> Double

// "CP2077CoopNet <semver> proto <wire major>.<wire minor>", e.g. "CP2077CoopNet 0.2.0-alpha.3 proto 2.1".
public static native func Net_Version() -> String

// The local player for the next PLAYER_SNAPSHOT (call at 30 Hz; the newest call wins). Position in
// metres, yaw and pitch in degrees, velocity in m/s, moveState 0..13 (idle, walk, run, sprint,
// crouch idle, crouch move, jump, fall, slide, swim, vehicle, dead, ladder, dodge), flags the v2
// player flag bits (DRIVING is not supported yet), health 0..255. False until the session is up and
// the relay clock is synced, or for an invalid state.
public static native func Net_PushPlayer(x: Float, y: Float, z: Float, yaw: Float, pitch: Float, vx: Float, vy: Float, vz: Float, moveState: Int32, flags: Int32, health: Int32) -> Bool

// The remote player <peer> at render time (100-150 ms in the past, extrapolated over gaps):
// "<mode> <x> <y> <z> <yaw> <pitch> <vx> <vy> <vz> <moveState> <flags> <health> <delayMs> <aheadMs>",
// mode interpolated|extrapolated|held|early, or "" when that player has sent nothing yet. Call once
// per frame and peer; CoopNet_SampleRemote (Helpers.reds) parses it.
public static native func Net_SampleRemote(peer: Int32) -> String
