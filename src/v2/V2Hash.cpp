#include "v2/V2Hash.hpp"

#include <algorithm>
#include <cstring>
#include <vector>

namespace coopnet::v2
{
namespace
{
constexpr std::array<uint32_t, 64> kRoundConstants = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
};

constexpr uint32_t RotateRight(uint32_t aValue, int aBits)
{
    return (aValue >> aBits) | (aValue << (32 - aBits));
}

std::span<const uint8_t> AsBytes(std::string_view aText)
{
    return {reinterpret_cast<const uint8_t*>(aText.data()), aText.size()};
}

bool IsAsciiSpace(char aChar)
{
    // str.isspace() for ASCII: \t \n \v \f \r, the file/group/record/unit separators, space.
    return aChar == ' ' || (aChar >= '\t' && aChar <= '\r') || (aChar >= '\x1c' && aChar <= '\x1f');
}

std::string_view StripAscii(std::string_view aText)
{
    while (!aText.empty() && IsAsciiSpace(aText.front()))
    {
        aText.remove_prefix(1);
    }
    while (!aText.empty() && IsAsciiSpace(aText.back()))
    {
        aText.remove_suffix(1);
    }
    return aText;
}

std::string LowerAscii(std::string_view aText)
{
    std::string lowered(aText);
    for (char& character : lowered)
    {
        if (character >= 'A' && character <= 'Z')
        {
            character = static_cast<char>(character - 'A' + 'a');
        }
    }
    return lowered;
}

std::vector<uint8_t> CookieMaterial(std::string_view aHost, uint16_t aPort, uint32_t aIssuedS, uint64_t aClientNonce)
{
    const std::string prefix = std::string(aHost) + "|" + std::to_string(aPort) + "|";
    std::vector<uint8_t> material(prefix.begin(), prefix.end());
    for (int shift = 0; shift < 32; shift += 8)
    {
        material.push_back(static_cast<uint8_t>(aIssuedS >> shift));
    }
    for (int shift = 0; shift < 64; shift += 8)
    {
        material.push_back(static_cast<uint8_t>(aClientNonce >> shift));
    }
    return material;
}
} // namespace

Sha256::Sha256()
    : m_state{0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19}
{
}

void Sha256::Compress(const uint8_t* aBlock)
{
    std::array<uint32_t, 64> schedule{};
    for (size_t index = 0; index < 16; ++index)
    {
        schedule[index] = (static_cast<uint32_t>(aBlock[index * 4]) << 24) |
                          (static_cast<uint32_t>(aBlock[index * 4 + 1]) << 16) |
                          (static_cast<uint32_t>(aBlock[index * 4 + 2]) << 8) | static_cast<uint32_t>(aBlock[index * 4 + 3]);
    }
    for (size_t index = 16; index < 64; ++index)
    {
        const uint32_t s0 = RotateRight(schedule[index - 15], 7) ^ RotateRight(schedule[index - 15], 18) ^
                            (schedule[index - 15] >> 3);
        const uint32_t s1 = RotateRight(schedule[index - 2], 17) ^ RotateRight(schedule[index - 2], 19) ^
                            (schedule[index - 2] >> 10);
        schedule[index] = schedule[index - 16] + s0 + schedule[index - 7] + s1;
    }
    uint32_t a = m_state[0];
    uint32_t b = m_state[1];
    uint32_t c = m_state[2];
    uint32_t d = m_state[3];
    uint32_t e = m_state[4];
    uint32_t f = m_state[5];
    uint32_t g = m_state[6];
    uint32_t h = m_state[7];
    for (size_t index = 0; index < 64; ++index)
    {
        const uint32_t sum1 = RotateRight(e, 6) ^ RotateRight(e, 11) ^ RotateRight(e, 25);
        const uint32_t choose = (e & f) ^ (~e & g);
        const uint32_t temp1 = h + sum1 + choose + kRoundConstants[index] + schedule[index];
        const uint32_t sum0 = RotateRight(a, 2) ^ RotateRight(a, 13) ^ RotateRight(a, 22);
        const uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
        const uint32_t temp2 = sum0 + majority;
        h = g;
        g = f;
        f = e;
        e = d + temp1;
        d = c;
        c = b;
        b = a;
        a = temp1 + temp2;
    }
    m_state[0] += a;
    m_state[1] += b;
    m_state[2] += c;
    m_state[3] += d;
    m_state[4] += e;
    m_state[5] += f;
    m_state[6] += g;
    m_state[7] += h;
}

