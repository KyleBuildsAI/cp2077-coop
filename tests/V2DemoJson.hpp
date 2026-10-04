#pragma once

// A minimal JSON document builder for the demo client's report (the same shape client_v2.py
// writes with json.dumps). Doubles are written in the shortest form that reads back exactly;
// NaN and infinities become null. Strings are UTF-8 and only control characters, quotes and
// backslashes are escaped.

#include <cstdint>
#include <initializer_list>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace coopnet::v2::demo
{
class JsonValue
{
public:
    enum class Type : uint8_t
    {
        Null,
        Bool,
        Int,
        Uint,
        Number,
        String,
        Array,
        Object,
    };

    JsonValue() = default;
    JsonValue(std::nullptr_t) {}
    JsonValue(bool aValue);
    JsonValue(int aValue);
    JsonValue(int64_t aValue);
    JsonValue(uint32_t aValue);
    JsonValue(uint64_t aValue);
    JsonValue(double aValue);
    JsonValue(const char* aValue);
    JsonValue(std::string aValue);
    JsonValue(std::string_view aValue);
    template <typename T>
    JsonValue(const std::optional<T>& aValue)
    {
        if (aValue)
        {
            *this = JsonValue(*aValue);
        }
    }

    static JsonValue Array(std::initializer_list<JsonValue> aItems = {});
    static JsonValue Object();

    // Object: sets (or replaces) a field and returns it. Array: appends.
    JsonValue& Set(std::string_view aKey, JsonValue aValue);
    JsonValue& Push(JsonValue aValue);

    [[nodiscard]] Type GetType() const
    {
        return m_type;
    }
    [[nodiscard]] std::string Dump(int aIndent = 1) const;

private:
    void Write(std::string& aOut, int aIndent, int aDepth) const;

    Type m_type = Type::Null;
    bool m_bool = false;
    int64_t m_int = 0;
    uint64_t m_uint = 0;
    double m_number = 0.0;
    std::string m_string;
    std::vector<JsonValue> m_items;
    std::vector<std::pair<std::string, JsonValue>> m_fields;
};

std::string JsonEscape(std::string_view aText);
} // namespace coopnet::v2::demo
