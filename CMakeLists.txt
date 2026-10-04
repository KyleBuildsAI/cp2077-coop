cmake_minimum_required(VERSION 3.21)

project(CP2077CoopNet VERSION 0.2.0 LANGUAGES CXX)

# Pre-release label, bumped per Phase 2 milestone (0.2.0-alpha.N). An empty type means a release.
set(COOPNET_PRERELEASE_TYPE "alpha") # alpha | beta | rc | ""
set(COOPNET_PRERELEASE_NUMBER 1)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Static CRT: the plugin must not depend on whichever VC++ redistributable the player has.
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

if(NOT MSVC)
  message(FATAL_ERROR "CP2077CoopNet is a Windows/MSVC-only project.")
endif()

# -----------------------------------------------------------------------------
# RED4ext.SDK (header-only; pinned to tag 1.0.0, see tools/fetch_deps.ps1)
# -----------------------------------------------------------------------------
set(RED4EXT_SDK_DIR "${PROJECT_SOURCE_DIR}/deps/RED4ext.SDK" CACHE PATH "RED4ext.SDK checkout")
if(NOT EXISTS "${RED4EXT_SDK_DIR}/CMakeLists.txt")
  message(FATAL_ERROR "RED4ext.SDK not found at ${RED4EXT_SDK_DIR}; run tools/fetch_deps.ps1")
endif()
set(RED4EXT_HEADER_ONLY ON CACHE BOOL "" FORCE)
add_subdirectory("${RED4EXT_SDK_DIR}" RED4ext.SDK EXCLUDE_FROM_ALL)

set(COOPNET_WARNINGS /W4 /permissive- /utf-8 /Zc:__cplusplus)

# -----------------------------------------------------------------------------
# Version: project(VERSION) plus the pre-release label above are the single source of truth.
# Query() reports them to RED4ext (RED4EXT_V1_SEMVER_EX) and Net_Version() returns
# "CP2077CoopNet <major.minor.patch>[-<type>.<n>] proto <protocol>".
# -----------------------------------------------------------------------------
set(COOPNET_PRERELEASE_TYPES "" alpha beta rc) # index = RED4EXT_V1_SEMVER_PRERELEASE_TYPE_*
list(FIND COOPNET_PRERELEASE_TYPES "${COOPNET_PRERELEASE_TYPE}" COOPNET_PRERELEASE_CODE)
if(COOPNET_PRERELEASE_CODE LESS 0)
  message(FATAL_ERROR "COOPNET_PRERELEASE_TYPE must be alpha, beta, rc or empty")
endif()
if(COOPNET_PRERELEASE_CODE EQUAL 0)
  set(COOPNET_PRERELEASE_NUMBER 0)
  set(COOPNET_SEMVER "${PROJECT_VERSION}")
else()
  set(COOPNET_SEMVER "${PROJECT_VERSION}-${COOPNET_PRERELEASE_TYPE}.${COOPNET_PRERELEASE_NUMBER}")
endif()
message(STATUS "CP2077CoopNet version ${COOPNET_SEMVER}")
add_library(coopnet_version INTERFACE)
target_include_directories(coopnet_version INTERFACE "${PROJECT_SOURCE_DIR}/src")
target_compile_definitions(coopnet_version INTERFACE
  COOPNET_VERSION_MAJOR=${PROJECT_VERSION_MAJOR}
  COOPNET_VERSION_MINOR=${PROJECT_VERSION_MINOR}
  COOPNET_VERSION_PATCH=${PROJECT_VERSION_PATCH}
  COOPNET_VERSION_PRERELEASE_TYPE=${COOPNET_PRERELEASE_CODE}
  COOPNET_VERSION_PRERELEASE_NUMBER=${COOPNET_PRERELEASE_NUMBER}
  "COOPNET_SEMVER_TEXT=\"${COOPNET_SEMVER}\""
)

# -----------------------------------------------------------------------------
# Transport core (no RED4ext dependency, shared by the plugin and the tests)
# -----------------------------------------------------------------------------
add_library(coopnet_core STATIC
  src/core/Clock.cpp
  src/core/Clock.hpp
  src/core/LoadReport.cpp
  src/core/LoadReport.hpp
  src/core/NativeRegistration.hpp
  src/core/Protocol.cpp
  src/core/Protocol.hpp
  src/core/Reliability.cpp
  src/core/Reliability.hpp
  src/core/ScriptString.hpp
  src/core/Transport.cpp
  src/core/Transport.hpp
  src/core/Version.hpp
)
target_include_directories(coopnet_core PUBLIC "${PROJECT_SOURCE_DIR}/src")
target_compile_definitions(coopnet_core PUBLIC WIN32_LEAN_AND_MEAN NOMINMAX _WIN32_WINNT=0x0A00)
target_compile_options(coopnet_core PRIVATE ${COOPNET_WARNINGS} /WX)
target_link_libraries(coopnet_core PUBLIC ws2_32 coopnet_version)

# -----------------------------------------------------------------------------
# Protocol v2 codec (port of relay/coopnet/proto.py and snapshot.py; no RED4ext, no Winsock)
# -----------------------------------------------------------------------------
set(COOPNET_V2_SOURCES
  src/v2/coop_proto_v2.h
  src/v2/V2Codec.cpp
  src/v2/V2Codec.hpp
  src/v2/V2Describe.cpp
  src/v2/V2Describe.hpp
  src/v2/V2Hash.cpp
  src/v2/V2Hash.hpp
)
add_library(coopnet_v2 STATIC ${COOPNET_V2_SOURCES})
target_include_directories(coopnet_v2 PUBLIC "${PROJECT_SOURCE_DIR}/src")
target_compile_options(coopnet_v2 PRIVATE ${COOPNET_WARNINGS} /WX)

# -----------------------------------------------------------------------------
# RED4ext plugin
# -----------------------------------------------------------------------------
add_library(CP2077CoopNet SHARED src/plugin/Main.cpp)
target_link_libraries(CP2077CoopNet PRIVATE coopnet_core RED4ext::SDK version)
target_compile_options(CP2077CoopNet PRIVATE ${COOPNET_WARNINGS})
set_target_properties(CP2077CoopNet PROPERTIES OUTPUT_NAME "CP2077CoopNet")

# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------
add_executable(coopnet_tests tests/UnitTests.cpp)
target_link_libraries(coopnet_tests PRIVATE coopnet_core)
target_compile_options(coopnet_tests PRIVATE ${COOPNET_WARNINGS} /WX)

add_executable(coopnet_loopback tests/LoopbackTest.cpp)
target_link_libraries(coopnet_loopback PRIVATE coopnet_core)
target_compile_options(coopnet_loopback PRIVATE ${COOPNET_WARNINGS} /WX)

# Loads the built plugin with LoadLibrary and calls Query/Supports (never Main, which needs the game).
add_executable(coopnet_plugin_probe tests/PluginProbe.cpp)
target_link_libraries(coopnet_plugin_probe PRIVATE RED4ext::SDK coopnet_version)
target_compile_options(coopnet_plugin_probe PRIVATE ${COOPNET_WARNINGS})
add_dependencies(coopnet_plugin_probe CP2077CoopNet)

