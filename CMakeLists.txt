cmake_minimum_required(VERSION 3.21)
project(CP2077CoopChecks LANGUAGES CXX)

# Exercise the deployed CB77 implementation, not a second protocol or toy core.
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
include(CTest)
option(COOP_BUILD_NATIVE_PLUGIN "Also build the existing Windows/MSVC RED4ext plugin" OFF)
option(COOP_ENABLE_SANITIZERS "Instrument portable C++ checks with ASan and UBSan" OFF)

add_library(coop_check_options INTERFACE)
if(MSVC)
    target_compile_options(coop_check_options INTERFACE /W4 /WX /permissive- /utf-8 /Zc:__cplusplus)
else()
    target_compile_options(coop_check_options INTERFACE -Wall -Wextra -Wpedantic -Werror)
endif()
if(COOP_ENABLE_SANITIZERS)
    if(MSVC OR NOT CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
        message(FATAL_ERROR "Portable ASan/UBSan checks require GCC or Clang (Linux CI uses GCC)")
    endif()
    target_compile_options(coop_check_options INTERFACE -fsanitize=address,undefined -fno-omit-frame-pointer)
    target_link_options(coop_check_options INTERFACE -fsanitize=address,undefined)
endif()

set(COOP_V2_DIR "${CMAKE_CURRENT_SOURCE_DIR}/plugin/src/v2")
add_library(coop_portable_v2 STATIC
    "${COOP_V2_DIR}/ClockSync.cpp"
    "${COOP_V2_DIR}/SnapshotBuffer.cpp"
    "${COOP_V2_DIR}/V2Codec.cpp"
    "${COOP_V2_DIR}/V2Delta.cpp"
    "${COOP_V2_DIR}/V2Describe.cpp"
    "${COOP_V2_DIR}/V2Hash.cpp"
    "${COOP_V2_DIR}/V2Reliability.cpp"
)
target_include_directories(coop_portable_v2 PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/plugin/src")
target_link_libraries(coop_portable_v2 PUBLIC coop_check_options)

if(COOP_BUILD_NATIVE_PLUGIN)
    if(NOT WIN32 OR NOT MSVC)
        message(FATAL_ERROR "The native plugin and Winsock transport require Windows/MSVC")
    endif()
    set(COOP_RED4EXT_SDK_DIR "${CMAKE_CURRENT_SOURCE_DIR}/plugin/deps/RED4ext.SDK" CACHE PATH "Existing pinned RED4ext.SDK checkout")
    find_package(Git REQUIRED)
    execute_process(COMMAND "${GIT_EXECUTABLE}" -C "${COOP_RED4EXT_SDK_DIR}" rev-parse HEAD
        OUTPUT_VARIABLE sdk_commit OUTPUT_STRIP_TRAILING_WHITESPACE RESULT_VARIABLE sdk_result)
    if(NOT sdk_result EQUAL 0 OR NOT sdk_commit STREQUAL "a4a781088a92a8efa890d94fde4efd8985d497c7")
        message(FATAL_ERROR "Native checks require RED4ext.SDK a4a781088a92a8efa890d94fde4efd8985d497c7; run plugin/tools/fetch_deps.ps1 or supply COOP_RED4EXT_SDK_DIR")
    endif()
    set(RED4EXT_SDK_DIR "${COOP_RED4EXT_SDK_DIR}" CACHE PATH "RED4ext.SDK checkout" FORCE)
    add_subdirectory(plugin)
endif()

if(BUILD_TESTING)
    add_subdirectory(tests)
endif()

message(STATUS "Game assets are not required for these checks. Full local redscript/runtime validation: tests/run_all.ps1")
