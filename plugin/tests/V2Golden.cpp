// Golden vectors between the C++ v2 codec and relay/coopnet/proto.py, both directions.
//
//   coopnet_v2_golden check <py_vectors.txt>   decode, describe and re-encode what proto.py wrote
//   coopnet_v2_golden emit  <cpp_vectors.txt>  write vectors encoded here, for tools/v2_golden.py
//
// tools/v2_golden.py run drives both. File format (one record per line, space separated):
//
//   SPEC <type> <name> <delivery> <sender> <route> <min_minor>
//   CONST <name> <value>
//   HASH sha256|hmac|build|room|mods|cookie|cookiecheck ...
//   QUANT mm|vel|yaw|pitch <hex float> <expected>     QUANT quat <x> <y> <z> <w> <expected>
//   VALID <name> <canonical 0|1> <datagram hex>, then "D <description line>" lines, then END
//   INVALID <name> <datagram hex>
//   DELTA <name> <budget> ... ENDDELTA   (TICK, W, S, OUT, APPLY, ESTATS, DSTATS lines)

#include "V2TestSupport.hpp"
#include "v2/V2Codec.hpp"
#include "v2/V2Delta.hpp"
#include "v2/V2Describe.hpp"
#include "v2/V2Hash.hpp"

#include <cinttypes>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

using namespace coopnet::v2;

namespace
{
// ---- small text helpers ----------------------------------------------------------------------------

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

bool FromHex(const std::string& aHex, Bytes& aOut)
{
    aOut.clear();
    if (aHex == "-")
    {
        return true; // empty
    }
    if (aHex.size() % 2 != 0)
    {
        return false;
    }
    const auto nibble = [](char aChar) -> int {
        if (aChar >= '0' && aChar <= '9') return aChar - '0';
        if (aChar >= 'a' && aChar <= 'f') return aChar - 'a' + 10;
        if (aChar >= 'A' && aChar <= 'F') return aChar - 'A' + 10;
        return -1;
    };
    for (size_t index = 0; index < aHex.size(); index += 2)
    {
        const int high = nibble(aHex[index]);
        const int low = nibble(aHex[index + 1]);
        if (high < 0 || low < 0)
        {
            return false;
        }
        aOut.push_back(static_cast<uint8_t>(high * 16 + low));
    }
    return true;
}

std::string HexOrDash(ByteSpan aBytes)
{
    return aBytes.empty() ? std::string("-") : Hex(aBytes);
}

std::string TextOf(const Bytes& aBytes)
{
    return std::string(aBytes.begin(), aBytes.end());
}

ByteSpan AsSpan(const Bytes& aBytes)
{
    return {aBytes.data(), aBytes.size()};
}

uint64_t ParseU64(const std::string& aText)
{
    return std::strtoull(aText.c_str(), nullptr, 10);
}

int64_t ParseI64(const std::string& aText)
{
    return std::strtoll(aText.c_str(), nullptr, 10);
}

double ParseDouble(const std::string& aText)
{
    return std::strtod(aText.c_str(), nullptr); // accepts Python's float.hex() and inf/nan
}

std::string HexFloat(double aValue)
{
    char text[64];
    std::snprintf(text, sizeof(text), "%a", aValue);
    return text;
}

const char* DeliveryName(Delivery aDelivery)
{
    switch (aDelivery)
    {
    case Delivery::Unreliable: return "unreliable";
    case Delivery::Reliable: return "reliable";
    case Delivery::ByChannel: return "by_channel";
    }
    return "?";
}

const char* SenderName(Sender aSender)
{
    switch (aSender)
    {
    case Sender::Relay: return "relay";
    case Sender::Client: return "client";
    case Sender::Host: return "host";
    }
    return "?";
}

const char* RouteName(Route aRoute)
{
    switch (aRoute)
    {
    case Route::Relay: return "relay";
    case Route::Broadcast: return "broadcast";
    case Route::Host: return "host";
    case Route::Target: return "target";
    case Route::Hit: return "hit";
    case Route::Peer: return "peer";
    }
    return "?";
}

const std::map<std::string, uint64_t>& Constants()
{
    static const std::map<std::string, uint64_t> kConstants = {
        {"PROTO_MAJOR", coopv2::kProtoMajor},
        {"PROTO_MINOR", coopv2::kProtoMinor},
        {"MIN_SUPPORTED_MINOR", coopv2::kMinSupportedMinor},
        {"MAX_PACKET", coopv2::kMaxPacket},
        {"PACKET_HEADER", kPacketHeaderSize},
        {"MESSAGE_HEADER", kMessageHeaderSize},
        {"MAX_MESSAGE_BODY", kMaxMessageBody},
        {"MAX_MESSAGES_PER_PACKET", kMaxMessagesPerPacket},
        {"HELLO_MIN_PACKET", coopv2::kHelloMinPacket},
        {"COOKIE_SIZE", kCookieSize},
        {"KEY_HASH_SIZE", kKeyHashSize},
        {"COOKIE_MAX_AGE_S", kCookieMaxAgeS},
        {"MAX_NAME_BYTES", kMaxNameBytes},
        {"MAX_ROOM_BYTES", kMaxRoomBytes},
        {"MAX_CHAT_BYTES", kMaxChatBytes},
        {"MAX_SCRIPT_BYTES", coopv2::kMaxScriptBytes},
        {"MAX_ENTITY_RECORDS", kMaxEntityRecords},
        {"MOVE_STATE_COUNT", kMoveStateCount},
        {"HISTORY_TICKS", kHistoryTicks},
        {"DEFAULT_BUDGET", kDefaultSnapshotBudget},
    };
    return kConstants;
}

// ---- checking proto.py vectors ---------------------------------------------------------------------

struct Checker
{
    std::map<std::string, int> passed;
    int failures = 0;
    std::vector<std::string> specNames;

