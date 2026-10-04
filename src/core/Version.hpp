#pragma once

// Plugin identity shared by Query() (what RED4ext logs), Net_Version() and Net_Stats().
//
// The numbers come from project(CP2077CoopNet VERSION x.y.z) in CMakeLists.txt through the
// coopnet_version interface target, so the DLL, the probe and the tests can never disagree.

#include "core/Protocol.hpp"

#include <cstdint>
#include <string_view>

#if !defined(COOPNET_VERSION_MAJOR) || !defined(COOPNET_VERSION_MINOR) || !defined(COOPNET_VERSION_PATCH)
#error "COOPNET_VERSION_* are defined by the coopnet_version CMake target (project VERSION in CMakeLists.txt)"
#endif

#define COOPNET_STRINGIFY_IMPL(x) #x
#define COOPNET_STRINGIFY(x) COOPNET_STRINGIFY_IMPL(x)
#define COOPNET_SEMVER_STRING                                                                                          \
    COOPNET_STRINGIFY(COOPNET_VERSION_MAJOR) "." COOPNET_STRINGIFY(COOPNET_VERSION_MINOR) "." COOPNET_STRINGIFY(       \
        COOPNET_VERSION_PATCH)

namespace coopnet
{
inline constexpr std::string_view kPluginName = "CP2077CoopNet";

inline constexpr uint32_t kVersionMajor = COOPNET_VERSION_MAJOR;
inline constexpr uint32_t kVersionMinor = COOPNET_VERSION_MINOR;
inline constexpr uint32_t kVersionPatch = COOPNET_VERSION_PATCH;
inline constexpr std::string_view kSemVer = COOPNET_SEMVER_STRING;

// What Net_Version() returns: "CP2077CoopNet <major.minor.patch> proto <wire protocol version>".
inline constexpr std::string_view kVersionString =
    "CP2077CoopNet " COOPNET_SEMVER_STRING " proto " COOPNET_STRINGIFY(COOPNET_PROTOCOL_VERSION);

static_assert(COOPNET_PROTOCOL_VERSION == kProtocolVersion, "protocol macro and constant must agree");
} // namespace coopnet
