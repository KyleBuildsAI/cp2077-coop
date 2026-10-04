// Deterministic tests for the frame codec and the reliability layer (no sockets, simulated clock),
// plus the Net_Version string.

#include "core/Protocol.hpp"
#include "core/Reliability.hpp"
#include "core/Version.hpp"

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <random>
#include <regex>
#include <string>
#include <vector>

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

using namespace coopnet;

std::span<const uint8_t> AsBytes(const std::string& aText)
{
    return {reinterpret_cast<const uint8_t*>(aText.data()), aText.size()};
}

void TestFrameRoundTrip()
{
    std::puts("frame round trip");
    FrameHeader header;
    header.channel = 17;
    header.sender = 3;
    header.target = 0xFFFF;
    header.sequence = 0xBEEF;
    header.ack = 0x1234;
    header.ackBits = 0xA5A5F00F;
    const std::string payload = "veh|12|-1043.5,22.1,7.9";

    std::array<uint8_t, kMaxDatagramSize> buffer{};
    const size_t size = EncodeFrame(header, AsBytes(payload), buffer);
    CHECK(size == kHeaderSize + payload.size());
    CHECK(buffer[0] == 'C' && buffer[1] == 'P' && buffer[2] == 'N' && buffer[3] == '2');

    FrameHeader decoded;
    std::span<const uint8_t> decodedPayload;
    CHECK(DecodeFrame(std::span<const uint8_t>(buffer.data(), size), decoded, decodedPayload) == DecodeResult::Ok);
    CHECK(decoded.channel == 17 && decoded.sender == 3 && decoded.target == 0xFFFF);
    CHECK(decoded.sequence == 0xBEEF && decoded.ack == 0x1234 && decoded.ackBits == 0xA5A5F00F);
    CHECK(std::string(decodedPayload.begin(), decodedPayload.end()) == payload);

    CHECK(DecodeFrame(std::span<const uint8_t>(buffer.data(), kHeaderSize - 1), decoded, decodedPayload) ==
          DecodeResult::TooShort);
    CHECK(DecodeFrame(std::span<const uint8_t>(buffer.data(), size - 1), decoded, decodedPayload) ==
          DecodeResult::BadLength);
    auto corrupted = buffer;
    corrupted[0] = 'X';
    CHECK(DecodeFrame(std::span<const uint8_t>(corrupted.data(), size), decoded, decodedPayload) ==
          DecodeResult::BadMagic);
    corrupted = buffer;
    corrupted[4] = kProtocolVersion + 1;
    CHECK(DecodeFrame(std::span<const uint8_t>(corrupted.data(), size), decoded, decodedPayload) ==
          DecodeResult::BadVersion);

    const std::string oversized(kMaxPayloadSize + 1, 'x');
    CHECK(EncodeFrame(header, AsBytes(oversized), buffer) == 0);
    const std::string largest(kMaxPayloadSize, 'x');
    CHECK(EncodeFrame(header, AsBytes(largest), buffer) == kMaxDatagramSize);
}

void TestChannelsAndSequences()
{
    std::puts("channels and sequence arithmetic");
    CHECK(DeliveryOf(0) == Delivery::Control);
    CHECK(DeliveryOf(1) == Delivery::Unreliable && DeliveryOf(15) == Delivery::Unreliable);
    CHECK(DeliveryOf(16) == Delivery::Reliable && DeliveryOf(31) == Delivery::Reliable);
    CHECK(DeliveryOf(32) == Delivery::Invalid && DeliveryOf(-1) == Delivery::Invalid);
    CHECK(SequenceGreater(1, 0));
    CHECK(SequenceGreater(0, 0xFFFF));
    CHECK(!SequenceGreater(0xFFFF, 0));
    CHECK(!SequenceGreater(5, 5));
    CHECK(SequenceDistance(0xFFFE, 2) == 4);
    CHECK(SequenceDistance(2, 0xFFFE) == -4);
}

