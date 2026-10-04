#include "v2/V2Codec.hpp"

#include <algorithm>
#include <bit>
#include <bitset>
#include <cmath>
#include <cstring>
#include <limits>
#include <type_traits>

namespace coopnet::v2
{
static_assert(std::endian::native == std::endian::little, "the v2 structs are copied as little-endian");
static_assert(kMaxMessageBody == 1174, "proto.py MAX_MESSAGE_BODY");

namespace
{
using coopv2::MsgType;

constexpr double kWorldXYLimit = 20000.0; // proto.WORLD_XY_LIMIT_M
constexpr double kWorldZLimit = 5000.0;   // proto.WORLD_Z_LIMIT_M
constexpr uint8_t kExtKnown = coopv2::kExtRemove | coopv2::kExtTarget | coopv2::kExtWeapon | coopv2::kExtWorldId;
constexpr uint8_t kExtFields = coopv2::kExtTarget | coopv2::kExtWeapon | coopv2::kExtWorldId;

// Bounds-checked little-endian reader over a span. Every read either copies the whole value or
// returns false and leaves the offset where it was.
class Reader
{
public:
    explicit Reader(ByteSpan aData, size_t aOffset = 0)
        : m_data(aData)
        , m_offset(std::min(aOffset, aData.size()))
    {
    }

    template<class T>
    bool Get(T& aValue)
    {
        static_assert(std::is_trivially_copyable_v<T>);
        if (Remaining() < sizeof(T))
        {
            return false;
        }
        std::memcpy(&aValue, m_data.data() + m_offset, sizeof(T));
        m_offset += sizeof(T);
        return true;
    }

    bool Text(size_t aLength, std::string& aText)
    {
        if (Remaining() < aLength)
        {
            return false;
        }
        aText.assign(reinterpret_cast<const char*>(m_data.data() + m_offset), aLength);
        m_offset += aLength;
        return true;
    }

    [[nodiscard]] size_t Remaining() const
    {
        return m_data.size() - m_offset;
    }

