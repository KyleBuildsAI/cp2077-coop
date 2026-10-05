#pragma once

// Shared by the v2 unit tests, the golden-vector tool and the fuzzer: a deterministic RNG,
// generators for random valid messages and datagrams, and datagram mutations.

#include "v2/V2Codec.hpp"
#include "v2/V2Delta.hpp"

#include <cstdint>
#include <string>
#include <vector>

namespace coopnet::v2::test
{
// SplitMix64: tiny, fast and the same sequence on every compiler.
class Rng
{
public:
    explicit Rng(uint64_t aSeed)
        : m_state(aSeed)
    {
    }

    uint64_t U64();
    uint32_t U32()
    {
        return static_cast<uint32_t>(U64() >> 32);
    }
    // Uniform in [0, aBound) (aBound > 0).
    uint64_t Below(uint64_t aBound);
    // Uniform in [aLow, aHigh].
    int64_t Range(int64_t aLow, int64_t aHigh);
    bool Chance(double aProbability);
    double Unit(); // [0, 1)
    // A float32 value in [-aLimit, aLimit], sometimes an edge value (0, -0, +-limit, denormal).
    float Coordinate(float aLimit);

private:
    uint64_t m_state;
};

// Valid UTF-8 text of at most aMaxBytes that IsValidText(aRule) accepts.
std::string RandomText(Rng& aRng, size_t aMaxBytes, TextRule aRule);
std::string RandomRoom(Rng& aRng);

JoinInfo RandomJoin(Rng& aRng);
EntityRecord RandomRecord(Rng& aRng, uint16_t aNetId);
// A valid body of the given message type. aMaxBytes bounds the encoded size (entity snapshots,
// text); every type fits in kMaxMessageBody.
Body RandomBody(Rng& aRng, uint8_t aType, size_t aMaxBytes = kMaxMessageBody);
uint8_t RandomMessageType(Rng& aRng);

// A valid datagram of any packet type (DATA most of the time).
Bytes RandomDatagram(Rng& aRng);
// 1..4 random edits: byte flips, overwrites, truncation, extension, insertion, deletion, and
// edits of the message header length/type fields.
Bytes Mutate(Rng& aRng, ByteSpan aDatagram);

// Decodes every layer of a datagram and encodes it again from the decoded values. Handshake
// packets get a zero header (as the encoders write them), DATA keeps its header.
Status Reencode(ByteSpan aDatagram, Bytes& aOut);

// A random entity population for delta tests (quantized states).
struct World
{
    explicit World(uint64_t aSeed, size_t aEntities);
    // Moves everything one tick; every 20 ticks a few entities leave and new ones join.
    void Step();
    EntityView States() const;
    std::map<uint16_t, double> Weights(Rng& aRng) const;

    Rng rng;
    uint32_t tick = 0;
    uint16_t nextId = 1;
    std::vector<std::pair<uint16_t, EntityState>> entities;
};
} // namespace coopnet::v2::test