    void Pass(const std::string& aKind)
    {
        ++passed[aKind];
    }

    void Fail(const std::string& aKind, const std::string& aWhat)
    {
        ++failures;
        if (failures <= 40)
        {
            std::printf("  FAIL %s: %s\n", aKind.c_str(), aWhat.c_str());
        }
    }

    void Expect(bool aOk, const std::string& aKind, const std::string& aWhat)
    {
        if (aOk)
        {
            Pass(aKind);
        }
        else
        {
            Fail(aKind, aWhat);
        }
    }
};

void CheckSpec(Checker& aChecker, const std::vector<std::string>& aTokens)
{
    const uint8_t type = static_cast<uint8_t>(ParseU64(aTokens[1]));
    const MsgSpec* spec = FindSpec(type);
    aChecker.specNames.push_back(aTokens[2]);
    const bool ok = spec != nullptr && aTokens[2] == spec->name && aTokens[3] == DeliveryName(spec->delivery) &&
                    aTokens[4] == SenderName(spec->sender) && aTokens[5] == RouteName(spec->route) &&
                    ParseU64(aTokens[6]) == spec->minMinor;
    aChecker.Expect(ok, "spec", aTokens[2]);
}

void CheckConst(Checker& aChecker, const std::vector<std::string>& aTokens)
{
    const auto& constants = Constants();
    const auto found = constants.find(aTokens[1]);
    aChecker.Expect(found != constants.end() && found->second == ParseU64(aTokens[2]), "const",
                    aTokens[1] + " expected " + aTokens[2]);
}

void CheckHash(Checker& aChecker, const std::vector<std::string>& aTokens)
{
    const std::string& kind = aTokens[1];
    Bytes a;
    Bytes b;
    Bytes expected;
    if (kind == "sha256")
    {
        FromHex(aTokens[2], a);
        FromHex(aTokens[3], expected);
        const Sha256Digest digest = Sha256Of(AsSpan(a));
        aChecker.Expect(Bytes(digest.begin(), digest.end()) == expected, "hash sha256", aTokens[2].substr(0, 32));
    }
    else if (kind == "hmac")
    {
        FromHex(aTokens[2], a);
        FromHex(aTokens[3], b);
        FromHex(aTokens[4], expected);
        const Sha256Digest digest = HmacSha256(AsSpan(a), AsSpan(b));
        aChecker.Expect(Bytes(digest.begin(), digest.end()) == expected, "hash hmac", aTokens[2].substr(0, 32));
    }
    else if (kind == "build")
    {
        FromHex(aTokens[2], a);
        aChecker.Expect(GameBuildId(TextOf(a)) == ParseU64(aTokens[3]), "hash build", aTokens[2]);
    }
    else if (kind == "room")
    {
        FromHex(aTokens[2], a);
        FromHex(aTokens[3], b);
        FromHex(aTokens[4], expected);
        const KeyHash hash = RoomKeyHash(TextOf(a), TextOf(b));
        aChecker.Expect(Bytes(hash.begin(), hash.end()) == expected, "hash room", aTokens[2]);
    }
    else if (kind == "mods")
    {
        const size_t count = static_cast<size_t>(ParseU64(aTokens[2]));
        std::vector<ModEntry> entries;
        for (size_t index = 0; index < count; ++index)
        {
            FromHex(aTokens[3 + 2 * index], a);
            FromHex(aTokens[4 + 2 * index], b);
            entries.push_back({TextOf(a), TextOf(b)});
        }
        aChecker.Expect(ModListHash(entries) == ParseU64(aTokens[3 + 2 * count]), "hash mods",
                        std::to_string(count) + " entries");
    }
    else if (kind == "cookie" || kind == "cookiecheck")
    {
        FromHex(aTokens[2], a);
        FromHex(aTokens[3], b);
        const uint16_t port = static_cast<uint16_t>(ParseU64(aTokens[4]));
        const uint64_t nonce = ParseU64(aTokens[5]);
        if (kind == "cookie")
        {
            FromHex(aTokens[7], expected);
            const Cookie cookie = MakeCookie(AsSpan(a), TextOf(b), port, nonce, static_cast<uint32_t>(ParseU64(aTokens[6])));
            aChecker.Expect(Bytes(cookie.begin(), cookie.end()) == expected, "hash cookie", aTokens[3]);
        }
        else
        {
            Bytes cookie;
            FromHex(aTokens[6], cookie);
            const bool valid = CheckCookie(AsSpan(a), TextOf(b), port, nonce, AsSpan(cookie),
                                           static_cast<uint32_t>(ParseU64(aTokens[7])));
            aChecker.Expect(valid == (aTokens[8] == "1"), "hash cookiecheck", aTokens[6]);
        }
    }
    else
    {
        aChecker.Fail("hash", "unknown kind " + kind);
    }
}

void CheckQuant(Checker& aChecker, const std::vector<std::string>& aTokens)
{
    const std::string& kind = aTokens[1];
    int64_t got = 0;
    int64_t expected = 0;
    if (kind == "quat")
    {
        got = PackQuat(ParseDouble(aTokens[2]), ParseDouble(aTokens[3]), ParseDouble(aTokens[4]), ParseDouble(aTokens[5]));
        expected = ParseI64(aTokens[6]);
    }
    else
    {
        const double value = ParseDouble(aTokens[2]);
        expected = ParseI64(aTokens[3]);
        if (kind == "mm") got = MetersToMm(value);
        else if (kind == "vel") got = VelocityToCms(value);
        else if (kind == "yaw") got = YawToU16(value);
        else if (kind == "pitch") got = PitchToI16(value);
    }
    aChecker.Expect(got == expected, "quant " + kind,
                    aTokens[2] + " -> C++ " + std::to_string(got) + ", Python " + std::to_string(expected));
}

void CheckValid(Checker& aChecker, const std::string& aName, bool aCanonical, const Bytes& aDatagram,
                const std::vector<std::string>& aExpected)
{
    std::vector<std::string> lines;
    const Status status = DescribeDatagram(AsSpan(aDatagram), lines);
    if (status != Status::Ok)
    {
        aChecker.Fail("valid decode", aName + ": C++ rejects (" + ToString(status) + "), proto.py accepts");
        return;
    }
    if (lines != aExpected)
    {
        for (size_t index = 0; index < std::max(lines.size(), aExpected.size()); ++index)
        {
            const std::string mine = index < lines.size() ? lines[index] : "<none>";
            const std::string theirs = index < aExpected.size() ? aExpected[index] : "<none>";
            if (mine != theirs)
            {
                aChecker.Fail("valid describe", aName + "\n      C++   : " + mine.substr(0, 400) +
                                                     "\n      Python: " + theirs.substr(0, 400));
                return;
            }
        }
    }
    aChecker.Pass("valid decode+describe");
    if (aCanonical)
    {
        Bytes again;
        const Status reencoded = test::Reencode(AsSpan(aDatagram), again);
        aChecker.Expect(reencoded == Status::Ok && again == aDatagram, "valid re-encode byte-for-byte",
                        aName + " (" + ToString(reencoded) + ")");
    }
}

void CheckInvalid(Checker& aChecker, const std::string& aName, const Bytes& aDatagram)
{
    std::vector<std::string> lines;
    const Status status = DescribeDatagram(AsSpan(aDatagram), lines);
    aChecker.Expect(status != Status::Ok, "invalid rejected", aName + ": C++ accepts, proto.py rejects");
}

EntityState ParseState(const std::vector<std::string>& aTokens, uint16_t& aNetId)
{
    aNetId = static_cast<uint16_t>(ParseU64(aTokens[1]));
    EntityState state;
    state.kind = static_cast<uint8_t>(ParseU64(aTokens[2]));
    state.spawnFlags = static_cast<uint8_t>(ParseU64(aTokens[3]));
    state.attitude = static_cast<uint8_t>(ParseU64(aTokens[4]));
    state.record = ParseU64(aTokens[5]);
    state.appearance = ParseU64(aTokens[6]);
    for (size_t axis = 0; axis < 3; ++axis)
    {
        state.pos[axis] = static_cast<int32_t>(ParseI64(aTokens[7 + axis]));
        state.vel[axis] = static_cast<int16_t>(ParseI64(aTokens[11 + axis]));
        state.state[axis] = static_cast<uint8_t>(ParseU64(aTokens[14 + axis]));
    }
    state.rot = static_cast<uint32_t>(ParseU64(aTokens[10]));
    state.target = static_cast<uint16_t>(ParseU64(aTokens[17]));
    state.weapon = ParseU64(aTokens[18]);
    state.worldId = ParseU64(aTokens[19]);
    return state;
}

std::string StateLine(uint16_t aNetId, const EntityState& aState)
{
    std::ostringstream line;
    line << "S " << aNetId << ' ' << unsigned(aState.kind) << ' ' << unsigned(aState.spawnFlags) << ' '
         << unsigned(aState.attitude) << ' ' << aState.record << ' ' << aState.appearance << ' ' << aState.pos[0] << ' '
         << aState.pos[1] << ' ' << aState.pos[2] << ' ' << aState.rot << ' ' << aState.vel[0] << ' ' << aState.vel[1]
         << ' ' << aState.vel[2] << ' ' << unsigned(aState.state[0]) << ' ' << unsigned(aState.state[1]) << ' '
         << unsigned(aState.state[2]) << ' ' << aState.target << ' ' << aState.weapon << ' ' << aState.worldId;
    return line.str();
}

std::string EncoderStatsLine(const DeltaEncoderStats& aStats)
{
    std::ostringstream line;
    line << "ESTATS " << aStats.snapshots << ' ' << aStats.bytes << ' ' << aStats.fullBytes << ' ' << aStats.records
         << ' ' << aStats.deferred << ' ' << aStats.fullSnapshots << ' ' << aStats.removals << ' ' << aStats.spawns;
    return line.str();
}

std::string DecoderStatsLine(const DeltaDecoderStats& aStats)
{
    std::ostringstream line;
    line << "DSTATS " << aStats.decoded << ' ' << aStats.duplicate << ' ' << aStats.missingBaseline << ' '
         << aStats.inconsistent << ' ' << aStats.stale;
    return line.str();
}

// Replays one DELTA block written by proto.py's DeltaEncoder/DeltaDecoder.
void CheckDelta(Checker& aChecker, const std::vector<std::string>& aHead, std::istream& aInput)
{
    const std::string name = aHead[1];
    DeltaEncoder encoder(static_cast<size_t>(ParseU64(aHead[2])));
    DeltaDecoder decoder;
    std::vector<uint32_t> acks;
    uint32_t sampleTime = 0;
    EntityView states;
    std::map<uint16_t, double> weights;
    std::map<uint32_t, Bytes> bodies;
    std::string line;
    int outs = 0;
    int applies = 0;
    bool ok = true;
    while (std::getline(aInput, line))
    {
        const std::vector<std::string> tokens = Split(line);
        if (tokens.empty())
        {
            continue;
        }
        const std::string& kind = tokens[0];
        if (kind == "ENDDELTA")
        {
            break;
        }
        if (kind == "TICK")
        {
            sampleTime = static_cast<uint32_t>(ParseU64(tokens[1]));
            acks.clear();
            for (size_t index = 0; index < ParseU64(tokens[2]); ++index)
            {
                acks.push_back(static_cast<uint32_t>(ParseU64(tokens[3 + index])));
            }
            states = EntityView();
            weights.clear();
        }
        else if (kind == "W")
        {
            weights[static_cast<uint16_t>(ParseU64(tokens[1]))] = ParseDouble(tokens[2]);
        }
        else if (kind == "S")
        {
            uint16_t netId = 0;
            const EntityState state = ParseState(tokens, netId);
            states.Set(netId, state);
        }
        else if (kind == "OUT")
        {
            for (const uint32_t ack : acks)
            {
                encoder.OnAck(ack);
            }
            EncodedSnapshot encoded;
            const Status status = encoder.Encode(sampleTime, states, weights, encoded);
            Bytes expected;
            FromHex(tokens[4], expected);
            const bool same = status == Status::Ok && std::to_string(encoded.tick) == tokens[1] &&
                              std::to_string(encoded.baseline) == tokens[2] && ViewHash(encoded.view) == tokens[3] &&
                              encoded.body == expected;
            if (!same && ok)
            {
                aChecker.Fail("delta encode", name + " tick " + tokens[1] + ": C++ tick " + std::to_string(encoded.tick) +
                                                  " baseline " + std::to_string(encoded.baseline) + " view " +
                                                  ViewHash(encoded.view) + " body " + Hex(AsSpan(encoded.body)).substr(0, 120) +
                                                  "\n      Python baseline " + tokens[2] + " view " + tokens[3] + " body " +
                                                  tokens[4].substr(0, 120));
                ok = false;
            }
            bodies[static_cast<uint32_t>(ParseU64(tokens[1]))] = expected;
            ++outs;
        }
        else if (kind == "APPLY")
        {
            const uint32_t tick = static_cast<uint32_t>(ParseU64(tokens[1]));
            Body body;
            std::string result = "decode-error";
            if (DecodeBody(static_cast<uint8_t>(coopv2::MsgType::EntitySnapshot), AsSpan(bodies[tick]), body) == Status::Ok)
            {
                const EntityView* view = decoder.Apply(std::get<EntitySnapshotMsg>(body));
                result = view != nullptr ? ViewHash(*view) : std::string("none");
            }
            if (result != tokens[2] && ok)
            {
                aChecker.Fail("delta decode", name + " apply tick " + tokens[1] + ": C++ " + result + ", Python " + tokens[2]);
                ok = false;
            }
            ++applies;
        }
        else if (kind == "ESTATS")
        {
            if (EncoderStatsLine(encoder.Stats()) != line && ok)
            {
                aChecker.Fail("delta stats", name + ": C++ " + EncoderStatsLine(encoder.Stats()) + ", Python " + line);
                ok = false;
            }
        }
        else if (kind == "DSTATS")
        {
            if (DecoderStatsLine(decoder.Stats()) != line && ok)
            {
                aChecker.Fail("delta stats", name + ": C++ " + DecoderStatsLine(decoder.Stats()) + ", Python " + line);
                ok = false;
            }
        }
    }
    if (ok)
    {
        aChecker.Pass("delta scenario");
        aChecker.passed["delta snapshots byte-for-byte"] += outs;
        aChecker.passed["delta applications"] += applies;
    }
}

int RunCheck(const char* aPath)
{
    std::ifstream input(aPath);
    if (!input)
    {
        std::printf("cannot open %s\n", aPath);
        return 2;
    }
    Checker checker;
    std::string line;
    while (std::getline(input, line))
    {
        const std::vector<std::string> tokens = Split(line);
        if (tokens.empty() || tokens[0][0] == '#')
        {
            continue;
        }
        const std::string& kind = tokens[0];
        if (kind == "SPEC") CheckSpec(checker, tokens);
        else if (kind == "CONST") CheckConst(checker, tokens);
        else if (kind == "HASH") CheckHash(checker, tokens);
        else if (kind == "QUANT") CheckQuant(checker, tokens);
        else if (kind == "VALID")
        {
            Bytes datagram;
            FromHex(tokens[3], datagram);
            std::vector<std::string> expected;
            while (std::getline(input, line) && line != "END")
            {
                expected.push_back(line.substr(2));
            }
            CheckValid(checker, tokens[1], tokens[2] == "1", datagram, expected);
        }
        else if (kind == "INVALID")
        {
            Bytes datagram;
            FromHex(tokens[2], datagram);
            CheckInvalid(checker, tokens[1], datagram);
        }
        else if (kind == "DELTA") CheckDelta(checker, tokens, input);
        else checker.Fail("format", "unknown line " + kind);
    }
    checker.Expect(checker.specNames.size() == MessageSpecs().size(), "spec table size",
                   std::to_string(checker.specNames.size()) + " Python specs vs " + std::to_string(MessageSpecs().size()));
    int total = 0;
    for (const auto& [kind, count] : checker.passed)
    {
        std::printf("  %-36s %6d\n", kind.c_str(), count);
        total += count;
    }
    std::printf("golden check (proto.py -> C++): %d passed, %d failed\n", total, checker.failures);
    return checker.failures == 0 && total > 0 ? EXIT_SUCCESS : EXIT_FAILURE;
}

// ---- emitting C++ vectors ----------------------------------------------------------------------------

void EmitValid(std::ostream& aOut, const std::string& aName, bool aCanonical, const Bytes& aDatagram,
               const std::vector<std::string>& aLines)
{
    aOut << "VALID " << aName << ' ' << (aCanonical ? 1 : 0) << ' ' << HexOrDash(AsSpan(aDatagram)) << '\n';
    for (const std::string& line : aLines)
    {
        aOut << "D " << line << '\n';
    }
    aOut << "END\n";
}

void EmitHashes(std::ostream& aOut, test::Rng& aRng)
{
    for (int index = 0; index < 40; ++index)
    {
        const size_t lengths[] = {0, 1, 3, 55, 56, 63, 64, 65, 119, 120, 127, 128, 200, 1000};
        Bytes data(lengths[index % std::size(lengths)]);
        for (auto& byte : data)
        {
            byte = static_cast<uint8_t>(aRng.U64());
        }
        const Sha256Digest digest = Sha256Of(AsSpan(data));
        aOut << "HASH sha256 " << HexOrDash(AsSpan(data)) << ' ' << Hex(digest) << '\n';
        Bytes key(static_cast<size_t>(aRng.Below(140)));
        for (auto& byte : key)
        {
            byte = static_cast<uint8_t>(aRng.U64());
        }
        const Sha256Digest mac = HmacSha256(AsSpan(key), AsSpan(data));
        aOut << "HASH hmac " << HexOrDash(AsSpan(key)) << ' ' << HexOrDash(AsSpan(data)) << ' ' << Hex(mac) << '\n';
    }
    for (int index = 0; index < 20; ++index)
    {
        const std::string version = test::RandomText(aRng, 12, TextRule::Strict);
        aOut << "HASH build " << HexOrDash(ByteSpan(reinterpret_cast<const uint8_t*>(version.data()), version.size()))
             << ' ' << GameBuildId(version) << '\n';
        const std::string room = test::RandomRoom(aRng);
        const std::string password = test::RandomText(aRng, 40, TextRule::Script);
        const KeyHash hash = RoomKeyHash(room, password);
        aOut << "HASH room " << Hex(room) << ' ' << (password.empty() ? std::string("-") : Hex(password)) << ' '
             << Hex(hash) << '\n';
    }
    for (int index = 0; index < 20; ++index)
    {
        static constexpr const char* kNames[] = {"Codeware", "  redscript", "TweakXL\t", "ArchiveXL", "cet",
                                                 "RED4ext\x1c", "CP2077 Coop", "a b", "Z"};
        std::vector<ModEntry> entries;
        const size_t count = static_cast<size_t>(aRng.Below(6));
        std::ostringstream line;
        line << "HASH mods " << count;
        for (size_t entry = 0; entry < count; ++entry)
        {
            ModEntry mod{kNames[aRng.Below(std::size(kNames))],
                         std::to_string(aRng.Below(3)) + "." + std::to_string(aRng.Below(30)) + (aRng.Chance(0.3) ? " " : "")};
            line << ' ' << Hex(mod.name) << ' ' << Hex(mod.version);
            entries.push_back(mod);
        }
        line << ' ' << ModListHash(entries);
        aOut << line.str() << '\n';
    }
    for (int index = 0; index < 20; ++index)
    {
        static constexpr const char* kHosts[] = {"203.0.113.5", "127.0.0.1", "::1", "2001:db8::42", "198.51.100.200"};
        Bytes secret(32);
        for (auto& byte : secret)
        {
            byte = static_cast<uint8_t>(aRng.U64());
        }
        const std::string host = kHosts[aRng.Below(std::size(kHosts))];
        const uint16_t port = static_cast<uint16_t>(aRng.Range(1, 65535));
        const uint64_t nonce = aRng.U64();
        const uint32_t issued = aRng.Chance(0.2) ? 0xFFFFFFF0u + static_cast<uint32_t>(aRng.Below(16)) : aRng.U32();
        const Cookie cookie = MakeCookie(AsSpan(secret), host, port, nonce, issued);
        aOut << "HASH cookie " << Hex(secret) << ' ' << Hex(host) << ' ' << port << ' ' << nonce << ' ' << issued << ' '
             << Hex(cookie) << '\n';
        const uint32_t now = issued + static_cast<uint32_t>(aRng.Range(-3, 14));
        Cookie presented = cookie;
        if (aRng.Chance(0.2))
        {
            presented[aRng.Below(presented.size())] ^= 0x01;
        }
        aOut << "HASH cookiecheck " << Hex(secret) << ' ' << Hex(host) << ' ' << port << ' ' << nonce << ' '
             << Hex(presented) << ' ' << now << ' ' << (CheckCookie(AsSpan(secret), host, port, nonce, presented, now) ? 1 : 0)
             << '\n';
    }
}

void EmitQuantizers(std::ostream& aOut, test::Rng& aRng)
{
    for (int index = 0; index < 400; ++index)
    {
        const double wide = (aRng.Unit() * 2.0 - 1.0) * 30000.0;
        const double half = std::floor(wide) + 0.5; // exact .5 ties
        const double value = index % 4 == 0 ? half / 1000.0 : wide;
        aOut << "QUANT mm " << HexFloat(value) << ' ' << MetersToMm(value) << '\n';
        aOut << "QUANT vel " << HexFloat(index % 4 == 0 ? half / 100.0 : wide / 50.0) << ' '
             << VelocityToCms(index % 4 == 0 ? half / 100.0 : wide / 50.0) << '\n';
        const double degrees = index % 5 == 0 ? std::floor(wide) * 45.0 : wide / 20.0;
        aOut << "QUANT yaw " << HexFloat(degrees) << ' ' << YawToU16(degrees) << '\n';
        aOut << "QUANT pitch " << HexFloat(wide / 200.0) << ' ' << PitchToI16(wide / 200.0) << '\n';
        double q[4];
        for (double& component : q)
        {
            component = aRng.Unit() * 2.0 - 1.0;
        }
        if (index % 7 == 0)
        {
            q[aRng.Below(4)] = 0.0;
        }
        aOut << "QUANT quat " << HexFloat(q[0]) << ' ' << HexFloat(q[1]) << ' ' << HexFloat(q[2]) << ' ' << HexFloat(q[3])
             << ' ' << PackQuat(q[0], q[1], q[2], q[3]) << '\n';
    }
}

void EmitDelta(std::ostream& aOut, const std::string& aName, uint64_t aSeed, size_t aEntities, size_t aBudget, int aTicks,
               double aLoss)
{
    test::World world(aSeed, aEntities);
    test::Rng rng(aSeed ^ 0x5DEECE66Dull);
    DeltaEncoder encoder(aBudget);
    DeltaDecoder decoder;
    std::multimap<int, uint32_t> inFlight;   // arrival tick -> snapshot tick
    std::multimap<int, uint32_t> acksDue;    // arrival tick -> acked tick
    std::map<uint32_t, Bytes> bodies;
    aOut << "DELTA " << aName << ' ' << aBudget << '\n';
    for (int now = 1; now <= aTicks; ++now)
    {
        world.Step();
        std::vector<uint32_t> acks;
        for (auto it = acksDue.begin(); it != acksDue.end() && it->first <= now;)
        {
            acks.push_back(it->second);
            it = acksDue.erase(it);
        }
        const uint32_t sampleTime = static_cast<uint32_t>(now) * 100u;
        aOut << "TICK " << sampleTime << ' ' << acks.size();
        for (const uint32_t ack : acks)
        {
            aOut << ' ' << ack;
            encoder.OnAck(ack);
        }
        aOut << '\n';
        const EntityView states = world.States();
        const std::map<uint16_t, double> weights = world.Weights(rng);
        for (const auto& [netId, weight] : weights)
        {
            aOut << "W " << netId << ' ' << HexFloat(weight) << '\n';
        }
        for (const auto& [netId, state] : states.Entries())
        {
            aOut << StateLine(netId, state) << '\n';
        }
        EncodedSnapshot encoded;
        if (encoder.Encode(sampleTime, states, weights, encoded) != Status::Ok)
        {
            std::printf("emit: delta encode failed at tick %d\n", now);
            std::exit(EXIT_FAILURE);
        }
        aOut << "OUT " << encoded.tick << ' ' << encoded.baseline << ' ' << ViewHash(encoded.view) << ' '
             << Hex(AsSpan(encoded.body)) << '\n';
        bodies[encoded.tick] = encoded.body;
        if (!rng.Chance(aLoss))
        {
            inFlight.emplace(now + static_cast<int>(rng.Range(1, 4)), encoded.tick);
        }
        if (rng.Chance(0.03))
        {
            inFlight.emplace(now + static_cast<int>(rng.Range(1, 8)), encoded.tick); // duplicate delivery
        }
        if (now % 50 == 0 && encoded.tick > 70)
        {
            inFlight.emplace(now + 1, encoded.tick - 70); // far too late: stale
        }
        for (auto it = inFlight.begin(); it != inFlight.end() && (it->first <= now || now == aTicks);)
        {
            const uint32_t tick = it->second;
            it = inFlight.erase(it);
            Body body;
            DecodeBody(static_cast<uint8_t>(coopv2::MsgType::EntitySnapshot), AsSpan(bodies[tick]), body);
            const EntityView* view = decoder.Apply(std::get<EntitySnapshotMsg>(body));
            aOut << "APPLY " << tick << ' ' << (view != nullptr ? ViewHash(*view) : std::string("none")) << '\n';
            if (view != nullptr && !rng.Chance(aLoss))
            {
                acksDue.emplace(now + static_cast<int>(rng.Range(1, 3)), tick);
            }
        }
    }
    aOut << EncoderStatsLine(encoder.Stats()) << '\n' << DecoderStatsLine(decoder.Stats()) << '\n' << "ENDDELTA\n";
}

int RunEmit(const char* aPath, uint64_t aSeed)
{
    std::ofstream out(aPath, std::ios::binary);
    if (!out)
    {
        std::printf("cannot write %s\n", aPath);
        return 2;
    }
    out << "# C++ v2 codec vectors (coopnet_v2_golden emit, seed " << aSeed << ")\n";
    for (const MsgSpec& spec : MessageSpecs())
    {
        out << "SPEC " << unsigned(spec.type) << ' ' << spec.name << ' ' << DeliveryName(spec.delivery) << ' '
            << SenderName(spec.sender) << ' ' << RouteName(spec.route) << ' ' << unsigned(spec.minMinor) << '\n';
    }
    for (const auto& [name, value] : Constants())
    {
        out << "CONST " << name << ' ' << value << '\n';
    }
    test::Rng rng(aSeed);
    EmitHashes(out, rng);
    EmitQuantizers(out, rng);
    int valid = 0;
    int mutatedValid = 0;
    int invalid = 0;
    // Every message type at least 20 times on its own, then mixed datagrams.
    for (const MsgSpec& spec : MessageSpecs())
    {
        for (int repeat = 0; repeat < 20; ++repeat)
        {
            const Body body = test::RandomBody(rng, spec.type);
            OutMessage message{static_cast<uint8_t>(rng.U64()), std::nullopt, body};
            const bool reliable = spec.delivery == Delivery::Reliable ||
                                  (spec.delivery == Delivery::ByChannel &&
                                   ScriptChannelReliable(std::get<ScriptMsg>(body).channel));
            if (reliable)
            {
                message.relSeq = static_cast<uint16_t>(rng.U64());
            }
            PacketInfo header{static_cast<uint8_t>(coopv2::PacketType::Data), rng.U64(), static_cast<uint16_t>(rng.U64()),
                              static_cast<uint16_t>(rng.U64()), rng.U32()};
            Bytes datagram;
            if (EncodeDataPacket(header, std::span<const OutMessage>(&message, 1), datagram) != Status::Ok)
            {
                continue;
            }
            std::vector<std::string> lines;
            if (DescribeDatagram(AsSpan(datagram), lines) != Status::Ok)
            {
                std::printf("emit: C++ rejects its own %s\n", spec.name);
                return EXIT_FAILURE;
            }
            EmitValid(out, std::string("cpp-") + spec.name + "-" + std::to_string(repeat), true, datagram, lines);
            ++valid;
        }
    }
    for (int index = 0; index < 600; ++index)
    {
        const Bytes datagram = test::RandomDatagram(rng);
        std::vector<std::string> lines;
        if (DescribeDatagram(AsSpan(datagram), lines) != Status::Ok)
        {
            std::printf("emit: C++ rejects its own random datagram %d\n", index);
            return EXIT_FAILURE;
        }
        EmitValid(out, "cpp-random-" + std::to_string(index), true, datagram, lines);
        ++valid;
        for (int mutation = 0; mutation < 4; ++mutation)
        {
            const Bytes mutated = test::Mutate(rng, AsSpan(datagram));
            std::vector<std::string> mutatedLines;
            const std::string name = "cpp-mutation-" + std::to_string(index) + "-" + std::to_string(mutation);
            if (DescribeDatagram(AsSpan(mutated), mutatedLines) == Status::Ok)
            {
                EmitValid(out, name, false, mutated, mutatedLines);
                ++mutatedValid;
            }
            else
            {
                out << "INVALID " << name << ' ' << HexOrDash(AsSpan(mutated)) << '\n';
                ++invalid;
            }
        }
    }
    EmitDelta(out, "cpp-delta-lossy", aSeed + 1, 60, 600, 260, 0.15);
    EmitDelta(out, "cpp-delta-clean", aSeed + 2, 30, 1000, 120, 0.0);
    std::printf("emitted %d valid datagrams, %d accepted mutations, %d rejected mutations, 2 delta scenarios -> %s\n",
                valid, mutatedValid, invalid, aPath);
    return EXIT_SUCCESS;
}
} // namespace

int main(int argc, char** argv)
{
    if (argc >= 3 && std::string(argv[1]) == "check")
    {
        return RunCheck(argv[2]);
    }
    if (argc >= 3 && std::string(argv[1]) == "emit")
    {
        const uint64_t seed = argc >= 4 ? std::strtoull(argv[3], nullptr, 10) : 20261004;
        return RunEmit(argv[2], seed);
    }
    std::puts("usage: coopnet_v2_golden check <py_vectors.txt> | emit <cpp_vectors.txt> [seed]");
    return 2;
}
