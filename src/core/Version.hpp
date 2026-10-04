#pragma once

// Plugin identity shared by Query() (what RED4ext logs), Net_Version() and Net_Stats().
//
// The numbers come from project(CP2077CoopNet VERSION x.y.z) and COOPNET_PRERELEASE_* in
// CMakeLists.txt through the coopnet_version interface target, so the DLL, the probe and the tests
// can never disagree.

#include "core/Protocol.hpp"

#include <cstdint>
#include <string_view>

#if !defined(COOPNET_VERSION_MAJOR) || !defined(COOPNET_VERSION_MINOR) || !defined(COOPNET_VERSION_PATCH) ||      \
    !defined(COOPNET_VERSION_PRERELEASE_TYPE) || !defined(COOPNET_VERSION_PRERELEASE_NUMBER) ||                        \
    !defined(COOPNET_SEMVER_TEXT)
#error "COOPNET_VERSION_* are defined by the coopnet_version CMake target (project VERSION in CMakeLists.txt)"
#endif

#define COOPNET_STRINGIFY_IMPL(x) #x
#define COOPNET_STRINGIFY(x) COOPNET_STRINGIFY_IMPL(x)
// "major.minor.patch" or "major.minor.patch-<alpha|beta|rc>.<n>"
#define COOPNET_SEMVER_STRING COOPNET_SEMVER_TEXT

namespace coopnet
{
inline constexpr std::string_view kPluginName = "CP2077CoopNet";

inline constexpr uint32_t kVersionMajor = COOPNET_VERSION_MAJOR;
inline constexpr uint32_t kVersionMinor = COOPNET_VERSION_MINOR;
inline constexpr uint32_t kVersionPatch = COOPNET_VERSION_PATCH;
// RED4EXT_V1_SEMVER_PRERELEASE_TYPE_*: 0 none, 1 alpha, 2 beta, 3 rc.
inline constexpr uint32_t kPrereleaseType = COOPNET_VERSION_PRERELEASE_TYPE;
inline constexpr uint32_t kPrereleaseNumber = COOPNET_VERSION_PRERELEASE_NUMBER;
inline constexpr std::string_view kSemVer = COOPNET_SEMVER_STRING;

// What Net_Version() returns: "CP2077CoopNet <semver> proto <wire protocol version>", e.g.
// "CP2077CoopNet 0.2.0-alpha.1 proto 1".
inline constexpr std::string_view kVersionString =
    "CP2077CoopNet " COOPNET_SEMVER_STRING " proto " COOPNET_STRINGIFY(COOPNET_PROTOCOL_VERSION);

static_assert(COOPNET_PROTOCOL_VERSION == kProtocolVersion, "protocol macro and constant must agree");
static_assert(kPrereleaseType <= 3, "RED4ext knows alpha (1), beta (2) and rc (3)");
} // namespace coopnet
