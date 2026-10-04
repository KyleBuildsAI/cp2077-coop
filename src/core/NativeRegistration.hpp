#pragma once

// Registers one global native and checks every step whose result the RTTI system lets us see.
//
// RED4ext SDK 1.0.0: CBaseFunction::AddParam and SetReturnType return false (and change nothing)
// when the type name does not resolve in RTTI, and CRTTISystem::RegisterFunction returns void. So
// the only way to know the game accepted a function is to look it up again by name, which is also
// how CET finds Game.<name>.
//
// The template takes the RTTI system and the function by type so the unit tests can drive it with
// fakes; Main.cpp instantiates it with RED4ext::CRTTISystem and RED4ext::CGlobalFunction.

#include <initializer_list>
#include <string>

namespace coopnet
{
struct NativeParam
{
    const char* type;
    const char* name;
};

// Returns an empty string when the function was registered and RTTI returns it under aName,
// otherwise the step that failed. A function whose signature could not be completed is not
// registered at all: CET builds the call from the RTTI parameter list, and a native that reads
// more parameters than CET pushed would read past the call frame. A missing native is a clean,
// reported failure instead.
template<typename TRtti, typename TFunction>
std::string RegisterNative(TRtti& aRtti, TFunction& aFunction, const char* aName,
                           std::initializer_list<NativeParam> aParams, const char* aReturnType)
{
    for (const NativeParam& param : aParams)
    {
        if (!aFunction.AddParam(param.type, param.name))
        {
            return std::string("parameter '") + param.name + "' of type " + param.type +
                   " not added: type not in RTTI, native not registered";
        }
    }
    if (aReturnType != nullptr && !aFunction.SetReturnType(aReturnType))
    {
        return std::string("return type ") + aReturnType + " not set: type not in RTTI, native not registered";
    }
    aRtti.RegisterFunction(&aFunction);
    const auto* found = aRtti.GetFunction(aName);
    if (found == nullptr)
    {
        return "RTTI lookup by name found nothing after RegisterFunction";
    }
    if (found != &aFunction)
    {
        return "RTTI lookup by name returned a different function (name already taken?)";
    }
    return {};
}
} // namespace coopnet
