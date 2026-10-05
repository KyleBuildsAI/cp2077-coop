#pragma once

// Protocol v2 (magic 0xCB77, major 2, minor 1) wire codec: packet header, cookie handshake bodies,
// message framing inside DATA packets and every message body, including delta entity snapshots
// with the X_WORLD_ID extension and SCRIPT_MSG.
//
// Port of relay/coopnet/proto.py, which is the single source of truth. The packed structs come
// from coop_proto_v2.h (a verbatim copy of relay/include/coop_proto_v2.h; tools/v2_golden.py checks
// they are identical, and relay/tools/check_c_header.py checks that header against proto.py).
// Every decoder accepts exactly what proto.py accepts: tools/v2_golden.py feeds the same valid and
// invalid datagrams to both and compares the verdicts and the decoded values.
//
// Rules shared by every function here:
// * Decoders never read outside the span they are given and never throw. Anything malformed,
//   truncated, oversized, non-finite, outside the world bounds or outside an enum returns a
//   Status other than Ok, and the output is unspecified.
// * Encoders validate what they wrote with the matching decoder (as proto.py does), so they never
//   emit bytes a receiver would reject. On failure the output buffer is left empty.
// * Text fields are UTF-8 std::string bytes.

#include "v2/coop_proto_v2.h"

#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <variant>
#include <vector>

