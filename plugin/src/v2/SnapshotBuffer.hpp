#pragma once

// Snapshot interpolation buffer for one remote object: port of InterpBuffer in
// relay/coopnet/interp.py, which stays the reference.
//
// Timeline: every snapshot carries sample_time, the sender's estimate of the relay clock (ms) when
// the state was sampled. The receiver renders the object at
//
//     render_time = relay_now - (fastest_transit + interp_delay)
//
// where fastest_transit is the minimum (arrival - sample_time) over the last transitWindow samples
// (the latency floor; a residual clock-offset error cancels out) and interp_delay adapts to jitter
// inside [minDelayMs, maxDelayMs]. The playout point slews at most 10 % of the frame time per frame,
// so delay changes never cause visible jumps; it snaps when the target moves by more than
// kSnapPlayoutMs (for example after ResetTiming).
//
// Between two samples the position follows a cubic Hermite spline built from the sent velocities
// (linear when a sample has none, e.g. a bridged v1 player); past the newest sample it is
// extrapolated with the last velocity for at most maxExtrapolateMs and then held. Before the first
// sample it is back-extrapolated along the first velocity ("early").
//
// tools/v2_interp_trace.py records interp.py's InterpBuffer in its test scenarios and in random
// ones; coopnet_v2_interp_tests replays the calls here and requires the same results bit for bit
// (positions, yaw, modes, render times, delays, counters).
//
// C++ additions, not in interp.py (and not part of the trace comparison):
// * every sample also carries pitch and the discrete player state (move state, flags, health);
//   SampleAt blends pitch linearly and reports the state of the sample at or before the render time
//   (the first sample when early, the newest when extrapolating or held);
// * SampleAt also reports a velocity: the sent velocities blended linearly (not the derivative of
//   the drawn curve), the newest one when extrapolating or held, zero for samples without velocity;
// * accessors for diagnostics (newest sample time, playout, counters).
//
// All times are milliseconds as double on the unwrapped relay timeline. Not thread-safe: the owner
// serialises access.

#include <array>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <optional>
#include <vector>

namespace coopnet::v2
{
using Vec3 = std::array<double, 3>;

struct InterpConfig
{
    double sendIntervalMs = 1000.0 / 30.0;
    double minDelayMs = 100.0;
    double maxDelayMs = 150.0;
    double maxExtrapolateMs = 250.0;
    double historyMs = 2000.0;
    size_t transitWindow = 90;
};

// interp.py InterpBuffer.PLAYER and InterpBuffer.ENTITY.
inline constexpr InterpConfig kPlayerInterp{1000.0 / 30.0, 100.0, 150.0, 250.0, 2000.0, 90};
inline constexpr InterpConfig kEntityInterp{100.0, 150.0, 200.0, 250.0, 2000.0, 90};
inline constexpr double kSnapPlayoutMs = 250.0;

enum class SampleMode : uint8_t
{
    Interpolated,
    Extrapolated, // past the newest sample, within maxExtrapolateMs
    Held,         // past the newest sample by more than maxExtrapolateMs: extrapolation capped
    Early,        // before the first sample: back-extrapolated along its velocity
};

const char* ToString(SampleMode aMode);

// Discrete state carried with each sample (C++ addition).
struct SnapshotState
{
    double pitch = 0.0;    // degrees, blended linearly between samples
    uint8_t moveState = 0; // MoveState
    uint16_t flags = 0;    // PlayerFlag
    uint8_t health = 0;    // 0..255
    bool operator==(const SnapshotState&) const = default;
};

struct SnapshotSample
{
    double t = 0.0; // sample_time on the relay timeline
    Vec3 pos{};
    Vec3 vel{};
    double yaw = 0.0;
    bool hasVel = true;
    SnapshotState state;
};

struct SampledPose
{
    Vec3 pos{};
    double yaw = 0.0;
    SampleMode mode = SampleMode::Interpolated;
    // C++ additions
    Vec3 vel{};
    double pitch = 0.0;
    SnapshotState state; // pitch inside is the reference sample's, the blended one is above
};

// interp.py's counts dictionary.
struct InterpCounts
{
    uint64_t interpolated = 0;
    uint64_t extrapolated = 0;
    uint64_t held = 0;
    uint64_t early = 0;
    uint64_t late = 0;
    uint64_t teleports = 0;
};

enum class PushResult : uint8_t
{
    Added,
    Duplicate, // a sample with exactly this time is already buffered (ignored, not counted)
    Late,      // not newer than the oldest buffered sample (counted as late)
};

class SnapshotBuffer
{
public:
    explicit SnapshotBuffer(const InterpConfig& aConfig = kPlayerInterp);

    // interp.py push(t, arrival_relay_ms, pos, vel, yaw, teleported, has_vel). The transit sample
    // (arrival - t) is recorded even for late and duplicate samples, as interp.py does. A teleported
    // sample first clears the history.
    PushResult Push(double aT, double aArrivalRelayMs, const Vec3& aPos, const Vec3& aVel, double aYaw,
                    bool aTeleported = false, bool aHasVel = true, const SnapshotState& aState = {});

    // Forgets the transit statistics and the playout point, e.g. after the relay clock stepped.
    void ResetTiming();

    [[nodiscard]] double TargetDelayMs() const;
    // The time to render at for this frame; moves the playout point (call once per frame).
    double RenderTime(double aRelayNowMs, double aFrameDtMs);
    // The pose at aT, or nullopt when nothing is buffered. Updates the counters.
    std::optional<SampledPose> SampleAt(double aT);

    [[nodiscard]] bool Empty() const
    {
        return m_samples.empty();
    }
    [[nodiscard]] size_t Size() const
    {
        return m_samples.size();
    }
    [[nodiscard]] std::optional<double> OldestTime() const;
    [[nodiscard]] std::optional<double> NewestTime() const;
    [[nodiscard]] size_t TransitCount() const
    {
        return m_transit.size();
    }
    [[nodiscard]] std::optional<double> PlayoutMs() const
    {
        return m_playout;
    }
    [[nodiscard]] const InterpCounts& Counts() const
    {
        return m_counts;
    }
    [[nodiscard]] const InterpConfig& Config() const
    {
        return m_config;
    }
    [[nodiscard]] const std::vector<SnapshotSample>& Samples() const
    {
        return m_samples;
    }

private:
    InterpConfig m_config;
    std::vector<SnapshotSample> m_samples; // ascending t, no two equal
    std::deque<double> m_transit;          // arrival - sample time, newest last
    std::optional<double> m_playout;
    InterpCounts m_counts;
};

// Python's float modulo (the result takes the sign of the divisor), as interp.py uses it.
double PyFloatMod(double aValue, double aModulus);
// interp.py lerp_angle: the shorter way round, result in [0, 360).
double LerpAngle(double aFrom, double aTo, double aU);
} // namespace coopnet::v2
