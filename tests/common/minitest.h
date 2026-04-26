#ifndef MLV_APP_MINITEST_H
#define MLV_APP_MINITEST_H

#include <exception>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace minitest {

struct Failure : public std::exception {
    explicit Failure(std::string message_in) : message(std::move(message_in)) {}
    const char * what() const noexcept override { return message.c_str(); }
    std::string message;
};

struct Skip : public std::exception {
    explicit Skip(std::string message_in) : message(std::move(message_in)) {}
    const char * what() const noexcept override { return message.c_str(); }
    std::string message;
};

struct TestCase {
    const char * suite;
    const char * name;
    void (*fn)();
};

inline std::vector<TestCase> & registry()
{
    static std::vector<TestCase> cases;
    return cases;
}

inline int & assertion_count()
{
    static int count = 0;
    return count;
}

inline int & skip_count()
{
    static int count = 0;
    return count;
}

inline std::string & active_filter()
{
    static std::string value;
    return value;
}

inline void set_filter(const std::string & value)
{
    active_filter() = value;
}

inline void note_assertion()
{
    assertion_count() += 1;
}

struct Registrar {
    Registrar(const char * suite, const char * name, void (*fn)())
    {
        registry().push_back({suite, name, fn});
    }
};

template <typename T>
std::string stringify(const T & value)
{
    std::ostringstream stream;
    stream << value;
    return stream.str();
}

[[noreturn]] inline void fail(const char * file,
                              int line,
                              const std::string & expression,
                              const std::string & details = std::string())
{
    std::ostringstream stream;
    stream << file << ":" << line << ": assertion failed: " << expression;
    if (!details.empty()) {
        stream << " (" << details << ")";
    }
    throw Failure(stream.str());
}

[[noreturn]] inline void skip(const char * file,
                              int line,
                              const std::string & reason)
{
    std::ostringstream stream;
    stream << file << ":" << line << ": skipped: " << reason;
    throw Skip(stream.str());
}

inline int run_all()
{
    int failed = 0;
    for (const TestCase & test : registry()) {
        const std::string test_name =
            std::string(test.suite) + "." + std::string(test.name);
        if (!active_filter().empty()) {
            const std::string & filter = active_filter();
            const bool suffix_wildcard =
                !filter.empty() && filter[filter.size() - 1] == '*';
            const std::string prefix =
                suffix_wildcard ? filter.substr(0, filter.size() - 1) : filter;
            if (suffix_wildcard) {
                bool matches = test_name.compare(0, prefix.size(), prefix) == 0;
                if (!matches && !prefix.empty() && prefix[prefix.size() - 1] == '_') {
                    std::string dotted_prefix = prefix;
                    dotted_prefix[dotted_prefix.size() - 1] = '.';
                    matches = test_name.compare(0, dotted_prefix.size(), dotted_prefix) == 0;
                }
                if (!matches) {
                    continue;
                }
            } else if (test_name != filter) {
                continue;
            }
        }
        try {
            test.fn();
            std::cout << "[PASS] " << test.suite << "." << test.name << "\n";
        } catch (const Skip & skip_error) {
            skip_count() += 1;
            std::cout << "[SKIP] " << test.suite << "." << test.name
                      << " - " << skip_error.what() << "\n";
        } catch (const Failure & failure) {
            failed += 1;
            std::cerr << "[FAIL] " << test.suite << "." << test.name
                      << " - " << failure.what() << "\n";
        } catch (const std::exception & error) {
            failed += 1;
            std::cerr << "[FAIL] " << test.suite << "." << test.name
                      << " - unexpected exception: " << error.what() << "\n";
        } catch (...) {
            failed += 1;
            std::cerr << "[FAIL] " << test.suite << "." << test.name
                      << " - unknown exception\n";
        }
    }

    std::cout << "[SUMMARY] tests=" << registry().size()
              << " assertions=" << assertion_count()
              << " skipped=" << skip_count()
              << " failed=" << failed << "\n";
    return failed;
}

} // namespace minitest

#define TEST(SUITE, NAME) \
    static void SUITE##_##NAME(); \
    static ::minitest::Registrar registrar_##SUITE##_##NAME(#SUITE, #NAME, &SUITE##_##NAME); \
    static void SUITE##_##NAME()

#define ASSERT_TRUE(EXPR) \
    do { \
        ::minitest::note_assertion(); \
        if (!(EXPR)) { \
            ::minitest::fail(__FILE__, __LINE__, #EXPR); \
        } \
    } while (0)

#define ASSERT_FALSE(EXPR) ASSERT_TRUE(!(EXPR))

#define ASSERT_EQ(EXPECTED, ACTUAL) \
    do { \
        ::minitest::note_assertion(); \
        const auto & expected_value = (EXPECTED); \
        const auto & actual_value = (ACTUAL); \
        if (!(expected_value == actual_value)) { \
            ::minitest::fail(__FILE__, __LINE__, #ACTUAL " == " #EXPECTED, \
                std::string("expected=") + ::minitest::stringify(expected_value) + \
                ", actual=" + ::minitest::stringify(actual_value)); \
        } \
    } while (0)

#define ASSERT_NE(EXPECTED, ACTUAL) \
    do { \
        ::minitest::note_assertion(); \
        const auto & expected_value = (EXPECTED); \
        const auto & actual_value = (ACTUAL); \
        if (expected_value == actual_value) { \
            ::minitest::fail(__FILE__, __LINE__, #ACTUAL " != " #EXPECTED, \
                std::string("both=") + ::minitest::stringify(actual_value)); \
        } \
    } while (0)

#define ASSERT_NEAR(EXPECTED, ACTUAL, EPSILON) \
    do { \
        ::minitest::note_assertion(); \
        const auto expected_value = (EXPECTED); \
        const auto actual_value = (ACTUAL); \
        const auto epsilon_value = (EPSILON); \
        if (!((actual_value >= (expected_value - epsilon_value)) && (actual_value <= (expected_value + epsilon_value)))) { \
            ::minitest::fail(__FILE__, __LINE__, #ACTUAL " ~= " #EXPECTED, \
                std::string("expected=") + ::minitest::stringify(expected_value) + \
                ", actual=" + ::minitest::stringify(actual_value) + \
                ", epsilon=" + ::minitest::stringify(epsilon_value)); \
        } \
    } while (0)

#define SKIP_TEST(REASON) \
    do { \
        ::minitest::skip(__FILE__, __LINE__, (REASON)); \
    } while (0)

#endif
