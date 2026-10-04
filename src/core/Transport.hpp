#pragma once

// Thread-safe protocol v2 client used by the RED4ext natives.
//
// Wire: protocol v2 (magic 0xCB77, relay/coopnet/proto.py) to relay_v2.py, which also serves
// Jakub's v1 CP1 text clients on the same port. The plugin's own CPN2 framing is retired.
//
// Threading model:
//   * The game thread (redscript natives / CET Lua) calls Connect, Disconnect, Send, Poll,
//     PushPlayer, SampleRemote and StatsJson. These only touch mutex-protected queues, the remote
//     player buffers, atomics and the stats snapshot.
//   * One background thread per connection owns the socket and every piece of protocol state: the
//     cookie handshake, the per-hop link (v2::Connection), the relay clock (v2::ClockSync) and the
//     peer list. It wakes on socket readiness, on Send/PushPlayer (an event), or on its own timers.
//
// Script messages: Send carries the payload as a SCRIPT_MSG (protocol 2.1). Channels 1..15 are
// unreliable; the transport puts an 8-bit per-channel counter in the SCRIPT_MSG flags so a receiver
// drops a message older than one it already delivered on that channel (newest wins). Channels
// 16..31 are reliable and ordered (one ordered stream per hop, exactly once).
//
// Poll returns "<senderId>|<channel>|<payload>". Transport events use sender 0 and channel 0:
//   connecting <addr>:<port>         the relay name resolved, the handshake starts
//   no_answer                        no reply to the handshake within 1.5 s (fallback signal for v1;
//                                    the transport keeps trying)
//   welcome <id> <role>              joined the room as peer <id> (role host|joiner|spectator)
//   peer_join <id> <role>[ legacy]   another player (legacy = a bridged v1 client)
//   peer_leave <id> <reason>         quit|timeout|kicked|rate_limit|protocol_error|server_shutdown|
//                                    slow_consumer|left|replaced|relay_lost
//   relay_lost                       no datagram from the relay for 5 s; reconnecting
//   relay_disconnect <reason>        the relay ended the session (timeout and server_shutdown
//                                    reconnect, the rest stop with an error)
//   rejected <reason> <text>         the relay refused the join (bad_key, role_taken, room_full, ...)
//   error <text>, disconnected
//
// Player snapshots: PushPlayer stores the newest local player state; the network thread sends it as
// a PLAYER_SNAPSHOT stamped with the relay clock, at most every 0.75 / player_hz s (newest wins).
// Received snapshots go into one v2::SnapshotBuffer per remote player; SampleRemote renders that
// player 100-150 ms in the past (interp.py's InterpBuffer.PLAYER), extrapolating over gaps.

#include "v2/SnapshotBuffer.hpp"

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <thread>