void Sha256::Update(std::span<const uint8_t> aData)
{
    m_totalBytes += aData.size();
    size_t offset = 0;
    if (m_buffered > 0)
    {
        const size_t take = std::min(aData.size(), m_buffer.size() - m_buffered);
        std::memcpy(m_buffer.data() + m_buffered, aData.data(), take);
        m_buffered += take;
        offset = take;
        if (m_buffered < m_buffer.size())
        {
            return;
        }
        Compress(m_buffer.data());
        m_buffered = 0;
    }
    while (aData.size() - offset >= m_buffer.size())
    {
        Compress(aData.data() + offset);
        offset += m_buffer.size();
    }
    const size_t rest = aData.size() - offset;
    if (rest > 0)
    {
        std::memcpy(m_buffer.data(), aData.data() + offset, rest);
        m_buffered = rest;
    }
}

void Sha256::Update(std::string_view aText)
{
    Update(AsBytes(aText));
}

Sha256Digest Sha256::Finish()
{
    const uint64_t bitLength = m_totalBytes * 8;
    m_buffer[m_buffered++] = 0x80;
    if (m_buffered > 56)
    {
        std::fill(m_buffer.begin() + static_cast<std::ptrdiff_t>(m_buffered), m_buffer.end(), uint8_t{0});
        Compress(m_buffer.data());
        m_buffered = 0;
    }
    std::fill(m_buffer.begin() + static_cast<std::ptrdiff_t>(m_buffered), m_buffer.begin() + 56, uint8_t{0});
    for (int index = 0; index < 8; ++index)
    {
        m_buffer[56 + static_cast<size_t>(index)] = static_cast<uint8_t>(bitLength >> (56 - 8 * index));
    }
    Compress(m_buffer.data());
    Sha256Digest digest{};
    for (size_t index = 0; index < 8; ++index)
    {
        digest[index * 4] = static_cast<uint8_t>(m_state[index] >> 24);
        digest[index * 4 + 1] = static_cast<uint8_t>(m_state[index] >> 16);
        digest[index * 4 + 2] = static_cast<uint8_t>(m_state[index] >> 8);
        digest[index * 4 + 3] = static_cast<uint8_t>(m_state[index]);
    }
    return digest;
}

Sha256Digest Sha256Of(std::span<const uint8_t> aData)
{
    Sha256 hash;
    hash.Update(aData);
    return hash.Finish();
}

Sha256Digest Sha256Of(std::string_view aText)
{
    return Sha256Of(AsBytes(aText));
}

Sha256Digest HmacSha256(std::span<const uint8_t> aKey, std::span<const uint8_t> aMessage)
{
    std::array<uint8_t, 64> block{};
    if (aKey.size() > block.size())
    {
        const Sha256Digest hashedKey = Sha256Of(aKey);
        std::copy(hashedKey.begin(), hashedKey.end(), block.begin());
    }
    else
    {
        std::copy(aKey.begin(), aKey.end(), block.begin());
    }
    std::array<uint8_t, 64> innerPad{};
    std::array<uint8_t, 64> outerPad{};
    for (size_t index = 0; index < block.size(); ++index)
    {
        innerPad[index] = static_cast<uint8_t>(block[index] ^ 0x36);
        outerPad[index] = static_cast<uint8_t>(block[index] ^ 0x5c);
    }
    Sha256 inner;
    inner.Update(innerPad);
    inner.Update(aMessage);
    const Sha256Digest innerDigest = inner.Finish();
    Sha256 outer;
    outer.Update(outerPad);
    outer.Update(innerDigest);
    return outer.Finish();
}

