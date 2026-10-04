#include "Transport.hpp"

#include "Version.hpp"
#include "v2/ClockSync.hpp"
#include "v2/V2Codec.hpp"
#include "v2/V2Hash.hpp"
#include "v2/V2Reliability.hpp"

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <mswsock.h>
#include <windows.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <deque>
#include <exception>
#include <limits>
#include <map>
#include <optional>
#include <random>
#include <span>
#include <sstream>
#include <system_error>
#include <variant>
#include <vector>

namespace coopnet
{
namespace
{
constexpr DWORD kMaxWaitMillis = 20;
constexpr int kSocketBufferBytes = 1 << 20;
constexpr std::string_view kGameVersion = "2.31a"; // GameBuildId input, as client_v2.py sends it
constexpr std::string_view kPlayerName = "CP2077CoopNet";
constexpr double kPlayerPaceFraction = 0.75; // snapshots at most every 0.75 / player_hz s
constexpr double kDefaultFrameMs = 1000.0 / 60.0;
constexpr double kMaxFrameMs = 100.0;
constexpr uint8_t kPeerFlagLegacy = 0x01;

double MonotonicMs()
{
    using namespace std::chrono;
    return duration<double, std::milli>(steady_clock::now().time_since_epoch()).count();
}

uint32_t LowBits(double aMs)
{
    return static_cast<uint32_t>(static_cast<uint64_t>(static_cast<int64_t>(std::floor(aMs))));
}

std::string ComposeMessage(int aSender, int aChannel, std::string_view aPayload)
{
    std::string message;
    message.reserve(aPayload.size() + 12);
    message += std::to_string(aSender);
    message += '|';
    message += std::to_string(aChannel);
    message += '|';
    message.append(aPayload.data(), aPayload.size());
    return message;
}

std::string JsonEscape(std::string_view aText)
{
    std::string escaped;
    escaped.reserve(aText.size() + 2);
    for (const char character : aText)
    {
        switch (character)
        {
        case '"':
            escaped += "\\\"";
            break;
        case '\\':
            escaped += "\\\\";
            break;
        case '\n':
            escaped += "\\n";
            break;
        case '\r':
            escaped += "\\r";
            break;
        case '\t':
            escaped += "\\t";
            break;
        default:
            if (static_cast<unsigned char>(character) < 0x20)
            {
                char buffer[8];
                std::snprintf(buffer, sizeof(buffer), "\\u%04x", static_cast<unsigned char>(character));
                escaped += buffer;
            }
            else
            {
                escaped += character;
            }
        }
    }
    return escaped;
}

std::string FormatDouble(double aValue, int aDecimals = 1)
{
    char buffer[48];
    std::snprintf(buffer, sizeof(buffer), "%.*f", aDecimals, aValue);
    return buffer;
}

std::string OptionalDouble(std::optional<double> aValue, int aDecimals = 1)
{
    return aValue ? FormatDouble(*aValue, aDecimals) : std::string("null");
}

std::string LastSocketError(const char* aWhat)
{
    return std::string(aWhat) + " failed (WSA error " + std::to_string(WSAGetLastError()) + ")";
}

// Empty when aText is empty or not valid UTF-8.
std::wstring Utf8ToWide(std::string_view aText)
{
    if (aText.empty() || aText.size() > static_cast<size_t>(std::numeric_limits<int>::max()))
    {
        return {};
    }
    const int inputLength = static_cast<int>(aText.size());
    const int size = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, aText.data(), inputLength, nullptr, 0);
    if (size <= 0)
    {
        return {};
    }
    std::wstring wide(static_cast<size_t>(size), L'\0');
    if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, aText.data(), inputLength, wide.data(), size) != size)
    {
        return {};
    }
    return wide;
}

const char* DisconnectReasonName(uint8_t aReason)
{
    switch (static_cast<coopv2::DisconnectReason>(aReason))
    {
    case coopv2::DisconnectReason::Quit:
        return "quit";
    case coopv2::DisconnectReason::Timeout:
        return "timeout";
    case coopv2::DisconnectReason::Kicked:
        return "kicked";
    case coopv2::DisconnectReason::RateLimit:
        return "rate_limit";
    case coopv2::DisconnectReason::ProtocolError:
        return "protocol_error";
    case coopv2::DisconnectReason::ServerShutdown:
        return "server_shutdown";
    case coopv2::DisconnectReason::SlowConsumer:
        return "slow_consumer";
    }
    return "left";
}

const char* RejectReasonName(uint8_t aReason)
{
    switch (static_cast<coopv2::RejectReason>(aReason))
    {
    case coopv2::RejectReason::Version:
        return "version";
    case coopv2::RejectReason::BadCookie:
        return "bad_cookie";
    case coopv2::RejectReason::RoomFull:
        return "room_full";
    case coopv2::RejectReason::BadKey:
        return "bad_key";
    case coopv2::RejectReason::RoleTaken:
        return "role_taken";
    case coopv2::RejectReason::ModMismatch:
        return "mod_mismatch";
    case coopv2::RejectReason::RateLimited:
        return "rate_limited";
    case coopv2::RejectReason::ServerFull:
        return "server_full";
    case coopv2::RejectReason::Malformed:
        return "malformed";
    case coopv2::RejectReason::GameBuild:
        return "game_build";
    }
    return "unknown";
}

const char* RoleName(uint8_t aRole)
{
    return aRole <= static_cast<uint8_t>(Role::Spectator) ? ToString(static_cast<Role>(aRole)) : "unknown";
}

// One overlapped GetAddrInfoExW call. The event is manual-reset and signalled by Winsock when the
// lookup completes, fails or is cancelled.
struct AddressLookup
{
    AddressLookup()
        : event(CreateEventW(nullptr, TRUE, FALSE, nullptr))
    {
    }

    ~AddressLookup()
    {
        if (result != nullptr)
        {
            FreeAddrInfoExW(result);
        }
        if (event != nullptr)
        {
            CloseHandle(event);
        }
    }

    AddressLookup(const AddressLookup&) = delete;
    AddressLookup& operator=(const AddressLookup&) = delete;

    OVERLAPPED overlapped{};
    HANDLE event = nullptr;
    HANDLE cancel = nullptr;
    ADDRINFOEXW* result = nullptr;
};

// How long a cancelled lookup may take to report completion before it is abandoned.
constexpr DWORD kLookupCancelWaitMillis = 1000;

// Winsock stays initialised for the rest of the process once a session has started it, and the
// plugin never calls WSACleanup. A cancelled GetAddrInfoExW lookup keeps running on a WS2_32
// thread-pool thread after its completion was reported (the LLMNR/NetBIOS query is not
// interrupted). If the last WSACleanup ran in between, that thread touched freed WS2_32 state:
// access violations inside WS2_32, with none of the plugin's frames on the stack, in the unit
// tests that started and stopped sessions after the stop-while-resolving checks. Process exit
// tears Winsock down; DllMain must not call WSACleanup anyway. Returns WSAStartup's result.
int AcquireWinsock()
{
    static std::once_flag once;
    static int result = 0;
    std::call_once(once, [] {
        WSADATA data{};
        result = WSAStartup(MAKEWORD(2, 2), &data);
    });
    return result;
}

struct ScriptRequest
{
    uint8_t channel = 0;
    uint8_t target = coopv2::kPeerBroadcast;
    std::string text;
};

struct PendingPlayer
{
    PlayerState state;
    double localMs = 0.0; // when Net_PushPlayer sampled it
};

struct PeerStatsSnapshot
{
    int id = 0;
    std::string role;
    std::string name;
    int minor = 0;
    bool legacy = false;
    std::optional<double> relayRttMs; // that peer <-> relay, reported by the relay
    std::optional<double> lossIn;     // its packets lost on the way to the relay
    std::optional<double> lossOut;    // relay packets lost on the way to it
    uint64_t unreliableReceived = 0;
    uint64_t unreliableStale = 0;
    uint64_t reliableReceived = 0;
    uint64_t snapshots = 0;
};

struct StatsSnapshot
{
    std::string host;
    int port = 0;
    std::string room;
    std::string role;
    int minor = -1;
    double uptimeSeconds = 0.0;
    std::optional<double> relayRttMs;
    std::optional<double> rtoMs;
    std::optional<double> lossIn;  // relay packets we did not get
    std::optional<double> lossOut; // our packets that were not acked
    bool clockSynced = false;
    std::optional<double> clockOffsetMs;
    std::optional<double> clockBoundMs;
    std::optional<double> clockRttMs;
    size_t clockSamples = 0;
    uint32_t clockSteps = 0;
    uint64_t packetsSent = 0;
    uint64_t packetsReceived = 0;
    uint64_t bytesSent = 0;
    uint64_t bytesReceived = 0;
    uint64_t reliableSent = 0;
    uint64_t reliableResent = 0;
    uint64_t reliableDelivered = 0;
    uint64_t reliableDuplicates = 0;
    size_t reliablePending = 0;
    size_t reliableBacklog = 0;
    uint64_t rttSamples = 0;
    uint64_t rttSkipped = 0;
    uint64_t badFrames = 0;
    uint64_t decodeErrors = 0;
    uint64_t foreignDatagrams = 0;
    uint64_t sendErrors = 0;
    uint64_t droppedInbound = 0;
    uint64_t droppedNoPeer = 0;
    uint64_t droppedBacklog = 0;
    uint64_t droppedOnReconnect = 0;
    uint64_t ignoredMessages = 0;
    uint64_t playersSent = 0;
    uint64_t unsyncedDropped = 0;
    uint64_t handshakes = 0;
    std::string lastError;
    std::vector<PeerStatsSnapshot> peers;
};

// A remote player's snapshots, shared by the network thread (Push) and the game thread (Sample).
struct RemoteEntry
{
    v2::SnapshotBuffer buffer{v2::kPlayerInterp};
    std::optional<uint16_t> lastSeq;
    uint64_t received = 0;
    uint64_t duplicates = 0;
    uint64_t reordered = 0;
    std::optional<double> lastSampleLocalMs; // the game thread's previous SampleRemote call
};
} // namespace

