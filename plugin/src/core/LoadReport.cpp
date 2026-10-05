#include "LoadReport.hpp"

#include "Version.hpp"

#include <algorithm>

namespace coopnet
{
std::vector<std::string> MissingNatives(const LoadReport& aReport)
{
    std::vector<std::string> missing;
    for (const std::string_view name : kNativeNames)
    {
        if (std::find(aReport.registered.begin(), aReport.registered.end(), name) == aReport.registered.end())
        {
            missing.emplace_back(name);
        }
    }
    return missing;
}

bool IsLoadComplete(const LoadReport& aReport)
{
    return aReport.scriptsAdded && MissingNatives(aReport).empty();
}

namespace
{
void AppendJoined(std::string& aOut, const std::vector<std::string>& aNames)
{
    for (size_t index = 0; index < aNames.size(); ++index)
    {
        if (index > 0)
        {
            aOut += ", ";
        }
        aOut += aNames[index];
    }
}

// "Net_X (reason)" for every name that has a recorded failure, the bare name otherwise (for
// example when the post-register callback never reached that native).
std::vector<std::string> WithReasons(const std::vector<std::string>& aNames, const std::vector<NativeFailure>& aFailed)
{
    std::vector<std::string> annotated;
    annotated.reserve(aNames.size());
    for (const std::string& name : aNames)
    {
        const auto failure = std::find_if(aFailed.begin(), aFailed.end(),
                                          [&name](const NativeFailure& aFailure) { return aFailure.name == name; });
        annotated.push_back(failure == aFailed.end() ? name : name + " (" + failure->reason + ")");
    }
    return annotated;
}
} // namespace

std::string FormatLoadReport(const LoadReport& aReport)
{
    std::string line(kVersionString);
    line += ": ";
    line += kRegisteredMarker;
    line += " (" + std::to_string(aReport.registered.size()) + "/" + std::to_string(kNativeNames.size()) + "): ";
    if (aReport.registered.empty())
    {
        line += "none";
    }
    AppendJoined(line, aReport.registered);

    const std::vector<std::string> missing = MissingNatives(aReport);
    if (!missing.empty())
    {
        line += "; MISSING: ";
        AppendJoined(line, WithReasons(missing, aReport.failed));
    }

    if (aReport.scriptsAdded)
    {
        line += "; scripts added: " + aReport.scriptsPath;
    }
    else
    {
        line += "; scripts NOT added: ";
        line += aReport.scriptsError.empty() ? std::string("unknown reason") : aReport.scriptsError;
        if (!aReport.scriptsPath.empty())
        {
            line += " (" + aReport.scriptsPath + ")";
        }
    }
    return line;
}
} // namespace coopnet
