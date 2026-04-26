#include "../common/minitest.h"
#include "../common/repo_paths.h"
#include "../common/test_artifacts.h"
#include "../common/test_runtime.h"

#include <QCoreApplication>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>

#include <iostream>
#include <map>
#include <string>

static bool compare_against_golden(const QString & golden_path, std::string * error_message)
{
    QFile golden_file(golden_path);
    if (!golden_file.open(QIODevice::ReadOnly | QIODevice::Text)) {
        if (error_message) {
            *error_message = "Could not open golden artifact file: " + golden_path.toStdString();
        }
        return false;
    }

    QJsonParseError parse_error;
    const QJsonDocument document = QJsonDocument::fromJson(golden_file.readAll(), &parse_error);
    if (parse_error.error != QJsonParseError::NoError || !document.isObject()) {
        if (error_message) {
            *error_message = "Could not parse golden artifact file: " + golden_path.toStdString();
        }
        return false;
    }

    std::map<std::string, std::string> expected;
    const QJsonObject object = document.object();
    for (auto it = object.begin(); it != object.end(); ++it) {
        expected.emplace(it.key().toStdString(), it.value().toString().toStdString());
    }

    if (expected != test_artifacts::all()) {
        if (error_message) {
            *error_message = "Golden artifact mismatch: " + golden_path.toStdString();
        }
        return false;
    }
    return true;
}

int main(int argc, char ** argv)
{
    test_runtime::force_single_threaded_pipeline();
    QCoreApplication app(argc, argv);

    std::string hash_output_path;
    std::string test_filter;
    QString golden_input_path;
    for (int index = 1; index < argc; ++index) {
        const std::string argument = argv[index];
        if (argument == "--hash-output" && (index + 1) < argc) {
            hash_output_path = argv[++index];
        } else if (argument == "--check-golden") {
            if ((index + 1) < argc && std::string(argv[index + 1]).rfind("--", 0) != 0) {
                golden_input_path = QString::fromLocal8Bit(argv[++index]);
            } else {
                golden_input_path = repo_file_path(QStringLiteral("tests/fixtures/golden/hashes.json"));
            }
        } else if (argument == "--gtest_filter" && (index + 1) < argc) {
            test_filter = argv[++index];
        } else if (argument.rfind("--gtest_filter=", 0) == 0) {
            test_filter = argument.substr(std::string("--gtest_filter=").size());
        }
    }

    if (!test_filter.empty()) {
        minitest::set_filter(test_filter);
    }

    const int failed = minitest::run_all();

    if (!hash_output_path.empty()) {
        std::string error_message;
        if (!test_artifacts::write_json(hash_output_path, &error_message)) {
            std::cerr << "[ERROR] " << error_message << "\n";
            return 2;
        }
    }

    if (!golden_input_path.isEmpty()) {
        std::string error_message;
        if (!compare_against_golden(golden_input_path, &error_message)) {
            std::cerr << "[ERROR] " << error_message << "\n";
            return 3;
        }
    }

    return failed == 0 ? 0 : 1;
}
