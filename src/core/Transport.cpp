#include "Transport.hpp"

#include "Reliability.hpp"
#include "Version.hpp"

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
#include <cstdio>
#include <cstring>
#include <deque>
#include <exception>
#include <limits>
#include <map>
#include <random>
#include <span>
#include <sstream>
#include <system_error>
#include <vector>

namespace coopnet
{
namespace
{
constexpr size_t kMaxRoomLength = 64;
constexpr size_t kUnreliableChannelSlots = kLastUnreliableChannel + 1;
constexpr DWORD kMaxWaitMillis = 20;
constexpr int kSocketBufferBytes = 1 << 20;

uint64_t NowMicros()
{
    using namespace std::chrono;
    return static_cast<uint64_t>(duration_cast<microseconds>(steady_clock::now().time_since_epoch()).count());
}

std::string ComposeMessage(uint16_t aSender, uint8_t aChannel, std::string_view aPayload)
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

std::string FormatDouble(double aValue)
{
    char buffer[32];
    std::snprintf(buffer, sizeof(buffer), "%.1f", aValue);
    return buffer;
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

struct OutgoingRequest
{
    uint8_t channel = 0;
    uint16_t target = kBroadcastId;
    std::string payload;
};

struct PeerStatsSnapshot
{
    uint16_t id = 0;
    uint32_t nonce = 0;
    bool unresponsive = false;
    bool rttValid = false;
    double rttMs = 0.0;
    double rttVariationMs = 0.0;
    double rtoMs = 0.0;
    double lastHeardMsAgo = 0.0;
    size_t inFlight = 0;
    size_t backlog = 0;
    size_t buffered = 0;
    ReliableStats reliable;
    uint64_t unreliableSent = 0;
    uint64_t unreliableReceived = 0;
    uint64_t unreliableStale = 0;
    uint64_t unreliableDropped = 0;
    uint64_t pingsSent = 0;
    uint64_t pongsReceived = 0;
};

struct StatsSnapshot
{
    std::string host;
    int port = 0;
    std::string room;
    bool relayRttValid = false;
    double relayRttMs = 0.0;
    double uptimeSeconds = 0.0;
    uint64_t packetsSent = 0;
    uint64_t packetsReceived = 0;
    uint64_t bytesSent = 0;
    uint64_t bytesReceived = 0;
    uint64_t badFrames = 0;
    uint64_t foreignDatagrams = 0;
    uint64_t unknownPeerFrames = 0;
    uint64_t sendErrors = 0;
    uint64_t droppedInbound = 0;
    uint64_t droppedNoPeer = 0;
    uint64_t droppedBacklog = 0;
    std::string lastError;
    std::vector<PeerStatsSnapshot> peers;
};
} // namespace

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

    bool PushInbox(std::string aMessage, bool aForce)
    {
        std::lock_guard lock(inboxMutex);
        if (!aForce && inbox.size() >= config.maxInbox)
        {
            return false;
        }
        inbox.push_back(std::move(aMessage));
        return true;
    }

    void PushEvent(std::string_view aText)
    {
        PushInbox(ComposeMessage(kRelayId, kControlChannel, aText), true);
    }

    void Wake() const
    {
        if (wakeEvent)
        {
            SetEvent(wakeEvent);
        }
    }

    const TransportConfig config;
    LogSink log;
    HANDLE wakeEvent = nullptr;

    std::atomic<bool> stopRequested{false};
    std::atomic<int> state{static_cast<int>(ConnectionState::Idle)};
    std::atomic<int> localId{0};
    std::atomic<int> peerCount{0};
    std::atomic<uint64_t> refusedSends{0};

    mutable std::mutex inboxMutex;
    std::deque<std::string> inbox;

    mutable std::mutex outboxMutex;
    std::deque<OutgoingRequest> outbox;

