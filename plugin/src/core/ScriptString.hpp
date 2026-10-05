#pragma once

// Writes a native's String result into the slot the caller handed us.
//
// RED4ext SDK 1.0.0 CString: the move assignment (CString-inl.hpp) memmoves the new text over the
// destination without freeing the destination's old heap buffer (strings of 20 bytes or more live
// on the heap). The copy assignment calls the game's own CString_copy, which releases it. A
// redscript caller such as `let raw = Net_Poll();` in a loop can hand the native the same live
// local every time, so `*aOut = CString(...)` (a prvalue, which picks the move assignment) would
// leak one buffer per call. Copying from a named value, as the SDK's function_registration example
// does, is correct whether the slot is fresh (CET) or live (redscript).
//
// Templated on the string type so the unit tests can check the assignment it uses with a mock.

#include <cstdint>
#include <string_view>

namespace coopnet
{
template<typename TString>
void AssignScriptString(TString* aOut, std::string_view aText)
{
    if (aOut == nullptr)
    {
        return;
    }
    const TString result(aText.empty() ? "" : aText.data(), static_cast<uint32_t>(aText.size()));
    *aOut = result; // copy assignment on purpose, see above
}
} // namespace coopnet
