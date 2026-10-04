// A C++ drop-in for the relay repo's client_v2.py: plays the host or the joiner against
// relay_v2.py with the plugin's protocol v2 modules (src/v2: codec, delta snapshots, Connection,
// ClockSync, SnapshotBuffer), so run_demo.py can run every scenario with a C++ client on either
// end (run_demo.py --host-exe / --joiner-exe; tools/run_v2_demo.py drives the combinations).
//
//   coopnet_v2_demo_client --role host --relay-port 11778 --room r --password pw --duration 25
//       --join-flags 1 --player-path course --seed 11 --up-latency-ms 47.5 --up-jitter-ms 20
//       --up-loss-pct 0.5 --down-latency-ms 47.5 ... --report host.json
//   coopnet_v2_demo_client --dump-world world.json     (testworld cross-check, no network)
//
// It takes client_v2.py's arguments, follows the same schedule and writes the same JSON report:
// cookie handshake, relay clock (the C++ ClockSync: interval intersection, 10 Hz until warm then
// 2 Hz, where client_v2.py uses interp.py's lowest-RTT estimate), 30 Hz PLAYER_SNAPSHOT (the
// joiner drives on the demo path and sends the VehicleBlock), the host's 10 Hz delta ENTITY_SNAPSHOT
// with interest management and the joiner's SNAPSHOT_ACK, the scripted reliable events (equip,
// vehicle enter/exit, hit, death, time/weather, chat, teleport, world facts, mod list, a
// 30-message burst), and 60 Hz rendering through SnapshotBuffer, measured against the sender's
// ground truth (coopnet/testworld.py, ported in V2DemoWorld) and against the v1 latest-packet
// model. Link impairments are simulated in-process on the uplink and the downlink. The run ends with
// client_v2.py's drain: events stop kDrainS before --duration, a final "<name> done" chat ends the
// reliable stream, and after --duration the client only acks and resends until the relay acked all
// of its events and the other player's marker arrived (or kLingerMaxS passed).
// Not part of the plugin.

#include "V2DemoJson.hpp"
#include "V2DemoWorld.hpp"
#include "V2TestSupport.hpp"

#include "core/Version.hpp"
#include "v2/ClockSync.hpp"
#include "v2/SnapshotBuffer.hpp"
#include "v2/V2Codec.hpp"
#include "v2/V2Delta.hpp"
#include "v2/V2Hash.hpp"
#include "v2/V2Reliability.hpp"

#include <winsock2.h>
#include <ws2tcpip.h>
#include <mswsock.h>
#include <windows.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <map>
#include <optional>
#include <queue>
#include <set>
#include <string>
#include <string_view>
#include <variant>
#include <vector>

using namespace coopnet::v2;
using demo::JsonValue;
using demo::PlayerPath;

namespace
{
constexpr double kPlayerIntervalS = 1.0 / 30.0;
constexpr double kFrameIntervalS = 1.0 / 60.0;
constexpr uint32_t kEntityEveryNPlayerTicks = 3;
constexpr double kEventIntervalS = 0.15;
constexpr double kChatIntervalS = 1.2;
constexpr int kBurstSize = 30;
constexpr double kDrainS = 4.0;
constexpr double kLingerMaxS = 20.0;
constexpr double kAckFlushS = 0.05;
constexpr std::string_view kDoneSuffix = " done";
constexpr double kHandshakeTimeoutS = 10.0;
constexpr const char* kGameVersion = "2.31a";
constexpr uint16_t kModVersion[3] = {0, 2, 0};
// client_v2.py's DEMO_MODS: the host's room uses strict mods, so both clients must hash the same list.
const std::vector<ModEntry>& DemoMods()
{
    static const std::vector<ModEntry> mods{{"CP2077Coop", "0.2.0"},
                                            {"Codeware", "1.18.0"},
                                            {"redscript", "0.5.27"},
                                            {"RED4ext", "1.27.0"},
                                            {"cyber_engine_tweaks", "1.35.0"}};
    return mods;
}

uint8_t U8(auto aValue)
{
    return static_cast<uint8_t>(aValue);
}

const char* RoleName(uint8_t aRole)
{
    switch (aRole)
    {
    case 0:
        return "ANY";
    case 1:
        return "HOST";
    case 2:
        return "JOINER";
    case 3:
        return "SPECTATOR";
    default:
        return "?";
    }
}

const char* RejectName(uint8_t aReason)
{
    static const char* const names[] = {"?", "VERSION", "BAD_COOKIE", "ROOM_FULL", "BAD_KEY", "ROLE_TAKEN",
                                        "MOD_MISMATCH", "RATE_LIMITED", "SERVER_FULL", "MALFORMED", "GAME_BUILD"};
    return aReason < std::size(names) ? names[aReason] : "?";
}

const char* DisconnectName(uint8_t aReason)
{
    static const char* const names[] = {"?", "QUIT", "TIMEOUT", "KICKED", "RATE_LIMIT", "PROTOCOL_ERROR",
                                        "SERVER_SHUTDOWN", "SLOW_CONSUMER"};
    return aReason < std::size(names) ? names[aReason] : "?";
}

std::string Hex(ByteSpan aBytes)
{
    static const char digits[] = "0123456789abcdef";
    std::string out;
    out.reserve(aBytes.size() * 2);
    for (const uint8_t byte : aBytes)
    {
        out += digits[byte >> 4];
        out += digits[byte & 0xF];
    }
    return out;
}

ByteSpan AsSpan(const Bytes& aBytes)
{
    return {aBytes.data(), aBytes.size()};
}

double Distance(const demo::Vec3& aLeft, const demo::Vec3& aRight)
{
    const double dx = aLeft[0] - aRight[0];
    const double dy = aLeft[1] - aRight[1];
    const double dz = aLeft[2] - aRight[2];
    return std::sqrt(dx * dx + dy * dy + dz * dz);
}

// client_v2.py summarize(): count, mean and the nearest-rank p50/p95/p99/max.
JsonValue Summarize(std::vector<double> aValues)
{
    JsonValue out = JsonValue::Object();
    out.Set("count", static_cast<uint64_t>(aValues.size()));
    if (aValues.empty())
    {
        return out;
    }
    std::sort(aValues.begin(), aValues.end());
    const auto pick = [&](double aFraction) {
        const auto index = static_cast<size_t>(aFraction * static_cast<double>(aValues.size() - 1) + 0.5);
        return aValues[std::min(aValues.size() - 1, index)];
    };
    double sum = 0.0;
    for (const double value : aValues)
    {
        sum += value;
    }
    out.Set("mean", sum / static_cast<double>(aValues.size()));
    out.Set("p50", pick(0.5));
    out.Set("p95", pick(0.95));
    out.Set("p99", pick(0.99));
    out.Set("max", aValues.back());
    return out;
}

struct TrackPoint
{
    double t = 0.0;
    demo::Vec3 pos{};
};

// |second derivative| of a (time s, position) track, as client_v2.py accelerations().
std::vector<double> Accelerations(const std::vector<TrackPoint>& aTrack)
{
    std::vector<double> result;
    for (size_t index = 1; index + 1 < aTrack.size(); ++index)
    {
        const TrackPoint& p0 = aTrack[index - 1];
        const TrackPoint& p1 = aTrack[index];
        const TrackPoint& p2 = aTrack[index + 1];
        if (p1.t - p0.t <= 0 || p2.t - p1.t <= 0)
        {
            continue;
        }
        double squared = 0.0;
        for (size_t axis = 0; axis < 3; ++axis)
        {
            const double v0 = (p1.pos[axis] - p0.pos[axis]) / (p1.t - p0.t);
            const double v1 = (p2.pos[axis] - p1.pos[axis]) / (p2.t - p1.t);
            const double accel = 2.0 * (v1 - v0) / (p2.t - p0.t);
            squared += accel * accel;
        }
        result.push_back(std::sqrt(squared));
    }
    return result;
}

// ---- arguments (client_v2.py parse_args) ---------------------------------------------------------

struct LegModel
{
    double latencyMs = 0.0;
    double jitterMs = 0.0;
    double lossPct = 0.0;
    double dupPct = 0.0;
};

struct Options
{
    std::string relayHost = "127.0.0.1";
    uint16_t relayPort = 11778;
    std::string role;
    std::string room = "demo";
    std::string password;
    std::optional<std::string> name;
    uint16_t joinFlags = 0;
    double duration = 20.0;
    size_t entityBudget = 1000;
    uint64_t worldSeed = 7;
    std::optional<uint64_t> seed;
    std::array<double, 2> legacyCenter{-1400.0, 200.0};
    double legacyRadius = 5.0;
    LegModel up;
    LegModel down;
    std::optional<std::string> report;
    std::string playerPath = "demo";
    std::optional<std::string> dumpWorld;

