// Integration test: two Transport instances (the code the plugin runs) talk protocol v2 through the
// relay's relay_v2.py over real UDP, usually with the relay's link simulation (latency, jitter, loss
// and duplicates on every relay send). tools/run_loopback.py starts the relay and this program.
//
//   coopnet_loopback.exe <host> <port> <reliableCount> <timeoutSeconds> [--restart]
//
// A joins as host, B as joiner, room "itest" with a key. Checked:
//   * invalid Connect arguments are refused; a wrong key and a second host are rejected by the relay
//   * both clients are welcomed, see each other and sync the relay clock
//   * invalid sends are refused (channel, size, UTF-8)
//   * reliable SCRIPT_MSG events arrive exactly once and in order, both ways (channels 16..20)
//   * unreliable messages (channel 1, 60 Hz) are never duplicated or reordered
//   * A's 30 Hz player snapshots (a 6 m/s circle) render on B through SampleRemote with a small
//     error against the truth at render time, 100-150 ms plus transit in the past
//   * a teleport never renders a point between the old and the new place
//   * B sees A leave; a send with no peer left is refused
// With --restart it prints RESTART_RELAY once both are in the room; the runner then restarts the
// relay on the same port and both clients must detect relay_lost, rejoin and exchange events again.
//
// Exit code 0 = every check passed.

#include "core/Transport.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <vector>

namespace
{
using namespace coopnet;
using Clock = std::chrono::steady_clock;

constexpr double kPi = 3.14159265358979323846;

std::mutex g_printMutex;
int g_failures = 0;

void Check(bool aOk, const char* aWhat)
{
    if (!aOk)
    {
        ++g_failures;
        std::printf("FAIL: %s\n", aWhat);
    }
}

LogSink MakeLogSink(const char* aName)
{
    return [aName](LogLevel aLevel, std::string_view aMessage)
    {
        static const char* const kLevels[] = {"info", "warn", "error"};
        std::lock_guard lock(g_printMutex);
        std::printf("[%s %s] %.*s\n", aName, kLevels[static_cast<int>(aLevel)], static_cast<int>(aMessage.size()),
                    aMessage.data());
    };
}

double Seconds(Clock::time_point aStart)
{
    return std::chrono::duration<double>(Clock::now() - aStart).count();
}

struct Received
{
    std::vector<std::string> events;
    std::vector<std::string> reliable;
    long lastSnapshot = -1;
    long snapshots = 0;
    bool snapshotOrderOk = true;
};

void Split(const std::string& aMessage, int& aSender, int& aChannel, std::string& aPayload)
{
    const size_t first = aMessage.find('|');
    const size_t second = aMessage.find('|', first + 1);
    aSender = std::atoi(aMessage.substr(0, first).c_str());
    aChannel = std::atoi(aMessage.substr(first + 1, second - first - 1).c_str());
    aPayload = aMessage.substr(second + 1);
}

void Drain(Transport& aTransport, Received& aReceived, const char* aName)
{
    std::string message;
    while (aTransport.Poll(message))
    {
        int sender = 0;
        int channel = 0;
        std::string payload;
        Split(message, sender, channel, payload);
        if (channel == 0)
        {
            aReceived.events.push_back(payload);
            std::lock_guard lock(g_printMutex);
            std::printf("[%s event] %s\n", aName, payload.c_str());
        }
        else if (channel >= kFirstReliableChannel)
        {
            aReceived.reliable.push_back(payload);
        }
        else
        {
            const long index = std::atol(payload.c_str() + 5); // "snap-<n>"
            if (index <= aReceived.lastSnapshot)
            {
                aReceived.snapshotOrderOk = false;
            }
            aReceived.lastSnapshot = index;
            ++aReceived.snapshots;
        }
    }
}

bool HasEvent(const Received& aReceived, const std::string& aPrefix)
{
    return std::any_of(aReceived.events.begin(), aReceived.events.end(),
                       [&](const std::string& aEvent) { return aEvent.rfind(aPrefix, 0) == 0; });
}

size_t CountEvents(const Received& aReceived, const std::string& aPrefix)
{
    return static_cast<size_t>(std::count_if(aReceived.events.begin(), aReceived.events.end(),
                                             [&](const std::string& aEvent) { return aEvent.rfind(aPrefix, 0) == 0; }));
}

bool CheckSequence(const std::vector<std::string>& aMessages, const std::string& aPrefix, int aCount)
{
    if (static_cast<int>(aMessages.size()) != aCount)
    {
        return false;
    }
    for (int index = 0; index < aCount; ++index)
    {
        if (aMessages[index] != aPrefix + std::to_string(index))
        {
            std::printf("  mismatch at %d: '%s'\n", index, aMessages[index].c_str());
            return false;
        }
    }
    return true;
}

double Percentile(std::vector<double> aValues, double aFraction)
{
    if (aValues.empty())
    {
        return 0.0;
    }
    std::sort(aValues.begin(), aValues.end());
    return aValues[static_cast<size_t>(aFraction * static_cast<double>(aValues.size() - 1))];
}

// A's true path: a 20 m circle at 0.3 rad/s (6 m/s), as a function of the relay clock. After the
// teleport time the circle is centred 500 m further east.
struct Path
{
    std::optional<double> teleportAtMs;

