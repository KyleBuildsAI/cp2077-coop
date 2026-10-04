#include "v2/ClockSync.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <vector>

namespace coopnet::v2
{
namespace
{
constexpr size_t kMaxOutstandingRequests = 64;

uint32_t LowBits(double aMs)
{
    return static_cast<uint32_t>(static_cast<uint64_t>(static_cast<int64_t>(std::floor(aMs))));
}
} // namespace

const char* ToString(ClockResult aResult)
{
    switch (aResult)
    {
    case ClockResult::Accepted:
        return "Accepted";
    case ClockResult::Stepped:
        return "Stepped";
    case ClockResult::UnknownT0:
        return "UnknownT0";
    case ClockResult::Invalid:
        return "Invalid";
    }
    return "?";
}

double UnwrapU32Near(uint32_t aStamp, double aNear)
{
    const double base = std::floor(aNear);
    const auto difference = static_cast<int32_t>(aStamp - LowBits(base));
    return base + static_cast<double>(difference);
}

ClockSync::ClockSync(const ClockSyncConfig& aConfig)
    : m_config(aConfig)
{
    m_config.window = std::max<size_t>(m_config.window, 1);
}

// ---- wire ----------------------------------------------------------------------------------------

bool ClockSync::RequestDue(double aLocalMs) const
{
    if (!m_lastRequestMs)
    {
        return true;
    }
    const double interval = m_samples.size() < m_config.warmup ? m_config.fastIntervalMs : m_config.slowIntervalMs;
    return aLocalMs - *m_lastRequestMs >= interval;
}

coopv2::TimeReq ClockSync::MakeRequest(double aLocalMs)
{
    m_lastRequestMs = aLocalMs;
    while (!m_requests.empty() &&
           (aLocalMs - m_requests.front().localMs > m_config.requestTimeoutMs || m_requests.size() >= kMaxOutstandingRequests))
    {
        m_requests.pop_front();
    }
    const uint32_t t0 = LowBits(aLocalMs);
    const auto same = std::find_if(m_requests.begin(), m_requests.end(),
                                   [&](const Request& aRequest) { return aRequest.t0 == t0; });
    if (same != m_requests.end())
    {
        same->localMs = aLocalMs; // two requests in one millisecond: the newer one wins
    }
    else
    {
        m_requests.push_back(Request{t0, aLocalMs});
    }
    return coopv2::TimeReq{t0};
}

ClockResult ClockSync::OnResponse(const coopv2::TimeResp& aResponse, double aLocalMs)
{
    const auto request = std::find_if(m_requests.begin(), m_requests.end(),
                                      [&](const Request& aRequest) { return aRequest.t0 == aResponse.t0; });
    if (request == m_requests.end())
    {
        return ClockResult::UnknownT0;
    }
    const double t0 = request->localMs;
    m_requests.erase(request);
    const double t3 = aLocalMs;
    if (t3 - t0 > m_config.requestTimeoutMs)
    {
        return ClockResult::UnknownT0;
    }
    if (t3 < t0)
    {
        return ClockResult::Invalid;
    }
    const double near = m_offset ? t0 + *m_offset : static_cast<double>(aResponse.t1);
    const double t1 = UnwrapU32Near(aResponse.t1, near);
    const auto hold = static_cast<double>(static_cast<int32_t>(aResponse.t2 - aResponse.t1));
    if (hold < 0.0 || hold > (t3 - t0) + m_config.relayStampResolutionMs)
    {
        return ClockResult::Invalid;
    }
    return OnExchange(t0, t1, t1 + hold, t3) ? ClockResult::Stepped : ClockResult::Accepted;
}

// ---- core ----------------------------------------------------------------------------------------

bool ClockSync::OnExchange(double aT0, double aT1, double aT2, double aT3)
{
    Sample sample;
    sample.low = aT2 - aT3;
    sample.high = aT1 + m_config.relayStampResolutionMs - aT0;
    if (sample.high < sample.low)
    {
        // Impossible stamps (relay hold longer than the round trip): keep only their midpoint.
        sample.low = sample.high = (sample.low + sample.high) / 2.0;
    }
    sample.rtt = std::max(0.0, (aT3 - aT0) - (aT2 - aT1));
    sample.at = aT3;
    m_samples.push_back(sample);
    while (m_samples.size() > m_config.window)
    {
        m_samples.pop_front();
    }

    const std::optional<double> previous = m_offset;
    Estimate(aT3);
    const double estimate = *m_estimate;
    const bool warming = m_samples.size() < m_config.warmup;
    if (!m_offset || warming || std::fabs(estimate - *m_offset) > m_config.stepThresholdMs)
    {
        m_offset = estimate;
    }
    else
    {
        m_offset = *m_offset + std::clamp(estimate - *m_offset, -m_config.maxSlewMs, m_config.maxSlewMs);
    }
    const bool stepped = previous && std::fabs(*m_offset - *previous) > m_config.stepNotifyMs;
    if (stepped)
    {
        ++m_steps;
    }
    return stepped;
}

void ClockSync::Estimate(double aNowMs)
{
    const size_t count = m_samples.size();
    std::vector<double> lows(count);
    std::vector<double> highs(count);
    double minRtt = std::numeric_limits<double>::infinity();
    for (size_t index = 0; index < count; ++index)
    {
        const Sample& sample = m_samples[index];
        const double widen = m_config.driftPpm * 1e-6 * std::max(0.0, aNowMs - sample.at);
        lows[index] = sample.low - widen;
        highs[index] = sample.high + widen;
        minRtt = std::min(minRtt, sample.rtt);
    }
    // Marzullo: the region shared by the most intervals starts at some interval's low end. Ties go
    // to the region that includes the newest exchange, then to the narrower one.
    size_t bestCount = 0;
    size_t bestNewest = 0;
    double bestLow = 0.0;
    double bestHigh = 0.0;
    for (size_t candidate = 0; candidate < count; ++candidate)
    {
        const double left = lows[candidate];
        double right = std::numeric_limits<double>::infinity();
        size_t members = 0;
        size_t newest = 0;
        for (size_t index = 0; index < count; ++index)
        {
            if (lows[index] <= left && left <= highs[index])
            {
                ++members;
                right = std::min(right, highs[index]);
                newest = std::max(newest, index);
            }
        }
        const bool better = members > bestCount ||
                            (members == bestCount && (newest > bestNewest ||
                                                      (newest == bestNewest && right - left < bestHigh - bestLow)));
        if (better)
        {
            bestCount = members;
            bestNewest = newest;
            bestLow = left;
            bestHigh = right;
        }
    }
    m_estimate = (bestLow + bestHigh) / 2.0;
    m_bound = (bestHigh - bestLow) / 2.0;
    m_agreeing = bestCount;
    m_minRtt = minRtt;
}

double ClockSync::RelayMs(double aLocalMs) const
{
    return aLocalMs + m_offset.value_or(0.0);
}

double ClockSync::UnwrapRelayMs(uint32_t aStamp, double aLocalMs) const
{
    return UnwrapU32Near(aStamp, RelayMs(aLocalMs));
}
} // namespace coopnet::v2
