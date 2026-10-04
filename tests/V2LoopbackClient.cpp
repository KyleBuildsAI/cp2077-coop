// A protocol v2 client over real UDP for tools/run_v2_loopback.py: the C++ Connection and
// ClockSync against relay_v2.py, with a link simulator on both directions of this client.
//
//   coopnet_v2_loopback_client --relay-port 11778 --role host --room loop --password pw --name host
//       --duration 20 --messages 300 --rate 20 --latency-ms 40 --jitter-ms 20 --loss-pct 1
//       --dup-pct 0 --seed 1 --report host.json
//
// After the cookie handshake it keeps TIME_REQ going (ClockSync's schedule), sends 30 Hz
// unreliable SCRIPT_MSG snapshots (channel 1) and, once the other peer has joined and the clock is
// synced, --messages reliable SCRIPT_MSG events (channel 16) at --rate per second, all broadcast
// through the relay. It checks that the other peer's events arrive exactly once and in order, and
// writes a JSON report: the clock estimate history and the QPC epoch of the local clock (Python's
// time.perf_counter() reads the same counter, so the runner can compare with the relay's start),
// delivery results and link statistics. Not part of the plugin.

#include "v2/ClockSync.hpp"
#include "v2/V2Codec.hpp"
#include "v2/V2Hash.hpp"
#include "v2/V2Reliability.hpp"

#include "V2TestSupport.hpp"

#include <winsock2.h>
#include <ws2tcpip.h>
#include <mswsock.h>
#include <windows.h>

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <map>
#include <optional>
#include <queue>
#include <set>
#include <sstream>
#include <string>
#include <vector>

using namespace coopnet::v2;

namespace
{
// ---- arguments -----------------------------------------------------------------------------------

struct Options
{
    std::string relayHost = "127.0.0.1";
    uint16_t relayPort = 11778;
    std::string role = "host";
    std::string room = "loopback";
    std::string password = "pw";
    std::string name = "client";
    double durationS = 20.0;
    uint32_t messages = 300;
    double rate = 20.0;
    double latencyMs = 0.0;
    double jitterMs = 0.0;
    double lossPct = 0.0;
    double dupPct = 0.0;
    uint64_t seed = 1;
    std::string report = "client.json";
};

bool ParseOptions(int argc, char** argv, Options& aOut)
{
    std::map<std::string, std::string> values;
    for (int index = 1; index + 1 < argc; index += 2)
    {
        values[argv[index]] = argv[index + 1];
    }
    if ((argc - 1) % 2 != 0)
    {
        return false;
    }
    try
    {
        for (const auto& [key, value] : values)
        {
            if (key == "--relay-host")
                aOut.relayHost = value;
            else if (key == "--relay-port")
                aOut.relayPort = static_cast<uint16_t>(std::stoul(value));
            else if (key == "--role")
                aOut.role = value;
            else if (key == "--room")
                aOut.room = value;
            else if (key == "--password")
                aOut.password = value;
            else if (key == "--name")
                aOut.name = value;
            else if (key == "--duration")
                aOut.durationS = std::stod(value);
            else if (key == "--messages")
                aOut.messages = static_cast<uint32_t>(std::stoul(value));
            else if (key == "--rate")
                aOut.rate = std::stod(value);
            else if (key == "--latency-ms")
                aOut.latencyMs = std::stod(value);
            else if (key == "--jitter-ms")
                aOut.jitterMs = std::stod(value);
            else if (key == "--loss-pct")
                aOut.lossPct = std::stod(value);
            else if (key == "--dup-pct")
                aOut.dupPct = std::stod(value);
            else if (key == "--seed")
                aOut.seed = std::stoull(value);
            else if (key == "--report")
                aOut.report = value;
            else
                return false;
        }
    }
    catch (const std::exception&)
    {
        return false;
    }
    return aOut.role == "host" || aOut.role == "joiner";
}

// ---- time ----------------------------------------------------------------------------------------

class LocalClock
{
public:
    LocalClock()
    {
        LARGE_INTEGER frequency;
        LARGE_INTEGER now;
        QueryPerformanceFrequency(&frequency);
        QueryPerformanceCounter(&now);
        m_frequency = static_cast<double>(frequency.QuadPart);
        m_epoch = now.QuadPart;
    }

