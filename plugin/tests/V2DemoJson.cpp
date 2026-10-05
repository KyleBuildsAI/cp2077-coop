#include "V2DemoJson.hpp"

#include <charconv>
#include <cmath>
#include <cstdio>

namespace coopnet::v2::demo
{
JsonValue::JsonValue(bool aValue)
    : m_type(Type::Bool)
    , m_bool(aValue)
{
}

JsonValue::JsonValue(int aValue)
    : m_type(Type::Int)
    , m_int(aValue)
{
}

JsonValue::JsonValue(int64_t aValue)
    : m_type(Type::Int)
    , m_int(aValue)
{
}

JsonValue::JsonValue(uint32_t aValue)
    : m_type(Type::Uint)
    , m_uint(aValue)
{
}

JsonValue::JsonValue(uint64_t aValue)
    : m_type(Type::Uint)
    , m_uint(aValue)
{
}

JsonValue::JsonValue(double aValue)
    : m_type(std::isfinite(aValue) ? Type::Number : Type::Null)
    , m_number(aValue)
{
}

JsonValue::JsonValue(const char* aValue)
    : m_type(Type::String)
    , m_string(aValue)
{
}

JsonValue::JsonValue(std::string aValue)
    : m_type(Type::String)
    , m_string(std::move(aValue))
{
}

JsonValue::JsonValue(std::string_view aValue)
    : m_type(Type::String)
    , m_string(aValue)
{
}

JsonValue JsonValue::Array(std::initializer_list<JsonValue> aItems)
{
    JsonValue value;
    value.m_type = Type::Array;
    value.m_items.assign(aItems.begin(), aItems.end());
    return value;
}

JsonValue JsonValue::Object()
{
    JsonValue value;
    value.m_type = Type::Object;
    return value;
}

JsonValue& JsonValue::Set(std::string_view aKey, JsonValue aValue)
{
    for (auto& [key, existing] : m_fields)
    {
        if (key == aKey)
        {
            existing = std::move(aValue);
            return existing;
        }
    }
    m_fields.emplace_back(std::string(aKey), std::move(aValue));
    return m_fields.back().second;
}

JsonValue& JsonValue::Push(JsonValue aValue)
{
    m_items.push_back(std::move(aValue));
    return m_items.back();
}

std::string JsonValue::Dump(int aIndent) const
{
    std::string out;
    Write(out, aIndent, 0);
    out += '\n';
    return out;
}

std::string JsonEscape(std::string_view aText)
{
    std::string out = "\"";
    for (const char character : aText)
    {
        const auto byte = static_cast<unsigned char>(character);
        if (character == '"' || character == '\\')
        {
            out += '\\';
            out += character;
        }
        else if (byte < 0x20)
        {
            char buffer[8];
            std::snprintf(buffer, sizeof(buffer), "\\u%04x", byte);
            out += buffer;
        }
        else
        {
            out += character;
        }
    }
    return out + "\"";
}

void JsonValue::Write(std::string& aOut, int aIndent, int aDepth) const
{
    const auto newline = [&](int aLevel) {
        if (aIndent > 0)
        {
            aOut += '\n';
            aOut.append(static_cast<size_t>(aIndent * aLevel), ' ');
        }
    };
    switch (m_type)
    {
    case Type::Null:
        aOut += "null";
        return;
    case Type::Bool:
        aOut += m_bool ? "true" : "false";
        return;
    case Type::Int:
        aOut += std::to_string(m_int);
        return;
    case Type::Uint:
        aOut += std::to_string(m_uint);
        return;
    case Type::Number:
    {
        char buffer[64];
        const auto result = std::to_chars(buffer, buffer + sizeof(buffer), m_number);
        aOut.append(buffer, result.ptr);
        return;
    }
    case Type::String:
        aOut += JsonEscape(m_string);
        return;
    case Type::Array:
        aOut += '[';
        for (size_t index = 0; index < m_items.size(); ++index)
        {
            aOut += index ? "," : "";
            newline(aDepth + 1);
            m_items[index].Write(aOut, aIndent, aDepth + 1);
        }
        if (!m_items.empty())
        {
            newline(aDepth);
        }
        aOut += ']';
        return;
    case Type::Object:
        aOut += '{';
        for (size_t index = 0; index < m_fields.size(); ++index)
        {
            aOut += index ? "," : "";
            newline(aDepth + 1);
            aOut += JsonEscape(m_fields[index].first);
            aOut += ": ";
            m_fields[index].second.Write(aOut, aIndent, aDepth + 1);
        }
        if (!m_fields.empty())
        {
            newline(aDepth);
        }
        aOut += '}';
        return;
    }
}
} // namespace coopnet::v2::demo