uint32_t GameBuildId(std::string_view aVersionText)
{
    uint32_t value = 0x811C9DC5u;
    for (const char character : aVersionText)
    {
        value = (value ^ static_cast<uint8_t>(character)) * 0x01000193u;
    }
    return value;
}

KeyHash RoomKeyHash(std::string_view aRoom, std::string_view aPassword)
{
    Sha256 hash;
    hash.Update(std::string_view("cp2077coop-v2|"));
    hash.Update(aRoom);
    hash.Update(std::string_view("|"));
    hash.Update(aPassword);
    const Sha256Digest digest = hash.Finish();
    KeyHash result{};
    std::copy_n(digest.begin(), result.size(), result.begin());
    return result;
}

uint64_t ModListHash(std::span<const ModEntry> aEntries)
{
    std::vector<std::string> lines;
    lines.reserve(aEntries.size());
    for (const ModEntry& entry : aEntries)
    {
        lines.push_back(LowerAscii(StripAscii(entry.name)) + "@" + std::string(StripAscii(entry.version)));
    }
    // Byte order of UTF-8 equals code point order, which is how Python sorts str.
    std::sort(lines.begin(), lines.end(), [](const std::string& aLeft, const std::string& aRight) {
        return std::lexicographical_compare(aLeft.begin(), aLeft.end(), aRight.begin(), aRight.end(),
                                            [](char aLhs, char aRhs) {
                                                return static_cast<uint8_t>(aLhs) < static_cast<uint8_t>(aRhs);
                                            });
    });
    Sha256 hash;
    for (size_t index = 0; index < lines.size(); ++index)
    {
        if (index > 0)
        {
            hash.Update(std::string_view("\n"));
        }
        hash.Update(lines[index]);
    }
    const Sha256Digest digest = hash.Finish();
    uint64_t value = 0;
    for (int index = 7; index >= 0; --index)
    {
        value = (value << 8) | digest[static_cast<size_t>(index)];
    }
    return value;
}

Cookie MakeCookie(std::span<const uint8_t> aSecret, std::string_view aHost, uint16_t aPort, uint64_t aClientNonce,
                  uint32_t aIssuedS)
{
    const std::vector<uint8_t> material = CookieMaterial(aHost, aPort, aIssuedS, aClientNonce);
    const Sha256Digest mac = HmacSha256(aSecret, material);
    Cookie cookie{};
    for (size_t index = 0; index < 4; ++index)
    {
        cookie[index] = static_cast<uint8_t>(aIssuedS >> (8 * index));
    }
    std::copy_n(mac.begin(), cookie.size() - 4, cookie.begin() + 4);
    return cookie;
}

bool CheckCookie(std::span<const uint8_t> aSecret, std::string_view aHost, uint16_t aPort, uint64_t aClientNonce,
                 std::span<const uint8_t> aCookie, uint32_t aNowS)
{
    constexpr uint32_t kCookieMaxAgeS = 10;
    if (aCookie.size() != Cookie{}.size())
    {
        return false;
    }
    const uint32_t issued = static_cast<uint32_t>(aCookie[0]) | (static_cast<uint32_t>(aCookie[1]) << 8) |
                            (static_cast<uint32_t>(aCookie[2]) << 16) | (static_cast<uint32_t>(aCookie[3]) << 24);
    if (static_cast<uint32_t>(aNowS - issued) > kCookieMaxAgeS)
    {
        return false;
    }
    const Cookie expected = MakeCookie(aSecret, aHost, aPort, aClientNonce, issued);
    return ConstantTimeEqual(expected, aCookie);
}

bool ConstantTimeEqual(std::span<const uint8_t> aLeft, std::span<const uint8_t> aRight)
{
    if (aLeft.size() != aRight.size())
    {
        return false;
    }
    uint8_t difference = 0;
    for (size_t index = 0; index < aLeft.size(); ++index)
    {
        difference = static_cast<uint8_t>(difference | (aLeft[index] ^ aRight[index]));
    }
    return difference == 0;
}
} // namespace coopnet::v2
