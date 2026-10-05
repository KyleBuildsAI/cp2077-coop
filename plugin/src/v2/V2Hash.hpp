#pragma once

// Hashes the v2 handshake needs, matching relay/coopnet/proto.py byte for byte:
//
//   RoomKeyHash  SHA-256("cp2077coop-v2|" + room + "|" + password)[0..16)  (what AUTH carries)
//   ModListHash  u64 LE of SHA-256 over the sorted "name@version" lines     (JoinFixed.mod_hash)
//   GameBuildId  FNV-1a 32 of the game version string, e.g. "2.31a"         (JoinFixed.game_build)
//   MakeCookie   issued_s u32 LE + HMAC-SHA256(secret, "host|port|" + issued + nonce)[0..12)
//
// SHA-256 and HMAC are implemented here (FIPS 180-4, RFC 2104) so the plugin keeps importing
// only kernel32, user32, version and ws2_32.

#include <array>
#include <cstddef>
#include <cstdint>
#include <span>
#include <string>
#include <string_view>

namespace coopnet::v2
{
using Sha256Digest = std::array<uint8_t, 32>;
using KeyHash = std::array<uint8_t, 16>;
using Cookie = std::array<uint8_t, 16>;

class Sha256
{
public:
    Sha256();

    void Update(std::span<const uint8_t> aData);
    void Update(std::string_view aText);
    // Finishes the hash. The object must not be updated afterwards.
    Sha256Digest Finish();

private:
    void Compress(const uint8_t* aBlock);

    std::array<uint32_t, 8> m_state{};
    std::array<uint8_t, 64> m_buffer{};
    uint64_t m_totalBytes = 0;
    size_t m_buffered = 0;
};

Sha256Digest Sha256Of(std::span<const uint8_t> aData);
Sha256Digest Sha256Of(std::string_view aText);
Sha256Digest HmacSha256(std::span<const uint8_t> aKey, std::span<const uint8_t> aMessage);

uint32_t GameBuildId(std::string_view aVersionText);
KeyHash RoomKeyHash(std::string_view aRoom, std::string_view aPassword);

struct ModEntry
{
    std::string name;
    std::string version;
};

// proto.py lower-cases and strips with Python's Unicode rules; this port folds ASCII letters and
// strips ASCII whitespace only, so names that differ only in non-ASCII case hash differently here.
uint64_t ModListHash(std::span<const ModEntry> aEntries);

// aHost is the address text exactly as the relay prints it (e.g. "203.0.113.5"), aPort decimal.
Cookie MakeCookie(std::span<const uint8_t> aSecret, std::string_view aHost, uint16_t aPort, uint64_t aClientNonce,
                  uint32_t aIssuedS);
// Valid when it was made for this address and nonce, at most kCookieMaxAgeS (10 s) before aNowS
// (u32 wrap-around arithmetic, as in proto.py). Compares in constant time.
bool CheckCookie(std::span<const uint8_t> aSecret, std::string_view aHost, uint16_t aPort, uint64_t aClientNonce,
                 std::span<const uint8_t> aCookie, uint32_t aNowS);

bool ConstantTimeEqual(std::span<const uint8_t> aLeft, std::span<const uint8_t> aRight);
} // namespace coopnet::v2
