#include "v2/V2Delta.hpp"

#include "v2/V2Hash.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <limits>

namespace coopnet::v2
{
namespace
{
constexpr std::array<int16_t, 3> kDefaultVel{0, 0, 0};
constexpr std::array<uint8_t, 3> kDefaultState{0, 0, 255};

template<class T>
void Append(std::vector<uint8_t>& aOut, T aValue)
{
    uint8_t bytes[sizeof(T)];
    std::memcpy(bytes, &aValue, sizeof(T));
    aOut.insert(aOut.end(), bytes, bytes + sizeof(T));
}

bool InWorld(const std::array<int32_t, 3>& aPos)
{
    return std::fabs(MmToMeters(aPos[0])) <= 20000.0 && std::fabs(MmToMeters(aPos[1])) <= 20000.0 &&
           std::fabs(MmToMeters(aPos[2])) <= 5000.0;
}

void SetRotation(EntityRecord& aRecord, const EntityState& aState)
{
    if (aState.UsesQuat())
    {
        aRecord.quat = aState.rot;
    }
    else
    {
        aRecord.yaw = static_cast<uint16_t>(aState.rot);
    }
}

void PruneHistory(std::map<uint32_t, EntityView>& aHistory, uint32_t aNewest, std::optional<uint32_t> aKeep)
{
    const int64_t limit = static_cast<int64_t>(aNewest) - kHistoryTicks;
    for (auto it = aHistory.begin(); it != aHistory.end();)
    {
        if (static_cast<int64_t>(it->first) <= limit && (!aKeep || it->first != *aKeep))
        {
            it = aHistory.erase(it);
        }
        else
        {
            ++it;
        }
    }
}
} // namespace

bool EntityState::UsesQuat() const
{
    return kind == static_cast<uint8_t>(coopv2::EntityKind::Vehicle);
}

bool EntityState::SameIdentity(const EntityState& aOther) const
{
    return kind == aOther.kind && spawnFlags == aOther.spawnFlags && attitude == aOther.attitude &&
           record == aOther.record && appearance == aOther.appearance && worldId == aOther.worldId;
}

EntityState Quantize(const EntityInput& aInput)
{
    EntityState state;
    state.kind = aInput.kind;
    state.spawnFlags = aInput.spawnFlags;
    state.attitude = aInput.attitude;
    state.record = aInput.record;
    state.appearance = aInput.appearance;
    state.pos = {MetersToMm(aInput.posMeters[0]), MetersToMm(aInput.posMeters[1]), MetersToMm(aInput.posMeters[2])};
    state.rot = state.UsesQuat() ? PackQuat(aInput.quat[0], aInput.quat[1], aInput.quat[2], aInput.quat[3])
                                 : YawToU16(aInput.yawDegrees);
    state.vel = {VelocityToCms(aInput.velMps[0]), VelocityToCms(aInput.velMps[1]), VelocityToCms(aInput.velMps[2])};
    state.state = {aInput.moveState, aInput.flags, aInput.health};
    state.target = aInput.target;
    state.weapon = aInput.weapon;
    state.worldId = aInput.worldId;
    return state;
}

// ---- view ----------------------------------------------------------------------------------------

const EntityState* EntityView::Find(uint16_t aNetId) const
{
    for (const Entry& entry : m_entries)
    {
        if (entry.first == aNetId)
        {
            return &entry.second;
        }
    }
    return nullptr;
}

void EntityView::Set(uint16_t aNetId, const EntityState& aState)
{
    for (Entry& entry : m_entries)
    {
        if (entry.first == aNetId)
        {
            entry.second = aState;
            return;
        }
    }
    m_entries.emplace_back(aNetId, aState);
}

bool EntityView::Erase(uint16_t aNetId)
{
    const auto it = std::find_if(m_entries.begin(), m_entries.end(),
                                 [aNetId](const Entry& aEntry) { return aEntry.first == aNetId; });
    if (it == m_entries.end())
    {
        return false;
    }
    m_entries.erase(it);
    return true;
}

std::string ViewHash(const EntityView& aView)
{
    std::vector<const EntityView::Entry*> sorted;
    sorted.reserve(aView.Size());
    for (const auto& entry : aView.Entries())
    {
        sorted.push_back(&entry);
    }
    std::sort(sorted.begin(), sorted.end(), [](const auto* aLeft, const auto* aRight) { return aLeft->first < aRight->first; });
    Sha256 hash;
    std::vector<uint8_t> packed;
    for (const auto* entry : sorted)
    {
        // snapshot.py VIEW_HASH_LAYOUT "<HBBBQQiiiIhhhBBBHQQ"
        const EntityState& state = entry->second;
        packed.clear();
        Append(packed, entry->first);
        Append(packed, state.kind);
        Append(packed, state.spawnFlags);
        Append(packed, state.attitude);
        Append(packed, state.record);
        Append(packed, state.appearance);
        for (const int32_t value : state.pos)
        {
            Append(packed, value);
        }
        Append(packed, state.rot);
        for (const int16_t value : state.vel)
        {
            Append(packed, value);
        }
        for (const uint8_t value : state.state)
        {
            Append(packed, value);
        }
        Append(packed, state.target);
        Append(packed, state.weapon);
        Append(packed, state.worldId);
        hash.Update(packed);
    }
    const Sha256Digest digest = hash.Finish();
    static constexpr char kDigits[] = "0123456789abcdef";
    std::string text;
    for (size_t index = 0; index < 8; ++index)
    {
        text += kDigits[digest[index] >> 4];
        text += kDigits[digest[index] & 0xF];
    }
    return text;
}

// ---- records -------------------------------------------------------------------------------------

std::optional<EntityRecord> MakeRecord(uint16_t aNetId, const EntityState* aOld, const EntityState& aNew)
{
    EntityRecord record;
    record.netId = aNetId;
    if (aOld == nullptr || !aOld->SameIdentity(aNew))
    {
        record.spawn = EntitySpawnInfo{aNew.kind, aNew.spawnFlags, aNew.attitude, aNew.record, aNew.appearance};
        record.pos = aNew.pos;
        SetRotation(record, aNew);
        if (aNew.vel != kDefaultVel)
        {
            record.vel = aNew.vel;
        }
        if (aNew.state != kDefaultState)
        {
            record.state = aNew.state;
        }
        if (aNew.target != 0)
        {
            record.target = aNew.target;
        }
        if (aNew.weapon != 0)
        {
            record.weapon = aNew.weapon;
        }
        if (aNew.worldId != 0)
        {
            record.worldId = aNew.worldId;
        }
        return record;
    }
    bool changed = false;
    if (aNew.pos != aOld->pos)
    {
        std::array<int64_t, 3> delta{};
        bool fits = true;
        for (size_t axis = 0; axis < 3; ++axis)
        {
            delta[axis] = static_cast<int64_t>(aNew.pos[axis]) - aOld->pos[axis];
            fits = fits && delta[axis] >= -32768 && delta[axis] <= 32767;
        }
        if (fits)
        {
            record.posDelta = std::array<int16_t, 3>{static_cast<int16_t>(delta[0]), static_cast<int16_t>(delta[1]),
                                                     static_cast<int16_t>(delta[2])};
        }
        else
        {
            record.pos = aNew.pos;
        }
        changed = true;
    }
    if (aNew.rot != aOld->rot)
    {
        SetRotation(record, aNew);
        changed = true;
    }
    if (aNew.vel != aOld->vel)
    {
        record.vel = aNew.vel;
        changed = true;
    }
    if (aNew.state != aOld->state)
    {
        record.state = aNew.state;
        changed = true;
    }
    if (aNew.target != aOld->target)
    {
        record.target = aNew.target;
        changed = true;
    }
    if (aNew.weapon != aOld->weapon)
    {
        record.weapon = aNew.weapon;
        changed = true;
    }
    if (!changed)
    {
        return std::nullopt;
    }
    return record;
}

Status ApplyRecord(const EntityState* aOld, const EntityRecord& aRecord, EntityState& aOut)
{
    if (aRecord.spawn)
    {
        if (!aRecord.pos || (!aRecord.quat && !aRecord.yaw))
        {
            return Status::BadRecord;
        }
        EntityState state;
        state.kind = aRecord.spawn->kind;
        state.spawnFlags = aRecord.spawn->spawnFlags;
        state.attitude = aRecord.spawn->attitude;
        state.record = aRecord.spawn->record;
        state.appearance = aRecord.spawn->appearance;
        state.pos = *aRecord.pos;
        state.rot = aRecord.quat ? *aRecord.quat : *aRecord.yaw;
        state.vel = aRecord.vel.value_or(kDefaultVel);
        state.state = aRecord.state.value_or(kDefaultState);
        state.target = aRecord.target.value_or(0);
        state.weapon = aRecord.weapon.value_or(0);
        state.worldId = aRecord.worldId.value_or(0);
        aOut = state;
        return Status::Ok;
    }
    if (aOld == nullptr)
    {
        return Status::BadRecord; // delta for an entity the baseline does not have
    }
    EntityState state = *aOld;
    if (aRecord.pos)
    {
        state.pos = *aRecord.pos;
    }
    else if (aRecord.posDelta)
    {
        for (size_t axis = 0; axis < 3; ++axis)
        {
            const int64_t value = static_cast<int64_t>(aOld->pos[axis]) + (*aRecord.posDelta)[axis];
            if (value < std::numeric_limits<int32_t>::min() || value > std::numeric_limits<int32_t>::max())
            {
                return Status::OutOfWorld;
            }
            state.pos[axis] = static_cast<int32_t>(value);
        }
    }
    if (aRecord.yaw || aRecord.quat)
    {
        if (aRecord.quat.has_value() != aOld->UsesQuat())
        {
            return Status::BadRecord; // rotation type does not match the entity kind
        }
        state.rot = aRecord.quat ? *aRecord.quat : *aRecord.yaw;
    }
    state.vel = aRecord.vel.value_or(aOld->vel);
    state.state = aRecord.state.value_or(aOld->state);
    state.target = aRecord.target.value_or(aOld->target);
    state.weapon = aRecord.weapon.value_or(aOld->weapon);
    state.worldId = aRecord.worldId.value_or(aOld->worldId);
    aOut = state;
    return Status::Ok;
}

// ---- encoder -------------------------------------------------------------------------------------

DeltaEncoder::DeltaEncoder(size_t aBudgetBytes)
    : m_budget(aBudgetBytes)
{
}

void DeltaEncoder::OnAck(uint32_t aTick)
{
    if (m_acked < aTick && aTick <= m_tick && m_history.contains(aTick))
    {
        m_acked = aTick;
    }
}

size_t DeltaEncoder::FullSize(const EntityView& aStates)
{
    size_t size = sizeof(coopv2::EntitySnapshotHeader);
    for (const auto& [netId, state] : aStates.Entries())
    {
        size += EntityRecordSize(*MakeRecord(netId, nullptr, state));
    }
    return size;
}

Status DeltaEncoder::Encode(uint32_t aSampleTime, const EntityView& aStates, const std::map<uint16_t, double>& aWeights,
                            EncodedSnapshot& aOut)
{
    for (const auto& [netId, state] : aStates.Entries())
    {
        if (netId == 0)
        {
            return Status::BadRecord;
        }
        if (!state.UsesQuat() && state.rot > 0xFFFF)
        {
            return Status::BadValue; // a yaw is 16 bits
        }
    }
    ++m_tick;
    const uint32_t baseline = m_history.contains(m_acked) ? m_acked : 0;
    static const EntityView kEmpty;
    const EntityView& baseView = baseline != 0 ? m_history.at(baseline) : kEmpty;
    EntityView view = baseView;
    std::vector<EntityRecord> records;
    size_t used = sizeof(coopv2::EntitySnapshotHeader);
    for (const auto& [netId, state] : baseView.Entries())
    {
        if (aStates.Find(netId) == nullptr)
        {
            EntityRecord removal;
            removal.netId = netId;
            removal.remove = true;
            used += EntityRecordSize(removal);
            records.push_back(removal);
            view.Erase(netId);
            ++m_stats.removals;
        }
    }
    for (const auto& entry : aStates.Entries())
    {
        const auto weight = aWeights.find(entry.first);
        m_priority[entry.first] += weight != aWeights.end() ? weight->second : 1.0;
    }
    for (auto it = m_priority.begin(); it != m_priority.end();)
    {
        it = aStates.Find(it->first) == nullptr ? m_priority.erase(it) : std::next(it);
    }
    std::vector<uint16_t> order;
    order.reserve(aStates.Size());
    for (const auto& entry : aStates.Entries())
    {
        order.push_back(entry.first);
    }
    std::sort(order.begin(), order.end(), [this](uint16_t aLeft, uint16_t aRight) {
        const double left = m_priority[aLeft];
        const double right = m_priority[aRight];
        return left != right ? left > right : aLeft < aRight;
    });
    for (const uint16_t netId : order)
    {
        const EntityState& state = *aStates.Find(netId);
        const std::optional<EntityRecord> record = MakeRecord(netId, baseView.Find(netId), state);
        if (!record)
        {
            m_priority[netId] = 0.0;
            continue;
        }
        const size_t size = EntityRecordSize(*record);
        if (used + size > m_budget || records.size() >= kMaxEntityRecords)
        {
            ++m_stats.deferred;
            continue;
        }
        records.push_back(*record);
        used += size;
        view.Set(netId, state);
        m_priority[netId] = 0.0;
        if (record->spawn)
        {
            ++m_stats.spawns;
        }
    }
    const size_t recordCount = records.size();
    const Body body = EntitySnapshotMsg{m_tick, baseline, aSampleTime, std::move(records)};
    if (const Status status = EncodeBody(body, aOut.body); status != Status::Ok)
    {
        return status;
    }
    m_history[m_tick] = view;
    PruneHistory(m_history, m_tick, m_acked);
    ++m_stats.snapshots;
    m_stats.bytes += aOut.body.size();
    m_stats.records += recordCount;
    m_stats.fullBytes += FullSize(aStates);
    if (baseline == 0)
    {
        ++m_stats.fullSnapshots;
    }
    aOut.tick = m_tick;
    aOut.baseline = baseline;
    aOut.view = std::move(view);
    return Status::Ok;
}

// ---- decoder -------------------------------------------------------------------------------------

const EntityView* DeltaDecoder::Apply(const EntitySnapshotMsg& aSnapshot)
{
    const uint32_t tick = aSnapshot.tick;
    if (m_history.contains(tick))
    {
        ++m_stats.duplicate;
        return nullptr;
    }
    if (static_cast<int64_t>(tick) <= static_cast<int64_t>(m_latest) - kHistoryTicks)
    {
        ++m_stats.stale;
        return nullptr;
    }
    static const EntityView kEmpty;
    const EntityView* base = &kEmpty;
    if (aSnapshot.baseline != 0)
    {
        const auto found = m_history.find(aSnapshot.baseline);
        if (found == m_history.end())
        {
            ++m_stats.missingBaseline;
            return nullptr;
        }
        base = &found->second;
    }
    EntityView view = *base;
    for (const EntityRecord& record : aSnapshot.records)
    {
        if (record.remove)
        {
            view.Erase(record.netId);
            continue;
        }
        EntityState state;
        if (ApplyRecord(base->Find(record.netId), record, state) != Status::Ok || !InWorld(state.pos))
        {
            ++m_stats.inconsistent;
            return nullptr;
        }
        view.Set(record.netId, state);
    }
    m_latest = std::max(m_latest, tick);
    auto& stored = m_history[tick];
    stored = std::move(view);
    PruneHistory(m_history, m_latest, std::nullopt);
    ++m_stats.decoded;
    return &m_history.at(tick);
}
} // namespace coopnet::v2
