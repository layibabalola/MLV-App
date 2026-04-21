#include "test_artifacts.h"

#include <fstream>
#include <sstream>

namespace test_artifacts {

static std::map<std::string, std::string> g_artifacts;

static std::string escape_json(const std::string & value)
{
    std::ostringstream stream;
    for (char ch : value) {
        switch (ch) {
            case '\\': stream << "\\\\"; break;
            case '"': stream << "\\\""; break;
            case '\n': stream << "\\n"; break;
            case '\r': stream << "\\r"; break;
            case '\t': stream << "\\t"; break;
            default: stream << ch; break;
        }
    }
    return stream.str();
}

void clear()
{
    g_artifacts.clear();
}

void record(const std::string & key, const std::string & value)
{
    g_artifacts[key] = value;
}

const std::map<std::string, std::string> & all()
{
    return g_artifacts;
}

bool write_json(const std::string & path, std::string * error_message)
{
    std::ofstream stream(path.c_str(), std::ios::out | std::ios::trunc);
    if (!stream.is_open()) {
        if (error_message) {
            *error_message = "Could not open artifact path: " + path;
        }
        return false;
    }

    stream << "{\n";
    for (auto it = g_artifacts.begin(); it != g_artifacts.end(); ++it) {
        stream << "  \"" << escape_json(it->first) << "\": \"" << escape_json(it->second) << "\"";
        if (std::next(it) != g_artifacts.end()) {
            stream << ",";
        }
        stream << "\n";
    }
    stream << "}\n";
    return true;
}

} // namespace test_artifacts
