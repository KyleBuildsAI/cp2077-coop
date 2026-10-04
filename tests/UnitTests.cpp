// Deterministic tests for the frame codec and the reliability layer (no sockets, simulated clock),
// plus the logic behind the Net_NowMs / Net_Version natives, native registration checks, String
// results and the startup summary line, and the transport's behaviour while a host name resolves.

#include "core/Clock.hpp"
#include "core/LoadReport.hpp"
#include "core/NativeRegistration.hpp"
#include "core/Protocol.hpp"
#include "core/Reliability.hpp"
#include "core/ScriptString.hpp"
#include "core/Transport.hpp"
#include "core/Version.hpp"

#include <winsock2.h>
#include <ws2tcpip.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <deque>
#include <map>
#include <memory>
#include <random>
#include <regex>
#include <set>
#include <string>
#include <thread>
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
// ---- Net_NowMs ---------------------------------------------------------------------------------

// The conversion is constexpr, so the epoch constant is also checked at compile time.
static_assert(FileTimeTicksToUnixMs(static_cast<uint64_t>(kFileTimeTicksAtUnixEpoch)) == 0.0);

void TestClockConversion()
{
    std::puts("Net_NowMs: FILETIME -> Unix epoch milliseconds");
    const auto epoch = static_cast<uint64_t>(kFileTimeTicksAtUnixEpoch);
    CHECK(FileTimeTicksToUnixMs(epoch) == 0.0);
    // 2000-01-01T00:00:00Z: FILETIME 125911584000000000, Unix 946684800000 ms (independent constants).
    CHECK(FileTimeTicksToUnixMs(125'911'584'000'000'000ULL) == 946'684'800'000.0);
    // 2026-01-01T00:00:00Z = 1767225600 s: whole milliseconds stay exact.
    const uint64_t newYear2026 = epoch + 1'767'225'600ULL * 10'000'000ULL;
    CHECK(FileTimeTicksToUnixMs(newYear2026) == 1'767'225'600'000.0);
    // 12345 ticks = 1.2345 ms: the fraction survives and floor() gives the whole millisecond.
    const double withFraction = FileTimeTicksToUnixMs(newYear2026 + 12'345);
    CHECK(std::fabs(withFraction - 1'767'225'600'001.2345) < 0.001);
    CHECK(std::floor(withFraction) == 1'767'225'600'001.0);
    // One 100 ns tick near the epoch, and a time before 1970.
    CHECK(std::fabs(FileTimeTicksToUnixMs(epoch + 1) - 0.0001) < 1e-12);
    CHECK(FileTimeTicksToUnixMs(epoch - 10'000) == -1.0);
}

void TestClockNow()
{
    std::puts("Net_NowMs: live clock (GetSystemTimePreciseAsFileTime)");
    using namespace std::chrono;

    const double now = UnixNowMs();
    const auto systemMs = static_cast<double>(duration_cast<milliseconds>(system_clock::now().time_since_epoch()).count());
    const double timeMs = static_cast<double>(std::time(nullptr)) * 1000.0;
    std::printf("    now=%.4f ms, system_clock-now=%.3f ms, time()-now=%.0f ms\n", now, systemMs - now, timeMs - now);
    CHECK(now > 1'700'000'000'000.0 && now < 4'102'444'800'000.0); // between 2023-11 and 2100
    CHECK(std::fabs(systemMs - now) < 50.0);
    CHECK(std::fabs(timeMs - now) < 2000.0);
    CHECK(now < 9'007'199'254'740'992.0); // below 2^53: every whole millisecond is exact

    // Monotonic within tolerance over a tight loop, with sub-millisecond resolution, and cheap.
    constexpr int kCalls = 200'000;
    double previous = UnixNowMs();
    double maxBackwardMs = 0.0;
    int fractionalValues = 0;
    std::set<double> distinct;
    const auto loopStart = steady_clock::now();
    for (int call = 0; call < kCalls; ++call)
    {
        const double value = UnixNowMs();
        maxBackwardMs = std::max(maxBackwardMs, previous - value);
        if (value != std::floor(value))
        {
            ++fractionalValues;
        }
        if (distinct.size() < 4096)
        {
            distinct.insert(value);
        }
        previous = value;
    }
    const double loopNs = static_cast<double>(duration_cast<nanoseconds>(steady_clock::now() - loopStart).count());
    std::printf("    %d calls: max backward step %.4f ms, %d with a sub-ms fraction, >=%zu distinct, %.1f ns/call\n",
                kCalls, maxBackwardMs, fractionalValues, distinct.size(), loopNs / kCalls);
    CHECK(maxBackwardMs <= 1.0);
    CHECK(fractionalValues > kCalls / 2);
    CHECK(distinct.size() > 100);
    CHECK(loopNs / kCalls < 2000.0); // per-frame stamping must be free compared to a 16 ms frame

    // Elapsed wall time tracks the steady (QPC) clock across a sleep.
    const double wallStart = UnixNowMs();
    const auto steadyStart = steady_clock::now();
    std::this_thread::sleep_for(milliseconds(60));
    const double wallElapsed = UnixNowMs() - wallStart;
    const double steadyElapsed =
        static_cast<double>(duration_cast<microseconds>(steady_clock::now() - steadyStart).count()) / 1000.0;
    std::printf("    60 ms sleep: Net_NowMs advanced %.3f ms, steady_clock %.3f ms\n", wallElapsed, steadyElapsed);
    CHECK(wallElapsed >= 59.0 && wallElapsed < 1000.0);
    CHECK(std::fabs(wallElapsed - steadyElapsed) < 5.0);
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

// ---- startup summary line ----------------------------------------------------------------------

void TestLoadReport()
{
    std::puts("startup summary line");
    std::set<std::string_view> unique(kNativeNames.begin(), kNativeNames.end());
    CHECK(unique.size() == kNativeNames.size());
    CHECK(std::all_of(kNativeNames.begin(), kNativeNames.end(), [](std::string_view aName) {
        return aName.starts_with("Net_");
    }));
    CHECK(unique.contains("Net_NowMs") && unique.contains("Net_Version"));

    LoadReport complete;
    complete.registered.assign(kNativeNames.begin(), kNativeNames.end());
    complete.scriptsPath = R"(G:\Game\red4ext\plugins\CP2077CoopNet\Scripts)";
    complete.scriptsAdded = true;
    const std::string line = FormatLoadReport(complete);
    std::printf("    %s\n", line.c_str());
    CHECK(IsLoadComplete(complete));
    CHECK(MissingNatives(complete).empty());
    CHECK(line == std::string(kVersionString) +
                      ": registered Net_* natives (10/10): Net_Connect, Net_ConnectRoom, Net_Disconnect, Net_Send, "
                      "Net_SendTo, Net_Poll, Net_Stats, Net_LocalId, Net_NowMs, Net_Version; scripts added: "
                      R"(G:\Game\red4ext\plugins\CP2077CoopNet\Scripts)");
    CHECK(line.find('\n') == std::string::npos);

    LoadReport broken;
    broken.registered = {"Net_Connect", "Net_ConnectRoom", "Net_Disconnect", "Net_Send", "Net_SendTo",
                         "Net_Poll",    "Net_Stats",       "Net_LocalId",    "Net_Version"};
    broken.scriptsPath = R"(G:\Game\red4ext\plugins\CP2077CoopNet\Scripts)";
    broken.scriptsError = "RED4ext refused the folder";
    const std::string brokenLine = FormatLoadReport(broken);
    std::printf("    %s\n", brokenLine.c_str());
    CHECK(!IsLoadComplete(broken));
    CHECK(MissingNatives(broken) == std::vector<std::string>{"Net_NowMs"});
    CHECK(brokenLine.find("registered Net_* natives (9/10)") != std::string::npos);
    CHECK(brokenLine.find("; MISSING: Net_NowMs;") != std::string::npos);
    CHECK(brokenLine.find("; scripts NOT added: RED4ext refused the folder (G:") != std::string::npos);

    LoadReport scriptsOnly;
    scriptsOnly.scriptsAdded = true;
    scriptsOnly.scriptsPath = "X";
    const std::string emptyLine = FormatLoadReport(scriptsOnly);
    CHECK(!IsLoadComplete(scriptsOnly));
    CHECK(emptyLine.find("(0/10): none; MISSING: Net_Connect,") != std::string::npos);

    LoadReport withReason;
    withReason.registered.assign(kNativeNames.begin(), kNativeNames.end() - 1); // all but Net_Version
    withReason.failed.push_back({"Net_Version", "RTTI lookup by name found nothing after RegisterFunction"});
    withReason.scriptsAdded = true;
    withReason.scriptsPath = "X";
    const std::string reasonLine = FormatLoadReport(withReason);
    std::printf("    %s\n", reasonLine.c_str());
    CHECK(reasonLine.find("registered Net_* natives (9/10)") != std::string::npos);
    CHECK(reasonLine.find("; MISSING: Net_Version (RTTI lookup by name found nothing after RegisterFunction); "
                          "scripts added: X") != std::string::npos);
}

// ---- native registration checks ----------------------------------------------------------------
// Fakes with the observable behaviour of RED4ext SDK 1.0.0: AddParam/SetReturnType return false and
// change nothing when the type name is unknown, RegisterFunction returns nothing, and GetFunction
// looks the name up.

bool IsFundamentalType(std::string_view aType)
{
    return aType == "String" || aType == "Int32" || aType == "Bool" || aType == "Double";
}

struct FakeFunction
{
    std::string name;
    std::vector<std::string> params;
    std::string returnType;

    bool AddParam(const char* aType, const char* aName)
    {
        if (!IsFundamentalType(aType))
        {
            return false;
        }
        params.push_back(std::string(aType) + " " + aName);
        return true;
    }

    bool SetReturnType(const char* aType)
    {
        if (!IsFundamentalType(aType))
        {
            return false;
        }
        returnType = aType;
        return true;
    }
};

struct FakeRtti
{
    bool dropRegistrations = false; // RegisterFunction silently does nothing
    bool keepFirst = false;         // a name that is already taken keeps its first function
    int registerCalls = 0;
    std::map<std::string, FakeFunction*> functions;

    void RegisterFunction(FakeFunction* aFunction)
    {
        ++registerCalls;
        if (dropRegistrations || (keepFirst && functions.contains(aFunction->name)))
        {
            return;
        }
        functions[aFunction->name] = aFunction;
    }

    FakeFunction* GetFunction(const char* aName)
    {
        const auto found = functions.find(aName);
        return found == functions.end() ? nullptr : found->second;
    }
};

void TestNativeRegistration()
{
    std::puts("native registration checks");
    {
        FakeRtti rtti;
        FakeFunction connect{"Net_Connect"};
        const std::string problem =
            RegisterNative(rtti, connect, "Net_Connect", {{"String", "host"}, {"Int32", "port"}}, "Bool");
        CHECK(problem.empty());
        CHECK(rtti.GetFunction("Net_Connect") == &connect);
        CHECK(connect.params == (std::vector<std::string>{"String host", "Int32 port"}));
        CHECK(connect.returnType == "Bool");
        FakeFunction disconnect{"Net_Disconnect"};
        CHECK(RegisterNative(rtti, disconnect, "Net_Disconnect", {}, nullptr).empty());
        CHECK(disconnect.returnType.empty());
    }
    {
        // a parameter type the RTTI does not know (typo "Int"): reported, and never registered
        FakeRtti rtti;
        FakeFunction send{"Net_Send"};
        const std::string problem =
            RegisterNative(rtti, send, "Net_Send", {{"Int", "channel"}, {"String", "payload"}}, "Bool");
        std::printf("    unknown parameter type -> \"%s\"\n", problem.c_str());
        CHECK(problem == "parameter 'channel' of type Int not added: type not in RTTI, native not registered");
        CHECK(rtti.registerCalls == 0);
        CHECK(rtti.GetFunction("Net_Send") == nullptr);
    }
    {
        FakeRtti rtti;
        FakeFunction poll{"Net_Poll"};
        const std::string problem = RegisterNative(rtti, poll, "Net_Poll", {}, "Str");
        CHECK(problem == "return type Str not set: type not in RTTI, native not registered");
        CHECK(rtti.registerCalls == 0);
    }
    {
        // RegisterFunction returns void; a registration the system dropped shows up only in the lookup
        FakeRtti rtti;
        rtti.dropRegistrations = true;
        FakeFunction version{"Net_Version"};
        const std::string problem = RegisterNative(rtti, version, "Net_Version", {}, "String");
        std::printf("    dropped registration -> \"%s\"\n", problem.c_str());
        CHECK(problem == "RTTI lookup by name found nothing after RegisterFunction");
        CHECK(rtti.registerCalls == 1);
    }
    {
        FakeRtti rtti;
        rtti.keepFirst = true;
        FakeFunction other{"Net_NowMs"};
        rtti.RegisterFunction(&other);
        FakeFunction ours{"Net_NowMs"};
        const std::string problem = RegisterNative(rtti, ours, "Net_NowMs", {}, "Double");
        CHECK(problem == "RTTI lookup by name returned a different function (name already taken?)");
    }
    {
        // the same flow Main.cpp runs at post-register: one native fails, the line says which and why
        FakeRtti rtti;
        std::vector<std::unique_ptr<FakeFunction>> functions;
        LoadReport report;
        for (const std::string_view name : kNativeNames)
        {
            functions.push_back(std::make_unique<FakeFunction>(FakeFunction{std::string(name)}));
            const char* returnType = name == "Net_NowMs" ? "Float64" : "String";
            const std::string problem =
                RegisterNative(rtti, *functions.back(), functions.back()->name.c_str(), {}, returnType);
            if (problem.empty())
            {
                report.registered.emplace_back(name);
            }
            else
            {
                report.failed.push_back({std::string(name), problem});
            }
        }
        report.scriptsAdded = true;
        report.scriptsPath = "X";
        const std::string line = FormatLoadReport(report);
        std::printf("    %s\n", line.c_str());
        CHECK(!IsLoadComplete(report));
        CHECK(report.registered.size() == 9);
        CHECK(line.find("registered Net_* natives (9/10)") != std::string::npos);
        CHECK(line.find("; MISSING: Net_NowMs (return type Float64 not set: type not in RTTI, native not "
                        "registered); scripts added: X") != std::string::npos);
    }
}

// ---- String results ----------------------------------------------------------------------------
// Models RED4ext::CString ownership: strings of 20 bytes or more own a heap buffer. The copy
// assignment releases the destination's old buffer (the game's CString_copy); the move assignment
// takes the source's buffer and does not release the old one (SDK 1.0.0 operator=(CString&&)).

struct MockScriptString
{
    static inline int liveBuffers = 0;

    std::string text;
    bool heap = false;

    MockScriptString() = default;

    MockScriptString(const char* aText, uint32_t aLength)
        : text(aText, aLength)
        , heap(aLength >= 20)
    {
        liveBuffers += heap ? 1 : 0;
    }

    MockScriptString(const MockScriptString& aOther)
        : text(aOther.text)
        , heap(aOther.heap)
    {
        liveBuffers += heap ? 1 : 0;
    }

    ~MockScriptString()
    {
        liveBuffers -= heap ? 1 : 0;
    }

    MockScriptString& operator=(const MockScriptString& aOther)
    {
        if (this != &aOther)
        {
            liveBuffers -= heap ? 1 : 0;
            text = aOther.text;
            heap = aOther.heap;
            liveBuffers += heap ? 1 : 0;
        }
        return *this;
    }

    MockScriptString& operator=(MockScriptString&& aOther) noexcept
    {
        text = std::move(aOther.text); // the old buffer is overwritten, never released
        heap = aOther.heap;
        aOther.heap = false;
        aOther.text.clear();
        return *this;
    }
};

void TestScriptString()
{
    std::puts("String results into a live slot (redscript `let raw = Net_Poll();` in a loop)");
    const std::string payload = "1|9|NP1|u|123456|42|1790000000000.000|1.000|2.000|3.000|90.00|walk";
    MockScriptString::liveBuffers = 0;
    {
        MockScriptString slot; // one local, reused by every iteration
        for (int index = 0; index < 100; ++index)
        {
            AssignScriptString(&slot, payload);
        }
        CHECK(slot.text == payload);
        CHECK(MockScriptString::liveBuffers == 1);
        AssignScriptString(&slot, std::string_view{});
        CHECK(slot.text.empty());
        CHECK(MockScriptString::liveBuffers == 0);
    }
    const int afterHelper = MockScriptString::liveBuffers;
    {
        // the pre-0.1.2 code: *aOut = CString(...) picks the move assignment
        MockScriptString slot;
        for (int index = 0; index < 100; ++index)
        {
            slot = MockScriptString(payload.data(), static_cast<uint32_t>(payload.size()));
        }
    }
    const int afterMove = MockScriptString::liveBuffers;
    std::printf("    100 polls: buffers leaked with AssignScriptString=%d, with move assignment=%d\n", afterHelper,
                afterMove);
    CHECK(afterHelper == 0);
    CHECK(afterMove == 99); // the model reproduces the leak, so the check above is meaningful
    AssignScriptString<MockScriptString>(nullptr, payload); // null result slot: no write
    MockScriptString::liveBuffers = 0;
}

// ---- stopping while the relay host name is still resolving -------------------------------------
// Single-label names that do not exist go through LLMNR/NetBIOS on Windows, which takes about a
// second. Every stop below must return long before that.

std::string UnresolvableSingleLabelName(std::mt19937& aRandom)
{
    char suffix[16];
    std::snprintf(suffix, sizeof(suffix), "%08x", static_cast<unsigned>(aRandom()));
    return std::string("coopnet-dnsprobe-") + suffix;
}

double MillisSince(std::chrono::steady_clock::time_point aStart)
{
    return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - aStart).count();
}

std::vector<std::string> DrainEvents(Transport& aTransport)
{
    std::vector<std::string> events;
    std::string message;
    while (aTransport.Poll(message))
    {
        events.push_back(message);
    }
    return events;
}

bool WaitForEvent(Transport& aTransport, const std::string& aPrefix, std::vector<std::string>& aSeen)
{
    const auto start = std::chrono::steady_clock::now();
    while (MillisSince(start) < 3000.0)
    {
        for (const std::string& event : DrainEvents(aTransport))
        {
            aSeen.push_back(event);
        }
        for (const std::string& event : aSeen)
        {
            if (event.starts_with(aPrefix))
            {
                return true;
            }
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    return false;
}

void TestStopWhileResolving()
{
    std::puts("Disconnect, reconnect and destroy while the relay host name resolves");
    std::mt19937 random(std::random_device{}());
    constexpr double kStopLimitMs = 250.0;

    // Reference: a plain blocking lookup of such a name on this machine.
    WSADATA data{};
    CHECK(WSAStartup(MAKEWORD(2, 2), &data) == 0);
    const std::string reference = UnresolvableSingleLabelName(random);
    addrinfo hints{};
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_DGRAM;
    addrinfo* result = nullptr;
    const auto lookupStart = std::chrono::steady_clock::now();
    const int status = getaddrinfo(reference.c_str(), "11779", &hints, &result);
    const double blockingMs = MillisSince(lookupStart);
    if (result != nullptr)
    {
        freeaddrinfo(result);
    }
    WSACleanup();
    std::printf("    blocking getaddrinfo('%s') took %.1f ms (status %d)\n", reference.c_str(), blockingMs, status);
    if (blockingMs < 2.0 * kStopLimitMs)
    {
        std::puts("    note: lookups fail fast on this machine, so the timings below cannot tell the old code apart");
    }

    {
        Transport transport;
        CHECK(transport.Connect(UnresolvableSingleLabelName(random), 11779, "dns"));
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
        const bool resolving = transport.State() == ConnectionState::Resolving;
        const auto start = std::chrono::steady_clock::now();
        transport.Disconnect();
        const double stopMs = MillisSince(start);
        std::printf("    Disconnect() while %s: %.1f ms\n", resolving ? "resolving" : ToString(transport.State()),
                    stopMs);
        CHECK(resolving);
        CHECK(stopMs < kStopLimitMs);
        CHECK(transport.State() == ConnectionState::Idle);
        const std::vector<std::string> events = DrainEvents(transport);
        CHECK(events == std::vector<std::string>{"0|0|disconnected"});
    }
    {
        Transport transport;
        CHECK(transport.Connect(UnresolvableSingleLabelName(random), 11779, "dns"));
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
        const auto start = std::chrono::steady_clock::now();
        CHECK(transport.Connect(UnresolvableSingleLabelName(random), 11779, "dns"));
        const double reconnectMs = MillisSince(start);
        const auto stopStart = std::chrono::steady_clock::now();
        transport.Disconnect();
        const double stopMs = MillisSince(stopStart);
        std::printf("    second Connect() while resolving: %.1f ms, then Disconnect(): %.1f ms\n", reconnectMs,
                    stopMs);
        CHECK(reconnectMs < kStopLimitMs);
        CHECK(stopMs < kStopLimitMs);
    }
    {
        // the path Main(Unload) takes: g_transport.reset()
        auto transport = std::make_unique<Transport>();
        CHECK(transport->Connect(UnresolvableSingleLabelName(random), 11779, "dns"));
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
        const auto start = std::chrono::steady_clock::now();
        transport.reset();
        const double destroyMs = MillisSince(start);
        std::printf("    destroying the Transport while resolving: %.1f ms\n", destroyMs);
        CHECK(destroyMs < kStopLimitMs);
    }
    {
        // the overlapped lookup still resolves real names and reports failures
        Transport transport;
        std::vector<std::string> seen;
        CHECK(transport.Connect("localhost", 9, "dns")); // discard port: no bench relay there
        const bool connecting = WaitForEvent(transport, "0|0|connecting 127.0.0.1:9", seen);
        std::printf("    localhost -> %s\n", connecting ? "0|0|connecting 127.0.0.1:9" : "no connecting event");
        CHECK(connecting);
        transport.Disconnect();

        seen.clear();
        CHECK(transport.Connect("relay.coopnet-test.invalid", 11779, "dns"));
        const bool failed = WaitForEvent(transport, "0|0|error cannot resolve 'relay.coopnet-test.invalid'", seen);
        std::printf("    relay.coopnet-test.invalid -> %s\n", failed ? seen.back().c_str() : "no error event");
        CHECK(failed);
        CHECK(transport.State() == ConnectionState::Error);
    }
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
    TestClockConversion();
    TestClockNow();
    TestVersionString();
    TestLoadReport();
    TestNativeRegistration();
    TestScriptString();
    TestStopWhileResolving();
    std::printf("%d checks, %d failures\n", g_checks, g_failures);
    return g_failures == 0 ? EXIT_SUCCESS : EXIT_FAILURE;
}
