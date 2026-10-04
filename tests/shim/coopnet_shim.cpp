// Test-only C API over coopnet::Transport so LuaJIT FFI can stand in for the plugin's Net_* natives.
//
// Each handle is an independent Transport (own socket and network thread), so two NetProbe Lua
// states in one process behave like two game instances. cnshim_now_ms mirrors Net_NowMs:
// GetSystemTimePreciseAsFileTime converted to Unix epoch milliseconds with a sub-ms fraction.

#include "core/Transport.hpp"

#include <windows.h>

#include <cstdio>
#include <cstring>
#include <exception>
#include <string>
#include <string_view>

#define CNSHIM_API extern "C" __declspec(dllexport)

namespace
{
constexpr long long kFileTimeTicksAtUnixEpoch = 116'444'736'000'000'000LL;
constexpr long long kFileTimeTicksPerMs = 10'000;

struct Handle
{
    coopnet::Transport transport;
    std::string pending; // message that did not fit the caller's buffer
};

int CopyOut(const std::string& aText, char* aBuffer, int aCapacity)
{
    const int length = static_cast<int>(aText.size());
    if (aBuffer == nullptr || aCapacity < length)
    {
        return -2 - length; // caller must retry with at least `length` bytes
    }
    std::memcpy(aBuffer, aText.data(), aText.size());
    return length;
}

void ReportException(const char* aWhere, const std::exception& aError)
{
    std::fprintf(stderr, "[cnshim] %s threw: %s\n", aWhere, aError.what());
}
} // namespace

CNSHIM_API void* cnshim_create()
{
    try
    {
        auto* handle = new Handle();
        handle->transport.SetLogSink(
            [](coopnet::LogLevel aLevel, std::string_view aMessage)
            {
                if (aLevel != coopnet::LogLevel::Info)
                {
                    std::fprintf(stderr, "[cnshim] %.*s\n", static_cast<int>(aMessage.size()), aMessage.data());
                }
            });
        return handle;
    }
    catch (const std::exception& error)
    {
        ReportException("create", error);
        return nullptr;
    }
}

CNSHIM_API void cnshim_destroy(void* aHandle)
{
    delete static_cast<Handle*>(aHandle);
}

CNSHIM_API int cnshim_connect(void* aHandle, const char* aHost, int aPort, const char* aRoom)
{
    try
    {
        auto* handle = static_cast<Handle*>(aHandle);
        return handle->transport.Connect(aHost ? aHost : "", aPort, aRoom ? aRoom : "") ? 1 : 0;
    }
    catch (const std::exception& error)
    {
        ReportException("connect", error);
        return 0;
    }
}

CNSHIM_API void cnshim_disconnect(void* aHandle)
{
    try
    {
        static_cast<Handle*>(aHandle)->transport.Disconnect();
    }
    catch (const std::exception& error)
    {
        ReportException("disconnect", error);
    }
}

CNSHIM_API int cnshim_send(void* aHandle, int aChannel, const char* aPayload, int aLength)
{
    try
    {
        auto* handle = static_cast<Handle*>(aHandle);
        return handle->transport.Send(aChannel, std::string_view(aPayload, static_cast<size_t>(aLength))) ? 1 : 0;
    }
    catch (const std::exception& error)
    {
        ReportException("send", error);
        return 0;
    }
}

// Returns the message length (>= 0), -1 when the inbox is empty, or -2 - length when the buffer
// is too small (the message is kept for the next call).
CNSHIM_API int cnshim_poll(void* aHandle, char* aBuffer, int aCapacity)
{
    try
    {
        auto* handle = static_cast<Handle*>(aHandle);
        if (handle->pending.empty() && !handle->transport.Poll(handle->pending))
        {
            return -1;
        }
        const int result = CopyOut(handle->pending, aBuffer, aCapacity);
        if (result >= 0)
        {
            handle->pending.clear();
        }
        return result;
    }
    catch (const std::exception& error)
    {
        ReportException("poll", error);
        return -1;
    }
}

CNSHIM_API int cnshim_stats(void* aHandle, char* aBuffer, int aCapacity)
{
    try
    {
        return CopyOut(static_cast<Handle*>(aHandle)->transport.StatsJson(), aBuffer, aCapacity);
    }
    catch (const std::exception& error)
    {
        ReportException("stats", error);
        return -1;
    }
}

CNSHIM_API int cnshim_local_id(void* aHandle)
{
    return static_cast<Handle*>(aHandle)->transport.LocalId();
}

CNSHIM_API double cnshim_now_ms()
{
    FILETIME fileTime{};
    GetSystemTimePreciseAsFileTime(&fileTime);
    const long long ticks =
        (static_cast<long long>(fileTime.dwHighDateTime) << 32) | static_cast<long long>(fileTime.dwLowDateTime);
    const long long sinceEpoch = ticks - kFileTimeTicksAtUnixEpoch;
    return static_cast<double>(sinceEpoch / kFileTimeTicksPerMs) +
           static_cast<double>(sinceEpoch % kFileTimeTicksPerMs) / static_cast<double>(kFileTimeTicksPerMs);
}
