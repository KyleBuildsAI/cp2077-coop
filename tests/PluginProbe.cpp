// Loads CP2077CoopNet.dll outside the game and calls the two exports that do not need the game:
// Supports() and Query(). Main() is deliberately never called (it resolves game addresses).
//
//   coopnet_plugin_probe.exe path\to\CP2077CoopNet.dll

#include <RED4ext/Common.hpp>

#include <RED4ext/Api/ApiVersion.hpp>
#include <RED4ext/Api/v1/PluginInfo.hpp>
#include <RED4ext/Api/v1/Runtime.hpp>

#include "core/Version.hpp"

#include <Windows.h>

#include <cstdio>
#include <cstdlib>

int wmain(int argc, wchar_t** argv)
{
    if (argc < 2)
    {
        std::fputs("usage: coopnet_plugin_probe <plugin.dll>\n", stderr);
        return EXIT_FAILURE;
    }
    HMODULE module = LoadLibraryW(argv[1]);
    if (module == nullptr)
    {
        std::fprintf(stderr, "LoadLibrary failed: %lu\n", GetLastError());
        return EXIT_FAILURE;
    }

    using SupportsFn = uint32_t (*)();
    using QueryFn = void (*)(RED4ext::v1::PluginInfo*);
    auto supports = reinterpret_cast<SupportsFn>(GetProcAddress(module, "Supports"));
    auto query = reinterpret_cast<QueryFn>(GetProcAddress(module, "Query"));
    auto mainExport = GetProcAddress(module, "Main");
    if (supports == nullptr || query == nullptr || mainExport == nullptr)
    {
        std::fputs("missing export(s)\n", stderr);
        FreeLibrary(module);
        return EXIT_FAILURE;
    }

    const uint32_t apiVersion = supports();
    RED4ext::v1::PluginInfo info{};
    query(&info);
    const RED4ext::v1::FileVer expectedRuntime = RED4EXT_V1_RUNTIME_VERSION_2_31;

    std::printf("Supports() = %u (RED4EXT_API_VERSION_1 = %u)\n", apiVersion, RED4EXT_API_VERSION_1);
    std::wprintf(L"Query(): name=%ls author=%ls\n", info.name, info.author);
    std::printf("  version=%u.%u.%u sdk=%u.%u.%u runtime=%u.%u.%u.%u\n", info.version.major, info.version.minor,
                info.version.patch, info.sdk.major, info.sdk.minor, info.sdk.patch, info.runtime.major,
                info.runtime.minor, info.runtime.build, info.runtime.revision);

    const bool runtimeOk = info.runtime.major == expectedRuntime.major && info.runtime.minor == expectedRuntime.minor &&
                           info.runtime.build == expectedRuntime.build &&
                           info.runtime.revision == expectedRuntime.revision;
    // The version RED4ext logs ("CP2077CoopNet (version: x.y.z, ...) has been loaded") must be the CMake one.
    const bool versionOk = info.version.major == coopnet::kVersionMajor &&
                           info.version.minor == coopnet::kVersionMinor &&
                           info.version.patch == coopnet::kVersionPatch;
    std::printf("  expected version=%u.%u.%u -> %s\n", coopnet::kVersionMajor, coopnet::kVersionMinor,
                coopnet::kVersionPatch, versionOk ? "match" : "MISMATCH");
    const bool ok = apiVersion == RED4EXT_API_VERSION_1 && runtimeOk && versionOk && info.sdk.major == 1;
    FreeLibrary(module);
    std::puts(ok ? "PROBE PASS" : "PROBE FAIL");
    return ok ? EXIT_SUCCESS : EXIT_FAILURE;
}
