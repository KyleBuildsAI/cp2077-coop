// Tests for the v2 Connection (src/v2/V2Reliability), the port of relay/coopnet/reliability.py.
//
//   coopnet_v2_reliability_tests                 the ported tests/test_reliability.py cases plus
//                                                 C++ additions (reordering, a 70,000 message run)
//   coopnet_v2_reliability_tests trace <file>    replay a reliability.py trace (tools/v2_link_trace.py)
//
// The trace replay is the port's fidelity check: same datagrams byte for byte, same deliveries,
// same state after every step. The native tests check the delivery guarantees on their own, with
// this file's random number generator, so they do not depend on Python.

#include "V2TestSupport.hpp"
#include "v2/V2Codec.hpp"
#include "v2/V2Describe.hpp"
#include "v2/V2Reliability.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <queue>
#include <set>
#include <sstream>
#include <string>
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

ByteSpan AsSpan(const Bytes& aBytes)
{
    return {aBytes.data(), aBytes.size()};
}

Bytes BytesOf(std::string_view aText)
{
    return Bytes(aText.begin(), aText.end());
}

// ---- in-memory link and the two-connection simulation of tests/test_reliability.py --------------

class LossyPipe
{
public:
    LossyPipe(test::Rng& aRng, double aLoss, double aDup, double aBaseDelay, double aJitter)
        : m_rng(aRng)
        , m_loss(aLoss)
        , m_dup(aDup)
        , m_baseDelay(aBaseDelay)
        , m_jitter(aJitter)
    {
    }

    void Push(double aNow, const Bytes& aPacket)
    {
        if (m_rng.Unit() < m_loss)
        {
            return;
        }
        const int copies = m_rng.Unit() < m_dup ? 2 : 1;
        for (int copy = 0; copy < copies; ++copy)
        {
            m_queue.push(Entry{aNow + m_baseDelay + m_rng.Unit() * m_jitter, ++m_counter, aPacket});
        }
    }

    std::vector<Bytes> Pop(double aNow)
    {
        std::vector<Bytes> due;
        while (!m_queue.empty() && m_queue.top().due <= aNow)
        {
            due.push_back(m_queue.top().packet);
            m_queue.pop();
        }
        return due;
    }

private:
    struct Entry
    {
        double due;
        uint64_t counter;
        Bytes packet;
        bool operator>(const Entry& aOther) const
        {
            return due != aOther.due ? due > aOther.due : counter > aOther.counter;
        }
    };

    test::Rng& m_rng;
    double m_loss;
    double m_dup;
    double m_baseDelay;
    double m_jitter;
    uint64_t m_counter = 0;
    std::priority_queue<Entry, std::vector<Entry>, std::greater<>> m_queue;
};

struct Received
{
    std::vector<Bytes> reliable;
    std::vector<Bytes> unreliable;
    int framingErrors = 0;
};

void Deliver(Connection& aConnection, double aNow, const Bytes& aPacket, Received& aSink)
{
    DecodedPacket decoded;
    if (DecodePacket(AsSpan(aPacket), decoded) != Status::Ok)
    {
        ++aSink.framingErrors;
        return;
    }
    std::vector<DeliveredMessage> delivered;
    if (aConnection.OnPacket(aNow, decoded.header, decoded.body, delivered) != Status::Ok)
    {
        ++aSink.framingErrors;
        return;
    }
    for (DeliveredMessage& message : delivered)
    {
        (message.reliable ? aSink.reliable : aSink.unreliable).push_back(std::move(message.body));
    }
}

struct SimConfig
{
    double loss = 0.0;
    double dup = 0.0;
    double seconds = 30.0;
    size_t messages = 600;
    uint64_t seed = 1;
    uint16_t startSeq = 1;
    uint16_t startRel = 0;
    int burstEvery = 0;
    double baseDelay = 0.04;
    double jitter = 0.03;
    double drainLimit = 30.0; // keep running while messages are unacknowledged (see test_reliability.py)
};

struct SimResult
{
    Connection a{1};
    Connection b{1};
    std::vector<Bytes> sentA;
    std::vector<Bytes> sentB;
    Received gotA;
    Received gotB;
    double endTime = 0.0;
};

