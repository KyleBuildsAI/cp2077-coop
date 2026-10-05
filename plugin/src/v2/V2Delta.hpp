#pragma once

// Delta-compressed entity snapshots, port of relay/coopnet/snapshot.py (EntityState, quantize,
// make_record, apply_record, view_hash, DeltaEncoder, DeltaDecoder).
//
// The host numbers snapshots with a tick and remembers, per tick, the view a receiver holding that
// snapshot reconstructs. Each snapshot is encoded against the newest tick the receiver confirmed
// with SNAPSHOT_ACK, or against nothing (baseline 0). Absent entities are unchanged; removals are
// explicit. A byte budget bounds every snapshot; entities go out in priority-accumulator order and
// unsent changes wait. Given the same inputs the encoder produces the same bytes as proto.py's
// DeltaEncoder (tools/v2_golden.py checks this both ways).

#include "v2/V2Codec.hpp"

#include <map>
#include <string>
#include <utility>
#include <vector>

namespace coopnet::v2
{
inline constexpr uint32_t kHistoryTicks = 64;
inline constexpr size_t kDefaultSnapshotBudget = 1000;

struct EntityState
{
    uint8_t kind = 0;
    uint8_t spawnFlags = 0;
    uint8_t attitude = 0;
    uint64_t record = 0;
    uint64_t appearance = 0;
    std::array<int32_t, 3> pos{}; // mm
    uint32_t rot = 0;             // yaw u16, or a smallest-three quaternion for vehicles
    std::array<int16_t, 3> vel{}; // cm/s
    std::array<uint8_t, 3> state{0, 0, 255};
    uint16_t target = 0;
    uint64_t weapon = 0;
    uint64_t worldId = 0; // static world id (protocol minor 1), 0 = dynamic

    [[nodiscard]] bool UsesQuat() const;
    [[nodiscard]] bool SameIdentity(const EntityState& aOther) const;
    bool operator==(const EntityState&) const = default;
};

struct EntityInput
{
    uint8_t kind = 0;
    uint8_t spawnFlags = 0;
    uint8_t attitude = 0;
    uint64_t record = 0;
    uint64_t appearance = 0;
    std::array<double, 3> posMeters{};
    double yawDegrees = 0.0;                    // used unless kind is Vehicle
    std::array<double, 4> quat{0.0, 0.0, 0.0, 1.0}; // x, y, z, w; used for vehicles
    std::array<double, 3> velMps{};
    uint8_t moveState = 0;
    uint8_t flags = 0;
    uint8_t health = 255;
    uint16_t target = 0;
    uint64_t weapon = 0;
    uint64_t worldId = 0;
};

EntityState Quantize(const EntityInput& aInput);

// net_id -> state, in insertion order (the order matters for the order of removal records).
class EntityView
{
public:
    using Entry = std::pair<uint16_t, EntityState>;

    [[nodiscard]] const EntityState* Find(uint16_t aNetId) const;
    void Set(uint16_t aNetId, const EntityState& aState); // keeps the position of an existing entry
    bool Erase(uint16_t aNetId);
    [[nodiscard]] size_t Size() const
    {
        return m_entries.size();
    }
    [[nodiscard]] const std::vector<Entry>& Entries() const
    {
        return m_entries;
    }
    bool operator==(const EntityView&) const = default;

private:
    std::vector<Entry> m_entries;
};

// First 16 hex digits of SHA-256 over the view sorted by net_id (snapshot.py view_hash).
std::string ViewHash(const EntityView& aView);

// The smallest record that turns aOld (the baseline view, or nullptr) into aNew; empty when unchanged.
std::optional<EntityRecord> MakeRecord(uint16_t aNetId, const EntityState* aOld, const EntityState& aNew);
// BadRecord for a delta to an unknown entity or a rotation that does not match the entity kind.
Status ApplyRecord(const EntityState* aOld, const EntityRecord& aRecord, EntityState& aOut);

struct DeltaEncoderStats
{
    uint64_t snapshots = 0;
    uint64_t bytes = 0;
    uint64_t fullBytes = 0;
    uint64_t records = 0;
    uint64_t deferred = 0;
    uint64_t fullSnapshots = 0;
    uint64_t removals = 0;
    uint64_t spawns = 0;
};

struct EncodedSnapshot
{
    Bytes body; // ENTITY_SNAPSHOT message body
    uint32_t tick = 0;
    uint32_t baseline = 0;
    EntityView view; // what a receiver holding this snapshot reconstructs
};

class DeltaEncoder
{
public:
    explicit DeltaEncoder(size_t aBudgetBytes = kDefaultSnapshotBudget);

    void OnAck(uint32_t aTick);
    // aStates: the entities currently relevant to the receiver. aWeights: priority weights
    // (default 1.0 for an entity without one).
    Status Encode(uint32_t aSampleTime, const EntityView& aStates, const std::map<uint16_t, double>& aWeights,
                  EncodedSnapshot& aOut);

    [[nodiscard]] const DeltaEncoderStats& Stats() const
    {
        return m_stats;
    }
    [[nodiscard]] uint32_t Tick() const
    {
        return m_tick;
    }
    [[nodiscard]] uint32_t Acked() const
    {
        return m_acked;
    }

    // Bytes the same entities would cost without delta compression.
    static size_t FullSize(const EntityView& aStates);

private:
    size_t m_budget;
    uint32_t m_tick = 0;
    uint32_t m_acked = 0;
    std::map<uint32_t, EntityView> m_history;
    std::map<uint16_t, double> m_priority;
    DeltaEncoderStats m_stats;
};

struct DeltaDecoderStats
{
    uint64_t decoded = 0;
    uint64_t duplicate = 0;
    uint64_t missingBaseline = 0;
    uint64_t inconsistent = 0;
    uint64_t stale = 0;
};

class DeltaDecoder
{
public:
    // The reconstructed view, or nullptr when the snapshot is a duplicate, stale, refers to a
    // baseline this decoder does not hold, or does not apply cleanly. The pointer stays valid until
    // the next Apply.
    const EntityView* Apply(const EntitySnapshotMsg& aSnapshot);

    [[nodiscard]] const DeltaDecoderStats& Stats() const
    {
        return m_stats;
    }
    [[nodiscard]] uint32_t Latest() const
    {
        return m_latest;
    }

private:
    std::map<uint32_t, EntityView> m_history;
    uint32_t m_latest = 0;
    DeltaDecoderStats m_stats;
};
} // namespace coopnet::v2
