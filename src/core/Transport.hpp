#pragma once

// Thread-safe UDP transport used by the RED4ext natives.
//
// Threading model:
//   * The game thread (redscript natives / CET Lua) calls Connect, Disconnect, Send, Poll and
//     StatsJson. These only touch mutex-protected queues, atomics and the stats snapshot.
//   * One background thread per connection owns the socket and every piece of protocol state
//     (peers, sequences, resend timers). It wakes on socket readiness, on Send (an event), or
//     on its own timers.
//
// Message format returned by Poll: "<senderId>|<channel>|<payload>". System events use
// sender 0 and channel 0, e.g. "0|0|welcome 2", "0|0|peer_join 3", "0|0|peer_leave 3 timeout".

#include "Protocol.hpp"

#include <atomic>
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <string_view>
#include <thread>

namespace coopnet
{
enum class ConnectionState : int
{
    Idle = 0,
    Resolving,
    Connecting,
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

struct TransportConfig
{
    size_t maxInbox = 8192;          // messages waiting for Poll
    size_t maxOutbox = 4096;         // Send requests waiting for the network thread
    uint64_t helloIntervalMicros = 500'000;
    uint64_t pingIntervalMicros = 1'000'000;   // relay keepalive and relay RTT
    uint64_t peerPingIntervalMicros = 500'000; // peer RTT samples, which drive the resend timeout
    uint64_t relayTimeoutMicros = 5'000'000;
    uint64_t statsIntervalMicros = 200'000;
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
    bool Connect(std::string_view aHost, int aPort, std::string_view aRoom);
    void Disconnect();

    // Queues a payload for every peer (aTarget = kBroadcastId) or for one peer id.
    // Channels 1..15 are unreliable/sequenced, 16..31 reliable/ordered.
    bool Send(int aChannel, std::string_view aPayload, int aTarget = kBroadcastId);

    // Pops the oldest queued message (FIFO). Returns false when the queue is empty.
    bool Poll(std::string& aMessage);

    [[nodiscard]] std::string StatsJson() const;
    [[nodiscard]] ConnectionState State() const;
    [[nodiscard]] int LocalId() const;
    [[nodiscard]] int PeerCount() const;

private:
    void StopThread();

    std::unique_ptr<SharedState> m_shared;
    std::thread m_thread;
    std::mutex m_lifecycleMutex;
};
} // namespace coopnet
