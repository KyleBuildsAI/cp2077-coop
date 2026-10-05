#include "v2/V2Reliability.hpp"

#include <algorithm>
#include <cmath>
#include <utility>

namespace coopnet::v2
{
namespace
{
constexpr double kMinRttVarTerm = 0.01; // reliability.py: max(4 * rttvar, 0.01)

// Steps from aFrom to aTo along the packet sequence order 1, 2, ..., 65535, 1, ... (0 skipped).
size_t PacketSteps(uint16_t aFrom, uint16_t aTo)
{
    size_t steps = static_cast<uint16_t>(aTo - aFrom);
    if (aTo < aFrom)
    {
        --steps; // crossed 65535 -> 1
    }
    return steps;
}

ByteSpan AsSpan(const Bytes& aBytes)
{
    return {aBytes.data(), aBytes.size()};
}
} // namespace

int32_t SeqDiff(uint16_t aNewer, uint16_t aOlder)
{
    return static_cast<int32_t>((static_cast<uint32_t>(aNewer) - aOlder + 32768u) % kSeqSpace) - 32768;
}

uint16_t NextPacketSeq(uint16_t aSeq)
{
    const auto next = static_cast<uint16_t>(aSeq + 1);
    return next == 0 ? uint16_t{1} : next;
}

const char* ToString(QueueResult aResult)
{
    switch (aResult)
    {
    case QueueResult::Queued:
        return "Queued";
    case QueueResult::WindowFull:
        return "WindowFull";
    case QueueResult::TooLarge:
        return "TooLarge";
    case QueueResult::BadType:
        return "BadType";
    }
    return "?";
}

Connection::Connection(uint64_t aToken, size_t aMaxPacket)
    : m_token(aToken)
    , m_maxPacket(std::clamp<size_t>(aMaxPacket, kPacketHeaderSize + MessageSize(0, true), coopv2::kMaxPacket))
{
}

bool Connection::SetStartSequences(uint16_t aNextSeq, uint16_t aRelNext, uint16_t aRelExpected)
{
    if (m_started)
    {
        return false;
    }
    m_nextSeq = aNextSeq == 0 ? uint16_t{1} : aNextSeq;
    m_relNext = aRelNext;
    m_relExpected = aRelExpected;
    return true;
}

// ---- send ----------------------------------------------------------------------------------------

QueueResult Connection::QueueReliable(uint8_t aType, uint8_t aPeer, ByteSpan aBody, double aNow)
{
    if (aType >= coopv2::kReliableBit)
    {
        return QueueResult::BadType;
    }
    if (aBody.size() > kMaxMessageBody)
    {
        return QueueResult::TooLarge;
    }
    if (!m_pending.empty() && SeqDiff(m_relNext, m_pending.front().relSeq) >= static_cast<int32_t>(kReliableWindow))
    {
        return QueueResult::WindowFull;
    }
    Pending pending;
    pending.relSeq = m_relNext;
    pending.type = aType;
    pending.peer = aPeer;
    pending.body.assign(aBody.begin(), aBody.end());
    pending.queuedAt = aNow;
    m_pending.push_back(std::move(pending));
    ++m_pendingLive;
    m_relNext = static_cast<uint16_t>(m_relNext + 1);
    m_started = true;
    return QueueResult::Queued;
}

double Connection::ResendInterval(const Pending& aPending) const
{
    const uint32_t shift = std::min(aPending.sends > 0 ? aPending.sends - 1 : 0u, kMaxBackoffShift);
    return m_rto * static_cast<double>(1u << shift);
}

bool Connection::IsDue(const Pending& aPending, double aNow) const
{
    return !aPending.lastSent || aNow - *aPending.lastSent >= ResendInterval(aPending);
}

bool Connection::ReliableDue(double aNow) const
{
    return std::any_of(m_pending.begin(), m_pending.end(),
                       [&](const Pending& aPending) { return !aPending.acked && IsDue(aPending, aNow); });
}

std::optional<double> Connection::NextReliableDue() const
{
    std::optional<double> earliest;
    for (const Pending& pending : m_pending)
    {
        if (pending.acked)
        {
            continue;
        }
        const double due = pending.lastSent ? *pending.lastSent + ResendInterval(pending) : pending.queuedAt;
        if (!earliest || due < *earliest)
        {
            earliest = due;
        }
    }
    return earliest;
}

Status Connection::BuildPackets(double aNow, std::span<const LinkMessage> aUnreliable, bool aForce,
                                std::vector<Bytes>& aOut)
{
    for (const LinkMessage& message : aUnreliable)
    {
        if (message.type >= coopv2::kReliableBit)
        {
            return Status::BadValue;
        }
        if (message.body.size() > kMaxMessageBody)
        {
            return Status::TooLarge;
        }
    }
    std::vector<size_t> due;
    for (size_t index = 0; index < m_pending.size(); ++index)
    {
        if (!m_pending[index].acked && IsDue(m_pending[index], aNow))
        {
            due.push_back(index);
        }
    }
    Bytes payload;
    std::vector<size_t> relItems;
    size_t size = kPacketHeaderSize;
    size_t parts = 0;
    size_t built = 0;
    const auto add = [&](uint8_t aType, uint8_t aPeer, std::optional<uint16_t> aRelSeq, ByteSpan aBody,
                         std::optional<size_t> aPendingIndex) {
        const size_t encoded = MessageSize(aBody.size(), aRelSeq.has_value());
        if (parts > 0 && (size + encoded > m_maxPacket || parts >= kMaxMessagesPerPacket))
        {
            FinishPacket(aNow, payload, relItems, aOut);
            ++built;
            payload.clear();
            relItems.clear();
            size = kPacketHeaderSize;
            parts = 0;
        }
        AppendMessage(payload, aType, aPeer, aRelSeq, aBody); // inputs were validated above
        size += encoded;
        ++parts;
        if (aPendingIndex)
        {
            relItems.push_back(*aPendingIndex);
        }
    };
    for (const size_t index : due)
    {
        const Pending& pending = m_pending[index];
        add(pending.type, pending.peer, pending.relSeq, AsSpan(pending.body), index);
    }
    for (const LinkMessage& message : aUnreliable)
    {
        add(message.type, message.peer, std::nullopt, message.body, std::nullopt);
    }
    if (parts > 0 || (aForce && built == 0))
    {
        FinishPacket(aNow, payload, relItems, aOut);
    }
    return Status::Ok;
}

void Connection::FinishPacket(double aNow, const Bytes& aPayload, const std::vector<size_t>& aRelItems,
                              std::vector<Bytes>& aOut)
{
    const uint16_t seq = m_nextSeq;
    m_nextSeq = NextPacketSeq(seq);
    Bytes packet;
    const PacketInfo header{static_cast<uint8_t>(coopv2::PacketType::Data), m_token, seq, m_remoteSeq, m_recvBits};
    EncodePacket(header, AsSpan(aPayload), packet); // at most kMaxPacket by construction
    SentPacket sent;
    sent.seq = seq;
    sent.time = aNow;
    sent.size = packet.size();
    sent.relSeqs.reserve(aRelItems.size());
    for (const size_t index : aRelItems)
    {
        Pending& pending = m_pending[index];
        if (pending.sends == 0)
        {
            ++m_stats.reliableSent;
        }
        else
        {
            ++m_stats.reliableResent;
        }
        ++pending.sends;
        pending.lastSent = aNow;
        sent.relSeqs.push_back(pending.relSeq);
    }
    m_sent.push_back(std::move(sent));
    ++m_sentLive;
    m_ackPending = false;
    m_lastSend = aNow;
    m_started = true;
    ++m_stats.packetsSent;
    m_stats.bytesSent += packet.size();
    aOut.push_back(std::move(packet));
    ExpireSent(aNow);
}

void Connection::DropSentFront()
{
    while (!m_sent.empty() && !m_sent.front().live)
    {
        m_sent.pop_front();
    }
}

void Connection::ExpireSent(double aNow)
{
    DropSentFront();
    while (!m_sent.empty())
    {
        SentPacket& oldest = m_sent.front();
        if (aNow - oldest.time <= kSentExpiryS && m_sentLive <= kSentHistoryLimit && m_sent.size() <= kSentSpanLimit)
        {
            break;
        }
        oldest.live = false;
        --m_sentLive;
        ++m_stats.packetsLost;
        DropSentFront();
    }
}

Connection::SentPacket* Connection::FindSent(uint16_t aSeq)
{
    if (m_sent.empty())
    {
        return nullptr;
    }
    const size_t index = PacketSteps(m_sent.front().seq, aSeq);
    if (index >= m_sent.size())
    {
        return nullptr;
    }
    SentPacket& packet = m_sent[index];
    return packet.live && packet.seq == aSeq ? &packet : nullptr;
}

Connection::Pending* Connection::FindPending(uint16_t aRelSeq)
{
    if (m_pending.empty())
    {
        return nullptr;
    }
    const size_t index = static_cast<uint16_t>(aRelSeq - m_pending.front().relSeq);
    if (index >= m_pending.size())
    {
        return nullptr;
    }
    Pending& pending = m_pending[index];
    return !pending.acked && pending.relSeq == aRelSeq ? &pending : nullptr;
}

void Connection::DropAckedPendingFront()
{
    while (!m_pending.empty() && m_pending.front().acked)
    {
        m_pending.pop_front();
    }
}

// ---- receive -------------------------------------------------------------------------------------

Status Connection::OnPacket(double aNow, const PacketInfo& aHeader, ByteSpan aBody, std::vector<DeliveredMessage>& aOut,
                            size_t aSize)
{
    std::vector<MessageView> messages;
    const Status framing = DecodeMessages(aBody, messages);
    if (framing != Status::Ok)
    {
        return framing;
    }
    if (aHeader.seq == 0)
    {
        return Status::BadValue; // packet sequence 0 is reserved
    }
    m_ackPending = true;
    m_started = true;
    const uint16_t previous = m_remoteSeq;
    if (!RecordReceived(aHeader.seq))
    {
        return Status::Ok;
    }
    ++m_stats.packetsReceived;
    m_stats.bytesReceived += aSize != 0 ? aSize : aBody.size() + kPacketHeaderSize;
    m_lastRecv = aNow;
    const bool inOrder = previous != 0 && aHeader.seq == NextPacketSeq(previous);
    ProcessAcks(aNow, aHeader.ack, aHeader.ackBits, inOrder);
    for (const MessageView& message : messages)
    {
        if (!message.reliable)
        {
            aOut.push_back(DeliveredMessage{message.type, message.peer, false, Bytes(message.body.begin(), message.body.end())});
        }
        else
        {
            ReceiveReliable(message.relSeq, message.type, message.peer, message.body, aOut);
        }
    }
    return Status::Ok;
}

bool Connection::RecordReceived(uint16_t aSeq)
{
    if (m_remoteSeq == 0)
    {
        m_remoteSeq = aSeq;
        m_recvBits = 0;
        ++m_stats.recvSpan;
        return true;
    }
    const int32_t distance = SeqDiff(aSeq, m_remoteSeq);
    if (distance > 0)
    {
        if (distance <= static_cast<int32_t>(kAckBitCount))
        {
            const uint64_t shifted = (static_cast<uint64_t>(m_recvBits) << distance) | (1ull << (distance - 1));
            m_recvBits = static_cast<uint32_t>(shifted);
        }
        else
        {
            m_recvBits = 0;
        }
        m_remoteSeq = aSeq;
        m_stats.recvSpan += static_cast<uint64_t>(distance);
        return true;
    }
    if (distance == 0)
    {
        ++m_stats.duplicates;
        return false;
    }
    const int32_t bit = -distance - 1;
    if (bit >= static_cast<int32_t>(kAckBitCount))
    {
        ++m_stats.tooOld;
        return false;
    }
    if (m_recvBits & (1u << bit))
    {
        ++m_stats.duplicates;
        return false;
    }
    m_recvBits |= 1u << bit;
    return true;
}

void Connection::ProcessAcks(double aNow, uint16_t aAck, uint32_t aAckBits, bool aSampleRtt)
{
    if (aAck == 0)
    {
        return;
    }
    if (SentPacket* packet = FindSent(aAck))
    {
        OnAcked(aNow, *packet, aSampleRtt);
    }
    for (uint32_t bit = 0; bit < kAckBitCount; ++bit)
    {
        if (aAckBits & (1u << bit))
        {
            if (SentPacket* packet = FindSent(static_cast<uint16_t>(aAck - 1 - bit)))
            {
                OnAcked(aNow, *packet, aSampleRtt);
            }
        }
    }
    // Packets more than 32 behind the newest ack can no longer be acknowledged: count them lost.
    for (SentPacket& packet : m_sent)
    {
        if (packet.live && SeqDiff(aAck, packet.seq) > static_cast<int32_t>(kAckBitCount))
        {
            packet.live = false;
            --m_sentLive;
            ++m_stats.packetsLost;
        }
    }
    DropSentFront();
    DropAckedPendingFront();
}

void Connection::OnAcked(double aNow, SentPacket& aPacket, bool aSampleRtt)
{
    aPacket.live = false;
    --m_sentLive;
    ++m_stats.packetsAcked;
    if (aSampleRtt)
    {
        ++m_stats.rttSamples;
        UpdateRtt(aNow - aPacket.time);
    }
    else
    {
        ++m_stats.rttSkipped;
    }
    for (const uint16_t relSeq : aPacket.relSeqs)
    {
        if (Pending* pending = FindPending(relSeq))
        {
            pending->acked = true;
            pending->body = Bytes();
            --m_pendingLive;
        }
    }
}

void Connection::UpdateRtt(double aSample)
{
    m_minSample = m_minSample ? std::min(*m_minSample, aSample) : aSample;
    m_maxSample = m_maxSample ? std::max(*m_maxSample, aSample) : aSample;
    // Same operations in the same order as reliability.py, so the floats match bit for bit.
    if (!m_srtt)
    {
        m_srtt = aSample;
        m_rttvar = aSample / 2.0;
    }
    else
    {
        m_rttvar = 0.75 * *m_rttvar + 0.25 * std::fabs(*m_srtt - aSample);
        m_srtt = 0.875 * *m_srtt + 0.125 * aSample;
    }
    const double rto = *m_srtt + std::max(4.0 * *m_rttvar, kMinRttVarTerm) + kAckDelayAllowanceS;
    m_rto = std::min(kMaxRtoS, std::max(kMinRtoS, rto));
}

void Connection::ReceiveReliable(uint16_t aRelSeq, uint8_t aType, uint8_t aPeer, ByteSpan aBody,
                                 std::vector<DeliveredMessage>& aOut)
{
    const int32_t distance = SeqDiff(aRelSeq, m_relExpected);
    Slot& slot = m_relBuffer[aRelSeq % kReliableWindow];
    if (distance < 0 || (slot.used && slot.relSeq == aRelSeq))
    {
        ++m_stats.reliableDuplicates;
        return;
    }
    if (distance >= static_cast<int32_t>(kReliableWindow))
    {
        ++m_stats.reliableOutOfWindow;
        return;
    }
    slot.used = true;
    slot.relSeq = aRelSeq;
    slot.type = aType;
    slot.peer = aPeer;
    slot.body.assign(aBody.begin(), aBody.end());
    ++m_buffered;
    for (;;)
    {
        Slot& next = m_relBuffer[m_relExpected % kReliableWindow];
        if (!next.used || next.relSeq != m_relExpected)
        {
            break;
        }
        aOut.push_back(DeliveredMessage{next.type, next.peer, true, std::move(next.body)});
        next = Slot{};
        --m_buffered;
        ++m_stats.reliableDelivered;
        m_relExpected = static_cast<uint16_t>(m_relExpected + 1);
    }
}

// ---- stats ---------------------------------------------------------------------------------------

double Connection::LossIn() const
{
    if (m_stats.recvSpan == 0)
    {
        return 0.0;
    }
    return std::max(0.0, 1.0 - static_cast<double>(m_stats.packetsReceived) / static_cast<double>(m_stats.recvSpan));
}

double Connection::LossOut() const
{
    const uint64_t judged = m_stats.packetsAcked + m_stats.packetsLost;
    return judged == 0 ? 0.0 : static_cast<double>(m_stats.packetsLost) / static_cast<double>(judged);
}

double Connection::RttMs() const
{
    return m_srtt ? *m_srtt * 1000.0 : 0.0;
}

std::optional<double> Connection::Srtt() const
{
    return m_srtt;
}

std::optional<double> Connection::Rttvar() const
{
    return m_rttvar;
}

double Connection::Rto() const
{
    return m_rto;
}
} // namespace coopnet::v2