    // Milliseconds since the epoch (monotonic, QueryPerformanceCounter).
    [[nodiscard]] double NowMs() const
    {
        LARGE_INTEGER now;
        QueryPerformanceCounter(&now);
        return static_cast<double>(now.QuadPart - m_epoch) * 1000.0 / m_frequency;
    }

    // The epoch in seconds on time.perf_counter()'s scale.
    [[nodiscard]] double EpochSeconds() const
    {
        return static_cast<double>(m_epoch) / m_frequency;
    }

private:
    double m_frequency = 1.0;
    int64_t m_epoch = 0;
};

class Sleeper
{
public:
    Sleeper()
        : m_timer(CreateWaitableTimerExW(nullptr, nullptr, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_ALL_ACCESS))
    {
    }
    ~Sleeper()
    {
        if (m_timer != nullptr)
        {
            CloseHandle(m_timer);
        }
    }
    Sleeper(const Sleeper&) = delete;
    Sleeper& operator=(const Sleeper&) = delete;

    void Sleep(int64_t aMicroseconds) const
    {
        if (m_timer == nullptr)
        {
            ::Sleep(1);
            return;
        }
        LARGE_INTEGER due;
        due.QuadPart = -aMicroseconds * 10; // relative, 100 ns units
        SetWaitableTimer(m_timer, &due, 0, nullptr, nullptr, FALSE);
        WaitForSingleObject(m_timer, 50);
    }

private:
    HANDLE m_timer;
};

// ---- link simulator (one direction) ----------------------------------------------------------------

class LinkSim
{
public:
    LinkSim(uint64_t aSeed, double aLatencyMs, double aJitterMs, double aLossPct, double aDupPct)
        : m_rng(aSeed)
        , m_latencyMs(aLatencyMs)
        , m_jitterMs(aJitterMs)
        , m_lossPct(aLossPct)
        , m_dupPct(aDupPct)
    {
    }

    void Submit(double aNowMs, Bytes aData)
    {
        ++submitted;
        if (m_rng.Unit() * 100.0 < m_lossPct)
        {
            ++dropped;
            return;
        }
        int copies = 1;
        if (m_rng.Unit() * 100.0 < m_dupPct)
        {
            copies = 2;
            ++duplicated;
        }
        for (int copy = 0; copy < copies; ++copy)
        {
            m_queue.push(Entry{aNowMs + m_latencyMs + m_rng.Unit() * m_jitterMs, ++m_counter, aData});
        }
    }

    std::vector<Bytes> PopDue(double aNowMs)
    {
        std::vector<Bytes> due;
        while (!m_queue.empty() && m_queue.top().due <= aNowMs)
        {
            due.push_back(m_queue.top().data);
            m_queue.pop();
        }
        return due;
    }

    uint64_t submitted = 0;
    uint64_t dropped = 0;
    uint64_t duplicated = 0;

private:
    struct Entry
    {
        double due;
        uint64_t counter;
        Bytes data;
        bool operator>(const Entry& aOther) const
        {
            return due != aOther.due ? due > aOther.due : counter > aOther.counter;
        }
    };

    test::Rng m_rng;
    double m_latencyMs;
    double m_jitterMs;
    double m_lossPct;
    double m_dupPct;
    uint64_t m_counter = 0;
    std::priority_queue<Entry, std::vector<Entry>, std::greater<>> m_queue;
};

ByteSpan AsSpan(const Bytes& aBytes)
{
    return {aBytes.data(), aBytes.size()};
}

std::string Json(const std::string& aText)
{
    std::string out = "\"";
    for (const char character : aText)
    {
        if (character == '"' || character == '\\')
        {
            out += '\\';
        }
        out += character;
    }
    return out + "\"";
}

std::string Number(std::optional<double> aValue)
{
    if (!aValue)
    {
        return "null";
    }
    char buffer[64];
    std::snprintf(buffer, sizeof(buffer), "%.6f", *aValue);
    return buffer;
}

// ---- the client ----------------------------------------------------------------------------------

class Client
{
public:
    explicit Client(const Options& aOptions)
        : m_options(aOptions)
        , m_uplink(aOptions.seed * 2 + 1, aOptions.latencyMs, aOptions.jitterMs, aOptions.lossPct, aOptions.dupPct)
        , m_downlink(aOptions.seed * 2 + 2, aOptions.latencyMs, aOptions.jitterMs, aOptions.lossPct, aOptions.dupPct)
    {
    }