    mutable std::mutex statsMutex;
    StatsSnapshot stats;
};

namespace
{
// Owns the socket and all protocol state. Lives entirely on the network thread.
class Session
{
public:
    Session(SharedState& aShared, std::string aHost, int aPort, std::string aRoom, uint32_t aNonce)
        : m_shared(aShared)
        , m_host(std::move(aHost))
        , m_port(aPort)
        , m_room(std::move(aRoom))
        , m_nonce(aNonce)
        , m_random(aNonce)
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
        m_startedMicros = NowMicros();
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
        m_shared.localId = 0;
        PublishStats(NowMicros());
        // A failed Open already pushed "error ..."; a stop during it (for example while the host
        // name was still resolving) ends the session like a normal disconnect.
        if (opened || m_shared.stopRequested.load())
        {
            m_shared.PushEvent("disconnected");
        }
    }

private:
    struct Peer
    {
        uint16_t id = 0;
        uint32_t nonce = 0;
        bool reportedUnresponsive = false;
        uint64_t createdMicros = 0;
        uint64_t lastHeardMicros = 0;
        uint64_t nextPingMicros = 0;
        ReliableEndpoint reliable;
        std::array<uint16_t, kUnreliableChannelSlots> sendSequence{};
        std::array<uint16_t, kUnreliableChannelSlots> lastReceived{};
        std::array<bool, kUnreliableChannelSlots> haveReceived{};
        uint64_t unreliableSent = 0;
        uint64_t unreliableReceived = 0;
        uint64_t unreliableStale = 0;
        uint64_t unreliableDropped = 0;
        uint64_t pingsSent = 0;
        uint64_t pongsReceived = 0;
    };

    // ---- lifecycle ------------------------------------------------------------------------

    bool Open()
    {
        WSADATA data{};
        const int startup = WSAStartup(MAKEWORD(2, 2), &data);
        if (startup != 0)
        {
            Fail("WSAStartup failed (error " + std::to_string(startup) + ")");
            return false;
        }
        m_wsaStarted = true;

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

        SetState(ConnectionState::Connecting);
        char address[INET_ADDRSTRLEN] = {};
        inet_ntop(AF_INET, &m_relayAddress.sin_addr, address, sizeof(address));
        m_shared.Log(LogLevel::Info, "connecting to relay " + std::string(address) + ":" + std::to_string(m_port) +
                                         " room '" + m_room + "'");
        m_shared.PushEvent("connecting " + std::string(address) + ":" + std::to_string(m_port));
        return true;
    }

