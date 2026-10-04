#include "Clock.hpp"

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

namespace coopnet
{
double UnixNowMs() noexcept
{
    FILETIME now{};
    GetSystemTimePreciseAsFileTime(&now);
    const uint64_t ticks = (static_cast<uint64_t>(now.dwHighDateTime) << 32) | now.dwLowDateTime;
    return FileTimeTicksToUnixMs(ticks);
}
} // namespace coopnet