    ~Client()
    {
        if (m_socket != INVALID_SOCKET)
        {
            closesocket(m_socket);
        }
    }

    Client(const Client&) = delete;
    Client& operator=(const Client&) = delete;

    bool Open()
    {
        m_socket = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        if (m_socket == INVALID_SOCKET)
        {
            m_error = "socket failed";
            return false;
        }
        u_long nonBlocking = 1;
        ioctlsocket(m_socket, FIONBIO, &nonBlocking);
        BOOL reportReset = FALSE;
        DWORD returned = 0;
        WSAIoctl(m_socket, SIO_UDP_CONNRESET, &reportReset, sizeof(reportReset), nullptr, 0, &returned, nullptr, nullptr);
        sockaddr_in local{};
        local.sin_family = AF_INET;
        local.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        if (bind(m_socket, reinterpret_cast<const sockaddr*>(&local), sizeof(local)) != 0)
        {
            m_error = "bind failed";
            return false;
        }
        m_relay.sin_family = AF_INET;
        m_relay.sin_port = htons(m_options.relayPort);
        if (inet_pton(AF_INET, m_options.relayHost.c_str(), &m_relay.sin_addr) != 1)
        {
            m_error = "bad relay host";
            return false;
        }
        return true;
    }

    void Run()
    {
        const double endMs = m_options.durationS * 1000.0;
        if (Handshake(std::min(endMs, 5000.0)))
        {
            Session(endMs);
        }
        WriteReport();
    }

    [[nodiscard]] bool Welcomed() const
    {
        return m_connection.has_value();
    }

private:
    // ---- I/O through the simulated link ---------------------------------------------------------

    void Send(Bytes aData)
    {
        m_uplink.Submit(m_clock.NowMs(), std::move(aData));
    }

    void PumpSocket()
    {
        uint8_t buffer[2048];
        for (int index = 0; index < 256; ++index)
        {
            sockaddr_in from{};
            int fromLength = sizeof(from);
            const int received = recvfrom(m_socket, reinterpret_cast<char*>(buffer), sizeof(buffer), 0,
                                          reinterpret_cast<sockaddr*>(&from), &fromLength);
            if (received <= 0)
            {
                break;
            }
            m_downlink.Submit(m_clock.NowMs(), Bytes(buffer, buffer + received));
        }
        for (const Bytes& data : m_uplink.PopDue(m_clock.NowMs()))
        {
            sendto(m_socket, reinterpret_cast<const char*>(data.data()), static_cast<int>(data.size()), 0,
                   reinterpret_cast<const sockaddr*>(&m_relay), sizeof(m_relay));
        }
    }

    // ---- handshake --------------------------------------------------------------------------------

    JoinInfo MakeJoin(uint64_t aNonce) const
    {
        JoinInfo join;
        join.fixed.minor = coopv2::kProtoMinor;
        join.fixed.role = static_cast<uint8_t>(m_options.role == "host" ? coopv2::Role::Host : coopv2::Role::Joiner);
        join.fixed.join_flags = 0;
        join.fixed.caps = 0x1FF;
        join.fixed.game_build = GameBuildId("2.31a");
        join.fixed.mod_major = 0;
        join.fixed.mod_minor = 2;
        join.fixed.mod_patch = 0;
        const ModEntry mods[] = {{"CP2077CoopNet", "0.2.0"}};
        join.fixed.mod_hash = ModListHash(mods);
        join.fixed.mod_count = 1;
        join.fixed.client_nonce = aNonce;
        join.fixed.resume_token = 0;
        join.room = m_options.room;
        join.name = m_options.name;
        return join;
    }