    [[nodiscard]] PlayerPath Path() const
    {
        return playerPath == "course" ? PlayerPath::Course : PlayerPath::Demo;
    }
    [[nodiscard]] std::string Name() const
    {
        return name.value_or(role);
    }
};

bool ParseOptions(int argc, char** argv, Options& aOut, std::string& aError)
{
    try
    {
        for (int index = 1; index < argc; ++index)
        {
            const std::string key = argv[index];
            const auto next = [&]() -> std::string {
                if (index + 1 >= argc)
                {
                    throw std::invalid_argument(key + " needs a value");
                }
                return argv[++index];
            };
            if (key == "--relay-host")
                aOut.relayHost = next();
            else if (key == "--relay-port")
                aOut.relayPort = static_cast<uint16_t>(std::stoul(next()));
            else if (key == "--role")
                aOut.role = next();
            else if (key == "--room")
                aOut.room = next();
            else if (key == "--password")
                aOut.password = next();
            else if (key == "--name")
                aOut.name = next();
            else if (key == "--join-flags")
                aOut.joinFlags = static_cast<uint16_t>(std::stoul(next()));
            else if (key == "--duration")
                aOut.duration = std::stod(next());
            else if (key == "--entity-budget")
                aOut.entityBudget = std::stoul(next());
            else if (key == "--world-seed")
                aOut.worldSeed = std::stoull(next());
            else if (key == "--seed")
                aOut.seed = std::stoull(next());
            else if (key == "--legacy-center")
            {
                aOut.legacyCenter[0] = std::stod(next());
                aOut.legacyCenter[1] = std::stod(next());
            }
            else if (key == "--legacy-radius")
                aOut.legacyRadius = std::stod(next());
            else if (key == "--up-latency-ms")
                aOut.up.latencyMs = std::stod(next());
            else if (key == "--up-jitter-ms")
                aOut.up.jitterMs = std::stod(next());
            else if (key == "--up-loss-pct")
                aOut.up.lossPct = std::stod(next());
            else if (key == "--up-dup-pct")
                aOut.up.dupPct = std::stod(next());
            else if (key == "--down-latency-ms")
                aOut.down.latencyMs = std::stod(next());
            else if (key == "--down-jitter-ms")
                aOut.down.jitterMs = std::stod(next());
            else if (key == "--down-loss-pct")
                aOut.down.lossPct = std::stod(next());
            else if (key == "--down-dup-pct")
                aOut.down.dupPct = std::stod(next());
            else if (key == "--report")
                aOut.report = next();
            else if (key == "--player-path")
                aOut.playerPath = next();
            else if (key == "--dump-world")
                aOut.dumpWorld = next();
            else
                throw std::invalid_argument("unknown argument " + key);
        }
    }
    catch (const std::exception& error)
    {
        aError = error.what();
        return false;
    }
    if (aOut.playerPath != "demo" && aOut.playerPath != "course")
    {
        aError = "--player-path must be demo or course";
        return false;
    }
    if (!aOut.dumpWorld && aOut.role != "host" && aOut.role != "joiner")
    {
        aError = "--role host|joiner is required";
        return false;
    }
    return true;
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

    // Seconds since the epoch (QueryPerformanceCounter, the clock behind time.perf_counter()).
    [[nodiscard]] double NowS() const
    {
        LARGE_INTEGER now;
        QueryPerformanceCounter(&now);
        return static_cast<double>(now.QuadPart - m_epoch) / m_frequency;
    }

    // The epoch on time.perf_counter()'s scale (the report's start_perf).
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

// ---- link simulator (coopnet/linksim.py, one direction) -------------------------------------------

class LinkSim
{
public:
    LinkSim(const LegModel& aModel, uint64_t aSeed)
        : m_model(aModel)
        , m_rng(aSeed)
    {
    }

    [[nodiscard]] bool Active() const
    {
        return m_model.latencyMs != 0.0 || m_model.jitterMs != 0.0 || m_model.lossPct != 0.0 || m_model.dupPct != 0.0;
    }

    void Submit(double aNowS, const Bytes& aData)
    {
        ++m_submitted;
        if (m_rng.Unit() * 100.0 < m_model.lossPct)
        {
            ++m_dropped;
            return;
        }
        int copies = 1;
        if (m_rng.Unit() * 100.0 < m_model.dupPct)
        {
            copies = 2;
            ++m_duplicated;
        }
        for (int copy = 0; copy < copies; ++copy)
        {
            const double delay = (m_model.latencyMs + m_rng.Unit() * m_model.jitterMs) / 1000.0;
            m_queue.push(Entry{aNowS + delay, ++m_counter, aData});
        }
    }

    std::vector<Bytes> PopDue(double aNowS)
    {
        std::vector<Bytes> due;
        while (!m_queue.empty() && m_queue.top().due <= aNowS)
        {
            due.push_back(m_queue.top().data);
            m_queue.pop();
        }
        return due;
    }

    [[nodiscard]] JsonValue Describe() const
    {
        JsonValue out = JsonValue::Object();
        out.Set("latency_ms", m_model.latencyMs);
        out.Set("jitter_ms", m_model.jitterMs);
        out.Set("loss_pct", m_model.lossPct);
        out.Set("dup_pct", m_model.dupPct);
        out.Set("submitted", m_submitted);
        out.Set("dropped", m_dropped);
        out.Set("duplicated", m_duplicated);
        return out;
    }

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

    LegModel m_model;
    test::Rng m_rng;
    uint64_t m_counter = 0;
    uint64_t m_submitted = 0;
    uint64_t m_dropped = 0;
    uint64_t m_duplicated = 0;
    std::priority_queue<Entry, std::vector<Entry>, std::greater<>> m_queue;
};

// ---- UDP socket to the relay plus the simulated uplink/downlink (client_v2.py Endpoint) ----------

class Endpoint
{
public:
    Endpoint(const LegModel& aUp, const LegModel& aDown, uint64_t aSeed)
        : uplink(aUp, aSeed)
        , downlink(aDown, aSeed + 1)
    {
    }
    ~Endpoint()
    {
        if (m_socket != INVALID_SOCKET)
        {
            closesocket(m_socket);
        }
    }
    Endpoint(const Endpoint&) = delete;
    Endpoint& operator=(const Endpoint&) = delete;

    bool Open(const std::string& aHost, uint16_t aPort, std::string& aError)
    {
        m_relay.sin_family = AF_INET;
        m_relay.sin_port = htons(aPort);
        if (inet_pton(AF_INET, aHost.c_str(), &m_relay.sin_addr) != 1)
        {
            addrinfo hints{};
            hints.ai_family = AF_INET;
            hints.ai_socktype = SOCK_DGRAM;
            addrinfo* found = nullptr;
            if (getaddrinfo(aHost.c_str(), nullptr, &hints, &found) != 0 || found == nullptr)
            {
                aError = "cannot resolve relay host " + aHost;
                return false;
            }
            m_relay.sin_addr = reinterpret_cast<const sockaddr_in*>(found->ai_addr)->sin_addr;
            freeaddrinfo(found);
        }
        m_socket = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        if (m_socket == INVALID_SOCKET)
        {
            aError = "socket failed";
            return false;
        }
        u_long nonBlocking = 1;
        ioctlsocket(m_socket, FIONBIO, &nonBlocking);
        BOOL reportReset = FALSE;
        DWORD returned = 0;
        WSAIoctl(m_socket, SIO_UDP_CONNRESET, &reportReset, sizeof(reportReset), nullptr, 0, &returned, nullptr, nullptr);
        sockaddr_in local{};
        local.sin_family = AF_INET;
        // A loopback relay gets a loopback socket (no firewall prompt for a new executable).
        const bool loopback = (ntohl(m_relay.sin_addr.s_addr) >> 24) == 127;
        local.sin_addr.s_addr = htonl(loopback ? INADDR_LOOPBACK : INADDR_ANY);
        if (bind(m_socket, reinterpret_cast<const sockaddr*>(&local), sizeof(local)) != 0)
        {
            aError = "bind failed";
            return false;
        }
        return true;
    }

    void Send(double aNowS, const Bytes& aData)
    {
        bytesSent += aData.size();
        if (uplink.Active())
        {
            uplink.Submit(aNowS, aData);
        }
        else
        {
            SendTo(aData);
        }
    }

    // Bypasses the uplink simulation (the final DISCONNECT, as client_v2.py does).
    void SendTo(const Bytes& aData)
    {
        if (sendto(m_socket, reinterpret_cast<const char*>(aData.data()), static_cast<int>(aData.size()), 0,
                   reinterpret_cast<const sockaddr*>(&m_relay), sizeof(m_relay)) == SOCKET_ERROR)
        {
            ++sendErrors;
        }
    }

    std::vector<Bytes> Pump(double aNowS)
    {
        for (const Bytes& data : uplink.PopDue(aNowS))
        {
            SendTo(data);
        }
        std::vector<Bytes> arrived;
        uint8_t buffer[2048];
        for (;;)
        {
            sockaddr_in from{};
            int fromLength = sizeof(from);
            const int received = recvfrom(m_socket, reinterpret_cast<char*>(buffer), sizeof(buffer), 0,
                                          reinterpret_cast<sockaddr*>(&from), &fromLength);
            if (received == SOCKET_ERROR)
            {
                if (WSAGetLastError() == WSAECONNRESET)
                {
                    continue;
                }
                break;
            }
            if (from.sin_addr.s_addr != m_relay.sin_addr.s_addr || from.sin_port != m_relay.sin_port)
            {
                ++foreign;
                continue;
            }
            bytesReceived += static_cast<uint64_t>(received);
            Bytes data(buffer, buffer + received);
            if (downlink.Active())
            {
                downlink.Submit(aNowS, data);
            }
            else
            {
                arrived.push_back(std::move(data));
            }
        }
        for (Bytes& data : downlink.PopDue(aNowS))
        {
            arrived.push_back(std::move(data));
        }
        return arrived;
    }

    LinkSim uplink;
    LinkSim downlink;
    uint64_t bytesSent = 0;
    uint64_t bytesReceived = 0;
    uint64_t foreign = 0;
    uint64_t sendErrors = 0;

private:
    SOCKET m_socket = INVALID_SOCKET;
    sockaddr_in m_relay{};
};

// ---- interest management (snapshot.py InterestManager) -------------------------------------------

class InterestManager
{
public:
    struct Candidate
    {
        uint8_t kind = 0;
        demo::Vec3 pos{};
        bool forced = false;
    };

    // Returns the relevant net ids with their priority weights.
    std::map<uint16_t, double> Update(const demo::Vec3& aViewer, const std::map<uint16_t, Candidate>& aEntities)
    {
        struct Ranked
        {
            int forcedRank;
            double distance;
            uint16_t netId;
            double weight;
        };
        std::vector<Ranked> ranked;
        for (const auto& [netId, candidate] : aEntities)
        {
            const double distance = Distance(aViewer, candidate.pos);
            double radius = candidate.kind == U8(coopv2::EntityKind::Vehicle) ? kVehicleRadius : kNpcRadius;
            if (m_relevant.contains(netId))
            {
                radius += kHysteresis;
            }
            if (candidate.forced || distance <= radius)
            {
                const double weight = KindWeight(candidate.kind) * (1.0 + 20.0 / (20.0 + distance));
                ranked.push_back(Ranked{candidate.forced ? 0 : 1, distance, netId, weight});
            }
        }
        std::sort(ranked.begin(), ranked.end(), [](const Ranked& aLeft, const Ranked& aRight) {
            if (aLeft.forcedRank != aRight.forcedRank)
                return aLeft.forcedRank < aRight.forcedRank;
            if (aLeft.distance != aRight.distance)
                return aLeft.distance < aRight.distance;
            return aLeft.netId < aRight.netId;
        });
        if (ranked.size() > kMaxEntities)
        {
            ranked.resize(kMaxEntities);
        }
        std::map<uint16_t, double> weights;
        m_relevant.clear();
        for (const Ranked& entry : ranked)
        {
            weights[entry.netId] = entry.weight;
            m_relevant.insert(entry.netId);
        }
        return weights;
    }

private:
    static double KindWeight(uint8_t aKind)
    {
        switch (static_cast<coopv2::EntityKind>(aKind))
        {
        case coopv2::EntityKind::CrowdNpc:
            return 1.0;
        case coopv2::EntityKind::CombatNpc:
            return 3.0;
        case coopv2::EntityKind::QuestNpc:
            return 2.0;
        case coopv2::EntityKind::Vehicle:
            return 2.0;
        case coopv2::EntityKind::Device:
            return 0.5;
        }
        return 1.0;
    }

    static constexpr double kNpcRadius = 100.0;
    static constexpr double kVehicleRadius = 200.0;
    static constexpr double kHysteresis = 15.0;
    static constexpr size_t kMaxEntities = 128;
    std::set<uint16_t> m_relevant;
};

// ---- the v1 receive model (interp.py LatestSlotFollower) ------------------------------------------

class LatestSlotFollower
{
public:
    void OnSample(uint16_t aSeq, const demo::Vec3& aPos)
    {
        if (!m_latestSeq || static_cast<uint16_t>(aSeq - *m_latestSeq) < 32768)
        {
            m_latestSeq = aSeq;
            m_target = aPos;
        }
    }

    std::optional<demo::Vec3> Frame(double aDtS)
    {
        if (!m_target)
        {
            return std::nullopt;
        }
        if (!m_pos)
        {
            m_pos = m_target;
        }
        else
        {
            const double u = std::min(1.0, aDtS * kFollowRate);
            for (size_t axis = 0; axis < 3; ++axis)
            {
                (*m_pos)[axis] += ((*m_target)[axis] - (*m_pos)[axis]) * u;
            }
        }
        return m_pos;
    }

private:
    static constexpr double kFollowRate = 12.0;
    std::optional<uint16_t> m_latestSeq;
    std::optional<demo::Vec3> m_target;
    std::optional<demo::Vec3> m_pos;
};

// ---- the client ----------------------------------------------------------------------------------

struct RemotePlayer
{
    explicit RemotePlayer(uint8_t aRole)
        : role(aRole)
        , buffer(kPlayerInterp)
    {
    }

    uint8_t role;
    SnapshotBuffer buffer;
    LatestSlotFollower follower;
    std::optional<uint16_t> latestSeq;
    std::optional<uint16_t> firstSeq;
    uint64_t received = 0;
    uint64_t duplicates = 0;
    uint64_t reordered = 0;
    uint32_t flagsSeen = 0;
    uint64_t drivingSeen = 0;
    std::optional<PlayerSnapshotMsg> last;
    std::vector<double> legacyRadiusError;
};

struct PendingMessage
{
    uint8_t type = 0;
    uint8_t peer = 0;
    Bytes body;
};

struct SentEvent
{
    uint8_t type = 0;
    uint8_t dest = 0;
    std::string hex;
};

struct ReceivedEvent
{
    uint8_t src = 0;
    uint8_t type = 0;
    std::string hex;
};

struct MotionErrors
{
    std::vector<double> position;
    std::vector<double> yaw;
};

class DemoClient
{
public:
    explicit DemoClient(const Options& aOptions)
        : m_options(aOptions)
        , m_endpoint(aOptions.up, aOptions.down, aOptions.seed.value_or(DefaultSeed()))
        , m_world(demo::BuildEntities(aOptions.worldSeed))
        , m_encoder(aOptions.entityBudget)
        , m_entityStream(kEntityInterp)
    {
        m_roleWanted = aOptions.role == "host" ? U8(coopv2::Role::Host) : U8(coopv2::Role::Joiner);
        for (const demo::EntitySpec& spec : m_world)
        {
            m_worldById[spec.netId] = &spec;
        }
    }

    JsonValue Run()
    {
        std::string error;
        if (!m_endpoint.Open(m_options.relayHost, m_options.relayPort, error))
        {
            m_fatal = error;
        }
        else if (Handshake())
        {
            Loop();
        }
        return BuildReport();
    }

    [[nodiscard]] bool Welcomed() const
    {
        return m_connection.has_value();
    }
    [[nodiscard]] size_t EventsSent() const
    {
        return m_sentEvents.size();
    }
    [[nodiscard]] size_t EventsReceived() const
    {
        return m_receivedEvents.size();
    }

private:
    static uint64_t DefaultSeed()
    {
        LARGE_INTEGER now;
        QueryPerformanceCounter(&now);
        return static_cast<uint64_t>(now.QuadPart) ^ (static_cast<uint64_t>(GetCurrentProcessId()) << 32);
    }

    // ---- utilities --------------------------------------------------------------------------------

    [[nodiscard]] double LocalMs(double aNowS) const
    {
        return aNowS * 1000.0;
    }
    [[nodiscard]] double RelayMs(double aNowS) const
    {
        return m_clockSync.RelayMs(LocalMs(aNowS));
    }
    [[nodiscard]] bool IsHost() const
    {
        return m_role == U8(coopv2::Role::Host);
    }

    JoinInfo MakeJoin(uint64_t aNonce) const
    {
        JoinInfo join;
        join.fixed.minor = coopv2::kProtoMinor;
        join.fixed.role = m_roleWanted;
        join.fixed.join_flags = m_options.joinFlags;
        join.fixed.caps = 0x1FF; // proto.ALL_CAPS
        join.fixed.game_build = GameBuildId(kGameVersion);
        join.fixed.mod_major = kModVersion[0];
        join.fixed.mod_minor = kModVersion[1];
        join.fixed.mod_patch = kModVersion[2];
        join.fixed.mod_hash = ModListHash(DemoMods());
        join.fixed.mod_count = static_cast<uint16_t>(DemoMods().size());
        join.fixed.client_nonce = aNonce;
        join.fixed.resume_token = 0;
        join.room = m_options.room;
        join.name = m_options.Name();
        return join;
    }

    // The other v2 player (ids below 200); bridged v1 players (200+) only when asked.
    [[nodiscard]] std::optional<uint8_t> OtherPeer(bool aIncludeLegacy = false) const
    {
        for (const auto& [peerId, info] : m_peers)
        {
            if (peerId != m_peerId && (peerId < coopv2::kLegacyPeerBase || aIncludeLegacy))
            {
                return peerId;
            }
        }
        return std::nullopt;
    }

    // ---- handshake --------------------------------------------------------------------------------

    bool Handshake()
    {
        test::Rng nonceRng(DefaultSeed());
        const JoinInfo join = MakeJoin(nonceRng.U64());
        std::optional<AuthInfo> auth;
        const double deadline = m_clock.NowS() + kHandshakeTimeoutS;
        double nextSend = 0.0;
        while (m_clock.NowS() < deadline)
        {
            const double now = m_clock.NowS();
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
                m_endpoint.Send(now, datagram);
                nextSend = now + 0.25;
            }
            std::vector<Bytes> arrived = m_endpoint.Pump(now);
            for (size_t index = 0; index < arrived.size(); ++index)
            {
                DecodedPacket packet;
                if (DecodePacket(AsSpan(arrived[index]), packet) != Status::Ok)
                {
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
                        m_peerId = welcome.peer_id;
                        m_role = welcome.role;
                        m_connection.emplace(welcome.token);
                        m_handshakeMs = LocalMs(now);
                        m_backlog.assign(std::make_move_iterator(arrived.begin() + static_cast<std::ptrdiff_t>(index) + 1),
                                         std::make_move_iterator(arrived.end()));
                        return true;
                    }
                }
                else if (type == coopv2::PacketType::Reject)
                {
                    RejectInfo reject;
                    if (DecodeReject(packet.body, reject) == Status::Ok)
                    {
                        JsonValue info = JsonValue::Object();
                        info.Set("reason", RejectName(reject.reason));
                        info.Set("text", reject.text);
                        info.Set("relay_minor", JsonValue::Array({JsonValue(static_cast<uint32_t>(reject.minorMin)),
                                                                  JsonValue(static_cast<uint32_t>(reject.minorMax))}));
                        m_reject = info;
                        return false;
                    }
                }
            }
            m_sleeper.Sleep(500);
        }
        JsonValue info = JsonValue::Object();
        info.Set("reason", "TIMEOUT");
        info.Set("text", "no WELCOME");
        m_reject = info;
        return false;
    }

