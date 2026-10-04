// CP2077CoopNet: RED4ext plugin exposing a small reliable/unreliable UDP transport to redscript
// and CET Lua as global native functions:
//
//   Net_Connect(host: String, port: Int32) -> Bool
//   Net_ConnectRoom(host: String, port: Int32, room: String) -> Bool
//   Net_Disconnect() -> Void
//   Net_Send(channel: Int32, payload: String) -> Bool
//   Net_SendTo(peer: Int32, channel: Int32, payload: String) -> Bool
//   Net_Poll() -> String          ("" when empty, otherwise "<sender>|<channel>|<payload>")
//   Net_Stats() -> String         (JSON)
//   Net_LocalId() -> Int32
//
// From CET: Game.Net_Connect("127.0.0.1", 11779), Game.Net_Poll(), ...

#include "core/Transport.hpp"

#include <RED4ext/RED4ext.hpp>

#include <Windows.h>

#include <exception>
#include <filesystem>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

namespace
{
constexpr std::string_view kDefaultRoom = "default";

RED4ext::v1::PluginHandle g_pluginHandle = nullptr;
const RED4ext::v1::Sdk* g_sdk = nullptr;
std::unique_ptr<coopnet::Transport> g_transport;

void Log(coopnet::LogLevel aLevel, std::string_view aMessage)
{
    if (g_sdk == nullptr || g_sdk->logger == nullptr)
    {
        return;
    }
    const std::string text(aMessage);
    switch (aLevel)
    {
    case coopnet::LogLevel::Info:
        g_sdk->logger->Info(g_pluginHandle, text.c_str());
        break;
    case coopnet::LogLevel::Warn:
        g_sdk->logger->Warn(g_pluginHandle, text.c_str());
        break;
    case coopnet::LogLevel::Error:
        g_sdk->logger->Error(g_pluginHandle, text.c_str());
        break;
    }
}

void LogException(const char* aNative, const std::exception& aError)
{
    Log(coopnet::LogLevel::Error, std::string(aNative) + " threw: " + aError.what());
}

std::string_view ToView(const RED4ext::CString& aText)
{
    const char* data = aText.c_str();
    return data == nullptr ? std::string_view{} : std::string_view(data, aText.Length());
}

void ReturnString(RED4ext::CString* aOut, std::string_view aText)
{
    if (aOut != nullptr)
    {
        *aOut = RED4ext::CString(aText.data(), static_cast<uint32_t>(aText.size()));
    }
}

// ---- natives ---------------------------------------------------------------------------------
// Every native reads all parameters, then skips ParamEnd (aFrame->code++), and never lets a C++
// exception unwind into the script VM.

void NetConnect(RED4ext::IScriptable*, RED4ext::CStackFrame* aFrame, bool* aOut, int64_t)
{
    RED4ext::CString host;
    int32_t port = 0;
    RED4ext::GetParameter(aFrame, &host);
    RED4ext::GetParameter(aFrame, &port);
    aFrame->code++;

    bool connected = false;
    try
    {
        connected = g_transport && g_transport->Connect(ToView(host), port, kDefaultRoom);
    }
    catch (const std::exception& error)
    {
        LogException("Net_Connect", error);
    }
    if (aOut != nullptr)
    {
        *aOut = connected;
    }
}

void NetConnectRoom(RED4ext::IScriptable*, RED4ext::CStackFrame* aFrame, bool* aOut, int64_t)
{
    RED4ext::CString host;
    int32_t port = 0;
    RED4ext::CString room;
    RED4ext::GetParameter(aFrame, &host);
    RED4ext::GetParameter(aFrame, &port);
    RED4ext::GetParameter(aFrame, &room);
    aFrame->code++;

    bool connected = false;
    try
    {
        connected = g_transport && g_transport->Connect(ToView(host), port, ToView(room));
    }
    catch (const std::exception& error)
    {
        LogException("Net_ConnectRoom", error);
    }
    if (aOut != nullptr)
    {
        *aOut = connected;
    }
}

void NetDisconnect(RED4ext::IScriptable*, RED4ext::CStackFrame* aFrame, void*, int64_t)
{
    aFrame->code++;
    try
    {
        if (g_transport)
        {
            g_transport->Disconnect();
        }
    }
    catch (const std::exception& error)
    {
        LogException("Net_Disconnect", error);
    }
}

void NetSend(RED4ext::IScriptable*, RED4ext::CStackFrame* aFrame, bool* aOut, int64_t)
{
    int32_t channel = 0;
    RED4ext::CString payload;
    RED4ext::GetParameter(aFrame, &channel);
    RED4ext::GetParameter(aFrame, &payload);
    aFrame->code++;

    bool queued = false;
    try
    {
        queued = g_transport && g_transport->Send(channel, ToView(payload));
    }
    catch (const std::exception& error)
    {
        LogException("Net_Send", error);
    }
    if (aOut != nullptr)
    {
        *aOut = queued;
    }
}

void NetSendTo(RED4ext::IScriptable*, RED4ext::CStackFrame* aFrame, bool* aOut, int64_t)
{
    int32_t peer = 0;
    int32_t channel = 0;
    RED4ext::CString payload;
    RED4ext::GetParameter(aFrame, &peer);
    RED4ext::GetParameter(aFrame, &channel);
    RED4ext::GetParameter(aFrame, &payload);
    aFrame->code++;

    bool queued = false;
    try
    {
        queued = g_transport && g_transport->Send(channel, ToView(payload), peer);
    }
    catch (const std::exception& error)
    {
        LogException("Net_SendTo", error);
    }
    if (aOut != nullptr)
    {
        *aOut = queued;
    }
}

void NetPoll(RED4ext::IScriptable*, RED4ext::CStackFrame* aFrame, RED4ext::CString* aOut, int64_t)
{
    aFrame->code++;
    std::string message;
    try
    {
        if (g_transport)
        {
            g_transport->Poll(message);
        }
    }
    catch (const std::exception& error)
    {
        LogException("Net_Poll", error);
        message.clear();
    }
    ReturnString(aOut, message);
}

void NetStats(RED4ext::IScriptable*, RED4ext::CStackFrame* aFrame, RED4ext::CString* aOut, int64_t)
{
    aFrame->code++;
    std::string stats = "{}";
    try
    {
        if (g_transport)
        {
            stats = g_transport->StatsJson();
        }
    }
    catch (const std::exception& error)
    {
        LogException("Net_Stats", error);
    }
    ReturnString(aOut, stats);
}

void NetLocalId(RED4ext::IScriptable*, RED4ext::CStackFrame* aFrame, int32_t* aOut, int64_t)
{
    aFrame->code++;
    if (aOut != nullptr)
    {
        *aOut = g_transport ? g_transport->LocalId() : 0;
    }
}

// ---- registration ----------------------------------------------------------------------------

struct NativeParam
{
    const char* type;
    const char* name;
};

template<typename TOut>
void RegisterGlobal(RED4ext::CRTTISystem* aRtti, const char* aName, RED4ext::ScriptingFunction_t<TOut> aHandler,
                    std::initializer_list<NativeParam> aParams, const char* aReturnType)
{
    auto* function = RED4ext::CGlobalFunction::Create(aName, aName, aHandler);
    if (function == nullptr)
    {
        Log(coopnet::LogLevel::Error, std::string("could not allocate native ") + aName);
        return;
    }
    function->flags = {.isNative = true, .isStatic = true};
    for (const auto& param : aParams)
    {
        function->AddParam(param.type, param.name);
    }
    if (aReturnType != nullptr)
    {
        function->SetReturnType(aReturnType);
    }
    aRtti->RegisterFunction(function);
}

void RegisterTypes()
{
}

void PostRegisterTypes()
{
    auto* rtti = RED4ext::CRTTISystem::Get();
    RegisterGlobal<bool*>(rtti, "Net_Connect", &NetConnect, {{"String", "host"}, {"Int32", "port"}}, "Bool");
    RegisterGlobal<bool*>(rtti, "Net_ConnectRoom", &NetConnectRoom,
                          {{"String", "host"}, {"Int32", "port"}, {"String", "room"}}, "Bool");
    RegisterGlobal<void*>(rtti, "Net_Disconnect", &NetDisconnect, {}, nullptr);
    RegisterGlobal<bool*>(rtti, "Net_Send", &NetSend, {{"Int32", "channel"}, {"String", "payload"}}, "Bool");
    RegisterGlobal<bool*>(rtti, "Net_SendTo", &NetSendTo,
                          {{"Int32", "peer"}, {"Int32", "channel"}, {"String", "payload"}}, "Bool");
    RegisterGlobal<RED4ext::CString*>(rtti, "Net_Poll", &NetPoll, {}, "String");
    RegisterGlobal<RED4ext::CString*>(rtti, "Net_Stats", &NetStats, {}, "String");
    RegisterGlobal<int32_t*>(rtti, "Net_LocalId", &NetLocalId, {}, "Int32");
    Log(coopnet::LogLevel::Info, "registered Net_* natives");
}

// ---- runtime check ---------------------------------------------------------------------------

bool ReadGameFileVersion(RED4ext::v1::FileVer& aVersion)
{
    wchar_t path[MAX_PATH] = {};
    const DWORD length = GetModuleFileNameW(nullptr, path, MAX_PATH);
    if (length == 0 || length >= MAX_PATH)
    {
        return false;
    }
    DWORD ignored = 0;
    const DWORD size = GetFileVersionInfoSizeW(path, &ignored);
    if (size == 0)
    {
        return false;
    }
    std::vector<BYTE> data(size);
    if (!GetFileVersionInfoW(path, 0, size, data.data()))
    {
        return false;
    }
    VS_FIXEDFILEINFO* info = nullptr;
    UINT infoLength = 0;
    if (!VerQueryValueW(data.data(), L"\\", reinterpret_cast<void**>(&info), &infoLength) || info == nullptr)
    {
        return false;
    }
    aVersion.major = HIWORD(info->dwFileVersionMS);
    aVersion.minor = LOWORD(info->dwFileVersionMS);
    aVersion.build = HIWORD(info->dwFileVersionLS);
    aVersion.revision = LOWORD(info->dwFileVersionLS);
    return true;
}

bool IsSupportedRuntime()
{
    RED4ext::v1::FileVer gameVersion{};
    if (!ReadGameFileVersion(gameVersion))
    {
        Log(coopnet::LogLevel::Error, "could not read the game's file version");
        return false;
    }
    const RED4ext::v1::FileVer required = RED4EXT_V1_RUNTIME_VERSION_2_31;
    const std::string found = std::to_string(gameVersion.major) + "." + std::to_string(gameVersion.minor) + "." +
                              std::to_string(gameVersion.build) + "." + std::to_string(gameVersion.revision);
    if (RED4ext::v1::CompareFileVer(gameVersion, required) != 0)
    {
        Log(coopnet::LogLevel::Error, "unsupported game version " + found + " (built for 2.31 = 3.0.80.51928)");
        return false;
    }
    Log(coopnet::LogLevel::Info, "game file version " + found + " (2.31) ok");
    return true;
}

// ---- bundled redscript declarations ------------------------------------------------------------

std::filesystem::path PluginDirectory()
{
    HMODULE module = nullptr;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                            reinterpret_cast<LPCWSTR>(&PluginDirectory), &module))
    {
        return {};
    }
    std::wstring path(MAX_PATH, L'\0');
    const DWORD length = GetModuleFileNameW(module, path.data(), static_cast<DWORD>(path.size()));
    if (length == 0 || length >= path.size())
    {
        return {};
    }
    path.resize(length);
    return std::filesystem::path(path).parent_path();
}