    bool Handshake(double aDeadlineMs)
    {
        test::Rng nonceRng(m_options.seed ^ static_cast<uint64_t>(GetCurrentProcessId()) << 20);
        const JoinInfo join = MakeJoin(nonceRng.U64());
        std::optional<AuthInfo> auth;
        double nextSend = 0.0;
        while (m_clock.NowMs() < aDeadlineMs)
        {
            const double now = m_clock.NowMs();
            if (now >= nextSend)
            {
                Bytes datagram;
                if (auth)
                {
                    EncodeAuth(*auth, datagram);
                }
                else
                {
                    EncodeHello(join, datagram);
                }
                Send(std::move(datagram));
                nextSend = now + 250.0;
            }
            PumpSocket();
            const std::vector<Bytes> arrived = m_downlink.PopDue(m_clock.NowMs());
            for (size_t index = 0; index < arrived.size(); ++index)
            {
                const Bytes& data = arrived[index];
                DecodedPacket packet;
                if (DecodePacket(AsSpan(data), packet) != Status::Ok)
                {
                    ++m_decodeErrors;
                    continue;
                }
                const auto type = static_cast<coopv2::PacketType>(packet.header.type);
                if (type == coopv2::PacketType::Challenge && !auth)
                {
                    coopv2::Challenge challenge{};
                    if (DecodeChallenge(packet.body, challenge) == Status::Ok)
                    {
                        auth = AuthInfo{};
                        auth->join = join;
                        std::copy(std::begin(challenge.cookie), std::end(challenge.cookie), auth->cookie.begin());
                        auth->keyHash = RoomKeyHash(m_options.room, m_options.password);
                        nextSend = 0.0;
                    }
                }
                else if (type == coopv2::PacketType::Welcome)
                {
                    coopv2::Welcome welcome{};
                    if (DecodeWelcome(packet.body, welcome) == Status::Ok)
                    {
                        m_welcome = welcome;
                        m_connection.emplace(welcome.token);
                        m_handshakeMs = m_clock.NowMs();
                        // Session DATA that arrived together with the WELCOME.
                        m_backlog.assign(arrived.begin() + static_cast<std::ptrdiff_t>(index) + 1, arrived.end());
                        return true;
                    }
                }
                else if (type == coopv2::PacketType::Reject)
                {
                    RejectInfo reject;
                    DecodeReject(packet.body, reject);
                    m_error = "rejected " + std::to_string(reject.reason) + " " + reject.text;
                    return false;
                }
            }
            m_sleeper.Sleep(500);
        }
        m_error = "handshake timeout";
        return false;
    }

    // ---- session ----------------------------------------------------------------------------------

