#pragma once

// Wall clock behind Net_NowMs().
//
// Net_NowMs() returns milliseconds since the Unix epoch (UTC) as a Double. The value comes from
// GetSystemTimePreciseAsFileTime (100 ns ticks), so it keeps a sub-millisecond fraction. A Double
// holds every whole millisecond exactly until the year 287396, and today's values (~1.8e12 ms)
// still resolve about 0.25 microseconds.
//
// Why Double and not Int64: CET hands 64-bit integers to Lua as LuaJIT cdata ("123LL"), built by
// compiling a `return <n>ll` chunk on every call. Such values need tonumber() before json.encode
// or string.format("%d"). A Double arrives as a plain Lua number.
//
// This is wall-clock time, not a monotonic clock: if Windows steps the system time, so does
// Net_NowMs(). Two game instances on one PC read the same clock, which is what the scoreboard needs.

#include <cstdint>

namespace coopnet
{
// 100 ns FILETIME ticks from 1601-01-01 to 1970-01-01 (UTC).
inline constexpr int64_t kFileTimeTicksAtUnixEpoch = 116'444'736'000'000'000LL;
inline constexpr int64_t kFileTimeTicksPerMs = 10'000;

// Converts a FILETIME tick count (100 ns units since 1601-01-01 UTC) to Unix epoch milliseconds.
// The whole milliseconds are exact; the fraction keeps the 100 ns resolution. Earlier than 1970
// gives a negative value.
constexpr double FileTimeTicksToUnixMs(uint64_t aTicks) noexcept
{
    const int64_t sinceEpoch = static_cast<int64_t>(aTicks) - kFileTimeTicksAtUnixEpoch;
    const int64_t wholeMs = sinceEpoch / kFileTimeTicksPerMs;
    const int64_t remainderTicks = sinceEpoch % kFileTimeTicksPerMs;
    return static_cast<double>(wholeMs) +
           static_cast<double>(remainderTicks) / static_cast<double>(kFileTimeTicksPerMs);
}

// Current UTC time in milliseconds since the Unix epoch (GetSystemTimePreciseAsFileTime).
double UnixNowMs() noexcept;
} // namespace coopnet
