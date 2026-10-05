#pragma once

// One hop of a protocol v2 session (client <-> relay): packet sequences and acks, RTT and RTO,
// unreliable messages and the reliable ordered stream.
//
// Port of relay/coopnet/reliability.py, which stays the reference. tools/v2_link_trace.py records
// every call and result of the Python Connection in its own test scenarios (0/10/30/45 % loss,
// duplicates, reordering, bursts, sequence wrap) and coopnet_v2_reliability_tests replays them:
// the C++ Connection must emit the same datagrams byte for byte, deliver the same messages and end
// every step in the same state, RTT floats included.
//
// Delivery model (per hop; the relay re-sequences for every receiver):
// * Every DATA packet has a 16-bit sequence (0 is never used, so ack == 0 means "nothing received
//   yet") and acknowledges the newest packet seen from the other side plus the 32 before it.
// * Unreliable messages are sent once.
// * Reliable messages carry a 16-bit message sequence, stay queued until a packet that contained
//   them is acknowledged, are resent after an RTO with exponential backoff (at most 8 x), and are
//   delivered exactly once, in order. A resend always goes out in a new packet with a new
//   sequence, so an ack names exactly one transmission (no Karn ambiguity).
// * RTT lesson from the CPN2 prototype: an ack arriving after a gap can be stale. An RTT sample is
//   only taken from acks carried by a packet that directly follows the previous packet received
//   from the other side. After a lost or reordered packet, the first ack that gets through can
//   cover packets whose earlier acks were lost, and their apparent RTT includes the time the gap
//   stayed open (CPN2 read 9.2 s on a 200 ms link from such acks). reliability.py does the same.
// * A DATA packet holds at most kMaxMessagesPerPacket messages.
//
// Times are seconds as double (any monotonic origin), the same values reliability.py uses. The
// class is not thread-safe: the transport's network thread owns it.

#include "v2/V2Codec.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <optional>
#include <span>
#include <vector>

namespace coopnet::v2
{
inline constexpr uint32_t kSeqSpace = 65536;
inline constexpr uint32_t kAckBitCount = 32;
inline constexpr size_t kReliableWindow = 256;
inline constexpr size_t kSentHistoryLimit = 2048;
// C++ only: the sent-packet history (acked entries included) never spans more than this many
// sequences, so 16-bit distances inside it stay unambiguous. Only reachable above ~2700 packets/s.
inline constexpr size_t kSentSpanLimit = 8192;
inline constexpr double kSentExpiryS = 3.0;
inline constexpr double kInitialRtoS = 0.25;
inline constexpr double kMinRtoS = 0.05;
inline constexpr double kMaxRtoS = 2.0;
inline constexpr double kAckDelayAllowanceS = 0.04;
inline constexpr uint32_t kMaxBackoffShift = 3;

// Signed distance aNewer - aOlder in 16-bit serial arithmetic (RFC 1982), in [-32768, 32767].
int32_t SeqDiff(uint16_t aNewer, uint16_t aOlder);
// The packet sequence after aSeq. Packet sequences skip 0.
uint16_t NextPacketSeq(uint16_t aSeq);

// reliability.py LinkStats, plus the RTT sample counters both sides keep.
struct LinkStats
{
    uint64_t packetsSent = 0;
    uint64_t packetsReceived = 0;
    uint64_t packetsAcked = 0;
    uint64_t packetsLost = 0;
    uint64_t duplicates = 0;
    uint64_t tooOld = 0;
    uint64_t bytesSent = 0;
    uint64_t bytesReceived = 0;
    uint64_t reliableSent = 0;
    uint64_t reliableResent = 0;
    uint64_t reliableDelivered = 0;
    uint64_t reliableDuplicates = 0;
    uint64_t reliableOutOfWindow = 0;
    uint64_t recvSpan = 0;
    uint64_t rttSamples = 0;
    uint64_t rttSkipped = 0; // acks carried by a packet that followed a gap
};

// An unreliable message to send: type (below 0x80), header peer, body.
struct LinkMessage
{
    uint8_t type = 0;
    uint8_t peer = 0;
    ByteSpan body;
};

struct DeliveredMessage
{
    uint8_t type = 0;
    uint8_t peer = 0;
    bool reliable = false;
    Bytes body;
};

enum class QueueResult : uint8_t
{
    Queued,
    WindowFull, // kReliableWindow messages are unacknowledged; try again later
    TooLarge,   // body above kMaxMessageBody
    BadType,    // type with the reliable bit set
};

class Connection
{
public:
    explicit Connection(uint64_t aToken = 0, size_t aMaxPacket = coopv2::kMaxPacket);

    // ---- send ----------------------------------------------------------------------------------

    QueueResult QueueReliable(uint8_t aType, uint8_t aPeer, ByteSpan aBody, double aNow);
    // Whether a reliable message is waiting for its first send or its resend timer expired.
    [[nodiscard]] bool ReliableDue(double aNow) const;
    // The earliest time a reliable message is (or was) due; nullopt when none is queued.
    [[nodiscard]] std::optional<double> NextReliableDue() const;
    // Packs the due reliable messages, then aUnreliable, into DATA datagrams appended to aOut.
    // aForce sends an ack-only packet when nothing else goes out. Returns TooLarge or BadValue,
    // with no state changed and nothing appended, when an unreliable message is invalid.
    Status BuildPackets(double aNow, std::span<const LinkMessage> aUnreliable, bool aForce, std::vector<Bytes>& aOut);

    // ---- receive -------------------------------------------------------------------------------

    // One DATA packet: aHeader and aBody from DecodePacket, aSize the datagram size (0 = header +
    // body). Appends the messages to deliver, in order. A framing error or sequence 0 returns a
    // Status other than Ok before any state changes. Duplicates and packets too old to judge
    // return Ok and deliver nothing.
    Status OnPacket(double aNow, const PacketInfo& aHeader, ByteSpan aBody, std::vector<DeliveredMessage>& aOut,
                    size_t aSize = 0);