    void Session(double aEndMs)
    {
        Connection& connection = *m_connection;
        double nextSnapshot = m_clock.NowMs();
        double nextEvent = 0.0;
        double nextRecord = 0.0;
        for (const Bytes& data : m_backlog)
        {
            OnDatagram(data, m_clock.NowMs());
        }
        m_backlog.clear();
        while (m_clock.NowMs() < aEndMs && !m_disconnected)
        {
            PumpSocket();
            double now = m_clock.NowMs();
            for (const Bytes& data : m_downlink.PopDue(now))
            {
                OnDatagram(data, now);
            }
            now = m_clock.NowMs();
            if (m_clockSync.RequestDue(now))
            {
                const coopv2::TimeReq request = m_clockSync.MakeRequest(now);
                QueueUnreliable(coopv2::kPeerRelay, Body{request});
            }
            if (now >= nextSnapshot)
            {
                nextSnapshot = std::max(nextSnapshot + 1000.0 / 30.0, now - 1000.0 / 30.0);
                ScriptMsg snapshot{1, 0, "snap " + std::to_string(m_snapshotsSent++)};
                QueueUnreliable(coopv2::kPeerBroadcast, Body{snapshot});
            }
            if (m_otherPeer && m_clockSync.Synced() && m_eventsSent < m_options.messages && now >= nextEvent)
            {
                if (!m_eventsStartMs)
                {
                    m_eventsStartMs = now;
                }
                Bytes body;
                EncodeBody(Body{ScriptMsg{16, 0, "evt " + std::to_string(m_eventsSent)}}, body);
                if (connection.QueueReliable(static_cast<uint8_t>(coopv2::MsgType::ScriptMsg), coopv2::kPeerBroadcast,
                                             AsSpan(body), now / 1000.0) == QueueResult::Queued)
                {
                    ++m_eventsSent;
                    nextEvent = std::max(nextEvent + 1000.0 / m_options.rate, now - 1000.0 / m_options.rate);
                }
            }
            const double sinceSend = connection.LastSend() ? now - *connection.LastSend() * 1000.0 : 1e9;
            const bool ackDue = connection.AckPending() && sinceSend >= 20.0;
            const bool keepalive = sinceSend >= 1000.0;
            if (!m_pending.empty() || connection.ReliableDue(now / 1000.0) || ackDue || keepalive)
            {
                std::vector<LinkMessage> messages;
                for (const auto& [peer, body] : m_pending)
                {
                    messages.push_back(LinkMessage{body.first, peer, AsSpan(body.second)});
                }
                std::vector<Bytes> packets;
                connection.BuildPackets(now / 1000.0, messages, ackDue || keepalive, packets);
                m_pending.clear();
                for (Bytes& packet : packets)
                {
                    Send(std::move(packet));
                }
            }
            if (now >= nextRecord && m_clockSync.OffsetMs())
            {
                nextRecord = now + 250.0;
                m_history.push_back({now, *m_clockSync.OffsetMs(), m_clockSync.ErrorBoundMs().value_or(0.0),
                                     static_cast<double>(m_clockSync.Samples())});
            }
            m_sleeper.Sleep(300);
        }
        Bytes bye;
        EncodeDisconnect(m_welcome.token, static_cast<uint8_t>(coopv2::DisconnectReason::Quit), bye);
        sendto(m_socket, reinterpret_cast<const char*>(bye.data()), static_cast<int>(bye.size()), 0,
               reinterpret_cast<const sockaddr*>(&m_relay), sizeof(m_relay));
    }

    void QueueUnreliable(uint8_t aPeer, const Body& aBody)
    {
        Bytes bytes;
        if (EncodeBody(aBody, bytes) == Status::Ok)
        {
            m_pending.emplace_back(aPeer, std::make_pair(TypeOf(aBody), std::move(bytes)));
        }
    }

    void OnDatagram(const Bytes& aData, double aNowMs)
    {
        DecodedPacket packet;
        if (DecodePacket(AsSpan(aData), packet) != Status::Ok)
        {
            ++m_decodeErrors;
            return;
        }
        const auto type = static_cast<coopv2::PacketType>(packet.header.type);
        if (type == coopv2::PacketType::Disconnect && packet.header.token == m_welcome.token)
        {
            uint8_t reason = 0;
            DecodeDisconnect(packet.body, reason);
            m_disconnected = reason;
            return;
        }
        if (type != coopv2::PacketType::Data || packet.header.token != m_welcome.token)
        {
            return;
        }
        std::vector<DeliveredMessage> delivered;
        if (m_connection->OnPacket(aNowMs / 1000.0, packet.header, packet.body, delivered, aData.size()) != Status::Ok)
        {
            ++m_framingErrors;
            return;
        }
        for (const DeliveredMessage& message : delivered)
        {
            Body body;
            if (DecodeBody(message.type, AsSpan(message.body), body) != Status::Ok)
            {
                ++m_decodeErrors;
                continue;
            }
            OnMessage(message, body, aNowMs);
        }
    }

    void OnMessage(const DeliveredMessage& aMessage, const Body& aBody, double aNowMs)
    {
        if (const auto* response = std::get_if<coopv2::TimeResp>(&aBody))
        {
            const ClockResult result = m_clockSync.OnResponse(*response, aNowMs);
            ++m_clockResults[ToString(result)];
            if (m_clockSync.Synced() && !m_syncedAtMs)
            {
                m_syncedAtMs = aNowMs;
            }
        }
        else if (const auto* joined = std::get_if<PeerJoinedMsg>(&aBody))
        {
            if (joined->fixed.peer_id != m_welcome.peer_id)
            {
                m_otherPeer = joined->fixed.peer_id;
            }
        }
        else if (const auto* script = std::get_if<ScriptMsg>(&aBody))
        {
            OnScript(aMessage, *script);
        }
    }