void TestRttEstimator()
{
    std::puts("rtt estimator");
    RttEstimator rtt;
    CHECK(rtt.RtoMicros() == RttEstimator::kInitialRtoMicros);
    for (int sample = 0; sample < 40; ++sample)
    {
        rtt.AddSample(320'000);
    }
    CHECK(rtt.SmoothedMs() > 319.0 && rtt.SmoothedMs() < 321.0);
    CHECK(rtt.RtoMicros() >= 330'000 && rtt.RtoMicros() <= 360'000);
    RttEstimator fast;
    fast.AddSample(1'000);
    for (int sample = 0; sample < 40; ++sample)
    {
        fast.AddSample(1'000);
    }
    CHECK(fast.RtoMicros() == RttEstimator::kMinRtoMicros);
}

void TestAckBitsAndDuplicates()
{
    std::puts("ack bits, duplicates, window");
    ReliableEndpoint receiver;
    const std::string body = "x";
    CHECK(receiver.OnReceive(0, 16, AsBytes(body)) == ReliableEndpoint::ReceiveResult::Accepted);
    CHECK(receiver.OnReceive(2, 16, AsBytes(body)) == ReliableEndpoint::ReceiveResult::Accepted);
    CHECK(receiver.OnReceive(3, 16, AsBytes(body)) == ReliableEndpoint::ReceiveResult::Accepted);
    CHECK(receiver.OnReceive(5, 16, AsBytes(body)) == ReliableEndpoint::ReceiveResult::Accepted);
    CHECK(receiver.OnReceive(3, 16, AsBytes(body)) == ReliableEndpoint::ReceiveResult::Duplicate);
    CHECK(receiver.AckValue() == 0);
    CHECK(receiver.AckBits() == 0b1011u); // 2 -> bit0, 3 -> bit1, 5 -> bit3
    CHECK(receiver.OnReceive(200, 16, AsBytes(body)) == ReliableEndpoint::ReceiveResult::OutOfWindow);

    std::vector<uint16_t> delivered;
    receiver.Deliver(
        [&delivered](uint16_t aSequence, uint8_t, std::string_view)
        {
            delivered.push_back(aSequence);
            return true;
        });
    CHECK(delivered.size() == 1 && delivered[0] == 0);
    CHECK(receiver.OnReceive(0, 16, AsBytes(body)) == ReliableEndpoint::ReceiveResult::Duplicate);
    CHECK(receiver.OnReceive(1, 16, AsBytes(body)) == ReliableEndpoint::ReceiveResult::Accepted);
    CHECK(receiver.AckValue() == 3);
    receiver.Deliver(
        [&delivered](uint16_t aSequence, uint8_t, std::string_view)
        {
            delivered.push_back(aSequence);
            return true;
        });
    CHECK(delivered.size() == 4 && delivered[3] == 3);
}

void TestBackpressure()
{
    std::puts("inbox backpressure keeps order and acks");
    ReliableEndpoint receiver;
    for (uint16_t sequence = 0; sequence < 10; ++sequence)
    {
        const std::string body = std::to_string(sequence);
        receiver.OnReceive(sequence, 20, AsBytes(body));
    }
    CHECK(receiver.AckValue() == 9); // buffered messages are acked even if not yet delivered
    std::vector<std::string> delivered;
    size_t capacity = 3;
    auto sink = [&](uint16_t, uint8_t, std::string_view aPayload)
    {
        if (delivered.size() >= capacity)
        {
            return false;
        }
        delivered.emplace_back(aPayload);
        return true;
    };
    CHECK(receiver.Deliver(sink) == 3);
    capacity = 100;
    CHECK(receiver.Deliver(sink) == 7);
    bool ordered = delivered.size() == 10;
    for (size_t index = 0; ordered && index < delivered.size(); ++index)
    {
        ordered = delivered[index] == std::to_string(index);
    }
    CHECK(ordered);
}

void TestFastRetransmit()
{
    std::puts("fast retransmit after three later acks");
    ReliableEndpoint sender;
    for (const char* body : {"a", "b", "c", "d", "e"})
    {
        sender.Enqueue(16, body);
    }
    std::vector<uint16_t> emitted;
    auto emit = [&emitted](uint16_t aSequence, uint8_t, std::string_view, bool) { emitted.push_back(aSequence); };
    sender.CollectDue(0, emit);
    CHECK(emitted.size() == 5);
    emitted.clear();
    // Two later messages acked: could still be reordering, no resend yet.
    sender.OnAck(0xFFFF, 0b011, 40'000);
    sender.CollectDue(40'000, emit);
    CHECK(emitted.empty());
    // Third later message acked (bits are cumulative state): 0 is now considered lost.
    sender.OnAck(0xFFFF, 0b111, 50'000);
    CHECK(sender.InFlight() == 2);
    sender.CollectDue(50'000, emit);
    CHECK(emitted.size() == 1 && emitted[0] == 0);
    CHECK(sender.Stats().fastResent == 1);
    // A stale ack carrying the same information must not trigger another immediate resend.
    emitted.clear();
    sender.OnAck(0xFFFF, 0b111, 60'000);
    sender.CollectDue(60'000, emit);
    CHECK(emitted.empty());
    sender.OnAck(4, 0, 70'000);
    CHECK(sender.InFlight() == 0);
    CHECK(sender.NextDueMicros() == UINT64_MAX);
}

void TestSackHorizon()
{
    std::puts("timeouts beyond the ack horizon are deferred");
    ReliableEndpoint sender;
    for (int index = 0; index < 100; ++index)
    {
        sender.Enqueue(16, "m" + std::to_string(index));
    }
    std::vector<uint16_t> emitted;
    auto emit = [&emitted](uint16_t aSequence, uint8_t, std::string_view, bool) { emitted.push_back(aSequence); };
    sender.CollectDue(0, emit);
    CHECK(emitted.size() == 100);
    emitted.clear();
    // Receiver lost 0, has 1..99; the bitfield can only report 1..32.
    sender.OnAck(0xFFFF, 0xFFFFFFFFu, 10'000);
    CHECK(sender.InFlight() == 68); // 0 and 33..99
    sender.CollectDue(10'000, emit);
    CHECK(emitted.size() == 1 && emitted[0] == 0);
    emitted.clear();
    // Long after every timer expired: only the oldest message is retransmitted.
    sender.CollectDue(600'000, emit);
    CHECK(emitted.size() == 1 && emitted[0] == 0);
    CHECK(sender.Stats().deferred == 67);
    // Hole repaired: the receiver acks everything cumulatively, nothing beyond 0 was resent.
    sender.OnAck(99, 0, 650'000);
    CHECK(sender.InFlight() == 0);
    CHECK(sender.Stats().resent == 2);
}

// Two endpoints over a simulated lossy, reordering, duplicating link (one direction carries data,
// the other carries pure acks).
struct SimFrame
{
    uint64_t deliverAt;
    uint64_t order;
    bool isData;
    uint16_t sequence;
    uint8_t channel;
    std::string payload;
    uint16_t ack;
    uint32_t ackBits;
};

struct LinkScenario
{
    const char* name;
    size_t messages;
    double messagesPerSecond; // 0 = enqueue as fast as the window allows
    double loss;
    double duplicate;
    uint64_t delayMicros;  // one way
    uint64_t jitterMicros; // uniform 0..jitter added per frame (causes reordering)
    unsigned seed;
};

struct TransferResult
{
    bool ok = false;
    ReliableStats stats;
    double p50Ms = 0.0;
    double p99Ms = 0.0;
    double maxMs = 0.0;
};

TransferResult RunLossyTransfer(const LinkScenario& aScenario)
{
    ReliableEndpoint sender;
    ReliableEndpoint receiver;
    std::mt19937 random(aScenario.seed);
    std::uniform_real_distribution<double> unit(0.0, 1.0);
    std::uniform_int_distribution<uint64_t> jitter(0, aScenario.jitterMicros);
    std::vector<SimFrame> wire;
    uint64_t order = 0;

    auto transmit = [&](SimFrame aFrame, uint64_t aNow)
    {
        if (unit(random) < aScenario.loss)
        {
            return;
        }
        const int copies = unit(random) < aScenario.duplicate ? 2 : 1;
        for (int copy = 0; copy < copies; ++copy)
        {
            aFrame.deliverAt = aNow + aScenario.delayMicros + jitter(random);
            aFrame.order = order++;
            wire.push_back(aFrame);
        }
    };

    std::vector<uint64_t> enqueuedAt;
    std::vector<double> latenciesMs;
    size_t delivered = 0;
    bool inOrder = true;
    const uint64_t stepMicros = 1'000;
    const uint64_t limitMicros = 900'000'000;
    uint64_t now = 0;
    for (; now < limitMicros; now += stepMicros)
    {
        size_t allowed = aScenario.messages;
        if (aScenario.messagesPerSecond > 0.0)
        {
            allowed = std::min(aScenario.messages,
                               static_cast<size_t>(aScenario.messagesPerSecond * static_cast<double>(now) / 1e6) + 1);
        }
        for (int burst = 0; burst < 40 && enqueuedAt.size() < allowed; ++burst)
        {
            if (!sender.Enqueue(16, "m" + std::to_string(enqueuedAt.size())))
            {
                break;
            }
            enqueuedAt.push_back(now);
        }
        if (now % 500'000 == 0)
        {
            // The transport measures RTT with PING/PONG every 500 ms; emulate those samples.
            sender.Rtt().AddSample(2 * aScenario.delayMicros + jitter(random) + jitter(random));
        }
        sender.CollectDue(now,
                          [&](uint16_t aSequence, uint8_t aChannel, std::string_view aPayload, bool)
                          { transmit({0, 0, true, aSequence, aChannel, std::string(aPayload), 0, 0}, now); });

        std::vector<SimFrame> due;
        for (auto it = wire.begin(); it != wire.end();)
        {
            if (it->deliverAt <= now)
            {
                due.push_back(std::move(*it));
                it = wire.erase(it);
            }
            else
            {
                ++it;
            }
        }
        std::sort(due.begin(), due.end(),
                  [](const SimFrame& aLeft, const SimFrame& aRight)
                  { return aLeft.deliverAt != aRight.deliverAt ? aLeft.deliverAt < aRight.deliverAt
                                                               : aLeft.order < aRight.order; });
        for (const auto& frame : due)
        {
            if (frame.isData)
            {
                receiver.OnReceive(frame.sequence, frame.channel, AsBytes(frame.payload));
            }
            else
            {
                sender.OnAck(frame.ack, frame.ackBits, now);
            }
        }
        receiver.Deliver(
            [&](uint16_t, uint8_t, std::string_view aPayload)
            {
                if (aPayload != "m" + std::to_string(delivered))
                {
                    inOrder = false;
                }
                else
                {
                    latenciesMs.push_back(static_cast<double>(now - enqueuedAt[delivered]) / 1000.0);
                }
                ++delivered;
                return true;
            });
        // Acks go out right after data arrives, and also ride on the 30 Hz snapshot stream the
        // game sends anyway (the transport piggybacks ack fields on every frame).
        const bool snapshotTick = now % 33'000 == 0;
        if (receiver.AckPending() || snapshotTick)
        {
            transmit({0, 0, false, 0, 0, {}, receiver.AckValue(), receiver.AckBits()}, now);
            receiver.ClearAckPending();
        }
        if (delivered == aScenario.messages && sender.InFlight() == 0 && sender.Backlog() == 0)
        {
            break;
        }
    }

    TransferResult result;
    result.stats = sender.Stats();
    std::sort(latenciesMs.begin(), latenciesMs.end());
    if (!latenciesMs.empty())
    {
        result.p50Ms = latenciesMs[latenciesMs.size() / 2];
        result.p99Ms = latenciesMs[latenciesMs.size() * 99 / 100];
        result.maxMs = latenciesMs.back();
    }
    result.ok = delivered == aScenario.messages && inOrder && sender.InFlight() == 0 && !sender.Failed();
    std::printf("    %-22s %6zu msgs loss=%2.0f%% rtt=%3llums: ok=%d resent=%llu (fast %llu) dupRx=%llu "
                "latency p50=%.0fms p99=%.0fms max=%.0fms sim=%.1fs\n",
                aScenario.name, aScenario.messages, aScenario.loss * 100,
                static_cast<unsigned long long>(2 * aScenario.delayMicros / 1000), result.ok ? 1 : 0,
                static_cast<unsigned long long>(result.stats.resent),
                static_cast<unsigned long long>(result.stats.fastResent),
                static_cast<unsigned long long>(receiver.Stats().duplicates), result.p50Ms, result.p99Ms,
                result.maxMs, static_cast<double>(now) / 1e6);
    return result;
}

void TestLossyTransfers()
{
    std::puts("reliable transfer over simulated links");
    // Russia <-> Warsaw relay <-> Los Angeles is ~320 ms RTT: 160 ms each way.
    const TransferResult clean = RunLossyTransfer({"clean burst", 2000, 0, 0.0, 0.0, 160'000, 0, 1});
    CHECK(clean.ok && clean.stats.resent == 0); // burst latency is queueing: 128-message window per RTT

    const TransferResult game = RunLossyTransfer({"game events 2% loss", 2000, 20, 0.02, 0.01, 160'000, 15'000, 5});
    CHECK(game.ok);
    CHECK(game.p50Ms < 200.0);  // one-way latency when nothing is lost
    CHECK(game.p99Ms < 900.0);  // one-way + about one retransmission timeout
    CHECK(game.maxMs < 2500.0); // double losses

    const TransferResult burstLoss = RunLossyTransfer({"burst 5% loss", 5000, 0, 0.05, 0.02, 160'000, 15'000, 6});
    CHECK(burstLoss.ok && burstLoss.stats.fastResent > 0);

    CHECK(RunLossyTransfer({"stress 30% loss", 3000, 0, 0.30, 0.05, 160'000, 40'000, 2}).ok);
    CHECK(RunLossyTransfer({"stress 60% loss", 1000, 0, 0.60, 0.10, 20'000, 30'000, 3}).ok);
    // More than 65536 messages: exercises 16-bit sequence wrap-around on both sides.
    CHECK(RunLossyTransfer({"sequence wrap", 70'000, 0, 0.05, 0.02, 5'000, 5'000, 4}).ok);
}
// ---- Net_Version -------------------------------------------------------------------------------

void TestVersionString()
{
    std::puts("Net_Version format");
    const std::string version(kVersionString);
    std::printf("    Net_Version() = \"%s\"\n", version.c_str());
    const std::regex format(R"(^CP2077CoopNet (\d+)\.(\d+)\.(\d+) proto (\d+)$)");
    std::smatch parts;
    CHECK(std::regex_match(version, parts, format));
    if (parts.size() == 5)
    {
        CHECK(std::stoul(parts[1].str()) == kVersionMajor);
        CHECK(std::stoul(parts[2].str()) == kVersionMinor);
        CHECK(std::stoul(parts[3].str()) == kVersionPatch);
        CHECK(std::stoul(parts[4].str()) == kProtocolVersion);
    }
    CHECK(version == std::string(kPluginName) + " " + std::string(kSemVer) + " proto " +
                         std::to_string(kProtocolVersion));
    CHECK(kSemVer == std::to_string(kVersionMajor) + "." + std::to_string(kVersionMinor) + "." +
                         std::to_string(kVersionPatch));
}

} // namespace

int main()
{
    TestFrameRoundTrip();
    TestChannelsAndSequences();
    TestRttEstimator();
    TestAckBitsAndDuplicates();
    TestBackpressure();
    TestFastRetransmit();
    TestSackHorizon();
    TestLossyTransfers();
    TestVersionString();
    std::printf("%d checks, %d failures\n", g_checks, g_failures);
    return g_failures == 0 ? EXIT_SUCCESS : EXIT_FAILURE;
}