// The Net_* declarations live next to the DLL (Scripts/*.reds) and are handed to the redscript
// compiler only when this plugin actually loaded, so a missing or rejected DLL can never leave
// the game with script declarations for natives that do not exist.
void RegisterScripts(RED4ext::v1::PluginHandle aHandle, const RED4ext::v1::Sdk* aSdk)
{
    const std::filesystem::path scripts = PluginDirectory() / L"Scripts";
    std::error_code error;
    if (!std::filesystem::is_directory(scripts, error))
    {
        Log(coopnet::LogLevel::Warn, "no Scripts folder next to the plugin; Net_* must be declared elsewhere");
        return;
    }
    if (aSdk->scripts == nullptr || !aSdk->scripts->Add(aHandle, scripts.c_str()))
    {
        Log(coopnet::LogLevel::Error, "RED4ext refused the plugin's Scripts folder");
        return;
    }
    Log(coopnet::LogLevel::Info, "added " + scripts.string() + " to the redscript compilation");
}
} // namespace

#ifndef RED4EXT_API_VERSION_LATEST
// SDK 1.0.0 removed the unversioned aliases; v1 is the latest loader API (RED4ext 1.30 accepts it).
#define RED4EXT_API_VERSION_LATEST RED4EXT_API_VERSION_1
#endif