    // ---- state and stats -----------------------------------------------------------------------

    [[nodiscard]] double LossIn() const;
    [[nodiscard]] double LossOut() const;
    [[nodiscard]] double RttMs() const; // smoothed, 0 before the first sample
    [[nodiscard]] std::optional<double> Srtt() const;
    [[nodiscard]] std::optional<double> Rttvar() const;
    [[nodiscard]] double Rto() const;
    // Smallest and largest RTT sample taken so far (seconds; diagnostics, not in reliability.py).
    [[nodiscard]] std::optional<double> MinRttSample() const
    {
        return m_minSample;
    }
    [[nodiscard]] std::optional<double> MaxRttSample() const
    {
        return m_maxSample;
    }

    [[nodiscard]] uint64_t Token() const
    {
        return m_token;
    }
    [[nodiscard]] size_t MaxPacket() const
    {
        return m_maxPacket;
    }
    [[nodiscard]] uint16_t NextSeq() const
    {
        return m_nextSeq;
    }
    [[nodiscard]] uint16_t RemoteSeq() const
    {
        return m_remoteSeq;
    }
    [[nodiscard]] uint32_t RecvBits() const
    {
        return m_recvBits;
    }
    [[nodiscard]] bool AckPending() const
    {
        return m_ackPending;
    }
    [[nodiscard]] uint16_t RelNext() const
    {
        return m_relNext;
    }
    [[nodiscard]] uint16_t RelExpected() const
    {
        return m_relExpected;
    }
    // Reliable messages queued and not yet acknowledged.
    [[nodiscard]] size_t PendingReliable() const
    {
        return m_pendingLive;
    }
    // Sent packets still waiting for an ack.
    [[nodiscard]] size_t SentAwaitingAck() const
    {
        return m_sentLive;
    }
    // Reliable messages received ahead of a gap, waiting to be delivered.
    [[nodiscard]] size_t BufferedReliable() const
    {
        return m_buffered;
    }
    [[nodiscard]] std::optional<double> LastSend() const
    {
        return m_lastSend;
    }
    [[nodiscard]] std::optional<double> LastRecv() const
    {
        return m_lastRecv;
    }
    [[nodiscard]] const LinkStats& Stats() const
    {
        return m_stats;
    }

    // Starting sequences, as reliability.py's tests set them for the wrap-around scenario. Only
    // allowed before anything was queued, sent or received; returns false otherwise. A packet
    // sequence of 0 becomes 1.
    bool SetStartSequences(uint16_t aNextSeq, uint16_t aRelNext, uint16_t aRelExpected);

private:
    struct Pending
    {
        uint16_t relSeq = 0;
        uint8_t type = 0;
        uint8_t peer = 0;
        Bytes body;
        double queuedAt = 0.0;
        std::optional<double> lastSent;
        uint32_t sends = 0;
        bool acked = false;
    };

    struct SentPacket
    {
        uint16_t seq = 0;
        double time = 0.0;
        std::vector<uint16_t> relSeqs;
        size_t size = 0;
        bool live = true;
    };

    struct Slot
    {
        bool used = false;
        uint16_t relSeq = 0;
        uint8_t type = 0;
        uint8_t peer = 0;
        Bytes body;
    };

    [[nodiscard]] double ResendInterval(const Pending& aPending) const;
    [[nodiscard]] bool IsDue(const Pending& aPending, double aNow) const;
    void FinishPacket(double aNow, const Bytes& aPayload, const std::vector<size_t>& aRelItems, std::vector<Bytes>& aOut);
    void ExpireSent(double aNow);
    void DropSentFront();
    SentPacket* FindSent(uint16_t aSeq);
    Pending* FindPending(uint16_t aRelSeq);
    void DropAckedPendingFront();
    bool RecordReceived(uint16_t aSeq);
    void ProcessAcks(double aNow, uint16_t aAck, uint32_t aAckBits, bool aSampleRtt);
    void OnAcked(double aNow, SentPacket& aPacket, bool aSampleRtt);
    void UpdateRtt(double aSample);
    void ReceiveReliable(uint16_t aRelSeq, uint8_t aType, uint8_t aPeer, ByteSpan aBody,
                         std::vector<DeliveredMessage>& aOut);

    uint64_t m_token;
    size_t m_maxPacket;
    uint16_t m_nextSeq = 1;
    uint16_t m_remoteSeq = 0;
    uint32_t m_recvBits = 0;
    bool m_ackPending = false;
    uint16_t m_relNext = 0;
    uint16_t m_relExpected = 0;

    // Sent packets in send order: consecutive sequences (skipping 0); acked ones stay as
    // tombstones until they reach the front, so a sequence maps to an index.
    std::deque<SentPacket> m_sent;
    size_t m_sentLive = 0;
    // Queued reliable messages in sequence order, same tombstone scheme; the front is never acked.
    std::deque<Pending> m_pending;
    size_t m_pendingLive = 0;
    // Receive buffer for reliable messages ahead of m_relExpected, indexed by relSeq % window.
    std::array<Slot, kReliableWindow> m_relBuffer{};
    size_t m_buffered = 0;

    std::optional<double> m_srtt;
    std::optional<double> m_rttvar;
    double m_rto = kInitialRtoS;
    std::optional<double> m_minSample;
    std::optional<double> m_maxSample;
    std::optional<double> m_lastSend;
    std::optional<double> m_lastRecv;
    bool m_started = false; // anything queued, sent or received
    LinkStats m_stats;
};

const char* ToString(QueueResult aResult);
} // namespace coopnet::v2
