// Tests for the logic behind the natives: Net_NowMs / Net_Version, native registration checks,
// String results and the startup summary line; the transport's argument checks and pose format;
// the transport's behaviour while a host name resolves; and its protocol v2 handshake against a
// scripted fake relay on 127.0.0.1 (no answer, REJECT, an expired cookie, WELCOME, relay silence
// and a relay DISCONNECT). The session itself is covered by tools/run_loopback.py against
// relay_v2.py.

#include "core/Clock.hpp"
#include "core/LoadReport.hpp"
#include "core/NativeRegistration.hpp"
#include "core/ScriptString.hpp"
#include "core/Transport.hpp"
#include "core/Version.hpp"
#include "v2/V2Codec.hpp"
#include "v2/V2Hash.hpp"
#include "v2/V2Reliability.hpp"

#include <winsock2.h>
#include <ws2tcpip.h>
#include <mswsock.h>

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
    const std::regex format(R"(^CP2077CoopNet (\d+)\.(\d+)\.(\d+)(-(alpha|beta|rc)\.(\d+))? proto (\d+)\.(\d+)$)");
    std::smatch parts;
    CHECK(std::regex_match(version, parts, format));
    if (parts.size() == 9)
    {
        CHECK(std::stoul(parts[1].str()) == kVersionMajor);
        CHECK(std::stoul(parts[2].str()) == kVersionMinor);
        CHECK(std::stoul(parts[3].str()) == kVersionPatch);
        CHECK(std::stoul(parts[7].str()) == coopv2::kProtoMajor);
        CHECK(std::stoul(parts[8].str()) == coopv2::kProtoMinor);
        const char* const types[] = {"", "alpha", "beta", "rc"};
        CHECK(kPrereleaseType <= 3 && parts[5].str() == types[kPrereleaseType]);
        CHECK(kPrereleaseType == 0 ? !parts[4].matched : std::stoul(parts[6].str()) == kPrereleaseNumber);
    }
    CHECK(version == std::string(kPluginName) + " " + std::string(kSemVer) + " proto 2.1");
    CHECK(kWireProtocol == "2.1");
    std::string numbers = std::to_string(kVersionMajor) + "." + std::to_string(kVersionMinor) + "." +
                          std::to_string(kVersionPatch);
    if constexpr (kPrereleaseType != 0)
    {
        const char* const types[] = {"", "alpha", "beta", "rc"};
        numbers += std::string("-") + types[kPrereleaseType] + "." + std::to_string(kPrereleaseNumber);
    }
    CHECK(kSemVer == numbers);
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
    CHECK(unique.contains("Net_ConnectV2") && unique.contains("Net_PushPlayer") && unique.contains("Net_SampleRemote"));

    LoadReport complete;
    complete.registered.assign(kNativeNames.begin(), kNativeNames.end());
    complete.scriptsPath = R"(G:\Game\red4ext\plugins\CP2077CoopNet\Scripts)";
    complete.scriptsAdded = true;
    const std::string line = FormatLoadReport(complete);
    std::printf("    %s\n", line.c_str());
    CHECK(IsLoadComplete(complete));
    CHECK(MissingNatives(complete).empty());
    CHECK(line == std::string(kVersionString) +
                      ": registered Net_* natives (13/13): Net_Connect, Net_ConnectRoom, Net_Disconnect, Net_Send, "
                      "Net_SendTo, Net_Poll, Net_Stats, Net_LocalId, Net_NowMs, Net_Version, Net_ConnectV2, "
                      "Net_PushPlayer, Net_SampleRemote; scripts added: "
                      R"(G:\Game\red4ext\plugins\CP2077CoopNet\Scripts)");
    CHECK(line.find('\n') == std::string::npos);

    LoadReport broken;
    broken.registered = {"Net_Connect", "Net_ConnectRoom", "Net_Disconnect", "Net_Send",       "Net_SendTo",
                         "Net_Poll",    "Net_Stats",       "Net_LocalId",    "Net_Version",    "Net_ConnectV2",
                         "Net_PushPlayer", "Net_SampleRemote"};
    broken.scriptsPath = R"(G:\Game\red4ext\plugins\CP2077CoopNet\Scripts)";
    broken.scriptsError = "RED4ext refused the folder";
    const std::string brokenLine = FormatLoadReport(broken);
    std::printf("    %s\n", brokenLine.c_str());
    CHECK(!IsLoadComplete(broken));
    CHECK(MissingNatives(broken) == std::vector<std::string>{"Net_NowMs"});
    CHECK(brokenLine.find("registered Net_* natives (12/13)") != std::string::npos);
    CHECK(brokenLine.find("; MISSING: Net_NowMs;") != std::string::npos);
    CHECK(brokenLine.find("; scripts NOT added: RED4ext refused the folder (G:") != std::string::npos);

    LoadReport scriptsOnly;
    scriptsOnly.scriptsAdded = true;
    scriptsOnly.scriptsPath = "X";
    const std::string emptyLine = FormatLoadReport(scriptsOnly);
    CHECK(!IsLoadComplete(scriptsOnly));
    CHECK(emptyLine.find("(0/13): none; MISSING: Net_Connect,") != std::string::npos);

    LoadReport withReason;
    for (const std::string_view name : kNativeNames)
    {
        if (name != "Net_Version")
        {
            withReason.registered.emplace_back(name);
        }
    }
    withReason.failed.push_back({"Net_Version", "RTTI lookup by name found nothing after RegisterFunction"});
    withReason.scriptsAdded = true;
    withReason.scriptsPath = "X";
    const std::string reasonLine = FormatLoadReport(withReason);
    std::printf("    %s\n", reasonLine.c_str());
    CHECK(reasonLine.find("registered Net_* natives (12/13)") != std::string::npos);
    CHECK(reasonLine.find("; MISSING: Net_Version (RTTI lookup by name found nothing after RegisterFunction); "
                          "scripts added: X") != std::string::npos);
}