    // ---- main loop --------------------------------------------------------------------------------

    void Loop()
    {
        double now = m_clock.NowS();
        const double end = m_options.duration;
        double nextPlayer = now;
        double nextFrame = now;
        double lastFrame = now;
        std::vector<Bytes> backlog = std::move(m_backlog);
        for (const Bytes& data : backlog) // session DATA that arrived in the same batch as the WELCOME
        {
            OnDatagram(data, now);
        }
        while (!m_disconnected && !m_fatal && !Finished(now, end))
        {
            for (const Bytes& data : m_endpoint.Pump(now))
            {
                OnDatagram(data, now);
            }
            if (now >= end)
            {
                Linger(now);
                m_sleeper.Sleep(500);
                now = m_clock.NowS();
                continue;
            }
            if (m_clockSync.RequestDue(LocalMs(now)))
            {
                QueueUnreliable(coopv2::kPeerRelay, Body{m_clockSync.MakeRequest(LocalMs(now))});
            }
            if (now >= nextPlayer)
            {
                PlayerTick(now);
                nextPlayer = std::max(nextPlayer + kPlayerIntervalS, now - kPlayerIntervalS);
            }
            if (now >= nextFrame)
            {
                RenderFrame(now, now - lastFrame);
                lastFrame = now;
                nextFrame = std::max(nextFrame + kFrameIntervalS, now - kFrameIntervalS);
            }
            EventsTick(now);
            if (!m_pending.empty() || m_connection->ReliableDue(now))
            {
                Flush(now);
            }
            m_sleeper.Sleep(500);
            now = m_clock.NowS();
        }
        m_lingerS = std::max(0.0, now - end);
        Bytes bye;
        EncodeDisconnect(m_connection->Token(), U8(coopv2::DisconnectReason::Quit), bye);
        m_endpoint.SendTo(bye);
    }

    // After --duration: finished once both reliable streams are drained, or at the cap.
    bool Finished(double aNowS, double aEndS)
    {
        if (aNowS < aEndS)
        {
            return false;
        }
        if (aNowS >= aEndS + kLingerMaxS)
        {
            m_lingerTimedOut = true;
            return true;
        }
        if (m_connection->PendingReliable() != 0)
        {
            return false;
        }
        return !OtherPeer() || !m_eventsStarted || m_otherDone;
    }