namespace coopnet::v2
{
using Bytes = std::vector<uint8_t>;
using ByteSpan = std::span<const uint8_t>;

inline constexpr size_t kPacketHeaderSize = sizeof(coopv2::PacketHeader);   // 20
inline constexpr size_t kMessageHeaderSize = sizeof(coopv2::MessageHeader); // 4
inline constexpr size_t kReliableSeqSize = 2;
inline constexpr size_t kMaxMessageBody = coopv2::kMaxPacket - kPacketHeaderSize - kMessageHeaderSize - kReliableSeqSize;
inline constexpr size_t kMaxMessagesPerPacket = 96;
inline constexpr size_t kCookieSize = 16;
inline constexpr size_t kKeyHashSize = 16;
inline constexpr uint32_t kCookieMaxAgeS = 10;
inline constexpr size_t kMaxNameBytes = 24;
inline constexpr size_t kMaxRoomBytes = 32;
inline constexpr size_t kMaxChatBytes = 200;
inline constexpr size_t kMaxModListTextBytes = 255;
inline constexpr size_t kMaxRejectTextBytes = 64;
inline constexpr size_t kMaxEntityRecords = 255;
inline constexpr uint8_t kMoveStateCount = 14;
inline constexpr uint8_t kPacketTypeCount = 7;

enum class Status : uint8_t
{
    Ok,
    TooLarge,           // datagram above kMaxPacket, or a message body above kMaxMessageBody
    NotV2,              // shorter than 4 bytes or not starting with the magic
    VersionMismatch,    // v2 framing with another major (DecodedPacket::major / type are filled in)
    Truncated,          // ends inside a fixed part, a length prefix or a counted field
    BadSize,            // a body with an exact size has another one
    TrailingBytes,      // bytes left after the last field
    UnknownPacketType,  // packet type outside 1..7
    UnknownMessageType, // message type with no codec
    TooManyMessages,    // more than kMaxMessagesPerPacket in one DATA packet
    BadText,            // text over its limit, invalid UTF-8 or a forbidden character
    BadRoom,            // room is not 1..32 of A-Z a-z 0-9 _ -
    BadPadding,         // HELLO below 240 bytes or with non-zero padding
    NonFinite,          // NaN or infinity in a float field
    OutOfWorld,         // |x|,|y| > 20 km or |z| > 5 km
    BadValue,           // an enum, range or cross-field rule failed
    BadRecord,          // an entity record breaks a mask rule
    DuplicateNetId,     // two records for one net_id in one snapshot
    TooManyRecords,     // more than kMaxEntityRecords records
    BadBaseline,        // baseline not older than tick
};

const char* ToString(Status aStatus);

// ---- quantizers (proto.py rounding: round half to even, then clamp) ------------------------------

int32_t MetersToMm(double aMeters);
double MmToMeters(int32_t aMillimeters);
int16_t VelocityToCms(double aMetersPerSecond);
double CmsToVelocity(int16_t aCentimetersPerSecond);
uint16_t YawToU16(double aDegrees);
double U16ToYaw(uint16_t aValue);
int16_t PitchToI16(double aDegrees);
double I16ToPitch(int16_t aValue);
// Smallest-three quaternion: 2-bit index of the dropped component + 3 x 10 bits.
uint32_t PackQuat(double aX, double aY, double aZ, double aW);
std::array<double, 4> UnpackQuat(uint32_t aPacked);

// ---- text ----------------------------------------------------------------------------------------

enum class TextRule : uint8_t
{
    Strict, // names, chat, mod list: no C0/C1 controls, DEL, U+2028 or U+2029
    Script, // SCRIPT_MSG: anything but NUL
};

// Valid UTF-8 (no overlongs, surrogates or code points above U+10FFFF) that the rule allows.
bool IsValidText(std::string_view aText, TextRule aRule);
bool IsValidRoom(std::string_view aRoom);

// ---- packets -------------------------------------------------------------------------------------

struct PacketInfo
{
    uint8_t type = static_cast<uint8_t>(coopv2::PacketType::Data);
    uint64_t token = 0;
    uint16_t seq = 0;
    uint16_t ack = 0;
    uint32_t ackBits = 0;
};

struct DecodedPacket
{
    uint8_t major = coopv2::kProtoMajor;
    PacketInfo header;
    ByteSpan body; // points into the datagram
};

bool IsV2(ByteSpan aDatagram);
Status DecodePacket(ByteSpan aDatagram, DecodedPacket& aOut);
// Writes header + body (major is always kProtoMajor). TooLarge above kMaxPacket.
Status EncodePacket(const PacketInfo& aHeader, ByteSpan aBody, Bytes& aOut);

// ---- handshake -----------------------------------------------------------------------------------

struct JoinInfo
{
    coopv2::JoinFixed fixed{};
    std::string room;
    std::string name;
};

struct AuthInfo
{
    JoinInfo join;
    std::array<uint8_t, kCookieSize> cookie{};
    std::array<uint8_t, kKeyHashSize> keyHash{};
};

struct RejectInfo
{
    uint8_t reason = 0;
    uint8_t minorMin = coopv2::kMinSupportedMinor;
    uint8_t minorMax = coopv2::kProtoMinor;
    std::string text; // raw bytes; proto.py decodes them leniently (invalid UTF-8 is replaced)
};

// Join info inside a HELLO or AUTH body, from aOffset; aOffset ends after the name.
Status DecodeJoin(ByteSpan aBody, size_t& aOffset, JoinInfo& aOut);
Status EncodeJoin(const JoinInfo& aJoin, Bytes& aOut); // appends

// Bodies (DecodedPacket::body) in; complete datagrams out.
Status DecodeHello(ByteSpan aBody, JoinInfo& aOut, size_t* aPaddingBytes = nullptr);
Status EncodeHello(const JoinInfo& aJoin, Bytes& aOut); // zero-padded to kHelloMinPacket
Status DecodeChallenge(ByteSpan aBody, coopv2::Challenge& aOut);
Status EncodeChallenge(const coopv2::Challenge& aChallenge, Bytes& aOut);
Status DecodeAuth(ByteSpan aBody, AuthInfo& aOut);
Status EncodeAuth(const AuthInfo& aAuth, Bytes& aOut);
Status DecodeWelcome(ByteSpan aBody, coopv2::Welcome& aOut);
Status EncodeWelcome(const coopv2::Welcome& aWelcome, Bytes& aOut); // the packet token is welcome.token
Status DecodeReject(ByteSpan aBody, RejectInfo& aOut);
Status EncodeReject(const RejectInfo& aReject, Bytes& aOut); // text cut to kMaxRejectTextBytes
Status DecodeDisconnect(ByteSpan aBody, uint8_t& aReason);
Status EncodeDisconnect(uint64_t aToken, uint8_t aReason, Bytes& aOut);

// ---- message framing inside DATA -----------------------------------------------------------------

struct MessageView
{
    uint8_t type = 0; // without the reliable bit
    uint8_t peer = 0;
    bool reliable = false;
    uint16_t relSeq = 0;
    ByteSpan body; // points into the payload
};

Status DecodeMessages(ByteSpan aPayload, std::vector<MessageView>& aOut);
// Appends one message. aType must be below 0x80; the body at most kMaxMessageBody.
Status AppendMessage(Bytes& aPayload, uint8_t aType, uint8_t aPeer, std::optional<uint16_t> aRelSeq, ByteSpan aBody);
size_t MessageSize(size_t aBodyBytes, bool aReliable);

// ---- message bodies ------------------------------------------------------------------------------

struct PeerJoinedMsg
{
    coopv2::PeerJoined fixed{};
    std::string name;
};

struct PlayerSnapshotMsg
{
    coopv2::PlayerSnapshot base{};
    std::optional<coopv2::VehicleBlock> vehicle; // written only when base.flags has kPlayerDriving
};

struct ChatMsg
{
    uint8_t channel = 0;
    std::string text;
};

struct ModListMsg
{
    uint8_t chunk = 0;
    uint8_t chunks = 0;
    std::string text;
};

struct ScriptMsg
{
    uint8_t channel = 0; // 1..15 unreliable, 16..31 reliable
    uint8_t flags = 0;   // relayed unchanged
    std::string text;
};

struct EntitySpawnInfo
{
    uint8_t kind = 0;
    uint8_t spawnFlags = 0;
    uint8_t attitude = 0;
    uint64_t record = 0;     // TweakDBID
    uint64_t appearance = 0; // CName hash
    bool operator==(const EntitySpawnInfo&) const = default;
};

// One entity record. An absent field means "unchanged since the baseline".
struct EntityRecord
{
    uint16_t netId = 0;
    bool remove = false; // alone: the entity left the snapshot
    std::optional<EntitySpawnInfo> spawn;
    std::optional<std::array<int32_t, 3>> pos;      // mm
    std::optional<std::array<int16_t, 3>> posDelta; // mm relative to the baseline position
    std::optional<uint16_t> yaw;
    std::optional<uint32_t> quat;
    std::optional<std::array<int16_t, 3>> vel;   // cm/s
    std::optional<std::array<uint8_t, 3>> state; // move_state, flags, health
    std::optional<uint16_t> target;
    std::optional<uint64_t> weapon;
    std::optional<uint64_t> worldId; // X_WORLD_ID (minor 1)
    bool operator==(const EntityRecord&) const = default;
};

struct EntitySnapshotMsg
{
    uint32_t tick = 0;
    uint32_t baseline = 0; // 0 = full snapshot
    uint32_t sampleTime = 0;
    std::vector<EntityRecord> records;
};

size_t EntityRecordSize(const EntityRecord& aRecord);

using Body = std::variant<coopv2::TimeReq, coopv2::TimeResp, PeerJoinedMsg, coopv2::PeerLeft, coopv2::LinkStats,
                          PlayerSnapshotMsg, EntitySnapshotMsg, coopv2::SnapshotAck, coopv2::FireFx, coopv2::Equip,
                          coopv2::VehicleEnter, coopv2::VehicleExit, coopv2::Hit, coopv2::Death, coopv2::TimeWeather,
                          ChatMsg, coopv2::TeleportReq, coopv2::TeleportResp, coopv2::WorldFact, ModListMsg,
                          coopv2::SessionConfig, ScriptMsg>;

uint8_t TypeOf(const Body& aBody);
Status DecodeBody(uint8_t aType, ByteSpan aBody, Body& aOut);
Status EncodeBody(const Body& aBody, Bytes& aOut); // replaces aOut

// ---- message table -------------------------------------------------------------------------------

enum class Delivery : uint8_t
{
    Unreliable,
    Reliable,
    ByChannel, // SCRIPT_MSG: reliable iff channel >= 16
};

enum class Sender : uint8_t
{
    Relay,
    Client,
    Host,
};

enum class Route : uint8_t
{
    Relay,     // consumed by the relay
    Broadcast, // every other room member
    Host,      // the room host
    Target,    // the peer named in the message header
    Hit,       // the host for entities, the victim for players
    Peer,      // the named peer, or everybody for kPeerBroadcast
};

struct MsgSpec
{
    uint8_t type;
    const char* name; // proto.py MsgType name
    Delivery delivery;
    Sender sender;
    Route route;
    uint8_t minMinor; // lowest negotiated minor that may send or receive the type
};

std::span<const MsgSpec> MessageSpecs();
const MsgSpec* FindSpec(uint8_t aType);
bool ScriptChannelReliable(uint8_t aChannel);
// Whether the reliable bit of a message header is allowed for this decoded body.
bool DeliveryOk(const MsgSpec& aSpec, bool aReliable, const Body& aBody);
// Lowest negotiated minor a peer needs to send or receive this decoded body.
uint8_t RequiredMinor(const Body& aBody);

// ---- convenience ---------------------------------------------------------------------------------

struct OutMessage
{
    uint8_t peer = coopv2::kPeerBroadcast;
    std::optional<uint16_t> relSeq; // set = reliable
    Body body;
};

// One DATA datagram with the given messages. TooLarge when they do not fit in kMaxPacket.
Status EncodeDataPacket(const PacketInfo& aHeader, std::span<const OutMessage> aMessages, Bytes& aOut);
} // namespace coopnet::v2