    // Resolves m_host with an overlapped GetAddrInfoExW and waits on both the lookup and the wake
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
        const std::wstring host = Utf8ToWide(m_host);
        if (host.empty())
        {
            Fail("cannot resolve '" + m_host + "' (not valid UTF-8)");
            return false;
        }
        const std::wstring service = std::to_wstring(m_port);
        auto lookup = std::make_unique<AddressLookup>();
        if (lookup->event == nullptr)
        {
            Fail("cannot resolve '" + m_host + "': CreateEvent failed (error " + std::to_string(GetLastError()) +
                 ")");
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
                Fail("cannot resolve '" + m_host + "': WaitForMultipleObjects failed (error " +
                     std::to_string(GetLastError()) + ")");
                CancelLookup(std::move(lookup));
                return false;
            }
            status = GetAddrInfoExOverlappedResult(&lookup->overlapped);
        }
        if (status != NO_ERROR || lookup->result == nullptr || lookup->result->ai_addr == nullptr)
        {
            Fail("cannot resolve '" + m_host + "' (error " + std::to_string(status) + ")");
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
        m_shared.Log(LogLevel::Warn, "DNS lookup of '" + m_host + "' did not confirm cancellation within " +
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
        if (m_wsaStarted)
        {
            WSACleanup();
            m_wsaStarted = false;
        }
    }

    void SayGoodbye()
    {
        if (m_socket == INVALID_SOCKET || !m_welcomed)
        {
            return;
        }
        ByteWriter bye;
        bye.U8(static_cast<uint8_t>(ControlOp::Bye));
        // Unreliable by nature; two copies make a lost goodbye unlikely. The relay's idle
        // timeout covers the rest.
        SendToRelay(bye);
        SendToRelay(bye);
    }

    void Loop()
    {
        const HANDLE handles[2] = {m_socketEvent, m_shared.wakeEvent};
        while (!m_shared.stopRequested.load())
        {
            uint64_t now = NowMicros();
            const uint64_t wake = NextWakeMicros(now);
            const DWORD waitMillis =
                wake <= now ? 0 : static_cast<DWORD>(std::min<uint64_t>((wake - now + 999) / 1000, kMaxWaitMillis));
            const DWORD waitResult = WaitForMultipleObjects(2, handles, FALSE, waitMillis);
            if (waitResult == WAIT_FAILED)
            {
                Fail("WaitForMultipleObjects failed (error " + std::to_string(GetLastError()) + ")");
                return;
            }
            if (m_shared.stopRequested.load())
            {
                return;
            }

            now = NowMicros();
            WSANETWORKEVENTS networkEvents{};
            WSAEnumNetworkEvents(m_socket, m_socketEvent, &networkEvents);
            DrainSocket(now);
            DrainOutbox();
            FlushReliable(now);
            RunTimers(now);
            FlushAcks();
            if (m_statsDirty || now >= m_nextStatsMicros)
            {
                PublishStats(now);
                m_statsDirty = false;
                m_nextStatsMicros = now + m_shared.config.statsIntervalMicros;
            }
        }
    }

    uint64_t NextWakeMicros(uint64_t aNow) const
    {
        uint64_t wake = aNow + kMaxWaitMillis * 1000ull;
        wake = std::min(wake, m_nextStatsMicros);
        wake = std::min(wake, m_welcomed ? m_nextRelayPingMicros : m_nextHelloMicros);
        for (const auto& [id, peer] : m_peers)
        {
            wake = std::min(wake, peer->reliable.NextDueMicros());
            wake = std::min(wake, peer->nextPingMicros);
        }
        return wake;
    }

    // ---- receive --------------------------------------------------------------------------

    void DrainSocket(uint64_t aNow)
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
            HandleDatagram(std::span<const uint8_t>(buffer.data(), static_cast<size_t>(received)), aNow);
        }
    }

    void HandleDatagram(std::span<const uint8_t> aDatagram, uint64_t aNow)
    {
        FrameHeader header;
        std::span<const uint8_t> payload;
        if (DecodeFrame(aDatagram, header, payload) != DecodeResult::Ok)
        {
            ++m_badFrames;
            return;
        }
        if (header.sender == kRelayId)
        {
            HandleRelayFrame(header, payload, aNow);
            return;
        }
        HandlePeerFrame(header, payload, aNow);
    }

    void HandleRelayFrame(const FrameHeader& aHeader, std::span<const uint8_t> aPayload, uint64_t aNow)
    {
        if (aHeader.channel != kControlChannel)
        {
            ++m_badFrames;
            return;
        }
        ByteReader reader(aPayload);
        uint8_t op = 0;
        if (!reader.U8(op))
        {
            ++m_badFrames;
            return;
        }
        m_lastRelayHeardMicros = aNow;

        switch (static_cast<ControlOp>(op))
        {
        case ControlOp::Welcome:
            HandleWelcome(reader, aNow);
            break;
        case ControlOp::Peers:
            if (m_welcomed)
            {
                HandlePeerList(reader, aNow);
            }
            break;
        case ControlOp::Pong:
        {
            uint32_t pingId = 0;
            uint64_t sentMicros = 0;
            if (reader.U32(pingId) && reader.U64(sentMicros) && sentMicros <= aNow)
            {
                m_relayRtt.AddSample(aNow - sentMicros);
            }
            break;
        }
        case ControlOp::Reject:
        {
            uint8_t length = 0;
            std::string reason = "rejected";
            if (reader.U8(length))
            {
                reader.Bytes(length, reason);
            }
            m_shared.PushEvent("rejected " + reason);
            Fail("relay rejected the connection: " + reason);
            m_shared.stopRequested = true;
            break;
        }
        default:
            ++m_badFrames;
            break;
        }
    }

    void HandleWelcome(ByteReader& aReader, uint64_t aNow)
    {
        uint16_t assignedId = 0;
        uint32_t echoedNonce = 0;
        if (!aReader.U16(assignedId) || !aReader.U32(echoedNonce) || echoedNonce != m_nonce || assignedId == kRelayId ||
            assignedId == kBroadcastId)
        {
            ++m_badFrames;
            return;
        }
        if (m_welcomed && assignedId == m_localId)
        {
            return; // duplicate WELCOME for a HELLO resend
        }
        m_localId = assignedId;
        m_welcomed = true;
        m_nextRelayPingMicros = aNow;
        m_shared.localId = assignedId;
        SetState(ConnectionState::Connected);
        m_shared.Log(LogLevel::Info, "welcome: local id " + std::to_string(assignedId));
        m_shared.PushEvent("welcome " + std::to_string(assignedId));
    }

    void HandlePeerList(ByteReader& aReader, uint64_t aNow)
    {
        uint16_t count = 0;
        if (!aReader.U16(count))
        {
            ++m_badFrames;
            return;
        }
        std::map<uint16_t, uint32_t> listed;
        for (uint16_t index = 0; index < count; ++index)
        {
            uint16_t id = 0;
            uint32_t nonce = 0;
            if (!aReader.U16(id) || !aReader.U32(nonce))
            {
                ++m_badFrames;
                return;
            }
            if (id != m_localId && id != kRelayId && id != kBroadcastId)
            {
                listed[id] = nonce;
            }
        }

        for (const auto& [id, nonce] : listed)
        {
            auto found = m_peers.find(id);
            if (found == m_peers.end())
            {
                AddPeer(id, nonce, aNow);
            }
            else if (found->second->nonce != nonce)
            {
                // Same id, different client session: the old reliability state is meaningless.
                RemovePeer(id, "replaced");
                AddPeer(id, nonce, aNow);
            }
        }

        std::vector<uint16_t> departed;
        for (const auto& [id, peer] : m_peers)
        {
            if (!listed.contains(id))
            {
                departed.push_back(id);
            }
        }
        for (const uint16_t id : departed)
        {
            RemovePeer(id, "left");
        }
    }

    void HandlePeerFrame(const FrameHeader& aHeader, std::span<const uint8_t> aPayload, uint64_t aNow)
    {
        if (!m_welcomed || aHeader.sender == m_localId || aHeader.sender == kBroadcastId)
        {
            return;
        }
        const bool addressedToUs = aHeader.target == m_localId;
        if (!addressedToUs && aHeader.target != kBroadcastId)
        {
            ++m_badFrames;
            return;
        }

        Peer* peer = FindPeer(aHeader.sender);
        if (peer == nullptr)
        {
            // The relay's PEERS list is the only source of membership. A frame from an unknown
            // sender is either early (its reliable content is resent once PEERS arrives) or late
            // from a peer that already left; adopting it would resurrect departed peers.
            ++m_unknownPeerFrames;
            return;
        }
        peer->lastHeardMicros = aNow;
        if (addressedToUs)
        {
            peer->reliable.OnAck(aHeader.ack, aHeader.ackBits, aNow);
        }

        switch (DeliveryOf(aHeader.channel))
        {
        case Delivery::Control:
            HandlePeerControl(*peer, aPayload, aNow);
            break;
        case Delivery::Unreliable:
            HandleUnreliable(*peer, aHeader, aPayload);
            break;
        case Delivery::Reliable:
            if (!addressedToUs)
            {
                ++m_badFrames;
                return;
            }
            peer->reliable.OnReceive(aHeader.sequence, aHeader.channel, aPayload);
            DeliverReliable(*peer);
            break;
        case Delivery::Invalid:
            ++m_badFrames;
            break;
        }
    }

    void HandleUnreliable(Peer& aPeer, const FrameHeader& aHeader, std::span<const uint8_t> aPayload)
    {
        const uint8_t channel = aHeader.channel;
        if (aPeer.haveReceived[channel] && !SequenceGreater(aHeader.sequence, aPeer.lastReceived[channel]))
        {
            ++aPeer.unreliableStale; // older than (or equal to) a snapshot we already delivered
            return;
        }
        aPeer.haveReceived[channel] = true;
        aPeer.lastReceived[channel] = aHeader.sequence;
        const std::string_view text(reinterpret_cast<const char*>(aPayload.data()), aPayload.size());
        if (m_shared.PushInbox(ComposeMessage(aPeer.id, channel, text), false))
        {
            ++aPeer.unreliableReceived;
        }
        else
        {
            ++aPeer.unreliableDropped;
            ++m_droppedInbound;
        }
    }

    void HandlePeerControl(Peer& aPeer, std::span<const uint8_t> aPayload, uint64_t aNow)
    {
        ByteReader reader(aPayload);
        uint8_t op = 0;
        if (!reader.U8(op))
        {
            ++m_badFrames;
            return;
        }
        switch (static_cast<ControlOp>(op))
        {
        case ControlOp::Ping:
        {
            uint32_t pingId = 0;
            uint64_t sentMicros = 0;
            if (!reader.U32(pingId) || !reader.U64(sentMicros))
            {
                ++m_badFrames;
                return;
            }
            ByteWriter pong;
            pong.U8(static_cast<uint8_t>(ControlOp::Pong));
            pong.U32(pingId);
            pong.U64(sentMicros);
            SendToPeer(aPeer, kControlChannel, 0, pong.Data());
            break;
        }
        case ControlOp::Pong:
        {
            uint32_t pingId = 0;
            uint64_t sentMicros = 0;
            if (reader.U32(pingId) && reader.U64(sentMicros) && sentMicros <= aNow)
            {
                aPeer.reliable.Rtt().AddSample(aNow - sentMicros);
                ++aPeer.pongsReceived;
            }
            break;
        }
        case ControlOp::Ack:
            break; // header already processed
        case ControlOp::Bye:
            RemovePeer(aPeer.id, "bye");
            break;
        default:
            ++m_badFrames;
            break;
        }
    }

    void DeliverReliable(Peer& aPeer)
    {
        const uint16_t sender = aPeer.id;
        aPeer.reliable.Deliver(
            [this, sender](uint16_t, uint8_t aChannel, std::string_view aPayload)
            { return m_shared.PushInbox(ComposeMessage(sender, aChannel, aPayload), false); });
    }

    // ---- send -----------------------------------------------------------------------------

    void DrainOutbox()
    {
        std::deque<OutgoingRequest> requests;
        {
            std::lock_guard lock(m_shared.outboxMutex);
            requests.swap(m_shared.outbox);
        }
        for (auto& request : requests)
        {
            if (!m_welcomed || m_peers.empty())
            {
                ++m_droppedNoPeer;
                continue;
            }
            if (request.target != kBroadcastId)
            {
                Peer* peer = FindPeer(request.target);
                if (peer == nullptr)
                {
                    ++m_droppedNoPeer;
                    continue;
                }
                SendRequest(*peer, request);
                continue;
            }
            for (auto& [id, peer] : m_peers)
            {
                SendRequest(*peer, request);
            }
        }
    }

    void SendRequest(Peer& aPeer, const OutgoingRequest& aRequest)
    {
        if (DeliveryOf(aRequest.channel) == Delivery::Reliable)
        {
            if (!aPeer.reliable.Enqueue(aRequest.channel, aRequest.payload))
            {
                ++m_droppedBacklog;
            }
            return;
        }
        const uint16_t sequence = aPeer.sendSequence[aRequest.channel]++;
        const auto* bytes = reinterpret_cast<const uint8_t*>(aRequest.payload.data());
        SendToPeer(aPeer, aRequest.channel, sequence, std::span<const uint8_t>(bytes, aRequest.payload.size()));
        ++aPeer.unreliableSent;
    }

    void FlushReliable(uint64_t aNow)
    {
        for (auto& [id, peerPointer] : m_peers)
        {
            Peer& peer = *peerPointer;
            DeliverReliable(peer); // retry messages that waited for inbox space
            peer.reliable.CollectDue(aNow,
                                     [this, &peer](uint16_t aSequence, uint8_t aChannel, std::string_view aPayload, bool)
                                     {
                                         const auto* bytes = reinterpret_cast<const uint8_t*>(aPayload.data());
                                         SendToPeer(peer, aChannel, aSequence,
                                                    std::span<const uint8_t>(bytes, aPayload.size()));
                                     });
            if (peer.reliable.Failed() && !peer.reportedUnresponsive)
            {
                peer.reportedUnresponsive = true;
                m_shared.Log(LogLevel::Warn, "peer " + std::to_string(peer.id) + " stopped acknowledging");
                m_shared.PushEvent("peer_unresponsive " + std::to_string(peer.id));
            }
        }
    }

    void FlushAcks()
    {
        for (auto& [id, peer] : m_peers)
        {
            if (peer->reliable.AckPending())
            {
                ByteWriter ack;
                ack.U8(static_cast<uint8_t>(ControlOp::Ack));
                SendToPeer(*peer, kControlChannel, 0, ack.Data());
            }
        }
    }

    void RunTimers(uint64_t aNow)
    {
        if (!m_welcomed)
        {
            if (aNow >= m_nextHelloMicros)
            {
                SendHello();
                m_nextHelloMicros = aNow + m_shared.config.helloIntervalMicros;
            }
            return;
        }

        if (aNow - m_lastRelayHeardMicros > m_shared.config.relayTimeoutMicros)
        {
            HandleRelayLost(aNow);
            return;
        }

        if (aNow >= m_nextRelayPingMicros)
        {
            ByteWriter ping;
            ping.U8(static_cast<uint8_t>(ControlOp::Ping));
            ping.U32(++m_pingCounter);
            ping.U64(aNow);
            SendToRelay(ping);
            m_nextRelayPingMicros = aNow + m_shared.config.pingIntervalMicros;
        }

        for (auto& [id, peer] : m_peers)
        {
            if (aNow < peer->nextPingMicros)
            {
                continue;
            }
            ByteWriter ping;
            ping.U8(static_cast<uint8_t>(ControlOp::Ping));
            ping.U32(++m_pingCounter);
            ping.U64(aNow);
            SendToPeer(*peer, kControlChannel, 0, ping.Data());
            ++peer->pingsSent;
            peer->nextPingMicros = aNow + m_shared.config.peerPingIntervalMicros;
        }
    }

    void HandleRelayLost(uint64_t aNow)
    {
        m_shared.Log(LogLevel::Warn, "relay silent, reconnecting");
        m_shared.PushEvent("relay_lost");
        // Peers will see us come back under a new session nonce, so both sides restart their
        // reliability state together.
        ResetPeers("relay_lost");
        m_welcomed = false;
        m_localId = 0;
        m_shared.localId = 0;
        m_nonce = NextNonce();
        m_nextHelloMicros = aNow;
        SetState(ConnectionState::Reconnecting);
    }

    void SendHello()
    {
        ByteWriter hello;
        hello.U8(static_cast<uint8_t>(ControlOp::Hello));
        hello.U32(m_nonce);
        hello.U8(static_cast<uint8_t>(m_room.size()));
        hello.Bytes(m_room);
        SendToRelay(hello);
    }

    void SendToRelay(const ByteWriter& aPayload)
    {
        FrameHeader header;
        header.channel = kControlChannel;
        header.sender = m_localId;
        header.target = kRelayId;
        SendFrame(header, aPayload.Data());
    }

    void SendToPeer(Peer& aPeer, uint8_t aChannel, uint16_t aSequence, std::span<const uint8_t> aPayload)
    {
        FrameHeader header;
        header.channel = aChannel;
        header.sender = m_localId;
        header.target = aPeer.id;
        header.sequence = aSequence;
        header.ack = aPeer.reliable.AckValue();
        header.ackBits = aPeer.reliable.AckBits();
        aPeer.reliable.ClearAckPending();
        SendFrame(header, aPayload);
    }

    void SendFrame(const FrameHeader& aHeader, std::span<const uint8_t> aPayload)
    {
        std::array<uint8_t, kMaxDatagramSize> frame{};
        const size_t size = EncodeFrame(aHeader, aPayload, frame);
        if (size == 0)
        {
            ++m_sendErrors;
            return;
        }
        const int sent = sendto(m_socket, reinterpret_cast<const char*>(frame.data()), static_cast<int>(size), 0,
                                reinterpret_cast<const sockaddr*>(&m_relayAddress), sizeof(m_relayAddress));
        if (sent == SOCKET_ERROR)
        {
            ++m_sendErrors;
            return;
        }
        ++m_packetsSent;
        m_bytesSent += size;
    }

    // ---- peers ----------------------------------------------------------------------------

    Peer* FindPeer(uint16_t aId)
    {
        const auto found = m_peers.find(aId);
        return found == m_peers.end() ? nullptr : found->second.get();
    }

    Peer& AddPeer(uint16_t aId, uint32_t aNonce, uint64_t aNow)
    {
        auto peer = std::make_unique<Peer>();
        peer->id = aId;
        peer->nonce = aNonce;
        peer->createdMicros = aNow;
        peer->lastHeardMicros = aNow;
        peer->nextPingMicros = aNow;
        Peer& reference = *peer;
        m_peers[aId] = std::move(peer);
        m_shared.peerCount = static_cast<int>(m_peers.size());
        m_statsDirty = true;
        m_shared.Log(LogLevel::Info, "peer " + std::to_string(aId) + " joined");
        m_shared.PushEvent("peer_join " + std::to_string(aId));
        return reference;
    }

    void RemovePeer(uint16_t aId, std::string_view aReason)
    {
        if (m_peers.erase(aId) == 0)
        {
            return;
        }
        m_shared.peerCount = static_cast<int>(m_peers.size());
        m_statsDirty = true;
        m_shared.Log(LogLevel::Info, "peer " + std::to_string(aId) + " left (" + std::string(aReason) + ")");
        m_shared.PushEvent("peer_leave " + std::to_string(aId) + " " + std::string(aReason));
    }

    void ResetPeers(std::string_view aReason)
    {
        std::vector<uint16_t> ids;
        for (const auto& [id, peer] : m_peers)
        {
            ids.push_back(id);
        }
        for (const uint16_t id : ids)
        {
            RemovePeer(id, aReason);
        }
    }

    // ---- state / stats --------------------------------------------------------------------

    uint32_t NextNonce()
    {
        uint32_t nonce = 0;
        while (nonce == 0)
        {
            nonce = static_cast<uint32_t>(m_random());
        }
        return nonce;
    }

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

    void PublishStats(uint64_t aNow)
    {
        StatsSnapshot snapshot;
        snapshot.host = m_host;
        snapshot.port = m_port;
        snapshot.room = m_room;
        snapshot.relayRttValid = m_relayRtt.HasSample();
        snapshot.relayRttMs = m_relayRtt.SmoothedMs();
        snapshot.uptimeSeconds = static_cast<double>(aNow - m_startedMicros) / 1e6;
        snapshot.packetsSent = m_packetsSent;
        snapshot.packetsReceived = m_packetsReceived;
        snapshot.bytesSent = m_bytesSent;
        snapshot.bytesReceived = m_bytesReceived;
        snapshot.badFrames = m_badFrames;
        snapshot.foreignDatagrams = m_foreignDatagrams;
        snapshot.unknownPeerFrames = m_unknownPeerFrames;
        snapshot.sendErrors = m_sendErrors;
        snapshot.droppedInbound = m_droppedInbound;
        snapshot.droppedNoPeer = m_droppedNoPeer;
        snapshot.droppedBacklog = m_droppedBacklog;
        snapshot.lastError = m_lastError;
        for (const auto& [id, peerPointer] : m_peers)
        {
            const Peer& peer = *peerPointer;
            PeerStatsSnapshot entry;
            entry.id = peer.id;
            entry.nonce = peer.nonce;
            entry.unresponsive = peer.reliable.Failed();
            entry.rttValid = peer.reliable.Rtt().HasSample();
            entry.rttMs = peer.reliable.Rtt().SmoothedMs();
            entry.rttVariationMs = peer.reliable.Rtt().VariationMs();
            entry.rtoMs = static_cast<double>(peer.reliable.Rtt().RtoMicros()) / 1000.0;
            entry.lastHeardMsAgo = static_cast<double>(aNow - peer.lastHeardMicros) / 1000.0;
            entry.inFlight = peer.reliable.InFlight();
            entry.backlog = peer.reliable.Backlog();
            entry.buffered = peer.reliable.Buffered();
            entry.reliable = peer.reliable.Stats();
            entry.unreliableSent = peer.unreliableSent;
            entry.unreliableReceived = peer.unreliableReceived;
            entry.unreliableStale = peer.unreliableStale;
            entry.unreliableDropped = peer.unreliableDropped;
            entry.pingsSent = peer.pingsSent;
            entry.pongsReceived = peer.pongsReceived;
            snapshot.peers.push_back(entry);
        }
        std::lock_guard lock(m_shared.statsMutex);
        m_shared.stats = std::move(snapshot);
    }

    SharedState& m_shared;
    const std::string m_host;
    const int m_port;
    const std::string m_room;
    uint32_t m_nonce;
    std::mt19937 m_random;

    bool m_wsaStarted = false;
    SOCKET m_socket = INVALID_SOCKET;
    WSAEVENT m_socketEvent = WSA_INVALID_EVENT;
    sockaddr_in m_relayAddress{};

    std::map<uint16_t, std::unique_ptr<Peer>> m_peers;
    uint16_t m_localId = 0;
    bool m_welcomed = false;
    bool m_statsDirty = true;

    uint64_t m_startedMicros = 0;
    uint64_t m_nextHelloMicros = 0;
    uint64_t m_nextRelayPingMicros = 0;
    uint64_t m_lastRelayHeardMicros = 0;
    uint64_t m_nextStatsMicros = 0;
    uint32_t m_pingCounter = 0;
    RttEstimator m_relayRtt;

    uint64_t m_packetsSent = 0;
    uint64_t m_packetsReceived = 0;
    uint64_t m_bytesSent = 0;
    uint64_t m_bytesReceived = 0;
    uint64_t m_badFrames = 0;
    uint64_t m_foreignDatagrams = 0;
    uint64_t m_unknownPeerFrames = 0;
    uint64_t m_sendErrors = 0;
    uint64_t m_droppedInbound = 0;
    uint64_t m_droppedNoPeer = 0;
    uint64_t m_droppedBacklog = 0;
    std::string m_lastError;
};

