#pragma once

// The single startup line the plugin writes to its RED4ext log at RTTI post-register.
// The Phase 1 bench check greps for "registered Net_* natives (N/N)".
//
//   CP2077CoopNet 0.1.2 proto 1: registered Net_* natives (10/10): Net_Connect, ..., Net_Version;
//   scripts added: <plugin folder>\Scripts
//
// (one line in the log; wrapped here.) A native counts as registered only when its parameter and
// return types resolved in RTTI and the RTTI system returns that same function when looked up by
// name after RegisterFunction (see NativeRegistration.hpp). When something went wrong the same
// line names it: "MISSING: Net_X (<failed step>)" for natives that did not pass, and
// "scripts NOT added: <reason>" for the folder.
//
// What the line cannot show: that a call from CET or redscript works. The in-game console check
// in INSTALL_PHASE1.md covers that.

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

struct NativeFailure
{
    std::string name;
    std::string reason; // the step that failed
};

struct LoadReport
{
    std::vector<std::string> registered; // natives that passed every registration check, in order
    std::vector<NativeFailure> failed;   // natives that did not, with the failed step
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
