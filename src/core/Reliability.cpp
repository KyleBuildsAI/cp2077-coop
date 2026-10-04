#include "Reliability.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <vector>

namespace coopnet
{
void RttEstimator::AddSample(uint64_t aRttMicros)
{
    const double sample = static_cast<double>(aRttMicros);
    if (!m_hasSample)
    {
        m_smoothed = sample;
        m_variation = sample / 2.0;
        m_hasSample = true;
        return;
    }
    m_variation = 0.75 * m_variation + 0.25 * std::fabs(m_smoothed - sample);
    m_smoothed = 0.875 * m_smoothed + 0.125 * sample;
}

uint64_t RttEstimator::RtoMicros() const
{
    if (!m_hasSample)
    {
        return kInitialRtoMicros;
    }
    constexpr double kMinimumVariationTerm = 20'000.0;
    const double rto = m_smoothed + std::max(4.0 * m_variation, kMinimumVariationTerm);
    return std::clamp(static_cast<uint64_t>(rto), kMinRtoMicros, kMaxRtoMicros);
}

bool ReliableEndpoint::Enqueue(uint8_t aChannel, std::string aPayload)
{
    if (m_backlog.size() >= kMaxBacklog)
    {
        return false;
    }
    m_backlog.push_back({aChannel, std::move(aPayload)});
    return true;
}

uint64_t ReliableEndpoint::ResendDelay() const
{
    // Exponential backoff only while the peer is silent. While acks keep arriving the link is
    // alive and a lost message is simply resent after one RTO; backing off there would stall the
    // ordered stream behind a single unlucky message.
    const uint32_t doublings = std::min<uint32_t>(m_silentTimeouts, 4);
    return std::min(m_rtt.RtoMicros() << doublings, RttEstimator::kMaxRtoMicros);
}

void ReliableEndpoint::CollectDue(uint64_t aNowMicros, const EmitFn& aEmit)
{
    if (m_failed)
    {
        return;
    }

    bool timedOut = false;
    for (size_t index = 0; index < m_inFlight.size(); ++index)
    {
        InFlightMessage& entry = m_inFlight[index];
        if (entry.nextResendMicros > aNowMicros)
        {
            continue;
        }
        // The ack bitfield only reaches kSackSpan sequences past the cumulative ack. Messages
        // beyond that horizon may well be sitting in the receiver's buffer behind a hole, so their
        // timers wait until the hole is repaired instead of flooding duplicates. The oldest
        // message is always eligible, which guarantees progress even when acks go missing.
        const bool beyondHorizon = SequenceDistance(m_peerAck, entry.sequence) > static_cast<int>(kSackSpan + 1);
        if (index > 0 && beyondHorizon && !entry.fastRetransmit)
        {
            // Re-armed when an ack brings it inside the horizon (see OnAck).
            entry.timedOutBeyondHorizon = true;
            entry.nextResendMicros = aNowMicros + ResendDelay();
            ++m_stats.deferred;
            continue;
        }
        if (entry.sendCount >= kMaxSendCount)
        {
            m_failed = true;
            return;
        }
        if (entry.fastRetransmit)
        {
            ++m_stats.fastResent;
            entry.fastRetransmit = false;
        }
        else
        {
            timedOut = true;
        }
        entry.overtaken = 0;
        entry.timedOutBeyondHorizon = false;
        ++entry.sendCount;
        entry.lastSentMicros = aNowMicros;
        entry.nextResendMicros = aNowMicros + ResendDelay();
        ++m_stats.resent;
        aEmit(entry.sequence, entry.channel, entry.payload, true);
    }

    if (timedOut)
    {
        m_silentTimeouts = m_ackedSinceTimeout ? 0 : m_silentTimeouts + 1;
        m_ackedSinceTimeout = false;
    }

    while (!m_backlog.empty())
    {
        if (!m_inFlight.empty() &&
            SequenceDistance(m_inFlight.front().sequence, m_nextSequence) >= static_cast<int>(kWindow))
        {
            break;
        }
        PendingMessage pending = std::move(m_backlog.front());
        m_backlog.pop_front();

        InFlightMessage entry{m_nextSequence++, pending.channel,    std::move(pending.payload),
                              aNowMicros,       aNowMicros,         aNowMicros + ResendDelay(),
                              1,                0,                  false,
                              false};
        ++m_stats.sent;
        aEmit(entry.sequence, entry.channel, entry.payload, false);
        m_inFlight.push_back(std::move(entry));
    }
}

void ReliableEndpoint::OnAck(uint16_t aAck, uint32_t aAckBits, uint64_t aNowMicros)
{
    const uint16_t lastAssigned = static_cast<uint16_t>(m_nextSequence - 1);
    if (SequenceGreater(aAck, lastAssigned))
    {
        return; // acknowledges something we never sent: corrupt or foreign frame
    }
    if (SequenceGreater(aAck, m_peerAck))
    {
        m_peerAck = aAck;
    }
    m_ackedSinceTimeout = true;
    m_silentTimeouts = 0;
    if (m_inFlight.empty())
    {
        return;
    }

    auto isAcked = [aAck, aAckBits](uint16_t aSequence)
    {
        if (!SequenceGreater(aSequence, aAck))
        {
            return true;
        }
        const int distance = SequenceDistance(aAck, aSequence);
        return distance >= 2 && distance < static_cast<int>(kSackSpan + 2) &&
               ((aAckBits >> (distance - 2)) & 1u) != 0;
    };

    struct AckedSend
    {
        uint16_t sequence;
        uint64_t firstSentMicros;
    };
    std::vector<AckedSend> newlyAcked;

    for (auto it = m_inFlight.begin(); it != m_inFlight.end();)
    {
        if (!isAcked(it->sequence))
        {
            ++it;
            continue;
        }
        // No RTT sample here: a cumulative ack that jumps after a hole is repaired acknowledges
        // messages sent long ago and would inflate the estimate. The transport feeds the
        // estimator from PING/PONG round trips instead.
        newlyAcked.push_back({it->sequence, it->firstSentMicros});
        ++m_stats.acked;
        it = m_inFlight.erase(it);
    }

    // A message whose timer already expired while it was beyond the horizon, and which the
    // receiver's latest report (now covering it) still lists as missing, is lost: resend now.
    for (auto& entry : m_inFlight)
    {
        const bool insideHorizon = SequenceDistance(m_peerAck, entry.sequence) <= static_cast<int>(kSackSpan + 1);
        if (entry.timedOutBeyondHorizon && insideHorizon)
        {
            entry.timedOutBeyondHorizon = false;
            entry.nextResendMicros = aNowMicros;
        }
    }

    if (newlyAcked.empty())
    {
        return;
    }

    // Fast retransmit (TCP-style duplicate threshold): once kFastRetransmitThreshold messages
    // that were first sent after this one's latest transmission have been acknowledged, this
    // transmission is considered lost and is resent without waiting for its timeout. Within one
    // tick resends go out before new messages, both in sequence order, so for equal timestamps a
    // lower sequence means "sent earlier".
    for (auto& entry : m_inFlight)
    {
        for (const auto& acked : newlyAcked)
        {
            const bool overtook = acked.firstSentMicros > entry.lastSentMicros ||
                                  (acked.firstSentMicros == entry.lastSentMicros &&
                                   SequenceGreater(acked.sequence, entry.sequence));
            if (overtook)
            {
                ++entry.overtaken;
            }
        }
        if (entry.overtaken >= kFastRetransmitThreshold && !entry.fastRetransmit)
        {
            // RACK-style reordering window: a message overtaken by a quarter RTT's worth of
            // reordering is not lost yet. Resend once it has been outstanding for SRTT * 1.25.
            uint64_t deadline = aNowMicros;
            if (m_rtt.HasSample())
            {
                const auto smoothed = static_cast<uint64_t>(m_rtt.SmoothedMs() * 1000.0);
                deadline = std::max(aNowMicros, entry.lastSentMicros + smoothed + smoothed / 4);
            }
            entry.fastRetransmit = true;
            entry.nextResendMicros = std::min(entry.nextResendMicros, deadline);
        }
    }
}

uint64_t ReliableEndpoint::NextDueMicros() const
{
    if (m_failed)
    {
        return std::numeric_limits<uint64_t>::max();
    }
    uint64_t next = std::numeric_limits<uint64_t>::max();
    const bool windowOpen = m_inFlight.empty() ||
                            SequenceDistance(m_inFlight.front().sequence, m_nextSequence) < static_cast<int>(kWindow);
    if (!m_backlog.empty() && windowOpen)
    {
        return 0;
    }
    for (const auto& entry : m_inFlight)
    {
        next = std::min(next, entry.nextResendMicros);
    }
    return next;
}

ReliableEndpoint::ReceiveResult ReliableEndpoint::OnReceive(uint16_t aSequence, uint8_t aChannel,
                                                            std::span<const uint8_t> aPayload)
{
    m_ackPending = true;

    const int distance = SequenceDistance(m_delivered, aSequence);
    if (distance <= 0)
    {
        ++m_stats.duplicates;
        return ReceiveResult::Duplicate;
    }
    if (distance > static_cast<int>(kWindow))
    {
        ++m_stats.outOfWindow;
        return ReceiveResult::OutOfWindow;
    }

    Slot& slot = m_slots[aSequence % kWindow];
    if (slot.used)
    {
        ++m_stats.duplicates;
        return ReceiveResult::Duplicate;
    }
    slot.used = true;
    slot.sequence = aSequence;
    slot.channel = aChannel;
    slot.payload.assign(reinterpret_cast<const char*>(aPayload.data()), aPayload.size());
    ++m_stats.received;
    AdvanceContiguous();
    return ReceiveResult::Accepted;
}

void ReliableEndpoint::AdvanceContiguous()
{
    for (;;)
    {
        const uint16_t next = static_cast<uint16_t>(m_ackContiguous + 1);
        if (SequenceDistance(m_delivered, next) > static_cast<int>(kWindow))
        {
            return;
        }
        const Slot& slot = m_slots[next % kWindow];
        if (!slot.used || slot.sequence != next)
        {
            return;
        }
        m_ackContiguous = next;
    }
}

size_t ReliableEndpoint::Deliver(const SinkFn& aSink)
{
    size_t count = 0;
    for (;;)
    {
        const uint16_t next = static_cast<uint16_t>(m_delivered + 1);
        Slot& slot = m_slots[next % kWindow];
        if (!slot.used || slot.sequence != next)
        {
            break;
        }
        if (!aSink(next, slot.channel, slot.payload))
        {
            break;
        }
        slot.used = false;
        slot.payload.clear();
        slot.payload.shrink_to_fit();
        m_delivered = next;
        ++m_stats.delivered;
        ++count;
    }
    return count;
}

uint32_t ReliableEndpoint::AckBits() const
{
    uint32_t bits = 0;
    for (uint32_t index = 0; index < 32; ++index)
    {
        const uint16_t sequence = static_cast<uint16_t>(m_ackContiguous + 2 + index);
        if (SequenceDistance(m_delivered, sequence) > static_cast<int>(kWindow))
        {
            break;
        }
        const Slot& slot = m_slots[sequence % kWindow];
        if (slot.used && slot.sequence == sequence)
        {
            bits |= 1u << index;
        }
    }
    return bits;
}

size_t ReliableEndpoint::Buffered() const
{
    return static_cast<size_t>(std::count_if(m_slots.begin(), m_slots.end(), [](const Slot& aSlot) { return aSlot.used; }));
}
} // namespace coopnet
