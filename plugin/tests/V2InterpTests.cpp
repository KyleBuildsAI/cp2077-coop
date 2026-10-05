// SnapshotBuffer (src/v2/SnapshotBuffer) tests.
//
//   coopnet_v2_interp_tests                 unit tests: relay/tests/test_interp.py's InterpTests
//                                           ported, plus the C++ additions and simulated links
//   coopnet_v2_interp_tests trace <file>    replays a trace of interp.py's InterpBuffer written by
//                                           tools/v2_interp_trace.py; every result must match to the
//                                           last bit

#include "v2/SnapshotBuffer.hpp"

#include "V2TestSupport.hpp"

#include <algorithm>
#include <bit>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <map>
#include <optional>
#include <sstream>
#include <string>
#include <vector>

using namespace coopnet::v2;

namespace
{
int g_checks = 0;
int g_failures = 0;

#define CHECK(condition)                                                                                               \
    do                                                                                                                 \
    {                                                                                                                  \
        ++g_checks;                                                                                                    \
        if (!(condition))                                                                                              \
        {                                                                                                              \
            ++g_failures;                                                                                              \
            std::printf("  FAILED %s:%d: %s\n", __FILE__, __LINE__, #condition);                                     \
        }                                                                                                              \
    } while (false)

double Distance(const Vec3& aA, const Vec3& aB)
{
    const double dx = aA[0] - aB[0];
    const double dy = aA[1] - aB[1];
    const double dz = aA[2] - aB[2];
    return std::sqrt(dx * dx + dy * dy + dz * dz);
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

// ---- relay/tests/test_interp.py InterpTests --------------------------------------------------------

struct Arrival
{
    double arrival;
    double t;
};

std::vector<Arrival> Feed(test::Rng& aRng, double aSeconds, double aIntervalMs, double aTransitMs, double aJitterMs,
                          double aLoss)
{
    std::vector<Arrival> arrivals;
    for (double t = 0.0; t < aSeconds * 1000.0; t += aIntervalMs)
    {
        if (aRng.Unit() >= aLoss)
        {
            arrivals.push_back({t + aTransitMs + aRng.Unit() * aJitterMs, t});
        }
    }
    std::sort(arrivals.begin(), arrivals.end(), [](const Arrival& aA, const Arrival& aB) { return aA.arrival < aB.arrival; });
    return arrivals;
}

struct CircleTruth
{
    double radius = 10.0;
    double omega = 0.6;
    Vec3 Pos(double aMs) const
    {
        const double angle = omega * aMs / 1000.0;
        return {radius * std::cos(angle), radius * std::sin(angle), 0.0};
    }
    Vec3 Vel(double aMs) const
    {
        const double angle = omega * aMs / 1000.0;
        return {-radius * omega * std::sin(angle), radius * omega * std::cos(angle), 0.0};
    }
};

struct LinkResult
{
    std::vector<double> errors;
    std::vector<double> delays;
    std::map<SampleMode, int> modes;
    double targetDelay = 0.0;
};

// Renders a circling object at 60 Hz through a simulated link (relay timeline = local timeline).
LinkResult RunCircle(uint64_t aSeed, double aTransitMs, double aJitterMs, double aLoss, double aOmega = 0.6)
{
    test::Rng rng(aSeed);
    SnapshotBuffer buffer(kPlayerInterp);
    const std::vector<Arrival> arrivals = Feed(rng, 10.0, 1000.0 / 30.0, aTransitMs, aJitterMs, aLoss);
    CircleTruth truth;
    truth.omega = aOmega;
    LinkResult result;
    size_t index = 0;
    for (double now = 0.0; now < 10000.0; now += 16.7)
    {
        while (index < arrivals.size() && arrivals[index].arrival <= now)
        {
            const double t = arrivals[index].t;
            buffer.Push(t, arrivals[index].arrival, truth.Pos(t), truth.Vel(t), 0.0);
            ++index;
        }
        if (!buffer.Empty() && now > 1000.0)
        {
            const double renderT = buffer.RenderTime(now, 16.7);
            const auto pose = buffer.SampleAt(renderT);
            result.errors.push_back(Distance(pose->pos, truth.Pos(renderT)));
            result.delays.push_back(now - renderT);
            ++result.modes[pose->mode];
        }
    }
    result.targetDelay = buffer.TargetDelayMs();
    return result;
}

void TestInterpolationErrorSmallWithJitterAndLoss()
{
    std::puts("interpolation error with jitter and loss (test_interp.py)");
    const LinkResult result = RunCircle(2, 120.0, 40.0, 0.1);
    const double p95 = Percentile(result.errors, 0.95);
    std::printf("    120 ms + 40 ms jitter, 10%% loss: p95 error %.6f m, target delay %.1f ms, render delay p50 %.1f ms\n",
                p95, result.targetDelay, Percentile(result.delays, 0.5));
    CHECK(p95 < 0.01);
    CHECK(result.targetDelay >= 100.0 && result.targetDelay <= 150.0);
}

void TestExtrapolationIsBounded()
{
    std::puts("extrapolation is bounded (test_interp.py)");
    SnapshotBuffer buffer(kPlayerInterp);
    buffer.Push(0.0, 50.0, {0.0, 0.0, 0.0}, {10.0, 0.0, 0.0}, 0.0);
    buffer.Push(100.0, 150.0, {1.0, 0.0, 0.0}, {10.0, 0.0, 0.0}, 0.0);
    auto pose = buffer.SampleAt(200.0);
    CHECK(pose && pose->mode == SampleMode::Extrapolated);
    CHECK(pose && std::fabs(pose->pos[0] - 2.0) < 1e-9);
    pose = buffer.SampleAt(1000.0);
    CHECK(pose && pose->mode == SampleMode::Held);
    CHECK(pose && std::fabs(pose->pos[0] - (1.0 + 10.0 * 0.25)) < 1e-9);
}

void TestTeleportClearsHistoryAndLateSamplesDropped()
{
    std::puts("teleport clears the history, late samples are dropped (test_interp.py)");
    SnapshotBuffer buffer(kPlayerInterp);
    for (int t = 0; t < 300; t += 33)
    {
        buffer.Push(t, t + 50.0, {t / 100.0, 0.0, 0.0}, {0.0, 0.0, 0.0}, 0.0);
    }
    CHECK(buffer.Push(300.0, 350.0, {500.0, 500.0, 0.0}, {0.0, 0.0, 0.0}, 0.0, true) == PushResult::Added);
    CHECK(buffer.Size() == 1);
    CHECK(buffer.Push(10.0, 360.0, {0.0, 0.0, 0.0}, {0.0, 0.0, 0.0}, 0.0) == PushResult::Late);
    CHECK(buffer.Counts().late == 1);
    CHECK(buffer.Counts().teleports == 1);
}

void TestPlayoutSnapsOnLargeDiscontinuity()
{
    std::puts("playout snaps on a large discontinuity (test_interp.py)");
    SnapshotBuffer buffer(kPlayerInterp);
    for (int t = 0; t < 1000; t += 33)
    {
        buffer.Push(t, t + 60.0, {0.0, 0.0, 0.0}, {0.0, 0.0, 0.0}, 0.0);
    }
    const double first = buffer.RenderTime(1100.0, 16.7);
    buffer.ResetTiming();
    for (int t = 1000; t < 2000; t += 33)
    {
        buffer.Push(t, t + 900.0, {0.0, 0.0, 0.0}, {0.0, 0.0, 0.0}, 0.0);
    }
    const double second = buffer.RenderTime(2900.0, 16.7);
    CHECK(std::fabs((1100.0 - first) - 160.0) < 1.0);
    CHECK(std::fabs((2900.0 - second) - 1000.0) < 1.0);
}

// ---- C++ additions ---------------------------------------------------------------------------------

void TestPythonModuloAndAngles()
{
    std::puts("Python float modulo and lerp_angle");
    CHECK(PyFloatMod(-1.0, 360.0) == 359.0);
    CHECK(PyFloatMod(370.0, 360.0) == 10.0);
    CHECK(PyFloatMod(720.0, 360.0) == 0.0 && !std::signbit(PyFloatMod(720.0, 360.0)));
    CHECK(PyFloatMod(-720.0, 360.0) == 0.0 && !std::signbit(PyFloatMod(-720.0, 360.0)));
    CHECK(PyFloatMod(10.0, -360.0) == -350.0);
    CHECK(LerpAngle(350.0, 10.0, 0.5) == 0.0);
    CHECK(std::fabs(LerpAngle(10.0, 350.0, 0.25) - 5.0) < 1e-12);
    CHECK(std::fabs(LerpAngle(0.0, 180.0, 0.5) - 270.0) < 1e-12); // ties go the negative way round, as in Python
    CHECK(LerpAngle(90.0, 90.0, 0.7) == 90.0);
}

void TestStateVelocityAndPitch()
{
    std::puts("pitch, velocity and player state carried through the buffer");
    SnapshotBuffer buffer(kPlayerInterp);
    SnapshotState walking{-10.0, 1, 0x0002, 200};
    SnapshotState running{20.0, 2, 0x0006, 180};
    buffer.Push(1000.0, 1050.0, {0.0, 0.0, 0.0}, {2.0, 0.0, 0.0}, 350.0, false, true, walking);
    buffer.Push(1100.0, 1150.0, {0.2, 0.0, 0.0}, {4.0, 0.0, 0.0}, 10.0, false, true, running);
    auto pose = buffer.SampleAt(1025.0);
    CHECK(pose && pose->mode == SampleMode::Interpolated);
    CHECK(pose && std::fabs(pose->pitch - (-2.5)) < 1e-12);
    CHECK(pose && pose->state == walking); // the sample at or before the render time
    CHECK(pose && std::fabs(pose->vel[0] - 2.5) < 1e-12);
    CHECK(pose && std::fabs(pose->yaw - 355.0) < 1e-9);
    pose = buffer.SampleAt(1100.0); // equal to the newest: extrapolated by 0 ms
    CHECK(pose && pose->mode == SampleMode::Extrapolated && pose->state == running && pose->pitch == 20.0);
    pose = buffer.SampleAt(900.0);
    CHECK(pose && pose->mode == SampleMode::Early && pose->state == walking && pose->vel[0] == 2.0);
    CHECK(pose && std::fabs(pose->pos[0] - (-0.2)) < 1e-12);

    // Samples without velocity (bridged v1 players): straight lines, reported velocity zero.
    SnapshotBuffer legacy(kPlayerInterp);
    legacy.Push(0.0, 40.0, {0.0, 0.0, 0.0}, {9.0, 9.0, 9.0}, 0.0, false, false);
    legacy.Push(100.0, 140.0, {1.0, 2.0, 3.0}, {9.0, 9.0, 9.0}, 0.0, false, false);
    pose = legacy.SampleAt(50.0);
    CHECK(pose && pose->pos == (Vec3{0.5, 1.0, 1.5}) && pose->vel == (Vec3{0.0, 0.0, 0.0}));
    pose = legacy.SampleAt(150.0);
    CHECK(pose && pose->pos == (Vec3{1.0, 2.0, 3.0}) && pose->mode == SampleMode::Extrapolated);
    CHECK(!SnapshotBuffer().SampleAt(0.0).has_value());
}

void TestHistoryAndTransitWindow()
{
    std::puts("history pruning and the transit window");
    InterpConfig config = kPlayerInterp;
    config.transitWindow = 4;
    SnapshotBuffer buffer(config);
    for (int index = 0; index < 400; ++index)
    {
        const double t = index * 1000.0 / 30.0;
        buffer.Push(t, t + 80.0 + (index % 4) * 10.0, {0.0, 0.0, 0.0}, {0.0, 0.0, 0.0}, 0.0);
    }
    CHECK(buffer.TransitCount() == 4);
    CHECK(buffer.NewestTime().value() - buffer.OldestTime().value() <= config.historyMs);
    CHECK(buffer.Size() >= 59 && buffer.Size() <= 61);
    std::printf("    400 samples at 30 Hz: %zu kept (history %.0f ms), transit window %zu\n", buffer.Size(),
                config.historyMs, buffer.TransitCount());
}

void TestSimulatedLinks()
{
    std::puts("simulated links (circle, 6 m/s and a sprint-like 9 m/s turn), 60 Hz render");
    struct Profile
    {
        const char* name;
        double transit;
        double jitter;
        double loss;
        double omega;
        double limit;
    };
    const Profile profiles[] = {
        {"bench 115 ms + 20 ms jitter, 1% loss", 115.0, 20.0, 0.01, 0.6, 0.01},
        {"ru-la 160 ms + 30 ms jitter, 2% loss", 160.0, 30.0, 0.02, 0.6, 0.01},
        {"lossy 60 ms + 40 ms jitter, 10% loss", 60.0, 40.0, 0.10, 0.6, 0.01},
        {"sprint turn 115 ms + 20 ms jitter, 1% loss", 115.0, 20.0, 0.01, 0.9, 0.02},
        {"jitter above the delay cap: 150 ms jitter", 100.0, 150.0, 0.02, 0.6, 1.5},
    };
    for (const Profile& profile : profiles)
    {
        const LinkResult result = RunCircle(7, profile.transit, profile.jitter, profile.loss, profile.omega);
        const double p95 = Percentile(result.errors, 0.95);
        const int total = static_cast<int>(result.errors.size());
        const int interpolated = result.modes.contains(SampleMode::Interpolated) ? result.modes.at(SampleMode::Interpolated) : 0;
        std::printf("    %-45s p95 %.6f m, max %.6f m, delay p50 %.1f ms, interpolated %.1f%%\n", profile.name, p95,
                    Percentile(result.errors, 1.0), Percentile(result.delays, 0.5), 100.0 * interpolated / total);
        CHECK(p95 < profile.limit);
    }
}

void TestCost()
{
    std::puts("cost of one RenderTime + SampleAt (a full 90-entry transit window)");
    SnapshotBuffer buffer(kPlayerInterp);
    test::Rng rng(3);
    for (int index = 0; index < 120; ++index)
    {
        const double t = index * 1000.0 / 30.0;
        buffer.Push(t, t + 100.0 + rng.Unit() * 30.0, {t, 0.0, 0.0}, {1.0, 0.0, 0.0}, 0.0);
    }
    constexpr int kCalls = 100'000;
    double sink = 0.0;
    const auto start = std::chrono::steady_clock::now();
    for (int call = 0; call < kCalls; ++call)
    {
        const double now = 4000.0 + (call % 100) * 0.1;
        sink += buffer.SampleAt(buffer.RenderTime(now, 16.7))->pos[0];
    }
    const double ns =
        static_cast<double>(std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now() - start).count()) /
        kCalls;
    std::printf("    %.0f ns per frame and remote player (checksum %.1f)\n", ns, sink);
    CHECK(ns < 20'000.0);
}

// ---- trace replay ----------------------------------------------------------------------------------

class TraceReplay
{
public:
    int Run(const std::string& aPath)
    {
        std::ifstream file(aPath);
        if (!file)
        {
            std::printf("cannot open %s\n", aPath.c_str());
            return EXIT_FAILURE;
        }
        std::string line;
        while (std::getline(file, line))
        {
            ++m_lineNumber;
            if (!line.empty() && line.back() == '\r')
            {
                line.pop_back();
            }
            if (!line.empty())
            {
                Apply(line);
            }
        }
        std::printf("interp trace: %d scenarios, %d pushes, %d renders, %d samples, %d delays, %d resets, "
                    "%d counter sets: %d values compared, %d mismatches\n",
                    m_scenarios, m_pushes, m_renders, m_samples, m_delays, m_resets, m_countSets, m_compared,
                    m_mismatches);
        const bool ok = m_mismatches == 0 && m_scenarios > 0 && !m_open;
        std::puts(ok ? "INTERP TRACE PASS" : "INTERP TRACE FAIL");
        return ok ? EXIT_SUCCESS : EXIT_FAILURE;
    }

private:
    static double Float(const std::string& aToken)
    {
        return std::strtod(aToken.c_str(), nullptr);
    }

    static std::optional<double> OptionalFloat(const std::string& aToken)
    {
        return aToken == "-" ? std::nullopt : std::optional<double>(Float(aToken));
    }

    void Mismatch(const std::string& aWhat, const std::string& aExpected, const std::string& aActual)
    {
        ++m_mismatches;
        if (m_mismatches <= 20)
        {
            std::printf("  MISMATCH %s line %d: %s expected %s, got %s\n", m_scenario.c_str(), m_lineNumber,
                        aWhat.c_str(), aExpected.c_str(), aActual.c_str());
        }
    }

    static std::string Show(double aValue)
    {
        char text[64];
        std::snprintf(text, sizeof(text), "%a (%.17g)", aValue, aValue);
        return text;
    }

    void Same(const std::string& aWhat, double aExpected, double aActual)
    {
        ++m_compared;
        if (std::bit_cast<uint64_t>(aExpected) != std::bit_cast<uint64_t>(aActual))
        {
            Mismatch(aWhat, Show(aExpected), Show(aActual));
        }
    }

    void Same(const std::string& aWhat, std::optional<double> aExpected, std::optional<double> aActual)
    {
        if (aExpected.has_value() != aActual.has_value())
        {
            ++m_compared;
            Mismatch(aWhat, aExpected ? Show(*aExpected) : "-", aActual ? Show(*aActual) : "-");
            return;
        }
        if (aExpected)
        {
            Same(aWhat, *aExpected, *aActual);
        }
    }

    void Same(const std::string& aWhat, uint64_t aExpected, uint64_t aActual)
    {
        ++m_compared;
        if (aExpected != aActual)
        {
            Mismatch(aWhat, std::to_string(aExpected), std::to_string(aActual));
        }
    }

    void Same(const std::string& aWhat, const std::string& aExpected, const std::string& aActual)
    {
        ++m_compared;
        if (aExpected != aActual)
        {
            Mismatch(aWhat, aExpected, aActual);
        }
    }

    void Apply(const std::string& aLine)
    {
        std::istringstream stream(aLine);
        std::vector<std::string> tokens;
        for (std::string token; stream >> token;)
        {
            tokens.push_back(token);
        }
        const std::string& op = tokens[0];
        const auto need = [&](size_t aCount) {
            if (tokens.size() != aCount)
            {
                Mismatch("record " + op, std::to_string(aCount) + " fields", std::to_string(tokens.size()));
                return false;
            }
            return m_buffer.has_value() || op == "SCENARIO" || op == "CONFIG";
        };
        if (op == "SCENARIO" && need(2))
        {
            m_scenario = tokens[1];
            m_open = true;
            ++m_scenarios;
        }
        else if (op == "CONFIG" && need(7))
        {
            InterpConfig config;
            config.sendIntervalMs = Float(tokens[1]);
            config.minDelayMs = Float(tokens[2]);
            config.maxDelayMs = Float(tokens[3]);
            config.maxExtrapolateMs = Float(tokens[4]);
            config.historyMs = Float(tokens[5]);
            config.transitWindow = static_cast<size_t>(std::stoul(tokens[6]));
            m_buffer.emplace(config);
        }
        else if (op == "PUSH" && need(18))
        {
            ++m_pushes;
            SnapshotBuffer& buffer = *m_buffer;
            const Vec3 pos{Float(tokens[3]), Float(tokens[4]), Float(tokens[5])};
            const Vec3 vel{Float(tokens[6]), Float(tokens[7]), Float(tokens[8])};
            buffer.Push(Float(tokens[1]), Float(tokens[2]), pos, vel, Float(tokens[9]), tokens[10] == "1",
                        tokens[11] == "1");
            Same("push size", std::stoull(tokens[12]), buffer.Size());
            Same("push late", std::stoull(tokens[13]), buffer.Counts().late);
            Same("push teleports", std::stoull(tokens[14]), buffer.Counts().teleports);
            Same("push transit", std::stoull(tokens[15]), buffer.TransitCount());
            Same("push oldest", OptionalFloat(tokens[16]), buffer.OldestTime());
            Same("push newest", OptionalFloat(tokens[17]), buffer.NewestTime());
        }
        else if (op == "RESET" && need(1))
        {
            ++m_resets;
            m_buffer->ResetTiming();
        }
        else if (op == "DELAY" && need(2))
        {
            ++m_delays;
            Same("target delay", Float(tokens[1]), m_buffer->TargetDelayMs());
        }
        else if (op == "RENDER" && need(5))
        {
            ++m_renders;
            const double renderT = m_buffer->RenderTime(Float(tokens[1]), Float(tokens[2]));
            Same("render time", Float(tokens[3]), renderT);
            Same("playout", OptionalFloat(tokens[4]), m_buffer->PlayoutMs());
        }
        else if (op == "SAMPLE" && (tokens.size() == 3 || tokens.size() == 7) && m_buffer)
        {
            ++m_samples;
            const auto pose = m_buffer->SampleAt(Float(tokens[1]));
            if (tokens[2] == "none")
            {
                Same("sample mode", std::string("none"), pose ? std::string(ToString(pose->mode)) : std::string("none"));
            }
            else
            {
                Same("sample mode", tokens[2], pose ? std::string(ToString(pose->mode)) : std::string("none"));
                if (pose)
                {
                    Same("sample x", Float(tokens[3]), pose->pos[0]);
                    Same("sample y", Float(tokens[4]), pose->pos[1]);
                    Same("sample z", Float(tokens[5]), pose->pos[2]);
                    Same("sample yaw", Float(tokens[6]), pose->yaw);
                }
            }
        }
        else if (op == "COUNTS" && need(7))
        {
            ++m_countSets;
            const InterpCounts& counts = m_buffer->Counts();
            Same("interpolated", std::stoull(tokens[1]), counts.interpolated);
            Same("extrapolated", std::stoull(tokens[2]), counts.extrapolated);
            Same("held", std::stoull(tokens[3]), counts.held);
            Same("early", std::stoull(tokens[4]), counts.early);
            Same("late", std::stoull(tokens[5]), counts.late);
            Same("teleports", std::stoull(tokens[6]), counts.teleports);
        }
        else if (op == "END" && need(1))
        {
            m_open = false;
            m_buffer.reset();
        }
        else
        {
            Mismatch("record", "a known record", aLine.substr(0, 40));
        }
    }

    std::optional<SnapshotBuffer> m_buffer;
    std::string m_scenario;
    bool m_open = false;
    int m_lineNumber = 0;
    int m_scenarios = 0;
    int m_pushes = 0;
    int m_renders = 0;
    int m_samples = 0;
    int m_delays = 0;
    int m_resets = 0;
    int m_countSets = 0;
    int m_compared = 0;
    int m_mismatches = 0;
};
} // namespace

int main(int argc, char** argv)
{
    if (argc == 3 && std::strcmp(argv[1], "trace") == 0)
    {
        return TraceReplay().Run(argv[2]);
    }
    if (argc != 1)
    {
        std::puts("usage: coopnet_v2_interp_tests [trace <file>]");
        return EXIT_FAILURE;
    }
    TestInterpolationErrorSmallWithJitterAndLoss();
    TestExtrapolationIsBounded();
    TestTeleportClearsHistoryAndLateSamplesDropped();
    TestPlayoutSnapsOnLargeDiscontinuity();
    TestPythonModuloAndAngles();
    TestStateVelocityAndPitch();
    TestHistoryAndTransitWindow();
    TestSimulatedLinks();
    TestCost();
    std::printf("%d checks, %d failures\n", g_checks, g_failures);
    return g_failures == 0 ? EXIT_SUCCESS : EXIT_FAILURE;
}