    [[nodiscard]] double CenterX(double aRelayMs) const
    {
        return teleportAtMs && aRelayMs >= *teleportAtMs ? -500.0 : -1000.0;
    }

    void At(double aRelayMs, PlayerState& aState) const
    {
        const double angle = 0.3 * aRelayMs / 1000.0;
        aState.x = static_cast<float>(CenterX(aRelayMs) + 20.0 * std::cos(angle));
        aState.y = static_cast<float>(500.0 + 20.0 * std::sin(angle));
        aState.z = 30.0f;
        aState.vx = static_cast<float>(-6.0 * std::sin(angle));
        aState.vy = static_cast<float>(6.0 * std::cos(angle));
        aState.vz = 0.0f;
        aState.yaw = static_cast<float>(std::fmod(angle * 180.0 / kPi + 90.0, 360.0));
        aState.pitch = -3.5f;
        aState.moveState = 2; // run
        aState.flags = 0x0002; // weapon drawn
        aState.health = 230;
    }

    // Distance from (x, y) to the circle that was active at aRelayMs, and to the other one.
    [[nodiscard]] double Error(double aRelayMs, double aX, double aY) const
    {
        PlayerState truth;
        At(aRelayMs, truth);
        return std::hypot(aX - truth.x, aY - truth.y);
    }

    [[nodiscard]] static double OffCircle(double aCenterX, double aX, double aY)
    {
        return std::fabs(std::hypot(aX - aCenterX, aY - 500.0) - 20.0);
    }
};

struct RenderStats
{
    std::vector<double> errors;
    std::vector<double> delays;
    std::map<v2::SampleMode, int> modes;
    int betweenTeleport = 0; // frames off both circles around the teleport
    int framesNearTeleport = 0;
};

class Bench
{
public:
    Bench(std::string aHost, int aPort)
        : m_host(std::move(aHost))
        , m_port(aPort)
    {
        m_alpha.SetLogSink(MakeLogSink("A"));
        m_bravo.SetLogSink(MakeLogSink("B"));
    }

    bool Join(double aTimeoutSeconds)
    {
        Check(m_alpha.Connect(Options(Role::Host, "pw")), "A Connect");
        Check(m_bravo.Connect(Options(Role::Joiner, "pw")), "B Connect");
        const auto start = Clock::now();
        while (Seconds(start) < aTimeoutSeconds &&
               (m_alpha.PeerCount() == 0 || m_bravo.PeerCount() == 0 || !m_alpha.ClockSynced() ||
                !m_bravo.ClockSynced()))
        {
            Pump(5);
        }
        const bool ok = m_alpha.PeerCount() > 0 && m_bravo.PeerCount() > 0 && m_alpha.ClockSynced() &&
                        m_bravo.ClockSynced();
        std::printf("joined after %.2fs: A id %d (%s), B id %d (%s), clocks synced %d/%d\n", Seconds(start),
                    m_alpha.LocalId(), HasEvent(m_alphaReceived, "welcome") ? "welcomed" : "-", m_bravo.LocalId(),
                    HasEvent(m_bravoReceived, "welcome") ? "welcomed" : "-", m_alpha.ClockSynced() ? 1 : 0,
                    m_bravo.ClockSynced() ? 1 : 0);
        if (!ok)
        {
            std::printf("A stats: %s\nB stats: %s\n", m_alpha.StatsJson().c_str(), m_bravo.StatsJson().c_str());
        }
        return ok;
    }