SimResult Simulate(const SimConfig& aConfig)
{
    test::Rng rng(aConfig.seed);
    SimResult result;
    result.a.SetStartSequences(aConfig.startSeq, aConfig.startRel, aConfig.startRel);
    result.b.SetStartSequences(aConfig.startSeq, aConfig.startRel, aConfig.startRel);
    LossyPipe ab(rng, aConfig.loss, aConfig.dup, aConfig.baseDelay, aConfig.jitter);
    LossyPipe ba(rng, aConfig.loss, aConfig.dup, aConfig.baseDelay, aConfig.jitter);
    double now = 0.0;
    const double step = 1.0 / 120.0;
    uint64_t counter = 0;
    uint64_t tick = 0;
    const auto pending = [&] { return result.a.PendingReliable() > 0 || result.b.PendingReliable() > 0; };
    while (now < aConfig.seconds || (pending() && now < aConfig.seconds + aConfig.drainLimit))
    {
        ++tick;
        for (int side = 0; side < 2; ++side)
        {
            Connection& connection = side == 0 ? result.a : result.b;
            LossyPipe& pipe = side == 0 ? ab : ba;
            std::vector<Bytes>& sent = side == 0 ? result.sentA : result.sentB;
            const Bytes snapshot = BytesOf("snap" + std::to_string(tick));
            std::vector<LinkMessage> unreliable;
            if (tick % 4 == 0)
            {
                unreliable.push_back(LinkMessage{0x10, 0xFF, AsSpan(snapshot)});
            }
            if (now < aConfig.seconds - 8 && sent.size() < aConfig.messages && rng.Unit() < 0.3)
            {
                const int count = aConfig.burstEvery != 0 && tick % aConfig.burstEvery == 0 ? 25 : 1;
                for (int index = 0; index < count; ++index)
                {
                    ++counter;
                    Bytes body = BytesOf("evt-" + std::to_string(counter));
                    if (connection.QueueReliable(0x20, 0xFF, AsSpan(body), now) == QueueResult::Queued)
                    {
                        sent.push_back(std::move(body));
                    }
                }
            }
            if (!unreliable.empty() || connection.ReliableDue(now) || (connection.AckPending() && tick % 4 == 0))
            {
                std::vector<Bytes> packets;
                CHECK(connection.BuildPackets(now, unreliable, connection.AckPending(), packets) == Status::Ok);
                for (const Bytes& packet : packets)
                {
                    pipe.Push(now, packet);
                }
            }
        }
        for (const Bytes& packet : ab.Pop(now))
        {
            Deliver(result.b, now, packet, result.gotB);
        }
        for (const Bytes& packet : ba.Pop(now))
        {
            Deliver(result.a, now, packet, result.gotA);
        }
        now += step;
    }
    result.endTime = now;
    return result;
}

void PrintLink(const char* aLabel, const SimResult& aResult)
{
    const LinkStats& stats = aResult.a.Stats();
    std::printf("    %-22s a->b %4zu msgs, resent %4llu, srtt %5.1f ms, rto %5.1f ms, rtt samples %4llu "
                "(skipped %4llu, max %5.1f ms), loss out %4.1f%%, done at %4.1f s\n",
                aLabel, aResult.sentA.size(), static_cast<unsigned long long>(stats.reliableResent),
                aResult.a.RttMs(), aResult.a.Rto() * 1000.0, static_cast<unsigned long long>(stats.rttSamples),
                static_cast<unsigned long long>(stats.rttSkipped), aResult.a.MaxRttSample().value_or(0.0) * 1000.0,
                aResult.a.LossOut() * 100.0, aResult.endTime);
}

// ---- ported tests/test_reliability.py ------------------------------------------------------------

void TestSeqArithmetic()
{
    std::puts("sequence arithmetic");
    CHECK(SeqDiff(1, 65535) == 2);
    CHECK(SeqDiff(65535, 1) == -2);
    CHECK(SeqDiff(100, 90) == 10);
    CHECK(SeqDiff(0, 32768) == -32768);
    CHECK(SeqDiff(32767, 0) == 32767);
    CHECK(NextPacketSeq(65535) == 1);
    CHECK(NextPacketSeq(1) == 2);
}

void TestExactlyOnceInOrder()
{
    std::puts("exactly once and in order under 0/10/30/45 % loss with duplicates (30 s, 120 Hz ticks)");
    struct Row
    {
        uint64_t seed;
        double loss;
        double dup;
        const char* label;
    };
    for (const Row& row : {Row{1, 0.0, 0.0, "loss 0%"}, Row{2, 0.1, 0.05, "loss 10%, dup 5%"},
                           Row{3, 0.3, 0.1, "loss 30%, dup 10%"}, Row{4, 0.45, 0.2, "loss 45%, dup 20%"}})
    {
        SimConfig config;
        config.seed = row.seed;
        config.loss = row.loss;
        config.dup = row.dup;
        const SimResult result = Simulate(config);
        PrintLink(row.label, result);
        CHECK(result.sentA.size() > 300);
        CHECK(result.gotB.reliable == result.sentA);
        CHECK(result.gotA.reliable == result.sentB);
        CHECK(result.a.PendingReliable() == 0 && result.b.PendingReliable() == 0);
        CHECK(result.gotA.framingErrors == 0 && result.gotB.framingErrors == 0);
        CHECK(result.b.Stats().reliableDelivered == result.sentA.size());
        if (row.dup > 0.0)
        {
            CHECK(result.b.Stats().duplicates > 0);
        }
    }
}