// ---- native registration checks ----------------------------------------------------------------
// Fakes with the observable behaviour of RED4ext SDK 1.0.0: AddParam/SetReturnType return false and
// change nothing when the type name is unknown, RegisterFunction returns nothing, and GetFunction
// looks the name up.

bool IsFundamentalType(std::string_view aType)
{
    return aType == "String" || aType == "Int32" || aType == "Bool" || aType == "Double" || aType == "Float";
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
        FakeFunction push{"Net_PushPlayer"};
        CHECK(RegisterNative(rtti, push, "Net_PushPlayer",
                             {{"Float", "x"}, {"Float", "y"}, {"Float", "z"}, {"Float", "yaw"}, {"Float", "pitch"},
                              {"Float", "vx"}, {"Float", "vy"}, {"Float", "vz"}, {"Int32", "moveState"},
                              {"Int32", "flags"}, {"Int32", "health"}},
                             "Bool")
                  .empty());
        CHECK(push.params.size() == 11 && push.params[4] == "Float pitch" && push.params[10] == "Int32 health");
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
        CHECK(report.registered.size() == kNativeNames.size() - 1);
        CHECK(line.find("registered Net_* natives (12/13)") != std::string::npos);
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

// Disconnect, a reconnect through Connect, and the destructor (the Main(Unload) path) must each
// return long before a lookup that is still in progress would finish.
void CheckStopsDuringSlowLookup(std::mt19937& aRandom, double aStopLimitMs)
{
    {
        Transport transport;
        CHECK(transport.Connect(UnresolvableSingleLabelName(aRandom), 11779, "dns"));
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
        const bool resolving = transport.State() == ConnectionState::Resolving;
        const auto start = std::chrono::steady_clock::now();
        transport.Disconnect();
        const double stopMs = MillisSince(start);
        std::printf("    Disconnect() while %s: %.1f ms\n", resolving ? "resolving" : ToString(transport.State()),
                    stopMs);
        CHECK(resolving);
        CHECK(stopMs < aStopLimitMs);
        CHECK(transport.State() == ConnectionState::Idle);
        const std::vector<std::string> events = DrainEvents(transport);
        CHECK(events == std::vector<std::string>{"0|0|disconnected"});
    }
    {
        Transport transport;
        CHECK(transport.Connect(UnresolvableSingleLabelName(aRandom), 11779, "dns"));
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
        const auto start = std::chrono::steady_clock::now();
        CHECK(transport.Connect(UnresolvableSingleLabelName(aRandom), 11779, "dns"));
        const double reconnectMs = MillisSince(start);
        const auto stopStart = std::chrono::steady_clock::now();
        transport.Disconnect();
        const double stopMs = MillisSince(stopStart);
        std::printf("    second Connect() while resolving: %.1f ms, then Disconnect(): %.1f ms\n", reconnectMs,
                    stopMs);
        CHECK(reconnectMs < aStopLimitMs);
        CHECK(stopMs < aStopLimitMs);
    }
    {
        // the path Main(Unload) takes: g_transport.reset()
        auto transport = std::make_unique<Transport>();
        CHECK(transport->Connect(UnresolvableSingleLabelName(aRandom), 11779, "dns"));
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
        const auto start = std::chrono::steady_clock::now();
        transport.reset();
        const double destroyMs = MillisSince(start);
        std::printf("    destroying the Transport while resolving: %.1f ms\n", destroyMs);
        CHECK(destroyMs < aStopLimitMs);
    }
}

// The overlapped lookup still resolves real names and reports failures.
void CheckLookupOutcomes()
{
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

    // A stop can only be caught mid-lookup where such a lookup blocks for a while (LLMNR/NetBIOS).
    // Where it fails at once, the session has already ended in an error before the stop, so the
    // timing checks are skipped rather than reported as failures.
    if (blockingMs >= 2.0 * kStopLimitMs)
    {
        CheckStopsDuringSlowLookup(random, kStopLimitMs);
    }
    else
    {
        std::puts("    skipped the stop timings: lookups fail fast on this machine, nothing to interrupt");
    }
    CheckLookupOutcomes();
}
// ---- transport argument checks and the pose format ----------------------------------------------

void TestConnectAndSendChecks()
{
    std::puts("transport argument checks (Connect, Send, PushPlayer, SampleRemote)");
    ConnectOptions options;
    options.host = "127.0.0.1";
    CHECK(ValidateConnectOptions(options).empty()); // port 11778, room "default", role any
    ConnectOptions bad = options;
    bad.port = 0;
    CHECK(!ValidateConnectOptions(bad).empty());
    bad = options;
    bad.host.clear();
    CHECK(!ValidateConnectOptions(bad).empty());
    for (const char* room : {"", "two words", "x/y", "123456789012345678901234567890123", "caf\xc3\xa9"})
    {
        bad = options;
        bad.room = room;
        CHECK(!ValidateConnectOptions(bad).empty());
    }
    bad = options;
    bad.room = "night-city_2077";
    CHECK(ValidateConnectOptions(bad).empty());
    bad = options;
    bad.role = 4;
    CHECK(!ValidateConnectOptions(bad).empty());
    bad.role = -1;
    CHECK(!ValidateConnectOptions(bad).empty());
    bad = options;
    bad.key = std::string(kMaxKeyBytes + 1, 'k');
    CHECK(!ValidateConnectOptions(bad).empty());

    Transport transport;
    CHECK(!transport.Connect("", 11778, "x"));
    CHECK(!transport.Connect("127.0.0.1", 70000, "x"));
    CHECK(!transport.Connect("127.0.0.1", 11778, "no spaces"));
    CHECK(transport.State() == ConnectionState::Idle);

    // Not connected: every send is refused, valid or not.
    CHECK(!transport.Send(16, "x"));
    CHECK(!transport.Send(1, "x"));
    CHECK(!transport.Send(0, "x"));
    CHECK(!transport.Send(32, "x"));
    CHECK(!transport.Send(16, std::string(kMaxPayloadSize + 1, 'x')));
    CHECK(!transport.Send(16, "\xff\xfe"));
    CHECK(!transport.Send(16, std::string("a\0b", 3)));
    CHECK(!transport.Send(16, "x", 0));
    CHECK(transport.StatsJson().find("\"refusedSends\":8") != std::string::npos);
    CHECK(IsUnreliableChannel(1) && IsUnreliableChannel(15) && !IsUnreliableChannel(16) && !IsUnreliableChannel(0));
    CHECK(IsReliableChannel(16) && IsReliableChannel(31) && !IsReliableChannel(32) && !IsReliableChannel(15));

    PlayerState player;
    player.x = 100.0f;
    player.y = -2000.5f;
    player.z = 30.0f;
    player.yaw = 725.0f;
    player.pitch = -120.0f; // clamped to -90 on the wire
    player.moveState = 3;
    player.flags = coopv2::kPlayerWeaponDrawn | coopv2::kPlayerSprinting;
    CHECK(ValidatePlayerState(player).empty());
    PlayerState wrong = player;
    wrong.x = std::nanf("");
    CHECK(!ValidatePlayerState(wrong).empty());
    wrong = player;
    wrong.y = 20000.5f;
    CHECK(!ValidatePlayerState(wrong).empty());
    wrong = player;
    wrong.z = -5001.0f;
    CHECK(!ValidatePlayerState(wrong).empty());
    wrong = player;
    wrong.moveState = 14;
    CHECK(!ValidatePlayerState(wrong).empty());
    wrong = player;
    wrong.flags = coopv2::kPlayerDriving;
    CHECK(!ValidatePlayerState(wrong).empty());
    wrong = player;
    wrong.flags = 0x10000;
    CHECK(!ValidatePlayerState(wrong).empty());
    wrong = player;
    wrong.health = 256;
    CHECK(!ValidatePlayerState(wrong).empty());
    CHECK(!transport.PushPlayer(player)); // not connected
    RemotePose pose;
    CHECK(!transport.SampleRemote(1, pose));
    CHECK(!transport.SampleRemote(0, pose));
    CHECK(!transport.SampleRemote(255, pose));
    CHECK(!transport.RelayNowMs().has_value());
    const std::string idle = transport.StatsJson();
    CHECK(idle.find("\"state\":\"idle\"") != std::string::npos);
    CHECK(idle.find("\"version\":\"" + std::string(kVersionString) + "\"") != std::string::npos);
    CHECK(idle.find("\"refusedPlayers\":1") != std::string::npos);
    CHECK(idle.find("\"peers\":[]") != std::string::npos);

    pose.mode = v2::SampleMode::Extrapolated;
    pose.x = -1234.5678;
    pose.y = 0.0004;
    pose.z = 19999.9996;
    pose.yaw = 359.996;
    pose.pitch = -3.5;
    pose.vx = 6.126;
    pose.vy = -0.004;
    pose.vz = 0.0;
    pose.moveState = 2;
    pose.flags = 0x4006;
    pose.health = 230;
    pose.delayMs = 133.33;
    pose.aheadMs = -20.06;
    const std::string text = FormatRemotePose(pose);
    std::printf("    pose text: \"%s\"\n", text.c_str());
    CHECK(text == "extrapolated -1234.568 0.000 20000.000 360.00 -3.50 6.13 -0.00 0.00 2 16390 230 133.3 -20.1");
}

// ---- protocol v2 handshake and session against a scripted fake relay ------------------------------

double NowSeconds()
{
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

// A UDP socket on 127.0.0.1 that plays the relay side of protocol v2 step by step.
class FakeRelay
{
public:
    static constexpr double kRelayOffsetMs = 5000.0; // relay clock = local steady clock + 5 s

    FakeRelay()
    {
        WSADATA data{};
        WSAStartup(MAKEWORD(2, 2), &data);
        m_socket = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        BOOL reportReset = FALSE;
        DWORD returned = 0;
        WSAIoctl(m_socket, SIO_UDP_CONNRESET, &reportReset, sizeof(reportReset), nullptr, 0, &returned, nullptr, nullptr);
        sockaddr_in local{};
        local.sin_family = AF_INET;
        local.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        bind(m_socket, reinterpret_cast<const sockaddr*>(&local), sizeof(local));
        int length = sizeof(local);
        getsockname(m_socket, reinterpret_cast<sockaddr*>(&local), &length);
        m_port = ntohs(local.sin_port);
    }

    ~FakeRelay()
    {
        closesocket(m_socket);
        WSACleanup();
    }

    FakeRelay(const FakeRelay&) = delete;
    FakeRelay& operator=(const FakeRelay&) = delete;

    [[nodiscard]] int Port() const
    {
        return m_port;
    }

    static double RelayMs()
    {
        return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now().time_since_epoch()).count() +
               kRelayOffsetMs;
    }

    // The next datagram from the client within aTimeoutMs.
    std::optional<v2::Bytes> Receive(double aTimeoutMs)
    {
        fd_set readable;
        FD_ZERO(&readable);
        FD_SET(m_socket, &readable);
        timeval timeout{0, static_cast<long>(std::max(0.0, aTimeoutMs) * 1000.0)};
        if (select(0, &readable, nullptr, nullptr, &timeout) != 1)
        {
            return std::nullopt;
        }
        std::array<uint8_t, 2048> buffer{};
        int fromLength = sizeof(m_client);
        const int received = recvfrom(m_socket, reinterpret_cast<char*>(buffer.data()), static_cast<int>(buffer.size()),
                                      0, reinterpret_cast<sockaddr*>(&m_client), &fromLength);
        if (received <= 0)
        {
            return std::nullopt;
        }
        return v2::Bytes(buffer.begin(), buffer.begin() + received);
    }

    // Skips datagrams until one of aType arrives (DATA is fed to the link). nullopt on timeout.
    std::optional<v2::Bytes> Expect(coopv2::PacketType aType, double aTimeoutMs)
    {
        const double deadline = NowSeconds() * 1000.0 + aTimeoutMs;
        while (NowSeconds() * 1000.0 < deadline)
        {
            std::optional<v2::Bytes> datagram = Receive(deadline - NowSeconds() * 1000.0);
            if (!datagram)
            {
                break;
            }
            v2::DecodedPacket packet;
            if (v2::DecodePacket(*datagram, packet) != v2::Status::Ok)
            {
                continue;
            }
            if (packet.header.type == static_cast<uint8_t>(aType))
            {
                return datagram;
            }
            if (packet.header.type == static_cast<uint8_t>(coopv2::PacketType::Data))
            {
                OnData(packet, datagram->size());
            }
        }
        return std::nullopt;
    }

    void Send(const v2::Bytes& aDatagram)
    {
        sendto(m_socket, reinterpret_cast<const char*>(aDatagram.data()), static_cast<int>(aDatagram.size()), 0,
               reinterpret_cast<const sockaddr*>(&m_client), sizeof(m_client));
    }

    void Welcome(uint8_t aPeerId, uint8_t aRole, uint64_t aToken)
    {
        coopv2::Welcome welcome{};
        welcome.minor = 1;
        welcome.peer_id = aPeerId;
        welcome.role = aRole;
        welcome.token = aToken;
        welcome.relay_time_ms = static_cast<uint32_t>(static_cast<uint64_t>(RelayMs()));
        welcome.player_hz = 30;
        welcome.entity_hz = 10;
        welcome.max_packet = coopv2::kMaxPacket;
        welcome.room_caps = coopv2::kCapPlayer;
        v2::Bytes datagram;
        v2::EncodeWelcome(welcome, datagram);
        Send(datagram);
        m_link.emplace(aToken);
        m_token = aToken;
    }

    // Runs the relay side for aMs: feeds DATA to the link, answers TIME_REQ, acks and keeps the
    // client alive, unless aSilent (then datagrams are read and ignored). Returns the delivered
    // client messages other than TIME_REQ.
    std::vector<v2::DeliveredMessage> Pump(double aMs, bool aSilent = false)
    {
        std::vector<v2::DeliveredMessage> collected;
        const double end = NowSeconds() * 1000.0 + aMs;
        do
        {
            if (std::optional<v2::Bytes> datagram = Receive(std::min(5.0, end - NowSeconds() * 1000.0)))
            {
                v2::DecodedPacket packet;
                if (!aSilent && v2::DecodePacket(*datagram, packet) == v2::Status::Ok &&
                    packet.header.type == static_cast<uint8_t>(coopv2::PacketType::Data))
                {
                    for (v2::DeliveredMessage& message : OnData(packet, datagram->size()))
                    {
                        collected.push_back(std::move(message));
                    }
                }
            }
            if (!aSilent)
            {
                Flush(false);
            }
        } while (NowSeconds() * 1000.0 < end);
        return collected;
    }

    void SendUnreliable(uint8_t aSource, const v2::Body& aBody)
    {
        v2::Bytes body;
        v2::EncodeBody(aBody, body);
        m_unreliable.push_back({v2::TypeOf(aBody), aSource, std::move(body)});
        Flush(true);
    }

    void SendReliable(uint8_t aSource, const v2::Body& aBody)
    {
        v2::Bytes body;
        v2::EncodeBody(aBody, body);
        m_link->QueueReliable(v2::TypeOf(aBody), aSource, v2::ByteSpan(body.data(), body.size()), NowSeconds());
        Flush(true);
    }

    void SendDisconnect(coopv2::DisconnectReason aReason)
    {
        v2::Bytes datagram;
        v2::EncodeDisconnect(m_token, static_cast<uint8_t>(aReason), datagram);
        Send(datagram);
        m_link.reset();
    }

    uint64_t timeRequests = 0;

private:
    struct Pending
    {
        uint8_t type;
        uint8_t peer;
        v2::Bytes body;
    };

    std::vector<v2::DeliveredMessage> OnData(const v2::DecodedPacket& aPacket, size_t aSize)
    {
        std::vector<v2::DeliveredMessage> delivered;
        std::vector<v2::DeliveredMessage> others;
        if (!m_link || aPacket.header.token != m_token ||
            m_link->OnPacket(NowSeconds(), aPacket.header, aPacket.body, delivered, aSize) != v2::Status::Ok)
        {
            return others;
        }
        for (v2::DeliveredMessage& message : delivered)
        {
            v2::Body body;
            if (message.type == static_cast<uint8_t>(coopv2::MsgType::TimeReq) &&
                v2::DecodeBody(message.type, v2::ByteSpan(message.body.data(), message.body.size()), body) ==
                    v2::Status::Ok)
            {
                ++timeRequests;
                const auto stamp = static_cast<uint32_t>(static_cast<uint64_t>(RelayMs()));
                const coopv2::TimeResp response{std::get<coopv2::TimeReq>(body).t0, stamp, stamp};
                v2::Bytes reply;
                v2::EncodeBody(v2::Body{response}, reply);
                m_unreliable.push_back({static_cast<uint8_t>(coopv2::MsgType::TimeResp), 0, std::move(reply)});
            }
            else
            {
                others.push_back(std::move(message));
            }
        }
        return others;
    }

    void Flush(bool aForce)
    {
        if (!m_link)
        {
            return;
        }
        const double now = NowSeconds();
        const bool ackDue = m_link->AckPending();
        const bool keepalive = !m_link->LastSend() || now - *m_link->LastSend() > 0.5;
        if (!aForce && m_unreliable.empty() && !m_link->ReliableDue(now) && !ackDue && !keepalive)
        {
            return;
        }
        std::vector<v2::LinkMessage> messages;
        for (const Pending& pending : m_unreliable)
        {
            messages.push_back({pending.type, pending.peer, v2::ByteSpan(pending.body.data(), pending.body.size())});
        }
        std::vector<v2::Bytes> packets;
        m_link->BuildPackets(now, messages, true, packets);
        m_unreliable.clear();
        for (const v2::Bytes& packet : packets)
        {
            Send(packet);
        }
    }

    SOCKET m_socket = INVALID_SOCKET;
    int m_port = 0;
    sockaddr_in m_client{};
    std::optional<v2::Connection> m_link;
    uint64_t m_token = 0;
    std::vector<Pending> m_unreliable;
};

std::vector<std::string> g_events; // every transport event WaitEvent has seen, in order

bool SawEvent(const std::string& aEvent)
{
    return std::find(g_events.begin(), g_events.end(), aEvent) != g_events.end();
}

// Polls the transport (pumping the fake relay meanwhile) until an event starting with aEvent
// arrives. Other messages go to aMessages.
bool WaitEvent(Transport& aTransport, FakeRelay* aRelay, const std::string& aEvent, double aTimeoutMs,
               std::vector<std::string>* aMessages = nullptr)
{
    const auto start = std::chrono::steady_clock::now();
    bool found = false;
    do
    {
        std::string message;
        while (aTransport.Poll(message))
        {
            if (message.starts_with("0|0|"))
            {
                g_events.push_back(message.substr(4));
                found = found || g_events.back().starts_with(aEvent);
            }
            else if (aMessages != nullptr)
            {
                aMessages->push_back(message);
            }
        }
        if (found)
        {
            break;
        }
        if (aRelay != nullptr)
        {
            aRelay->Pump(5.0);
        }
        else
        {
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
        }
    } while (MillisSince(start) < aTimeoutMs);
    return found;
}

v2::JoinInfo HelloJoin(const v2::Bytes& aDatagram)
{
    v2::DecodedPacket packet;
    v2::JoinInfo join;
    v2::DecodePacket(aDatagram, packet);
    v2::DecodeHello(packet.body, join);
    return join;
}

v2::AuthInfo AuthOf(const v2::Bytes& aDatagram)
{
    v2::DecodedPacket packet;
    v2::AuthInfo auth;
    v2::DecodePacket(aDatagram, packet);
    v2::DecodeAuth(packet.body, auth);
    return auth;
}

void SendChallenge(FakeRelay& aRelay, uint8_t aFill)
{
    coopv2::Challenge challenge{};
    challenge.minor_min = 0;
    challenge.minor_max = 1;
    std::fill(std::begin(challenge.cookie), std::end(challenge.cookie), aFill);
    v2::Bytes datagram;
    v2::EncodeChallenge(challenge, datagram);
    aRelay.Send(datagram);
}

void SendReject(FakeRelay& aRelay, coopv2::RejectReason aReason, const std::string& aText)
{
    v2::RejectInfo reject;
    reject.reason = static_cast<uint8_t>(aReason);
    reject.text = aText;
    v2::Bytes datagram;
    v2::EncodeReject(reject, datagram);
    aRelay.Send(datagram);
}

void PrintEvents()
{
    std::printf("    events:");
    for (const std::string& event : g_events)
    {
        std::printf(" [%s]", event.c_str());
    }
    std::puts("");
}

void TestHandshakeNoAnswer()
{
    std::puts("v2 handshake: no answer (the v1 fallback signal)");
    FakeRelay relay;
    Transport transport;
    g_events.clear();
    CHECK(transport.Connect(ConnectOptions{"127.0.0.1", relay.Port(), "fake", "pw", static_cast<int>(Role::Joiner)}));
    const auto start = std::chrono::steady_clock::now();
    int hellos = 0;
    bool earlyNoAnswer = false;
    v2::JoinInfo join;
    size_t helloSize = 0;
    while (MillisSince(start) < 1800.0)
    {
        if (std::optional<v2::Bytes> datagram = relay.Expect(coopv2::PacketType::Hello, 30.0))
        {
            ++hellos;
            helloSize = datagram->size();
            join = HelloJoin(*datagram);
        }
        if (MillisSince(start) < 1300.0)
        {
            WaitEvent(transport, nullptr, "no_answer", 0.0);
            earlyNoAnswer = earlyNoAnswer || SawEvent("no_answer");
        }
    }
    const bool noAnswer = WaitEvent(transport, nullptr, "no_answer", 300.0);
    std::printf("    %d HELLOs in 1.8 s (%zu bytes each)\n", hellos, helloSize);
    PrintEvents();
    CHECK(hellos >= 6 && hellos <= 9); // every 250 ms
    CHECK(helloSize == coopv2::kHelloMinPacket);
    CHECK(join.fixed.minor == 1 && join.fixed.role == 2 && join.room == "fake" && join.name == "CP2077CoopNet");
    CHECK(join.fixed.mod_count == 1 && join.fixed.resume_token == 0 && join.fixed.caps == coopv2::kCapPlayer);
    CHECK(join.fixed.game_build == v2::GameBuildId("2.31a"));
    CHECK(!g_events.empty() && g_events.front() == "connecting 127.0.0.1:" + std::to_string(relay.Port()));
    CHECK(!earlyNoAnswer && noAnswer);
    CHECK(transport.State() == ConnectionState::Connecting);
    CHECK(!transport.Send(16, "x"));
    transport.Disconnect();
    CHECK(transport.State() == ConnectionState::Idle);
}

void TestHandshakeReject()
{
    std::puts("v2 handshake: CHALLENGE, AUTH with the room key hash, REJECT bad_key");
    FakeRelay relay;
    Transport transport;
    g_events.clear();
    CHECK(transport.Connect(
        ConnectOptions{"127.0.0.1", relay.Port(), "night-city", "s3cret", static_cast<int>(Role::Host)}));
    const std::optional<v2::Bytes> hello = relay.Expect(coopv2::PacketType::Hello, 1000.0);
    CHECK(hello.has_value());
    SendChallenge(relay, 0xAB);
    const std::optional<v2::Bytes> authDatagram = relay.Expect(coopv2::PacketType::Auth, 1000.0);
    CHECK(authDatagram.has_value());
    if (hello && authDatagram)
    {
        const v2::AuthInfo auth = AuthOf(*authDatagram);
        v2::Cookie expected{};
        expected.fill(0xAB);
        CHECK(auth.cookie == expected);
        CHECK(auth.keyHash == v2::RoomKeyHash("night-city", "s3cret"));
        CHECK(auth.join.fixed.role == 1 && auth.join.room == "night-city");
        CHECK(auth.join.fixed.client_nonce == HelloJoin(*hello).fixed.client_nonce);
    }
    SendReject(relay, coopv2::RejectReason::BadKey, "wrong room password");
    CHECK(WaitEvent(transport, nullptr, "disconnected", 2000.0));
    PrintEvents();
    CHECK(SawEvent("rejected bad_key wrong room password"));
    CHECK(transport.State() == ConnectionState::Error);
    CHECK(transport.StatsJson().find("\"lastError\":\"the relay rejected the join: bad_key (wrong room password)\"") !=
          std::string::npos);
}

void TestSessionWithFakeRelay()
{
    std::puts("v2 session against the fake relay: expired cookie, welcome, clock, peers, script messages, "
              "player snapshots, relay shutdown, silence and a kick");
    FakeRelay relay;
    Transport transport;
    g_events.clear();
    CHECK(transport.Connect(ConnectOptions{"127.0.0.1", relay.Port(), "bench", "", static_cast<int>(Role::Any)}));
    CHECK(relay.Expect(coopv2::PacketType::Hello, 1000.0).has_value());
    SendChallenge(relay, 1);
    CHECK(relay.Expect(coopv2::PacketType::Auth, 1000.0).has_value());
    SendReject(relay, coopv2::RejectReason::BadCookie, "cookie expired or forged");
    CHECK(relay.Expect(coopv2::PacketType::Hello, 1000.0).has_value()); // starts over
    SendChallenge(relay, 2);
    const std::optional<v2::Bytes> auth = relay.Expect(coopv2::PacketType::Auth, 1000.0);
    CHECK(auth && AuthOf(*auth).cookie[0] == 2);
    constexpr uint64_t kToken = 0x0123456789ABCDEFull;
    relay.Welcome(7, 2, kToken);
    CHECK(WaitEvent(transport, &relay, "welcome 7 joiner", 1000.0));
    CHECK(transport.LocalId() == 7 && transport.State() == ConnectionState::Connected);
    CHECK(!SawEvent("rejected bad_cookie cookie expired or forged"));

    // Clock: TIME_REQ at 10 Hz until synced (3 exchanges).
    const auto syncStart = std::chrono::steady_clock::now();
    while (!transport.ClockSynced() && MillisSince(syncStart) < 2000.0)
    {
        relay.Pump(10.0);
    }
    const double syncMs = MillisSince(syncStart);
    const std::optional<double> relayNow = transport.RelayNowMs();
    const double expected = FakeRelay::RelayMs();
    std::printf("    clock synced after %.0f ms and %llu TIME_REQ; relay clock estimate %.2f ms off\n", syncMs,
                static_cast<unsigned long long>(relay.timeRequests), relayNow ? *relayNow - expected : 1e9);
    CHECK(transport.ClockSynced() && relayNow && std::fabs(*relayNow - expected) < 3.0);

    // Peers and script messages.
    v2::PeerJoinedMsg joined;
    joined.fixed.peer_id = 3;
    joined.fixed.role = 1;
    joined.fixed.minor = 1;
    joined.name = "fakehost";
    relay.SendReliable(0, v2::Body{joined});
    CHECK(WaitEvent(transport, &relay, "peer_join 3 host", 1000.0));
    CHECK(transport.PeerCount() == 1);
    std::vector<std::string> messages;
    relay.SendUnreliable(3, v2::Body{v2::ScriptMsg{1, 5, "first"}});
    relay.SendUnreliable(3, v2::Body{v2::ScriptMsg{1, 4, "stale"}});
    relay.SendUnreliable(3, v2::Body{v2::ScriptMsg{1, 5, "duplicate"}});
    relay.SendUnreliable(3, v2::Body{v2::ScriptMsg{2, 4, "other channel"}});
    relay.SendUnreliable(3, v2::Body{v2::ScriptMsg{1, 6, "second"}});
    relay.SendReliable(3, v2::Body{v2::ScriptMsg{16, 0, "event|1"}});
    relay.SendReliable(3, v2::Body{v2::ScriptMsg{31, 0, "event|2"}});
    WaitEvent(transport, &relay, "never", 300.0, &messages);
    CHECK(messages ==
          (std::vector<std::string>{"3|1|first", "3|2|other channel", "3|1|second", "3|16|event|1", "3|31|event|2"}));
    relay.Pump(250.0); // the next stats snapshot
    CHECK(transport.StatsJson().find("\"unrelStale\":2") != std::string::npos);

    // Client -> relay: script messages and a player snapshot.
    CHECK(transport.Send(16, "hello host"));
    CHECK(transport.Send(2, "snap", 3));
    CHECK(transport.Send(17, "only you", 3));
    CHECK(transport.Send(17, "nobody", 9)); // accepted, dropped on the network thread (unknown peer)
    PlayerState player;
    player.x = 12.5f;
    player.y = -800.25f;
    player.z = 41.0f;
    player.yaw = -90.0f;
    player.pitch = 10.0f;
    player.vx = 3.0f;
    player.moveState = 2;
    player.flags = coopv2::kPlayerWeaponDrawn;
    player.health = 200;
    const double pushedAt = FakeRelay::RelayMs();
    CHECK(transport.PushPlayer(player));
    const std::vector<v2::DeliveredMessage> fromClient = relay.Pump(300.0);
    std::vector<std::string> scripts;
    std::optional<v2::PlayerSnapshotMsg> snapshot;
    for (const v2::DeliveredMessage& message : fromClient)
    {
        v2::Body body;
        if (v2::DecodeBody(message.type, v2::ByteSpan(message.body.data(), message.body.size()), body) != v2::Status::Ok)
        {
            continue;
        }
        if (const auto* script = std::get_if<v2::ScriptMsg>(&body))
        {
            scripts.push_back(std::to_string(message.peer) + "|" + std::to_string(script->channel) + "|" +
                              std::to_string(message.reliable ? 1 : 0) + "|" + script->text);
        }
        else if (const auto* sent = std::get_if<v2::PlayerSnapshotMsg>(&body))
        {
            snapshot = *sent;
            CHECK(message.peer == coopv2::kPeerBroadcast && !message.reliable);
        }
    }
    std::sort(scripts.begin(), scripts.end());
    CHECK(scripts == (std::vector<std::string>{"255|16|1|hello host", "3|17|1|only you", "3|2|0|snap"}));
    CHECK(snapshot.has_value());
    if (snapshot)
    {
        const coopv2::PlayerSnapshot& base = snapshot->base;
        const double stampError = static_cast<double>(base.sample_time) - std::fmod(std::floor(pushedAt), 4294967296.0);
        std::printf("    PLAYER_SNAPSHOT seq %u, sample_time %+.0f ms from the relay clock at the push, pos %.2f %.2f "
                    "%.2f, yaw %u, pitch %d, vx %d, move %u, flags 0x%04x, health %u\n",
                    base.snap_seq, stampError, base.x, base.y, base.z, base.yaw, base.pitch, base.vx, base.move_state,
                    base.flags, base.health);
        CHECK(base.snap_seq == 1 && base.x == 12.5f && base.y == -800.25f && base.z == 41.0f);
        CHECK(base.yaw == 49152 && base.pitch == 1000 && base.vx == 300 && base.vy == 0);
        CHECK(base.move_state == 2 && base.flags == coopv2::kPlayerWeaponDrawn && base.health == 200);
        CHECK(std::fabs(stampError) < 5.0);
    }

    // Remote player: 30 Hz snapshots from peer 3 moving at 6 m/s along x.
    RemotePose pose;
    CHECK(!transport.SampleRemote(3, pose));
    const auto moveStart = std::chrono::steady_clock::now();
    uint16_t sequence = 0;
    double lastSnapshot = 0.0;
    int sampled = 0;
    double worstError = 0.0;
    const auto truthX = [](double aRelayMs) { return 6.0 * (aRelayMs - FakeRelay::kRelayOffsetMs) / 1000.0; };
    const double originX = truthX(FakeRelay::RelayMs());
    while (MillisSince(moveStart) < 1200.0)
    {
        const double relayMs = FakeRelay::RelayMs();
        if (relayMs - lastSnapshot >= 1000.0 / 30.0)
        {
            lastSnapshot = relayMs;
            v2::PlayerSnapshotMsg remote;
            remote.base.snap_seq = ++sequence;
            remote.base.sample_time = static_cast<uint32_t>(static_cast<uint64_t>(relayMs));
            remote.base.x = static_cast<float>(truthX(relayMs) - originX);
            remote.base.y = 50.0f;
            remote.base.vx = 600;
            remote.base.move_state = 2;
            remote.base.health = 255;
            relay.SendUnreliable(3, v2::Body{remote});
        }
        relay.Pump(4.0);
        if (MillisSince(moveStart) > 400.0 && transport.SampleRemote(3, pose))
        {
            ++sampled;
            const double renderRelay = FakeRelay::RelayMs() - pose.delayMs;
            worstError = std::max(worstError, std::fabs(pose.x - (truthX(renderRelay) - originX)));
        }
    }
    std::printf("    remote peer 3: %d samples, last \"%s\", worst x error %.3f m\n", sampled,
                FormatRemotePose(pose).c_str(), worstError);
    CHECK(sampled > 100);
    CHECK(pose.mode == v2::SampleMode::Interpolated && pose.moveState == 2 && pose.health == 255);
    CHECK(pose.delayMs >= 99.0 && pose.delayMs < 160.0);
    CHECK(worstError < 0.05);
    relay.Pump(250.0);
    CHECK(transport.StatsJson().find("\"interp\":{\"received\":") != std::string::npos);

    // The relay shuts down: the session ends, peer 3 leaves and a fresh HELLO goes out (no resume).
    relay.SendDisconnect(coopv2::DisconnectReason::ServerShutdown);
    CHECK(WaitEvent(transport, nullptr, "peer_leave 3 relay_lost", 1000.0));
    CHECK(SawEvent("relay_disconnect server_shutdown"));
    const std::optional<v2::Bytes> rehello = relay.Expect(coopv2::PacketType::Hello, 1000.0);
    CHECK(rehello && HelloJoin(*rehello).fixed.resume_token == 0);
    CHECK(transport.State() == ConnectionState::Reconnecting && transport.LocalId() == 0);
    CHECK(!transport.SampleRemote(3, pose));
    SendChallenge(relay, 3);
    CHECK(relay.Expect(coopv2::PacketType::Auth, 1000.0).has_value());
    constexpr uint64_t kSecondToken = 0x1111222233334444ull;
    relay.Welcome(8, 1, kSecondToken);
    CHECK(WaitEvent(transport, &relay, "welcome 8 host", 1000.0));

    // Silence: 5 s without a datagram means relay_lost and a HELLO that asks to resume.
    const auto silentStart = std::chrono::steady_clock::now();
    relay.Pump(4500.0, true);
    WaitEvent(transport, nullptr, "relay_lost", 0.0);
    const bool earlyLoss = SawEvent("relay_lost");
    const std::optional<v2::Bytes> resumed = relay.Expect(coopv2::PacketType::Hello, 1500.0);
    const double lostAfter = MillisSince(silentStart);
    CHECK(WaitEvent(transport, nullptr, "relay_lost", 500.0));
    const bool resumeOk = resumed && HelloJoin(*resumed).fixed.resume_token == kSecondToken;
    std::printf("    relay silent: relay_lost and a new HELLO after %.0f ms, resume_token %s\n", lostAfter,
                resumeOk ? "= the lost session's token" : "WRONG");
    CHECK(!earlyLoss && resumeOk);
    SendChallenge(relay, 4);
    CHECK(relay.Expect(coopv2::PacketType::Auth, 1000.0).has_value());
    relay.Welcome(8, 1, 0x5555666677778888ull);
    CHECK(WaitEvent(transport, &relay, "welcome 8 host", 1000.0));

    // A kick ends the session for good.
    relay.SendDisconnect(coopv2::DisconnectReason::Kicked);
    CHECK(WaitEvent(transport, nullptr, "disconnected", 1000.0));
    CHECK(SawEvent("relay_disconnect kicked"));
    CHECK(transport.State() == ConnectionState::Error);
    PrintEvents();
}

} // namespace

int main()
{
    std::setvbuf(stdout, nullptr, _IONBF, 0); // keep the output of a crashing run
    TestClockConversion();
    TestClockNow();
    TestVersionString();
    TestLoadReport();
    TestNativeRegistration();
    TestScriptString();
    TestConnectAndSendChecks();
    TestStopWhileResolving();
    TestHandshakeNoAnswer();
    TestHandshakeReject();
    TestSessionWithFakeRelay();
    std::printf("%d checks, %d failures\n", g_checks, g_failures);
    return g_failures == 0 ? EXIT_SUCCESS : EXIT_FAILURE;
}
