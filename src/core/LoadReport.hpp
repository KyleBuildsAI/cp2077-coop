#pragma once

// The single startup line the plugin writes to its RED4ext log once its natives are registered.
// The Phase 1 bench check greps for "registered Net_* natives (N/N)".
//
//   CP2077CoopNet 0.1.1 proto 1: registered Net_* natives (10/10): Net_Connect, ..., Net_Version;
//   scripts added: <plugin folder>\Scripts
//
// (one line in the log; wrapped here.) When something went wrong the same line names it:
// "MISSING: Net_X" for natives that did not register, "scripts NOT added: <reason>" for the folder.

#include <array>
#include <string>
#include <string_view>
#include <vector>

namespace coopnet
{
// Every global native the plugin registers, in registration order.
inline constexpr std::array<std::string_view, 10> kNativeNames = {
    "Net_Connect", "Net_ConnectRoom", "Net_Disconnect", "Net_Send",  "Net_SendTo",
    "Net_Poll",    "Net_Stats",       "Net_LocalId",    "Net_NowMs", "Net_Version",
};

inline constexpr std::string_view kRegisteredMarker = "registered Net_* natives";

struct LoadReport
{
    std::vector<std::string> registered; // natives that RED4ext accepted, in order
    std::string scriptsPath;             // UTF-8 path handed to sdk->scripts->Add
    bool scriptsAdded = false;
    std::string scriptsError; // why the folder was not added (empty when added)
};

// Names from kNativeNames that are not in aReport.registered.
std::vector<std::string> MissingNatives(const LoadReport& aReport);

// True when every native registered and the Scripts folder was added.
bool IsLoadComplete(const LoadReport& aReport);

// The one-line summary described above (no trailing newline).
std::string FormatLoadReport(const LoadReport& aReport);
} // namespace coopnet
