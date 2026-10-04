#pragma once

// Relay clock estimation over protocol v2 TIME_REQ / TIME_RESP, for snapshot interpolation.
//
// Every snapshot carries sample_time, the sender's estimate of the relay clock (ms) when the state
// was sampled. Receivers render remote objects at relay_now - delay, so both players need the same
// relay clock: the Phase 2 exit criterion is under 5 ms between the two game instances.
//
// One exchange gives four stamps: t0 local send, t1 relay receive, t2 relay send, t3 local receive.
// With relay = local + theta and both one-way delays >= 0, whatever the jitter:
//
//     t2 - t3  <=  theta  <=  t1 - t0
//
// so every exchange is an interval that must contain the true offset; its midpoint is the classic
// NTP offset and its width the round trip. interp.py's ClockSync takes the midpoint of the
// lowest-RTT exchange in a window, which is off by half the jitter difference of that one
// exchange. Here the intervals of the window are intersected instead (Marzullo's algorithm: the
// region shared by the most intervals, so a bad exchange is outvoted). With symmetric jitter the
// error shrinks to half the difference between the smallest uplink and the smallest downlink
// delay seen in the window. Older intervals widen by driftPpm per second of age, so clocks that
// run at slightly different rates stay consistent. Asymmetric base latency remains a bias of half
// the difference (no two-way method can see it).
//
// The applied offset follows interp.py: during warm-up it jumps to each new estimate; afterwards
// it slews at most maxSlewMs per exchange and steps only for changes above stepThresholdMs. A step
// above stepNotifyMs is reported so snapshot buffers can reset their timing.
//
// All times are milliseconds as double. Local time must be monotonic (steady_clock / QPC). Not
// thread-safe: the network thread owns it.

#include "v2/coop_proto_v2.h"

#include <cstddef>
#include <cstdint>
#include <deque>
#include <optional>

namespace coopnet::v2
{
struct ClockSyncConfig
{
    size_t window = 32;                  // exchanges kept for the estimate
    size_t syncedAfter = 3;              // Synced() from this many exchanges (interp.py)
    size_t warmup = 16;                  // the offset jumps to the estimate until this many (interp.py)
    double stepThresholdMs = 50.0;       // larger changes step instead of slewing (interp.py)
    double stepNotifyMs = 20.0;          // a change above this is reported as a step (interp.py)
    double maxSlewMs = 2.0;              // per exchange after warm-up (interp.py)
    double driftPpm = 200.0;             // interval widening per second of age (clock rate difference)
    double relayStampResolutionMs = 1.0; // relay_v2.py truncates its clock to whole ms; 0 = exact
    double fastIntervalMs = 100.0;       // TIME_REQ period until warm (client_v2.py: 10 Hz)
    double slowIntervalMs = 500.0;       // afterwards; client_v2.py uses 1 Hz, 2 Hz keeps the window within 16 s
    double requestTimeoutMs = 5000.0;    // later responses are ignored
};

enum class ClockResult : uint8_t
{
    Accepted,
    Stepped,       // accepted, and the applied offset stepped (reset interpolation timing)
    UnknownT0,     // no outstanding request with this t0 (stale, duplicate or never sent)
    Invalid,       // relay hold time negative or longer than the round trip, or t3 < t0
};

const char* ToString(ClockResult aResult);

class ClockSync
{
public:
    explicit ClockSync(const ClockSyncConfig& aConfig = {});

    // ---- wire ----------------------------------------------------------------------------------

    // Whether a TIME_REQ should go out now.
    [[nodiscard]] bool RequestDue(double aLocalMs) const;
    // A TIME_REQ for a request sent at aLocalMs; remembers the exact send time under the u32 t0.
    coopv2::TimeReq MakeRequest(double aLocalMs);
    // A TIME_RESP received at aLocalMs. t0 must match an outstanding request (its exact send time is
    // used, not the u32 echo); t1 is unwrapped from u32 near t0 + the current offset.
    ClockResult OnResponse(const coopv2::TimeResp& aResponse, double aLocalMs);

    // ---- core ----------------------------------------------------------------------------------

    // One exchange with t1/t2 already on the unwrapped relay timeline, as the relay stamped them.
    // Returns true when the applied offset stepped (interp.py's on_response).
    bool OnExchange(double aT0, double aT1, double aT2, double aT3);

    [[nodiscard]] bool Synced() const
    {
        return m_samples.size() >= m_config.syncedAfter;
    }
    // Applied offset (relay - local), what RelayMs uses; nullopt before the first exchange.
    [[nodiscard]] std::optional<double> OffsetMs() const
    {
        return m_offset;
    }
    // Latest estimate before slewing: the midpoint of the best interval overlap.
    [[nodiscard]] std::optional<double> EstimateMs() const
    {
        return m_estimate;
    }
    // Half the width of the best overlap: |true offset - estimate| is below this, as long as the
    // relay's stamps are right and drift stays within driftPpm. It has to allow for any split of
    // the round trip between the two directions, so on a real link it is about half the lowest
    // round trip; with symmetric delays the actual error is far smaller.
    [[nodiscard]] std::optional<double> ErrorBoundMs() const
    {
        return m_bound;
    }
    // Lowest round trip in the window (relay hold time excluded).
    [[nodiscard]] std::optional<double> RttMs() const
    {
        return m_minRtt;
    }
    [[nodiscard]] size_t Samples() const
    {
        return m_samples.size();
    }
    // How many exchanges the best overlap agreed with (the others were outvoted).
    [[nodiscard]] size_t Agreeing() const
    {
        return m_agreeing;
    }
    [[nodiscard]] uint32_t Steps() const
    {
        return m_steps;
    }
    [[nodiscard]] const ClockSyncConfig& Config() const
    {
        return m_config;
    }

    // Relay time for a local time (local + applied offset; local when nothing is known yet).
    [[nodiscard]] double RelayMs(double aLocalMs) const;
    // A u32 relay stamp from the wire (sample_time) on the unwrapped relay timeline, taking the
    // value nearest to RelayMs(aLocalMs).
    [[nodiscard]] double UnwrapRelayMs(uint32_t aStamp, double aLocalMs) const;

private:
    struct Sample
    {
        double low;  // t2 - t3
        double high; // t1 - t0 (+ relay stamp resolution)
        double rtt;
        double at;   // t3
    };

    struct Request
    {
        uint32_t t0;
        double localMs;
    };

    void Estimate(double aNowMs);

    ClockSyncConfig m_config;
    std::deque<Sample> m_samples;
    std::deque<Request> m_requests;
    std::optional<double> m_lastRequestMs;
    std::optional<double> m_offset;
    std::optional<double> m_estimate;
    std::optional<double> m_bound;
    std::optional<double> m_minRtt;
    size_t m_agreeing = 0;
    uint32_t m_steps = 0;
};

// The value of a u32 millisecond stamp nearest to aNear on an unwrapped timeline.
double UnwrapU32Near(uint32_t aStamp, double aNear);
} // namespace coopnet::v2
