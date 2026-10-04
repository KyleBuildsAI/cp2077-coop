#include "Protocol.hpp"

#include <cstring>

namespace coopnet
{
namespace
{
void PutU16(uint8_t* aOut, uint16_t aValue)
{
    aOut[0] = static_cast<uint8_t>(aValue);
    aOut[1] = static_cast<uint8_t>(aValue >> 8);
}

void PutU32(uint8_t* aOut, uint32_t aValue)
{
    for (int index = 0; index < 4; ++index)
    {
        aOut[index] = static_cast<uint8_t>(aValue >> (8 * index));
    }
}

uint16_t GetU16(const uint8_t* aIn)
{
    return static_cast<uint16_t>(aIn[0] | (aIn[1] << 8));
}

uint32_t GetU32(const uint8_t* aIn)
{
    uint32_t value = 0;
    for (int index = 0; index < 4; ++index)
    {
        value |= static_cast<uint32_t>(aIn[index]) << (8 * index);
    }
    return value;
}
} // namespace

Delivery DeliveryOf(int channel)
{
    if (channel == kControlChannel)
    {
        return Delivery::Control;
    }
    if (channel >= kFirstUnreliableChannel && channel <= kLastUnreliableChannel)
    {
        return Delivery::Unreliable;
    }
    if (channel >= kFirstReliableChannel && channel <= kLastReliableChannel)
    {
        return Delivery::Reliable;
    }
    return Delivery::Invalid;
}

size_t EncodeFrame(const FrameHeader& aHeader, std::span<const uint8_t> aPayload, std::span<uint8_t> aOut)
{
    if (aPayload.size() > kMaxPayloadSize)
    {
        return 0;
    }
    const size_t total = kHeaderSize + aPayload.size();
    if (aOut.size() < total)
    {
        return 0;
    }

    uint8_t* out = aOut.data();
    PutU32(out + 0, kMagic);
    out[4] = aHeader.version;
    out[5] = aHeader.channel;
    PutU16(out + 6, aHeader.sender);
    PutU16(out + 8, aHeader.target);
    PutU16(out + 10, aHeader.sequence);
    PutU16(out + 12, aHeader.ack);
    PutU32(out + 14, aHeader.ackBits);
    PutU16(out + 18, static_cast<uint16_t>(aPayload.size()));
    if (!aPayload.empty())
    {
        std::memcpy(out + kHeaderSize, aPayload.data(), aPayload.size());
    }
    return total;
}

DecodeResult DecodeFrame(std::span<const uint8_t> aDatagram, FrameHeader& aHeader, std::span<const uint8_t>& aPayload)
{
    if (aDatagram.size() < kHeaderSize)
    {
        return DecodeResult::TooShort;
    }
    const uint8_t* in = aDatagram.data();
    if (GetU32(in) != kMagic)
    {
        return DecodeResult::BadMagic;
    }
    aHeader.version = in[4];
    if (aHeader.version != kProtocolVersion)
    {
        return DecodeResult::BadVersion;
    }
    aHeader.channel = in[5];
    aHeader.sender = GetU16(in + 6);
    aHeader.target = GetU16(in + 8);
    aHeader.sequence = GetU16(in + 10);
    aHeader.ack = GetU16(in + 12);
    aHeader.ackBits = GetU32(in + 14);
    aHeader.payloadSize = GetU16(in + 18);
    if (aHeader.payloadSize != aDatagram.size() - kHeaderSize || aHeader.payloadSize > kMaxPayloadSize)
    {
        return DecodeResult::BadLength;
    }
    aPayload = aDatagram.subspan(kHeaderSize, aHeader.payloadSize);
    return DecodeResult::Ok;
}

void ByteWriter::U8(uint8_t aValue)
{
    m_data.push_back(aValue);
}

void ByteWriter::U16(uint16_t aValue)
{
    U8(static_cast<uint8_t>(aValue));
    U8(static_cast<uint8_t>(aValue >> 8));
}

void ByteWriter::U32(uint32_t aValue)
{
    U16(static_cast<uint16_t>(aValue));
    U16(static_cast<uint16_t>(aValue >> 16));
}

void ByteWriter::U64(uint64_t aValue)
{
    U32(static_cast<uint32_t>(aValue));
    U32(static_cast<uint32_t>(aValue >> 32));
}

void ByteWriter::Bytes(std::string_view aBytes)
{
    m_data.insert(m_data.end(), aBytes.begin(), aBytes.end());
}

bool ByteReader::U8(uint8_t& aValue)
{
    if (Remaining() < 1)
    {
        return false;
    }
    aValue = m_data[m_offset++];
    return true;
}

bool ByteReader::U16(uint16_t& aValue)
{
    if (Remaining() < 2)
    {
        return false;
    }
    aValue = GetU16(m_data.data() + m_offset);
    m_offset += 2;
    return true;
}

bool ByteReader::U32(uint32_t& aValue)
{
    if (Remaining() < 4)
    {
        return false;
    }
    aValue = GetU32(m_data.data() + m_offset);
    m_offset += 4;
    return true;
}

bool ByteReader::U64(uint64_t& aValue)
{
    uint32_t low = 0;
    uint32_t high = 0;
    if (Remaining() < 8 || !U32(low) || !U32(high))
    {
        return false;
    }
    aValue = (static_cast<uint64_t>(high) << 32) | low;
    return true;
}

bool ByteReader::Bytes(size_t aCount, std::string& aValue)
{
    if (Remaining() < aCount)
    {
        return false;
    }
    aValue.assign(reinterpret_cast<const char*>(m_data.data() + m_offset), aCount);
    m_offset += aCount;
    return true;
}
} // namespace coopnet
