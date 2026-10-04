// Tests for ClockSync (src/v2/ClockSync): the cases of the relay's tests/test_interp.py ClockSync
// tests, the TIME_REQ / TIME_RESP wire handling (t0 matching, u32 unwrapping, impossible stamps),
// and accuracy on simulated links against interp.py's estimator (midpoint of the lowest-RTT
// exchange of the last 16), fed the same exchanges.
//
// The simulated relay behaves like relay_v2.py: its clock is whole milliseconds (truncated), it
// stamps t1 when it reads the request and t2 when it answers. Links have a base latency per
// direction, uniform jitter (which also reorders), loss, and optionally a relay clock that runs
// at a different rate (drift).

#include "v2/ClockSync.hpp"

#include "V2TestSupport.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <optional>
#include <queue>
#include <vector>

using namespace coopnet::v2;

namespace
{
int g_failures = 0;
int g_checks = 0;

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

// interp.py's ClockSync, the reference estimator the tests compare against.
class LowestRttClock
{
public:
    void OnResponse(double aT0, double aT1, double aT2, double aT3)
    {
        const double rtt = std::max(0.0, (aT3 - aT0) - (aT2 - aT1));
        const double offset = ((aT1 - aT0) + (aT2 - aT3)) / 2.0;
        m_samples.emplace_back(rtt, offset);
        if (m_samples.size() > kWindow)
        {
            m_samples.pop_front();
        }
        const auto best = *std::min_element(m_samples.begin(), m_samples.end());
        const bool warming = m_samples.size() < kWindow;
        if (!m_offset || warming || std::fabs(best.second - *m_offset) > 50.0)
        {
            m_offset = best.second;
        }
        else
        {
            m_offset = *m_offset + std::clamp(best.second - *m_offset, -2.0, 2.0);
        }
    }