    // Past --duration: no snapshots, frames or events; resend and ack until drained.
    void Linger(double aNowS)
    {
        if (!m_pending.empty() || m_connection->ReliableDue(aNowS))
        {
            Flush(aNowS);
        }
        else if (m_connection->AckPending() && aNowS - m_connection->LastSend().value_or(0.0) >= kAckFlushS)
        {
            std::vector<Bytes> packets;
            m_connection->BuildPackets(aNowS, {}, true, packets);
            for (const Bytes& packet : packets)
            {
                m_endpoint.Send(aNowS, packet);
            }
        }
    }

    void QueueUnreliable(uint8_t aPeer, const Body& aBody)
    {
        PendingMessage message{TypeOf(aBody), aPeer, {}};
        if (EncodeBody(aBody, message.body) != Status::Ok)
        {
            ++m_encodeErrors;
            return;
        }
        m_pending.push_back(std::move(message));
    }

    void Flush(double aNowS)
    {
        std::vector<LinkMessage> messages;
        messages.reserve(m_pending.size());
        for (const PendingMessage& message : m_pending)
        {
            messages.push_back(LinkMessage{message.type, message.peer, AsSpan(message.body)});
        }
        std::vector<Bytes> packets;
        if (m_connection->BuildPackets(aNowS, messages, false, packets) != Status::Ok)
        {
            ++m_encodeErrors;
        }
        m_pending.clear();
        for (const Bytes& packet : packets)
        {
            m_endpoint.Send(aNowS, packet);
        }
    }

    // ---- receive ----------------------------------------------------------------------------------

    void OnDatagram(const Bytes& aData, double aNowS)
    {
        DecodedPacket packet;
        if (DecodePacket(AsSpan(aData), packet) != Status::Ok)
        {
            ++m_invalidMessages;
            return;
        }
        const auto type = static_cast<coopv2::PacketType>(packet.header.type);
        if (type == coopv2::PacketType::Disconnect && packet.header.token == m_connection->Token())
        {
            uint8_t reason = 0;
            DecodeDisconnect(packet.body, reason);
            m_disconnected = DisconnectName(reason);
            return;
        }
        if (type != coopv2::PacketType::Data || packet.header.token != m_connection->Token())
        {
            return;
        }
        std::vector<DeliveredMessage> delivered;
        if (m_connection->OnPacket(aNowS, packet.header, packet.body, delivered, aData.size()) != Status::Ok)
        {
            ++m_invalidMessages;
            return;
        }
        for (const DeliveredMessage& message : delivered)
        {
            Body body;
            if (DecodeBody(message.type, AsSpan(message.body), body) != Status::Ok)
            {
                ++m_invalidMessages;
                continue;
            }
            if (message.reliable && message.peer != coopv2::kPeerRelay)
            {
                m_receivedEvents.push_back(ReceivedEvent{message.peer, message.type, Hex(AsSpan(message.body))});
            }
            OnMessage(message.peer, body, aNowS);
        }
    }

    void ResetRenderTiming()
    {
        for (auto& [peerId, remote] : m_remote)
        {
            remote.buffer.ResetTiming();
        }
        m_entityStream.ResetTiming();
    }

    void OnMessage(uint8_t aSrc, const Body& aBody, double aNowS)
    {
        if (const auto* response = std::get_if<coopv2::TimeResp>(&aBody))
        {
            const double t3 = LocalMs(aNowS);
            const ClockResult result = m_clockSync.OnResponse(*response, t3);
            ++m_clockResults[ToString(result)];
            if (result == ClockResult::Stepped)
            {
                ResetRenderTiming();
            }
            if (m_clockSync.OffsetMs() && (result == ClockResult::Accepted || result == ClockResult::Stepped))
            {
                m_clockSamples.push_back({t3, *m_clockSync.OffsetMs(), m_clockSync.RttMs().value_or(0.0)});
            }
        }
        else if (const auto* joined = std::get_if<PeerJoinedMsg>(&aBody))
        {
            m_peers[joined->fixed.peer_id] = *joined;
            m_everSeenPeers.insert(joined->fixed.peer_id);
            if (auto found = m_remote.find(joined->fixed.peer_id); found != m_remote.end())
            {
                found->second.role = joined->fixed.role;
            }
        }
        else if (const auto* left = std::get_if<coopv2::PeerLeft>(&aBody))
        {
            m_peers.erase(left->peer_id);
        }
        else if (const auto* chat = std::get_if<ChatMsg>(&aBody))
        {
            if (aSrc == OtherPeer().value_or(coopv2::kPeerRelay) && chat->text.ends_with(kDoneSuffix))
            {
                m_otherDone = true;
            }
        }
        else if (const auto* stats = std::get_if<coopv2::LinkStats>(&aBody))
        {
            m_linkStats[stats->peer_id] = *stats;
        }
        else if (const auto* snapshot = std::get_if<PlayerSnapshotMsg>(&aBody))
        {
            OnPlayerSnapshot(aSrc, *snapshot, aNowS);
        }
        else if (const auto* entities = std::get_if<EntitySnapshotMsg>(&aBody))
        {
            OnEntitySnapshot(*entities, aNowS);
        }
        else if (const auto* ack = std::get_if<coopv2::SnapshotAck>(&aBody))
        {
            m_encoder.OnAck(ack->tick);
        }
        else if (const auto* fact = std::get_if<coopv2::WorldFact>(&aBody))
        {
            if (fact->fact_hash == demo::kEpochFact)
            {
                m_epochMs = fact->value;
            }
        }
        else if (const auto* request = std::get_if<coopv2::TeleportReq>(&aBody))
        {
            if (IsHost())
            {
                const demo::PlayerTruth truth =
                    demo::PlayerTruthAt(U8(coopv2::Role::Host), RelayMs(aNowS) / 1000.0, m_options.Path());
                coopv2::TeleportResp reply{};
                reply.req_id = request->req_id;
                reply.accepted = 1;
                reply.reserved = 0;
                reply.x = static_cast<float>(truth.pos[0]);
                reply.y = static_cast<float>(truth.pos[1]);
                reply.z = static_cast<float>(truth.pos[2]);
                reply.yaw = YawToU16(truth.yaw);
                SendEvent(aNowS, aSrc, Body{reply});
            }
        }
    }

