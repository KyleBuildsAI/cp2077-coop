// Global natives registered by red4ext/plugins/CP2077CoopNet/CP2077CoopNet.dll.
// The same functions are callable from CET Lua as Game.Net_Connect(...), Game.Net_Poll(), ...
//
// Channels: 1..15 unreliable + sequenced (snapshots; stale ones are dropped),
//           16..31 reliable + ordered (events; resent until acknowledged).
// Net_Poll returns "" when nothing is queued, otherwise "<senderId>|<channel>|<payload>".
// Transport events arrive with sender 0 and channel 0, e.g. "0|0|peer_join 2".

public static native func Net_Connect(host: String, port: Int32) -> Bool
public static native func Net_ConnectRoom(host: String, port: Int32, room: String) -> Bool
public static native func Net_Disconnect() -> Void
public static native func Net_Send(channel: Int32, payload: String) -> Bool
public static native func Net_SendTo(peer: Int32, channel: Int32, payload: String) -> Bool
public static native func Net_Poll() -> String
public static native func Net_Stats() -> String
public static native func Net_LocalId() -> Int32
