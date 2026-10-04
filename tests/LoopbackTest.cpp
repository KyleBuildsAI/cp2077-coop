// Integration test: two Transport instances (the same code the plugin runs) talk through a real
// UDP relay (tools/coopnet_relay.py), usually with simulated latency, jitter and loss.
//
//   coopnet_loopback.exe [host] [port] [reliableCount] [timeoutSeconds]
//
// Exit code 0 = every reliable message arrived exactly once and in order in both directions,
// unreliable snapshots were never duplicated or reordered, invalid sends were refused and the
// remaining peer noticed the disconnect.

#include "core/Transport.hpp"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace
{
using namespace coopnet;
using Clock = std::chrono::steady_clock;

std::mutex g_printMutex;

LogSink MakeLogSink(const char* aName)
{
    return [aName](LogLevel aLevel, std::string_view aMessage)
    {
        static const char* const kLevels[] = {"info", "warn", "error"};
        std::lock_guard lock(g_printMutex);
        std::printf("[%s %s] %.*s\n", aName, kLevels[static_cast<int>(aLevel)], static_cast<int>(aMessage.size()),
                    aMessage.data());
    };
}

struct Received
{
    std::vector<std::string> events;
    std::vector<std::string> reliable;
    long lastSnapshot = -1;
    long snapshots = 0;
    bool snapshotOrderOk = true;
};

void Split(const std::string& aMessage, int& aSender, int& aChannel, std::string& aPayload)
{
    const size_t first = aMessage.find('|');
    const size_t second = aMessage.find('|', first + 1);
    aSender = std::atoi(aMessage.substr(0, first).c_str());
    aChannel = std::atoi(aMessage.substr(first + 1, second - first - 1).c_str());
    aPayload = aMessage.substr(second + 1);
}

void Drain(Transport& aTransport, Received& aReceived, const char* aName, bool aVerbose)
{
    std::string message;
    while (aTransport.Poll(message))
    {
        int sender = 0;
        int channel = 0;
        std::string payload;
        Split(message, sender, channel, payload);
        if (channel == 0)
        {
            aReceived.events.push_back(payload);
            std::lock_guard lock(g_printMutex);
            std::printf("[%s event] %s\n", aName, payload.c_str());
        }
        else if (channel >= 16)
        {
            aReceived.reliable.push_back(payload);
        }
        else
        {
            const long index = std::atol(payload.c_str() + 5); // "snap-<n>"
            if (index <= aReceived.lastSnapshot)
            {
                aReceived.snapshotOrderOk = false;
            }
            aReceived.lastSnapshot = index;
            ++aReceived.snapshots;
        }
        if (aVerbose)
        {
            std::printf("[%s] %s\n", aName, message.c_str());
        }
    }
}

bool HasEvent(const Received& aReceived, const std::string& aPrefix)
{
    for (const auto& event : aReceived.events)
    {
        if (event.rfind(aPrefix, 0) == 0)
        {
            return true;
        }
    }
    return false;
}

bool CheckSequence(const std::vector<std::string>& aMessages, const std::string& aPrefix, int aCount)
{
    if (static_cast<int>(aMessages.size()) != aCount)
    {
        return false;
    }
    for (int index = 0; index < aCount; ++index)
    {
        if (aMessages[index] != aPrefix + std::to_string(index))
        {
            std::printf("  mismatch at %d: '%s'\n", index, aMessages[index].c_str());
            return false;
        }
    }
    return true;
}
} // namespace

