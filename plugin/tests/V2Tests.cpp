// Unit tests for the v2 codec (src/v2): hashes, quantizers, packet header, cookie handshake,
// message framing, every message body and its validation rules, SCRIPT_MSG, X_WORLD_ID, and the
// delta snapshot encoder/decoder over a lossy simulated link.
//
// Cross-language agreement with proto.py is covered by tools/v2_golden.py (coopnet_v2_golden) and
// robustness by coopnet_v2_fuzz; these tests pin the behaviour on their own.

#include "V2TestSupport.hpp"
#include "v2/V2Codec.hpp"
#include "v2/V2Delta.hpp"
#include "v2/V2Describe.hpp"
#include "v2/V2Hash.hpp"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <numbers>
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

ByteSpan AsSpan(std::string_view aText)
{
    return {reinterpret_cast<const uint8_t*>(aText.data()), aText.size()};
}

template<size_t N>
std::string HexOf(const std::array<uint8_t, N>& aBytes)
{
    return Hex(ByteSpan(aBytes.data(), aBytes.size()));
}

Bytes Encoded(const Body& aBody)
{
    Bytes bytes;
    const Status status = EncodeBody(aBody, bytes);
    CHECK(status == Status::Ok);
    return bytes;
}

Status DecodeAs(uint8_t aType, const Bytes& aBytes)
{
    Body body;
    return DecodeBody(aType, AsSpan(aBytes), body);
}

Status EncodeStatus(const Body& aBody)
{
    Bytes bytes;
    return EncodeBody(aBody, bytes);
}

constexpr uint8_t Type(coopv2::MsgType aType)
{
    return static_cast<uint8_t>(aType);
}

PlayerSnapshotMsg Player()
{
    PlayerSnapshotMsg player;
    player.base = coopv2::PlayerSnapshot{7, 1000, -1450.0f, 180.0f, 22.0f, 16384, -350, 600, 0, 0, 2, 230, 0x0402};
    return player;
}

// ---- hashes ------------------------------------------------------------------------------------------

