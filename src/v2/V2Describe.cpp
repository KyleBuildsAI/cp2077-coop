#include "v2/V2Describe.hpp"

#include <bit>
#include <cstdio>
#include <type_traits>

namespace coopnet::v2
{
namespace
{
class Line
{
public:
    explicit Line(std::string_view aHead)
        : m_text(aHead)
    {
    }

    template<class T>
    Line& Add(std::string_view aName, T aValue)
    {
        m_text += ' ';
        m_text += aName;
        m_text += '=';
        if constexpr (std::is_floating_point_v<T>)
        {
            char bits[16];
            std::snprintf(bits, sizeof(bits), "f:%08x", std::bit_cast<uint32_t>(static_cast<float>(aValue)));
            m_text += bits;
        }
        else
        {
            m_text += std::to_string(aValue);
        }
        return *this;
    }

    Line& Text(std::string_view aName, std::string_view aHex)
    {
        m_text += ' ';
        m_text += aName;
        m_text += '=';
        m_text += aHex;
        return *this;
    }

    Line& Token(std::string_view aToken)
    {
        m_text += ' ';
        m_text += aToken;
        return *this;
    }

    [[nodiscard]] std::string Str() const
    {
        return m_text;
    }

private:
    std::string m_text;
};

template<class T, size_t N>
std::string Triple(const std::array<T, N>& aValues)
{
    std::string text;
    for (size_t index = 0; index < N; ++index)
    {
        if (index > 0)
        {
            text += ',';
        }
        text += std::to_string(aValues[index]);
    }
    return text;
}

std::string DescribeRecord(const EntityRecord& aRecord)
{
    std::string token = "rec:" + std::to_string(aRecord.netId);
    if (aRecord.remove)
    {
        return token + ":remove";
    }
    if (aRecord.spawn)
    {
        const EntitySpawnInfo& spawn = *aRecord.spawn;
        token += ":spawn=" + std::to_string(spawn.kind) + "," + std::to_string(spawn.spawnFlags) + "," +
                 std::to_string(spawn.attitude) + "," + std::to_string(spawn.record) + "," +
                 std::to_string(spawn.appearance);
    }
    if (aRecord.pos)
    {
        token += ":pos=" + Triple(*aRecord.pos);
    }
    if (aRecord.posDelta)
    {
        token += ":pos_delta=" + Triple(*aRecord.posDelta);
    }
    if (aRecord.yaw)
    {
        token += ":yaw=" + std::to_string(*aRecord.yaw);
    }
    if (aRecord.quat)
    {
        token += ":quat=" + std::to_string(*aRecord.quat);
    }
    if (aRecord.vel)
    {
        token += ":vel=" + Triple(*aRecord.vel);
    }
    if (aRecord.state)
    {
        token += ":state=" + Triple(*aRecord.state);
    }
    if (aRecord.target)
    {
        token += ":target=" + std::to_string(*aRecord.target);
    }
    if (aRecord.weapon)
    {
        token += ":weapon=" + std::to_string(*aRecord.weapon);
    }
    if (aRecord.worldId)
    {
        token += ":world_id=" + std::to_string(*aRecord.worldId);
    }
    return token;
}

void AddVehicle(Line& aLine, const coopv2::VehicleBlock& aBlock)
{
    aLine.Add("vehicle.vehicle_net", aBlock.vehicle_net)
        .Add("vehicle.px", aBlock.px)
        .Add("vehicle.py", aBlock.py)
        .Add("vehicle.pz", aBlock.pz)
        .Add("vehicle.quat", aBlock.quat)
        .Add("vehicle.lvx", aBlock.lvx)
        .Add("vehicle.lvy", aBlock.lvy)
        .Add("vehicle.lvz", aBlock.lvz)
        .Add("vehicle.avx", aBlock.avx)
        .Add("vehicle.avy", aBlock.avy)
        .Add("vehicle.avz", aBlock.avz)
        .Add("vehicle.steer", aBlock.steer)
        .Add("vehicle.throttle", aBlock.throttle)
        .Add("vehicle.brake", aBlock.brake)
        .Add("vehicle.vflags", aBlock.vflags);
}

std::string DescribeBodyImpl(const Body& aBody)
{
    return std::visit(
        [](const auto& aValue) -> std::string {
            using T = std::decay_t<decltype(aValue)>;
            if constexpr (std::is_same_v<T, coopv2::TimeReq>)
            {
                return Line("TIME_REQ").Add("t0", aValue.t0).Str();
            }
            else if constexpr (std::is_same_v<T, coopv2::TimeResp>)
            {
                return Line("TIME_RESP").Add("t0", aValue.t0).Add("t1", aValue.t1).Add("t2", aValue.t2).Str();
            }
            else if constexpr (std::is_same_v<T, PeerJoinedMsg>)
            {
                const auto& fixed = aValue.fixed;
                return Line("PEER_JOINED")
                    .Add("peer_id", fixed.peer_id)
                    .Add("role", fixed.role)
                    .Add("minor", fixed.minor)
                    .Add("peer_flags", fixed.peer_flags)
                    .Add("caps", fixed.caps)
                    .Add("mod_hash", fixed.mod_hash)
                    .Add("mod_count", fixed.mod_count)
                    .Add("mod_major", fixed.mod_major)
                    .Add("mod_minor", fixed.mod_minor)
                    .Add("mod_patch", fixed.mod_patch)
                    .Text("name", Hex(aValue.name))
                    .Str();
            }
            else if constexpr (std::is_same_v<T, coopv2::PeerLeft>)
            {
                return Line("PEER_LEFT").Add("peer_id", aValue.peer_id).Add("reason", aValue.reason).Str();
            }
            else if constexpr (std::is_same_v<T, coopv2::LinkStats>)
            {
                return Line("LINK_STATS")
                    .Add("peer_id", aValue.peer_id)
                    .Add("reserved", aValue.reserved)
                    .Add("rtt_ms", aValue.rtt_ms)
                    .Add("loss_in_permille", aValue.loss_in_permille)
                    .Add("loss_out_permille", aValue.loss_out_permille)
                    .Str();
            }
            else if constexpr (std::is_same_v<T, PlayerSnapshotMsg>)
            {
                const auto& base = aValue.base;
                Line line("PLAYER_SNAPSHOT");
                line.Add("snap_seq", base.snap_seq)
                    .Add("sample_time", base.sample_time)
                    .Add("x", base.x)
                    .Add("y", base.y)
                    .Add("z", base.z)
                    .Add("yaw", base.yaw)
                    .Add("pitch", base.pitch)
                    .Add("vx", base.vx)
                    .Add("vy", base.vy)
                    .Add("vz", base.vz)
                    .Add("move_state", base.move_state)
                    .Add("health", base.health)
                    .Add("flags", base.flags);
                if (aValue.vehicle)
                {
                    AddVehicle(line, *aValue.vehicle);
                }
                else
                {
                    line.Token("vehicle=none");
                }
                return line.Str();
            }
            else if constexpr (std::is_same_v<T, EntitySnapshotMsg>)
            {
                Line line("ENTITY_SNAPSHOT");
                line.Add("tick", aValue.tick)
                    .Add("baseline", aValue.baseline)
                    .Add("sample_time", aValue.sampleTime)
                    .Add("count", aValue.records.size());
                for (const EntityRecord& record : aValue.records)
                {
                    line.Token(DescribeRecord(record));
                }
                return line.Str();
            }
            else if constexpr (std::is_same_v<T, coopv2::SnapshotAck>)
            {
                return Line("SNAPSHOT_ACK").Add("tick", aValue.tick).Str();
            }
            else if constexpr (std::is_same_v<T, coopv2::FireFx>)
            {
                return Line("FIRE_FX")
                    .Add("time_ms", aValue.time_ms)
                    .Add("ox", aValue.ox)
                    .Add("oy", aValue.oy)
                    .Add("oz", aValue.oz)
                    .Add("dx", aValue.dx)
                    .Add("dy", aValue.dy)
                    .Add("dz", aValue.dz)
                    .Add("weapon_class", aValue.weapon_class)
                    .Add("shots", aValue.shots)
                    .Str();
            }
            else if constexpr (std::is_same_v<T, coopv2::Equip>)
            {
                return Line("EQUIP")
                    .Add("slot", aValue.slot)
                    .Add("weapon_class", aValue.weapon_class)
                    .Add("flags", aValue.flags)
                    .Add("item_record", aValue.item_record)
                    .Add("appearance", aValue.appearance)
                    .Add("ammo", aValue.ammo)
                    .Str();
            }
            else if constexpr (std::is_same_v<T, coopv2::VehicleEnter>)
            {
                return Line("VEHICLE_ENTER")
                    .Add("vehicle_net", aValue.vehicle_net)
                    .Add("seat", aValue.seat)
                    .Add("flags", aValue.flags)
                    .Add("record", aValue.record)
                    .Add("appearance", aValue.appearance)
                    .Add("x", aValue.x)
                    .Add("y", aValue.y)
                    .Add("z", aValue.z)
                    .Add("quat", aValue.quat)
                    .Str();
            }
            else if constexpr (std::is_same_v<T, coopv2::VehicleExit>)
            {
                return Line("VEHICLE_EXIT")
                    .Add("vehicle_net", aValue.vehicle_net)
                    .Add("seat", aValue.seat)
                    .Add("flags", aValue.flags)
                    .Add("x", aValue.x)
                    .Add("y", aValue.y)
                    .Add("z", aValue.z)
                    .Add("yaw", aValue.yaw)
                    .Str();
            }
            else if constexpr (std::is_same_v<T, coopv2::Hit>)
            {
                return Line("HIT")
                    .Add("target_net", aValue.target_net)
                    .Add("target_kind", aValue.target_kind)
                    .Add("hit_zone", aValue.hit_zone)
                    .Add("attack", aValue.attack)
                    .Add("flags", aValue.flags)
                    .Add("damage", aValue.damage)
                    .Add("weapon_record", aValue.weapon_record)
                    .Add("rx", aValue.rx)
                    .Add("ry", aValue.ry)
                    .Add("rz", aValue.rz)
                    .Add("time_ms", aValue.time_ms)
                    .Str();
            }
            else if constexpr (std::is_same_v<T, coopv2::Death>)
            {
                return Line("DEATH")
                    .Add("target_net", aValue.target_net)
                    .Add("target_kind", aValue.target_kind)
                    .Add("cause", aValue.cause)
                    .Add("killer_net", aValue.killer_net)
                    .Add("killer_kind", aValue.killer_kind)
                    .Add("flags", aValue.flags)
                    .Add("time_ms", aValue.time_ms)
                    .Str();
            }
            else if constexpr (std::is_same_v<T, coopv2::TimeWeather>)
            {
                return Line("TIME_WEATHER")
                    .Add("game_seconds", aValue.game_seconds)
                    .Add("time_scale_x100", aValue.time_scale_x100)
                    .Add("flags", aValue.flags)
                    .Add("weather_id", aValue.weather_id)
                    .Add("weather_record", aValue.weather_record)
                    .Add("transition_s", aValue.transition_s)
                    .Str();
            }
            else if constexpr (std::is_same_v<T, ChatMsg>)
            {
                return Line("CHAT").Add("channel", aValue.channel).Text("text", Hex(aValue.text)).Str();
            }
            else if constexpr (std::is_same_v<T, coopv2::TeleportReq>)
            {
                return Line("TELEPORT_REQ")
                    .Add("req_id", aValue.req_id)
                    .Add("mode", aValue.mode)
                    .Add("reserved", aValue.reserved)
                    .Str();
            }
            else if constexpr (std::is_same_v<T, coopv2::TeleportResp>)
            {
                return Line("TELEPORT_RESP")
                    .Add("req_id", aValue.req_id)
                    .Add("accepted", aValue.accepted)
                    .Add("reserved", aValue.reserved)
                    .Add("x", aValue.x)
                    .Add("y", aValue.y)
                    .Add("z", aValue.z)
                    .Add("yaw", aValue.yaw)
                    .Str();
            }
            else if constexpr (std::is_same_v<T, coopv2::WorldFact>)
            {
                return Line("WORLD_FACT").Add("fact_hash", aValue.fact_hash).Add("value", aValue.value).Str();
            }
            else if constexpr (std::is_same_v<T, ModListMsg>)
            {
                return Line("MOD_LIST")
                    .Add("chunk", aValue.chunk)
                    .Add("chunks", aValue.chunks)
                    .Text("text", Hex(aValue.text))
                    .Str();
            }
            else if constexpr (std::is_same_v<T, coopv2::SessionConfig>)
            {
                return Line("SESSION_CONFIG")
                    .Add("npc_radius_m", aValue.npc_radius_m)
                    .Add("vehicle_radius_m", aValue.vehicle_radius_m)
                    .Add("entity_hz", aValue.entity_hz)
                    .Add("joiner_population", aValue.joiner_population)
                    .Add("flags", aValue.flags)
                    .Str();
            }
            else
            {
                static_assert(std::is_same_v<T, ScriptMsg>, "every Body alternative is described");
                return Line("SCRIPT_MSG")
                    .Add("channel", aValue.channel)
                    .Add("flags", aValue.flags)
                    .Text("text", Hex(aValue.text))
                    .Str();
            }
        },
        aBody);
}

std::string HexU64(uint64_t aValue)
{
    char text[24];
    std::snprintf(text, sizeof(text), "%016llx", static_cast<unsigned long long>(aValue));
    return text;
}

std::string HexU32(uint32_t aValue)
{
    char text[16];
    std::snprintf(text, sizeof(text), "%08x", aValue);
    return text;
}
} // namespace

std::string Hex(ByteSpan aBytes)
{
    static constexpr char kDigits[] = "0123456789abcdef";
    std::string text;
    text.reserve(aBytes.size() * 2);
    for (const uint8_t byte : aBytes)
    {
        text += kDigits[byte >> 4];
        text += kDigits[byte & 0xF];
    }
    return text;
}

std::string Hex(std::string_view aBytes)
{
    return Hex(ByteSpan(reinterpret_cast<const uint8_t*>(aBytes.data()), aBytes.size()));
}

std::string DescribeJoin(const JoinInfo& aJoin)
{
    const auto& fixed = aJoin.fixed;
    std::string text = Line("")
                           .Add("minor", fixed.minor)
                           .Add("role", fixed.role)
                           .Add("join_flags", fixed.join_flags)
                           .Add("caps", fixed.caps)
                           .Add("game_build", fixed.game_build)
                           .Add("mod_major", fixed.mod_major)
                           .Add("mod_minor", fixed.mod_minor)
                           .Add("mod_patch", fixed.mod_patch)
                           .Add("mod_hash", fixed.mod_hash)
                           .Add("mod_count", fixed.mod_count)
                           .Add("client_nonce", fixed.client_nonce)
                           .Add("resume_token", fixed.resume_token)
                           .Text("room", Hex(aJoin.room))
                           .Text("name", Hex(aJoin.name))
                           .Str();
    return text.substr(1); // drop the separator in front of the first field
}

std::string DescribeBody(const Body& aBody)
{
    return DescribeBodyImpl(aBody);
}

std::string DescribeMessage(const MessageView& aMessage, const Body& aBody)
{
    const MsgSpec* spec = FindSpec(aMessage.type);
    const bool deliveryOk = spec != nullptr && DeliveryOk(*spec, aMessage.reliable, aBody);
    Line line("msg");
    line.Add("type", aMessage.type).Add("peer", aMessage.peer);
    line.Text("rel", aMessage.reliable ? std::to_string(aMessage.relSeq) : std::string("-"));
    line.Text("delivery", deliveryOk ? "ok" : "bad");
    line.Add("minor", RequiredMinor(aBody));
    line.Token(DescribeBody(aBody));
    return line.Str();
}

Status DescribeDatagram(ByteSpan aDatagram, std::vector<std::string>& aLines)
{
    aLines.clear();
    DecodedPacket packet;
    if (const Status status = DecodePacket(aDatagram, packet); status != Status::Ok)
    {
        return status;
    }
    const PacketInfo& header = packet.header;
    aLines.push_back(Line("packet")
                         .Add("type", header.type)
                         .Text("token", HexU64(header.token))
                         .Add("seq", header.seq)
                         .Add("ack", header.ack)
                         .Text("ack_bits", HexU32(header.ackBits))
                         .Str());
    Status status = Status::Ok;
    switch (static_cast<coopv2::PacketType>(header.type))
    {
    case coopv2::PacketType::Hello:
    {
        JoinInfo join;
        size_t padding = 0;
        status = DecodeHello(packet.body, join, &padding);
        if (status == Status::Ok)
        {
            aLines.push_back("hello " + DescribeJoin(join) + " padding=" + std::to_string(padding));
        }
        break;
    }
    case coopv2::PacketType::Challenge:
    {
        coopv2::Challenge challenge{};
        status = DecodeChallenge(packet.body, challenge);
        if (status == Status::Ok)
        {
            aLines.push_back(Line("challenge")
                                 .Add("minor_min", challenge.minor_min)
                                 .Add("minor_max", challenge.minor_max)
                                 .Add("reserved", challenge.reserved)
                                 .Text("cookie", Hex(ByteSpan(challenge.cookie, sizeof(challenge.cookie))))
                                 .Str());
        }
        break;
    }
    case coopv2::PacketType::Auth:
    {
        AuthInfo auth;
        status = DecodeAuth(packet.body, auth);
        if (status == Status::Ok)
        {
            aLines.push_back("auth " + DescribeJoin(auth.join) + " cookie=" + Hex(auth.cookie) +
                             " key_hash=" + Hex(auth.keyHash));
        }
        break;
    }
    case coopv2::PacketType::Welcome:
    {
        coopv2::Welcome welcome{};
        status = DecodeWelcome(packet.body, welcome);
        if (status == Status::Ok)
        {
            aLines.push_back(Line("welcome")
                                 .Add("minor", welcome.minor)
                                 .Add("peer_id", welcome.peer_id)
                                 .Add("role", welcome.role)
                                 .Add("room_flags", welcome.room_flags)
                                 .Add("token", welcome.token)
                                 .Add("relay_time_ms", welcome.relay_time_ms)
                                 .Add("player_hz", welcome.player_hz)
                                 .Add("entity_hz", welcome.entity_hz)
                                 .Add("max_packet", welcome.max_packet)
                                 .Add("room_caps", welcome.room_caps)
                                 .Str());
        }
        break;
    }
    case coopv2::PacketType::Reject:
    {
        RejectInfo reject;
        status = DecodeReject(packet.body, reject);
        if (status == Status::Ok)
        {
            aLines.push_back(Line("reject")
                                 .Add("reason", reject.reason)
                                 .Add("minor_min", reject.minorMin)
                                 .Add("minor_max", reject.minorMax)
                                 .Text("text", Hex(reject.text))
                                 .Str());
        }
        break;
    }
    case coopv2::PacketType::Data:
    {
        std::vector<MessageView> messages;
        status = DecodeMessages(packet.body, messages);
        for (size_t index = 0; status == Status::Ok && index < messages.size(); ++index)
        {
            Body body;
            status = DecodeBody(messages[index].type, messages[index].body, body);
            if (status == Status::Ok)
            {
                aLines.push_back(DescribeMessage(messages[index], body));
            }
        }
        break;
    }
    case coopv2::PacketType::Disconnect:
    {
        uint8_t reason = 0;
        status = DecodeDisconnect(packet.body, reason);
        if (status == Status::Ok)
        {
            aLines.push_back(Line("disconnect").Add("reason", reason).Str());
        }
        break;
    }
    default: status = Status::UnknownPacketType; break;
    }
    return status;
}
} // namespace coopnet::v2