int main(int argc, char** argv)
{
    const std::string host = argc > 1 ? argv[1] : "127.0.0.1";
    const int port = argc > 2 ? std::atoi(argv[2]) : 11779;
    const int reliableCount = argc > 3 ? std::atoi(argv[3]) : 500;
    const int timeoutSeconds = argc > 4 ? std::atoi(argv[4]) : 60;
    const int backCount = reliableCount / 2;

    Transport alpha;
    Transport bravo;
    alpha.SetLogSink(MakeLogSink("A"));
    bravo.SetLogSink(MakeLogSink("B"));
    Received alphaReceived;
    Received bravoReceived;

    bool ok = true;
    if (alpha.Connect(host, 0, "itest") || alpha.Connect("", port, "itest"))
    {
        std::puts("FAIL: invalid Connect arguments were accepted");
        ok = false;
    }
    if (!alpha.Connect(host, port, "itest") || !bravo.Connect(host, port, "itest"))
    {
        std::puts("FAIL: Connect returned false");
        return EXIT_FAILURE;
    }

    const auto start = Clock::now();
    auto elapsed = [&start] { return std::chrono::duration<double>(Clock::now() - start).count(); };

    while (elapsed() < 10.0 && (alpha.PeerCount() == 0 || bravo.PeerCount() == 0))
    {
        Drain(alpha, alphaReceived, "A", false);
        Drain(bravo, bravoReceived, "B", false);
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    if (alpha.PeerCount() == 0 || bravo.PeerCount() == 0)
    {
        std::puts("FAIL: peers never saw each other");
        std::printf("A stats: %s\nB stats: %s\n", alpha.StatsJson().c_str(), bravo.StatsJson().c_str());
        return EXIT_FAILURE;
    }
    std::printf("peers connected after %.2fs (A id %d, B id %d)\n", elapsed(), alpha.LocalId(), bravo.LocalId());

    // Control channel is reserved, channels above 31 do not exist, payloads must fit one datagram.
    if (alpha.Send(0, "x") || alpha.Send(32, "x") || alpha.Send(16, std::string(kMaxPayloadSize + 1, 'x')))
    {
        std::puts("FAIL: invalid Send was accepted");
        ok = false;
    }

    int alphaSent = 0;
    int bravoSent = 0;
    long snapshotIndex = 0;
    auto lastSnapshot = Clock::now();
    const double transferStart = elapsed();
    while (elapsed() < timeoutSeconds)
    {
        // A: bursts of reliable events + 60 Hz snapshots; B: reliable replies.
        for (int burst = 0; burst < 25 && alphaSent < reliableCount; ++burst)
        {
            if (!alpha.Send(16 + (alphaSent % 4), "rel-" + std::to_string(alphaSent)))
            {
                break;
            }
            ++alphaSent;
        }
        for (int burst = 0; burst < 10 && bravoSent < backCount; ++burst)
        {
            if (!bravo.Send(20, "back-" + std::to_string(bravoSent)))
            {
                break;
            }
            ++bravoSent;
        }
        if (Clock::now() - lastSnapshot >= std::chrono::milliseconds(16))
        {
            alpha.Send(1, "snap-" + std::to_string(snapshotIndex++));
            lastSnapshot = Clock::now();
        }
        Drain(alpha, alphaReceived, "A", false);
        Drain(bravo, bravoReceived, "B", false);
        if (static_cast<int>(bravoReceived.reliable.size()) >= reliableCount &&
            static_cast<int>(alphaReceived.reliable.size()) >= backCount)
        {
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
    const double transferSeconds = elapsed() - transferStart;

    const bool forwardOk = CheckSequence(bravoReceived.reliable, "rel-", reliableCount);
    const bool backOk = CheckSequence(alphaReceived.reliable, "back-", backCount);
    std::printf("reliable A->B: %zu/%d in order=%d | B->A: %zu/%d in order=%d | %.2fs\n",
                bravoReceived.reliable.size(), reliableCount, forwardOk ? 1 : 0, alphaReceived.reliable.size(),
                backCount, backOk ? 1 : 0, transferSeconds);
    std::printf("unreliable A->B: sent %ld, received %ld, never reordered/duplicated=%d\n", snapshotIndex,
                bravoReceived.snapshots, bravoReceived.snapshotOrderOk ? 1 : 0);
    ok = ok && forwardOk && backOk && bravoReceived.snapshotOrderOk && bravoReceived.snapshots > 0;

    // Idle for a few ping rounds so the RTT estimate settles, then show the final counters.
    for (int second = 0; second < 3; ++second)
    {
        const auto idleUntil = Clock::now() + std::chrono::seconds(1);
        while (Clock::now() < idleUntil)
        {
            Drain(alpha, alphaReceived, "A", false);
            Drain(bravo, bravoReceived, "B", false);
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
        }
        std::printf("idle %ds: A %s\n", second + 1, alpha.StatsJson().c_str());
    }
    std::printf("B stats: %s\n", bravo.StatsJson().c_str());

    // Disconnect A; B must see it leave (BYE -> relay -> PEERS without A).
    alpha.Disconnect();
    const double leaveStart = elapsed();
    while (elapsed() - leaveStart < 5.0 && !HasEvent(bravoReceived, "peer_leave"))
    {
        Drain(bravo, bravoReceived, "B", false);
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    const bool leaveSeen = HasEvent(bravoReceived, "peer_leave");
    std::printf("B saw A leave: %d (%.2fs)\n", leaveSeen ? 1 : 0, elapsed() - leaveStart);
    ok = ok && leaveSeen && HasEvent(alphaReceived, "welcome") && HasEvent(bravoReceived, "peer_join");
    if (bravo.Send(16, "nobody is listening"))
    {
        std::puts("FAIL: Send with no peers was accepted");
        ok = false;
    }
    bravo.Disconnect();

    std::puts(ok ? "LOOPBACK PASS" : "LOOPBACK FAIL");
    return ok ? EXIT_SUCCESS : EXIT_FAILURE;
}