void TestBursts()
{
    std::puts("bursts of 25 fill the 256 window without breaking order (20 % loss)");
    SimConfig config;
    config.loss = 0.2;
    config.dup = 0.05;
    config.seed = 5;
    config.burstEvery = 90;
    config.messages = 2000;
    const SimResult result = Simulate(config);
    PrintLink("bursts", result);
    CHECK(result.sentA.size() > 1000);
    CHECK(result.gotB.reliable == result.sentA);
    CHECK(result.gotA.reliable == result.sentB);
}

void TestWraparound()
{
    std::puts("packet and reliable sequences wrap around 65535");
    SimConfig config;
    config.loss = 0.2;
    config.dup = 0.05;
    config.seed = 6;
    config.startSeq = 65400;
    config.startRel = 65300;
    const SimResult result = Simulate(config);
    PrintLink("wrap", result);
    CHECK(result.gotB.reliable == result.sentA);
    CHECK(result.gotA.reliable == result.sentB);
    CHECK(result.a.NextSeq() < 65400 && result.a.NextSeq() != 0);
    CHECK(result.a.RelNext() < 65300);
}

void TestUnreliableNeverRetransmitted()
{
    std::puts("unreliable messages are sent once; packet-level dedupe drops duplicate copies");
    SimConfig config;
    config.loss = 0.3;
    config.seed = 7;
    SimConfig dupConfig = config;
    dupConfig.dup = 0.3;
    for (const SimConfig& run : {config, dupConfig})
    {
        const SimResult result = Simulate(run);
        const std::set<Bytes> unique(result.gotB.unreliable.begin(), result.gotB.unreliable.end());
        CHECK(unique.size() == result.gotB.unreliable.size());
        CHECK(!result.gotB.unreliable.empty());
    }
}

void TestWindowLimit()
{
    std::puts("send window, body size and type limits");
    Connection connection;
    const Bytes x = BytesOf("x");
    for (size_t index = 0; index < kReliableWindow; ++index)
    {
        CHECK(connection.QueueReliable(0x20, 0, AsSpan(x), 0.0) == QueueResult::Queued);
    }
    CHECK(connection.QueueReliable(0x20, 0, AsSpan(x), 0.0) == QueueResult::WindowFull);
    Connection other;
    const Bytes largest(kMaxMessageBody, 0xAB);
    const Bytes tooLarge(kMaxMessageBody + 1, 0xAB);
    CHECK(other.QueueReliable(0x20, 0, AsSpan(tooLarge), 0.0) == QueueResult::TooLarge);
    CHECK(other.QueueReliable(0xA0, 0, AsSpan(x), 0.0) == QueueResult::BadType);
    CHECK(other.QueueReliable(0x20, 0, AsSpan(largest), 0.0) == QueueResult::Queued);
    std::vector<Bytes> packets;
    const std::vector<LinkMessage> invalid{LinkMessage{0x10, 0, AsSpan(tooLarge)}};
    CHECK(other.BuildPackets(0.0, invalid, false, packets) == Status::TooLarge && packets.empty());
    const std::vector<LinkMessage> badType{LinkMessage{0x90, 0, AsSpan(x)}};
    CHECK(other.BuildPackets(0.0, badType, false, packets) == Status::BadValue && packets.empty());
    CHECK(other.NextSeq() == 1 && other.Stats().packetsSent == 0);
    CHECK(other.BuildPackets(0.0, {}, false, packets) == Status::Ok && packets.size() == 1);
    CHECK(packets[0].size() == coopv2::kMaxPacket);
    CHECK(!other.SetStartSequences(5, 5, 5));
}

void TestRttEstimate()
{
    std::puts("RTT estimate converges to a clean 100 ms round trip");
    Connection a(1);
    Connection b(1);
    double now = 0.0;
    const Bytes x = BytesOf("x");
    const Bytes y = BytesOf("y");
    Received sink;
    for (int round = 0; round < 100; ++round)
    {
        std::vector<Bytes> packets;
        const std::vector<LinkMessage> toB{LinkMessage{0x10, 0, AsSpan(x)}};
        a.BuildPackets(now, toB, false, packets);
        for (const Bytes& packet : packets)
        {
            Deliver(b, now + 0.05, packet, sink);
        }
        packets.clear();
        const std::vector<LinkMessage> toA{LinkMessage{0x10, 0, AsSpan(y)}};
        b.BuildPackets(now + 0.05, toA, false, packets);
        for (const Bytes& packet : packets)
        {
            Deliver(a, now + 0.1, packet, sink);
        }
        now += 0.1;
    }
    CHECK(a.Srtt() && std::abs(*a.Srtt() - 0.1) <= 0.005);
    CHECK(a.Rto() >= 0.1);
    CHECK(a.Stats().rttSkipped == 1); // the first packet from b: nothing to compare it with
}

