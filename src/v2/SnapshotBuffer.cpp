#include "v2/SnapshotBuffer.hpp"

#include <algorithm>
#include <cmath>

// Every expression below keeps interp.py's operation order (Python evaluates left to right and
// MSVC /fp:precise neither reorders nor contracts), so the results match the reference bit for bit.

namespace coopnet::v2
{
namespace
{
// Python min(a, b) / max(a, b): the first argument unless the second is strictly smaller / larger.
double PyMin(double aFirst, double aSecond)
{
    return aSecond < aFirst ? aSecond : aFirst;
}

double PyMax(double aFirst, double aSecond)
{
    return aSecond > aFirst ? aSecond : aFirst;
}

double Lerp(double aFrom, double aTo, double aU)
{
    return aFrom + (aTo - aFrom) * aU;
}

constexpr Vec3 kZero{0.0, 0.0, 0.0};
} // namespace

const char* ToString(SampleMode aMode)
{
    switch (aMode)
    {
    case SampleMode::Interpolated:
        return "interpolated";
    case SampleMode::Extrapolated:
        return "extrapolated";
    case SampleMode::Held:
        return "held";
    case SampleMode::Early:
        return "early";
    }
    return "?";
}

double PyFloatMod(double aValue, double aModulus)
{
    // CPython float_rem.
    double mod = std::fmod(aValue, aModulus);
    if (mod != 0.0)
    {
        if ((aModulus < 0.0) != (mod < 0.0))
        {
            mod += aModulus;
        }
    }
    else
    {
        mod = std::copysign(0.0, aModulus);
    }
    return mod;
}

double LerpAngle(double aFrom, double aTo, double aU)
{
    const double delta = PyFloatMod(aTo - aFrom + 180.0, 360.0) - 180.0;
    return PyFloatMod(aFrom + delta * aU, 360.0);
}

SnapshotBuffer::SnapshotBuffer(const InterpConfig& aConfig)
    : m_config(aConfig)
{
    m_config.transitWindow = std::max<size_t>(m_config.transitWindow, 1);
}

PushResult SnapshotBuffer::Push(double aT, double aArrivalRelayMs, const Vec3& aPos, const Vec3& aVel, double aYaw,
                                bool aTeleported, bool aHasVel, const SnapshotState& aState)
{
    m_transit.push_back(aArrivalRelayMs - aT);
    while (m_transit.size() > m_config.transitWindow)
    {
        m_transit.pop_front();
    }
    if (aTeleported)
    {
        m_samples.clear();
        ++m_counts.teleports;
    }
    if (!m_samples.empty() && aT <= m_samples.front().t)
    {
        ++m_counts.late;
        return PushResult::Late;
    }
    const auto position = std::lower_bound(m_samples.begin(), m_samples.end(), aT,
                                           [](const SnapshotSample& aSample, double aTime) { return aSample.t < aTime; });
    if (position != m_samples.end() && position->t == aT)
    {
        return PushResult::Duplicate;
    }
    m_samples.insert(position, SnapshotSample{aT, aPos, aVel, aYaw, aHasVel, aState});
    const double horizon = m_samples.back().t - m_config.historyMs;
    size_t drop = 0;
    while (m_samples.size() - drop > 2 && m_samples[drop].t < horizon)
    {
        ++drop;
    }
    m_samples.erase(m_samples.begin(), m_samples.begin() + static_cast<std::ptrdiff_t>(drop));
    return PushResult::Added;
}

void SnapshotBuffer::ResetTiming()
{
    m_transit.clear();
    m_playout.reset();
}

double SnapshotBuffer::TargetDelayMs() const
{
    if (m_transit.empty())
    {
        return m_config.minDelayMs;
    }
    std::vector<double> ordered(m_transit.begin(), m_transit.end());
    std::sort(ordered.begin(), ordered.end());
    const auto index = static_cast<size_t>(0.95 * static_cast<double>(ordered.size() - 1));
    const double jitter = ordered[index] - ordered[0];
    const double wanted = PyMax(2.0 * m_config.sendIntervalMs, jitter + m_config.sendIntervalMs);
    return PyMin(m_config.maxDelayMs, PyMax(m_config.minDelayMs, wanted));
}

double SnapshotBuffer::RenderTime(double aRelayNowMs, double aFrameDtMs)
{
    if (m_transit.empty())
    {
        return aRelayNowMs - m_config.minDelayMs;
    }
    double fastest = m_transit.front();
    for (const double transit : m_transit)
    {
        fastest = PyMin(fastest, transit);
    }
    const double target = fastest + TargetDelayMs();
    if (!m_playout || std::fabs(target - *m_playout) > kSnapPlayoutMs)
    {
        m_playout = target;
    }
    else
    {
        const double step = 0.1 * aFrameDtMs;
        m_playout = *m_playout + PyMax(-step, PyMin(step, target - *m_playout));
    }
    return aRelayNowMs - *m_playout;
}

std::optional<SampledPose> SnapshotBuffer::SampleAt(double aT)
{
    if (m_samples.empty())
    {
        return std::nullopt;
    }
    const SnapshotSample& first = m_samples.front();
    const SnapshotSample& last = m_samples.back();
    SampledPose pose;
    if (aT <= first.t)
    {
        ++m_counts.early;
        const double behindS = PyMin(first.t - aT, m_config.maxExtrapolateMs) / 1000.0;
        for (size_t axis = 0; axis < 3; ++axis)
        {
            pose.pos[axis] = first.hasVel ? first.pos[axis] - first.vel[axis] * behindS : first.pos[axis];
        }
        pose.yaw = first.yaw;
        pose.mode = SampleMode::Early;
        pose.vel = first.hasVel ? first.vel : kZero;
        pose.pitch = first.state.pitch;
        pose.state = first.state;
        return pose;
    }
    if (aT >= last.t)
    {
        const double aheadS = PyMin(aT - last.t, m_config.maxExtrapolateMs) / 1000.0;
        if (aT - last.t > m_config.maxExtrapolateMs)
        {
            ++m_counts.held;
            pose.mode = SampleMode::Held;
        }
        else
        {
            ++m_counts.extrapolated;
            pose.mode = SampleMode::Extrapolated;
        }
        for (size_t axis = 0; axis < 3; ++axis)
        {
            pose.pos[axis] = last.hasVel ? last.pos[axis] + last.vel[axis] * aheadS : last.pos[axis];
        }
        pose.yaw = last.yaw;
        pose.vel = last.hasVel ? last.vel : kZero;
        pose.pitch = last.state.pitch;
        pose.state = last.state;
        return pose;
    }
    const auto after = std::upper_bound(m_samples.begin(), m_samples.end(), aT,
                                        [](double aTime, const SnapshotSample& aSample) { return aTime < aSample.t; });
    const SnapshotSample& a = *(after - 1);
    const SnapshotSample& b = *after;
    const double spanS = (b.t - a.t) / 1000.0;
    const double u = (aT - a.t) / (b.t - a.t);
    ++m_counts.interpolated;
    const bool bothVel = a.hasVel && b.hasVel;
    if (bothVel)
    {
        const double u2 = u * u;
        const double u3 = u * u * u;
        const double h00 = 2 * u3 - 3 * u2 + 1;
        const double h10 = u3 - 2 * u2 + u;
        const double h01 = -2 * u3 + 3 * u2;
        const double h11 = u3 - u2;
        for (size_t axis = 0; axis < 3; ++axis)
        {
            pose.pos[axis] = h00 * a.pos[axis] + h10 * spanS * a.vel[axis] + h01 * b.pos[axis] +
                             h11 * spanS * b.vel[axis];
        }
    }
    else
    {
        for (size_t axis = 0; axis < 3; ++axis)
        {
            pose.pos[axis] = Lerp(a.pos[axis], b.pos[axis], u);
        }
    }
    pose.yaw = LerpAngle(a.yaw, b.yaw, u);
    pose.mode = SampleMode::Interpolated;
    for (size_t axis = 0; axis < 3; ++axis)
    {
        pose.vel[axis] = bothVel ? Lerp(a.vel[axis], b.vel[axis], u) : 0.0;
    }
    pose.pitch = Lerp(a.state.pitch, b.state.pitch, u);
    pose.state = a.state;
    return pose;
}

std::optional<double> SnapshotBuffer::OldestTime() const
{
    return m_samples.empty() ? std::nullopt : std::optional<double>(m_samples.front().t);
}

std::optional<double> SnapshotBuffer::NewestTime() const
{
    return m_samples.empty() ? std::nullopt : std::optional<double>(m_samples.back().t);
}
} // namespace coopnet::v2
