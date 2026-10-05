#include "V2TestSupport.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <limits>
#include <set>

namespace coopnet::v2::test
{
namespace
{
ByteSpan AsSpan(const Bytes& aBytes)
{
    return {aBytes.data(), aBytes.size()};
}

void AppendUtf8(std::string& aText, uint32_t aCodePoint)
{
    if (aCodePoint < 0x80)
    {
        aText += static_cast<char>(aCodePoint);
    }
    else if (aCodePoint < 0x800)
    {
        aText += static_cast<char>(0xC0 | (aCodePoint >> 6));
        aText += static_cast<char>(0x80 | (aCodePoint & 0x3F));
    }
    else if (aCodePoint < 0x10000)
    {
        aText += static_cast<char>(0xE0 | (aCodePoint >> 12));
        aText += static_cast<char>(0x80 | ((aCodePoint >> 6) & 0x3F));
        aText += static_cast<char>(0x80 | (aCodePoint & 0x3F));
    }
    else
    {
        aText += static_cast<char>(0xF0 | (aCodePoint >> 18));
        aText += static_cast<char>(0x80 | ((aCodePoint >> 12) & 0x3F));
        aText += static_cast<char>(0x80 | ((aCodePoint >> 6) & 0x3F));
        aText += static_cast<char>(0x80 | (aCodePoint & 0x3F));
    }
}

uint32_t RandomCodePoint(Rng& aRng, TextRule aRule)
{
    const uint64_t pick = aRng.Below(100);
    if (aRule == TextRule::Script && pick < 10)
    {
        static constexpr uint32_t kControls[] = {0x01, 0x09, 0x0A, 0x0D, 0x1B, 0x1F, 0x7F, 0x85, 0x9F, 0x2028, 0x2029};
        return kControls[aRng.Below(std::size(kControls))];
    }
    if (pick < 70)
    {
        return static_cast<uint32_t>(aRng.Range(0x20, 0x7E));
    }
    if (pick < 82)
    {
        return static_cast<uint32_t>(aRng.Range(0x410, 0x44F)); // Cyrillic, 2 bytes
    }
    if (pick < 92)
    {
        return static_cast<uint32_t>(aRng.Range(0x4E00, 0x4EFF)); // CJK, 3 bytes
    }
    return static_cast<uint32_t>(aRng.Range(0x1F600, 0x1F64F)); // emoji, 4 bytes
}

std::array<int32_t, 3> RandomPosMm(Rng& aRng)
{
    const auto axis = [&aRng](int64_t aLimit) -> int32_t {
        if (aRng.Chance(0.05))
        {
            return static_cast<int32_t>(aRng.Chance(0.5) ? aLimit : -aLimit);
        }
        return static_cast<int32_t>(aRng.Range(-aLimit, aLimit));
    };
    return {axis(20000000), axis(20000000), axis(5000000)};
}

template<class T>
T RandomInt(Rng& aRng)
{
    return static_cast<T>(aRng.U64());
}

std::array<int16_t, 3> RandomI16x3(Rng& aRng)
{
    return {RandomInt<int16_t>(aRng), RandomInt<int16_t>(aRng), RandomInt<int16_t>(aRng)};
}

coopv2::VehicleBlock RandomVehicleBlock(Rng& aRng)
{
    coopv2::VehicleBlock block{};
    block.vehicle_net = static_cast<uint16_t>(aRng.Range(1, 0xFFFF));
    block.px = aRng.Coordinate(coopv2::kWorldXYLimit);
    block.py = aRng.Coordinate(coopv2::kWorldXYLimit);
    block.pz = aRng.Coordinate(coopv2::kWorldZLimit);
    block.quat = aRng.U32();
    block.lvx = RandomInt<int16_t>(aRng);
    block.lvy = RandomInt<int16_t>(aRng);
    block.lvz = RandomInt<int16_t>(aRng);
    block.avx = RandomInt<int16_t>(aRng);
    block.avy = RandomInt<int16_t>(aRng);
    block.avz = RandomInt<int16_t>(aRng);
    block.steer = static_cast<int8_t>(aRng.Range(-100, 100));
    block.throttle = static_cast<uint8_t>(aRng.Range(0, 100));
    block.brake = static_cast<uint8_t>(aRng.Range(0, 100));
    block.vflags = RandomInt<uint8_t>(aRng);
    return block;
}

EntitySnapshotMsg RandomEntitySnapshot(Rng& aRng, size_t aMaxBytes)
{
    EntitySnapshotMsg snapshot;
    snapshot.tick = static_cast<uint32_t>(aRng.Range(1, 0xFFFFFFFFll));
    snapshot.baseline = snapshot.tick > 1 && aRng.Chance(0.6) ? static_cast<uint32_t>(aRng.Range(1, snapshot.tick - 1)) : 0;
    snapshot.sampleTime = aRng.U32();
    const bool many = aRng.Chance(0.1);
    const size_t wanted = many ? static_cast<size_t>(aRng.Range(100, 255)) : static_cast<size_t>(aRng.Below(30));
    size_t used = sizeof(coopv2::EntitySnapshotHeader);
    std::set<uint16_t> ids;
    for (size_t attempt = 0; snapshot.records.size() < wanted && attempt < wanted * 3; ++attempt)
    {
        const uint16_t netId = many ? static_cast<uint16_t>(snapshot.records.size() + 1)
                                    : static_cast<uint16_t>(aRng.Range(1, 0xFFFF));
        if (!ids.insert(netId).second)
        {
            continue;
        }
        EntityRecord record;
        if (many)
        {
            record.netId = netId;
            record.yaw = static_cast<uint16_t>(aRng.U32());
        }
        else
        {
            record = RandomRecord(aRng, netId);
        }
        const size_t size = EntityRecordSize(record);
        if (used + size > aMaxBytes)
        {
            break;
        }
        used += size;
        snapshot.records.push_back(record);
    }
    return snapshot;
}

Bytes HandshakeDatagram(Rng& aRng, uint64_t aPick)
{
    Bytes out;
    if (aPick < 8)
    {
        EncodeHello(RandomJoin(aRng), out);
    }
    else if (aPick < 12)
    {
        AuthInfo auth;
        auth.join = RandomJoin(aRng);
        for (auto& byte : auth.cookie)
        {
            byte = RandomInt<uint8_t>(aRng);
        }
        for (auto& byte : auth.keyHash)
        {
            byte = RandomInt<uint8_t>(aRng);
        }
        EncodeAuth(auth, out);
    }
    else if (aPick < 16)
    {
        coopv2::Challenge challenge{};
        challenge.minor_min = RandomInt<uint8_t>(aRng);
        challenge.minor_max = RandomInt<uint8_t>(aRng);
        challenge.reserved = RandomInt<uint16_t>(aRng);
        for (auto& byte : challenge.cookie)
        {
            byte = RandomInt<uint8_t>(aRng);
        }
        EncodeChallenge(challenge, out);
    }
    else if (aPick < 20)
    {
        coopv2::Welcome welcome{};
        welcome.minor = RandomInt<uint8_t>(aRng);
        welcome.peer_id = RandomInt<uint8_t>(aRng);
        welcome.role = RandomInt<uint8_t>(aRng);
        welcome.room_flags = RandomInt<uint8_t>(aRng);
        welcome.token = aRng.U64();
        welcome.relay_time_ms = aRng.U32();
        welcome.player_hz = RandomInt<uint8_t>(aRng);
        welcome.entity_hz = RandomInt<uint8_t>(aRng);
        welcome.max_packet = RandomInt<uint16_t>(aRng);
        welcome.room_caps = aRng.U32();
        EncodeWelcome(welcome, out);
    }
    else if (aPick < 24)
    {
        RejectInfo reject;
        reject.reason = RandomInt<uint8_t>(aRng);
        reject.minorMin = RandomInt<uint8_t>(aRng);
        reject.minorMax = RandomInt<uint8_t>(aRng);
        reject.text = RandomText(aRng, kMaxRejectTextBytes, TextRule::Strict);
        EncodeReject(reject, out);
    }
    else
    {
        EncodeDisconnect(aRng.U64(), RandomInt<uint8_t>(aRng), out);
    }
    return out;
}
} // namespace

uint64_t Rng::U64()
{
    m_state += 0x9E3779B97F4A7C15ull;
    uint64_t value = m_state;
    value = (value ^ (value >> 30)) * 0xBF58476D1CE4E5B9ull;
    value = (value ^ (value >> 27)) * 0x94D049BB133111EBull;
    return value ^ (value >> 31);
}

uint64_t Rng::Below(uint64_t aBound)
{
    return aBound == 0 ? 0 : U64() % aBound;
}

int64_t Rng::Range(int64_t aLow, int64_t aHigh)
{
    const uint64_t span = static_cast<uint64_t>(aHigh) - static_cast<uint64_t>(aLow) + 1;
    const uint64_t offset = span == 0 ? U64() : Below(span);
    return static_cast<int64_t>(static_cast<uint64_t>(aLow) + offset);
}

bool Rng::Chance(double aProbability)
{
    return Unit() < aProbability;
}

double Rng::Unit()
{
    return static_cast<double>(U64() >> 11) * (1.0 / 9007199254740992.0);
}

float Rng::Coordinate(float aLimit)
{
    if (Chance(0.1))
    {
        const float edges[] = {0.0f, -0.0f, aLimit, -aLimit, 1.0e-40f, -1.0e-40f, std::nextafter(aLimit, 0.0f)};
        return edges[Below(std::size(edges))];
    }
    const float value = static_cast<float>((Unit() * 2.0 - 1.0) * static_cast<double>(aLimit));
    return std::clamp(value, -aLimit, aLimit);
}

std::string RandomText(Rng& aRng, size_t aMaxBytes, TextRule aRule)
{
    const size_t target = static_cast<size_t>(aRng.Below(aMaxBytes + 1));
    std::string text;
    for (int attempt = 0; attempt < 4000 && text.size() < target; ++attempt)
    {
        std::string piece;
        AppendUtf8(piece, RandomCodePoint(aRng, aRule));
        if (text.size() + piece.size() > aMaxBytes)
        {
            continue;
        }
        if (aRule == TextRule::Strict && !IsValidText(piece, TextRule::Strict))
        {
            continue;
        }
        text += piece;
    }
    return text;
}

std::string RandomRoom(Rng& aRng)
{
    static constexpr char kChars[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-";
    const size_t length = static_cast<size_t>(aRng.Range(1, 32));
    std::string room;
    for (size_t index = 0; index < length; ++index)
    {
        room += kChars[aRng.Below(sizeof(kChars) - 1)];
    }
    return room;
}

JoinInfo RandomJoin(Rng& aRng)
{
    JoinInfo join;
    auto& fixed = join.fixed;
    fixed.minor = RandomInt<uint8_t>(aRng);
    fixed.role = static_cast<uint8_t>(aRng.Range(0, 3));
    fixed.join_flags = RandomInt<uint16_t>(aRng);
    fixed.caps = aRng.U32();
    fixed.game_build = aRng.U32();
    fixed.mod_major = RandomInt<uint16_t>(aRng);
    fixed.mod_minor = RandomInt<uint16_t>(aRng);
    fixed.mod_patch = RandomInt<uint16_t>(aRng);
    fixed.mod_hash = aRng.U64();
    fixed.mod_count = RandomInt<uint16_t>(aRng);
    fixed.client_nonce = aRng.U64();
    fixed.resume_token = aRng.Chance(0.5) ? 0 : aRng.U64();
    join.room = RandomRoom(aRng);
    join.name = RandomText(aRng, kMaxNameBytes, TextRule::Strict);
    return join;
}

EntityRecord RandomRecord(Rng& aRng, uint16_t aNetId)
{
    EntityRecord record;
    record.netId = aNetId;
    if (aRng.Chance(0.08))
    {
        record.remove = true;
        return record;
    }
    if (aRng.Chance(0.25))
    {
        record.spawn = EntitySpawnInfo{static_cast<uint8_t>(aRng.Range(1, 5)), RandomInt<uint8_t>(aRng),
                                       RandomInt<uint8_t>(aRng), aRng.U64(), aRng.U64()};
        record.pos = RandomPosMm(aRng);
        if (aRng.Chance(0.5))
        {
            record.yaw = RandomInt<uint16_t>(aRng);
        }
        else
        {
            record.quat = aRng.U32();
        }
    }
    else
    {
        const uint64_t position = aRng.Below(3);
        if (position == 1)
        {
            record.pos = RandomPosMm(aRng);
        }
        else if (position == 2)
        {
            record.posDelta = RandomI16x3(aRng);
        }
        const uint64_t rotation = aRng.Below(3);
        if (rotation == 1)
        {
            record.yaw = RandomInt<uint16_t>(aRng);
        }
        else if (rotation == 2)
        {
            record.quat = aRng.U32();
        }
    }
    if (aRng.Chance(0.4))
    {
        record.vel = RandomI16x3(aRng);
    }
    if (aRng.Chance(0.3))
    {
        record.state = std::array<uint8_t, 3>{static_cast<uint8_t>(aRng.Below(kMoveStateCount)),
                                              RandomInt<uint8_t>(aRng), RandomInt<uint8_t>(aRng)};
    }
    if (aRng.Chance(0.15))
    {
        record.target = RandomInt<uint16_t>(aRng);
    }
    if (aRng.Chance(0.1))
    {
        record.weapon = aRng.U64();
    }
    if (aRng.Chance(0.15))
    {
        record.worldId = aRng.U64();
    }
    const bool empty = !record.spawn && !record.pos && !record.posDelta && !record.yaw && !record.quat && !record.vel &&
                       !record.state && !record.target && !record.weapon && !record.worldId;
    if (empty)
    {
        record.yaw = RandomInt<uint16_t>(aRng);
    }
    return record;
}

uint8_t RandomMessageType(Rng& aRng)
{
    const auto specs = MessageSpecs();
    return specs[aRng.Below(specs.size())].type;
}

Body RandomBody(Rng& aRng, uint8_t aType, size_t aMaxBytes)
{
    using coopv2::MsgType;
    switch (static_cast<MsgType>(aType))
    {
    case MsgType::TimeReq: return coopv2::TimeReq{aRng.U32()};
    case MsgType::TimeResp: return coopv2::TimeResp{aRng.U32(), aRng.U32(), aRng.U32()};
    case MsgType::PeerJoined:
    {
        PeerJoinedMsg value;
        value.fixed = coopv2::PeerJoined{RandomInt<uint8_t>(aRng), static_cast<uint8_t>(aRng.Range(0, 3)),
                                         RandomInt<uint8_t>(aRng), RandomInt<uint8_t>(aRng), aRng.U32(), aRng.U64(),
                                         RandomInt<uint16_t>(aRng), RandomInt<uint16_t>(aRng),
                                         RandomInt<uint16_t>(aRng), RandomInt<uint16_t>(aRng)};
        value.name = RandomText(aRng, kMaxNameBytes, TextRule::Strict);
        return value;
    }
    case MsgType::PeerLeft: return coopv2::PeerLeft{RandomInt<uint8_t>(aRng), RandomInt<uint8_t>(aRng)};
    case MsgType::LinkStats:
        return coopv2::LinkStats{RandomInt<uint8_t>(aRng), RandomInt<uint8_t>(aRng), RandomInt<uint16_t>(aRng),
                                 RandomInt<uint16_t>(aRng), RandomInt<uint16_t>(aRng)};
    case MsgType::PlayerSnapshot:
    {
        PlayerSnapshotMsg value;
        auto& base = value.base;
        base.snap_seq = RandomInt<uint16_t>(aRng);
        base.sample_time = aRng.U32();
        base.x = aRng.Coordinate(coopv2::kWorldXYLimit);
        base.y = aRng.Coordinate(coopv2::kWorldXYLimit);
        base.z = aRng.Coordinate(coopv2::kWorldZLimit);
        base.yaw = RandomInt<uint16_t>(aRng);
        base.pitch = static_cast<int16_t>(aRng.Range(-9000, 9000));
        base.vx = RandomInt<int16_t>(aRng);
        base.vy = RandomInt<int16_t>(aRng);
        base.vz = RandomInt<int16_t>(aRng);
        base.move_state = static_cast<uint8_t>(aRng.Below(kMoveStateCount));
        base.health = RandomInt<uint8_t>(aRng);
        base.flags = static_cast<uint16_t>(RandomInt<uint16_t>(aRng) & ~coopv2::kPlayerDriving);
        const size_t drivingSize = sizeof(coopv2::PlayerSnapshot) + sizeof(coopv2::VehicleBlock);
        if (aMaxBytes >= drivingSize && aRng.Chance(0.4))
        {
            base.flags = static_cast<uint16_t>(base.flags | coopv2::kPlayerDriving);
            value.vehicle = RandomVehicleBlock(aRng);
        }
        return value;
    }
    case MsgType::EntitySnapshot: return RandomEntitySnapshot(aRng, std::min(aMaxBytes, kMaxMessageBody));
    case MsgType::SnapshotAck: return coopv2::SnapshotAck{aRng.U32()};
    case MsgType::FireFx:
        return coopv2::FireFx{aRng.U32(), RandomInt<int32_t>(aRng), RandomInt<int32_t>(aRng), RandomInt<int32_t>(aRng),
                              RandomInt<int16_t>(aRng), RandomInt<int16_t>(aRng), RandomInt<int16_t>(aRng),
                              RandomInt<uint8_t>(aRng), RandomInt<uint8_t>(aRng)};
    case MsgType::Equip:
        return coopv2::Equip{static_cast<uint8_t>(aRng.Below(8)), static_cast<uint8_t>(aRng.Below(16)),
                             RandomInt<uint16_t>(aRng), aRng.U64(), aRng.U64(), RandomInt<uint16_t>(aRng)};
    case MsgType::VehicleEnter:
        return coopv2::VehicleEnter{RandomInt<uint16_t>(aRng), RandomInt<uint8_t>(aRng), RandomInt<uint8_t>(aRng),
                                    aRng.U64(), aRng.U64(), aRng.Coordinate(coopv2::kWorldXYLimit),
                                    aRng.Coordinate(coopv2::kWorldXYLimit), aRng.Coordinate(coopv2::kWorldZLimit),
                                    aRng.U32()};
    case MsgType::VehicleExit:
        return coopv2::VehicleExit{RandomInt<uint16_t>(aRng), RandomInt<uint8_t>(aRng), RandomInt<uint8_t>(aRng),
                                   aRng.Coordinate(coopv2::kWorldXYLimit), aRng.Coordinate(coopv2::kWorldXYLimit),
                                   aRng.Coordinate(coopv2::kWorldZLimit), RandomInt<uint16_t>(aRng)};
    case MsgType::Hit:
    {
        const float damages[] = {0.0f, -0.0f, 1.0e6f, 33.5f, static_cast<float>(aRng.Unit() * 1.0e6)};
        return coopv2::Hit{static_cast<uint16_t>(aRng.Range(1, 0xFFFF)), static_cast<uint8_t>(aRng.Below(2)),
                           static_cast<uint8_t>(aRng.Below(16)), RandomInt<uint8_t>(aRng), RandomInt<uint8_t>(aRng),
                           damages[aRng.Below(std::size(damages))], aRng.U64(), RandomInt<int16_t>(aRng),
                           RandomInt<int16_t>(aRng), RandomInt<int16_t>(aRng), aRng.U32()};
    }
    case MsgType::Death:
        return coopv2::Death{RandomInt<uint16_t>(aRng), static_cast<uint8_t>(aRng.Below(2)), RandomInt<uint8_t>(aRng),
                             RandomInt<uint16_t>(aRng), static_cast<uint8_t>(aRng.Below(3)), RandomInt<uint8_t>(aRng),
                             aRng.U32()};
    case MsgType::TimeWeather:
        return coopv2::TimeWeather{aRng.U32(), static_cast<uint16_t>(aRng.Range(0, 10000)), RandomInt<uint8_t>(aRng),
                                   static_cast<uint8_t>(aRng.Below(32)), aRng.U64(), RandomInt<uint16_t>(aRng)};
    case MsgType::Chat:
        return ChatMsg{RandomInt<uint8_t>(aRng),
                       RandomText(aRng, std::min(kMaxChatBytes, aMaxBytes > 2 ? aMaxBytes - 2 : 0), TextRule::Strict)};
    case MsgType::TeleportReq:
        return coopv2::TeleportReq{RandomInt<uint16_t>(aRng), static_cast<uint8_t>(aRng.Below(2)), RandomInt<uint8_t>(aRng)};
    case MsgType::TeleportResp:
        return coopv2::TeleportResp{RandomInt<uint16_t>(aRng), RandomInt<uint8_t>(aRng), RandomInt<uint8_t>(aRng),
                                    aRng.Coordinate(coopv2::kWorldXYLimit), aRng.Coordinate(coopv2::kWorldXYLimit),
                                    aRng.Coordinate(coopv2::kWorldZLimit), RandomInt<uint16_t>(aRng)};
    case MsgType::WorldFact: return coopv2::WorldFact{aRng.U64(), RandomInt<int32_t>(aRng)};
    case MsgType::ModList:
    {
        ModListMsg value;
        value.chunks = static_cast<uint8_t>(aRng.Range(1, 16));
        value.chunk = static_cast<uint8_t>(aRng.Below(value.chunks));
        value.text = RandomText(aRng, std::min(kMaxModListTextBytes, aMaxBytes > 3 ? aMaxBytes - 3 : 0), TextRule::Strict);
        return value;
    }
    case MsgType::SessionConfig:
        return coopv2::SessionConfig{static_cast<uint16_t>(aRng.Range(10, 500)), static_cast<uint16_t>(aRng.Range(10, 1000)),
                                     static_cast<uint8_t>(aRng.Range(1, 30)), RandomInt<uint8_t>(aRng),
                                     RandomInt<uint16_t>(aRng)};
    case MsgType::ScriptMsg:
    {
        ScriptMsg value;
        value.channel = static_cast<uint8_t>(aRng.Range(coopv2::kScriptFirstChannel, coopv2::kScriptLastChannel));
        value.flags = aRng.Chance(0.8) ? 0 : RandomInt<uint8_t>(aRng);
        const size_t limit = std::min<size_t>(coopv2::kMaxScriptBytes, aMaxBytes > 4 ? aMaxBytes - 4 : 0);
        value.text = aRng.Chance(0.05) ? std::string(limit, 'x') : RandomText(aRng, limit, TextRule::Script);
        return value;
    }
    }
    return coopv2::TimeReq{0};
}

Bytes RandomDatagram(Rng& aRng)
{
    const uint64_t pick = aRng.Below(100);
    if (pick < 28)
    {
        return HandshakeDatagram(aRng, pick);
    }
    PacketInfo header;
    header.token = aRng.U64();
    header.seq = RandomInt<uint16_t>(aRng);
    header.ack = RandomInt<uint16_t>(aRng);
    header.ackBits = aRng.U32();
    const bool many = aRng.Chance(0.04);
    const size_t wanted = many ? kMaxMessagesPerPacket : static_cast<size_t>(aRng.Below(7));
    Bytes payload;
    Bytes body;
    for (size_t index = 0; index < wanted; ++index)
    {
        const uint8_t type = many ? static_cast<uint8_t>(coopv2::MsgType::SnapshotAck) : RandomMessageType(aRng);
        const MsgSpec* spec = FindSpec(type);
        const size_t room = coopv2::kMaxPacket - kPacketHeaderSize - payload.size();
        if (room <= kMessageHeaderSize + kReliableSeqSize)
        {
            break;
        }
        const Body value = RandomBody(aRng, type, room - kMessageHeaderSize - kReliableSeqSize);
        if (EncodeBody(value, body) != Status::Ok)
        {
            continue;
        }
        bool reliable = spec->delivery == Delivery::Reliable;
        if (spec->delivery == Delivery::ByChannel)
        {
            reliable = ScriptChannelReliable(std::get<ScriptMsg>(value).channel);
        }
        if (aRng.Chance(0.05))
        {
            reliable = !reliable; // the codec accepts it; the description reports delivery=bad
        }
        if (MessageSize(body.size(), reliable) > room)
        {
            break;
        }
        const std::optional<uint16_t> relSeq = reliable ? std::optional<uint16_t>(RandomInt<uint16_t>(aRng)) : std::nullopt;
        AppendMessage(payload, type, RandomInt<uint8_t>(aRng), relSeq, AsSpan(body));
    }
    Bytes out;
    EncodePacket(header, AsSpan(payload), out);
    return out;
}

Bytes Mutate(Rng& aRng, ByteSpan aDatagram)
{
    Bytes data(aDatagram.begin(), aDatagram.end());
    const int edits = static_cast<int>(aRng.Range(1, 4));
    for (int edit = 0; edit < edits; ++edit)
    {
        const uint64_t op = aRng.Below(10);
        if (data.empty() && op < 8)
        {
            data.push_back(RandomInt<uint8_t>(aRng));
            continue;
        }
        const size_t at = static_cast<size_t>(aRng.Below(data.size()));
        switch (op)
        {
        case 0: data[at] = static_cast<uint8_t>(data[at] ^ (1u << aRng.Below(8))); break;
        case 1: data[at] = RandomInt<uint8_t>(aRng); break;
        case 2: data.resize(at); break;
        case 3:
            for (int count = static_cast<int>(aRng.Range(1, 16)); count > 0; --count)
            {
                data.push_back(RandomInt<uint8_t>(aRng));
            }
            break;
        case 4: data.insert(data.begin() + static_cast<std::ptrdiff_t>(at), static_cast<size_t>(aRng.Range(1, 8)), RandomInt<uint8_t>(aRng)); break;
        case 5:
        {
            const size_t count = std::min(data.size() - at, static_cast<size_t>(aRng.Range(1, 16)));
            data.erase(data.begin() + static_cast<std::ptrdiff_t>(at), data.begin() + static_cast<std::ptrdiff_t>(at + count));
            break;
        }
        case 6:
        {
            static constexpr uint8_t kInteresting[] = {0x00, 0x01, 0x7F, 0x80, 0xFE, 0xFF};
            data[at] = kInteresting[aRng.Below(std::size(kInteresting))];
            break;
        }
        case 7:
            if (data.size() >= kPacketHeaderSize + kMessageHeaderSize)
            {
                // the first message header: type, peer, length
                const size_t field = kPacketHeaderSize + static_cast<size_t>(aRng.Below(4));
                data[field] = RandomInt<uint8_t>(aRng);
            }
            break;
        case 8:
            if (data.size() >= 4)
            {
                static constexpr uint32_t kFloats[] = {0x7FC00000u, 0x7F800000u, 0xFF800000u, 0x46A00000u, 0x469C4000u};
                const uint32_t bits = kFloats[aRng.Below(std::size(kFloats))];
                const size_t slot = static_cast<size_t>(aRng.Below(data.size() - 3));
                std::memcpy(data.data() + slot, &bits, sizeof(bits));
            }
            break;
        default:
            if (data.size() > 4)
            {
                data[2] = static_cast<uint8_t>(aRng.Chance(0.5) ? 2 : aRng.Below(4)); // major
                data[3] = static_cast<uint8_t>(aRng.Below(9));                       // packet type
            }
            break;
        }
    }
    if (data.size() > 1400)
    {
        data.resize(1400);
    }
    return data;
}

Status Reencode(ByteSpan aDatagram, Bytes& aOut)
{
    DecodedPacket packet;
    if (const Status status = DecodePacket(aDatagram, packet); status != Status::Ok)
    {
        return status;
    }
    switch (static_cast<coopv2::PacketType>(packet.header.type))
    {
    case coopv2::PacketType::Hello:
    {
        JoinInfo join;
        const Status status = DecodeHello(packet.body, join);
        return status == Status::Ok ? EncodeHello(join, aOut) : status;
    }
    case coopv2::PacketType::Challenge:
    {
        coopv2::Challenge challenge{};
        const Status status = DecodeChallenge(packet.body, challenge);
        return status == Status::Ok ? EncodeChallenge(challenge, aOut) : status;
    }
    case coopv2::PacketType::Auth:
    {
        AuthInfo auth;
        const Status status = DecodeAuth(packet.body, auth);
        return status == Status::Ok ? EncodeAuth(auth, aOut) : status;
    }
    case coopv2::PacketType::Welcome:
    {
        coopv2::Welcome welcome{};
        const Status status = DecodeWelcome(packet.body, welcome);
        return status == Status::Ok ? EncodeWelcome(welcome, aOut) : status;
    }
    case coopv2::PacketType::Reject:
    {
        RejectInfo reject;
        const Status status = DecodeReject(packet.body, reject);
        return status == Status::Ok ? EncodeReject(reject, aOut) : status;
    }
    case coopv2::PacketType::Disconnect:
    {
        uint8_t reason = 0;
        const Status status = DecodeDisconnect(packet.body, reason);
        return status == Status::Ok ? EncodeDisconnect(packet.header.token, reason, aOut) : status;
    }
    case coopv2::PacketType::Data:
    {
        std::vector<MessageView> messages;
        if (const Status status = DecodeMessages(packet.body, messages); status != Status::Ok)
        {
            return status;
        }
        Bytes payload;
        Bytes body;
        for (const MessageView& message : messages)
        {
            Body value;
            if (const Status status = DecodeBody(message.type, message.body, value); status != Status::Ok)
            {
                return status;
            }
            if (const Status status = EncodeBody(value, body); status != Status::Ok)
            {
                return status;
            }
            const std::optional<uint16_t> relSeq = message.reliable ? std::optional<uint16_t>(message.relSeq) : std::nullopt;
            if (const Status status = AppendMessage(payload, message.type, message.peer, relSeq, AsSpan(body));
                status != Status::Ok)
            {
                return status;
            }
        }
        return EncodePacket(packet.header, AsSpan(payload), aOut);
    }
    }
    return Status::UnknownPacketType;
}

// ---- delta world ---------------------------------------------------------------------------------

World::World(uint64_t aSeed, size_t aEntities)
    : rng(aSeed)
{
    for (size_t index = 0; index < aEntities; ++index)
    {
        EntityState state;
        state.kind = static_cast<uint8_t>(rng.Range(1, 5));
        state.spawnFlags = static_cast<uint8_t>(rng.Below(16));
        state.attitude = static_cast<uint8_t>(rng.Below(3));
        state.record = 1000 + state.kind;
        state.appearance = rng.U64();
        state.pos = {static_cast<int32_t>(rng.Range(-1500000, -1300000)), static_cast<int32_t>(rng.Range(100000, 300000)),
                     static_cast<int32_t>(rng.Range(10000, 40000))};
        state.rot = state.UsesQuat() ? rng.U32() : static_cast<uint32_t>(rng.Below(65536));
        state.worldId = state.kind == static_cast<uint8_t>(coopv2::EntityKind::QuestNpc) ? ((0x8Full << 56) | nextId) : 0;
        entities.emplace_back(nextId++, state);
    }
}

void World::Step()
{
    ++tick;
    if (tick % 20 == 0 && entities.size() > 3)
    {
        for (int removed = 0; removed < 3; ++removed)
        {
            entities.erase(entities.begin() + static_cast<std::ptrdiff_t>(rng.Below(entities.size())));
        }
        const World fresh(rng.U64(), 3);
        for (auto [unusedId, state] : fresh.entities)
        {
            static_cast<void>(unusedId);
            state.worldId = state.worldId != 0 ? ((0x8Full << 56) | nextId) : 0;
            entities.emplace_back(nextId++, state);
        }
    }
    for (auto& [netId, state] : entities)
    {
        static_cast<void>(netId);
        if (rng.Chance(0.3))
        {
            continue; // idle this tick
        }
        if (rng.Chance(0.02))
        {
            state.pos[0] += static_cast<int32_t>(rng.Range(40000, 90000)); // teleport: beyond a 16-bit delta
        }
        for (auto& axis : state.pos)
        {
            axis += static_cast<int32_t>(rng.Range(-800, 800));
        }
        if (rng.Chance(0.5))
        {
            state.rot = state.UsesQuat() ? rng.U32() : static_cast<uint32_t>(rng.Below(65536));
        }
        if (rng.Chance(0.4))
        {
            state.vel = {static_cast<int16_t>(rng.Range(-900, 900)), static_cast<int16_t>(rng.Range(-900, 900)), 0};
        }
        if (rng.Chance(0.1))
        {
            state.state = {static_cast<uint8_t>(rng.Below(kMoveStateCount)), static_cast<uint8_t>(rng.Below(256)),
                           static_cast<uint8_t>(rng.Below(256))};
        }
        if (rng.Chance(0.05))
        {
            state.target = static_cast<uint16_t>(rng.Below(3) == 0 ? 0 : rng.Range(1, 0xFFFF));
        }
        if (rng.Chance(0.03))
        {
            state.weapon = rng.Chance(0.3) ? 0 : rng.U64();
        }
        if (rng.Chance(0.01))
        {
            state.attitude = static_cast<uint8_t>(rng.Below(3)); // identity change: the spawn is sent again
        }
    }
}

EntityView World::States() const
{
    EntityView view;
    for (const auto& [netId, state] : entities)
    {
        view.Set(netId, state);
    }
    return view;
}

std::map<uint16_t, double> World::Weights(Rng& aRng) const
{
    static constexpr double kWeights[] = {0.5, 1.0, 1.25, 2.0, 3.0, 0.1, 1.1};
    std::map<uint16_t, double> weights;
    for (const auto& [netId, state] : entities)
    {
        static_cast<void>(state);
        if (aRng.Chance(0.5))
        {
            weights[netId] = kWeights[aRng.Below(std::size(kWeights))];
        }
    }
    return weights;
}
} // namespace coopnet::v2::test