void TestAckBits()
{
    std::puts("ack bits, duplicates and packets too old to judge");
    Connection connection;
    std::vector<DeliveredMessage> out;
    for (const int seq : {1, 2, 4, 7})
    {
        CHECK(connection.OnPacket(0.0, PacketInfo{6, 0, static_cast<uint16_t>(seq), 0, 0}, {}, out) == Status::Ok);
    }
    CHECK(connection.RemoteSeq() == 7);
    // bit i = seq 6 - i: seq6 no, seq5 no, seq4 yes, seq3 no, seq2 yes, seq1 yes
    CHECK((connection.RecvBits() & 0b111111u) == 0b110100u);
    CHECK(connection.OnPacket(0.0, PacketInfo{6, 0, 4, 0, 0}, {}, out) == Status::Ok && out.empty());
    CHECK(connection.Stats().duplicates == 1);
    CHECK(connection.OnPacket(0.0, PacketInfo{6, 0, 60, 0, 0}, {}, out) == Status::Ok);
    CHECK(connection.RecvBits() == 0 && connection.RemoteSeq() == 60);
    CHECK(connection.OnPacket(0.0, PacketInfo{6, 0, 27, 0, 0}, {}, out) == Status::Ok);
    CHECK(connection.Stats().tooOld == 1);
    CHECK(connection.OnPacket(0.0, PacketInfo{6, 0, 28, 0, 0}, {}, out) == Status::Ok);
    CHECK(connection.RecvBits() == 0x80000000u && connection.Stats().tooOld == 1);
    CHECK(connection.Stats().packetsReceived == 6 && connection.Stats().recvSpan == 60);
}

void TestSeqZeroAndBadFraming()
{
    std::puts("sequence 0 is reserved; bad framing changes no state");
    Connection connection;
    std::vector<DeliveredMessage> out;
    CHECK(connection.OnPacket(0.0, PacketInfo{6, 0, 0, 0, 0}, {}, out) == Status::BadValue);
    const Bytes badFraming{0x10, 0x00, 0xFF, 0x00};
    CHECK(connection.OnPacket(0.0, PacketInfo{6, 0, 1, 0, 0}, AsSpan(badFraming), out) == Status::Truncated);
    CHECK(connection.RemoteSeq() == 0 && !connection.AckPending());
    Bytes ninetySeven;
    for (int index = 0; index < 97; ++index)
    {
        AppendMessage(ninetySeven, 0x10, 1, std::nullopt, {});
    }
    CHECK(connection.OnPacket(0.0, PacketInfo{6, 0, 1, 0, 0}, AsSpan(ninetySeven), out) == Status::TooManyMessages);
    CHECK(connection.RemoteSeq() == 0 && out.empty());
}

void TestPacketsHoldAtMost96Messages()
{
    std::puts("a DATA packet holds at most 96 messages");
    Connection connection(1);
    for (int index = 0; index < 200; ++index)
    {
        CHECK(connection.QueueReliable(0x20, 0, {}, 0.0) == QueueResult::Queued);
    }
    const std::vector<LinkMessage> unreliable(50, LinkMessage{0x10, 0, {}});
    std::vector<Bytes> packets;
    CHECK(connection.BuildPackets(0.0, unreliable, false, packets) == Status::Ok);
    size_t total = 0;
    size_t largest = 0;
    for (const Bytes& packet : packets)
    {
        DecodedPacket decoded;
        std::vector<MessageView> messages;
        CHECK(DecodePacket(AsSpan(packet), decoded) == Status::Ok);
        CHECK(DecodeMessages(decoded.body, messages) == Status::Ok);
        total += messages.size();
        largest = std::max(largest, messages.size());
    }
    CHECK(total == 250 && largest <= kMaxMessagesPerPacket && packets.size() == 3);
}

void TestRttIgnoresAcksAfterAGap()
{
    std::puts("RTT lesson: no sample from an ack that arrives after a lost packet");
    Connection a(1);
    Connection b(1);
    const Bytes x = BytesOf("x");
    const Bytes y = BytesOf("y");
    Received sink;
    const auto aToB = [&](double aNow) {
        std::vector<Bytes> packets;
        const std::vector<LinkMessage> messages{LinkMessage{0x10, 0, AsSpan(x)}};
        a.BuildPackets(aNow, messages, false, packets);
        for (const Bytes& packet : packets)
        {
            Deliver(b, aNow + 0.05, packet, sink);
        }
    };
    const auto bPackets = [&](double aNow) {
        std::vector<Bytes> packets;
        const std::vector<LinkMessage> messages{LinkMessage{0x10, 0, AsSpan(y)}};
        b.BuildPackets(aNow, messages, false, packets);
        return packets;
    };
    double now = 0.0;
    for (int round = 0; round < 10; ++round)
    {
        aToB(now);
        for (const Bytes& packet : bPackets(now + 0.05))
        {
            Deliver(a, now + 0.1, packet, sink);
        }
        now += 0.1;
    }
    CHECK(a.Srtt() && std::abs(*a.Srtt() - 0.1) < 0.001);
    const uint64_t samples = a.Stats().rttSamples;
    const uint64_t skipped = a.Stats().rttSkipped;
    CHECK(skipped == 1);
    aToB(now);
    CHECK(bPackets(now + 0.05).size() == 1); // lost: it acked a's newest packet
    for (const Bytes& packet : bPackets(now + 1.0))
    {
        Deliver(a, now + 1.05, packet, sink); // acks it again, 1 s later
    }
    CHECK(a.Stats().rttSamples == samples);
    CHECK(a.Stats().rttSkipped == skipped + 1);
    CHECK(a.Srtt() && std::abs(*a.Srtt() - 0.1) < 0.001); // a 1.05 s sample would pull it to 0.22 s
    CHECK(a.SentAwaitingAck() == 0);
}