// ---- free functions ------------------------------------------------------------------------------

const char* ToString(ConnectionState aState)
{
    switch (aState)
    {
    case ConnectionState::Idle:
        return "idle";
    case ConnectionState::Resolving:
        return "resolving";
    case ConnectionState::Connecting:
        return "connecting";
    case ConnectionState::Connected:
        return "connected";
    case ConnectionState::Reconnecting:
        return "reconnecting";
    case ConnectionState::Error:
        return "error";
    }
    return "unknown";
}

const char* ToString(Role aRole)
{
    switch (aRole)
    {
    case Role::Any:
        return "any";
    case Role::Host:
        return "host";
    case Role::Joiner:
        return "joiner";
    case Role::Spectator:
        return "spectator";
    }
    return "unknown";
}

bool IsUnreliableChannel(int aChannel)
{
    return aChannel >= kFirstUnreliableChannel && aChannel <= kLastUnreliableChannel;
}

bool IsReliableChannel(int aChannel)
{
    return aChannel >= kFirstReliableChannel && aChannel <= kLastReliableChannel;
}

std::string ValidateConnectOptions(const ConnectOptions& aOptions)
{
    if (aOptions.host.empty())
    {
        return "empty relay host";
    }
    if (aOptions.port <= 0 || aOptions.port > 65535)
    {
        return "port must be 1..65535";
    }
    if (!v2::IsValidRoom(aOptions.room))
    {
        return "room must be 1..32 of A-Z a-z 0-9 _ -";
    }
    if (aOptions.key.size() > kMaxKeyBytes)
    {
        return "room key above " + std::to_string(kMaxKeyBytes) + " bytes";
    }
    if (aOptions.role < static_cast<int>(Role::Any) || aOptions.role > static_cast<int>(Role::Spectator))
    {
        return "role must be 0 (any), 1 (host), 2 (joiner) or 3 (spectator)";
    }
    return {};
}

std::string ValidatePlayerState(const PlayerState& aState)
{
    const float values[] = {aState.x, aState.y, aState.z, aState.yaw, aState.pitch, aState.vx, aState.vy, aState.vz};
    for (const float value : values)
    {
        if (!std::isfinite(value))
        {
            return "non-finite value";
        }
    }
    if (std::fabs(aState.x) > coopv2::kWorldXYLimit || std::fabs(aState.y) > coopv2::kWorldXYLimit ||
        std::fabs(aState.z) > coopv2::kWorldZLimit)
    {
        return "position outside the world bounds";
    }
    if (aState.moveState < 0 || aState.moveState >= v2::kMoveStateCount)
    {
        return "move state must be 0..13";
    }
    if (aState.flags < 0 || aState.flags > 0xFFFF)
    {
        return "flags must be 0..65535";
    }
    if ((aState.flags & coopv2::kPlayerDriving) != 0)
    {
        return "the DRIVING flag needs a vehicle block (not supported yet)";
    }
    if (aState.health < 0 || aState.health > 255)
    {
        return "health must be 0..255";
    }
    return {};
}

std::string FormatRemotePose(const RemotePose& aPose)
{
    char buffer[256];
    std::snprintf(buffer, sizeof(buffer), "%s %.3f %.3f %.3f %.2f %.2f %.2f %.2f %.2f %d %d %d %.1f %.1f",
                  v2::ToString(aPose.mode), aPose.x, aPose.y, aPose.z, aPose.yaw, aPose.pitch, aPose.vx, aPose.vy,
                  aPose.vz, aPose.moveState, aPose.flags, aPose.health, aPose.delayMs, aPose.aheadMs);
    return buffer;
}

// ---- shared state ----------------------------------------------------------------------------------

struct SharedState
{
    explicit SharedState(TransportConfig aConfig)
        : config(aConfig)
        , wakeEvent(CreateEventW(nullptr, FALSE, FALSE, nullptr))
    {
    }

    ~SharedState()
    {
        if (wakeEvent)
        {
            CloseHandle(wakeEvent);
        }
    }

    SharedState(const SharedState&) = delete;
    SharedState& operator=(const SharedState&) = delete;

    void Log(LogLevel aLevel, std::string_view aMessage) const
    {
        if (log)
        {
            log(aLevel, aMessage);
        }
    }

    // Unreliable messages are dropped when the inbox is full. Reliable ones were already
    // acknowledged to the relay, so they get up to twice the space before they are dropped.
    bool PushInbox(std::string aMessage, bool aReliable)
    {
        std::lock_guard lock(inboxMutex);
        const size_t limit = aReliable ? config.maxInbox * 2 : config.maxInbox;
        if (inbox.size() >= limit)
        {
            return false;
        }
        inbox.push_back(std::move(aMessage));
        return true;
    }

    void PushEvent(std::string_view aText)
    {
        std::lock_guard lock(inboxMutex);
        inbox.push_back(ComposeMessage(0, 0, aText));
    }

    void Wake() const
    {
        if (wakeEvent)
        {
            SetEvent(wakeEvent);
        }
    }

    // ---- remote players (network thread pushes, game thread samples) ----

    void SetClock(bool aSynced, double aOffsetMs)
    {
        std::lock_guard lock(remoteMutex);
        clockSynced = aSynced;
        clockOffsetMs = aOffsetMs;
        clockReady = aSynced;
    }

    void ResetRemoteTiming()
    {
        std::lock_guard lock(remoteMutex);
        for (auto& [id, remote] : remotes)
        {
            remote.buffer.ResetTiming();
        }
    }

    void RemoveRemote(int aPeer)
    {
        std::lock_guard lock(remoteMutex);
        remotes.erase(aPeer);
    }

    void ClearRemotes()
    {
        std::lock_guard lock(remoteMutex);
        remotes.clear();
        clockSynced = false;
        clockOffsetMs = 0.0;
        clockReady = false;
    }

    const TransportConfig config;
    LogSink log;
    HANDLE wakeEvent = nullptr;

    std::atomic<bool> stopRequested{false};
    std::atomic<int> state{static_cast<int>(ConnectionState::Idle)};
    std::atomic<int> localId{0};
    std::atomic<int> peerCount{0};
    std::atomic<int> scriptPeers{0};          // peers that can receive SCRIPT_MSG (v2, minor >= 1)
    std::atomic<bool> scriptAllowed{false};   // the negotiated minor allows SCRIPT_MSG
    std::atomic<bool> clockReady{false};      // mirrors clockSynced for lock-free checks
    std::atomic<uint64_t> refusedSends{0};
    std::atomic<uint64_t> refusedPlayers{0};

    mutable std::mutex inboxMutex;
    std::deque<std::string> inbox;

    mutable std::mutex outboxMutex;
    std::deque<ScriptRequest> outbox;
    std::optional<PendingPlayer> pendingPlayer;
    uint64_t playersPushed = 0;

    mutable std::mutex statsMutex;
    StatsSnapshot stats;

    mutable std::mutex remoteMutex;
    std::map<int, RemoteEntry> remotes;
    bool clockSynced = false;
    double clockOffsetMs = 0.0;
};

namespace
{
// Owns the socket and all protocol state. Lives entirely on the network thread.
class Session
{
public:
    Session(SharedState& aShared, ConnectOptions aOptions, uint64_t aSeed)
        : m_shared(aShared)
        , m_options(std::move(aOptions))
        , m_random(aSeed)
        , m_reliableTokens(aShared.config.reliableBurst)
    {
    }

    Session(const Session&) = delete;
    Session& operator=(const Session&) = delete;

    ~Session()
    {
        Close();
    }

    void Run()
    {
        m_startedMs = MonotonicMs();
        const bool opened = Open();
        if (opened)
        {
            Loop();
            SayGoodbye();
        }
        Close();
        if (static_cast<ConnectionState>(m_shared.state.load()) != ConnectionState::Error)
        {
            SetState(ConnectionState::Idle);
        }
        m_shared.peerCount = 0;
        m_shared.scriptPeers = 0;
        m_shared.scriptAllowed = false;
        m_shared.localId = 0;
        m_shared.ClearRemotes();
        {
            std::lock_guard lock(m_shared.outboxMutex);
            m_shared.pendingPlayer.reset();
        }
        PublishStats(MonotonicMs());
        // A failed Open already pushed "error ..."; a stop during it (for example while the host
        // name was still resolving) ends the session like a normal disconnect.
        if (opened || m_shared.stopRequested.load())
        {
            m_shared.PushEvent("disconnected");
        }
    }

private:
    enum class Phase
    {
        Hello, // HELLO until a CHALLENGE arrives
        Auth,  // AUTH with the cookie until WELCOME or REJECT
        Live,  // DATA
    };

    struct Peer
    {
        uint8_t id = 0;
        uint8_t role = 0;
        uint8_t minor = 0;
        bool legacy = false;
        std::string name;
        std::array<std::optional<uint8_t>, kLastUnreliableChannel + 1> lastUnreliable{};
        std::optional<coopv2::LinkStats> link;
        uint64_t unreliableReceived = 0;
        uint64_t unreliableStale = 0;
        uint64_t reliableReceived = 0;
        uint64_t snapshots = 0;

        [[nodiscard]] bool TakesScript() const
        {
            return !legacy && minor >= 1;
        }
    };

    struct OutMessage
    {
        uint8_t type = 0;
        uint8_t peer = 0;
        v2::Bytes body;
    };

    struct ReliableOut
    {
        uint8_t peer = 0;
        v2::Bytes body;
    };

    // ---- lifecycle ------------------------------------------------------------------------