    void CheckRejections()
    {
        Transport wrongKey;
        Transport secondHost;
        Received wrongKeyEvents;
        Received secondHostEvents;
        Check(wrongKey.Connect(Options(Role::Joiner, "not-the-key")), "wrong-key Connect");
        Check(secondHost.Connect(Options(Role::Host, "pw")), "second-host Connect");
        const auto start = Clock::now();
        while (Seconds(start) < 5.0 && (!HasEvent(wrongKeyEvents, "disconnected") || !HasEvent(secondHostEvents, "disconnected")))
        {
            Drain(wrongKey, wrongKeyEvents, "wrong key");
            Drain(secondHost, secondHostEvents, "second host");
            Pump(5);
        }
        Check(HasEvent(wrongKeyEvents, "rejected bad_key"), "a wrong room key is rejected (bad_key)");
        Check(HasEvent(secondHostEvents, "rejected role_taken"), "a second host is rejected (role_taken)");
        Check(wrongKey.State() == ConnectionState::Error && secondHost.State() == ConnectionState::Error,
              "rejected transports end in the error state");
    }

    void CheckInvalidSends()
    {
        const bool refused = !m_alpha.Send(0, "x") && !m_alpha.Send(32, "x") &&
                             !m_alpha.Send(16, std::string(kMaxPayloadSize + 1, 'x')) && !m_alpha.Send(16, "\xc3\x28") &&
                             !m_alpha.Send(16, "x", 256);
        Check(refused, "invalid sends are refused (channel 0/32, 1001 bytes, invalid UTF-8, target 256)");
        Check(m_alpha.Send(16, std::string(kMaxPayloadSize, 'y'), m_bravo.LocalId()),
              "a 1000-byte reliable message to one peer is accepted");
        m_bigPending = true;
    }