void TestHashes()
{
    std::puts("SHA-256, HMAC-SHA256 and the v2 identity hashes");
    CHECK(HexOf(Sha256Of(std::string_view(""))) == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    CHECK(HexOf(Sha256Of(std::string_view("abc"))) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    CHECK(HexOf(Sha256Of(std::string_view("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"))) ==
          "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
    Sha256 million;
    const std::string chunk(1000, 'a');
    for (int index = 0; index < 1000; ++index)
    {
        million.Update(chunk);
    }
    CHECK(HexOf(million.Finish()) == "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0");
    // RFC 4231 test cases 1, 2 and 6 (key longer than the block).
    const Bytes key1(20, 0x0b);
    CHECK(HexOf(HmacSha256(AsSpan(key1), AsSpan(std::string_view("Hi There")))) ==
          "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7");
    CHECK(HexOf(HmacSha256(AsSpan(std::string_view("Jefe")), AsSpan(std::string_view("what do ya want for nothing?")))) ==
          "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843");
    const Bytes key6(131, 0xaa);
    CHECK(HexOf(HmacSha256(AsSpan(key6), AsSpan(std::string_view("Test Using Larger Than Block-Size Key - Hash Key First")))) ==
          "60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54");
    // Values from proto.py.
    CHECK(GameBuildId("2.31a") == 3136553228u);
    CHECK(HexOf(RoomKeyHash("night-city", "pw")) == "3a47c185437f40d49cfd6cfc0632dd38");
    const ModEntry mods[] = {{"Codeware", "1.18.0"}, {" redscript ", "0.5.27"}};
    const ModEntry folded[] = {{"REDSCRIPT", "0.5.27"}, {"codeware", "1.18.0"}};
    CHECK(ModListHash(mods) == 8110454347611502495ull);
    CHECK(ModListHash(folded) == ModListHash(mods));
    const ModEntry other[] = {{"Codeware", "1.17.0"}, {"redscript", "0.5.27"}};
    CHECK(ModListHash(other) != ModListHash(mods));
    // Cookie: matches proto.make_cookie, expires after 10 s, bound to address, port, nonce and secret.
    std::array<uint8_t, 32> secret{};
    for (size_t index = 0; index < secret.size(); ++index)
    {
        secret[index] = static_cast<uint8_t>(index);
    }
    const Cookie cookie = MakeCookie(secret, "203.0.113.5", 50000, 77, 1000);
    CHECK(HexOf(cookie) == "e80300007361c3cd7877948e990b3789");
    CHECK(CheckCookie(secret, "203.0.113.5", 50000, 77, cookie, 1005));
    CHECK(CheckCookie(secret, "203.0.113.5", 50000, 77, cookie, 1010));
    CHECK(!CheckCookie(secret, "203.0.113.5", 50000, 77, cookie, 1011));
    CHECK(!CheckCookie(secret, "203.0.113.5", 50000, 77, cookie, 999)); // issued in the future
    CHECK(!CheckCookie(secret, "203.0.113.6", 50000, 77, cookie, 1001));
    CHECK(!CheckCookie(secret, "203.0.113.5", 50001, 77, cookie, 1001));
    CHECK(!CheckCookie(secret, "203.0.113.5", 50000, 78, cookie, 1001));
    std::array<uint8_t, 32> otherSecret = secret;
    otherSecret[0] ^= 1;
    CHECK(!CheckCookie(otherSecret, "203.0.113.5", 50000, 77, cookie, 1001));
    CHECK(!CheckCookie(secret, "203.0.113.5", 50000, 77, ByteSpan(cookie.data(), 15), 1001));
    const Cookie wrapped = MakeCookie(secret, "::1", 1, 5, 0xFFFFFFFEu);
    CHECK(CheckCookie(secret, "::1", 1, 5, wrapped, 3)); // u32 wrap-around: 5 s old
}

// ---- quantizers ------------------------------------------------------------------------------------

void TestQuantizers()
{
    std::puts("quantizers (round half to even, clamping)");
    CHECK(YawToU16(360.0) == 0);
    CHECK(YawToU16(-90.0) == 49152);
    CHECK(YawToU16(-1e-20) == 0);
    CHECK(std::fabs(U16ToYaw(YawToU16(123.4)) - 123.4) < 0.01);
    CHECK(MetersToMm(-1450.0005) == -1450000);
    CHECK(MetersToMm(0.0025) == 2 && MetersToMm(0.0035) == 4 && MetersToMm(0.0005) == 0 && MetersToMm(0.0015) == 2);
    CHECK(MetersToMm(1e12) == 2147483647 && MetersToMm(-1e12) == -2147483647 - 1);
    CHECK(MetersToMm(std::nan("")) == 0);
    CHECK(VelocityToCms(1e9) == 32767 && VelocityToCms(-1e9) == -32768);
    CHECK(PitchToI16(91.0) == 9000 && PitchToI16(-3.5) == -350);
    CHECK(PackQuat(0, 0, 0.38, 0.92) == 3758621460u);
    CHECK(PackQuat(0, 0, 0, 0) == 3758621184u);
    CHECK(PackQuat(std::nan(""), 0, 0, 1) == PackQuat(0, 0, 0, 1));
    test::Rng rng(3);
    double worst = 0.0;
    for (int index = 0; index < 2000; ++index)
    {
        std::array<double, 4> q{};
        double norm = 0.0;
        for (double& component : q)
        {
            component = rng.Unit() * 2.0 - 1.0;
            norm += component * component;
        }
        norm = std::sqrt(norm);
        for (double& component : q)
        {
            component /= norm;
        }
        const auto r = UnpackQuat(PackQuat(q[0], q[1], q[2], q[3]));
        const double dot = std::fabs(q[0] * r[0] + q[1] * r[1] + q[2] * r[2] + q[3] * r[3]);
        worst = std::max(worst, 2.0 * std::acos(std::min(1.0, dot)) * 180.0 / std::numbers::pi);
    }
    std::printf("    smallest-three quaternion: worst error %.4f deg over 2000 random rotations\n", worst);
    CHECK(worst < 0.25);
}

// ---- text --------------------------------------------------------------------------------------------

void TestText()
{
    std::puts("UTF-8 and text rules");
    CHECK(IsValidText("V \xD0\xA1\xD0\xB8\xD0\xBB\xD1\x8C", TextRule::Strict)); // "V Сил"
    CHECK(IsValidText("\xF0\x9F\x99\x82", TextRule::Strict));                   // U+1F642
    CHECK(!IsValidText("\xC0\xAF", TextRule::Strict));                          // overlong '/'
    CHECK(!IsValidText("\xE0\x80\xAF", TextRule::Script));                      // overlong
    CHECK(!IsValidText("\xED\xA0\x80", TextRule::Script));                      // surrogate
    CHECK(!IsValidText("\xF4\x90\x80\x80", TextRule::Script));                  // above U+10FFFF
    CHECK(!IsValidText("\xF5\x80\x80\x80", TextRule::Script));
    CHECK(!IsValidText("\xE2\x82", TextRule::Script));                          // truncated
    CHECK(!IsValidText("\x80", TextRule::Script));                              // lone continuation
    CHECK(!IsValidText("line\nbreak", TextRule::Strict));
    CHECK(IsValidText("line\nbreak\t{\"a\":1}", TextRule::Script));
    CHECK(!IsValidText(std::string_view("nul\0inside", 10), TextRule::Script));
    CHECK(!IsValidText("\xE2\x80\xA8", TextRule::Strict) && IsValidText("\xE2\x80\xA8", TextRule::Script)); // U+2028
    CHECK(!IsValidText("\xC2\x85", TextRule::Strict));                                                       // U+0085
    CHECK(!IsValidText("\x7F", TextRule::Strict));
    CHECK(IsValidRoom("night-city_1") && !IsValidRoom("") && !IsValidRoom(std::string(33, 'a')) &&
          !IsValidRoom("bad room") && !IsValidRoom("\xD0\xBA"));
}

// ---- packets and handshake ---------------------------------------------------------------------------

void TestPacketHeader()
{
    std::puts("packet header");
    Bytes datagram;
    const Bytes body{'x', 'y', 'z'};
    CHECK(EncodePacket(PacketInfo{6, 0x1234, 5, 4, 0xF0F0F0F0}, AsSpan(body), datagram) == Status::Ok);
    CHECK(datagram.size() == kPacketHeaderSize + 3);
    CHECK(datagram[0] == 0xCB && datagram[1] == 0x77 && datagram[2] == 2 && datagram[3] == 6);
    DecodedPacket packet;
    CHECK(DecodePacket(AsSpan(datagram), packet) == Status::Ok);
    CHECK(packet.header.token == 0x1234 && packet.header.seq == 5 && packet.header.ack == 4 &&
          packet.header.ackBits == 0xF0F0F0F0 && packet.body.size() == 3 && packet.body[2] == 'z');
    CHECK(!IsV2(AsSpan(std::string_view("CP1,1,0,0,0,1,0,1"))) && !IsV2(AsSpan(std::string_view("WELCOME,1"))));
    CHECK(DecodePacket(AsSpan(Bytes{0xCB, 0x77, 0x02}), packet) == Status::NotV2);
    Bytes notMagic(32, 0);
    notMagic[0] = 'X';
    CHECK(DecodePacket(AsSpan(notMagic), packet) == Status::NotV2);
    Bytes unknownType{0xCB, 0x77, 2, 99};
    unknownType.resize(20, 0);
    CHECK(DecodePacket(AsSpan(unknownType), packet) == Status::UnknownPacketType);
    Bytes truncated{0xCB, 0x77, 2, 6, 0, 0};
    CHECK(DecodePacket(AsSpan(truncated), packet) == Status::Truncated);
    Bytes large(1300, 0);
    large[0] = 0xCB;
    large[1] = 0x77;
    large[2] = 2;
    CHECK(DecodePacket(AsSpan(large), packet) == Status::TooLarge);
    Bytes future{0xCB, 0x77, 3, 1};
    future.resize(44, 0);
    CHECK(DecodePacket(AsSpan(future), packet) == Status::VersionMismatch && packet.major == 3 && packet.header.type == 1);
    const Bytes big(1181, 0);
    CHECK(EncodePacket(PacketInfo{}, AsSpan(big), datagram) == Status::TooLarge && datagram.empty());
    const Bytes fits(1180, 0);
    CHECK(EncodePacket(PacketInfo{}, AsSpan(fits), datagram) == Status::Ok && datagram.size() == 1200);
}

JoinInfo SampleJoin()
{
    JoinInfo join;
    join.fixed = coopv2::JoinFixed{0, 1, 0, 0x1FF, GameBuildId("2.31a"), 0, 2, 0, 0x1122334455667788ull, 5, 42, 0};
    join.room = "night-city_1";
    join.name = "V \xD0\xA1\xD0\xB8\xD0\xBB\xD1\x8C\xD0\xB2\xD0\xB5\xD1\x80\xD1\x85\xD0\xB5\xD0\xBD\xD0\xB4"; // V Сильверхенд
    return join;
}

void TestHandshake()
{
    std::puts("cookie handshake bodies");
    const JoinInfo join = SampleJoin();
    Bytes hello;
    CHECK(EncodeHello(join, hello) == Status::Ok);
    CHECK(hello.size() >= coopv2::kHelloMinPacket);
    DecodedPacket packet;
    CHECK(DecodePacket(AsSpan(hello), packet) == Status::Ok && packet.header.type == 1);
    JoinInfo decoded;
    size_t padding = 0;
    CHECK(DecodeHello(packet.body, decoded, &padding) == Status::Ok);
    CHECK(decoded.room == join.room && decoded.name == join.name && decoded.fixed.client_nonce == 42 &&
          decoded.fixed.mod_hash == 0x1122334455667788ull && decoded.fixed.caps == 0x1FF);
    CHECK(padding == hello.size() - kPacketHeaderSize - sizeof(coopv2::JoinFixed) - 2 - join.room.size() - join.name.size());
    // Anti-amplification: CHALLENGE (40) and REJECT stay smaller than the HELLO that triggers them.
    coopv2::Challenge challenge{0, 1, 0, {}};
    Bytes challengePacket;
    CHECK(EncodeChallenge(challenge, challengePacket) == Status::Ok && challengePacket.size() == 40);
    Bytes reject;
    CHECK(EncodeReject(RejectInfo{4, 0, 1, std::string(200, 'x')}, reject) == Status::Ok);
    CHECK(reject.size() < hello.size() && reject.size() == kPacketHeaderSize + 4 + kMaxRejectTextBytes);
    // A HELLO without padding, or with non-zero padding, gets nothing.
    Bytes unpadded;
    CHECK(EncodeJoin(join, unpadded) == Status::Ok);
    CHECK(DecodeHello(AsSpan(unpadded), decoded) == Status::BadPadding);
    Bytes dirty(packet.body.begin(), packet.body.end());
    dirty.back() = 1;
    CHECK(DecodeHello(AsSpan(dirty), decoded) == Status::BadPadding);
    // Bad room, name and role.
    for (const char* room : {"", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bad room", "\xD0\xBA\xD0\xBE"})
    {
        JoinInfo bad = join;
        bad.room = room;
        CHECK(EncodeHello(bad, hello) == Status::BadRoom && hello.empty());
    }
    JoinInfo evil = join;
    evil.name = "evil\x1b[31m";
    CHECK(EncodeHello(evil, hello) == Status::BadText);
    evil.name = std::string(25, 'n');
    CHECK(EncodeHello(evil, hello) == Status::BadText);
    JoinInfo badRole = join;
    badRole.fixed.role = 4;
    CHECK(EncodeHello(badRole, hello) == Status::BadValue);
    // AUTH, WELCOME, REJECT, DISCONNECT round trips.
    AuthInfo auth;
    auth.join = join;
    for (size_t index = 0; index < auth.cookie.size(); ++index)
    {
        auth.cookie[index] = static_cast<uint8_t>(index);
    }
    auth.keyHash = RoomKeyHash("r", "p");
    Bytes authPacket;
    CHECK(EncodeAuth(auth, authPacket) == Status::Ok);
    CHECK(DecodePacket(AsSpan(authPacket), packet) == Status::Ok);
    AuthInfo authBack;
    CHECK(DecodeAuth(packet.body, authBack) == Status::Ok && authBack.cookie == auth.cookie &&
          authBack.keyHash == auth.keyHash && authBack.join.name == join.name);
    Bytes shortAuth(packet.body.begin(), packet.body.end() - 1);
    CHECK(DecodeAuth(AsSpan(shortAuth), authBack) == Status::BadSize);
    const coopv2::Welcome welcome{1, 3, 2, 1, (1ull << 63) + 5, 99, 30, 10, 1200, 0x1FF};
    Bytes welcomePacket;
    CHECK(EncodeWelcome(welcome, welcomePacket) == Status::Ok && welcomePacket.size() == 44);
    CHECK(DecodePacket(AsSpan(welcomePacket), packet) == Status::Ok && packet.header.token == welcome.token);
    coopv2::Welcome welcomeBack{};
    CHECK(DecodeWelcome(packet.body, welcomeBack) == Status::Ok && std::memcmp(&welcomeBack, &welcome, sizeof(welcome)) == 0);
    CHECK(EncodeReject(RejectInfo{1, 0, 1, "relay speaks v2.1"}, reject) == Status::Ok);
    CHECK(DecodePacket(AsSpan(reject), packet) == Status::Ok);
    RejectInfo rejectBack;
    CHECK(DecodeReject(packet.body, rejectBack) == Status::Ok && rejectBack.reason == 1 && rejectBack.minorMax == 1 &&
          rejectBack.text == "relay speaks v2.1");
    Bytes disconnect;
    CHECK(EncodeDisconnect(77, static_cast<uint8_t>(coopv2::DisconnectReason::Kicked), disconnect) == Status::Ok);
    uint8_t reason = 0;
    CHECK(DecodePacket(AsSpan(disconnect), packet) == Status::Ok && packet.header.token == 77 &&
          DecodeDisconnect(packet.body, reason) == Status::Ok && reason == 3);
    CHECK(DecodeDisconnect(ByteSpan(), reason) == Status::BadSize);
}

// ---- messages ----------------------------------------------------------------------------------------

void TestFraming()
{
    std::puts("message framing inside DATA");
    Bytes payload;
    const Bytes ack = Encoded(coopv2::SnapshotAck{9});
    for (size_t index = 0; index < kMaxMessagesPerPacket; ++index)
    {
        CHECK(AppendMessage(payload, Type(coopv2::MsgType::SnapshotAck), 0xFF, std::nullopt, AsSpan(ack)) == Status::Ok);
    }
    std::vector<MessageView> messages;
    CHECK(DecodeMessages(AsSpan(payload), messages) == Status::Ok && messages.size() == 96);
    CHECK(AppendMessage(payload, Type(coopv2::MsgType::SnapshotAck), 0xFF, std::nullopt, AsSpan(ack)) == Status::Ok);
    CHECK(DecodeMessages(AsSpan(payload), messages) == Status::TooManyMessages);
    Bytes reliable;
    CHECK(AppendMessage(reliable, Type(coopv2::MsgType::Chat), 2, uint16_t{65535}, AsSpan(Encoded(ChatMsg{0, "hi"}))) ==
          Status::Ok);
    CHECK(DecodeMessages(AsSpan(reliable), messages) == Status::Ok && messages.size() == 1 && messages[0].reliable &&
          messages[0].relSeq == 65535 && messages[0].peer == 2 && messages[0].type == Type(coopv2::MsgType::Chat));
    CHECK(MessageSize(3, true) == 9 && MessageSize(3, false) == 7);
    for (size_t cut = 1; cut < reliable.size(); ++cut)
    {
        const Bytes prefix(reliable.begin(), reliable.begin() + static_cast<std::ptrdiff_t>(cut));
        CHECK(DecodeMessages(AsSpan(prefix), messages) == Status::Truncated);
    }
    CHECK(DecodeMessages(ByteSpan(), messages) == Status::Ok && messages.empty());
    const Bytes tooBig(kMaxMessageBody + 1, 0);
    CHECK(AppendMessage(payload, 0x30, 0, std::nullopt, AsSpan(tooBig)) == Status::TooLarge);
    CHECK(AppendMessage(payload, 0x80, 0, std::nullopt, AsSpan(ack)) == Status::BadValue);
    // EncodeDataPacket: too many messages, too large.
    std::vector<OutMessage> many(97, OutMessage{0xFF, std::nullopt, coopv2::SnapshotAck{1}});
    Bytes datagram;
    CHECK(EncodeDataPacket(PacketInfo{}, many, datagram) == Status::TooManyMessages);
    std::vector<OutMessage> heavy(2, OutMessage{0xFF, uint16_t{1}, ScriptMsg{20, 0, std::string(1000, 'x')}});
    CHECK(EncodeDataPacket(PacketInfo{}, heavy, datagram) == Status::TooLarge);
    heavy.resize(1);
    CHECK(EncodeDataPacket(PacketInfo{}, heavy, datagram) == Status::Ok && datagram.size() == 20 + 6 + 4 + 1000);
    Body unknown;
    CHECK(DecodeBody(0x7E, AsSpan(ack), unknown) == Status::UnknownMessageType);
}

void TestEveryTypeRoundTrips()
{
    std::puts("every message type: encode, decode, describe");
    test::Rng rng(11);
    int roundTrips = 0;
    for (const MsgSpec& spec : MessageSpecs())
    {
        for (int repeat = 0; repeat < 200; ++repeat)
        {
            const Body body = test::RandomBody(rng, spec.type);
            CHECK(TypeOf(body) == spec.type);
            Bytes bytes;
            const Status status = EncodeBody(body, bytes);
            CHECK(status == Status::Ok);
            if (status != Status::Ok)
            {
                std::printf("    %s does not encode: %s\n", spec.name, ToString(status));
                break;
            }
            Body back;
            CHECK(DecodeBody(spec.type, AsSpan(bytes), back) == Status::Ok);
            CHECK(DescribeBody(back) == DescribeBody(body));
            Bytes again;
            CHECK(EncodeBody(back, again) == Status::Ok && again == bytes);
            ++roundTrips;
        }
    }
    std::printf("    %d body round trips over %zu message types\n", roundTrips, MessageSpecs().size());
    // Documented sizes.
    CHECK(Encoded(Player()).size() == 32);
    PlayerSnapshotMsg driving = Player();
    driving.base.flags |= coopv2::kPlayerDriving;
    driving.vehicle = coopv2::VehicleBlock{0x8002, -1450.0f, 180.0f, 22.0f, PackQuat(0, 0, 0.38, 0.92), 1400, 0, 0, 0, 0,
                                           120, -20, 70, 0, 1};
    const Bytes drivingBytes = Encoded(driving);
    CHECK(drivingBytes.size() == 66);
    CHECK(DecodeAs(Type(coopv2::MsgType::PlayerSnapshot), Bytes(drivingBytes.begin(), drivingBytes.end() - 1)) ==
          Status::BadSize);
    Bytes plusVehicleSized = Encoded(Player());
    plusVehicleSized.resize(66, 0);
    CHECK(DecodeAs(Type(coopv2::MsgType::PlayerSnapshot), plusVehicleSized) == Status::BadSize);
    PlayerSnapshotMsg flagWithoutBlock = Player();
    flagWithoutBlock.base.flags |= coopv2::kPlayerDriving;
    CHECK(EncodeStatus(flagWithoutBlock) == Status::BadValue);
    CHECK(Encoded(ScriptMsg{1, 0, ""}).size() == 4);
    CHECK(Encoded(ChatMsg{0, "ok"}).size() == 4);
}

void TestValidation()
{
    std::puts("value validation (what proto.py rejects)");
    const auto rejects = [](const Body& aBody) { return EncodeStatus(aBody) != Status::Ok; };
    const float nan = std::numeric_limits<float>::quiet_NaN();
    const float inf = std::numeric_limits<float>::infinity();
    for (const auto& change : std::vector<void (*)(coopv2::PlayerSnapshot&)>{
             [](coopv2::PlayerSnapshot& aBase) { aBase.x = std::numeric_limits<float>::quiet_NaN(); },
             [](coopv2::PlayerSnapshot& aBase) { aBase.y = std::numeric_limits<float>::infinity(); },
             [](coopv2::PlayerSnapshot& aBase) { aBase.x = 1e6f; },
             [](coopv2::PlayerSnapshot& aBase) { aBase.z = -6000.0f; },
             [](coopv2::PlayerSnapshot& aBase) { aBase.move_state = 99; },
             [](coopv2::PlayerSnapshot& aBase) { aBase.pitch = 9001; }})
    {
        PlayerSnapshotMsg player = Player();
        change(player.base);
        CHECK(rejects(player));
    }
    PlayerSnapshotMsg edge = Player();
    edge.base.x = 20000.0f;
    edge.base.z = -5000.0f;
    edge.base.pitch = -9000;
    edge.base.move_state = 13;
    CHECK(!rejects(edge));
    PlayerSnapshotMsg driving = Player();
    driving.base.flags |= coopv2::kPlayerDriving;
    driving.vehicle = coopv2::VehicleBlock{1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0};
    CHECK(!rejects(driving));
    for (const auto& change : std::vector<void (*)(coopv2::VehicleBlock&)>{
             [](coopv2::VehicleBlock& aBlock) { aBlock.vehicle_net = 0; },
             [](coopv2::VehicleBlock& aBlock) { aBlock.steer = 101; },
             [](coopv2::VehicleBlock& aBlock) { aBlock.steer = -101; },
             [](coopv2::VehicleBlock& aBlock) { aBlock.throttle = 101; },
             [](coopv2::VehicleBlock& aBlock) { aBlock.brake = 101; },
             [](coopv2::VehicleBlock& aBlock) { aBlock.pz = std::numeric_limits<float>::quiet_NaN(); }})
    {
        PlayerSnapshotMsg player = driving;
        change(*player.vehicle);
        CHECK(rejects(player));
    }
    const coopv2::Hit hit{12, 0, 2, 1, 1, 33.5f, 5, 0, 0, 150, 77};
    CHECK(!rejects(hit));
    for (const auto& change : std::vector<void (*)(coopv2::Hit&)>{
             [](coopv2::Hit& aHit) { aHit.target_kind = 2; }, [](coopv2::Hit& aHit) { aHit.damage = -1.0f; },
             [](coopv2::Hit& aHit) { aHit.damage = 1.5e6f; }, [](coopv2::Hit& aHit) { aHit.hit_zone = 16; },
             [](coopv2::Hit& aHit) { aHit.target_net = 0; },
             [](coopv2::Hit& aHit) { aHit.damage = std::numeric_limits<float>::infinity(); }})
    {
        coopv2::Hit bad = hit;
        change(bad);
        CHECK(rejects(bad));
    }
    CHECK(rejects(coopv2::Death{1, 0, 0, 0, 3, 0, 0}) && rejects(coopv2::Death{1, 2, 0, 0, 0, 0, 0}));
    CHECK(rejects(coopv2::TimeWeather{0, 10001, 0, 0, 0, 0}) && rejects(coopv2::TimeWeather{0, 100, 0, 32, 0, 0}));
    CHECK(rejects(coopv2::TeleportReq{1, 2, 0}) && !rejects(coopv2::TeleportReq{1, 1, 0}));
    CHECK(rejects(coopv2::TeleportResp{1, 1, 0, nan, 0, 0, 0}) && rejects(coopv2::TeleportResp{1, 1, 0, 0, 0, 5001.0f, 0}));
    CHECK(rejects(coopv2::VehicleEnter{1, 0, 0, 1, 2, inf, 0, 0, 0}) && rejects(coopv2::VehicleExit{1, 0, 0, 0, 20001.0f, 0, 0}));
    CHECK(rejects(coopv2::Equip{8, 1, 0, 1, 1, 1}) && rejects(coopv2::Equip{0, 16, 0, 1, 1, 1}));
    CHECK(rejects(coopv2::SessionConfig{9, 200, 10, 0, 0}) && rejects(coopv2::SessionConfig{501, 200, 10, 0, 0}) &&
          rejects(coopv2::SessionConfig{100, 1001, 10, 0, 0}) && rejects(coopv2::SessionConfig{100, 200, 0, 0, 0}) &&
          rejects(coopv2::SessionConfig{100, 200, 31, 0, 0}) && !rejects(coopv2::SessionConfig{10, 10, 1, 0, 0}));
    CHECK(rejects(ModListMsg{2, 2, "a@1"}) && rejects(ModListMsg{0, 17, "a@1"}) && !rejects(ModListMsg{15, 16, "a@1"}));
    CHECK(rejects(ModListMsg{0, 1, std::string(256, 'm')}) && !rejects(ModListMsg{0, 1, std::string(255, 'm')}));
    CHECK(rejects(ChatMsg{0, std::string(201, 'x')}) && !rejects(ChatMsg{0, std::string(200, 'x')}));
    for (const char* text : {"line\nbreak", "bell\x07", " \x1f", "\xFF"})
    {
        CHECK(rejects(ChatMsg{0, text}));
    }
    PeerJoinedMsg joined;
    joined.fixed.role = 4;
    CHECK(rejects(joined));
    joined.fixed.role = 3;
    joined.name = std::string(25, 'n');
    CHECK(rejects(joined));
    // A mutated chat body with an invalid UTF-8 byte.
    Bytes chat = Encoded(ChatMsg{0, "ok"});
    chat.back() = 0xFF;
    CHECK(DecodeAs(Type(coopv2::MsgType::Chat), chat) == Status::BadText);
    Bytes trailing = Encoded(coopv2::WorldFact{1, -5});
    trailing.push_back(0);
    CHECK(DecodeAs(Type(coopv2::MsgType::WorldFact), trailing) == Status::TrailingBytes);
    trailing.resize(5);
    CHECK(DecodeAs(Type(coopv2::MsgType::WorldFact), trailing) == Status::Truncated);
}

void TestScriptMessages()
{
    std::puts("SCRIPT_MSG (protocol minor 1)");
    const Bytes longest = Encoded(ScriptMsg{1, 0, std::string(coopv2::kMaxScriptBytes, 'x')});
    CHECK(longest.size() == 2 + 2 + 1000);
    std::string cyrillic;
    for (int index = 0; index < 500; ++index)
    {
        cyrillic += "\xD0\xB6";
    }
    CHECK(EncodeStatus(ScriptMsg{31, 0x80, cyrillic}) == Status::Ok);
    CHECK(EncodeStatus(ScriptMsg{1, 0, std::string(1001, 'x')}) == Status::BadText);
    CHECK(EncodeStatus(ScriptMsg{1, 0, cyrillic + "\xD0\xB6"}) == Status::BadText);
    CHECK(EncodeStatus(ScriptMsg{1, 0, std::string("nul\0inside", 10)}) == Status::BadText);
    CHECK(EncodeStatus(ScriptMsg{0, 0, "x"}) == Status::BadValue);
    CHECK(EncodeStatus(ScriptMsg{32, 0, "x"}) == Status::BadValue);
    CHECK(EncodeStatus(ScriptMsg{16, 0, "evt|weapon|draw\t{\"ammo\": 30}\n"}) == Status::Ok);
    Body back;
    CHECK(DecodeBody(Type(coopv2::MsgType::ScriptMsg), AsSpan(Encoded(ScriptMsg{31, 0x80, "x"})), back) == Status::Ok &&
          std::get<ScriptMsg>(back).flags == 0x80 && std::get<ScriptMsg>(back).channel == 31);
    const Bytes ok = Encoded(ScriptMsg{1, 0, "ok"});
    for (const Bytes& mutated : {Bytes(ok.begin(), ok.end() - 1), Bytes(ok.begin(), ok.begin() + 3),
                                 Bytes(ok.begin(), ok.begin() + 2)})
    {
        CHECK(DecodeAs(Type(coopv2::MsgType::ScriptMsg), mutated) == Status::Truncated);
    }
    Bytes extra = ok;
    extra.push_back('!');
    CHECK(DecodeAs(Type(coopv2::MsgType::ScriptMsg), extra) == Status::TrailingBytes);
    const MsgSpec* spec = FindSpec(Type(coopv2::MsgType::ScriptMsg));
    CHECK(spec != nullptr && spec->delivery == Delivery::ByChannel && spec->minMinor == 1 && spec->route == Route::Peer);
    for (uint8_t channel : {uint8_t{1}, uint8_t{15}})
    {
        CHECK(DeliveryOk(*spec, false, ScriptMsg{channel, 0, ""}) && !DeliveryOk(*spec, true, ScriptMsg{channel, 0, ""}));
    }
    for (uint8_t channel : {uint8_t{16}, uint8_t{31}})
    {
        CHECK(DeliveryOk(*spec, true, ScriptMsg{channel, 0, ""}) && !DeliveryOk(*spec, false, ScriptMsg{channel, 0, ""}));
    }
    const MsgSpec* chat = FindSpec(Type(coopv2::MsgType::Chat));
    CHECK(DeliveryOk(*chat, true, ChatMsg{}) && !DeliveryOk(*chat, false, ChatMsg{}));
    CHECK(RequiredMinor(ScriptMsg{1, 0, ""}) == 1 && RequiredMinor(ChatMsg{}) == 0);
}

void TestEntitySnapshots()
{
    std::puts("ENTITY_SNAPSHOT records and X_WORLD_ID");
    EntityRecord vehicle;
    vehicle.netId = 1;
    vehicle.spawn = EntitySpawnInfo{4, 2, 1, 9, 1};
    vehicle.pos = std::array<int32_t, 3>{-1450000, 180000, 22000};
    vehicle.quat = PackQuat(0, 0, 0, 1);
    vehicle.vel = std::array<int16_t, 3>{1200, 0, 0};
    EntityRecord walker;
    walker.netId = 2;
    walker.posDelta = std::array<int16_t, 3>{120, -40, 0};
    walker.yaw = uint16_t{300};
    walker.state = std::array<uint8_t, 3>{1, 2, 200};
    EntityRecord removed;
    removed.netId = 3;
    removed.remove = true;
    EntityRecord aiming;
    aiming.netId = 4;
    aiming.target = uint16_t{0xFF02};
    aiming.weapon = 77;
    EntityRecord placed;
    placed.netId = 6;
    placed.spawn = EntitySpawnInfo{3, 0, 1, 10, 2};
    placed.pos = std::array<int32_t, 3>{-1449000, 181000, 22000};
    placed.yaw = uint16_t{900};
    placed.worldId = 0x8F00112233445566ull;
    EntityRecord bound;
    bound.netId = 7;
    bound.worldId = 0x0000000001000001ull;
    const EntitySnapshotMsg snapshot{10, 8, 5000, {vehicle, walker, removed, aiming, placed, bound}};
    const Bytes bytes = Encoded(snapshot);
    Body back;
    CHECK(DecodeBody(Type(coopv2::MsgType::EntitySnapshot), AsSpan(bytes), back) == Status::Ok);
    const auto& decoded = std::get<EntitySnapshotMsg>(back);
    CHECK(decoded.records.size() == 6 && decoded.records[2].remove && decoded.records[3].target == 0xFF02 &&
          decoded.records[4].worldId == 0x8F00112233445566ull && decoded.records == snapshot.records);
    CHECK(EntityRecordSize(bound) == 3 + 1 + 8);
    CHECK(Encoded(EntitySnapshotMsg{1, 0, 0, {bound}}).size() == 14 + 12);
    CHECK(RequiredMinor(snapshot) == 1 && RequiredMinor(EntitySnapshotMsg{1, 0, 0, {walker}}) == 0);
    // proto.py's rule list.
    const auto bad = [](EntityRecord aRecord) {
        return EncodeStatus(EntitySnapshotMsg{2, 0, 0, {aRecord}}) != Status::Ok;
    };
    EntityRecord record;
    record.netId = 0;
    record.yaw = uint16_t{1};
    CHECK(bad(record));
    record = EntityRecord{};
    record.netId = 1;
    record.pos = std::array<int32_t, 3>{0, 0, 0};
    record.posDelta = std::array<int16_t, 3>{1, 1, 1};
    CHECK(bad(record));
    record = EntityRecord{};
    record.netId = 1;
    record.yaw = uint16_t{1};
    record.quat = 2;
    CHECK(bad(record));
    record = EntityRecord{};
    record.netId = 1;
    record.spawn = EntitySpawnInfo{1, 0, 0, 1, 1};
    record.yaw = uint16_t{5};
    CHECK(bad(record)); // spawn without POS
    record.pos = std::array<int32_t, 3>{0, 0, 0};
    record.yaw.reset();
    CHECK(bad(record)); // spawn without a rotation
    record.yaw = uint16_t{5};
    record.spawn->kind = 9;
    CHECK(bad(record));
    record = EntityRecord{};
    record.netId = 1;
    record.pos = std::array<int32_t, 3>{2147483647, 0, 0};
    CHECK(bad(record));
    record = EntityRecord{};
    record.netId = 1;
    record.state = std::array<uint8_t, 3>{99, 0, 0};
    CHECK(bad(record));
    record = EntityRecord{};
    record.netId = 1;
    CHECK(bad(record)); // empty
    record.remove = true;
    record.worldId = 5;
    CHECK(bad(record)); // a removal carries no fields
    CHECK(EncodeStatus(EntitySnapshotMsg{2, 0, 0, {walker, walker}}) == Status::DuplicateNetId);
    CHECK(EncodeStatus(EntitySnapshotMsg{2, 2, 0, {}}) == Status::BadBaseline);
    CHECK(EncodeStatus(EntitySnapshotMsg{2, 3, 0, {}}) == Status::BadBaseline);
    Bytes countTooHigh = Encoded(EntitySnapshotMsg{2, 0, 0, {}});
    countTooHigh[12] = 0x00;
    countTooHigh[13] = 0x01; // count 256
    CHECK(DecodeAs(Type(coopv2::MsgType::EntitySnapshot), countTooHigh) == Status::TooManyRecords);
    Bytes removal = Encoded(EntitySnapshotMsg{2, 0, 0, {removed}});
    removal[14 + 2] |= coopv2::kMaskYaw;
    CHECK(DecodeAs(Type(coopv2::MsgType::EntitySnapshot), removal) == Status::BadRecord);
    Bytes unknownExt = Encoded(EntitySnapshotMsg{1, 0, 0, {bound}});
    unknownExt[14 + 3] |= 0x10;
    CHECK(DecodeAs(Type(coopv2::MsgType::EntitySnapshot), unknownExt) == Status::BadRecord);
    // Spawn reserved byte: ignored on decode, written as 0 (as proto.py does).
    Bytes reserved = Encoded(EntitySnapshotMsg{1, 0, 0, {vehicle}});
    reserved[14 + 3 + 3] = 0x5A;
    Body reservedBody;
    CHECK(DecodeBody(Type(coopv2::MsgType::EntitySnapshot), AsSpan(reserved), reservedBody) == Status::Ok);
    CHECK(Encoded(reservedBody) == Encoded(EntitySnapshotMsg{1, 0, 0, {vehicle}}));
    // The largest snapshot: 255 small records fit one message body.
    EntitySnapshotMsg crowd{5, 0, 0, {}};
    for (uint16_t netId = 1; netId <= 230; ++netId)
    {
        EntityRecord small;
        small.netId = netId;
        small.yaw = netId;
        crowd.records.push_back(small);
    }
    CHECK(Encoded(crowd).size() == 14 + 230 * 5);
}

void TestDeltaLink()
{
    std::puts("delta snapshots over a lossy link (acks lost too, 300 ticks, 80 entities)");
    for (const double loss : {0.0, 0.2, 0.45})
    {
        test::World world(7, 80);
        test::Rng rng(99);
        DeltaEncoder encoder(1000);
        DeltaDecoder decoder;
        std::multimap<int, std::pair<uint32_t, Bytes>> inFlight;
        std::multimap<int, uint32_t> acks;
        std::map<uint32_t, std::string> sentViews;
        int mismatches = 0;
        int decodedTicks = 0;
        size_t largest = 0;
        for (int now = 1; now <= 300; ++now)
        {
            world.Step();
            for (auto it = acks.begin(); it != acks.end() && it->first <= now; it = acks.erase(it))
            {
                encoder.OnAck(it->second);
            }
            EncodedSnapshot encoded;
            CHECK(encoder.Encode(static_cast<uint32_t>(now) * 100, world.States(), world.Weights(rng), encoded) == Status::Ok);
            largest = std::max(largest, encoded.body.size());
            sentViews[encoded.tick] = ViewHash(encoded.view);
            if (!rng.Chance(loss))
            {
                inFlight.emplace(now + static_cast<int>(rng.Range(1, 4)), std::make_pair(encoded.tick, encoded.body));
            }
            for (auto it = inFlight.begin(); it != inFlight.end() && (it->first <= now || now == 300);)
            {
                Body body;
                CHECK(DecodeBody(Type(coopv2::MsgType::EntitySnapshot), AsSpan(it->second.second), body) == Status::Ok);
                if (const EntityView* view = decoder.Apply(std::get<EntitySnapshotMsg>(body)))
                {
                    ++decodedTicks;
                    mismatches += ViewHash(*view) != sentViews[it->second.first] ? 1 : 0;
                    if (!rng.Chance(loss))
                    {
                        acks.emplace(now + static_cast<int>(rng.Range(1, 3)), it->second.first);
                    }
                }
                it = inFlight.erase(it);
            }
        }
        // With loss the final state needs a few clean ticks to converge: flush with no loss.
        for (int now = 301; now <= 340; ++now)
        {
            for (auto it = acks.begin(); it != acks.end(); it = acks.erase(it))
            {
                encoder.OnAck(it->second);
            }
            EncodedSnapshot encoded;
            CHECK(encoder.Encode(static_cast<uint32_t>(now) * 100, world.States(), {}, encoded) == Status::Ok);
            Body body;
            DecodeBody(Type(coopv2::MsgType::EntitySnapshot), AsSpan(encoded.body), body);
            if (const EntityView* view = decoder.Apply(std::get<EntitySnapshotMsg>(body)))
            {
                ++decodedTicks;
                mismatches += ViewHash(*view) != ViewHash(encoded.view) ? 1 : 0;
                acks.emplace(now, encoded.tick);
            }
        }
        EncodedSnapshot last;
        encoder.Encode(34100, world.States(), {}, last);
        Body body;
        DecodeBody(Type(coopv2::MsgType::EntitySnapshot), AsSpan(last.body), body);
        const EntityView* finalView = decoder.Apply(std::get<EntitySnapshotMsg>(body));
        const auto& stats = encoder.Stats();
        std::printf("    loss %2.0f%%: decoded %d ticks, view mismatches %d, largest body %zu B, "
                    "%.0f%% of full size, deferred %llu, missing baseline %llu\n",
                    loss * 100.0, decodedTicks, mismatches, largest,
                    100.0 * static_cast<double>(stats.bytes) / static_cast<double>(stats.fullBytes),
                    static_cast<unsigned long long>(stats.deferred),
                    static_cast<unsigned long long>(decoder.Stats().missingBaseline));
        CHECK(mismatches == 0);
        CHECK(largest <= 1000);
        CHECK(decoder.Stats().inconsistent == 0);
        CHECK(finalView != nullptr && ViewHash(*finalView) == ViewHash(world.States()));
    }
}

void TestDeltaRecords()
{
    std::puts("delta records: world ids travel with the spawn");
    EntityInput input;
    input.kind = static_cast<uint8_t>(coopv2::EntityKind::QuestNpc);
    input.attitude = 1;
    input.record = 77;
    input.appearance = 5;
    input.posMeters = {-1400.0, 200.0, 20.0};
    input.yawDegrees = 90.0;
    input.worldId = 0x8F00000012345678ull;
    const EntityState placed = Quantize(input);
    CHECK(placed.rot == 16384 && placed.pos[0] == -1400000 && placed.state == (std::array<uint8_t, 3>{0, 0, 255}));
    const auto spawn = MakeRecord(9, nullptr, placed);
    CHECK(spawn && spawn->spawn && spawn->worldId == 0x8F00000012345678ull && spawn->yaw == 16384 && !spawn->vel);
    EntityState applied;
    CHECK(ApplyRecord(nullptr, *spawn, applied) == Status::Ok && applied == placed);
    input.posMeters[0] = -1399.0;
    const EntityState moved = Quantize(input);
    const auto delta = MakeRecord(9, &placed, moved);
    CHECK(delta && !delta->spawn && !delta->worldId && delta->posDelta == (std::array<int16_t, 3>{1000, 0, 0}));
    CHECK(!MakeRecord(9, &moved, moved));
    input.worldId = 0x8F00000087654321ull;
    const auto respawn = MakeRecord(9, &moved, Quantize(input));
    CHECK(respawn && respawn->spawn && respawn->worldId == 0x8F00000087654321ull);
    EntityState teleported = moved;
    teleported.pos[1] += 40000;
    const auto jump = MakeRecord(9, &moved, teleported);
    CHECK(jump && jump->pos && !jump->posDelta);
    EntityRecord quatOnWalker;
    quatOnWalker.netId = 9;
    quatOnWalker.quat = 1;
    CHECK(ApplyRecord(&moved, quatOnWalker, applied) == Status::BadRecord);
    CHECK(ApplyRecord(nullptr, *delta, applied) == Status::BadRecord);
    EntityView view;
    view.Set(9, placed);
    EntityView rebound;
    rebound.Set(9, Quantize(input));
    CHECK(ViewHash(view) != ViewHash(rebound));
    CHECK(ViewHash(EntityView()) == "e3b0c44298fc1c14");
    EntityInput car;
    car.kind = static_cast<uint8_t>(coopv2::EntityKind::Vehicle);
    car.quat = {0.0, 0.0, 0.38, 0.92};
    CHECK(Quantize(car).rot == 3758621460u && Quantize(car).UsesQuat());
}
} // namespace

int main()
{
    TestHashes();
    TestQuantizers();
    TestText();
    TestPacketHeader();
    TestHandshake();
    TestFraming();
    TestEveryTypeRoundTrips();
    TestValidation();
    TestScriptMessages();
    TestEntitySnapshots();
    TestDeltaRecords();
    TestDeltaLink();
    std::printf("%d checks, %d failures\n", g_checks, g_failures);
    return g_failures == 0 ? EXIT_SUCCESS : EXIT_FAILURE;
}