    void OnScript(const DeliveredMessage& aMessage, const ScriptMsg& aScript)
    {
        if (aMessage.reliable && aScript.channel == 16 && aScript.text.rfind("evt ", 0) == 0)
        {
            const uint32_t index = static_cast<uint32_t>(std::stoul(aScript.text.substr(4)));
            if (index != m_eventsReceived && !m_firstMismatch)
            {
                m_firstMismatch = m_eventsReceived;
            }
            ++m_eventsReceived;
        }
        else if (!aMessage.reliable && aScript.channel == 1 && aScript.text.rfind("snap ", 0) == 0)
        {
            const uint32_t index = static_cast<uint32_t>(std::stoul(aScript.text.substr(5)));
            if (!m_snapshotsSeen.insert(index).second)
            {
                ++m_snapshotDuplicates;
            }
            else if (m_snapshotsSeen.size() > 1 && index < *m_snapshotsSeen.rbegin())
            {
                ++m_snapshotsReordered;
            }
        }
    }

    // ---- report -----------------------------------------------------------------------------------

    void WriteReport() const
    {
        std::ostringstream out;
        out << "{\n";
        out << "  \"role\": " << Json(m_options.role) << ",\n";
        out << "  \"error\": " << (m_error.empty() ? "null" : Json(m_error)) << ",\n";
        out << "  \"epoch_perf_s\": " << Number(m_clock.EpochSeconds()) << ",\n";
        out << "  \"welcomed\": " << (m_connection ? "true" : "false") << ",\n";
        out << "  \"peer_id\": " << (m_connection ? std::to_string(m_welcome.peer_id) : "null") << ",\n";
        out << "  \"minor\": " << (m_connection ? std::to_string(m_welcome.minor) : "null") << ",\n";
        out << "  \"other_peer\": " << (m_otherPeer ? std::to_string(*m_otherPeer) : "null") << ",\n";
        out << "  \"handshake_ms\": " << Number(m_handshakeMs) << ",\n";
        out << "  \"disconnected\": " << (m_disconnected ? std::to_string(*m_disconnected) : "null") << ",\n";
        out << "  \"args\": {\"latency_ms\": " << m_options.latencyMs << ", \"jitter_ms\": " << m_options.jitterMs
            << ", \"loss_pct\": " << m_options.lossPct << ", \"dup_pct\": " << m_options.dupPct
            << ", \"duration_s\": " << m_options.durationS << ", \"messages\": " << m_options.messages
            << ", \"rate\": " << m_options.rate << "},\n";
        out << "  \"clock\": {\"offset_ms\": " << Number(m_clockSync.OffsetMs())
            << ", \"estimate_ms\": " << Number(m_clockSync.EstimateMs())
            << ", \"bound_ms\": " << Number(m_clockSync.ErrorBoundMs()) << ", \"rtt_ms\": " << Number(m_clockSync.RttMs())
            << ", \"samples\": " << m_clockSync.Samples() << ", \"agreeing\": " << m_clockSync.Agreeing()
            << ", \"steps\": " << m_clockSync.Steps() << ", \"synced_at_ms\": " << Number(m_syncedAtMs)
            << ", \"results\": {";
        bool first = true;
        for (const auto& [name, count] : m_clockResults)
        {
            out << (first ? "" : ", ") << Json(name) << ": " << count;
            first = false;
        }
        out << "},\n    \"history\": [";
        for (size_t index = 0; index < m_history.size(); ++index)
        {
            const auto& row = m_history[index];
            out << (index ? ", " : "") << "[" << Number(row[0]) << ", " << Number(row[1]) << ", " << Number(row[2])
                << ", " << static_cast<int>(row[3]) << "]";
        }
        out << "]},\n";
        out << "  \"reliable\": {\"sent\": " << m_eventsSent << ", \"received\": " << m_eventsReceived
            << ", \"first_mismatch\": " << (m_firstMismatch ? std::to_string(*m_firstMismatch) : "null")
            << ", \"events_start_ms\": " << Number(m_eventsStartMs) << "},\n";
        out << "  \"unreliable\": {\"sent\": " << m_snapshotsSent << ", \"received\": " << m_snapshotsSeen.size()
            << ", \"duplicates\": " << m_snapshotDuplicates << ", \"reordered\": " << m_snapshotsReordered << "},\n";
        if (m_connection)
        {
            const LinkStats& stats = m_connection->Stats();
            out << "  \"link\": {\"packets_sent\": " << stats.packetsSent << ", \"packets_received\": "
                << stats.packetsReceived << ", \"packets_acked\": " << stats.packetsAcked << ", \"packets_lost\": "
                << stats.packetsLost << ", \"duplicates\": " << stats.duplicates << ", \"reliable_sent\": "
                << stats.reliableSent << ", \"reliable_resent\": " << stats.reliableResent
                << ", \"reliable_delivered\": " << stats.reliableDelivered << ", \"reliable_duplicates\": "
                << stats.reliableDuplicates << ", \"rtt_samples\": " << stats.rttSamples << ", \"rtt_skipped\": "
                << stats.rttSkipped << ", \"srtt_ms\": " << m_connection->RttMs() << ", \"rto_ms\": "
                << m_connection->Rto() * 1000.0 << ", \"max_rtt_sample_ms\": "
                << Number(m_connection->MaxRttSample() ? std::optional<double>(*m_connection->MaxRttSample() * 1000.0)
                                                       : std::nullopt)
                << ", \"pending_reliable\": " << m_connection->PendingReliable() << ", \"loss_in\": "
                << m_connection->LossIn() << ", \"loss_out\": " << m_connection->LossOut() << "},\n";
        }
        out << "  \"sim\": {\"up_submitted\": " << m_uplink.submitted << ", \"up_dropped\": " << m_uplink.dropped
            << ", \"down_submitted\": " << m_downlink.submitted << ", \"down_dropped\": " << m_downlink.dropped << "},\n";
        out << "  \"errors\": {\"decode\": " << m_decodeErrors << ", \"framing\": " << m_framingErrors << "}\n";
        out << "}\n";
        std::ofstream file(m_options.report, std::ios::binary);
        file << out.str();
    }