    void OnPlayerSnapshot(uint8_t aSrc, const PlayerSnapshotMsg& aSnapshot, double aNowS)
    {
        if (!m_clockSync.Synced())
        {
            ++m_unsyncedDropped;
            return;
        }
        auto found = m_remote.find(aSrc);
        if (found == m_remote.end())
        {
            const auto info = m_peers.find(aSrc);
            const uint8_t role = info != m_peers.end() ? info->second.fixed.role : U8(coopv2::Role::Joiner);
            found = m_remote.emplace(aSrc, RemotePlayer(role)).first;
        }
        RemotePlayer& remote = found->second;
        const coopv2::PlayerSnapshot& base = aSnapshot.base;
        const uint16_t seq = base.snap_seq;
        if (remote.latestSeq)
        {
            const int32_t distance = SeqDiff(seq, *remote.latestSeq);
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
        if (!remote.latestSeq || static_cast<uint16_t>(seq - *remote.latestSeq) < 32768)
        {
            remote.latestSeq = seq;
            remote.last = aSnapshot;
        }
        if (!remote.firstSeq || static_cast<uint16_t>(*remote.firstSeq - seq) < 32768)
        {
            remote.firstSeq = seq;
        }
        ++remote.received;
        remote.flagsSeen |= base.flags;
        if (aSnapshot.vehicle)
        {
            ++remote.drivingSeen;
        }
        const demo::Vec3 pos{base.x, base.y, base.z};
        const demo::Vec3 vel{CmsToVelocity(base.vx), CmsToVelocity(base.vy), CmsToVelocity(base.vz)};
        const bool legacy = (base.flags & coopv2::kPlayerLegacy) != 0;
        if (legacy)
        {
            const double dx = pos[0] - m_options.legacyCenter[0];
            const double dy = pos[1] - m_options.legacyCenter[1];
            remote.legacyRadiusError.push_back(std::abs(std::sqrt(dx * dx + dy * dy) - m_options.legacyRadius));
        }
        const double localMs = LocalMs(aNowS);
        SnapshotState state;
        state.pitch = I16ToPitch(base.pitch);
        state.moveState = base.move_state;
        state.flags = base.flags;
        state.health = base.health;
        remote.buffer.Push(m_clockSync.UnwrapRelayMs(base.sample_time, localMs), m_clockSync.RelayMs(localMs), pos, vel,
                           U16ToYaw(base.yaw), (base.flags & coopv2::kPlayerTeleported) != 0, !legacy, state);
        remote.follower.OnSample(seq, pos);
    }

    void OnEntitySnapshot(const EntitySnapshotMsg& aSnapshot, double aNowS)
    {
        if (!m_clockSync.Synced())
        {
            ++m_unsyncedDropped;
            return;
        }
        const EntityView* decoded = m_decoder.Apply(aSnapshot);
        if (decoded == nullptr)
        {
            return;
        }
        const EntityView view = *decoded;
        m_joinerViewHashes[aSnapshot.tick] = ViewHash(view);
        const double localMs = LocalMs(aNowS);
        const double sampleTime = m_clockSync.UnwrapRelayMs(aSnapshot.sampleTime, localMs);
        const double arrival = m_clockSync.RelayMs(localMs);
        m_entityStream.Push(sampleTime, arrival, {0.0, 0.0, 0.0}, {0.0, 0.0, 0.0}, 0.0);
        for (const EntityRecord& record : aSnapshot.records)
        {
            if (record.remove)
            {
                continue;
            }
            if (!(record.spawn || record.pos || record.posDelta || record.vel || record.yaw || record.quat))
            {
                continue;
            }
            const EntityState* state = view.Find(record.netId);
            if (state == nullptr)
            {
                continue;
            }
            auto buffer = m_entityBuffers.find(record.netId);
            if (buffer == m_entityBuffers.end() || record.spawn)
            {
                m_entityBuffers.erase(record.netId);
                buffer = m_entityBuffers.emplace(record.netId, SnapshotBuffer(kEntityInterp)).first;
            }
            buffer->second.Push(sampleTime, arrival,
                                {MmToMeters(state->pos[0]), MmToMeters(state->pos[1]), MmToMeters(state->pos[2])},
                                {CmsToVelocity(state->vel[0]), CmsToVelocity(state->vel[1]), CmsToVelocity(state->vel[2])},
                                0.0);
        }
        if (aSnapshot.tick == m_decoder.Latest())
        {
            for (const auto& [netId, state] : m_entityView.Entries())
            {
                if (view.Find(netId) == nullptr)
                {
                    m_entityBuffers.erase(netId);
                    ++m_entityRemovals;
                }
            }
            for (const auto& [netId, state] : view.Entries())
            {
                if (m_entityView.Find(netId) == nullptr)
                {
                    ++m_entitySpawns;
                }
            }
            m_entityView = view;
        }
    }

    // ---- send -------------------------------------------------------------------------------------

    void PlayerTick(double aNowS)
    {
        if (!m_clockSync.Synced())
        {
            return; // sample_time would be meaningless before the relay clock is known
        }
        ++m_playerTicks;
        const double relayMs = RelayMs(aNowS);
        const demo::PlayerTruth truth = demo::PlayerTruthAt(m_role, relayMs / 1000.0, m_options.Path());
        uint16_t flags = static_cast<uint16_t>(coopv2::kPlayerWeaponDrawn | (4 << coopv2::kPlayerWeaponClassShift));
        uint8_t move = truth.move;
        if (move == U8(coopv2::MoveState::Sprint))
        {
            flags |= coopv2::kPlayerSprinting;
        }
        PlayerSnapshotMsg snapshot;
        if (truth.driving)
        {
            flags = static_cast<uint16_t>(coopv2::kPlayerInVehicle | coopv2::kPlayerDriving);
            move = U8(coopv2::MoveState::Vehicle);
            coopv2::VehicleBlock vehicle{};
            vehicle.vehicle_net = static_cast<uint16_t>(0x8000 + m_peerId);
            vehicle.px = static_cast<float>(truth.pos[0]);
            vehicle.py = static_cast<float>(truth.pos[1]);
            vehicle.pz = static_cast<float>(truth.pos[2]);
            vehicle.quat = PackQuat(truth.quat[0], truth.quat[1], truth.quat[2], truth.quat[3]);
            vehicle.lvx = VelocityToCms(truth.vel[0]);
            vehicle.lvy = VelocityToCms(truth.vel[1]);
            vehicle.lvz = VelocityToCms(truth.vel[2]);
            vehicle.avx = 0;
            vehicle.avy = 0;
            vehicle.avz = 126;
            vehicle.steer = 12;
            vehicle.throttle = 60;
            vehicle.brake = 0;
            vehicle.vflags = 1;
            snapshot.vehicle = vehicle;
        }
        m_snapSeq = static_cast<uint16_t>(m_snapSeq + 1);
        coopv2::PlayerSnapshot& base = snapshot.base;
        base.snap_seq = m_snapSeq;
        base.sample_time = static_cast<uint32_t>(static_cast<int64_t>(relayMs) & 0xFFFFFFFF);
        base.x = static_cast<float>(truth.pos[0]);
        base.y = static_cast<float>(truth.pos[1]);
        base.z = static_cast<float>(truth.pos[2]);
        base.yaw = YawToU16(truth.yaw);
        base.pitch = PitchToI16(-3.5);
        base.vx = VelocityToCms(truth.vel[0]);
        base.vy = VelocityToCms(truth.vel[1]);
        base.vz = VelocityToCms(truth.vel[2]);
        base.move_state = move;
        base.health = 230;
        base.flags = flags;
        QueueUnreliable(coopv2::kPeerBroadcast, Body{snapshot});
        ++m_snapshotsSent;
        if (IsHost() && m_epochMs && m_playerTicks % kEntityEveryNPlayerTicks == 0)
        {
            EntitySnapshotBody(relayMs);
        }
        if (!IsHost() && m_decoder.Latest() != 0)
        {
            QueueUnreliable(coopv2::kPeerBroadcast, Body{coopv2::SnapshotAck{m_decoder.Latest()}});
        }
        Flush(aNowS);
    }

    void EntitySnapshotBody(double aRelayMs)
    {
        const double t = (aRelayMs - static_cast<double>(*m_epochMs)) / 1000.0;
        const std::optional<uint8_t> other = OtherPeer();
        demo::Vec3 viewer = demo::kOrigin;
        if (other)
        {
            const auto remote = m_remote.find(*other);
            if (remote != m_remote.end() && remote->second.last)
            {
                const coopv2::PlayerSnapshot& last = remote->second.last->base;
                viewer = {last.x, last.y, last.z};
            }
        }
        std::map<uint16_t, demo::EntityTruth> truths;
        std::map<uint16_t, InterestManager::Candidate> candidates;
        for (const demo::EntitySpec& spec : m_world)
        {
            if (!demo::Alive(spec, t))
            {
                continue;
            }
            const demo::EntityTruth truth = demo::EntityTruthAt(spec, t);
            truths[spec.netId] = truth;
            candidates[spec.netId] = InterestManager::Candidate{spec.kind, truth.pos,
                                                                spec.kind == U8(coopv2::EntityKind::CombatNpc)};
        }
        const std::map<uint16_t, double> weights = m_interest.Update(viewer, candidates);
        EntityView states;
        for (const auto& [netId, weight] : weights)
        {
            const demo::EntitySpec& spec = *m_worldById.at(netId);
            const demo::EntityTruth& truth = truths.at(netId);
            const bool combat = spec.kind == U8(coopv2::EntityKind::CombatNpc);
            EntityInput input;
            input.kind = spec.kind;
            input.spawnFlags = spec.kind == U8(coopv2::EntityKind::CrowdNpc) ? 0x01
                               : spec.kind == U8(coopv2::EntityKind::Vehicle) ? 0x02
                                                                              : 0x00; // SpawnFlag CROWD / TRAFFIC
            input.attitude = combat ? 2 : 1;
            input.record = spec.Record();
            input.appearance = spec.Appearance();
            input.posMeters = truth.pos;
            input.yawDegrees = truth.yaw;
            input.quat = truth.quat;
            input.velMps = truth.vel;
            input.moveState = truth.move;
            input.flags = truth.flags;
            input.health = truth.health;
            input.target = combat ? static_cast<uint16_t>(coopv2::kPlayerTargetBase + other.value_or(0)) : 0;
            input.weapon = combat ? 0xA11CE : 0;
            states.Set(netId, Quantize(input));
        }
        EncodedSnapshot encoded;
        const uint32_t sampleTime = static_cast<uint32_t>(static_cast<int64_t>(aRelayMs) & 0xFFFFFFFF);
        if (m_encoder.Encode(sampleTime, states, weights, encoded) != Status::Ok)
        {
            ++m_encodeErrors;
            return;
        }
        m_hostViewHashes[encoded.tick] = ViewHash(encoded.view);
        m_entityBytes.push_back(static_cast<double>(encoded.body.size()));
        m_pending.push_back(PendingMessage{U8(coopv2::MsgType::EntitySnapshot), coopv2::kPeerBroadcast,
                                           std::move(encoded.body)});
    }

    // ---- events -----------------------------------------------------------------------------------

    void SendEvent(double aNowS, uint8_t aDest, const Body& aBody)
    {
        Bytes body;
        if (EncodeBody(aBody, body) != Status::Ok)
        {
            ++m_encodeErrors;
            m_fatal = "event did not encode";
            return;
        }
        const QueueResult result = m_connection->QueueReliable(TypeOf(aBody), aDest, AsSpan(body), aNowS);
        if (result != QueueResult::Queued)
        {
            m_fatal = std::string("reliable queue: ") + ToString(result);
            return;
        }
        m_sentEvents.push_back(SentEvent{TypeOf(aBody), aDest, Hex(AsSpan(body))});
    }

    void EventsTick(double aNowS)
    {
        const std::optional<uint8_t> other = OtherPeer(true);
        if (!m_readyAt)
        {
            if (other && m_clockSync.Synced())
            {
                m_readyAt = aNowS + 0.3;
            }
            return;
        }
        if (m_eventsStarted && !m_doneSent && aNowS > m_options.duration - kDrainS)
        {
            m_doneSent = true; // the last event of this stream
            SendEvent(aNowS, coopv2::kPeerBroadcast, Body{ChatMsg{0, m_options.Name() + std::string(kDoneSuffix)}});
        }
        if (aNowS < *m_readyAt || aNowS > m_options.duration - kDrainS || !other)
        {
            return;
        }
        const double relayMs = RelayMs(aNowS);
        if (!m_eventsStarted)
        {
            m_eventsStarted = true;
            m_nextEvent = aNowS;
            m_nextChat = aNowS + 0.5;
            if (IsHost())
            {
                m_epochMs = static_cast<int32_t>(relayMs);
                SendEvent(aNowS, coopv2::kPeerBroadcast, Body{coopv2::WorldFact{demo::kEpochFact, *m_epochMs}});
                coopv2::SessionConfig config{};
                config.npc_radius_m = 100;
                config.vehicle_radius_m = 200;
                config.entity_hz = 10;
                config.joiner_population = 0;
                config.flags = 0;
                SendEvent(aNowS, coopv2::kPeerBroadcast, Body{config});
            }
        }
        if (!m_burstDone && aNowS >= *m_readyAt + 2.0)
        {
            m_burstDone = true;
            for (int index = 0; index < kBurstSize; ++index)
            {
                SendEvent(aNowS, coopv2::kPeerBroadcast, Body{EquipValues()});
            }
        }
        if (!IsHost() && !m_teleportSent && aNowS >= *m_readyAt + 4.0)
        {
            m_teleportSent = true;
            SendEvent(aNowS, *other, Body{coopv2::TeleportReq{7, 0, 0}});
        }
        if (IsHost() && m_epochMs)
        {
            const double worldT = (relayMs - static_cast<double>(*m_epochMs)) / 1000.0;
            for (const demo::EntitySpec& spec : m_world)
            {
                if (spec.deathAt && worldT >= *spec.deathAt && !m_deathsSent.contains(spec.netId))
                {
                    m_deathsSent.insert(spec.netId);
                    coopv2::Death death{};
                    death.target_net = spec.netId;
                    death.target_kind = 0;
                    death.cause = 1;
                    death.killer_net = *other;
                    death.killer_kind = 1;
                    death.flags = 0;
                    death.time_ms = static_cast<uint32_t>(static_cast<int64_t>(relayMs) & 0xFFFFFFFF);
                    SendEvent(aNowS, coopv2::kPeerBroadcast, Body{death});
                }
            }
        }
        if (aNowS >= m_nextChat)
        {
            m_nextChat += kChatIntervalS;
            ++m_eventCounter;
            // "<name> chat #<n> privet" with "privet" in Cyrillic UTF-8, as client_v2.py.
            ChatMsg chat{0, m_options.Name() + " chat #" + std::to_string(m_eventCounter) +
                                " \xD0\xBF\xD1\x80\xD0\xB8\xD0\xB2\xD0\xB5\xD1\x82"};
            SendEvent(aNowS, coopv2::kPeerBroadcast, Body{chat});
        }
        if (aNowS >= m_nextEvent)
        {
            m_nextEvent += kEventIntervalS;
            ScriptedEvent(aNowS, *other, relayMs);
        }
    }

    coopv2::Equip EquipValues()
    {
        ++m_eventCounter;
        const uint32_t k = m_eventCounter;
        coopv2::Equip equip{};
        equip.slot = static_cast<uint8_t>(k % 3);
        equip.weapon_class = static_cast<uint8_t>(1 + k % 10);
        equip.flags = 0;
        equip.item_record = 0xA0000000ull + k;
        equip.appearance = k;
        equip.ammo = static_cast<uint16_t>(k & 0xFFFF);
        return equip;
    }

    void ScriptedEvent(double aNowS, uint8_t aOther, double aRelayMs)
    {
        const demo::PlayerTruth truth = demo::PlayerTruthAt(m_role, aRelayMs / 1000.0, m_options.Path());
        const auto x = static_cast<float>(truth.pos[0]);
        const auto y = static_cast<float>(truth.pos[1]);
        const auto z = static_cast<float>(truth.pos[2]);
        const uint32_t step = m_eventCounter % 6;
        if (step == 0)
        {
            SendEvent(aNowS, coopv2::kPeerBroadcast, Body{EquipValues()});
            return;
        }
        ++m_eventCounter;
        const uint32_t k = m_eventCounter;
        const uint8_t vehicleSeat = 0;
        const auto vehicleNet = static_cast<uint16_t>(0x8000 + m_peerId);
        if (step == 1 && IsHost())
        {
            coopv2::TimeWeather weather{};
            weather.game_seconds = 43200 + 60 * k;
            weather.time_scale_x100 = 100;
            weather.flags = 0;
            weather.weather_id = static_cast<uint8_t>(k % 8);
            weather.weather_record = 0xB000 + k % 8;
            weather.transition_s = 10;
            SendEvent(aNowS, coopv2::kPeerBroadcast, Body{weather});
        }
        else if (step == 1)
        {
            std::string text;
            for (const ModEntry& mod : DemoMods())
            {
                text += (text.empty() ? "" : ";") + mod.name + "@" + mod.version;
            }
            SendEvent(aNowS, coopv2::kPeerBroadcast, Body{ModListMsg{0, 1, text.substr(0, 250)}});
        }
        else if (step == 2)
        {
            coopv2::Hit hit{};
            if (IsHost())
            {
                hit.target_net = aOther;
                hit.target_kind = 1;
            }
            else
            {
                hit.target_net = static_cast<uint16_t>(1 + k % m_world.size());
                hit.target_kind = 0;
            }
            hit.hit_zone = static_cast<uint8_t>(k % 16);
            hit.attack = 1;
            hit.flags = 0;
            hit.damage = static_cast<float>(12.5 + k);
            hit.weapon_record = 0xA0000000ull + k;
            hit.rx = 0;
            hit.ry = 0;
            hit.rz = 120;
            hit.time_ms = static_cast<uint32_t>(static_cast<int64_t>(aRelayMs) & 0xFFFFFFFF);
            SendEvent(aNowS, coopv2::kPeerBroadcast, Body{hit});
        }
        else if (step == 3)
        {
            coopv2::VehicleEnter enter{};
            enter.vehicle_net = vehicleNet;
            enter.seat = vehicleSeat;
            enter.flags = 0;
            enter.record = 0xC0000000ull + k;
            enter.appearance = k;
            enter.x = x;
            enter.y = y;
            enter.z = z;
            enter.quat = PackQuat(truth.quat[0], truth.quat[1], truth.quat[2], truth.quat[3]);
            SendEvent(aNowS, coopv2::kPeerBroadcast, Body{enter});
        }
        else if (step == 4)
        {
            coopv2::VehicleExit exit{};
            exit.vehicle_net = vehicleNet;
            exit.seat = vehicleSeat;
            exit.flags = 0;
            exit.x = x;
            exit.y = y;
            exit.z = z;
            exit.yaw = YawToU16(truth.yaw);
            SendEvent(aNowS, coopv2::kPeerBroadcast, Body{exit});
        }
        else if (IsHost())
        {
            SendEvent(aNowS, coopv2::kPeerBroadcast, Body{coopv2::WorldFact{0xFAC70000ull + k % 64, static_cast<int32_t>(k)}});
        }
        else
        {
            SendEvent(aNowS, coopv2::kPeerBroadcast, Body{EquipValues()});
        }
    }

    // ---- render -----------------------------------------------------------------------------------

    void RenderFrame(double aNowS, double aDtS)
    {
        ++m_frameIndex;
        const double relayMs = RelayMs(aNowS);
        const double dtMs = std::max(1.0, aDtS * 1000.0);
        const std::optional<uint8_t> other = OtherPeer();
        const auto found = other ? m_remote.find(*other) : m_remote.end();
        if (found != m_remote.end() && !found->second.buffer.Empty() && m_eventsStarted)
        {
            RemotePlayer& remote = found->second;
            m_renderedPeer = *other;
            const double renderT = remote.buffer.RenderTime(relayMs, dtMs);
            const std::optional<SampledPose> pose = remote.buffer.SampleAt(renderT);
            const demo::PlayerTruth truth = demo::PlayerTruthAt(remote.role, renderT / 1000.0, m_options.Path());
            const double error = Distance(pose->pos, truth.pos);
            const double yawError = std::abs(PyFloatMod(pose->yaw - truth.yaw + 180.0, 360.0) - 180.0);
            m_playerErrors.push_back(error);
            m_yawErrors.push_back(yawError);
            MotionErrors& motion = m_motionErrors[truth.motion];
            motion.position.push_back(error);
            motion.yaw.push_back(yawError);
            m_renderDelays.push_back(relayMs - renderT);
            ++m_renderModes[ToString(pose->mode)];
            m_v2Track.push_back(TrackPoint{aNowS, pose->pos});
            if (const std::optional<demo::Vec3> follower = remote.follower.Frame(aDtS))
            {
                m_v1Track.push_back(V1Point{aNowS, *follower, relayMs});
            }
            m_truthTrack.push_back(TrackPoint{aNowS, truth.pos});
        }
        if (!IsHost() && m_epochMs && m_frameIndex % 3 == 0)
        {
            RenderEntities(relayMs, dtMs * 3.0);
        }
    }

    void RenderEntities(double aRelayMs, double aDtMs)
    {
        if (m_entityStream.Empty())
        {
            return;
        }
        const double renderT = m_entityStream.RenderTime(aRelayMs, aDtMs);
        const double worldT = (renderT - static_cast<double>(*m_epochMs)) / 1000.0;
        for (const auto& [netId, state] : m_entityView.Entries())
        {
            const auto buffer = m_entityBuffers.find(netId);
            const auto spec = m_worldById.find(netId);
            if (buffer == m_entityBuffers.end() || spec == m_worldById.end() || !demo::Alive(*spec->second, worldT))
            {
                continue;
            }
            const std::optional<SampledPose> sample = buffer->second.SampleAt(renderT);
            if (!sample)
            {
                continue;
            }
            const demo::EntityTruth truth = demo::EntityTruthAt(*spec->second, worldT);
            const double error = Distance(sample->pos, truth.pos);
            m_entityErrors.push_back(error);
            if (error > 0.25)
            {
                const double newest = buffer->second.NewestTime().value_or(renderT);
                m_entityOutliers.push_back(JsonValue::Array(
                    {JsonValue(static_cast<uint32_t>(netId)), JsonValue(static_cast<uint32_t>(spec->second->kind)),
                     JsonValue(std::round(worldT * 100.0) / 100.0), JsonValue(std::round(error * 1000.0) / 1000.0),
                     JsonValue(ToString(sample->mode)), JsonValue(static_cast<uint64_t>(buffer->second.Size())),
                     JsonValue(std::round((renderT - newest) * 10.0) / 10.0)}));
                m_entityOutlierErrors.push_back(error);
            }
        }
    }

    // ---- report -----------------------------------------------------------------------------------

    struct V1Point
    {
        double t = 0.0;
        demo::Vec3 pos{};
        double relayMs = 0.0;
    };

    // The constant delay that best aligns the v1 model with the truth, and its error.
    JsonValue V1BestDelay() const
    {
        JsonValue out = JsonValue::Object();
        if (m_v1Track.size() < 30 || !m_renderedPeer)
        {
            return out;
        }
        const auto remote = m_remote.find(*m_renderedPeer);
        if (remote == m_remote.end())
        {
            return out;
        }
        std::optional<std::pair<int, double>> best;
        std::vector<double> bestErrors;
        for (int delay = 0; delay < 505; delay += 5)
        {
            std::vector<double> errors;
            double squared = 0.0;
            for (size_t index = 0; index < m_v1Track.size(); index += 2)
            {
                const V1Point& point = m_v1Track[index];
                const demo::PlayerTruth truth =
                    demo::PlayerTruthAt(remote->second.role, (point.relayMs - delay) / 1000.0, m_options.Path());
                const double error = Distance(point.pos, truth.pos);
                errors.push_back(error);
                squared += error * error;
            }
            const double rms = std::sqrt(squared / static_cast<double>(errors.size()));
            if (!best || rms < best->second)
            {
                best = std::make_pair(delay, rms);
                bestErrors = std::move(errors);
            }
        }
        out.Set("best_delay_ms", best->first);
        out.Set("rms_error_m", best->second);
        out.Set("error", Summarize(bestErrors));
        return out;
    }

    JsonValue ArgsJson() const
    {
        JsonValue args = JsonValue::Object();
        args.Set("relay_host", m_options.relayHost);
        args.Set("relay_port", static_cast<uint32_t>(m_options.relayPort));
        args.Set("role", m_options.role);
        args.Set("room", m_options.room);
        args.Set("password", m_options.password);
        args.Set("name", m_options.Name());
        args.Set("join_flags", static_cast<uint32_t>(m_options.joinFlags));
        args.Set("duration", m_options.duration);
        args.Set("entity_budget", static_cast<uint64_t>(m_options.entityBudget));
        args.Set("world_seed", m_options.worldSeed);
        args.Set("seed", m_options.seed);
        args.Set("legacy_center", JsonValue::Array({m_options.legacyCenter[0], m_options.legacyCenter[1]}));
        args.Set("legacy_radius", m_options.legacyRadius);
        args.Set("up_latency_ms", m_options.up.latencyMs);
        args.Set("up_jitter_ms", m_options.up.jitterMs);
        args.Set("up_loss_pct", m_options.up.lossPct);
        args.Set("up_dup_pct", m_options.up.dupPct);
        args.Set("down_latency_ms", m_options.down.latencyMs);
        args.Set("down_jitter_ms", m_options.down.jitterMs);
        args.Set("down_loss_pct", m_options.down.lossPct);
        args.Set("down_dup_pct", m_options.down.dupPct);
        args.Set("report", m_options.report);
        args.Set("player_path", m_options.playerPath);
        return args;
    }

    JsonValue LinkJson() const
    {
        const LinkStats& stats = m_connection->Stats();
        JsonValue link = JsonValue::Object();
        link.Set("packets_sent", stats.packetsSent);
        link.Set("packets_received", stats.packetsReceived);
        link.Set("packets_acked", stats.packetsAcked);
        link.Set("packets_lost", stats.packetsLost);
        link.Set("duplicates", stats.duplicates);
        link.Set("too_old", stats.tooOld);
        link.Set("bytes_sent", stats.bytesSent);
        link.Set("bytes_received", stats.bytesReceived);
        link.Set("reliable_sent", stats.reliableSent);
        link.Set("reliable_resent", stats.reliableResent);
        link.Set("reliable_delivered", stats.reliableDelivered);
        link.Set("reliable_duplicates", stats.reliableDuplicates);
        link.Set("reliable_out_of_window", stats.reliableOutOfWindow);
        link.Set("recv_span", stats.recvSpan);
        link.Set("rtt_samples", stats.rttSamples);
        link.Set("rtt_skipped", stats.rttSkipped);
        link.Set("pending_reliable", static_cast<uint64_t>(m_connection->PendingReliable()));
        link.Set("loss_in", m_connection->LossIn());
        link.Set("loss_out", m_connection->LossOut());
        return link;
    }

    JsonValue BuildReport() const
    {
        JsonValue report = JsonValue::Object();
        report.Set("args", ArgsJson());
        report.Set("implementation", std::string("cpp ") + std::string(coopnet::kVersionString));
        report.Set("start_perf", m_clock.EpochSeconds());
        if (m_connection)
        {
            JsonValue welcome = JsonValue::Object();
            welcome.Set("minor", static_cast<uint32_t>(m_welcome.minor));
            welcome.Set("peer_id", static_cast<uint32_t>(m_welcome.peer_id));
            welcome.Set("role", static_cast<uint32_t>(m_welcome.role));
            welcome.Set("room_flags", static_cast<uint32_t>(m_welcome.room_flags));
            welcome.Set("token", m_welcome.token);
            welcome.Set("relay_time_ms", m_welcome.relay_time_ms);
            welcome.Set("player_hz", static_cast<uint32_t>(m_welcome.player_hz));
            welcome.Set("entity_hz", static_cast<uint32_t>(m_welcome.entity_hz));
            welcome.Set("max_packet", static_cast<uint32_t>(m_welcome.max_packet));
            welcome.Set("room_caps", m_welcome.room_caps);
            welcome.Set("handshake_ms", std::round(m_handshakeMs * 10.0) / 10.0);
            report.Set("welcome", welcome);
        }
        else
        {
            report.Set("welcome", nullptr);
        }
        report.Set("reject", m_reject ? *m_reject : JsonValue());
        report.Set("fatal", m_fatal);
        report.Set("peer_id", m_connection ? JsonValue(static_cast<uint32_t>(m_peerId)) : JsonValue());
        report.Set("role", m_connection ? JsonValue(RoleName(m_role)) : JsonValue());
        report.Set("disconnected", m_disconnected);
        JsonValue seen = JsonValue::Array();
        for (const uint8_t peer : m_everSeenPeers)
        {
            seen.Push(static_cast<uint32_t>(peer));
        }
        report.Set("peers_seen", seen);
        if (!m_connection)
        {
            return report;
        }

        JsonValue clock = JsonValue::Object();
        clock.Set("offset_ms", m_clockSync.OffsetMs());
        clock.Set("rtt_ms", m_clockSync.RttMs());
        clock.Set("estimator", "interval-intersection"); // src/v2/ClockSync (run_demo.py holds it to 5 ms)
        clock.Set("estimate_ms", m_clockSync.EstimateMs());
        clock.Set("bound_ms", m_clockSync.ErrorBoundMs());
        clock.Set("steps", m_clockSync.Steps());
        clock.Set("exchanges", static_cast<uint64_t>(m_clockSync.Samples()));
        JsonValue results = JsonValue::Object();
        for (const auto& [name, count] : m_clockResults)
        {
            results.Set(name, count);
        }
        clock.Set("results", results);
        // Every accepted exchange: [local ms, applied offset ms, lowest rtt ms] (client_v2.py keeps the last 5).
        JsonValue history = JsonValue::Array();
        for (const auto& sample : m_clockSamples)
        {
            history.Push(JsonValue::Array({sample[0], sample[1], sample[2]}));
        }
        clock.Set("history", history);
        report.Set("clock", clock);
        report.Set("unsynced_dropped", m_unsyncedDropped);
        report.Set("link", LinkJson());
        report.Set("rtt_to_relay_ms", m_connection->RttMs());
        report.Set("uplink", m_endpoint.uplink.Describe());
        report.Set("downlink", m_endpoint.downlink.Describe());
        JsonValue linkStats = JsonValue::Object();
        for (const auto& [peerId, stats] : m_linkStats)
        {
            JsonValue entry = JsonValue::Object();
            entry.Set("peer_id", static_cast<uint32_t>(stats.peer_id));
            entry.Set("rtt_ms", static_cast<uint32_t>(stats.rtt_ms));
            entry.Set("loss_in_permille", static_cast<uint32_t>(stats.loss_in_permille));
            entry.Set("loss_out_permille", static_cast<uint32_t>(stats.loss_out_permille));
            linkStats.Set(std::to_string(peerId), entry);
        }
        report.Set("link_stats_from_relay", linkStats);
        report.Set("invalid_messages", m_invalidMessages);
        report.Set("encode_errors", m_encodeErrors);
        JsonValue socketJson = JsonValue::Object();
        socketJson.Set("bytes_sent", m_endpoint.bytesSent);
        socketJson.Set("bytes_received", m_endpoint.bytesReceived);
        socketJson.Set("foreign", m_endpoint.foreign);
        socketJson.Set("send_errors", m_endpoint.sendErrors);
        report.Set("socket", socketJson);
        report.Set("snapshots_sent", m_snapshotsSent);

        JsonValue remotes = JsonValue::Object();
        for (const auto& [src, remote] : m_remote)
        {
            JsonValue entry = JsonValue::Object();
            entry.Set("received", remote.received);
            entry.Set("duplicates", remote.duplicates);
            entry.Set("reordered", remote.reordered);
            entry.Set("first_seq", remote.firstSeq);
            entry.Set("latest_seq", remote.latestSeq);
            entry.Set("seq_span", remote.firstSeq ? static_cast<uint32_t>(static_cast<uint16_t>(*remote.latestSeq - *remote.firstSeq)) + 1u
                                                  : 0u);
            entry.Set("flags_seen", remote.flagsSeen);
            entry.Set("driving_seen", remote.drivingSeen);
            const InterpCounts& counts = remote.buffer.Counts();
            JsonValue buffer = JsonValue::Object();
            buffer.Set("interpolated", counts.interpolated);
            buffer.Set("extrapolated", counts.extrapolated);
            buffer.Set("held", counts.held);
            buffer.Set("early", counts.early);
            buffer.Set("late", counts.late);
            buffer.Set("teleports", counts.teleports);
            entry.Set("buffer", buffer);
            entry.Set("legacy_radius_error", Summarize(remote.legacyRadiusError));
            remotes.Set(std::to_string(src), entry);
        }
        report.Set("remote_players", remotes);

        JsonValue sent = JsonValue::Array();
        for (const SentEvent& event : m_sentEvents)
        {
            sent.Push(JsonValue::Array({JsonValue(static_cast<uint32_t>(event.type)),
                                        JsonValue(static_cast<uint32_t>(event.dest)), JsonValue(event.hex)}));
        }
        report.Set("sent_events", sent);
        JsonValue received = JsonValue::Array();
        for (const ReceivedEvent& event : m_receivedEvents)
        {
            received.Push(JsonValue::Array({JsonValue(static_cast<uint32_t>(event.src)),
                                            JsonValue(static_cast<uint32_t>(event.type)), JsonValue(event.hex)}));
        }
        report.Set("received_events", received);
        report.Set("events_started", m_eventsStarted);
        JsonValue drain = JsonValue::Object();
        drain.Set("done_sent", m_doneSent);
        drain.Set("other_done", m_otherDone);
        drain.Set("linger_s", std::round(m_lingerS * 1000.0) / 1000.0);
        drain.Set("timed_out", m_lingerTimedOut);
        drain.Set("pending_reliable", static_cast<uint64_t>(m_connection->PendingReliable()));
        report.Set("drain", drain);

        JsonValue entity = JsonValue::Object();
        const DeltaEncoderStats& encoder = m_encoder.Stats();
        JsonValue encoderJson = JsonValue::Object();
        encoderJson.Set("snapshots", encoder.snapshots);
        encoderJson.Set("bytes", encoder.bytes);
        encoderJson.Set("full_bytes", encoder.fullBytes);
        encoderJson.Set("records", encoder.records);
        encoderJson.Set("deferred", encoder.deferred);
        encoderJson.Set("full_snapshots", encoder.fullSnapshots);
        encoderJson.Set("removals", encoder.removals);
        encoderJson.Set("spawns", encoder.spawns);
        entity.Set("encoder", encoderJson);
        entity.Set("bytes", Summarize(m_entityBytes));
        JsonValue hostHashes = JsonValue::Object();
        for (const auto& [tick, hash] : m_hostViewHashes)
        {
            hostHashes.Set(std::to_string(tick), hash);
        }
        entity.Set("host_view_hashes", hostHashes);
        const DeltaDecoderStats& decoder = m_decoder.Stats();
        JsonValue decoderJson = JsonValue::Object();
        decoderJson.Set("decoded", decoder.decoded);
        decoderJson.Set("duplicate", decoder.duplicate);
        decoderJson.Set("missing_baseline", decoder.missingBaseline);
        decoderJson.Set("inconsistent", decoder.inconsistent);
        decoderJson.Set("stale", decoder.stale);
        entity.Set("decoder", decoderJson);
        JsonValue joinerHashes = JsonValue::Object();
        for (const auto& [tick, hash] : m_joinerViewHashes)
        {
            joinerHashes.Set(std::to_string(tick), hash);
        }
        entity.Set("joiner_view_hashes", joinerHashes);
        entity.Set("spawns_seen", m_entitySpawns);
        entity.Set("removals_seen", m_entityRemovals);
        entity.Set("view_size_end", static_cast<uint64_t>(m_entityView.Size()));
        entity.Set("alignment_error_m", Summarize(m_entityErrors));
        entity.Set("outliers_over_25cm", static_cast<uint64_t>(m_entityOutliers.size()));
        std::vector<size_t> order(m_entityOutliers.size());
        for (size_t index = 0; index < order.size(); ++index)
        {
            order[index] = index;
        }
        std::stable_sort(order.begin(), order.end(),
                         [&](size_t aLeft, size_t aRight) { return m_entityOutlierErrors[aLeft] > m_entityOutlierErrors[aRight]; });
        JsonValue examples = JsonValue::Array();
        for (size_t index = 0; index < std::min<size_t>(12, order.size()); ++index)
        {
            examples.Push(m_entityOutliers[order[index]]);
        }
        entity.Set("outlier_examples", examples);
        entity.Set("stream_delay_target_ms", m_entityStream.TargetDelayMs());
        report.Set("entity", entity);

        std::vector<TrackPoint> v1Track;
        for (const V1Point& point : m_v1Track)
        {
            v1Track.push_back(TrackPoint{point.t, point.pos});
        }
        JsonValue render = JsonValue::Object();
        render.Set("player_error_m", Summarize(m_playerErrors));
        render.Set("yaw_error_deg", Summarize(m_yawErrors));
        JsonValue byMotion = JsonValue::Object();
        for (const auto& [motion, errors] : m_motionErrors)
        {
            JsonValue entry = JsonValue::Object();
            entry.Set("error_m", Summarize(errors.position));
            entry.Set("yaw_error_deg", Summarize(errors.yaw));
            byMotion.Set(motion, entry);
        }
        render.Set("by_motion", byMotion);
        render.Set("render_delay_ms", Summarize(m_renderDelays));
        JsonValue modes = JsonValue::Object();
        for (const auto& [mode, count] : m_renderModes)
        {
            modes.Set(mode, count);
        }
        render.Set("modes", modes);
        render.Set("accel_v2_mps2", Summarize(Accelerations(m_v2Track)));
        render.Set("accel_v1_model_mps2", Summarize(Accelerations(v1Track)));
        render.Set("accel_truth_mps2", Summarize(Accelerations(m_truthTrack)));
        render.Set("v1_model", V1BestDelay());
        report.Set("render", render);
        return report;
    }

    // ---- state ------------------------------------------------------------------------------------

    Options m_options;
    LocalClock m_clock;
    Sleeper m_sleeper;
    Endpoint m_endpoint;
    uint8_t m_roleWanted = 0;
    std::optional<Connection> m_connection;
    std::vector<Bytes> m_backlog;
    coopv2::Welcome m_welcome{};
    double m_handshakeMs = 0.0;
    std::optional<JsonValue> m_reject;
    std::optional<std::string> m_fatal;
    uint8_t m_peerId = 0;
    uint8_t m_role = 0;
    ClockSync m_clockSync;
    std::map<std::string, uint64_t> m_clockResults;
    std::vector<std::array<double, 3>> m_clockSamples;
    std::map<uint8_t, PeerJoinedMsg> m_peers;
    std::set<uint8_t> m_everSeenPeers;
    std::map<uint8_t, coopv2::LinkStats> m_linkStats;
    std::vector<PendingMessage> m_pending;
    std::optional<std::string> m_disconnected;
    std::vector<demo::EntitySpec> m_world;
    std::map<uint16_t, const demo::EntitySpec*> m_worldById;
    std::optional<int32_t> m_epochMs;
    std::optional<double> m_readyAt;
    bool m_eventsStarted = false;
    uint32_t m_eventCounter = 0;
    double m_nextEvent = 0.0;
    double m_nextChat = 0.0;
    bool m_burstDone = false;
    bool m_doneSent = false;
    bool m_otherDone = false;
    double m_lingerS = 0.0;
    bool m_lingerTimedOut = false;
    bool m_teleportSent = false;
    std::set<uint16_t> m_deathsSent;
    std::vector<SentEvent> m_sentEvents;
    std::vector<ReceivedEvent> m_receivedEvents;
    uint64_t m_invalidMessages = 0;
    uint64_t m_encodeErrors = 0;
    uint16_t m_snapSeq = 0;
    uint32_t m_playerTicks = 0;
    uint64_t m_snapshotsSent = 0;
    std::map<uint8_t, RemotePlayer> m_remote;
    DeltaEncoder m_encoder;
    InterestManager m_interest;
    std::map<uint32_t, std::string> m_hostViewHashes;
    std::vector<double> m_entityBytes;
    DeltaDecoder m_decoder;
    std::map<uint32_t, std::string> m_joinerViewHashes;
    EntityView m_entityView;
    std::map<uint16_t, SnapshotBuffer> m_entityBuffers;
    SnapshotBuffer m_entityStream;
    std::vector<double> m_entityErrors;
    std::vector<JsonValue> m_entityOutliers;
    std::vector<double> m_entityOutlierErrors;
    uint64_t m_entitySpawns = 0;
    uint64_t m_entityRemovals = 0;
    std::vector<double> m_playerErrors;
    std::vector<double> m_yawErrors;
    std::map<std::string, MotionErrors> m_motionErrors;
    std::vector<double> m_renderDelays;
    std::map<std::string, uint64_t> m_renderModes;
    std::vector<TrackPoint> m_v2Track;
    std::vector<V1Point> m_v1Track;
    std::vector<TrackPoint> m_truthTrack;
    uint64_t m_frameIndex = 0;
    std::optional<uint8_t> m_renderedPeer;
    uint64_t m_unsyncedDropped = 0;
};

// ---- --dump-world: the testworld cross-check ------------------------------------------------------

JsonValue Vec(const demo::Vec3& aValue)
{
    return JsonValue::Array({aValue[0], aValue[1], aValue[2]});
}

JsonValue DumpWorld(uint64_t aSeed)
{
    JsonValue out = JsonValue::Object();
    JsonValue entities = JsonValue::Array();
    const std::vector<demo::EntitySpec> world = demo::BuildEntities(aSeed);
    for (const demo::EntitySpec& spec : world)
    {
        entities.Push(JsonValue::Array(
            {JsonValue(static_cast<uint32_t>(spec.netId)), JsonValue(static_cast<uint32_t>(spec.kind)), Vec(spec.center),
             JsonValue(spec.radius), JsonValue(spec.speed), JsonValue(spec.phase), JsonValue(spec.spawnAt),
             JsonValue(spec.despawnAt), JsonValue(spec.deathAt), JsonValue(static_cast<uint32_t>(spec.flags))}));
    }
    out.Set("entities", entities);
    // Entity truth at a few world times: [net_id, t, alive, pos, vel, yaw, move, flags, health].
    JsonValue entityTruth = JsonValue::Array();
    for (const double t : {0.0, 2.5, 6.0, 9.75, 13.0, 31.3})
    {
        for (const demo::EntitySpec& spec : world)
        {
            const demo::EntityTruth truth = demo::EntityTruthAt(spec, t);
            entityTruth.Push(JsonValue::Array({JsonValue(static_cast<uint32_t>(spec.netId)), JsonValue(t),
                                               JsonValue(demo::Alive(spec, t)), Vec(truth.pos), Vec(truth.vel),
                                               JsonValue(truth.yaw), JsonValue(static_cast<uint32_t>(truth.move)),
                                               JsonValue(static_cast<uint32_t>(truth.flags)),
                                               JsonValue(static_cast<uint32_t>(truth.health))}));
        }
    }
    out.Set("entity_truth", entityTruth);
    // Player truth: [path, role, t, pos, vel, yaw, motion, move, driving] every 0.05 s over two course loops.
    JsonValue playerTruth = JsonValue::Array();
    for (const PlayerPath path : {PlayerPath::Demo, PlayerPath::Course})
    {
        for (const uint8_t role : {U8(coopv2::Role::Host), U8(coopv2::Role::Joiner)})
        {
            for (int step = 0; step <= 1600; ++step)
            {
                const double t = step * 0.05;
                const demo::PlayerTruth truth = demo::PlayerTruthAt(role, t, path);
                playerTruth.Push(JsonValue::Array(
                    {JsonValue(path == PlayerPath::Course ? "course" : "demo"), JsonValue(static_cast<uint32_t>(role)),
                     JsonValue(t), Vec(truth.pos), Vec(truth.vel), JsonValue(truth.yaw), JsonValue(truth.motion),
                     JsonValue(static_cast<uint32_t>(truth.move)), JsonValue(truth.driving)}));
            }
        }
    }
    out.Set("player_truth", playerTruth);
    out.Set("course_period_s", demo::CoursePeriodS());
    return out;
}

bool WriteText(const std::string& aPath, const std::string& aText)
{
    std::ofstream file(aPath, std::ios::binary);
    file << aText;
    return static_cast<bool>(file);
}
} // namespace

