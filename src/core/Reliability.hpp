#pragma once

// Per-peer reliability state. Pure logic (no sockets, no clock) so it can be unit tested
// deterministically; the transport thread feeds it timestamps in microseconds.
//
// Sender: reliable messages get a 16-bit sequence, stay "in flight" until acked, and are resent
// when their retransmission timeout (RTO, from a smoothed RTT estimate, RFC 6298 style) expires,
// or immediately when a selective ack proves a later message overtook them (fast retransmit).
//
// Receiver: out-of-order messages are buffered (window of kWindow) and handed to the
// application strictly in sequence order, exactly once. Acks are cumulative (`ack`) plus a
// 32-bit selective bitfield (`ackBits`, bit i => ack + 2 + i received).

#include "Protocol.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <functional>
#include <span>
#include <string>
#include <string_view>

namespace coopnet
{
class RttEstimator
{
public:
    static constexpr uint64_t kInitialRtoMicros = 500'000;
    static constexpr uint64_t kMinRtoMicros = 60'000;
    static constexpr uint64_t kMaxRtoMicros = 2'000'000;

    void AddSample(uint64_t aRttMicros);

    [[nodiscard]] bool HasSample() const
    {
        return m_hasSample;
    }
    [[nodiscard]] double SmoothedMs() const
    {
        return m_smoothed / 1000.0;
    }
    [[nodiscard]] double VariationMs() const
    {
        return m_variation / 1000.0;
    }
    [[nodiscard]] uint64_t RtoMicros() const;

private:
    bool m_hasSample = false;
    double m_smoothed = 0.0;  // microseconds
    double m_variation = 0.0; // microseconds
};

struct ReliableStats
{
    uint64_t sent = 0;          // first transmissions
    uint64_t resent = 0;        // retransmissions (timeout + fast)
    uint64_t fastResent = 0;    // retransmissions triggered by selective acks
    uint64_t deferred = 0;      // timeouts postponed because the message is beyond the ack horizon
    uint64_t acked = 0;
    uint64_t received = 0;      // unique messages accepted
    uint64_t duplicates = 0;    // already-received copies (resends whose ack was lost)
    uint64_t outOfWindow = 0;   // too far ahead of the receive window; sender will resend
    uint64_t delivered = 0;     // handed to the application
};

class ReliableEndpoint
{
public:
    static constexpr size_t kWindow = 128;
    static constexpr size_t kMaxBacklog = 4096;
    static constexpr size_t kSackSpan = 32;       // sequences covered by the ack bitfield
    static constexpr uint32_t kMaxSendCount = 64; // after this many transmissions the peer is dead
    static constexpr uint32_t kFastRetransmitThreshold = 3;

    // Called with (sequence, channel, payload, isResend) for every frame that must go out now.
    using EmitFn = std::function<void(uint16_t, uint8_t, std::string_view, bool)>;
    // Called with (sequence, channel, payload); returns false if the application queue is full.
    using SinkFn = std::function<bool(uint16_t, uint8_t, std::string_view)>;

    // ---- sender -------------------------------------------------------------------------
    bool Enqueue(uint8_t aChannel, std::string aPayload);
    void CollectDue(uint64_t aNowMicros, const EmitFn& aEmit);
    void OnAck(uint16_t aAck, uint32_t aAckBits, uint64_t aNowMicros);
    // Earliest time something needs (re)sending, or UINT64_MAX when idle.
    [[nodiscard]] uint64_t NextDueMicros() const;

    [[nodiscard]] size_t InFlight() const
    {
        return m_inFlight.size();
    }
    [[nodiscard]] size_t Backlog() const
    {
        return m_backlog.size();
    }
    [[nodiscard]] bool Failed() const
    {
        return m_failed;
    }

    // ---- receiver -----------------------------------------------------------------------
    enum class ReceiveResult
    {
        Accepted,
        Duplicate,
        OutOfWindow
    };
    ReceiveResult OnReceive(uint16_t aSequence, uint8_t aChannel, std::span<const uint8_t> aPayload);
    size_t Deliver(const SinkFn& aSink);

    [[nodiscard]] uint16_t AckValue() const
    {
        return m_ackContiguous;
    }
    [[nodiscard]] uint32_t AckBits() const;
    [[nodiscard]] bool AckPending() const
    {
        return m_ackPending;
    }
    void ClearAckPending()
    {
        m_ackPending = false;
    }
    [[nodiscard]] size_t Buffered() const;

    // ---- shared -------------------------------------------------------------------------
    RttEstimator& Rtt()
    {
        return m_rtt;
    }
    [[nodiscard]] const RttEstimator& Rtt() const
    {
        return m_rtt;
    }
    [[nodiscard]] const ReliableStats& Stats() const
    {
        return m_stats;
    }

private:
    struct PendingMessage
    {
        uint8_t channel;
        std::string payload;
    };

    struct InFlightMessage
    {
        uint16_t sequence;
        uint8_t channel;
        std::string payload;
        uint64_t firstSentMicros;
        uint64_t lastSentMicros;
        uint64_t nextResendMicros;
        uint32_t sendCount;
        uint32_t overtaken;  // later messages acked since this one's latest transmission
        bool fastRetransmit; // overtaken often enough to be considered lost
        bool timedOutBeyondHorizon; // timer expired while the ack bitfield could not cover it
    };

    struct Slot
    {
        bool used = false;
        uint16_t sequence = 0;
        uint8_t channel = 0;
        std::string payload;
    };

    uint64_t ResendDelay() const;
    void AdvanceContiguous();

    std::deque<PendingMessage> m_backlog;
    std::deque<InFlightMessage> m_inFlight;
    uint16_t m_nextSequence = 0;
    uint16_t m_peerAck = 0xFFFF; // highest cumulative ack received from the peer
    uint32_t m_silentTimeouts = 0;  // consecutive timeout rounds without any ack in between
    bool m_ackedSinceTimeout = true;
    bool m_failed = false;

    std::array<Slot, kWindow> m_slots{};
    uint16_t m_delivered = 0xFFFF;     // last sequence handed to the application
    uint16_t m_ackContiguous = 0xFFFF; // every sequence <= this one was received
    bool m_ackPending = false;

    RttEstimator m_rtt;
    ReliableStats m_stats;
};
} // namespace coopnet