    [[nodiscard]] size_t Offset() const
    {
        return m_offset;
    }

private:
    ByteSpan m_data;
    size_t m_offset;
};

template<class T>
void Put(Bytes& aOut, const T& aValue)
{
    static_assert(std::is_trivially_copyable_v<T>);
    const auto* bytes = reinterpret_cast<const uint8_t*>(&aValue);
    aOut.insert(aOut.end(), bytes, bytes + sizeof(T));
}

void PutText(Bytes& aOut, std::string_view aText)
{
    aOut.insert(aOut.end(), reinterpret_cast<const uint8_t*>(aText.data()),
                reinterpret_cast<const uint8_t*>(aText.data()) + aText.size());
}

ByteSpan AsSpan(const Bytes& aBytes)
{
    return {aBytes.data(), aBytes.size()};
}

// Python's round(): half to even, independent of the FPU rounding mode.
double RoundHalfEven(double aValue)
{
    const double rounded = std::round(aValue);
    if (std::fabs(aValue - std::trunc(aValue)) == 0.5)
    {
        return 2.0 * std::round(aValue / 2.0);
    }
    return rounded;
}

// Rounds, then clamps into [aLow, aHigh]. NaN maps to 0 (proto.py raises instead).
int64_t RoundClamp(double aValue, double aLow, double aHigh)
{
    const double rounded = RoundHalfEven(aValue);
    if (std::isnan(rounded))
    {
        return 0;
    }
    return static_cast<int64_t>(rounded < aLow ? aLow : rounded > aHigh ? aHigh : rounded);
}

// Python's float %: the result takes the sign of the divisor.
double PythonMod(double aValue, double aDivisor)
{
    double mod = std::fmod(aValue, aDivisor);
    if (mod != 0.0)
    {
        if ((aDivisor < 0.0) != (mod < 0.0))
        {
            mod += aDivisor;
        }
    }
    else
    {
        mod = std::copysign(0.0, aDivisor);
    }
    return mod;
}

// Python's sum() over floats (3.12+: the first term, then Neumaier-compensated additions).
template<size_t N>
double PythonSum(const std::array<double, N>& aTerms)
{
    double result = aTerms[0];
    double compensation = 0.0;
    for (size_t index = 1; index < N; ++index)
    {
        const double term = aTerms[index];
        const double total = result + term;
        if (std::fabs(result) >= std::fabs(term))
        {
            compensation += (result - total) + term;
        }
        else
        {
            compensation += (term - total) + result;
        }
        result = total;
    }
    if (compensation != 0.0 && std::isfinite(compensation))
    {
        result += compensation;
    }
    return result;
}

const double kQuatScale = 1.0 / std::sqrt(2.0);

// ---- text ----

// Decodes one UTF-8 sequence at aText[aIndex]; returns 0 bytes when invalid.
size_t DecodeUtf8(std::string_view aText, size_t aIndex, uint32_t& aCodePoint)
{
    const auto byte = [&](size_t aOffset) { return static_cast<uint8_t>(aText[aIndex + aOffset]); };
    const size_t left = aText.size() - aIndex;
    const uint8_t lead = byte(0);
    if (lead < 0x80)
    {
        aCodePoint = lead;
        return 1;
    }
    const auto continuation = [&](size_t aOffset, uint8_t aLow = 0x80, uint8_t aHigh = 0xBF) {
        return aOffset < left && byte(aOffset) >= aLow && byte(aOffset) <= aHigh;
    };
    if (lead >= 0xC2 && lead <= 0xDF)
    {
        if (!continuation(1))
        {
            return 0;
        }
        aCodePoint = ((lead & 0x1Fu) << 6) | (byte(1) & 0x3Fu);
        return 2;
    }
    if (lead >= 0xE0 && lead <= 0xEF)
    {
        const uint8_t low = lead == 0xE0 ? 0xA0 : 0x80;
        const uint8_t high = lead == 0xED ? 0x9F : 0xBF;
        if (!continuation(1, low, high) || !continuation(2))
        {
            return 0;
        }
        aCodePoint = ((lead & 0x0Fu) << 12) | ((byte(1) & 0x3Fu) << 6) | (byte(2) & 0x3Fu);
        return 3;
    }
    if (lead >= 0xF0 && lead <= 0xF4)
    {
        const uint8_t low = lead == 0xF0 ? 0x90 : 0x80;
        const uint8_t high = lead == 0xF4 ? 0x8F : 0xBF;
        if (!continuation(1, low, high) || !continuation(2) || !continuation(3))
        {
            return 0;
        }
        aCodePoint = ((lead & 0x07u) << 18) | ((byte(1) & 0x3Fu) << 12) | ((byte(2) & 0x3Fu) << 6) |
                     (byte(3) & 0x3Fu);
        return 4;
    }
    return 0;
}

bool ForbiddenStrict(uint32_t aCodePoint)
{
    return aCodePoint < 0x20 || (aCodePoint >= 0x7F && aCodePoint < 0xA0) || aCodePoint == 0x2028 ||
           aCodePoint == 0x2029;
}

// ---- value checks shared by several bodies ----

Status CheckWorld(double aX, double aY, double aZ)
{
    if (!std::isfinite(aX) || !std::isfinite(aY) || !std::isfinite(aZ))
    {
        return Status::NonFinite;
    }
    if (std::fabs(aX) > kWorldXYLimit || std::fabs(aY) > kWorldXYLimit || std::fabs(aZ) > kWorldZLimit)
    {
        return Status::OutOfWorld;
    }
    return Status::Ok;
}

Status CheckWorld(float aX, float aY, float aZ)
{
    return CheckWorld(static_cast<double>(aX), static_cast<double>(aY), static_cast<double>(aZ));
}

Status CheckPlayerBase(const coopv2::PlayerSnapshot& aBase)
{
    if (const Status world = CheckWorld(aBase.x, aBase.y, aBase.z); world != Status::Ok)
    {
        return world;
    }
    if (aBase.move_state >= kMoveStateCount || aBase.pitch < -9000 || aBase.pitch > 9000)
    {
        return Status::BadValue;
    }
    return Status::Ok;
}

Status CheckVehicleBlock(const coopv2::VehicleBlock& aBlock)
{
    if (const Status world = CheckWorld(aBlock.px, aBlock.py, aBlock.pz); world != Status::Ok)
    {
        return world;
    }
    if (aBlock.vehicle_net == 0 || aBlock.steer < -100 || aBlock.steer > 100 || aBlock.throttle > 100 ||
        aBlock.brake > 100)
    {
        return Status::BadValue;
    }
    return Status::Ok;
}

// ---- generic fixed bodies ----

template<class T>
Status ReadExact(ByteSpan aBody, T& aOut)
{
    if (aBody.size() < sizeof(T))
    {
        return Status::Truncated;
    }
    if (aBody.size() != sizeof(T))
    {
        return Status::TrailingBytes;
    }
    std::memcpy(&aOut, aBody.data(), sizeof(T));
    return Status::Ok;
}

// A fixed struct followed by a LengthT length prefix and that many text bytes, and nothing else.
template<class LengthT, class T>
Status ReadWithText(ByteSpan aBody, T& aFixed, std::string& aText, size_t aLimit, TextRule aRule)
{
    Reader reader(aBody);
    LengthT length = 0;
    if (!reader.Get(aFixed) || !reader.Get(length) || !reader.Text(length, aText))
    {
        return Status::Truncated;
    }
    if (aText.size() > aLimit || !IsValidText(aText, aRule))
    {
        return Status::BadText;
    }
    return reader.Remaining() == 0 ? Status::Ok : Status::TrailingBytes;
}

template<class LengthT, class T>
Status WriteWithText(Bytes& aOut, const T& aFixed, std::string_view aText)
{
    if (aText.size() > static_cast<size_t>(std::numeric_limits<LengthT>::max()))
    {
        return Status::BadText;
    }
    Put(aOut, aFixed);
    Put(aOut, static_cast<LengthT>(aText.size()));
    PutText(aOut, aText);
    return Status::Ok;
}

// ---- per-type decoders ----

Status DecodePeerJoined(ByteSpan aBody, PeerJoinedMsg& aOut)
{
    if (const Status status = ReadWithText<uint8_t>(aBody, aOut.fixed, aOut.name, kMaxNameBytes, TextRule::Strict);
        status != Status::Ok)
    {
        return status;
    }
    return aOut.fixed.role <= static_cast<uint8_t>(coopv2::Role::Spectator) ? Status::Ok : Status::BadValue;
}

Status DecodePlayer(ByteSpan aBody, PlayerSnapshotMsg& aOut)
{
    constexpr size_t kBase = sizeof(coopv2::PlayerSnapshot);
    if (aBody.size() < kBase)
    {
        return Status::Truncated;
    }
    uint16_t flags = 0;
    std::memcpy(&flags, aBody.data() + kBase - sizeof(flags), sizeof(flags));
    const bool driving = (flags & coopv2::kPlayerDriving) != 0;
    if (aBody.size() != kBase + (driving ? sizeof(coopv2::VehicleBlock) : 0))
    {
        return Status::BadSize;
    }
    std::memcpy(&aOut.base, aBody.data(), kBase);
    if (const Status status = CheckPlayerBase(aOut.base); status != Status::Ok)
    {
        return status;
    }
    aOut.vehicle.reset();
    if (driving)
    {
        coopv2::VehicleBlock block{};
        std::memcpy(&block, aBody.data() + kBase, sizeof(block));
        if (const Status status = CheckVehicleBlock(block); status != Status::Ok)
        {
            return status;
        }
        aOut.vehicle = block;
    }
    return Status::Ok;
}

template<class T, size_t N>
bool GetArray(Reader& aReader, std::optional<std::array<T, N>>& aOut)
{
    std::array<T, N> values{};
    if (!aReader.Get(values))
    {
        return false;
    }
    aOut = values;
    return true;
}

template<class T>
bool GetOptional(Reader& aReader, std::optional<T>& aOut)
{
    T value{};
    if (!aReader.Get(value))
    {
        return false;
    }
    aOut = value;
    return true;
}

Status DecodeRecord(Reader& aReader, EntityRecord& aRecord)
{
    uint8_t mask = 0;
    if (!aReader.Get(aRecord.netId) || !aReader.Get(mask))
    {
        return Status::Truncated;
    }
    if (aRecord.netId == 0)
    {
        return Status::BadRecord;
    }
    uint8_t ext = 0;
    if ((mask & coopv2::kMaskExt) != 0)
    {
        if (!aReader.Get(ext))
        {
            return Status::Truncated;
        }
        if (ext == 0 || (ext & ~kExtKnown) != 0)
        {
            return Status::BadRecord;
        }
    }
    if ((ext & coopv2::kExtRemove) != 0)
    {
        if (mask != coopv2::kMaskExt || ext != coopv2::kExtRemove)
        {
            return Status::BadRecord;
        }
        aRecord.remove = true;
        return Status::Ok;
    }
    const bool both_positions = (mask & coopv2::kMaskPos) != 0 && (mask & coopv2::kMaskPosDelta) != 0;
    const bool both_rotations = (mask & coopv2::kMaskYaw) != 0 && (mask & coopv2::kMaskQuat) != 0;
    const bool empty = (mask & ~coopv2::kMaskExt) == 0 && (ext & kExtFields) == 0;
    if (both_positions || both_rotations || empty)
    {
        return Status::BadRecord;
    }
    if ((mask & coopv2::kMaskSpawn) != 0)
    {
        if ((mask & coopv2::kMaskPos) == 0 || (mask & (coopv2::kMaskYaw | coopv2::kMaskQuat)) == 0)
        {
            return Status::BadRecord;
        }
        coopv2::EntitySpawn spawn{};
        if (!aReader.Get(spawn))
        {
            return Status::Truncated;
        }
        if (spawn.kind < static_cast<uint8_t>(coopv2::EntityKind::CrowdNpc) ||
            spawn.kind > static_cast<uint8_t>(coopv2::EntityKind::Device))
        {
            return Status::BadValue;
        }
        // proto.py ignores the reserved byte when decoding and writes 0.
        aRecord.spawn = EntitySpawnInfo{spawn.kind, spawn.spawn_flags, spawn.attitude, spawn.record, spawn.appearance};
    }
    if ((mask & coopv2::kMaskPos) != 0)
    {
        if (!GetArray(aReader, aRecord.pos))
        {
            return Status::Truncated;
        }
        const auto& pos = *aRecord.pos;
        if (const Status world = CheckWorld(MmToMeters(pos[0]), MmToMeters(pos[1]), MmToMeters(pos[2]));
            world != Status::Ok)
        {
            return world;
        }
    }
    if ((mask & coopv2::kMaskPosDelta) != 0 && !GetArray(aReader, aRecord.posDelta))
    {
        return Status::Truncated;
    }
    if ((mask & coopv2::kMaskYaw) != 0 && !GetOptional(aReader, aRecord.yaw))
    {
        return Status::Truncated;
    }
    if ((mask & coopv2::kMaskQuat) != 0 && !GetOptional(aReader, aRecord.quat))
    {
        return Status::Truncated;
    }
    if ((mask & coopv2::kMaskVel) != 0 && !GetArray(aReader, aRecord.vel))
    {
        return Status::Truncated;
    }
    if ((mask & coopv2::kMaskState) != 0)
    {
        if (!GetArray(aReader, aRecord.state))
        {
            return Status::Truncated;
        }
        if ((*aRecord.state)[0] >= kMoveStateCount)
        {
            return Status::BadValue;
        }
    }
    if ((ext & coopv2::kExtTarget) != 0 && !GetOptional(aReader, aRecord.target))
    {
        return Status::Truncated;
    }
    if ((ext & coopv2::kExtWeapon) != 0 && !GetOptional(aReader, aRecord.weapon))
    {
        return Status::Truncated;
    }
    if ((ext & coopv2::kExtWorldId) != 0 && !GetOptional(aReader, aRecord.worldId))
    {
        return Status::Truncated;
    }
    return Status::Ok;
}

Status DecodeEntity(ByteSpan aBody, EntitySnapshotMsg& aOut)
{
    Reader reader(aBody);
    coopv2::EntitySnapshotHeader header{};
    if (!reader.Get(header))
    {
        return Status::Truncated;
    }
    if (header.count > kMaxEntityRecords)
    {
        return Status::TooManyRecords;
    }
    if (header.baseline != 0 && header.baseline >= header.tick)
    {
        return Status::BadBaseline;
    }
    aOut.tick = header.tick;
    aOut.baseline = header.baseline;
    aOut.sampleTime = header.sample_time;
    aOut.records.clear();
    aOut.records.reserve(header.count);
    std::bitset<65536> seen;
    for (uint16_t index = 0; index < header.count; ++index)
    {
        EntityRecord record;
        if (const Status status = DecodeRecord(reader, record); status != Status::Ok)
        {
            return status;
        }
        if (seen[record.netId])
        {
            return Status::DuplicateNetId;
        }
        seen[record.netId] = true;
        aOut.records.push_back(std::move(record));
    }
    return reader.Remaining() == 0 ? Status::Ok : Status::TrailingBytes;
}

// ---- per-type encoders (raw bytes; EncodeBody validates them) ----

uint8_t Bit(bool aSet, uint8_t aBit)
{
    return aSet ? aBit : uint8_t{0};
}

void EncodeRecord(const EntityRecord& aRecord, Bytes& aOut)
{
    const uint8_t ext = static_cast<uint8_t>(
        Bit(aRecord.remove, coopv2::kExtRemove) | Bit(aRecord.target.has_value(), coopv2::kExtTarget) |
        Bit(aRecord.weapon.has_value(), coopv2::kExtWeapon) | Bit(aRecord.worldId.has_value(), coopv2::kExtWorldId));
    const uint8_t mask = static_cast<uint8_t>(
        Bit(aRecord.spawn.has_value(), coopv2::kMaskSpawn) | Bit(aRecord.pos.has_value(), coopv2::kMaskPos) |
        Bit(aRecord.posDelta.has_value(), coopv2::kMaskPosDelta) | Bit(aRecord.yaw.has_value(), coopv2::kMaskYaw) |
        Bit(aRecord.quat.has_value(), coopv2::kMaskQuat) | Bit(aRecord.vel.has_value(), coopv2::kMaskVel) |
        Bit(aRecord.state.has_value(), coopv2::kMaskState) | Bit(ext != 0, coopv2::kMaskExt));
    Put(aOut, aRecord.netId);
    Put(aOut, mask);
    if (ext != 0)
    {
        Put(aOut, ext);
    }
    if (aRecord.spawn)
    {
        const coopv2::EntitySpawn spawn{aRecord.spawn->kind, aRecord.spawn->spawnFlags, aRecord.spawn->attitude, 0,
                                        aRecord.spawn->record, aRecord.spawn->appearance};
        Put(aOut, spawn);
    }
    if (aRecord.pos)
    {
        Put(aOut, *aRecord.pos);
    }
    if (aRecord.posDelta)
    {
        Put(aOut, *aRecord.posDelta);
    }
    if (aRecord.yaw)
    {
        Put(aOut, *aRecord.yaw);
    }
    if (aRecord.quat)
    {
        Put(aOut, *aRecord.quat);
    }
    if (aRecord.vel)
    {
        Put(aOut, *aRecord.vel);
    }
    if (aRecord.state)
    {
        Put(aOut, *aRecord.state);
    }
    if (aRecord.target)
    {
        Put(aOut, *aRecord.target);
    }
    if (aRecord.weapon)
    {
        Put(aOut, *aRecord.weapon);
    }
    if (aRecord.worldId)
    {
        Put(aOut, *aRecord.worldId);
    }
}

Status EncodeRaw(const Body& aBody, Bytes& aOut)
{
    return std::visit(
        [&aOut](const auto& aValue) -> Status {
            using T = std::decay_t<decltype(aValue)>;
            if constexpr (std::is_same_v<T, PeerJoinedMsg>)
            {
                return WriteWithText<uint8_t>(aOut, aValue.fixed, aValue.name);
            }
            else if constexpr (std::is_same_v<T, PlayerSnapshotMsg>)
            {
                Put(aOut, aValue.base);
                if ((aValue.base.flags & coopv2::kPlayerDriving) != 0)
                {
                    if (!aValue.vehicle)
                    {
                        return Status::BadValue;
                    }
                    Put(aOut, *aValue.vehicle);
                }
                return Status::Ok;
            }
            else if constexpr (std::is_same_v<T, EntitySnapshotMsg>)
            {
                if (aValue.records.size() > 0xFFFF)
                {
                    return Status::TooManyRecords;
                }
                const coopv2::EntitySnapshotHeader header{aValue.tick, aValue.baseline, aValue.sampleTime,
                                                          static_cast<uint16_t>(aValue.records.size())};
                Put(aOut, header);
                for (const EntityRecord& record : aValue.records)
                {
                    EncodeRecord(record, aOut);
                }
                return Status::Ok;
            }
            else if constexpr (std::is_same_v<T, ChatMsg>)
            {
                return WriteWithText<uint8_t>(aOut, coopv2::ChatFixed{aValue.channel}, aValue.text);
            }
            else if constexpr (std::is_same_v<T, ModListMsg>)
            {
                return WriteWithText<uint8_t>(aOut, coopv2::ModListFixed{aValue.chunk, aValue.chunks}, aValue.text);
            }
            else if constexpr (std::is_same_v<T, ScriptMsg>)
            {
                return WriteWithText<uint16_t>(aOut, coopv2::ScriptMsgFixed{aValue.channel, aValue.flags}, aValue.text);
            }
            else
            {
                Put(aOut, aValue);
                return Status::Ok;
            }
        },
        aBody);
}

template<class T>
Status DecodeFixed(ByteSpan aBody, Body& aOut, Status (*aCheck)(const T&) = nullptr)
{
    T value{};
    if (const Status status = ReadExact(aBody, value); status != Status::Ok)
    {
        return status;
    }
    if (aCheck != nullptr)
    {
        if (const Status status = aCheck(value); status != Status::Ok)
        {
            return status;
        }
    }
    aOut = value;
    return Status::Ok;
}

Status CheckEquip(const coopv2::Equip& aValue)
{
    return aValue.slot < 8 && aValue.weapon_class <= coopv2::kPlayerWeaponClassMask ? Status::Ok : Status::BadValue;
}

Status CheckVehicleEnter(const coopv2::VehicleEnter& aValue)
{
    return CheckWorld(aValue.x, aValue.y, aValue.z);
}

Status CheckVehicleExit(const coopv2::VehicleExit& aValue)
{
    return CheckWorld(aValue.x, aValue.y, aValue.z);
}

Status CheckHit(const coopv2::Hit& aValue)
{
    const double damage = static_cast<double>(aValue.damage);
    if (!std::isfinite(damage))
    {
        return Status::NonFinite;
    }
    const bool ok = aValue.target_kind <= 1 && damage >= 0.0 && damage <= 1.0e6 && aValue.hit_zone < 16 &&
                    aValue.target_net != 0;
    return ok ? Status::Ok : Status::BadValue;
}

Status CheckDeath(const coopv2::Death& aValue)
{
    return aValue.target_kind <= 1 && aValue.killer_kind <= 2 ? Status::Ok : Status::BadValue;
}

Status CheckTimeWeather(const coopv2::TimeWeather& aValue)
{
    return aValue.weather_id < 32 && aValue.time_scale_x100 <= 10000 ? Status::Ok : Status::BadValue;
}

Status CheckTeleportReq(const coopv2::TeleportReq& aValue)
{
    return aValue.mode <= 1 ? Status::Ok : Status::BadValue;
}

Status CheckTeleportResp(const coopv2::TeleportResp& aValue)
{
    return CheckWorld(aValue.x, aValue.y, aValue.z);
}

Status CheckSessionConfig(const coopv2::SessionConfig& aValue)
{
    const bool ok = aValue.npc_radius_m >= 10 && aValue.npc_radius_m <= 500 && aValue.vehicle_radius_m >= 10 &&
                    aValue.vehicle_radius_m <= 1000 && aValue.entity_hz >= 1 && aValue.entity_hz <= 30;
    return ok ? Status::Ok : Status::BadValue;
}

constexpr MsgSpec kSpecs[] = {
    {0x01, "TIME_REQ", Delivery::Unreliable, Sender::Client, Route::Relay, 0},
    {0x02, "TIME_RESP", Delivery::Unreliable, Sender::Relay, Route::Relay, 0},
    {0x03, "PEER_JOINED", Delivery::Reliable, Sender::Relay, Route::Relay, 0},
    {0x04, "PEER_LEFT", Delivery::Reliable, Sender::Relay, Route::Relay, 0},
    {0x05, "LINK_STATS", Delivery::Unreliable, Sender::Relay, Route::Relay, 0},
    {0x10, "PLAYER_SNAPSHOT", Delivery::Unreliable, Sender::Client, Route::Broadcast, 0},
    {0x11, "ENTITY_SNAPSHOT", Delivery::Unreliable, Sender::Host, Route::Broadcast, 0},
    {0x12, "SNAPSHOT_ACK", Delivery::Unreliable, Sender::Client, Route::Host, 0},
    {0x13, "FIRE_FX", Delivery::Unreliable, Sender::Client, Route::Broadcast, 0},
    {0x20, "EQUIP", Delivery::Reliable, Sender::Client, Route::Broadcast, 0},
    {0x21, "VEHICLE_ENTER", Delivery::Reliable, Sender::Client, Route::Broadcast, 0},
    {0x22, "VEHICLE_EXIT", Delivery::Reliable, Sender::Client, Route::Broadcast, 0},
    {0x23, "HIT", Delivery::Reliable, Sender::Client, Route::Hit, 0},
    {0x24, "DEATH", Delivery::Reliable, Sender::Client, Route::Broadcast, 0},
    {0x25, "TIME_WEATHER", Delivery::Reliable, Sender::Host, Route::Broadcast, 0},
    {0x26, "CHAT", Delivery::Reliable, Sender::Client, Route::Broadcast, 0},
    {0x27, "TELEPORT_REQ", Delivery::Reliable, Sender::Client, Route::Target, 0},
    {0x28, "TELEPORT_RESP", Delivery::Reliable, Sender::Client, Route::Target, 0},
    {0x29, "WORLD_FACT", Delivery::Reliable, Sender::Host, Route::Broadcast, 0},
    {0x2A, "MOD_LIST", Delivery::Reliable, Sender::Client, Route::Broadcast, 0},
    {0x2B, "SESSION_CONFIG", Delivery::Reliable, Sender::Host, Route::Broadcast, 0},
    {0x30, "SCRIPT_MSG", Delivery::ByChannel, Sender::Client, Route::Peer, 1},
};

Status FinishPacket(PacketInfo aHeader, const Bytes& aBody, Bytes& aOut)
{
    return EncodePacket(aHeader, AsSpan(aBody), aOut);
}

PacketInfo HandshakeHeader(coopv2::PacketType aType, uint64_t aToken = 0)
{
    PacketInfo header;
    header.type = static_cast<uint8_t>(aType);
    header.token = aToken;
    return header;
}
} // namespace

const char* ToString(Status aStatus)
{
    switch (aStatus)
    {
    case Status::Ok: return "ok";
    case Status::TooLarge: return "too large";
    case Status::NotV2: return "not a v2 datagram";
    case Status::VersionMismatch: return "unsupported protocol major";
    case Status::Truncated: return "truncated";
    case Status::BadSize: return "bad size";
    case Status::TrailingBytes: return "trailing bytes";
    case Status::UnknownPacketType: return "unknown packet type";
    case Status::UnknownMessageType: return "unknown message type";
    case Status::TooManyMessages: return "too many messages in one packet";
    case Status::BadText: return "bad text";
    case Status::BadRoom: return "bad room name";
    case Status::BadPadding: return "bad HELLO padding";
    case Status::NonFinite: return "non-finite float";
    case Status::OutOfWorld: return "coordinate out of world bounds";
    case Status::BadValue: return "value out of range";
    case Status::BadRecord: return "malformed entity record";
    case Status::DuplicateNetId: return "duplicate net_id";
    case Status::TooManyRecords: return "too many entity records";
    case Status::BadBaseline: return "baseline must precede tick";
    }
    return "unknown status";
}

// ---- quantizers ----------------------------------------------------------------------------------

int32_t MetersToMm(double aMeters)
{
    return static_cast<int32_t>(RoundClamp(aMeters * 1000.0, -2147483648.0, 2147483647.0));
}

double MmToMeters(int32_t aMillimeters)
{
    return static_cast<double>(aMillimeters) / 1000.0;
}

int16_t VelocityToCms(double aMetersPerSecond)
{
    return static_cast<int16_t>(RoundClamp(aMetersPerSecond * 100.0, -32768.0, 32767.0));
}

double CmsToVelocity(int16_t aCentimetersPerSecond)
{
    return static_cast<double>(aCentimetersPerSecond) / 100.0;
}

uint16_t YawToU16(double aDegrees)
{
    const double turns = PythonMod(aDegrees, 360.0) / 360.0 * 65536.0;
    return static_cast<uint16_t>(RoundClamp(turns, 0.0, 65536.0) & 0xFFFF);
}

double U16ToYaw(uint16_t aValue)
{
    return static_cast<double>(aValue) * 360.0 / 65536.0;
}

int16_t PitchToI16(double aDegrees)
{
    return static_cast<int16_t>(RoundClamp(aDegrees * 100.0, -9000.0, 9000.0));
}

double I16ToPitch(int16_t aValue)
{
    return static_cast<double>(aValue) / 100.0;
}

uint32_t PackQuat(double aX, double aY, double aZ, double aW)
{
    std::array<double, 4> components{aX, aY, aZ, aW};
    double length = std::sqrt(PythonSum(std::array<double, 4>{aX * aX, aY * aY, aZ * aZ, aW * aW}));
    if (!std::isfinite(length) || length < 1e-6)
    {
        components = {0.0, 0.0, 0.0, 1.0};
        length = 1.0;
    }
    for (double& component : components)
    {
        component /= length;
    }
    size_t largest = 0;
    for (size_t index = 1; index < components.size(); ++index)
    {
        if (std::fabs(components[index]) > std::fabs(components[largest]))
        {
            largest = index;
        }
    }
    if (components[largest] < 0.0)
    {
        for (double& component : components)
        {
            component = -component;
        }
    }
    uint32_t packed = static_cast<uint32_t>(largest);
    for (size_t index = 0; index < components.size(); ++index)
    {
        if (index == largest)
        {
            continue;
        }
        double normalized = components[index] / kQuatScale * 0.5 + 0.5;
        normalized = normalized < 0.0 ? 0.0 : normalized > 1.0 ? 1.0 : normalized;
        packed = (packed << 10) | static_cast<uint32_t>(RoundClamp(normalized * 1023.0, 0.0, 1023.0));
    }
    return packed;
}

std::array<double, 4> UnpackQuat(uint32_t aPacked)
{
    const size_t largest = (aPacked >> 30) & 0x3;
    std::array<double, 3> small{};
    const int shifts[3] = {20, 10, 0};
    for (size_t index = 0; index < small.size(); ++index)
    {
        const double value = static_cast<double>((aPacked >> shifts[index]) & 0x3FF) / 1023.0;
        small[index] = (value - 0.5) * 2.0 * kQuatScale;
    }
    const double sum = PythonSum(std::array<double, 3>{small[0] * small[0], small[1] * small[1], small[2] * small[2]});
    const double missing = std::sqrt(std::max(0.0, 1.0 - sum));
    std::array<double, 4> result{};
    size_t next = 0;
    for (size_t index = 0; index < result.size(); ++index)
    {
        result[index] = index == largest ? missing : small[next++];
    }
    return result;
}

// ---- text ----------------------------------------------------------------------------------------

bool IsValidText(std::string_view aText, TextRule aRule)
{
    size_t index = 0;
    while (index < aText.size())
    {
        uint32_t codePoint = 0;
        const size_t length = DecodeUtf8(aText, index, codePoint);
        if (length == 0)
        {
            return false;
        }
        if (aRule == TextRule::Strict ? ForbiddenStrict(codePoint) : codePoint == 0)
        {
            return false;
        }
        index += length;
    }
    return true;
}

bool IsValidRoom(std::string_view aRoom)
{
    if (aRoom.empty() || aRoom.size() > kMaxRoomBytes)
    {
        return false;
    }
    return std::all_of(aRoom.begin(), aRoom.end(), [](char aChar) {
        return (aChar >= 'A' && aChar <= 'Z') || (aChar >= 'a' && aChar <= 'z') || (aChar >= '0' && aChar <= '9') ||
               aChar == '_' || aChar == '-';
    });
}

// ---- packets -------------------------------------------------------------------------------------

bool IsV2(ByteSpan aDatagram)
{
    return aDatagram.size() >= 2 && aDatagram[0] == coopv2::kMagic0 && aDatagram[1] == coopv2::kMagic1;
}

Status DecodePacket(ByteSpan aDatagram, DecodedPacket& aOut)
{
    if (aDatagram.size() > coopv2::kMaxPacket)
    {
        return Status::TooLarge;
    }
    if (aDatagram.size() < 4 || !IsV2(aDatagram))
    {
        return Status::NotV2;
    }
    if (aDatagram[2] != coopv2::kProtoMajor)
    {
        aOut.major = aDatagram[2];
        aOut.header.type = aDatagram[3];
        return Status::VersionMismatch;
    }
    coopv2::PacketHeader header{};
    if (aDatagram.size() < sizeof(header))
    {
        return Status::Truncated;
    }
    std::memcpy(&header, aDatagram.data(), sizeof(header));
    if (header.ptype < 1 || header.ptype > kPacketTypeCount)
    {
        return Status::UnknownPacketType;
    }
    aOut.major = header.major;
    aOut.header = PacketInfo{header.ptype, header.token, header.seq, header.ack, header.ack_bits};
    aOut.body = aDatagram.subspan(sizeof(header));
    return Status::Ok;
}

Status EncodePacket(const PacketInfo& aHeader, ByteSpan aBody, Bytes& aOut)
{
    aOut.clear();
    if (aHeader.type < 1 || aHeader.type > kPacketTypeCount)
    {
        return Status::UnknownPacketType;
    }
    if (kPacketHeaderSize + aBody.size() > coopv2::kMaxPacket)
    {
        return Status::TooLarge;
    }
    const coopv2::PacketHeader header{{coopv2::kMagic0, coopv2::kMagic1}, coopv2::kProtoMajor, aHeader.type,
                                      aHeader.token, aHeader.seq, aHeader.ack, aHeader.ackBits};
    aOut.reserve(kPacketHeaderSize + aBody.size());
    Put(aOut, header);
    aOut.insert(aOut.end(), aBody.begin(), aBody.end());
    return Status::Ok;
}

// ---- handshake -----------------------------------------------------------------------------------

Status DecodeJoin(ByteSpan aBody, size_t& aOffset, JoinInfo& aOut)
{
    if (aOffset > aBody.size() || aBody.size() - aOffset < sizeof(coopv2::JoinFixed) + 2)
    {
        return Status::Truncated;
    }
    Reader reader(aBody, aOffset);
    reader.Get(aOut.fixed);
    for (std::string* text : {&aOut.room, &aOut.name})
    {
        const size_t limit = text == &aOut.room ? kMaxRoomBytes : kMaxNameBytes;
        uint8_t length = 0;
        if (!reader.Get(length) || !reader.Text(length, *text))
        {
            return Status::Truncated;
        }
        if (text->size() > limit || !IsValidText(*text, TextRule::Strict))
        {
            return Status::BadText;
        }
    }
    if (!IsValidRoom(aOut.room))
    {
        return Status::BadRoom;
    }
    if (aOut.fixed.role > static_cast<uint8_t>(coopv2::Role::Spectator))
    {
        return Status::BadValue;
    }
    aOffset = reader.Offset();
    return Status::Ok;
}

Status EncodeJoin(const JoinInfo& aJoin, Bytes& aOut)
{
    if (!IsValidRoom(aJoin.room))
    {
        return Status::BadRoom;
    }
    if (aJoin.name.size() > kMaxNameBytes || !IsValidText(aJoin.name, TextRule::Strict))
    {
        return Status::BadText;
    }
    if (aJoin.fixed.role > static_cast<uint8_t>(coopv2::Role::Spectator))
    {
        return Status::BadValue;
    }
    Put(aOut, aJoin.fixed);
    Put(aOut, static_cast<uint8_t>(aJoin.room.size()));
    PutText(aOut, aJoin.room);
    Put(aOut, static_cast<uint8_t>(aJoin.name.size()));
    PutText(aOut, aJoin.name);
    return Status::Ok;
}

Status DecodeHello(ByteSpan aBody, JoinInfo& aOut, size_t* aPaddingBytes)
{
    if (aBody.size() + kPacketHeaderSize < coopv2::kHelloMinPacket)
    {
        return Status::BadPadding;
    }
    size_t offset = 0;
    if (const Status status = DecodeJoin(aBody, offset, aOut); status != Status::Ok)
    {
        return status;
    }
    if (std::any_of(aBody.begin() + static_cast<std::ptrdiff_t>(offset), aBody.end(),
                    [](uint8_t aByte) { return aByte != 0; }))
    {
        return Status::BadPadding;
    }
    if (aPaddingBytes != nullptr)
    {
        *aPaddingBytes = aBody.size() - offset;
    }
    return Status::Ok;
}

Status EncodeHello(const JoinInfo& aJoin, Bytes& aOut)
{
    aOut.clear();
    Bytes body;
    if (const Status status = EncodeJoin(aJoin, body); status != Status::Ok)
    {
        return status;
    }
    const size_t minimumBody = coopv2::kHelloMinPacket - kPacketHeaderSize;
    if (body.size() < minimumBody)
    {
        body.resize(minimumBody, 0);
    }
    return FinishPacket(HandshakeHeader(coopv2::PacketType::Hello), body, aOut);
}

Status DecodeChallenge(ByteSpan aBody, coopv2::Challenge& aOut)
{
    return aBody.size() == sizeof(aOut) ? ReadExact(aBody, aOut) : Status::BadSize;
}

Status EncodeChallenge(const coopv2::Challenge& aChallenge, Bytes& aOut)
{
    Bytes body;
    Put(body, aChallenge);
    return FinishPacket(HandshakeHeader(coopv2::PacketType::Challenge), body, aOut);
}

Status DecodeAuth(ByteSpan aBody, AuthInfo& aOut)
{
    size_t offset = 0;
    if (const Status status = DecodeJoin(aBody, offset, aOut.join); status != Status::Ok)
    {
        return status;
    }
    if (aBody.size() - offset != kCookieSize + kKeyHashSize)
    {
        return Status::BadSize;
    }
    std::memcpy(aOut.cookie.data(), aBody.data() + offset, kCookieSize);
    std::memcpy(aOut.keyHash.data(), aBody.data() + offset + kCookieSize, kKeyHashSize);
    return Status::Ok;
}

Status EncodeAuth(const AuthInfo& aAuth, Bytes& aOut)
{
    aOut.clear();
    Bytes body;
    if (const Status status = EncodeJoin(aAuth.join, body); status != Status::Ok)
    {
        return status;
    }
    body.insert(body.end(), aAuth.cookie.begin(), aAuth.cookie.end());
    body.insert(body.end(), aAuth.keyHash.begin(), aAuth.keyHash.end());
    return FinishPacket(HandshakeHeader(coopv2::PacketType::Auth), body, aOut);
}

Status DecodeWelcome(ByteSpan aBody, coopv2::Welcome& aOut)
{
    return aBody.size() == sizeof(aOut) ? ReadExact(aBody, aOut) : Status::BadSize;
}

Status EncodeWelcome(const coopv2::Welcome& aWelcome, Bytes& aOut)
{
    Bytes body;
    Put(body, aWelcome);
    return FinishPacket(HandshakeHeader(coopv2::PacketType::Welcome, aWelcome.token), body, aOut);
}

Status DecodeReject(ByteSpan aBody, RejectInfo& aOut)
{
    Reader reader(aBody);
    coopv2::RejectFixed fixed{};
    if (!reader.Get(fixed))
    {
        return Status::Truncated;
    }
    aOut.reason = fixed.reason;
    aOut.minorMin = fixed.minor_min;
    aOut.minorMax = fixed.minor_max;
    // proto.py slices the text and ignores whatever follows it.
    const size_t length = std::min<size_t>(fixed.text_len, reader.Remaining());
    reader.Text(length, aOut.text);
    return Status::Ok;
}

Status EncodeReject(const RejectInfo& aReject, Bytes& aOut)
{
    const std::string_view text = std::string_view(aReject.text).substr(0, kMaxRejectTextBytes);
    Bytes body;
    Put(body, coopv2::RejectFixed{aReject.reason, aReject.minorMin, aReject.minorMax, static_cast<uint8_t>(text.size())});
    PutText(body, text);
    return FinishPacket(HandshakeHeader(coopv2::PacketType::Reject), body, aOut);
}

Status DecodeDisconnect(ByteSpan aBody, uint8_t& aReason)
{
    if (aBody.size() != 1)
    {
        return Status::BadSize;
    }
    aReason = aBody[0];
    return Status::Ok;
}

Status EncodeDisconnect(uint64_t aToken, uint8_t aReason, Bytes& aOut)
{
    const Bytes body{aReason};
    return FinishPacket(HandshakeHeader(coopv2::PacketType::Disconnect, aToken), body, aOut);
}

// ---- message framing -----------------------------------------------------------------------------

Status DecodeMessages(ByteSpan aPayload, std::vector<MessageView>& aOut)
{
    aOut.clear();
    Reader reader(aPayload);
    while (reader.Remaining() > 0)
    {
        coopv2::MessageHeader header{};
        if (!reader.Get(header))
        {
            return Status::Truncated;
        }
        MessageView message;
        message.type = static_cast<uint8_t>(header.type & 0x7F);
        message.peer = header.peer;
        message.reliable = (header.type & coopv2::kReliableBit) != 0;
        if (message.reliable && !reader.Get(message.relSeq))
        {
            return Status::Truncated;
        }
        if (header.length > reader.Remaining())
        {
            return Status::Truncated;
        }
        message.body = aPayload.subspan(reader.Offset(), header.length);
        reader = Reader(aPayload, reader.Offset() + header.length);
        aOut.push_back(message);
        if (aOut.size() > kMaxMessagesPerPacket)
        {
            return Status::TooManyMessages;
        }
    }
    return Status::Ok;
}

Status AppendMessage(Bytes& aPayload, uint8_t aType, uint8_t aPeer, std::optional<uint16_t> aRelSeq, ByteSpan aBody)
{
    if (aType >= coopv2::kReliableBit)
    {
        return Status::BadValue;
    }
    if (aBody.size() > kMaxMessageBody)
    {
        return Status::TooLarge;
    }
    const uint8_t typeByte = static_cast<uint8_t>(aType | (aRelSeq ? coopv2::kReliableBit : 0));
    Put(aPayload, coopv2::MessageHeader{typeByte, aPeer, static_cast<uint16_t>(aBody.size())});
    if (aRelSeq)
    {
        Put(aPayload, *aRelSeq);
    }
    aPayload.insert(aPayload.end(), aBody.begin(), aBody.end());
    return Status::Ok;
}

size_t MessageSize(size_t aBodyBytes, bool aReliable)
{
    return kMessageHeaderSize + (aReliable ? kReliableSeqSize : 0) + aBodyBytes;
}

// ---- bodies --------------------------------------------------------------------------------------

size_t EntityRecordSize(const EntityRecord& aRecord)
{
    size_t size = 3;
    if (aRecord.remove || aRecord.target || aRecord.weapon || aRecord.worldId)
    {
        size += 1;
    }
    size += aRecord.spawn ? sizeof(coopv2::EntitySpawn) : 0;
    size += aRecord.pos ? 12 : 0;
    size += aRecord.posDelta ? 6 : 0;
    size += aRecord.yaw ? 2 : 0;
    size += aRecord.quat ? 4 : 0;
    size += aRecord.vel ? 6 : 0;
    size += aRecord.state ? 3 : 0;
    size += aRecord.target ? 2 : 0;
    size += aRecord.weapon ? 8 : 0;
    size += aRecord.worldId ? 8 : 0;
    return size;
}

uint8_t TypeOf(const Body& aBody)
{
    return std::visit(
        [](const auto& aValue) -> uint8_t {
            using T = std::decay_t<decltype(aValue)>;
            MsgType type{};
            if constexpr (std::is_same_v<T, coopv2::TimeReq>) type = MsgType::TimeReq;
            else if constexpr (std::is_same_v<T, coopv2::TimeResp>) type = MsgType::TimeResp;
            else if constexpr (std::is_same_v<T, PeerJoinedMsg>) type = MsgType::PeerJoined;
            else if constexpr (std::is_same_v<T, coopv2::PeerLeft>) type = MsgType::PeerLeft;
            else if constexpr (std::is_same_v<T, coopv2::LinkStats>) type = MsgType::LinkStats;
            else if constexpr (std::is_same_v<T, PlayerSnapshotMsg>) type = MsgType::PlayerSnapshot;
            else if constexpr (std::is_same_v<T, EntitySnapshotMsg>) type = MsgType::EntitySnapshot;
            else if constexpr (std::is_same_v<T, coopv2::SnapshotAck>) type = MsgType::SnapshotAck;
            else if constexpr (std::is_same_v<T, coopv2::FireFx>) type = MsgType::FireFx;
            else if constexpr (std::is_same_v<T, coopv2::Equip>) type = MsgType::Equip;
            else if constexpr (std::is_same_v<T, coopv2::VehicleEnter>) type = MsgType::VehicleEnter;
            else if constexpr (std::is_same_v<T, coopv2::VehicleExit>) type = MsgType::VehicleExit;
            else if constexpr (std::is_same_v<T, coopv2::Hit>) type = MsgType::Hit;
            else if constexpr (std::is_same_v<T, coopv2::Death>) type = MsgType::Death;
            else if constexpr (std::is_same_v<T, coopv2::TimeWeather>) type = MsgType::TimeWeather;
            else if constexpr (std::is_same_v<T, ChatMsg>) type = MsgType::Chat;
            else if constexpr (std::is_same_v<T, coopv2::TeleportReq>) type = MsgType::TeleportReq;
            else if constexpr (std::is_same_v<T, coopv2::TeleportResp>) type = MsgType::TeleportResp;
            else if constexpr (std::is_same_v<T, coopv2::WorldFact>) type = MsgType::WorldFact;
            else if constexpr (std::is_same_v<T, ModListMsg>) type = MsgType::ModList;
            else if constexpr (std::is_same_v<T, coopv2::SessionConfig>) type = MsgType::SessionConfig;
            else if constexpr (std::is_same_v<T, ScriptMsg>) type = MsgType::ScriptMsg;
            return static_cast<uint8_t>(type);
        },
        aBody);
}

Status DecodeBody(uint8_t aType, ByteSpan aBody, Body& aOut)
{
    switch (static_cast<MsgType>(aType))
    {
    case MsgType::TimeReq: return DecodeFixed<coopv2::TimeReq>(aBody, aOut);
    case MsgType::TimeResp: return DecodeFixed<coopv2::TimeResp>(aBody, aOut);
    case MsgType::PeerJoined:
    {
        PeerJoinedMsg value;
        const Status status = DecodePeerJoined(aBody, value);
        if (status == Status::Ok)
        {
            aOut = std::move(value);
        }
        return status;
    }
    case MsgType::PeerLeft: return DecodeFixed<coopv2::PeerLeft>(aBody, aOut);
    case MsgType::LinkStats: return DecodeFixed<coopv2::LinkStats>(aBody, aOut);
    case MsgType::PlayerSnapshot:
    {
        PlayerSnapshotMsg value;
        const Status status = DecodePlayer(aBody, value);
        if (status == Status::Ok)
        {
            aOut = value;
        }
        return status;
    }
    case MsgType::EntitySnapshot:
    {
        EntitySnapshotMsg value;
        const Status status = DecodeEntity(aBody, value);
        if (status == Status::Ok)
        {
            aOut = std::move(value);
        }
        return status;
    }
    case MsgType::SnapshotAck: return DecodeFixed<coopv2::SnapshotAck>(aBody, aOut);
    case MsgType::FireFx: return DecodeFixed<coopv2::FireFx>(aBody, aOut);
    case MsgType::Equip: return DecodeFixed<coopv2::Equip>(aBody, aOut, &CheckEquip);
    case MsgType::VehicleEnter: return DecodeFixed<coopv2::VehicleEnter>(aBody, aOut, &CheckVehicleEnter);
    case MsgType::VehicleExit: return DecodeFixed<coopv2::VehicleExit>(aBody, aOut, &CheckVehicleExit);
    case MsgType::Hit: return DecodeFixed<coopv2::Hit>(aBody, aOut, &CheckHit);
    case MsgType::Death: return DecodeFixed<coopv2::Death>(aBody, aOut, &CheckDeath);
    case MsgType::TimeWeather: return DecodeFixed<coopv2::TimeWeather>(aBody, aOut, &CheckTimeWeather);
    case MsgType::Chat:
    {
        coopv2::ChatFixed fixed{};
        ChatMsg value;
        const Status status = ReadWithText<uint8_t>(aBody, fixed, value.text, kMaxChatBytes, TextRule::Strict);
        if (status == Status::Ok)
        {
            value.channel = fixed.channel;
            aOut = std::move(value);
        }
        return status;
    }
    case MsgType::TeleportReq: return DecodeFixed<coopv2::TeleportReq>(aBody, aOut, &CheckTeleportReq);
    case MsgType::TeleportResp: return DecodeFixed<coopv2::TeleportResp>(aBody, aOut, &CheckTeleportResp);
    case MsgType::WorldFact: return DecodeFixed<coopv2::WorldFact>(aBody, aOut);
    case MsgType::ModList:
    {
        coopv2::ModListFixed fixed{};
        ModListMsg value;
        Status status = ReadWithText<uint8_t>(aBody, fixed, value.text, kMaxModListTextBytes, TextRule::Strict);
        if (status == Status::Ok && !(fixed.chunk < fixed.chunks && fixed.chunks <= 16))
        {
            status = Status::BadValue;
        }
        if (status == Status::Ok)
        {
            value.chunk = fixed.chunk;
            value.chunks = fixed.chunks;
            aOut = std::move(value);
        }
        return status;
    }
    case MsgType::SessionConfig: return DecodeFixed<coopv2::SessionConfig>(aBody, aOut, &CheckSessionConfig);
    case MsgType::ScriptMsg:
    {
        coopv2::ScriptMsgFixed fixed{};
        ScriptMsg value;
        Status status = ReadWithText<uint16_t>(aBody, fixed, value.text, coopv2::kMaxScriptBytes, TextRule::Script);
        if (status == Status::Ok &&
            (fixed.channel < coopv2::kScriptFirstChannel || fixed.channel > coopv2::kScriptLastChannel))
        {
            status = Status::BadValue;
        }
        if (status == Status::Ok)
        {
            value.channel = fixed.channel;
            value.flags = fixed.flags;
            aOut = std::move(value);
        }
        return status;
    }
    }
    return Status::UnknownMessageType;
}

Status EncodeBody(const Body& aBody, Bytes& aOut)
{
    aOut.clear();
    Status status = EncodeRaw(aBody, aOut);
    if (status == Status::Ok)
    {
        // Like proto.py: never emit what a receiver would reject.
        Body check;
        status = DecodeBody(TypeOf(aBody), AsSpan(aOut), check);
    }
    if (status != Status::Ok)
    {
        aOut.clear();
    }
    return status;
}

// ---- message table -------------------------------------------------------------------------------

std::span<const MsgSpec> MessageSpecs()
{
    return kSpecs;
}

const MsgSpec* FindSpec(uint8_t aType)
{
    for (const MsgSpec& spec : kSpecs)
    {
        if (spec.type == aType)
        {
            return &spec;
        }
    }
    return nullptr;
}

bool ScriptChannelReliable(uint8_t aChannel)
{
    return aChannel >= coopv2::kScriptFirstReliableChannel;
}

bool DeliveryOk(const MsgSpec& aSpec, bool aReliable, const Body& aBody)
{
    switch (aSpec.delivery)
    {
    case Delivery::Unreliable: return !aReliable;
    case Delivery::Reliable: return aReliable;
    case Delivery::ByChannel:
        if (const auto* script = std::get_if<ScriptMsg>(&aBody))
        {
            return ScriptChannelReliable(script->channel) == aReliable;
        }
        return false;
    }
    return false;
}

uint8_t RequiredMinor(const Body& aBody)
{
    if (const auto* entity = std::get_if<EntitySnapshotMsg>(&aBody))
    {
        const bool worldIds = std::any_of(entity->records.begin(), entity->records.end(),
                                          [](const EntityRecord& aRecord) { return aRecord.worldId.has_value(); });
        if (worldIds)
        {
            return 1;
        }
    }
    const MsgSpec* spec = FindSpec(TypeOf(aBody));
    return spec != nullptr ? spec->minMinor : 0;
}

Status EncodeDataPacket(const PacketInfo& aHeader, std::span<const OutMessage> aMessages, Bytes& aOut)
{
    aOut.clear();
    if (aMessages.size() > kMaxMessagesPerPacket)
    {
        return Status::TooManyMessages;
    }
    Bytes payload;
    Bytes body;
    for (const OutMessage& message : aMessages)
    {
        if (const Status status = EncodeBody(message.body, body); status != Status::Ok)
        {
            return status;
        }
        if (const Status status = AppendMessage(payload, TypeOf(message.body), message.peer, message.relSeq, AsSpan(body));
            status != Status::Ok)
        {
            return status;
        }
    }
    PacketInfo header = aHeader;
    header.type = static_cast<uint8_t>(coopv2::PacketType::Data);
    return EncodePacket(header, AsSpan(payload), aOut);
}
} // namespace coopnet::v2