void TestNoInflatedRttSamples()
{
    std::puts("RTT lesson under loss: no sample above the simulation's physical maximum");
    // A packet takes at most 70 ms each way plus one 8.3 ms tick to be popped, and the peer
    // answers within 4 ticks, so no true round trip in Simulate() exceeds about 190 ms.
    struct Row
    {
        uint64_t seed;
        double loss;
        double dup;
    };
    for (const Row& row : {Row{1, 0.0, 0.0}, Row{2, 0.1, 0.05}, Row{3, 0.3, 0.1}, Row{4, 0.45, 0.2}})
    {
        SimConfig config;
        config.seed = row.seed;
        config.loss = row.loss;
        config.dup = row.dup;
        const SimResult result = Simulate(config);
        for (const Connection* connection : {&result.a, &result.b})
        {
            const LinkStats& stats = connection->Stats();
            CHECK(stats.rttSamples > 100);
            CHECK(connection->MaxRttSample() && *connection->MaxRttSample() <= 0.19);
            if (row.loss > 0.0)
            {
                CHECK(stats.rttSkipped > stats.rttSamples / 10);
            }
        }
    }
}

// ---- C++ additions -------------------------------------------------------------------------------

void TestHeavyReordering()
{
    std::puts("heavy reordering: 120 ms jitter against a 33 ms send interval, 20 % loss");
    SimConfig config;
    config.loss = 0.2;
    config.dup = 0.05;
    config.seed = 8;
    config.jitter = 0.12;
    const SimResult result = Simulate(config);
    PrintLink("reorder 120 ms", result);
    CHECK(result.gotB.reliable == result.sentA);
    CHECK(result.gotA.reliable == result.sentB);
    CHECK(result.b.Stats().reliableDuplicates > 0 || result.b.Stats().duplicates > 0);
}

void TestNextReliableDue()
{
    std::puts("next reliable due time");
    Connection connection;
    CHECK(!connection.NextReliableDue());
    const Bytes x = BytesOf("x");
    connection.QueueReliable(0x20, 0, AsSpan(x), 1.0);
    CHECK(connection.NextReliableDue() == 1.0);
    std::vector<Bytes> packets;
    connection.BuildPackets(2.0, {}, false, packets);
    CHECK(connection.NextReliableDue() == 2.0 + kInitialRtoS);
    CHECK(!connection.ReliableDue(2.0 + kInitialRtoS - 0.001) && connection.ReliableDue(2.0 + kInitialRtoS));
    connection.BuildPackets(2.0 + kInitialRtoS, {}, false, packets);
    CHECK(connection.NextReliableDue() == 2.0 + kInitialRtoS + 2 * kInitialRtoS); // backoff doubles
    std::vector<DeliveredMessage> out;
    connection.OnPacket(3.0, PacketInfo{6, 0, 1, static_cast<uint16_t>(connection.NextSeq() - 1), 0}, {}, out);
    CHECK(!connection.NextReliableDue() && connection.PendingReliable() == 0);
}

void TestLongTransfer()
{
    std::puts("70,000 reliable messages at 5 % loss (reliable sequence wraps), throughput-bound");
    test::Rng rng(11);
    Connection a(1);
    Connection b(1);
    LossyPipe ab(rng, 0.05, 0.01, 0.04, 0.03);
    LossyPipe ba(rng, 0.05, 0.01, 0.04, 0.03);
    constexpr uint32_t kTotal = 70000;
    uint32_t queued = 0;
    uint32_t delivered = 0;
    bool inOrder = true;
    double now = 0.0;
    uint64_t tick = 0;
    const auto started = std::chrono::steady_clock::now();
    while (delivered < kTotal && now < 600.0)
    {
        ++tick;
        while (queued < kTotal)
        {
            const Bytes body = BytesOf(std::to_string(queued));
            if (a.QueueReliable(0x20, 0, AsSpan(body), now) != QueueResult::Queued)
            {
                break;
            }
            ++queued;
        }
        if (a.ReliableDue(now) || tick % 4 == 0)
        {
            std::vector<Bytes> packets;
            a.BuildPackets(now, {}, true, packets);
            for (const Bytes& packet : packets)
            {
                ab.Push(now, packet);
            }
        }
        if (b.AckPending() && tick % 2 == 0)
        {
            std::vector<Bytes> packets;
            b.BuildPackets(now, {}, true, packets);
            for (const Bytes& packet : packets)
            {
                ba.Push(now, packet);
            }
        }
        Received gotB;
        for (const Bytes& packet : ab.Pop(now))
        {
            Deliver(b, now, packet, gotB);
        }
        for (const Bytes& body : gotB.reliable)
        {
            inOrder = inOrder && body == BytesOf(std::to_string(delivered));
            ++delivered;
        }
        Received gotA;
        for (const Bytes& packet : ba.Pop(now))
        {
            Deliver(a, now, packet, gotA);
        }
        now += 1.0 / 120.0;
    }
    const double wallMs =
        std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - started).count();
    std::printf("    delivered %u in order=%d in %.1f simulated s (%.0f msgs/s), resent %llu, %.0f ms CPU\n", delivered,
                inOrder ? 1 : 0, now, delivered / now, static_cast<unsigned long long>(a.Stats().reliableResent), wallMs);
    CHECK(delivered == kTotal && inOrder);
    CHECK(b.RelExpected() == static_cast<uint16_t>(kTotal));
}