void RunSession(SharedState& aShared, std::string aHost, int aPort, std::string aRoom, uint32_t aNonce)
{
    try
    {
        Session session(aShared, std::move(aHost), aPort, std::move(aRoom), aNonce);
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

uint32_t RandomNonce()
{
    std::random_device device;
    uint32_t nonce = 0;
    while (nonce == 0)
    {
        nonce = device();
    }
    return nonce;
}
} // namespace

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
    std::lock_guard lifecycle(m_lifecycleMutex);
    if (aHost.empty() || aPort <= 0 || aPort > 65535 || aRoom.size() > kMaxRoomLength)
    {
        m_shared->Log(LogLevel::Error, "Net_Connect: invalid host, port or room");
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
    }
    m_shared->stopRequested = false;
    m_shared->state = static_cast<int>(ConnectionState::Resolving);
    m_shared->localId = 0;
    m_shared->peerCount = 0;
    ResetEvent(m_shared->wakeEvent);

    try
    {
        m_thread = std::thread(RunSession, std::ref(*m_shared), std::string(aHost), aPort,
                               std::string(aRoom.empty() ? "default" : aRoom), RandomNonce());
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
    const Delivery delivery = DeliveryOf(aChannel);
    const bool validTarget = aTarget == kBroadcastId || (aTarget > kRelayId && aTarget < kBroadcastId);
    if ((delivery != Delivery::Reliable && delivery != Delivery::Unreliable) || aPayload.size() > kMaxPayloadSize ||
        !validTarget)
    {
        ++m_shared->refusedSends;
        return false;
    }
    if (static_cast<ConnectionState>(m_shared->state.load()) != ConnectionState::Connected ||
        m_shared->peerCount.load() == 0)
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
            {static_cast<uint8_t>(aChannel), static_cast<uint16_t>(aTarget), std::string(aPayload)});
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

std::string Transport::StatsJson() const
{
    StatsSnapshot stats;
    {
        std::lock_guard lock(m_shared->statsMutex);
        stats = m_shared->stats;
    }
    size_t inboxSize = 0;
    size_t outboxSize = 0;
    {
        std::lock_guard lock(m_shared->inboxMutex);
        inboxSize = m_shared->inbox.size();
    }
    {
        std::lock_guard lock(m_shared->outboxMutex);
        outboxSize = m_shared->outbox.size();
    }

    std::ostringstream json;
    json << "{\"state\":\"" << ToString(State()) << "\"";
    json << ",\"id\":" << LocalId();
    json << ",\"version\":\"" << kVersionString << "\"";
    json << ",\"relay\":\"" << JsonEscape(stats.host) << ":" << stats.port << "\"";
    json << ",\"room\":\"" << JsonEscape(stats.room) << "\"";
    json << ",\"relayRttMs\":" << (stats.relayRttValid ? FormatDouble(stats.relayRttMs) : "null");
    json << ",\"uptimeS\":" << FormatDouble(stats.uptimeSeconds);
    json << ",\"txPackets\":" << stats.packetsSent << ",\"rxPackets\":" << stats.packetsReceived;
    json << ",\"txBytes\":" << stats.bytesSent << ",\"rxBytes\":" << stats.bytesReceived;
    json << ",\"inQueue\":" << inboxSize << ",\"outQueue\":" << outboxSize;
    json << ",\"badFrames\":" << stats.badFrames << ",\"foreign\":" << stats.foreignDatagrams;
    json << ",\"unknownPeerFrames\":" << stats.unknownPeerFrames;
    json << ",\"sendErrors\":" << stats.sendErrors << ",\"droppedIn\":" << stats.droppedInbound;
    json << ",\"droppedNoPeer\":" << stats.droppedNoPeer << ",\"droppedBacklog\":" << stats.droppedBacklog;
    json << ",\"refusedSends\":" << m_shared->refusedSends.load();
    json << ",\"lastError\":\"" << JsonEscape(stats.lastError) << "\"";
    json << ",\"peers\":[";
    for (size_t index = 0; index < stats.peers.size(); ++index)
    {
        const PeerStatsSnapshot& peer = stats.peers[index];
        if (index > 0)
        {
            json << ",";
        }
        json << "{\"id\":" << peer.id << ",\"nonce\":" << peer.nonce;
        json << ",\"unresponsive\":" << (peer.unresponsive ? "true" : "false");
        json << ",\"rttMs\":" << (peer.rttValid ? FormatDouble(peer.rttMs) : "null");
        json << ",\"rttVarMs\":" << FormatDouble(peer.rttVariationMs);
        json << ",\"rtoMs\":" << FormatDouble(peer.rtoMs);
        json << ",\"lastHeardMs\":" << FormatDouble(peer.lastHeardMsAgo);
        json << ",\"inFlight\":" << peer.inFlight << ",\"backlog\":" << peer.backlog << ",\"buffered\":" << peer.buffered;
        json << ",\"relSent\":" << peer.reliable.sent << ",\"relResent\":" << peer.reliable.resent;
        json << ",\"relFastResent\":" << peer.reliable.fastResent << ",\"relAcked\":" << peer.reliable.acked;
        json << ",\"relRecv\":" << peer.reliable.received << ",\"relDup\":" << peer.reliable.duplicates;
        json << ",\"relOutOfWindow\":" << peer.reliable.outOfWindow << ",\"relDelivered\":" << peer.reliable.delivered;
        json << ",\"unrelSent\":" << peer.unreliableSent << ",\"unrelRecv\":" << peer.unreliableReceived;
        json << ",\"unrelStale\":" << peer.unreliableStale << ",\"unrelDropped\":" << peer.unreliableDropped;
        json << ",\"pings\":" << peer.pingsSent << ",\"pongs\":" << peer.pongsReceived << "}";
    }
    json << "]}";
    return json.str();
}
} // namespace coopnet
