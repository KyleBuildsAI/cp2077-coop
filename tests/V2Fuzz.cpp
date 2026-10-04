// Fuzzes the v2 codec: random, mutated, truncated and oversized datagrams and message bodies.
//
//   coopnet_v2_fuzz [count=20000] [seed]
//
// Every input is copied into a heap block of exactly its size, so a read past the end is a
// heap-buffer-overflow under AddressSanitizer (the coopnet_v2_fuzz_asan build of this file uses
// /fsanitize=address). Without ASan the run still proves that nothing crashes or throws.
//
// Invariants checked for every input the decoder accepts:
// * each decoded body encodes again, and decoding that gives the same description;
// * the whole datagram re-encodes, and the re-encoded datagram describes the same (handshake
//   packets are compared without their header line, which the encoders write as zeros, and a
//   REJECT text longer than 64 bytes is cut by the encoder, as proto.py's encode_reject does);
// * decoded entity snapshots go through a DeltaDecoder without crashing: as sent, their spawns as
//   a full snapshot (baseline 0), and all records as a delta against that, so the record
//   application paths run too.

#include "V2TestSupport.hpp"
#include "v2/V2Codec.hpp"
#include "v2/V2Delta.hpp"
#include "v2/V2Describe.hpp"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <memory>
#include <string>
#include <vector>

using namespace coopnet::v2;