// ---- trace replay --------------------------------------------------------------------------------

std::vector<std::string> Split(const std::string& aLine)
{
    std::vector<std::string> tokens;
    std::istringstream stream(aLine);
    std::string token;
    while (stream >> token)
    {
        tokens.push_back(token);
    }
    return tokens;
}

bool ParseHex(const std::string& aHex, Bytes& aOut)
{
    aOut.clear();
    if (aHex == "-")
    {
        return true;
    }
    if (aHex.size() % 2 != 0)
    {
        return false;
    }
    for (size_t index = 0; index < aHex.size(); index += 2)
    {
        aOut.push_back(static_cast<uint8_t>(std::stoul(aHex.substr(index, 2), nullptr, 16)));
    }
    return true;
}

std::optional<double> ParseFloat(const std::string& aText)
{
    if (aText == "-")
    {
        return std::nullopt;
    }
    return std::strtod(aText.c_str(), nullptr);
}

std::string FloatText(std::optional<double> aValue)
{
    if (!aValue)
    {
        return "-";
    }
    char buffer[64];
    std::snprintf(buffer, sizeof(buffer), "%a", *aValue);
    return buffer;
}

class TraceReplay
{
public:
    explicit TraceReplay(std::vector<std::string> aLines)
        : m_lines(std::move(aLines))
    {
    }

    bool Run()
    {
        while (m_index < m_lines.size())
        {
            const std::vector<std::string> tokens = Split(m_lines[m_index]);
            ++m_index;
            if (tokens.empty())
            {
                continue;
            }
            if (tokens[0] == "SCENARIO")
            {
                m_scenario = tokens.size() > 1 ? tokens[1] : "?";
                m_connections.clear();
                m_failed = false;
                m_scenarioCalls = 0;
                continue;
            }
            if (tokens[0] == "END")
            {
                ++m_scenarios;
                std::printf("    %-16s %6llu calls %s\n", m_scenario.c_str(),
                            static_cast<unsigned long long>(m_scenarioCalls), m_failed ? "MISMATCH" : "identical");
                m_failedScenarios += m_failed ? 1 : 0;
                continue;
            }
            if (m_failed)
            {
                continue; // skip the rest of a scenario after its first mismatch
            }
            Step(tokens);
        }
        std::printf("trace: %d scenarios, %llu calls, %llu datagrams, %llu deliveries, %llu states compared; "
                    "%d scenarios with mismatches\n",
                    m_scenarios, static_cast<unsigned long long>(m_calls), static_cast<unsigned long long>(m_datagrams),
                    static_cast<unsigned long long>(m_deliveries), static_cast<unsigned long long>(m_states),
                    m_failedScenarios);
        return m_failedScenarios == 0 && m_scenarios > 0;
    }

private:
    void Fail(const std::string& aWhat)
    {
        if (!m_failed)
        {
            std::printf("  MISMATCH %s line %zu: %s\n", m_scenario.c_str(), m_index, aWhat.c_str());
        }
        m_failed = true;
    }

    Connection* Find(const std::string& aId)
    {
        const auto found = m_connections.find(std::stoi(aId));
        if (found == m_connections.end())
        {
            Fail("unknown connection " + aId);
            return nullptr;
        }
        return found->second.get();
    }

    std::vector<std::string> NextTokens()
    {
        if (m_index >= m_lines.size())
        {
            return {};
        }
        return Split(m_lines[m_index++]);
    }

