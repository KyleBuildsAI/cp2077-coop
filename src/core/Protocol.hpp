#pragma once

// Wire format of the CP2077CoopNet UDP protocol (version 1).
//
// Every datagram is exactly one frame, little-endian:
//
//   offset size field
//   0      4    magic        'C','P','N','2'
//   4      1    version      kProtocolVersion
//   5      1    channel      0 = control, 1..15 unreliable, 16..31 reliable ordered
//   6      2    sender       peer id assigned by the relay (0 = relay itself)
//   8      2    target       peer id, kRelayId (0) or kBroadcastId (0xFFFF)
//   10     2    sequence     reliable: message sequence; unreliable: per-channel sequence
//   12     2    ack          cumulative ack: every reliable sequence <= ack from target was received
//   14     4    ackBits      bit i set => reliable sequence (ack + 2 + i) was also received
//   18     2    payloadSize  number of payload bytes that follow
//   20     n    payload
//
// Control frames (channel 0) start their payload with a ControlOp byte.

#include <cstddef>
#include <cstdint>
#include <span>
#include <string>
#include <string_view>
#include <vector>

namespace coopnet
{
constexpr uint32_t kMagic = 0x324E5043; // bytes "CPN2" when written little-endian
constexpr uint8_t kProtocolVersion = 1;
constexpr size_t kHeaderSize = 20;
constexpr size_t kMaxDatagramSize = 1200;
constexpr size_t kMaxPayloadSize = kMaxDatagramSize - kHeaderSize;

constexpr uint16_t kRelayId = 0;
constexpr uint16_t kBroadcastId = 0xFFFF;

constexpr uint8_t kControlChannel = 0;
constexpr uint8_t kFirstUnreliableChannel = 1;
constexpr uint8_t kLastUnreliableChannel = 15;
constexpr uint8_t kFirstReliableChannel = 16;
constexpr uint8_t kLastReliableChannel = 31;

enum class Delivery
{
    Control,
    Unreliable,
    Reliable,
    Invalid
};

Delivery DeliveryOf(int channel);

enum class ControlOp : uint8_t
{
    Hello = 1,   // client -> relay: u32 nonce, u8 roomLength, room bytes
    Welcome = 2, // relay -> client: u16 assignedId, u32 nonce (echo)
    Peers = 3,   // relay -> client: u16 count, count * (u16 id, u32 nonce)
    Ping = 4,    // any -> any: u32 pingId, u64 senderTimeMicros
    Pong = 5,    // reply to Ping: u32 pingId, u64 senderTimeMicros (echoed)
    Bye = 6,     // client -> relay / peers: leaving
    Ack = 7,     // peer -> peer: header ack fields only
    Reject = 8   // relay -> client: u8 reasonLength, reason bytes
};

struct FrameHeader
{
    uint8_t version = kProtocolVersion;
    uint8_t channel = 0;
    uint16_t sender = 0;
    uint16_t target = 0;
    uint16_t sequence = 0;
    uint16_t ack = 0;
    uint32_t ackBits = 0;
    uint16_t payloadSize = 0;
};

// Writes header + payload into `out`. Returns the frame size, or 0 if the payload is too large
// or `out` is too small.
size_t EncodeFrame(const FrameHeader& aHeader, std::span<const uint8_t> aPayload, std::span<uint8_t> aOut);

enum class DecodeResult
{
    Ok,
    TooShort,
    BadMagic,
    BadVersion,
    BadLength
};

DecodeResult DecodeFrame(std::span<const uint8_t> aDatagram, FrameHeader& aHeader, std::span<const uint8_t>& aPayload);

// Serial-number arithmetic (RFC 1982) for 16-bit sequences.
constexpr bool SequenceGreater(uint16_t aLeft, uint16_t aRight)
{
    return static_cast<int16_t>(static_cast<uint16_t>(aLeft - aRight)) > 0;
}

constexpr int SequenceDistance(uint16_t aFrom, uint16_t aTo)
{
    return static_cast<int16_t>(static_cast<uint16_t>(aTo - aFrom));
}

// Small helpers for building/reading control payloads.
class ByteWriter
{
public:
    void U8(uint8_t aValue);
    void U16(uint16_t aValue);
    void U32(uint32_t aValue);
    void U64(uint64_t aValue);
    void Bytes(std::string_view aBytes);

    [[nodiscard]] const std::vector<uint8_t>& Data() const
    {
        return m_data;
    }

private:
    std::vector<uint8_t> m_data;
};

class ByteReader
{
public:
    explicit ByteReader(std::span<const uint8_t> aData)
        : m_data(aData)
    {
    }

    bool U8(uint8_t& aValue);
    bool U16(uint16_t& aValue);
    bool U32(uint32_t& aValue);
    bool U64(uint64_t& aValue);
    bool Bytes(size_t aCount, std::string& aValue);

    [[nodiscard]] size_t Remaining() const
    {
        return m_data.size() - m_offset;
    }

private:
    std::span<const uint8_t> m_data;
    size_t m_offset = 0;
};
} // namespace coopnet