RED4EXT_C_EXPORT bool RED4EXT_CALL Main(RED4ext::v1::PluginHandle aHandle, RED4ext::v1::EMainReason aReason,
                                        const RED4ext::v1::Sdk* aSdk)
{
    switch (aReason)
    {
    case RED4ext::v1::EMainReason::Load:
    {
        g_pluginHandle = aHandle;
        g_sdk = aSdk;
        if (!IsSupportedRuntime())
        {
            return false;
        }
        try
        {
            g_transport = std::make_unique<coopnet::Transport>();
            g_transport->SetLogSink(&Log);
        }
        catch (const std::exception& error)
        {
            LogException("Main(Load)", error);
            return false;
        }
        auto* rtti = RED4ext::CRTTISystem::Get();
        rtti->AddRegisterCallback(RegisterTypes);
        rtti->AddPostRegisterCallback(PostRegisterTypes);
        RegisterScripts(aHandle, aSdk);
        Log(coopnet::LogLevel::Info, "CP2077CoopNet loaded (protocol v1)");
        break;
    }
    case RED4ext::v1::EMainReason::Unload:
    {
        // Joins the network thread (it is woken immediately, so this does not stall shutdown).
        g_transport.reset();
        g_sdk = nullptr;
        break;
    }
    }
    return true;
}

RED4EXT_C_EXPORT void RED4EXT_CALL Query(RED4ext::v1::PluginInfo* aInfo)
{
    aInfo->name = L"CP2077CoopNet";
    aInfo->author = L"CP2077 Coop";
    aInfo->version = RED4EXT_V1_SEMVER(0, 1, 0);
    aInfo->runtime = RED4EXT_V1_RUNTIME_VERSION_2_31;
    aInfo->sdk = RED4EXT_V1_SDK_VERSION_CURRENT;
}

RED4EXT_C_EXPORT uint32_t RED4EXT_CALL Supports()
{
    return RED4EXT_API_VERSION_LATEST;
}