    void Step(const std::vector<std::string>& aTokens)
    {
        const std::string& kind = aTokens[0];
        if (kind == "NEW")
        {
            m_connections[std::stoi(aTokens[1])] =
                std::make_unique<Connection>(std::stoull(aTokens[2]), static_cast<size_t>(std::stoul(aTokens[3])));
            return;
        }
        Connection* connection = Find(aTokens[1]);
        if (connection == nullptr)
        {
            return;
        }
        if (kind == "START")
        {
            if (!connection->SetStartSequences(static_cast<uint16_t>(std::stoul(aTokens[2])),
                                               static_cast<uint16_t>(std::stoul(aTokens[3])),
                                               static_cast<uint16_t>(std::stoul(aTokens[4]))))
            {
                Fail("SetStartSequences refused");
            }
            return;
        }
        ++m_calls;
        ++m_scenarioCalls;
        const double now = *ParseFloat(aTokens[2]);
        if (kind == "QUEUE")
        {
            Bytes body;
            ParseHex(aTokens[5], body);
            const QueueResult result = connection->QueueReliable(static_cast<uint8_t>(std::stoul(aTokens[3])),
                                                                 static_cast<uint8_t>(std::stoul(aTokens[4])),
                                                                 AsSpan(body), now);
            const char* expected = aTokens[6] == "1" ? "Queued" : aTokens[6] == "0" ? "WindowFull" : "TooLarge";
            if (std::string(ToString(result)) != expected)
            {
                Fail(std::string("QueueReliable ") + ToString(result) + ", reliability.py " + expected);
            }
        }
        else if (kind == "DUE")
        {
            if (connection->ReliableDue(now) != (aTokens[3] == "1"))
            {
                Fail("ReliableDue differs");
            }
        }
        else if (kind == "BUILD")
        {
            ReplayBuild(*connection, now, aTokens);
        }
        else if (kind == "RECV")
        {
            ReplayRecv(*connection, now, aTokens);
        }
        else
        {
            Fail("unknown record " + kind);
        }
    }

    void ReplayBuild(Connection& aConnection, double aNow, const std::vector<std::string>& aTokens)
    {
        const bool force = aTokens[3] == "1";
        const size_t count = std::stoul(aTokens[4]);
        std::vector<Bytes> bodies(count);
        std::vector<LinkMessage> unreliable;
        for (size_t index = 0; index < count; ++index)
        {
            ParseHex(aTokens[5 + 3 * index + 2], bodies[index]);
        }
        for (size_t index = 0; index < count; ++index)
        {
            unreliable.push_back(LinkMessage{static_cast<uint8_t>(std::stoul(aTokens[5 + 3 * index])),
                                             static_cast<uint8_t>(std::stoul(aTokens[5 + 3 * index + 1])),
                                             AsSpan(bodies[index])});
        }
        std::vector<Bytes> packets;
        if (aConnection.BuildPackets(aNow, unreliable, force, packets) != Status::Ok)
        {
            Fail("BuildPackets refused");
            return;
        }
        std::vector<Bytes> expected;
        for (std::vector<std::string> line = NextTokens(); !line.empty() && line[0] == "OUT"; line = NextTokens())
        {
            Bytes packet;
            ParseHex(line[1], packet);
            expected.push_back(std::move(packet));
        }
        // NextTokens consumed the BUILT line.
        if (packets.size() != expected.size())
        {
            Fail("BuildPackets made " + std::to_string(packets.size()) + " packets, reliability.py " +
                 std::to_string(expected.size()));
            return;
        }
        for (size_t index = 0; index < packets.size(); ++index)
        {
            ++m_datagrams;
            if (packets[index] != expected[index])
            {
                Fail("datagram " + std::to_string(index) + " differs:\n    C++    " + Hex(AsSpan(packets[index])) +
                     "\n    Python " + Hex(AsSpan(expected[index])));
                return;
            }
        }
        CompareState(aConnection, NextTokens());
    }

    void ReplayRecv(Connection& aConnection, double aNow, const std::vector<std::string>& aTokens)
    {
        PacketInfo header;
        header.seq = static_cast<uint16_t>(std::stoul(aTokens[3]));
        header.ack = static_cast<uint16_t>(std::stoul(aTokens[4]));
        header.ackBits = static_cast<uint32_t>(std::stoul(aTokens[5]));
        const size_t size = std::stoul(aTokens[6]);
        Bytes body;
        ParseHex(aTokens[7], body);
        std::vector<DeliveredMessage> delivered;
        const Status status = aConnection.OnPacket(aNow, header, AsSpan(body), delivered, size);
        std::vector<std::string> line = NextTokens();
        if (!line.empty() && line[0] == "RECVERR")
        {
            if (status == Status::Ok)
            {
                Fail("OnPacket accepted a packet reliability.py rejects");
                return;
            }
        }
        else
        {
            if (status != Status::Ok)
            {
                Fail(std::string("OnPacket rejected a packet reliability.py accepts: ") + ToString(status));
                return;
            }
            size_t index = 0;
            for (; !line.empty() && line[0] == "GOT"; line = NextTokens(), ++index)
            {
                ++m_deliveries;
                Bytes payload;
                ParseHex(line[4], payload);
                if (index >= delivered.size() || delivered[index].type != std::stoul(line[1]) ||
                    delivered[index].peer != std::stoul(line[2]) || delivered[index].reliable != (line[3] == "1") ||
                    delivered[index].body != payload)
                {
                    Fail("delivery " + std::to_string(index) + " differs");
                    return;
                }
            }
            if (index != delivered.size())
            {
                Fail("OnPacket delivered " + std::to_string(delivered.size()) + ", reliability.py " +
                     std::to_string(index));
                return;
            }
        }
        CompareState(aConnection, NextTokens());
    }