namespace
{
struct Stats
{
    int inputs = 0;
    int accepted = 0;
    int bodiesAccepted = 0;
    int bodiesRejected = 0;
    int deltaApplied = 0;
    int invariantFailures = 0;
    std::map<std::string, int> outcomes;
};

void InvariantFailed(Stats& aStats, const std::string& aWhat, ByteSpan aInput)
{
    ++aStats.invariantFailures;
    if (aStats.invariantFailures <= 10)
    {
        std::printf("  INVARIANT FAILED: %s\n    input %s\n", aWhat.c_str(), Hex(aInput).substr(0, 200).c_str());
    }
}

// Decode -> encode -> decode must describe the same body.
void CheckBodyRoundTrip(Stats& aStats, uint8_t aType, const Body& aBody, ByteSpan aInput)
{
    Bytes encoded;
    if (const Status status = EncodeBody(aBody, encoded); status != Status::Ok)
    {
        InvariantFailed(aStats, std::string("decoded body does not encode: ") + ToString(status), aInput);
        return;
    }
    Body again;
    if (DecodeBody(aType, ByteSpan(encoded.data(), encoded.size()), again) != Status::Ok ||
        DescribeBody(again) != DescribeBody(aBody))
    {
        InvariantFailed(aStats, "body round trip changed " + DescribeBody(aBody).substr(0, 120), aInput);
    }
}

void Exercise(Stats& aStats, ByteSpan aInput, DeltaDecoder& aDecoder)
{
    ++aStats.inputs;
    std::vector<std::string> lines;
    const Status status = DescribeDatagram(aInput, lines);
    ++aStats.outcomes[ToString(status)];
    if (status != Status::Ok)
    {
        return;
    }
    ++aStats.accepted;
    DecodedPacket packet;
    DecodePacket(aInput, packet);
    if (packet.header.type == static_cast<uint8_t>(coopv2::PacketType::Data))
    {
        std::vector<MessageView> messages;
        DecodeMessages(packet.body, messages);
        for (const MessageView& message : messages)
        {
            Body body;
            if (DecodeBody(message.type, message.body, body) != Status::Ok)
            {
                InvariantFailed(aStats, "accepted datagram has a body that does not decode", aInput);
                continue;
            }
            CheckBodyRoundTrip(aStats, message.type, body, aInput);
            if (const auto* entity = std::get_if<EntitySnapshotMsg>(&body))
            {
                aDecoder.Apply(*entity);
                // The spawns as a full snapshot, then the whole record list as a delta against it.
                EntitySnapshotMsg full{entity->tick + 1, 0, entity->sampleTime, {}};
                for (const EntityRecord& record : entity->records)
                {
                    if (record.spawn)
                    {
                        full.records.push_back(record);
                    }
                }
                aDecoder.Apply(full);
                EntitySnapshotMsg delta = *entity;
                delta.baseline = full.tick;
                delta.tick = full.tick + 1;
                aDecoder.Apply(delta);
                aStats.deltaApplied += 3;
            }
        }
    }
    if (packet.header.type == static_cast<uint8_t>(coopv2::PacketType::Reject))
    {
        RejectInfo reject;
        DecodeReject(packet.body, reject);
        if (reject.text.size() > kMaxRejectTextBytes)
        {
            return; // the encoder cuts the text, so the re-encoded packet legitimately differs
        }
    }
    Bytes again;
    std::vector<std::string> againLines;
    if (test::Reencode(aInput, again) != Status::Ok ||
        DescribeDatagram(ByteSpan(again.data(), again.size()), againLines) != Status::Ok)
    {
        InvariantFailed(aStats, "accepted datagram does not re-encode", aInput);
        return;
    }
    const bool data = packet.header.type == static_cast<uint8_t>(coopv2::PacketType::Data);
    const bool disconnect = packet.header.type == static_cast<uint8_t>(coopv2::PacketType::Disconnect);
    const bool welcome = packet.header.type == static_cast<uint8_t>(coopv2::PacketType::Welcome);
    const size_t from = data || disconnect || welcome ? 0 : 1;
    if (lines.size() != againLines.size() || !std::equal(lines.begin() + static_cast<std::ptrdiff_t>(from), lines.end(),
                                                         againLines.begin() + static_cast<std::ptrdiff_t>(from)))
    {
        // WELCOME and DISCONNECT keep the token but not seq/ack; compare their bodies only.
        if (!((welcome || disconnect) && lines.size() == againLines.size() && lines.back() == againLines.back()))
        {
            InvariantFailed(aStats, "re-encoded datagram describes differently: " + lines.back().substr(0, 120), aInput);
        }
    }
}

void ExerciseBody(Stats& aStats, uint8_t aType, ByteSpan aInput)
{
    ++aStats.inputs;
    Body body;
    const Status status = DecodeBody(aType, aInput, body);
    ++aStats.outcomes[std::string("body ") + ToString(status)];
    if (status != Status::Ok)
    {
        ++aStats.bodiesRejected;
        return;
    }
    ++aStats.bodiesAccepted;
    CheckBodyRoundTrip(aStats, aType, body, aInput);
}

// Copies aBytes into a heap block of exactly that size (ASan flags any read past it).
std::unique_ptr<uint8_t[]> Exact(const Bytes& aBytes)
{
    auto block = std::make_unique<uint8_t[]>(aBytes.size());
    if (!aBytes.empty())
    {
        std::memcpy(block.get(), aBytes.data(), aBytes.size());
    }
    return block;
}

Bytes RandomBytes(test::Rng& aRng, size_t aSize)
{
    Bytes bytes(aSize);
    for (auto& byte : bytes)
    {
        byte = static_cast<uint8_t>(aRng.U64());
    }
    return bytes;
}
} // namespace