    Options m_options;
    LocalClock m_clock;
    Sleeper m_sleeper;
    LinkSim m_uplink;
    LinkSim m_downlink;
    SOCKET m_socket = INVALID_SOCKET;
    sockaddr_in m_relay{};
    std::string m_error;
    coopv2::Welcome m_welcome{};
    std::optional<Connection> m_connection;
    std::vector<Bytes> m_backlog;
    std::optional<double> m_handshakeMs;
    ClockSync m_clockSync;
    std::optional<double> m_syncedAtMs;
    std::map<std::string, uint64_t> m_clockResults;
    std::vector<std::array<double, 4>> m_history;
    std::vector<std::pair<uint8_t, std::pair<uint8_t, Bytes>>> m_pending;
    std::optional<uint8_t> m_otherPeer;
    std::optional<uint8_t> m_disconnected;
    uint32_t m_eventsSent = 0;
    uint32_t m_eventsReceived = 0;
    std::optional<uint32_t> m_firstMismatch;
    std::optional<double> m_eventsStartMs;
    uint32_t m_snapshotsSent = 0;
    std::set<uint32_t> m_snapshotsSeen;
    uint64_t m_snapshotDuplicates = 0;
    uint64_t m_snapshotsReordered = 0;
    uint64_t m_decodeErrors = 0;
    uint64_t m_framingErrors = 0;
};
} // namespace

int main(int argc, char** argv)
{
    Options options;
    if (!ParseOptions(argc, argv, options))
    {
        std::puts("usage: coopnet_v2_loopback_client --relay-port N --role host|joiner [--room R] [--password P] "
                  "[--name N] [--duration S] [--messages M] [--rate R] [--latency-ms L] [--jitter-ms J] "
                  "[--loss-pct P] [--dup-pct D] [--seed X] [--report path]");
        return EXIT_FAILURE;
    }
    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0)
    {
        std::puts("WSAStartup failed");
        return EXIT_FAILURE;
    }
    int code = EXIT_FAILURE;
    {
        Client client(options);
        if (client.Open())
        {
            client.Run();
            code = client.Welcomed() ? EXIT_SUCCESS : EXIT_FAILURE;
        }
    }
    WSACleanup();
    return code;
}