    void CompareState(const Connection& aConnection, const std::vector<std::string>& aState)
    {
        if (aState.size() != 32 || aState[0] != "STATE")
        {
            Fail("expected a STATE record");
            return;
        }
        ++m_states;
        const LinkStats& stats = aConnection.Stats();
        const std::vector<std::pair<const char*, std::string>> mine{
            {"srtt", FloatText(aConnection.Srtt())},
            {"rttvar", FloatText(aConnection.Rttvar())},
            {"rto", FloatText(aConnection.Rto())},
            {"next_seq", std::to_string(aConnection.NextSeq())},
            {"remote_seq", std::to_string(aConnection.RemoteSeq())},
            {"recv_bits", std::to_string(aConnection.RecvBits())},
            {"ack_pending", aConnection.AckPending() ? "1" : "0"},
            {"rel_next", std::to_string(aConnection.RelNext())},
            {"rel_expected", std::to_string(aConnection.RelExpected())},
            {"pending", std::to_string(aConnection.PendingReliable())},
            {"sent", std::to_string(aConnection.SentAwaitingAck())},
            {"buffered", std::to_string(aConnection.BufferedReliable())},
            {"last_send", FloatText(aConnection.LastSend())},
            {"last_recv", FloatText(aConnection.LastRecv())},
            {"packets_sent", std::to_string(stats.packetsSent)},
            {"packets_received", std::to_string(stats.packetsReceived)},
            {"packets_acked", std::to_string(stats.packetsAcked)},
            {"packets_lost", std::to_string(stats.packetsLost)},
            {"duplicates", std::to_string(stats.duplicates)},
            {"too_old", std::to_string(stats.tooOld)},
            {"bytes_sent", std::to_string(stats.bytesSent)},
            {"bytes_received", std::to_string(stats.bytesReceived)},
            {"reliable_sent", std::to_string(stats.reliableSent)},
            {"reliable_resent", std::to_string(stats.reliableResent)},
            {"reliable_delivered", std::to_string(stats.reliableDelivered)},
            {"reliable_duplicates", std::to_string(stats.reliableDuplicates)},
            {"reliable_out_of_window", std::to_string(stats.reliableOutOfWindow)},
            {"recv_span", std::to_string(stats.recvSpan)},
            {"rtt_samples", std::to_string(stats.rttSamples)},
            {"rtt_skipped", std::to_string(stats.rttSkipped)},
        };
        for (size_t index = 0; index < mine.size(); ++index)
        {
            std::string expected = aState[index + 2];
            if (index < 3 || index == 12 || index == 13)
            {
                expected = FloatText(ParseFloat(expected)); // same float, C++ spelling
            }
            if (mine[index].second != expected)
            {
                Fail(std::string("state ") + mine[index].first + " = " + mine[index].second + ", reliability.py " +
                     aState[index + 2]);
                return;
            }
        }
    }

    std::vector<std::string> m_lines;
    size_t m_index = 0;
    std::map<int, std::unique_ptr<Connection>> m_connections;
    std::string m_scenario;
    bool m_failed = false;
    int m_scenarios = 0;
    int m_failedScenarios = 0;
    uint64_t m_calls = 0;
    uint64_t m_scenarioCalls = 0;
    uint64_t m_datagrams = 0;
    uint64_t m_deliveries = 0;
    uint64_t m_states = 0;
};

int RunTrace(const char* aPath)
{
    std::ifstream file(aPath);
    if (!file)
    {
        std::printf("cannot open %s\n", aPath);
        return EXIT_FAILURE;
    }
    std::vector<std::string> lines;
    for (std::string line; std::getline(file, line);)
    {
        lines.push_back(line);
    }
    std::printf("replaying %zu trace lines from %s\n", lines.size(), aPath);
    TraceReplay replay(std::move(lines));
    return replay.Run() ? EXIT_SUCCESS : EXIT_FAILURE;
}
} // namespace

int main(int argc, char** argv)
{
    if (argc == 3 && std::string(argv[1]) == "trace")
    {
        return RunTrace(argv[2]);
    }
    if (argc != 1)
    {
        std::puts("usage: coopnet_v2_reliability_tests [trace <file>]");
        return EXIT_FAILURE;
    }
    TestSeqArithmetic();
    TestExactlyOnceInOrder();
    TestBursts();
    TestWraparound();
    TestUnreliableNeverRetransmitted();
    TestWindowLimit();
    TestRttEstimate();
    TestAckBits();
    TestSeqZeroAndBadFraming();
    TestPacketsHoldAtMost96Messages();
    TestRttIgnoresAcksAfterAGap();
    TestNoInflatedRttSamples();
    TestHeavyReordering();
    TestNextReliableDue();
    TestLongTransfer();
    std::printf("%d checks, %d failures\n", g_checks, g_failures);
    return g_failures == 0 ? EXIT_SUCCESS : EXIT_FAILURE;
}