    bool Open()
    {
        const int startup = AcquireWinsock();
        if (startup != 0)
        {
            Fail("WSAStartup failed (error " + std::to_string(startup) + ")");
            return false;
        }

        SetState(ConnectionState::Resolving);
        if (!Resolve())
        {
            return false;
        }

        m_socket = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        if (m_socket == INVALID_SOCKET)
        {
            Fail(LastSocketError("socket"));
            return false;
        }

        // Windows reports ICMP "port unreachable" as WSAECONNRESET on the next recvfrom; a relay
        // restart must not look like a socket failure.
        BOOL reportReset = FALSE;
        DWORD returned = 0;
        if (WSAIoctl(m_socket, SIO_UDP_CONNRESET, &reportReset, sizeof(reportReset), nullptr, 0, &returned, nullptr,
                     nullptr) == SOCKET_ERROR)
        {
            m_shared.Log(LogLevel::Warn, LastSocketError("SIO_UDP_CONNRESET"));
        }
        const int bufferBytes = kSocketBufferBytes;
        setsockopt(m_socket, SOL_SOCKET, SO_RCVBUF, reinterpret_cast<const char*>(&bufferBytes), sizeof(bufferBytes));
        setsockopt(m_socket, SOL_SOCKET, SO_SNDBUF, reinterpret_cast<const char*>(&bufferBytes), sizeof(bufferBytes));

        sockaddr_in local{};
        local.sin_family = AF_INET;
        local.sin_addr.s_addr = htonl(INADDR_ANY);
        local.sin_port = 0;
        if (bind(m_socket, reinterpret_cast<const sockaddr*>(&local), sizeof(local)) == SOCKET_ERROR)
        {
            Fail(LastSocketError("bind"));
            return false;
        }

        m_socketEvent = WSACreateEvent();
        if (m_socketEvent == WSA_INVALID_EVENT)
        {
            Fail(LastSocketError("WSACreateEvent"));
            return false;
        }
        if (WSAEventSelect(m_socket, m_socketEvent, FD_READ) == SOCKET_ERROR)
        {
            Fail(LastSocketError("WSAEventSelect"));
            return false;
        }
        m_timer = CreateWaitableTimerExW(nullptr, nullptr, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_ALL_ACCESS);
        if (m_timer == nullptr)
        {
            m_timer = CreateWaitableTimerExW(nullptr, nullptr, 0, TIMER_ALL_ACCESS);
        }
        if (m_timer == nullptr)
        {
            m_shared.Log(LogLevel::Warn, "no waitable timer (error " + std::to_string(GetLastError()) +
                                             "); timers follow the system tick");
        }

        char address[INET_ADDRSTRLEN] = {};
        inet_ntop(AF_INET, &m_relayAddress.sin_addr, address, sizeof(address));
        m_shared.Log(LogLevel::Info, "connecting to relay " + std::string(address) + ":" +
                                         std::to_string(m_options.port) + " room '" + m_options.room + "' role " +
                                         ToString(static_cast<Role>(m_options.role)) + " (protocol v2)");
        m_shared.PushEvent("connecting " + std::string(address) + ":" + std::to_string(m_options.port));
        StartHandshake(MonotonicMs(), ConnectionState::Connecting);
        return true;
    }

    // Resolves the host with an overlapped GetAddrInfoExW and waits on both the lookup and the wake
    // event. Disconnect, a reconnect and the Transport destructor (Main(Unload)) set stopRequested
    // and wake this thread; the lookup is then cancelled, so StopThread's join on the game thread
    // never waits for a slow or unreachable DNS server. Returns false on failure (after Fail) and
    // on a stop request (without Fail).
    bool Resolve()
    {
        if (m_shared.stopRequested.load())
        {
            return false;
        }
        const std::wstring host = Utf8ToWide(m_options.host);
        if (host.empty())
        {
            Fail("cannot resolve '" + m_options.host + "' (not valid UTF-8)");
            return false;
        }
        const std::wstring service = std::to_wstring(m_options.port);
        auto lookup = std::make_unique<AddressLookup>();
        if (lookup->event == nullptr)
        {
            Fail("cannot resolve '" + m_options.host + "': CreateEvent failed (error " +
                 std::to_string(GetLastError()) + ")");
            return false;
        }
        ADDRINFOEXW hints{};
        hints.ai_family = AF_INET;
        hints.ai_socktype = SOCK_DGRAM;
        hints.ai_protocol = IPPROTO_UDP;
        lookup->overlapped.hEvent = lookup->event;
        int status = GetAddrInfoExW(host.c_str(), service.c_str(), NS_ALL, nullptr, &hints, &lookup->result, nullptr,
                                    &lookup->overlapped, nullptr, &lookup->cancel);
        if (status == WSA_IO_PENDING)
        {
            const HANDLE handles[2] = {lookup->event, m_shared.wakeEvent};
            while (true)
            {
                const DWORD waitResult = WaitForMultipleObjects(2, handles, FALSE, INFINITE);
                if (waitResult == WAIT_OBJECT_0)
                {
                    break;
                }
                if (waitResult == WAIT_OBJECT_0 + 1)
                {
                    if (m_shared.stopRequested.load())
                    {
                        CancelLookup(std::move(lookup));
                        return false;
                    }
                    continue; // woken for something else; keep waiting for the lookup
                }
                Fail("cannot resolve '" + m_options.host + "': WaitForMultipleObjects failed (error " +
                     std::to_string(GetLastError()) + ")");
                CancelLookup(std::move(lookup));
                return false;
            }
            status = GetAddrInfoExOverlappedResult(&lookup->overlapped);
        }
        if (status != NO_ERROR || lookup->result == nullptr || lookup->result->ai_addr == nullptr)
        {
            Fail("cannot resolve '" + m_options.host + "' (error " + std::to_string(status) + ")");
            return false;
        }
        std::memcpy(&m_relayAddress, lookup->result->ai_addr, sizeof(m_relayAddress));
        return true;
    }

    // Cancels a pending lookup and waits for Winsock to report its completion, after which the
    // OVERLAPPED and the result pointer are no longer written and can be freed. If the completion
    // does not arrive in time, the lookup is deliberately left allocated (a few hundred bytes)
    // rather than freed while Winsock may still write to it.
    void CancelLookup(std::unique_ptr<AddressLookup> aLookup)
    {
        const INT cancelStatus = GetAddrInfoExCancel(&aLookup->cancel);
        if (WaitForSingleObject(aLookup->event, kLookupCancelWaitMillis) == WAIT_OBJECT_0)
        {
            return;
        }
        m_shared.Log(LogLevel::Warn, "DNS lookup of '" + m_options.host + "' did not confirm cancellation within " +
                                         std::to_string(kLookupCancelWaitMillis) + " ms (GetAddrInfoExCancel " +
                                         std::to_string(cancelStatus) + "); leaving it allocated");
        static_cast<void>(aLookup.release());
    }

    void Close()
    {
        if (m_socket != INVALID_SOCKET)
        {
            closesocket(m_socket);
            m_socket = INVALID_SOCKET;
        }
        if (m_socketEvent != WSA_INVALID_EVENT)
        {
            WSACloseEvent(m_socketEvent);
            m_socketEvent = WSA_INVALID_EVENT;
        }
        if (m_timer != nullptr)
        {
            CloseHandle(m_timer);
            m_timer = nullptr;
        }
    }

    void SayGoodbye()
    {
        if (m_socket == INVALID_SOCKET || m_phase != Phase::Live)
        {
            return;
        }
        v2::Bytes bye;
        if (v2::EncodeDisconnect(m_token, static_cast<uint8_t>(coopv2::DisconnectReason::Quit), bye) == v2::Status::Ok)
        {
            // Unreliable by nature; two copies make a lost goodbye unlikely. The relay's idle
            // timeout (10 s) covers the rest.
            SendDatagram(bye);
            SendDatagram(bye);
        }
    }