    [[nodiscard]] std::optional<double> Offset() const
    {
        return m_offset;
    }

private:
    static constexpr size_t kWindow = 16;
    std::deque<std::pair<double, double>> m_samples;
    std::optional<double> m_offset;
};

ClockSyncConfig ExactStamps()
{
    ClockSyncConfig config;
    config.relayStampResolutionMs = 0.0;
    config.driftPpm = 0.0;
    return config;
}

// test_interp.py's exchange(): one request with the given one-way delays.
bool Exchange(ClockSync& aSync, double aTrueOffset, double aLocalSend, double aUpMs, double aDownMs,
              double aRelayHoldMs = 0.2)
{
    const double t0 = aLocalSend;
    const double t1 = t0 + aTrueOffset + aUpMs;
    const double t2 = t1 + aRelayHoldMs;
    const double t3 = t2 - aTrueOffset + aDownMs;
    return aSync.OnExchange(t0, t1, t2, t3);
}

// ---- ported test_interp.py ClockSync tests --------------------------------------------------------

void TestSymmetricJitteredLink()
{
    std::puts("symmetric jittered link (test_interp.py: 80 + 0..30 ms each way, offset 5000 ms)");
    test::Rng rng(1);
    ClockSync sync(ExactStamps());
    for (int index = 0; index < 40; ++index)
    {
        Exchange(sync, 5000.0, index * 100.0, 80 + rng.Unit() * 30, 80 + rng.Unit() * 30);
    }
    std::printf("    offset error %+.3f ms (interp.py's test allows 8 ms), bound %.3f ms\n", *sync.OffsetMs() - 5000.0,
                *sync.ErrorBoundMs());
    CHECK(std::fabs(*sync.OffsetMs() - 5000.0) <= 8.0);
    CHECK(std::fabs(*sync.OffsetMs() - 5000.0) <= *sync.ErrorBoundMs() + 1e-9);
    CHECK(sync.Synced());
}

void TestAsymmetryBias()
{
    std::puts("asymmetric link: the bias is half the difference (no two-way method can see it)");
    ClockSync sync(ExactStamps());
    for (int index = 0; index < 20; ++index)
    {
        Exchange(sync, -300.0, index * 100.0, 20.0, 80.0);
    }
    CHECK(std::fabs(*sync.OffsetMs() - (-300.0 - 30.0)) <= 0.5);
}

void TestSlewAfterWarmupAndStepOnJump()
{
    std::puts("slew after warm-up, step on a jump (each phase fills the window)");
    ClockSync sync(ExactStamps());
    const size_t window = sync.Config().window;
    for (size_t index = 0; index < window; ++index)
    {
        Exchange(sync, 0.0, index * 100.0, 10.0, 10.0);
    }
    const double base = *sync.OffsetMs();
    double largestChange = 0.0;
    // The old intervals keep constraining the estimate until they leave the window, so this phase
    // runs a few exchanges longer than the window before the offset must have arrived.
    for (size_t index = 0; index < window + 8; ++index)
    {
        const double before = *sync.OffsetMs();
        Exchange(sync, 10.0, 20000 + index * 100.0, 5.0, 5.0);
        largestChange = std::max(largestChange, std::fabs(*sync.OffsetMs() - before));
    }
    CHECK(std::fabs(*sync.OffsetMs() - base - 10.0) <= 0.5);
    CHECK(largestChange <= sync.Config().maxSlewMs + 1e-9);
    bool stepped = false;
    size_t steppedAt = 0;
    for (size_t index = 0; index < window; ++index)
    {
        if (Exchange(sync, 500.0, 50000 + index * 100.0, 1.0, 1.0) && !stepped)
        {
            stepped = true;
            steppedAt = index + 1;
        }
    }
    std::printf("    stepped after %zu of %zu exchanges at the new offset (half the window; ties go to the newest)\n",
                steppedAt, window);
    CHECK(stepped && steppedAt == window / 2);
    CHECK(std::fabs(*sync.OffsetMs() - 500.0) <= 1.0);
    CHECK(sync.Steps() == 1);
}

// ---- wire handling -------------------------------------------------------------------------------

void TestWireExchange()
{
    std::puts("TIME_REQ / TIME_RESP: t0 matching, duplicates, impossible stamps, timeouts");
    ClockSync sync;
    CHECK(sync.RequestDue(0.0));
    const coopv2::TimeReq first = sync.MakeRequest(1000.6);
    CHECK(first.t0 == 1000);
    CHECK(!sync.RequestDue(1050.0) && sync.RequestDue(1100.7));
    // relay = local + 5000.25: receive at local 1010.6, answer 0.4 ms later
    const coopv2::TimeResp response{first.t0, 6010, 6011};
    CHECK(sync.OnResponse(response, 1021.0) == ClockResult::Accepted);
    CHECK(sync.OnResponse(response, 1022.0) == ClockResult::UnknownT0); // duplicate
    CHECK(sync.OnResponse(coopv2::TimeResp{77, 6010, 6011}, 1022.0) == ClockResult::UnknownT0);
    // interval [6011 - 1021, 6010 + 1 - 1000.6] = [4990, 5010.4]
    CHECK(std::fabs(*sync.EstimateMs() - 5000.2) < 1e-9);
    const coopv2::TimeReq second = sync.MakeRequest(1200.0);
    CHECK(sync.OnResponse(coopv2::TimeResp{second.t0, 6210, 6209}, 1220.0) == ClockResult::Invalid); // t2 < t1
    const coopv2::TimeReq third = sync.MakeRequest(1300.0);
    CHECK(sync.OnResponse(coopv2::TimeResp{third.t0, 6310, 6340}, 1320.0) == ClockResult::Invalid); // hold > rtt
    const coopv2::TimeReq fourth = sync.MakeRequest(1400.0);
    CHECK(sync.OnResponse(coopv2::TimeResp{fourth.t0, 6410, 6410}, 1400.0 + 6000.0) == ClockResult::UnknownT0);
    CHECK(sync.Samples() == 1);
    // Two requests in the same millisecond: the newer send time is used.
    sync.MakeRequest(1500.2);
    const coopv2::TimeReq same = sync.MakeRequest(1500.7);
    CHECK(sync.OnResponse(coopv2::TimeResp{same.t0, 6505, 6505}, 1510.7) == ClockResult::Accepted);
    CHECK(sync.Samples() == 2);
}

void TestRequestSchedule()
{
    std::puts("request schedule: fast until warm, then slow");
    ClockSync sync;
    double now = 0.0;
    int requests = 0;
    while (sync.Samples() < sync.Config().warmup)
    {
        if (sync.RequestDue(now))
        {
            const coopv2::TimeReq request = sync.MakeRequest(now);
            ++requests;
            const auto relay = static_cast<uint32_t>(now + 100.0 + 5.0);
            sync.OnResponse(coopv2::TimeResp{request.t0, relay, relay}, now + 10.0);
        }
        now += 1.0;
    }
    std::printf("    warm after %d requests in %.0f ms\n", requests, now);
    CHECK(requests == static_cast<int>(sync.Config().warmup));
    CHECK(now < sync.Config().warmup * sync.Config().fastIntervalMs + 50.0);
    const double warmAt = now;
    int later = 0;
    for (; now < warmAt + 10000.0; now += 1.0)
    {
        if (sync.RequestDue(now))
        {
            sync.MakeRequest(now);
            ++later;
        }
    }
    CHECK(later == static_cast<int>(10000.0 / sync.Config().slowIntervalMs));
}

void TestU32Wrap()
{
    std::puts("relay clock crossing 2^32 ms; sample_time unwrapping");
    CHECK(UnwrapU32Near(5, 4294967290.0) == 4294967301.0);
    CHECK(UnwrapU32Near(4294967290u, 4294967301.0) == 4294967290.0);
    CHECK(UnwrapU32Near(1000, 1003.7) == 1000.0);
    CHECK(UnwrapU32Near(4294967295u, 0.5) == -1.0);
    ClockSync sync;
    const double trueOffset = 4294967296.0 - 1500.0; // relay = local + offset, wraps at local 1500
    double previousRelay = 0.0;
    bool monotonic = true;
    for (int index = 0; index < 40; ++index)
    {
        const double t0 = 100.0 + index * 100.0;
        const coopv2::TimeReq request = sync.MakeRequest(t0);
        const double relayReceive = t0 + 20.0 + trueOffset;
        const auto stamp = static_cast<uint32_t>(static_cast<uint64_t>(std::floor(relayReceive)));
        CHECK(sync.OnResponse(coopv2::TimeResp{request.t0, stamp, stamp}, t0 + 40.0) != ClockResult::Invalid);
        const double relay = sync.RelayMs(t0 + 40.0);
        monotonic = monotonic && relay > previousRelay;
        previousRelay = relay;
    }
    CHECK(monotonic);
    CHECK(std::fabs(*sync.OffsetMs() - trueOffset) < 1.0);
    CHECK(sync.Steps() == 0);
    const double local = 4100.0;
    const auto sampleTime = static_cast<uint32_t>(static_cast<uint64_t>(local + trueOffset - 120.0)); // wrapped
    CHECK(std::fabs(sync.UnwrapRelayMs(sampleTime, local) - (local + trueOffset - 120.0)) < 2.0);
}

void TestOutlierIsOutvoted()
{
    std::puts("a single impossible exchange is outvoted");
    ClockSync sync(ExactStamps());
    for (int index = 0; index < 32; ++index)
    {
        Exchange(sync, 250.0, index * 100.0, 30.0, 30.0);
    }
    const double before = *sync.OffsetMs();
    // A response whose t1 is 300 ms late: its interval misses every other one.
    sync.OnExchange(3300.0, 3300.0 + 250.0 + 30.0 + 300.0, 3300.0 + 250.0 + 30.0 + 300.0, 3360.0);
    CHECK(sync.Agreeing() == sync.Config().window - 1);
    CHECK(*sync.OffsetMs() == before);
}

// ---- accuracy on simulated links -----------------------------------------------------------------

struct LinkProfile
{
    const char* name;
    double upBaseMs;
    double downBaseMs;
    double jitterMs; // uniform 0..jitter on each leg
    double loss;     // each leg
    double driftPpm; // relay clock rate relative to local
    double limitMs;  // largest |error| allowed after warm-up (offset error, asymmetry bias removed)
};

struct Accuracy
{
    double max = 0.0;
    double p50 = 0.0;
    double p99 = 0.0;
    double referenceMax = 0.0;
    double referenceP99 = 0.0;
    double boundViolations = 0.0;
    int steps = 0;
    size_t exchanges = 0;
};

double Percentile(std::vector<double> aValues, double aFraction)
{
    if (aValues.empty())
    {
        return 0.0;
    }
    std::sort(aValues.begin(), aValues.end());
    return aValues[static_cast<size_t>(aFraction * static_cast<double>(aValues.size() - 1))];
}

Accuracy Simulate(const LinkProfile& aProfile, uint64_t aSeed, double aSeconds)
{
    test::Rng rng(aSeed);
    ClockSync sync;
    LowestRttClock reference;
    const double offset0 = 123456.789;
    const double rate = 1.0 + aProfile.driftPpm * 1e-6;
    const auto relayClock = [&](double aLocalMs) { return offset0 + aLocalMs * rate; };
    struct Arrival
    {
        double at;
        uint64_t order;
        coopv2::TimeResp response;
        double t0Exact;
        bool operator>(const Arrival& aOther) const
        {
            return at != aOther.at ? at > aOther.at : order > aOther.order;
        }
    };
    std::priority_queue<Arrival, std::vector<Arrival>, std::greater<>> inFlight;
    uint64_t order = 0;
    std::vector<double> errors;
    std::vector<double> referenceErrors;
    Accuracy result;
    const double bias = (aProfile.upBaseMs - aProfile.downBaseMs) / 2.0;
    for (double now = 0.0; now < aSeconds * 1000.0; now += 1.0)
    {
        while (!inFlight.empty() && inFlight.top().at <= now)
        {
            const Arrival arrival = inFlight.top();
            inFlight.pop();
            const ClockResult verdict = sync.OnResponse(arrival.response, now);
            CHECK(verdict == ClockResult::Accepted || verdict == ClockResult::Stepped);
            result.steps += verdict == ClockResult::Stepped && now >= 10000.0 ? 1 : 0; // warm-up may step
            reference.OnResponse(arrival.t0Exact, arrival.response.t1, arrival.response.t2, now);
            ++result.exchanges;
        }
        if (sync.RequestDue(now))
        {
            const coopv2::TimeReq request = sync.MakeRequest(now);
            if (!rng.Chance(aProfile.loss))
            {
                const double atRelay = now + aProfile.upBaseMs + rng.Unit() * aProfile.jitterMs;
                const double hold = rng.Unit() * 0.5; // relay_v2.py answers from the same tick
                const auto t1 = static_cast<uint32_t>(std::floor(relayClock(atRelay)));
                const auto t2 = static_cast<uint32_t>(std::floor(relayClock(atRelay + hold)));
                if (!rng.Chance(aProfile.loss))
                {
                    const double back = atRelay + hold + aProfile.downBaseMs + rng.Unit() * aProfile.jitterMs;
                    // The local loop only notices an arrival on its next 1 ms step.
                    inFlight.push(Arrival{std::ceil(back), ++order, coopv2::TimeResp{request.t0, t1, t2}, now});
                }
            }
        }
        if (now >= 10000.0 && static_cast<int64_t>(now) % 100 == 0 && sync.Synced())
        {
            const double truth = relayClock(now) - now;
            errors.push_back(std::fabs(*sync.OffsetMs() - truth - bias));
            referenceErrors.push_back(std::fabs(*reference.Offset() - truth - bias));
            // The bound holds for any jitter and asymmetry while drift stays within the allowance;
            // 0.2 ms covers the drift since the last exchange (up to 1 s at 100 ppm is 0.1 ms).
            const bool boundApplies = std::fabs(aProfile.driftPpm) <= sync.Config().driftPpm;
            if (boundApplies && std::fabs(*sync.EstimateMs() - truth) > *sync.ErrorBoundMs() + 0.2)
            {
                result.boundViolations += 1.0;
            }
        }
    }
    result.max = Percentile(errors, 1.0);
    result.p50 = Percentile(errors, 0.5);
    result.p99 = Percentile(errors, 0.99);
    result.referenceMax = Percentile(referenceErrors, 1.0);
    result.referenceP99 = Percentile(referenceErrors, 0.99);
    return result;
}

void TestAccuracyOnSimulatedLinks()
{
    std::puts("accuracy after 10 s on simulated links, 120 s each, relay stamps truncated to whole ms");
    std::puts("    profile                  |error| p50 / p99 / max (ms)    interp.py estimator p99 / max");
    const LinkProfile profiles[] = {
        {"loopback", 0.1, 0.1, 0.3, 0.0, 0.0, 1.0},
        {"jitter 20 ms, 1% loss", 40.0, 40.0, 20.0, 0.01, 0.0, 5.0},
        {"LA-Warsaw 78+18 ms, 2%", 78.0, 78.0, 18.0, 0.02, 0.0, 5.0},
        {"stress 60+40 ms, 10%", 60.0, 60.0, 40.0, 0.10, 0.0, 5.0},
        {"brutal 90+60 ms, 20%", 90.0, 90.0, 60.0, 0.20, 0.0, 5.0},
        {"drift +100 ppm, 20 ms", 40.0, 40.0, 20.0, 0.01, 100.0, 5.0},
        {"drift -250 ppm, 20 ms", 40.0, 40.0, 20.0, 0.01, -250.0, 5.0},
        {"asymmetric 20 / 80 ms", 20.0, 80.0, 10.0, 0.01, 0.0, 5.0},
    };
    for (const LinkProfile& profile : profiles)
    {
        double worst = 0.0;
        double worstReference = 0.0;
        Accuracy last;
        for (uint64_t seed = 1; seed <= 5; ++seed)
        {
            last = Simulate(profile, seed * 7919, 120.0);
            worst = std::max(worst, last.max);
            worstReference = std::max(worstReference, last.referenceMax);
            CHECK(last.max < profile.limitMs);
            CHECK(last.boundViolations == 0.0);
            CHECK(last.steps == 0);
        }
        std::printf("    %-24s %6.2f / %6.2f / %6.2f (worst of 5: %5.2f)   %6.2f / %6.2f (worst %5.2f)\n", profile.name,
                    last.p50, last.p99, last.max, worst, last.referenceP99, last.referenceMax, worstReference);
        if (profile.jitterMs >= 18.0 && profile.driftPpm == 0.0 && profile.upBaseMs == profile.downBaseMs)
        {
            CHECK(worst < worstReference);
        }
    }
}
} // namespace

int main()
{
    TestSymmetricJitteredLink();
    TestAsymmetryBias();
    TestSlewAfterWarmupAndStepOnJump();
    TestWireExchange();
    TestRequestSchedule();
    TestU32Wrap();
    TestOutlierIsOutvoted();
    TestAccuracyOnSimulatedLinks();
    std::printf("%d checks, %d failures\n", g_checks, g_failures);
    return g_failures == 0 ? EXIT_SUCCESS : EXIT_FAILURE;
}