int main(int argc, char** argv)
{
    Options options;
    std::string error;
    if (!ParseOptions(argc, argv, options, error))
    {
        std::fprintf(stderr, "%s\nusage: coopnet_v2_demo_client --role host|joiner [client_v2.py arguments] | "
                             "--dump-world PATH [--world-seed N]\n",
                     error.c_str());
        return EXIT_FAILURE;
    }
    if (options.dumpWorld)
    {
        if (!WriteText(*options.dumpWorld, DumpWorld(options.worldSeed).Dump(0)))
        {
            std::fprintf(stderr, "cannot write %s\n", options.dumpWorld->c_str());
            return EXIT_FAILURE;
        }
        return EXIT_SUCCESS;
    }
    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0)
    {
        std::fprintf(stderr, "WSAStartup failed\n");
        return EXIT_FAILURE;
    }
    int code = 2;
    {
        DemoClient client(options);
        const JsonValue report = client.Run();
        if (options.report && !WriteText(*options.report, report.Dump(1)))
        {
            std::fprintf(stderr, "cannot write %s\n", options.report->c_str());
        }
        if (client.Welcomed())
        {
            std::printf("[%s] ok; events sent=%zu received=%zu\n", options.role.c_str(), client.EventsSent(),
                        client.EventsReceived());
            code = 0;
        }
        else
        {
            std::printf("[%s] rejected\n", options.role.c_str());
        }
    }
    WSACleanup();
    return code;
}