    // Waits for the socket, a wake from the game thread or the next timer. The wait timeout of
    // WaitForMultipleObjects follows the system timer (15.6 ms unless something raised it), so a
    // high-resolution waitable timer carries the short waits that packet pacing needs.
    DWORD Wait(double aNow, double aWake)
    {
        const HANDLE handles[3] = {m_socketEvent, m_shared.wakeEvent, m_timer};
        if (aWake <= aNow)
        {
            return WaitForMultipleObjects(2, handles, FALSE, 0);
        }
        const double waitMs = std::min(aWake - aNow, static_cast<double>(kMaxWaitMillis));
        if (m_timer != nullptr)
        {
            LARGE_INTEGER due{};
            due.QuadPart = -static_cast<LONGLONG>(waitMs * 10'000.0); // relative, 100 ns units
            if (SetWaitableTimer(m_timer, &due, 0, nullptr, nullptr, FALSE))
            {
                return WaitForMultipleObjects(3, handles, FALSE, kMaxWaitMillis * 2);
            }
        }
        return WaitForMultipleObjects(2, handles, FALSE, static_cast<DWORD>(std::ceil(waitMs)));
    }

    void Loop()
    {
        while (!m_shared.stopRequested.load())
        {
            double now = MonotonicMs();
            const DWORD waitResult = Wait(now, NextWakeMs(now));
            if (waitResult == WAIT_FAILED)
            {
                Fail("WaitForMultipleObjects failed (error " + std::to_string(GetLastError()) + ")");
                return;
            }
            if (m_shared.stopRequested.load())
            {
                return;
            }

            now = MonotonicMs();
            WSANETWORKEVENTS networkEvents{};
            WSAEnumNetworkEvents(m_socket, m_socketEvent, &networkEvents);
            DrainSocket(now);
            if (m_shared.stopRequested.load())
            {
                return; // a fatal REJECT or DISCONNECT
            }
            if (m_phase == Phase::Live)
            {
                if (now - m_lastHeardMs > m_shared.config.relayTimeoutMs)
                {
                    m_shared.Log(LogLevel::Warn, "relay silent for " + FormatDouble(now - m_lastHeardMs, 0) +
                                                     " ms, reconnecting");
                    m_shared.PushEvent("relay_lost");
                    LoseSession(now, "relay_lost", true);
                }
                else
                {
                    LiveTick(now);
                }
            }
            if (m_phase != Phase::Live)
            {
                HandshakeTick(now);
            }
            if (m_statsDirty || now >= m_nextStatsMs)
            {
                PublishStats(now);
                m_statsDirty = false;
                m_nextStatsMs = now + m_shared.config.statsIntervalMs;
            }
        }
    }

    double NextWakeMs(double aNow) const
    {
        double wake = aNow + kMaxWaitMillis;
        wake = std::min(wake, m_nextStatsMs);
        if (m_phase != Phase::Live)
        {
            return std::min(wake, m_nextHandshakeMs);
        }
        const v2::Connection& link = *m_link;
        // Payload waits for the packet pacing (Flush), so a due message must not spin the loop.
        const double earliestSend = aNow + std::max(0.0, m_shared.config.minPacketIntervalMs - SinceSendMs(aNow));
        if (const auto due = link.NextReliableDue())
        {
            wake = std::min(wake, std::max(*due * 1000.0, earliestSend));
        }
        if (const auto lastSend = link.LastSend())
        {
            const double sent = *lastSend * 1000.0;
            wake = std::min(wake, sent + m_shared.config.keepaliveMs);
            if (link.AckPending())
            {
                wake = std::min(wake, sent + m_shared.config.ackDelayMs);
            }
        }
        if (m_hasPendingPlayer)
        {
            wake = std::min(wake, m_lastPlayerSendMs + PlayerIntervalMs());
        }
        if (!m_pending.empty())
        {
            wake = std::min(wake, earliestSend);
        }
        if (!m_reliableBacklog.empty() && m_reliableTokens < 1.0)
        {
            wake = std::min(wake, aNow + (1.0 - m_reliableTokens) * 1000.0 / m_shared.config.reliablePerSecond);
        }
        return wake;
    }

    // ---- handshake --------------------------------------------------------------------------

    v2::JoinInfo MakeJoin() const
    {
        v2::JoinInfo join;
        join.fixed.minor = coopv2::kProtoMinor;
        join.fixed.role = static_cast<uint8_t>(m_options.role);
        join.fixed.join_flags = 0;
        join.fixed.caps = coopv2::kCapPlayer;
        join.fixed.game_build = v2::GameBuildId(kGameVersion);
        join.fixed.mod_major = static_cast<uint16_t>(kVersionMajor);
        join.fixed.mod_minor = static_cast<uint16_t>(kVersionMinor);
        join.fixed.mod_patch = static_cast<uint16_t>(kVersionPatch);
        const v2::ModEntry mods[] = {{std::string(kPluginName), std::string(kSemVer)}};
        join.fixed.mod_hash = v2::ModListHash(mods);
        join.fixed.mod_count = 1;
        join.fixed.client_nonce = m_nonce;
        join.fixed.resume_token = m_resumeToken;
        join.room = m_options.room;
        join.name = std::string(kPlayerName);
        return join;
    }

    void StartHandshake(double aNow, ConnectionState aState)
    {
        m_phase = Phase::Hello;
        m_nonce = m_random();
        m_handshakeStartMs = aNow;
        m_nextHandshakeMs = aNow;
        m_answered = false;
        m_noAnswerReported = false;
        ++m_handshakes;
        SetState(aState);
    }

    void HandshakeTick(double aNow)
    {
        if (!m_answered && !m_noAnswerReported && aNow - m_handshakeStartMs >= m_shared.config.noAnswerMs)
        {
            m_noAnswerReported = true;
            m_shared.Log(LogLevel::Warn, "the relay did not answer the v2 handshake within " +
                                             FormatDouble(m_shared.config.noAnswerMs, 0) + " ms; still trying");
            m_shared.PushEvent("no_answer");
        }
        if (m_phase == Phase::Auth && aNow - m_challengeAtMs > m_shared.config.authTimeoutMs)
        {
            m_shared.Log(LogLevel::Warn, "no WELCOME for the AUTH; starting over for a fresh cookie");
            m_phase = Phase::Hello;
        }
        if (aNow < m_nextHandshakeMs)
        {
            return;
        }
        m_nextHandshakeMs = aNow + m_shared.config.handshakeResendMs;
        v2::Bytes datagram;
        const v2::JoinInfo join = MakeJoin();
        v2::Status status;
        if (m_phase == Phase::Hello)
        {
            status = v2::EncodeHello(join, datagram);
        }
        else
        {
            v2::AuthInfo auth;
            auth.join = join;
            auth.cookie = m_cookie;
            auth.keyHash = v2::RoomKeyHash(m_options.room, m_options.key);
            status = v2::EncodeAuth(auth, datagram);
        }
        if (status != v2::Status::Ok)
        {
            Fail(std::string("cannot encode the handshake: ") + v2::ToString(status));
            m_shared.stopRequested = true;
            return;
        }
        SendDatagram(datagram);
    }

    void OnChallenge(v2::ByteSpan aBody, double aNow)
    {
        coopv2::Challenge challenge{};
        if (v2::DecodeChallenge(aBody, challenge) != v2::Status::Ok)
        {
            ++m_badFrames;
            return;
        }
        m_answered = true;
        if (m_phase != Phase::Hello)
        {
            return; // a CHALLENGE for a repeated HELLO; the first cookie is in use
        }
        std::copy(std::begin(challenge.cookie), std::end(challenge.cookie), m_cookie.begin());
        m_phase = Phase::Auth;
        m_challengeAtMs = aNow;
        m_nextHandshakeMs = aNow; // send AUTH now
    }

    void OnWelcome(v2::ByteSpan aBody, double aNow)
    {
        coopv2::Welcome welcome{};
        if (v2::DecodeWelcome(aBody, welcome) != v2::Status::Ok || welcome.peer_id == 0 ||
            welcome.peer_id == coopv2::kPeerBroadcast)
        {
            ++m_badFrames;
            return;
        }
        if (m_phase == Phase::Live)
        {
            return; // the relay repeats WELCOME for a repeated AUTH
        }
        m_answered = true;
        m_phase = Phase::Live;
        m_token = welcome.token;
        m_resumeToken = welcome.token;
        m_localId = welcome.peer_id;
        m_role = welcome.role;
        m_minor = welcome.minor;
        m_playerHz = welcome.player_hz > 0 ? welcome.player_hz : 30;
        m_link.emplace(welcome.token);
        m_clock = v2::ClockSync{};
        m_lastHeardMs = aNow;
        m_lastPlayerSendMs = -1e18;
        m_shared.localId = welcome.peer_id;
        m_shared.scriptAllowed = welcome.minor >= 1;
        m_shared.SetClock(false, 0.0);
        SetState(ConnectionState::Connected);
        m_shared.Log(LogLevel::Info, "welcome: peer " + std::to_string(welcome.peer_id) + " role " +
                                         RoleName(welcome.role) + " protocol 2." + std::to_string(welcome.minor) +
                                         " in " + FormatDouble(aNow - m_handshakeStartMs, 0) + " ms");
        m_shared.PushEvent("welcome " + std::to_string(welcome.peer_id) + " " + RoleName(welcome.role));
    }

    void OnReject(v2::ByteSpan aBody)
    {
        v2::RejectInfo reject;
        if (v2::DecodeReject(aBody, reject) != v2::Status::Ok)
        {
            ++m_badFrames;
            return;
        }
        m_answered = true;
        if (m_phase == Phase::Live)
        {
            return;
        }
        const std::string reason = RejectReasonName(reject.reason);
        if (reject.reason == static_cast<uint8_t>(coopv2::RejectReason::BadCookie))
        {
            m_shared.Log(LogLevel::Warn, "the relay refused an expired cookie; starting over");
            m_phase = Phase::Hello;
            return;
        }
        // The text is relay-supplied: keep it printable for the event line and the log.
        std::string text;
        for (const char character : reject.text)
        {
            text += static_cast<unsigned char>(character) < 0x20 ? ' ' : character;
        }
        m_shared.PushEvent("rejected " + reason + (text.empty() ? "" : " " + text));
        Fail("the relay rejected the join: " + reason + (text.empty() ? "" : " (" + text + ")"));
        m_shared.stopRequested = true;
    }

    // ---- receive ------------------------------------------------------------------------------

    void DrainSocket(double aNow)
    {
        std::array<uint8_t, 2048> buffer{};
        for (;;)
        {
            sockaddr_in from{};
            int fromLength = sizeof(from);
            const int received = recvfrom(m_socket, reinterpret_cast<char*>(buffer.data()),
                                          static_cast<int>(buffer.size()), 0, reinterpret_cast<sockaddr*>(&from),
                                          &fromLength);
            if (received == SOCKET_ERROR)
            {
                const int error = WSAGetLastError();
                if (error == WSAEWOULDBLOCK)
                {
                    return;
                }
                if (error == WSAEMSGSIZE)
                {
                    ++m_badFrames;
                    continue;
                }
                if (error == WSAECONNRESET)
                {
                    continue;
                }
                m_lastError = "recvfrom failed (WSA error " + std::to_string(error) + ")";
                return;
            }
            if (from.sin_addr.s_addr != m_relayAddress.sin_addr.s_addr || from.sin_port != m_relayAddress.sin_port)
            {
                ++m_foreignDatagrams;
                continue;
            }
            ++m_packetsReceived;
            m_bytesReceived += static_cast<uint64_t>(received);
            HandleDatagram(v2::ByteSpan(buffer.data(), static_cast<size_t>(received)), aNow);
            if (m_shared.stopRequested.load())
            {
                return;
            }
        }
    }

    void HandleDatagram(v2::ByteSpan aDatagram, double aNow)
    {
        v2::DecodedPacket packet;
        if (v2::DecodePacket(aDatagram, packet) != v2::Status::Ok)
        {
            ++m_badFrames;
            return;
        }
        switch (static_cast<coopv2::PacketType>(packet.header.type))
        {
        case coopv2::PacketType::Challenge:
            OnChallenge(packet.body, aNow);
            break;
        case coopv2::PacketType::Welcome:
            OnWelcome(packet.body, aNow);
            break;
        case coopv2::PacketType::Reject:
            OnReject(packet.body);
            break;
        case coopv2::PacketType::Data:
            if (m_phase == Phase::Live && packet.header.token == m_token)
            {
                OnData(packet, aDatagram.size(), aNow);
            }
            break;
        case coopv2::PacketType::Disconnect:
            if (m_phase == Phase::Live && packet.header.token == m_token)
            {
                OnRelayDisconnect(packet.body, aNow);
            }
            break;
        default:
            ++m_badFrames;
            break;
        }
    }

    void OnData(const v2::DecodedPacket& aPacket, size_t aSize, double aNow)
    {
        m_lastHeardMs = aNow;
        std::vector<v2::DeliveredMessage> delivered;
        if (m_link->OnPacket(aNow / 1000.0, aPacket.header, aPacket.body, delivered, aSize) != v2::Status::Ok)
        {
            ++m_badFrames;
            return;
        }
        for (const v2::DeliveredMessage& message : delivered)
        {
            OnMessage(message, aNow);
            if (m_phase != Phase::Live)
            {
                return;
            }
        }
    }

    void OnRelayDisconnect(v2::ByteSpan aBody, double aNow)
    {
        uint8_t reason = 0;
        if (v2::DecodeDisconnect(aBody, reason) != v2::Status::Ok)
        {
            ++m_badFrames;
            return;
        }
        const std::string name = DisconnectReasonName(reason);
        m_shared.PushEvent("relay_disconnect " + name);
        const auto why = static_cast<coopv2::DisconnectReason>(reason);
        if (why == coopv2::DisconnectReason::Timeout || why == coopv2::DisconnectReason::ServerShutdown ||
            why == coopv2::DisconnectReason::SlowConsumer)
        {
            m_shared.Log(LogLevel::Warn, "the relay ended the session (" + name + "), reconnecting");
            LoseSession(aNow, "relay_lost", false);
            return;
        }
        Fail("the relay ended the session: " + name);
        m_shared.stopRequested = true;
    }

    void OnMessage(const v2::DeliveredMessage& aMessage, double aNow)
    {
        v2::Body body;
        if (v2::DecodeBody(aMessage.type, v2::ByteSpan(aMessage.body.data(), aMessage.body.size()), body) !=
            v2::Status::Ok)
        {
            ++m_decodeErrors;
            return;
        }
        if (const auto* response = std::get_if<coopv2::TimeResp>(&body))
        {
            OnTimeResponse(*response, aNow);
        }
        else if (const auto* joined = std::get_if<v2::PeerJoinedMsg>(&body))
        {
            OnPeerJoined(*joined);
        }
        else if (const auto* left = std::get_if<coopv2::PeerLeft>(&body))
        {
            RemovePeer(left->peer_id, DisconnectReasonName(left->reason));
        }
        else if (const auto* link = std::get_if<coopv2::LinkStats>(&body))
        {
            OnLinkStats(*link);
        }
        else if (const auto* player = std::get_if<v2::PlayerSnapshotMsg>(&body))
        {
            OnPlayerSnapshot(aMessage.peer, *player, aNow);
        }
        else if (const auto* script = std::get_if<v2::ScriptMsg>(&body))
        {
            OnScript(aMessage.peer, aMessage.reliable, *script);
        }
        else
        {
            ++m_ignoredMessages; // gameplay messages from other v2 clients; later milestones expose them
        }
    }

    void OnTimeResponse(const coopv2::TimeResp& aResponse, double aNow)
    {
        const v2::ClockResult result = m_clock.OnResponse(aResponse, aNow);
        if (result == v2::ClockResult::Stepped)
        {
            m_shared.Log(LogLevel::Warn, "relay clock estimate stepped to " +
                                             FormatDouble(m_clock.OffsetMs().value_or(0.0), 2) +
                                             " ms; resetting interpolation timing");
            m_shared.ResetRemoteTiming();
        }
        if (result == v2::ClockResult::Accepted || result == v2::ClockResult::Stepped)
        {
            const bool wasSynced = m_clockPublished;
            m_shared.SetClock(m_clock.Synced(), m_clock.OffsetMs().value_or(0.0));
            m_clockPublished = m_clock.Synced();
            if (m_clockPublished && !wasSynced)
            {
                m_shared.Log(LogLevel::Info, "relay clock synced: offset " + FormatDouble(*m_clock.OffsetMs(), 2) +
                                                 " ms, bound " + FormatDouble(m_clock.ErrorBoundMs().value_or(0.0), 2) +
                                                 " ms");
            }
        }
    }

    void OnPeerJoined(const v2::PeerJoinedMsg& aJoined)
    {
        const uint8_t id = aJoined.fixed.peer_id;
        if (id == 0 || id == m_localId || id == coopv2::kPeerBroadcast)
        {
            ++m_badFrames;
            return;
        }
        if (m_peers.contains(id))
        {
            // The same id joined again (a resumed session): its streams and snapshots start over.
            // A brand-new peer keeps snapshots that overtook its PEER_JOINED.
            RemovePeer(id, "replaced");
        }
        Peer peer;
        peer.id = id;
        peer.role = aJoined.fixed.role;
        peer.minor = aJoined.fixed.minor;
        peer.legacy = (aJoined.fixed.peer_flags & kPeerFlagLegacy) != 0;
        peer.name = aJoined.name;
        const std::string role = RoleName(peer.role);
        const bool legacy = peer.legacy;
        m_peers[id] = std::move(peer);
        UpdatePeerCounts();
        m_shared.Log(LogLevel::Info, "peer " + std::to_string(id) + " joined (" + role + (legacy ? ", legacy v1" : "") +
                                         ", '" + aJoined.name + "')");
        m_shared.PushEvent("peer_join " + std::to_string(id) + " " + role + (legacy ? " legacy" : ""));
    }

    void OnLinkStats(const coopv2::LinkStats& aStats)
    {
        const auto found = m_peers.find(aStats.peer_id);
        if (found != m_peers.end())
        {
            found->second.link = aStats;
        }
    }

    void OnPlayerSnapshot(uint8_t aSource, const v2::PlayerSnapshotMsg& aSnapshot, double aNow)
    {
        if (!m_clock.Synced())
        {
            ++m_unsyncedDropped; // sample_time cannot be placed before the relay clock is known
            return;
        }
        const auto peer = m_peers.find(aSource);
        if (peer != m_peers.end())
        {
            ++peer->second.snapshots;
        }
        const coopv2::PlayerSnapshot& base = aSnapshot.base;
        const double t = m_clock.UnwrapRelayMs(base.sample_time, aNow);
        const double arrival = m_clock.RelayMs(aNow);
        const v2::Vec3 pos{base.x, base.y, base.z};
        const v2::Vec3 vel{v2::CmsToVelocity(base.vx), v2::CmsToVelocity(base.vy), v2::CmsToVelocity(base.vz)};
        const bool legacy = (base.flags & coopv2::kPlayerLegacy) != 0;
        const bool teleported = (base.flags & coopv2::kPlayerTeleported) != 0;
        const v2::SnapshotState state{v2::I16ToPitch(base.pitch), base.move_state, base.flags, base.health};

        std::lock_guard lock(m_shared.remoteMutex);
        RemoteEntry& remote = m_shared.remotes[aSource];
        if (remote.lastSeq)
        {
            const int32_t distance = v2::SeqDiff(base.snap_seq, *remote.lastSeq);
            if (distance == 0)
            {
                ++remote.duplicates;
                return;
            }
            if (distance < 0)
            {
                ++remote.reordered;
            }
        }
        if (!remote.lastSeq || v2::SeqDiff(base.snap_seq, *remote.lastSeq) > 0)
        {
            remote.lastSeq = base.snap_seq;
        }
        ++remote.received;
        remote.buffer.Push(t, arrival, pos, vel, v2::U16ToYaw(base.yaw), teleported, !legacy, state);
    }

    void OnScript(uint8_t aSource, bool aReliable, const v2::ScriptMsg& aScript)
    {
        auto found = m_peers.find(aSource);
        if (!aReliable)
        {
            if (found != m_peers.end() && aScript.channel <= kLastUnreliableChannel)
            {
                Peer& peer = found->second;
                std::optional<uint8_t>& last = peer.lastUnreliable[aScript.channel];
                if (last && static_cast<int8_t>(static_cast<uint8_t>(aScript.flags - *last)) <= 0)
                {
                    ++peer.unreliableStale; // older than (or equal to) a message already delivered
                    return;
                }
                last = aScript.flags;
                ++peer.unreliableReceived;
            }
        }
        else if (found != m_peers.end())
        {
            ++found->second.reliableReceived;
        }
        if (!m_shared.PushInbox(ComposeMessage(aSource, aScript.channel, aScript.text), aReliable))
        {
            ++m_droppedInbound;
            if (aReliable && !m_inboxOverflowReported)
            {
                m_inboxOverflowReported = true;
                m_shared.Log(LogLevel::Error, "inbox full: dropping reliable messages (Net_Poll is not drained)");
            }
        }
    }

    // ---- send ---------------------------------------------------------------------------------

    void LiveTick(double aNow)
    {
        DrainOutbox();
        TakePlayer(aNow);
        PumpReliable(aNow);
        Flush(aNow);
    }

    void DrainOutbox()
    {
        std::deque<ScriptRequest> requests;
        {
            std::lock_guard lock(m_shared.outboxMutex);
            requests.swap(m_shared.outbox);
        }
        for (ScriptRequest& request : requests)
        {
            if (request.target != coopv2::kPeerBroadcast)
            {
                const auto found = m_peers.find(request.target);
                if (found == m_peers.end() || !found->second.TakesScript())
                {
                    ++m_droppedNoPeer;
                    continue;
                }
            }
            if (IsUnreliableChannel(request.channel))
            {
                const uint8_t sequence = ++m_scriptSequence[request.channel];
                QueueUnreliable(request.target, v2::Body{v2::ScriptMsg{request.channel, sequence, std::move(request.text)}});
                continue;
            }
            v2::Bytes body;
            if (v2::EncodeBody(v2::Body{v2::ScriptMsg{request.channel, 0, std::move(request.text)}}, body) !=
                v2::Status::Ok)
            {
                ++m_encodeErrors;
                continue;
            }
            if (m_reliableBacklog.size() >= m_shared.config.maxReliableBacklog)
            {
                ++m_droppedBacklog;
                continue;
            }
            m_reliableBacklog.push_back(ReliableOut{request.target, std::move(body)});
        }
    }

    double PlayerIntervalMs() const
    {
        return kPlayerPaceFraction * 1000.0 / static_cast<double>(m_playerHz);
    }

    void TakePlayer(double aNow)
    {
        if (aNow - m_lastPlayerSendMs < PlayerIntervalMs())
        {
            std::lock_guard lock(m_shared.outboxMutex);
            m_hasPendingPlayer = m_shared.pendingPlayer.has_value();
            return;
        }
        std::optional<PendingPlayer> pending;
        {
            std::lock_guard lock(m_shared.outboxMutex);
            pending.swap(m_shared.pendingPlayer);
        }
        m_hasPendingPlayer = false;
        if (!pending)
        {
            return;
        }
        if (!m_clock.Synced())
        {
            ++m_unsyncedDropped;
            return;
        }
        const PlayerState& state = pending->state;
        v2::PlayerSnapshotMsg snapshot;
        coopv2::PlayerSnapshot& base = snapshot.base;
        base.snap_seq = ++m_snapSequence;
        base.sample_time = LowBits(m_clock.RelayMs(pending->localMs));
        base.x = state.x;
        base.y = state.y;
        base.z = state.z;
        base.yaw = v2::YawToU16(state.yaw);
        base.pitch = v2::PitchToI16(state.pitch);
        base.vx = v2::VelocityToCms(state.vx);
        base.vy = v2::VelocityToCms(state.vy);
        base.vz = v2::VelocityToCms(state.vz);
        base.move_state = static_cast<uint8_t>(state.moveState);
        base.health = static_cast<uint8_t>(state.health);
        base.flags = static_cast<uint16_t>(state.flags);
        if (QueueUnreliable(coopv2::kPeerBroadcast, v2::Body{snapshot}))
        {
            ++m_playersSent;
            m_lastPlayerSendMs = aNow;
        }
    }

    // Moves reliable messages into the link while its window has room, paced below the relay's
    // reliable rate limit.
    void PumpReliable(double aNow)
    {
        const TransportConfig& config = m_shared.config;
        if (m_lastTokenMs)
        {
            m_reliableTokens =
                std::min(config.reliableBurst, m_reliableTokens + (aNow - *m_lastTokenMs) * config.reliablePerSecond / 1000.0);
        }
        m_lastTokenMs = aNow;
        while (!m_reliableBacklog.empty() && m_reliableTokens >= 1.0)
        {
            const ReliableOut& next = m_reliableBacklog.front();
            const v2::QueueResult result =
                m_link->QueueReliable(static_cast<uint8_t>(coopv2::MsgType::ScriptMsg), next.peer,
                                      v2::ByteSpan(next.body.data(), next.body.size()), aNow / 1000.0);
            if (result == v2::QueueResult::WindowFull)
            {
                return;
            }
            if (result != v2::QueueResult::Queued)
            {
                ++m_encodeErrors;
            }
            m_reliableBacklog.pop_front();
            m_reliableTokens -= 1.0;
        }
    }

    bool QueueUnreliable(uint8_t aPeer, const v2::Body& aBody)
    {
        OutMessage message;
        message.type = v2::TypeOf(aBody);
        message.peer = aPeer;
        if (v2::EncodeBody(aBody, message.body) != v2::Status::Ok)
        {
            ++m_encodeErrors;
            return false;
        }
        m_pending.push_back(std::move(message));
        return true;
    }

    double SinceSendMs(double aNow) const
    {
        return m_link->LastSend() ? aNow - *m_link->LastSend() * 1000.0 : 1e18;
    }

    // Whether something other than an ack or a keepalive waits for the next DATA packet.
    bool PayloadWaiting(double aNow) const
    {
        return !m_pending.empty() || m_link->ReliableDue(aNow / 1000.0) || m_clock.RequestDue(aNow);
    }

    // Sends what is waiting in as few DATA packets as possible, at most one burst every
    // minPacketIntervalMs: relay_v2.py counts every packet against 120/s, and a client over the
    // limit collects violations until it is kicked. The TIME_REQ is created here, right before the
    // packet leaves, so its t0 does not include that wait.
    void Flush(double aNow)
    {
        v2::Connection& link = *m_link;
        const double sinceSend = SinceSendMs(aNow);
        const bool ackDue = link.AckPending() && sinceSend >= m_shared.config.ackDelayMs;
        const bool keepalive = sinceSend >= m_shared.config.keepaliveMs;
        if (!PayloadWaiting(aNow) && !ackDue && !keepalive)
        {
            return;
        }
        if (sinceSend < m_shared.config.minPacketIntervalMs)
        {
            return;
        }
        if (m_clock.RequestDue(aNow))
        {
            QueueUnreliable(coopv2::kPeerRelay, v2::Body{m_clock.MakeRequest(aNow)});
        }
        std::vector<v2::LinkMessage> messages;
        messages.reserve(m_pending.size());
        for (const OutMessage& message : m_pending)
        {
            messages.push_back(v2::LinkMessage{message.type, message.peer,
                                               v2::ByteSpan(message.body.data(), message.body.size())});
        }
        std::vector<v2::Bytes> packets;
        const v2::Status status = link.BuildPackets(aNow / 1000.0, messages, ackDue || keepalive, packets);
        m_pending.clear();
        if (status != v2::Status::Ok)
        {
            ++m_encodeErrors;
            m_shared.Log(LogLevel::Error, std::string("could not build a DATA packet: ") + v2::ToString(status));
            return;
        }
        for (const v2::Bytes& packet : packets)
        {
            SendDatagram(packet);
        }
    }

    void SendDatagram(const v2::Bytes& aDatagram)
    {
        const int sent = sendto(m_socket, reinterpret_cast<const char*>(aDatagram.data()),
                                static_cast<int>(aDatagram.size()), 0,
                                reinterpret_cast<const sockaddr*>(&m_relayAddress), sizeof(m_relayAddress));
        if (sent == SOCKET_ERROR)
        {
            ++m_sendErrors;
            return;
        }
        ++m_packetsSent;
        m_bytesSent += aDatagram.size();
    }

    // ---- peers and session loss -----------------------------------------------------------------

    void RemovePeer(uint8_t aId, std::string_view aReason)
    {
        if (m_peers.erase(aId) == 0)
        {
            return;
        }
        m_shared.RemoveRemote(aId);
        UpdatePeerCounts();
        m_shared.Log(LogLevel::Info, "peer " + std::to_string(aId) + " left (" + std::string(aReason) + ")");
        m_shared.PushEvent("peer_leave " + std::to_string(aId) + " " + std::string(aReason));
    }

    void UpdatePeerCounts()
    {
        int script = 0;
        for (const auto& [id, peer] : m_peers)
        {
            script += peer.TakesScript() ? 1 : 0;
        }
        m_shared.peerCount = static_cast<int>(m_peers.size());
        m_shared.scriptPeers = script;
        m_statsDirty = true;
    }

    // The session is gone (relay silent or ended it): every peer leaves, unsent and unacknowledged
    // messages are dropped (the peers restart their streams too), and the handshake starts over.
    // aResume asks the relay for the same peer id through resume_token.
    void LoseSession(double aNow, std::string_view aReason, bool aResume)
    {
        std::vector<uint8_t> ids;
        for (const auto& [id, peer] : m_peers)
        {
            ids.push_back(id);
        }
        for (const uint8_t id : ids)
        {
            RemovePeer(id, aReason);
        }
        m_droppedOnReconnect += m_reliableBacklog.size() + (m_link ? m_link->PendingReliable() : 0);
        m_reliableBacklog.clear();
        m_pending.clear();
        m_link.reset();
        m_clock = v2::ClockSync{};
        m_clockPublished = false;
        m_shared.ClearRemotes();
        m_resumeToken = aResume ? m_token : 0;
        m_token = 0;
        m_localId = 0;
        m_shared.localId = 0;
        m_shared.scriptAllowed = false;
        StartHandshake(aNow, ConnectionState::Reconnecting);
    }

    // ---- state / stats ------------------------------------------------------------------------

    void SetState(ConnectionState aState)
    {
        m_shared.state = static_cast<int>(aState);
        m_statsDirty = true;
    }

    void Fail(std::string aReason)
    {
        m_lastError = aReason;
        m_shared.Log(LogLevel::Error, aReason);
        m_shared.PushEvent("error " + aReason);
        SetState(ConnectionState::Error);
    }

    void PublishStats(double aNow)
    {
        StatsSnapshot snapshot;
        snapshot.host = m_options.host;
        snapshot.port = m_options.port;
        snapshot.room = m_options.room;
        snapshot.role = m_phase == Phase::Live ? RoleName(m_role) : ToString(static_cast<Role>(m_options.role));
        snapshot.minor = m_phase == Phase::Live ? m_minor : -1;
        snapshot.uptimeSeconds = (aNow - m_startedMs) / 1000.0;
        if (m_link)
        {
            const v2::LinkStats& link = m_link->Stats();
            if (link.rttSamples > 0)
            {
                snapshot.relayRttMs = m_link->RttMs();
            }
            snapshot.rtoMs = m_link->Rto() * 1000.0;
            snapshot.lossIn = m_link->LossIn();
            snapshot.lossOut = m_link->LossOut();
            snapshot.reliableSent = link.reliableSent;
            snapshot.reliableResent = link.reliableResent;
            snapshot.reliableDelivered = link.reliableDelivered;
            snapshot.reliableDuplicates = link.reliableDuplicates;
            snapshot.reliablePending = m_link->PendingReliable();
            snapshot.rttSamples = link.rttSamples;
            snapshot.rttSkipped = link.rttSkipped;
        }
        snapshot.reliableBacklog = m_reliableBacklog.size();
        snapshot.clockSynced = m_clock.Synced();
        snapshot.clockOffsetMs = m_clock.OffsetMs();
        snapshot.clockBoundMs = m_clock.ErrorBoundMs();
        snapshot.clockRttMs = m_clock.RttMs();
        snapshot.clockSamples = m_clock.Samples();
        snapshot.clockSteps = m_clock.Steps();
        snapshot.packetsSent = m_packetsSent;
        snapshot.packetsReceived = m_packetsReceived;
        snapshot.bytesSent = m_bytesSent;
        snapshot.bytesReceived = m_bytesReceived;
        snapshot.badFrames = m_badFrames;
        snapshot.decodeErrors = m_decodeErrors + m_encodeErrors;
        snapshot.foreignDatagrams = m_foreignDatagrams;
        snapshot.sendErrors = m_sendErrors;
        snapshot.droppedInbound = m_droppedInbound;
        snapshot.droppedNoPeer = m_droppedNoPeer;
        snapshot.droppedBacklog = m_droppedBacklog;
        snapshot.droppedOnReconnect = m_droppedOnReconnect;
        snapshot.ignoredMessages = m_ignoredMessages;
        snapshot.playersSent = m_playersSent;
        snapshot.unsyncedDropped = m_unsyncedDropped;
        snapshot.handshakes = m_handshakes;
        snapshot.lastError = m_lastError;
        for (const auto& [id, peer] : m_peers)
        {
            PeerStatsSnapshot entry;
            entry.id = id;
            entry.role = RoleName(peer.role);
            entry.name = peer.name;
            entry.minor = peer.minor;
            entry.legacy = peer.legacy;
            if (peer.link)
            {
                entry.relayRttMs = peer.link->rtt_ms;
                entry.lossIn = peer.link->loss_in_permille / 1000.0;
                entry.lossOut = peer.link->loss_out_permille / 1000.0;
            }
            entry.unreliableReceived = peer.unreliableReceived;
            entry.unreliableStale = peer.unreliableStale;
            entry.reliableReceived = peer.reliableReceived;
            entry.snapshots = peer.snapshots;
            snapshot.peers.push_back(std::move(entry));
        }
        std::lock_guard lock(m_shared.statsMutex);
        m_shared.stats = std::move(snapshot);
    }

    SharedState& m_shared;
    const ConnectOptions m_options;
    std::mt19937_64 m_random;

    SOCKET m_socket = INVALID_SOCKET;
    WSAEVENT m_socketEvent = WSA_INVALID_EVENT;
    HANDLE m_timer = nullptr; // high-resolution waitable timer for the loop's short waits
    sockaddr_in m_relayAddress{};

    // handshake
    Phase m_phase = Phase::Hello;
    uint64_t m_nonce = 0;
    uint64_t m_resumeToken = 0;
    v2::Cookie m_cookie{};
    double m_handshakeStartMs = 0.0;
    double m_nextHandshakeMs = 0.0;
    double m_challengeAtMs = 0.0;
    bool m_answered = false;
    bool m_noAnswerReported = false;
    uint64_t m_handshakes = 0;

    // session
    uint64_t m_token = 0;
    uint8_t m_localId = 0;
    uint8_t m_role = 0;
    uint8_t m_minor = 0;
    uint8_t m_playerHz = 30;
    std::optional<v2::Connection> m_link;
    v2::ClockSync m_clock;
    bool m_clockPublished = false;
    double m_lastHeardMs = 0.0;
    std::map<uint8_t, Peer> m_peers;

    // outgoing
    std::vector<OutMessage> m_pending;
    std::deque<ReliableOut> m_reliableBacklog;
    double m_reliableTokens;
    std::optional<double> m_lastTokenMs;
    std::array<uint8_t, kLastUnreliableChannel + 1> m_scriptSequence{};
    uint16_t m_snapSequence = 0;
    double m_lastPlayerSendMs = -1e18;
    bool m_hasPendingPlayer = false;

    // stats
    bool m_statsDirty = true;
    double m_startedMs = 0.0;
    double m_nextStatsMs = 0.0;
    bool m_inboxOverflowReported = false;
    uint64_t m_packetsSent = 0;
    uint64_t m_packetsReceived = 0;
    uint64_t m_bytesSent = 0;
    uint64_t m_bytesReceived = 0;
    uint64_t m_badFrames = 0;
    uint64_t m_decodeErrors = 0;
    uint64_t m_encodeErrors = 0;
    uint64_t m_foreignDatagrams = 0;
    uint64_t m_sendErrors = 0;
    uint64_t m_droppedInbound = 0;
    uint64_t m_droppedNoPeer = 0;
    uint64_t m_droppedBacklog = 0;
    uint64_t m_droppedOnReconnect = 0;
    uint64_t m_ignoredMessages = 0;
    uint64_t m_playersSent = 0;
    uint64_t m_unsyncedDropped = 0;
    std::string m_lastError;
};

void RunSession(SharedState& aShared, ConnectOptions aOptions, uint64_t aSeed)
{
    try
    {
        Session session(aShared, std::move(aOptions), aSeed);
        session.Run();
    }
    catch (const std::exception& error)
    {
        // Never let an exception escape a thread inside the game process.
        aShared.state = static_cast<int>(ConnectionState::Error);
        aShared.Log(LogLevel::Error, std::string("network thread stopped: ") + error.what());
        aShared.PushEvent(std::string("error network thread stopped: ") + error.what());
    }
}

uint64_t RandomSeed()
{
    std::random_device device;
    return (static_cast<uint64_t>(device()) << 32) ^ device() ^
           static_cast<uint64_t>(std::chrono::steady_clock::now().time_since_epoch().count());
}
} // namespace

// ---- Transport -------------------------------------------------------------------------------------

Transport::Transport(TransportConfig aConfig)
    : m_shared(std::make_unique<SharedState>(aConfig))
{
}

Transport::~Transport()
{
    Disconnect();
}

void Transport::SetLogSink(LogSink aSink)
{
    std::lock_guard lifecycle(m_lifecycleMutex);
    m_shared->log = std::move(aSink);
}

bool Transport::Connect(std::string_view aHost, int aPort, std::string_view aRoom)
{
    ConnectOptions options;
    options.host = std::string(aHost);
    options.port = aPort;
    options.room = aRoom.empty() ? std::string(kDefaultRoom) : std::string(aRoom);
    return Connect(options);
}

bool Transport::Connect(const ConnectOptions& aOptions)
{
    std::lock_guard lifecycle(m_lifecycleMutex);
    const std::string problem = ValidateConnectOptions(aOptions);
    if (!problem.empty())
    {
        m_shared->Log(LogLevel::Error, "Net_Connect: " + problem);
        return false;
    }
    if (!m_shared->wakeEvent)
    {
        m_shared->Log(LogLevel::Error, "Net_Connect: could not create the wake event");
        return false;
    }

    StopThread();

    {
        std::lock_guard inboxLock(m_shared->inboxMutex);
        m_shared->inbox.clear();
    }
    {
        std::lock_guard outboxLock(m_shared->outboxMutex);
        m_shared->outbox.clear();
        m_shared->pendingPlayer.reset();
    }
    m_shared->ClearRemotes();
    m_shared->stopRequested = false;
    m_shared->state = static_cast<int>(ConnectionState::Resolving);
    m_shared->localId = 0;
    m_shared->peerCount = 0;
    m_shared->scriptPeers = 0;
    m_shared->scriptAllowed = false;
    ResetEvent(m_shared->wakeEvent);

    try
    {
        m_thread = std::thread(RunSession, std::ref(*m_shared), aOptions, RandomSeed());
    }
    catch (const std::system_error& error)
    {
        m_shared->state = static_cast<int>(ConnectionState::Error);
        m_shared->Log(LogLevel::Error, std::string("Net_Connect: cannot start network thread: ") + error.what());
        return false;
    }
    return true;
}

void Transport::Disconnect()
{
    std::lock_guard lifecycle(m_lifecycleMutex);
    StopThread();
}

void Transport::StopThread()
{
    if (!m_thread.joinable())
    {
        return;
    }
    m_shared->stopRequested = true;
    m_shared->Wake();
    m_thread.join();
}

bool Transport::Send(int aChannel, std::string_view aPayload, int aTarget)
{
    const bool validChannel = IsUnreliableChannel(aChannel) || IsReliableChannel(aChannel);
    const bool validTarget = aTarget == kBroadcastPeer || (aTarget > 0 && aTarget < kBroadcastPeer);
    if (!validChannel || !validTarget || aPayload.size() > kMaxPayloadSize ||
        !v2::IsValidText(aPayload, v2::TextRule::Script))
    {
        ++m_shared->refusedSends;
        return false;
    }
    if (static_cast<ConnectionState>(m_shared->state.load()) != ConnectionState::Connected ||
        !m_shared->scriptAllowed.load() || m_shared->scriptPeers.load() == 0)
    {
        ++m_shared->refusedSends;
        return false;
    }
    {
        std::lock_guard lock(m_shared->outboxMutex);
        if (m_shared->outbox.size() >= m_shared->config.maxOutbox)
        {
            ++m_shared->refusedSends;
            return false;
        }
        m_shared->outbox.push_back(
            {static_cast<uint8_t>(aChannel), static_cast<uint8_t>(aTarget), std::string(aPayload)});
    }
    m_shared->Wake();
    return true;
}

bool Transport::Poll(std::string& aMessage)
{
    std::lock_guard lock(m_shared->inboxMutex);
    if (m_shared->inbox.empty())
    {
        return false;
    }
    aMessage = std::move(m_shared->inbox.front());
    m_shared->inbox.pop_front();
    return true;
}

bool Transport::PushPlayer(const PlayerState& aState)
{
    if (!ValidatePlayerState(aState).empty() ||
        static_cast<ConnectionState>(m_shared->state.load()) != ConnectionState::Connected ||
        !m_shared->clockReady.load())
    {
        ++m_shared->refusedPlayers;
        return false;
    }
    {
        std::lock_guard lock(m_shared->outboxMutex);
        PendingPlayer next{aState, MonotonicMs()};
        if (m_shared->pendingPlayer && (m_shared->pendingPlayer->state.flags & coopv2::kPlayerTeleported) != 0)
        {
            // The unsent sample was a teleport: the newer one replaces it but must not be
            // interpolated from the place before the teleport either.
            next.state.flags |= coopv2::kPlayerTeleported;
        }
        m_shared->pendingPlayer = next;
        ++m_shared->playersPushed;
    }
    m_shared->Wake();
    return true;
}

bool Transport::SampleRemote(int aPeer, RemotePose& aPose)
{
    if (aPeer <= 0 || aPeer >= kBroadcastPeer)
    {
        return false;
    }
    const double nowLocal = MonotonicMs();
    std::lock_guard lock(m_shared->remoteMutex);
    if (!m_shared->clockSynced)
    {
        return false;
    }
    const auto found = m_shared->remotes.find(aPeer);
    if (found == m_shared->remotes.end() || found->second.buffer.Empty())
    {
        return false;
    }
    RemoteEntry& remote = found->second;
    const double relayNow = nowLocal + m_shared->clockOffsetMs;
    const double frameMs =
        remote.lastSampleLocalMs ? std::clamp(nowLocal - *remote.lastSampleLocalMs, 0.0, kMaxFrameMs) : kDefaultFrameMs;
    remote.lastSampleLocalMs = nowLocal;
    const double renderTime = remote.buffer.RenderTime(relayNow, frameMs);
    const std::optional<v2::SampledPose> pose = remote.buffer.SampleAt(renderTime);
    if (!pose)
    {
        return false;
    }
    aPose.mode = pose->mode;
    aPose.x = pose->pos[0];
    aPose.y = pose->pos[1];
    aPose.z = pose->pos[2];
    aPose.yaw = pose->yaw;
    aPose.pitch = pose->pitch;
    aPose.vx = pose->vel[0];
    aPose.vy = pose->vel[1];
    aPose.vz = pose->vel[2];
    aPose.moveState = pose->state.moveState;
    aPose.flags = pose->state.flags;
    aPose.health = pose->state.health;
    aPose.delayMs = relayNow - renderTime;
    aPose.aheadMs = renderTime - remote.buffer.NewestTime().value_or(renderTime);
    return true;
}

ConnectionState Transport::State() const
{
    return static_cast<ConnectionState>(m_shared->state.load());
}

int Transport::LocalId() const
{
    return m_shared->localId.load();
}

int Transport::PeerCount() const
{
    return m_shared->peerCount.load();
}

bool Transport::ClockSynced() const
{
    return m_shared->clockReady.load();
}

std::optional<double> Transport::RelayNowMs() const
{
    const double nowLocal = MonotonicMs();
    std::lock_guard lock(m_shared->remoteMutex);
    if (!m_shared->clockSynced)
    {
        return std::nullopt;
    }
    return nowLocal + m_shared->clockOffsetMs;
}

std::string Transport::StatsJson() const
{
    StatsSnapshot stats;
    {
        std::lock_guard lock(m_shared->statsMutex);
        stats = m_shared->stats;
    }
    size_t inboxSize = 0;
    size_t outboxSize = 0;
    uint64_t playersPushed = 0;
    {
        std::lock_guard lock(m_shared->inboxMutex);
        inboxSize = m_shared->inbox.size();
    }
    {
        std::lock_guard lock(m_shared->outboxMutex);
        outboxSize = m_shared->outbox.size();
        playersPushed = m_shared->playersPushed;
    }
    struct RemoteStats
    {
        uint64_t received = 0;
        uint64_t duplicates = 0;
        uint64_t reordered = 0;
        size_t buffered = 0;
        double targetDelayMs = 0.0;
        v2::InterpCounts counts;
    };
    std::map<int, RemoteStats> remotes;
    {
        std::lock_guard lock(m_shared->remoteMutex);
        for (const auto& [id, remote] : m_shared->remotes)
        {
            remotes[id] = RemoteStats{remote.received, remote.duplicates, remote.reordered, remote.buffer.Size(),
                                      remote.buffer.TargetDelayMs(), remote.buffer.Counts()};
        }
    }

    std::ostringstream json;
    json << "{\"state\":\"" << ToString(State()) << "\"";
    json << ",\"id\":" << LocalId();
    json << ",\"role\":\"" << stats.role << "\"";
    json << ",\"version\":\"" << kVersionString << "\"";
    json << ",\"wire\":\"v2." << (stats.minor >= 0 ? std::to_string(stats.minor) : std::string("?")) << "\"";
    json << ",\"relay\":\"" << JsonEscape(stats.host) << ":" << stats.port << "\"";
    json << ",\"room\":\"" << JsonEscape(stats.room) << "\"";
    json << ",\"relayRttMs\":" << OptionalDouble(stats.relayRttMs);
    json << ",\"rtoMs\":" << OptionalDouble(stats.rtoMs);
    json << ",\"lossIn\":" << OptionalDouble(stats.lossIn, 4) << ",\"lossOut\":" << OptionalDouble(stats.lossOut, 4);
    json << ",\"clock\":{\"synced\":" << (stats.clockSynced ? "true" : "false")
         << ",\"offsetMs\":" << OptionalDouble(stats.clockOffsetMs, 2)
         << ",\"boundMs\":" << OptionalDouble(stats.clockBoundMs, 2) << ",\"rttMs\":" << OptionalDouble(stats.clockRttMs, 2)
         << ",\"samples\":" << stats.clockSamples << ",\"steps\":" << stats.clockSteps << "}";
    json << ",\"uptimeS\":" << FormatDouble(stats.uptimeSeconds);
    json << ",\"txPackets\":" << stats.packetsSent << ",\"rxPackets\":" << stats.packetsReceived;
    json << ",\"txBytes\":" << stats.bytesSent << ",\"rxBytes\":" << stats.bytesReceived;
    json << ",\"inQueue\":" << inboxSize << ",\"outQueue\":" << outboxSize;
    json << ",\"relSent\":" << stats.reliableSent << ",\"relResent\":" << stats.reliableResent;
    json << ",\"relDelivered\":" << stats.reliableDelivered << ",\"relDup\":" << stats.reliableDuplicates;
    json << ",\"relPending\":" << stats.reliablePending << ",\"relBacklog\":" << stats.reliableBacklog;
    json << ",\"rttSamples\":" << stats.rttSamples << ",\"rttSkipped\":" << stats.rttSkipped;
    json << ",\"playersPushed\":" << playersPushed << ",\"playersSent\":" << stats.playersSent;
    json << ",\"unsyncedDropped\":" << stats.unsyncedDropped << ",\"handshakes\":" << stats.handshakes;
    json << ",\"badFrames\":" << stats.badFrames << ",\"decodeErrors\":" << stats.decodeErrors;
    json << ",\"foreign\":" << stats.foreignDatagrams << ",\"ignored\":" << stats.ignoredMessages;
    json << ",\"sendErrors\":" << stats.sendErrors << ",\"droppedIn\":" << stats.droppedInbound;
    json << ",\"droppedNoPeer\":" << stats.droppedNoPeer << ",\"droppedBacklog\":" << stats.droppedBacklog;
    json << ",\"droppedOnReconnect\":" << stats.droppedOnReconnect;
    json << ",\"refusedSends\":" << m_shared->refusedSends.load();
    json << ",\"refusedPlayers\":" << m_shared->refusedPlayers.load();
    json << ",\"lastError\":\"" << JsonEscape(stats.lastError) << "\"";
    json << ",\"peers\":[";
    for (size_t index = 0; index < stats.peers.size(); ++index)
    {
        const PeerStatsSnapshot& peer = stats.peers[index];
        json << (index > 0 ? "," : "") << "{\"id\":" << peer.id << ",\"role\":\"" << peer.role << "\"";
        json << ",\"name\":\"" << JsonEscape(peer.name) << "\",\"minor\":" << peer.minor;
        json << ",\"legacy\":" << (peer.legacy ? "true" : "false");
        // Round trip through the relay: our hop plus the relay's measurement of theirs.
        const std::optional<double> endToEnd = stats.relayRttMs && peer.relayRttMs
                                                   ? std::optional<double>(*stats.relayRttMs + *peer.relayRttMs)
                                                   : std::nullopt;
        json << ",\"rttMs\":" << OptionalDouble(endToEnd) << ",\"relayRttMs\":" << OptionalDouble(peer.relayRttMs);
        json << ",\"lossIn\":" << OptionalDouble(peer.lossIn, 3) << ",\"lossOut\":" << OptionalDouble(peer.lossOut, 3);
        json << ",\"unrelRecv\":" << peer.unreliableReceived << ",\"unrelStale\":" << peer.unreliableStale;
        json << ",\"relRecv\":" << peer.reliableReceived << ",\"snapshots\":" << peer.snapshots;
        const auto remote = remotes.find(peer.id);
        if (remote != remotes.end())
        {
            const RemoteStats& entry = remote->second;
            json << ",\"interp\":{\"received\":" << entry.received << ",\"duplicates\":" << entry.duplicates
                 << ",\"reordered\":" << entry.reordered << ",\"buffered\":" << entry.buffered
                 << ",\"targetDelayMs\":" << FormatDouble(entry.targetDelayMs)
                 << ",\"interpolated\":" << entry.counts.interpolated << ",\"extrapolated\":" << entry.counts.extrapolated
                 << ",\"held\":" << entry.counts.held << ",\"early\":" << entry.counts.early
                 << ",\"late\":" << entry.counts.late << ",\"teleports\":" << entry.counts.teleports << "}";
        }
        json << "}";
    }
    json << "]}";
    return json.str();
}
} // namespace coopnet