namespace coopnet
{
enum class ConnectionState : int
{
    Idle = 0,
    Resolving,
    Connecting, // handshake in progress
    Connected,
    Reconnecting,
    Error
};

const char* ToString(ConnectionState aState);

enum class LogLevel
{
    Info,
    Warn,
    Error
};

using LogSink = std::function<void(LogLevel, std::string_view)>;

// Net_Send channels, carried as SCRIPT_MSG channels.
inline constexpr int kFirstUnreliableChannel = 1;
inline constexpr int kLastUnreliableChannel = 15;
inline constexpr int kFirstReliableChannel = 16;
inline constexpr int kLastReliableChannel = 31;
inline constexpr size_t kMaxPayloadSize = 1000; // SCRIPT_MSG text limit (coopv2::kMaxScriptBytes)
inline constexpr int kBroadcastPeer = 255;      // Send target: every other player
inline constexpr std::string_view kDefaultRoom = "default";
inline constexpr int kDefaultRelayPort = 11778;
inline constexpr size_t kMaxKeyBytes = 256;

bool IsUnreliableChannel(int aChannel);
bool IsReliableChannel(int aChannel);

// Protocol v2 roles (coopv2::Role).
enum class Role : int
{
    Any = 0, // the relay picks host when the room has none, joiner otherwise
    Host = 1,
    Joiner = 2,
    Spectator = 3,
};

const char* ToString(Role aRole);

struct ConnectOptions
{
    std::string host;
    int port = kDefaultRelayPort;
    std::string room{kDefaultRoom}; // 1..32 of A-Z a-z 0-9 _ -
    std::string key;                // room password (may be empty); only its hash is sent
    int role = static_cast<int>(Role::Any);
};

// Empty when the options are valid, otherwise the reason.
std::string ValidateConnectOptions(const ConnectOptions& aOptions);

// The local player as Net_PushPlayer passes it.
struct PlayerState
{
    float x = 0.0f; // world position, metres
    float y = 0.0f;
    float z = 0.0f;
    float yaw = 0.0f;   // degrees, any range
    float pitch = 0.0f; // degrees, clamped to +-90
    float vx = 0.0f;    // metres per second, clamped to +-327 m/s
    float vy = 0.0f;
    float vz = 0.0f;
    int moveState = 0; // coopv2::MoveState, 0..13
    int flags = 0;     // coopv2::PlayerFlag bits, 0..0xFFFF without DRIVING (vehicles come later)
    int health = 255;  // 0..255
};

// Empty when the state can be sent, otherwise the reason.
std::string ValidatePlayerState(const PlayerState& aState);

// A remote player at render time.
struct RemotePose
{
    v2::SampleMode mode = v2::SampleMode::Interpolated;
    double x = 0.0;
    double y = 0.0;
    double z = 0.0;
    double yaw = 0.0;   // [0, 360)
    double pitch = 0.0; // degrees
    double vx = 0.0;
    double vy = 0.0;
    double vz = 0.0;
    int moveState = 0;
    int flags = 0;
    int health = 0;
    double delayMs = 0.0; // relay now - render time
    double aheadMs = 0.0; // render time - newest sample (> 0: extrapolating or held)
};

// What Net_SampleRemote returns:
// "<mode> <x> <y> <z> <yaw> <pitch> <vx> <vy> <vz> <moveState> <flags> <health> <delayMs> <aheadMs>"
// with mode interpolated|extrapolated|held|early, positions to the millimetre.
std::string FormatRemotePose(const RemotePose& aPose);

struct TransportConfig
{
    size_t maxInbox = 8192;           // messages waiting for Poll (reliable ones may use twice this)
    size_t maxOutbox = 4096;          // Send requests waiting for the network thread
    size_t maxReliableBacklog = 4096; // reliable messages waiting for the link's 256-message window
    double handshakeResendMs = 250.0;
    double noAnswerMs = 1500.0;       // "no_answer" fallback signal after this long without a reply
    double authTimeoutMs = 5000.0;    // AUTH unanswered this long: start over for a fresh cookie
    double relayTimeoutMs = 5000.0;   // no datagram from the relay: relay_lost, reconnect
    double ackDelayMs = 20.0;
    double keepaliveMs = 1000.0;
    double minPacketIntervalMs = 10.0; // at most ~100 DATA packets/s, below relay_v2.py's 120/s
    double reliablePerSecond = 50.0; // below relay_v2.py's 60/s reliable bucket
    double reliableBurst = 100.0;    // below its burst of 120
    double statsIntervalMs = 200.0;
};

struct SharedState;

class Transport
{
public:
    explicit Transport(TransportConfig aConfig = {});
    ~Transport();

    Transport(const Transport&) = delete;
    Transport& operator=(const Transport&) = delete;

    // Must be called before Connect; the sink is invoked from both the game and network threads.
    void SetLogSink(LogSink aSink);

    // Starts (or restarts) the network thread. Host resolution happens on that thread so a slow
    // DNS lookup never stalls the game. Returns false only for invalid arguments or thread failure.
    bool Connect(const ConnectOptions& aOptions);
    // The 0.1.x API (Net_Connect, Net_ConnectRoom): no room key, role Any; an empty room means
    // "default".
    bool Connect(std::string_view aHost, int aPort, std::string_view aRoom);

    // Stops the network thread and waits for it. A lookup still in progress is cancelled, so
    // neither this, a reconnect through Connect, nor the destructor waits for the DNS server.
    void Disconnect();

    // Queues a payload for every other player (aTarget = kBroadcastPeer) or for one peer id. False
    // for an invalid channel, a payload above kMaxPayloadSize or not valid UTF-8 (or holding NUL),
    // an invalid target, no connection, no peer that can receive script messages, or a full outbox.
    bool Send(int aChannel, std::string_view aPayload, int aTarget = kBroadcastPeer);

    // Pops the oldest queued message (FIFO). Returns false when the queue is empty.
    bool Poll(std::string& aMessage);

    // Stores the local player for the next PLAYER_SNAPSHOT (newest wins). False when the state is
    // invalid (ValidatePlayerState), or before the session is up and the relay clock is synced.
    bool PushPlayer(const PlayerState& aState);

    // The remote player aPeer at render time (call once per frame). False when that player has
    // sent no snapshot yet or the relay clock is not synced.
    bool SampleRemote(int aPeer, RemotePose& aPose);

    [[nodiscard]] std::string StatsJson() const;
    [[nodiscard]] ConnectionState State() const;
    [[nodiscard]] int LocalId() const;
    [[nodiscard]] int PeerCount() const;
    [[nodiscard]] bool ClockSynced() const;
    // Relay clock estimate (ms) for the current monotonic time; nullopt until synced.
    [[nodiscard]] std::optional<double> RelayNowMs() const;

private:
    void StopThread();

    std::unique_ptr<SharedState> m_shared;
    std::thread m_thread;
    std::mutex m_lifecycleMutex;
};
} // namespace coopnet