int main(int argc, char** argv)
{
    const int count = argc >= 2 ? std::atoi(argv[1]) : 20000;
    const uint64_t seed = argc >= 3 ? std::strtoull(argv[2], nullptr, 10) : 0xC0FFEE2077ull;
#if defined(__SANITIZE_ADDRESS__)
    const char* build = "AddressSanitizer";
#else
    const char* build = "no sanitizer";
#endif
    std::printf("v2 codec fuzz: %d inputs, seed %llu, %s\n", count, static_cast<unsigned long long>(seed), build);
    const auto start = std::chrono::steady_clock::now();

    test::Rng rng(seed);
    std::vector<Bytes> corpus;
    for (int index = 0; index < 96; ++index)
    {
        corpus.push_back(test::RandomDatagram(rng));
    }
    for (const MsgSpec& spec : MessageSpecs())
    {
        Bytes body;
        EncodeBody(test::RandomBody(rng, spec.type), body);
        Bytes payload;
        AppendMessage(payload, spec.type, 0xFF, std::nullopt, ByteSpan(body.data(), body.size()));
        Bytes datagram;
        EncodePacket(PacketInfo{}, ByteSpan(payload.data(), payload.size()), datagram);
        corpus.push_back(datagram);
    }

    Stats stats;
    DeltaDecoder decoder;
    for (int index = 0; index < count; ++index)
    {
        Bytes input;
        const int strategy = index % 6;
        const Bytes& seedDatagram = corpus[rng.Below(corpus.size())];
        switch (strategy)
        {
        case 0: input = RandomBytes(rng, static_cast<size_t>(rng.Below(1500))); break;
        case 1:
            input = RandomBytes(rng, static_cast<size_t>(rng.Range(4, 1300)));
            input[0] = coopv2::kMagic0;
            input[1] = coopv2::kMagic1;
            input[2] = coopv2::kProtoMajor;
            input[3] = static_cast<uint8_t>(rng.Range(1, 7));
            break;
        case 2: input = test::Mutate(rng, ByteSpan(seedDatagram.data(), seedDatagram.size())); break;
        case 3: input.assign(seedDatagram.begin(), seedDatagram.begin() + static_cast<std::ptrdiff_t>(rng.Below(seedDatagram.size()))); break;
        case 4:
        {
            input = seedDatagram;
            const Bytes tail = RandomBytes(rng, static_cast<size_t>(rng.Range(1201, 1500)) - std::min<size_t>(input.size(), 1200));
            input.insert(input.end(), tail.begin(), tail.end());
            break;
        }
        default:
        {
            const uint8_t type = rng.Chance(0.9) ? test::RandomMessageType(rng) : static_cast<uint8_t>(rng.Below(0x80));
            Bytes body;
            EncodeBody(test::RandomBody(rng, type), body);
            if (body.empty() || rng.Chance(0.3))
            {
                body = RandomBytes(rng, static_cast<size_t>(rng.Below(80)));
            }
            const Bytes mutated = test::Mutate(rng, ByteSpan(body.data(), body.size()));
            const auto block = Exact(mutated);
            ExerciseBody(stats, type, ByteSpan(block.get(), mutated.size()));
            continue;
        }
        }
        const auto block = Exact(input);
        Exercise(stats, ByteSpan(block.get(), input.size()), decoder);
    }

    const double seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
    std::printf("  %d inputs in %.2f s: %d datagrams accepted, %d bodies accepted, %d bodies rejected, "
                "%d entity snapshots through the delta decoder\n",
                stats.inputs, seconds, stats.accepted, stats.bodiesAccepted, stats.bodiesRejected, stats.deltaApplied);
    for (const auto& [outcome, number] : stats.outcomes)
    {
        std::printf("    %-40s %6d\n", outcome.c_str(), number);
    }
    const auto& decoderStats = decoder.Stats();
    std::printf("  delta decoder: decoded %llu, missing baseline %llu, inconsistent %llu, duplicate %llu, stale %llu\n",
                static_cast<unsigned long long>(decoderStats.decoded),
                static_cast<unsigned long long>(decoderStats.missingBaseline),
                static_cast<unsigned long long>(decoderStats.inconsistent),
                static_cast<unsigned long long>(decoderStats.duplicate), static_cast<unsigned long long>(decoderStats.stale));
    const bool ok = stats.invariantFailures == 0 && stats.inputs == count;
    std::printf("FUZZ %s: %d inputs, %d invariant failures, no crash\n", ok ? "PASS" : "FAIL", stats.inputs,
                stats.invariantFailures);
    return ok ? EXIT_SUCCESS : EXIT_FAILURE;
}