    // Reliable both ways, unreliable A->B, player snapshots A->B rendered on B, then a teleport.
    void Transfer(int aCount, double aTimeoutSeconds)
    {
        const int backCount = aCount / 2;
        int alphaSent = 0;
        int bravoSent = 0;
        long snapshotIndex = 0;
        auto lastSnapshot = Clock::now();
        auto lastPlayer = Clock::now();
        const auto start = Clock::now();
        bool transferDone = false;
        std::optional<double> doneAt;
        while (Seconds(start) < aTimeoutSeconds)
        {
            for (int burst = 0; burst < 25 && alphaSent < aCount; ++burst)
            {
                if (!m_alpha.Send(16 + (alphaSent % 4), "rel-" + std::to_string(alphaSent)))
                {
                    break;
                }
                ++alphaSent;
            }
            for (int burst = 0; burst < 10 && bravoSent < backCount; ++burst)
            {
                if (!m_bravo.Send(20, "back-" + std::to_string(bravoSent)))
                {
                    break;
                }
                ++bravoSent;
            }
            if (Clock::now() - lastSnapshot >= std::chrono::milliseconds(16))
            {
                m_alpha.Send(1, "snap-" + std::to_string(snapshotIndex++));
                lastSnapshot = Clock::now();
            }
            if (Clock::now() - lastPlayer >= std::chrono::microseconds(33'333))
            {
                lastPlayer = Clock::now();
                PushAlpha(false);
            }
            Render();
            Pump(2);
            const bool bigArrived = !m_bigPending || std::any_of(m_bravoReceived.reliable.begin(), m_bravoReceived.reliable.end(),
                                                                 [](const std::string& aText) { return aText.size() == kMaxPayloadSize; });
            transferDone = static_cast<int>(CountRel(m_bravoReceived)) >= aCount &&
                           static_cast<int>(m_alphaReceived.reliable.size()) >= backCount && bigArrived;
            if (transferDone && !doneAt)
            {
                doneAt = Seconds(start);
            }
            // Keep the player moving for 3 s after the events, with a teleport in the middle.
            if (doneAt && !m_path.teleportAtMs && Seconds(start) > *doneAt + 1.5)
            {
                PushAlpha(true);
                lastPlayer = Clock::now();
            }
            if (doneAt && Seconds(start) > *doneAt + 3.0)
            {
                break;
            }
        }
        std::printf("transfer took %.2fs\n", doneAt.value_or(Seconds(start)));

        std::vector<std::string> forward;
        for (const std::string& text : m_bravoReceived.reliable)
        {
            if (text.size() != kMaxPayloadSize)
            {
                forward.push_back(text);
            }
        }
        const bool forwardOk = CheckSequence(forward, "rel-", aCount);
        const bool backOk = CheckSequence(m_alphaReceived.reliable, "back-", backCount);
        std::printf("reliable A->B: %zu/%d in order=%d | B->A: %zu/%d in order=%d | 1000-byte message arrived=%d\n",
                    forward.size(), aCount, forwardOk ? 1 : 0, m_alphaReceived.reliable.size(), backCount,
                    backOk ? 1 : 0, m_bravoReceived.reliable.size() > forward.size() ? 1 : 0);
        std::printf("unreliable A->B: sent %ld, received %ld (%.1f%%), never reordered/duplicated=%d\n", snapshotIndex,
                    m_bravoReceived.snapshots, 100.0 * static_cast<double>(m_bravoReceived.snapshots) / std::max(1L, snapshotIndex),
                    m_bravoReceived.snapshotOrderOk ? 1 : 0);
        Check(forwardOk, "reliable A->B exactly once and in order");
        Check(backOk, "reliable B->A exactly once and in order");
        Check(m_bravoReceived.reliable.size() == forward.size() + 1, "the 1000-byte message arrived once");
        Check(m_bravoReceived.snapshotOrderOk, "unreliable messages never reordered or duplicated");
        Check(m_bravoReceived.snapshots > snapshotIndex / 2, "most unreliable messages arrived");
    }

    void ReportRender(double aErrorLimit)
    {
        const RenderStats& stats = m_render;
        const int frames = static_cast<int>(stats.errors.size());
        const auto modeShare = [&](v2::SampleMode aMode) {
            const auto found = stats.modes.find(aMode);
            return frames == 0 || found == stats.modes.end() ? 0.0 : 100.0 * found->second / frames;
        };
        std::printf("render B<-A: %d frames, error p50 %.4f m p95 %.4f m max %.4f m, delay p50 %.1f ms p95 %.1f ms, "
                    "interpolated %.1f%% extrapolated %.1f%% held %.1f%% early %.1f%%\n",
                    frames, Percentile(stats.errors, 0.5), Percentile(stats.errors, 0.95), Percentile(stats.errors, 1.0),
                    Percentile(stats.delays, 0.5), Percentile(stats.delays, 0.95), modeShare(v2::SampleMode::Interpolated),
                    modeShare(v2::SampleMode::Extrapolated), modeShare(v2::SampleMode::Held), modeShare(v2::SampleMode::Early));
        std::printf("teleport: %d frames rendered within 0.5 s of it, %d of them between the two places\n",
                    stats.framesNearTeleport, stats.betweenTeleport);
        Check(frames > 100, "B rendered A for more than 100 frames");
        Check(Percentile(stats.errors, 0.95) < aErrorLimit, "render error p95 below the profile limit");
        Check(Percentile(stats.delays, 0.5) >= 99.0, "render delay at least the 100 ms minimum");
        Check(modeShare(v2::SampleMode::Interpolated) > 80.0, "more than 80% of frames interpolated");
        Check(stats.framesNearTeleport > 10 && stats.betweenTeleport == 0, "a teleport is never interpolated across");
        const std::string bravoStats = m_bravo.StatsJson();
        Check(bravoStats.find("\"teleports\":1") != std::string::npos, "B's buffer for A saw exactly one teleport");
    }

    void Idle(double aSeconds)
    {
        const auto start = Clock::now();
        while (Seconds(start) < aSeconds)
        {
            Pump(5);
        }
    }

    void PrintStats()
    {
        std::printf("A stats: %s\n", m_alpha.StatsJson().c_str());
        std::printf("B stats: %s\n", m_bravo.StatsJson().c_str());
    }

    // The relay restarts: both must report relay_lost, rejoin and carry events again.
    void Restart(double aTimeoutSeconds)
    {
        const size_t alphaWelcomes = CountEvents(m_alphaReceived, "welcome");
        const size_t bravoWelcomes = CountEvents(m_bravoReceived, "welcome");
        {
            std::lock_guard lock(g_printMutex);
            std::puts("RESTART_RELAY");
            std::fflush(stdout);
        }
        const auto start = Clock::now();
        while (Seconds(start) < aTimeoutSeconds &&
               (CountEvents(m_alphaReceived, "welcome") == alphaWelcomes ||
                CountEvents(m_bravoReceived, "welcome") == bravoWelcomes || m_alpha.PeerCount() == 0 ||
                m_bravo.PeerCount() == 0 || !m_alpha.ClockSynced() || !m_bravo.ClockSynced()))
        {
            Pump(5);
        }
        std::printf("rejoined after %.2fs: A id %d, B id %d\n", Seconds(start), m_alpha.LocalId(), m_bravo.LocalId());
        Check(HasEvent(m_alphaReceived, "relay_lost") && HasEvent(m_bravoReceived, "relay_lost"),
              "both clients report relay_lost");
        Check(CountEvents(m_alphaReceived, "welcome") > alphaWelcomes && CountEvents(m_bravoReceived, "welcome") > bravoWelcomes,
              "both clients are welcomed again");
        m_alphaReceived.reliable.clear();
        m_bravoReceived.reliable.clear();
        for (int index = 0; index < 20; ++index)
        {
            m_alpha.Send(16, "again-" + std::to_string(index));
            m_bravo.Send(16, "again-" + std::to_string(index));
        }
        const auto transfer = Clock::now();
        while (Seconds(transfer) < 10.0 && (m_alphaReceived.reliable.size() < 20 || m_bravoReceived.reliable.size() < 20))
        {
            Pump(5);
        }
        Check(CheckSequence(m_alphaReceived.reliable, "again-", 20) && CheckSequence(m_bravoReceived.reliable, "again-", 20),
              "after the restart 20 events each way arrive in order");
    }

    void Leave()
    {
        const int alphaId = m_alpha.LocalId();
        m_alpha.Disconnect();
        const auto start = Clock::now();
        const std::string expected = "peer_leave " + std::to_string(alphaId) + " quit";
        while (Seconds(start) < 5.0 && !HasEvent(m_bravoReceived, expected))
        {
            Pump(5);
        }
        std::printf("B saw A leave: %d (%.2fs)\n", HasEvent(m_bravoReceived, expected) ? 1 : 0, Seconds(start));
        Check(HasEvent(m_bravoReceived, expected), "B sees A leave with reason quit");
        Check(HasEvent(m_alphaReceived, "welcome") && HasEvent(m_bravoReceived, "peer_join"), "welcome and peer_join seen");
        Check(!m_bravo.Send(16, "nobody is listening"), "a send with no peer left is refused");
        m_bravo.Disconnect();
    }

private:
    ConnectOptions Options(Role aRole, const char* aKey) const
    {
        ConnectOptions options;
        options.host = m_host;
        options.port = m_port;
        options.room = "itest";
        options.key = aKey;
        options.role = static_cast<int>(aRole);
        return options;
    }

    void Pump(int aMillis)
    {
        Drain(m_alpha, m_alphaReceived, "A");
        Drain(m_bravo, m_bravoReceived, "B");
        std::this_thread::sleep_for(std::chrono::milliseconds(aMillis));
    }

    static size_t CountRel(const Received& aReceived)
    {
        return static_cast<size_t>(std::count_if(aReceived.reliable.begin(), aReceived.reliable.end(),
                                                 [](const std::string& aText) { return aText.rfind("rel-", 0) == 0; }));
    }

    void PushAlpha(bool aTeleport)
    {
        const std::optional<double> relayNow = m_alpha.RelayNowMs();
        if (!relayNow)
        {
            return;
        }
        if (aTeleport)
        {
            m_path.teleportAtMs = *relayNow;
        }
        PlayerState state;
        m_path.At(*relayNow, state);
        if (aTeleport)
        {
            state.flags |= 0x8000; // TELEPORTED
        }
        if (m_alpha.PushPlayer(state))
        {
            ++m_pushes;
        }
    }

    void Render()
    {
        if (Clock::now() - m_lastRender < std::chrono::microseconds(16'667))
        {
            return;
        }
        m_lastRender = Clock::now();
        RemotePose pose;
        if (!m_bravo.SampleRemote(m_alpha.LocalId(), pose))
        {
            return;
        }
        const std::optional<double> relayNow = m_bravo.RelayNowMs();
        if (!relayNow)
        {
            return;
        }
        const double renderMs = *relayNow - pose.delayMs;
        if (m_path.teleportAtMs && std::fabs(renderMs - *m_path.teleportAtMs) < 500.0)
        {
            ++m_render.framesNearTeleport;
            if (Path::OffCircle(-1000.0, pose.x, pose.y) > 1.0 && Path::OffCircle(-500.0, pose.x, pose.y) > 1.0)
            {
                ++m_render.betweenTeleport;
            }
            return; // the 100 ms "early" window right after a teleport shows the new place on purpose
        }
        m_render.errors.push_back(m_path.Error(renderMs, pose.x, pose.y));
        m_render.delays.push_back(pose.delayMs);
        ++m_render.modes[pose.mode];
    }

    std::string m_host;
    int m_port;
    Transport m_alpha;
    Transport m_bravo;
    Received m_alphaReceived;
    Received m_bravoReceived;
    Path m_path;
    RenderStats m_render;
    Clock::time_point m_lastRender{};
    int m_pushes = 0;
    bool m_bigPending = false;
};
} // namespace

int main(int argc, char** argv)
{
    const std::string host = argc > 1 ? argv[1] : "127.0.0.1";
    const int port = argc > 2 ? std::atoi(argv[2]) : kDefaultRelayPort;
    const int reliableCount = argc > 3 ? std::atoi(argv[3]) : 400;
    const int timeoutSeconds = argc > 4 ? std::atoi(argv[4]) : 90;
    const bool restart = argc > 5 && std::strcmp(argv[5], "--restart") == 0;
    const double errorLimit = argc > 6 ? std::atof(argv[6]) : 0.05;

    {
        Transport probe;
        Check(!probe.Connect(host, 0, "itest") && !probe.Connect("", port, "itest") &&
                  !probe.Connect(host, port, "bad room"),
              "invalid Connect arguments are refused");
    }

    Bench bench(host, port);
    if (!bench.Join(15.0))
    {
        std::puts("FAIL: the clients never met in the room");
        std::puts("LOOPBACK FAIL");
        return EXIT_FAILURE;
    }
    bench.CheckRejections();
    bench.CheckInvalidSends();
    if (restart)
    {
        bench.Restart(30.0);
    }
    else
    {
        bench.Transfer(reliableCount, timeoutSeconds);
        bench.ReportRender(errorLimit);
        bench.Idle(2.0);
    }
    bench.PrintStats();
    bench.Leave();

    std::puts(g_failures == 0 ? "LOOPBACK PASS" : "LOOPBACK FAIL");
    return g_failures == 0 ? EXIT_SUCCESS : EXIT_FAILURE;
}
