# Test-only shim: wraps the CP2077CoopNet Transport (dllproto/src/core) in a small C API so the
# NetProbe tests can drive the real UDP transport from LuaJIT FFI, the same code the plugin's
# Net_* natives call. It is never shipped to the game.
cmake_minimum_required(VERSION 3.21)
project(coopnet_shim LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

set(COOPNET_CORE_SRC "${CMAKE_CURRENT_SOURCE_DIR}/../../build/coresrc/src" CACHE PATH
    "Pinned legacy core export (contains core/Transport.hpp)")
if(NOT EXISTS "${COOPNET_CORE_SRC}/core/Transport.hpp")
  message(FATAL_ERROR "core/Transport.hpp not found under ${COOPNET_CORE_SRC}")
endif()
file(GLOB COOPNET_CORE_SOURCES CONFIGURE_DEPENDS "${COOPNET_CORE_SRC}/core/*.cpp")

# core/Version.hpp (dllproto 0.1.1+) expects COOPNET_VERSION_* from dllproto's project(VERSION).
set(COOPNET_PROJECT_CMAKE "${COOPNET_CORE_SRC}/../CMakeLists.txt" CACHE FILEPATH "dllproto CMakeLists.txt")
set(COOPNET_SHIM_VERSION_MAJOR 0)
set(COOPNET_SHIM_VERSION_MINOR 0)
set(COOPNET_SHIM_VERSION_PATCH 0)
if(EXISTS "${COOPNET_PROJECT_CMAKE}")
  file(READ "${COOPNET_PROJECT_CMAKE}" COOPNET_PROJECT_TEXT)
  if(COOPNET_PROJECT_TEXT MATCHES "project\\(CP2077CoopNet VERSION ([0-9]+)\\.([0-9]+)\\.([0-9]+)")
    set(COOPNET_SHIM_VERSION_MAJOR ${CMAKE_MATCH_1})
    set(COOPNET_SHIM_VERSION_MINOR ${CMAKE_MATCH_2})
    set(COOPNET_SHIM_VERSION_PATCH ${CMAKE_MATCH_3})
  endif()
endif()
message(STATUS "coopnet core version ${COOPNET_SHIM_VERSION_MAJOR}.${COOPNET_SHIM_VERSION_MINOR}.${COOPNET_SHIM_VERSION_PATCH}")

add_library(coopnet_shim SHARED coopnet_shim.cpp ${COOPNET_CORE_SOURCES})
target_include_directories(coopnet_shim PRIVATE "${COOPNET_CORE_SRC}")
target_compile_definitions(coopnet_shim PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX _WIN32_WINNT=0x0A00
  COOPNET_VERSION_MAJOR=${COOPNET_SHIM_VERSION_MAJOR}
  COOPNET_VERSION_MINOR=${COOPNET_SHIM_VERSION_MINOR}
  COOPNET_VERSION_PATCH=${COOPNET_SHIM_VERSION_PATCH})
target_compile_options(coopnet_shim PRIVATE /W4 /permissive- /utf-8 /Zc:__cplusplus)
target_link_libraries(coopnet_shim PRIVATE ws2_32)
