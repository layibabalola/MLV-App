#include "../common/minitest.h"
#include "../common/repo_paths.h"
#include "../common/test_artifacts.h"
#include "../common/test_runtime.h"

#include "../../src/debug/ForceSingleThread.h"
#include "../../src/debug/StageTimingCsvSink.h"

#include <QGuiApplication>
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

    const auto & actual = test_artifacts::all();

    auto is_optional_gpu_key = [](const std::string & key) -> bool
    {
        return key.find(".gpu.") != std::string::npos;
    };

    for (const auto & expected_entry : expected) {
        const auto actual_it = actual.find(expected_entry.first);
        if (actual_it == actual.end()) {
            if (is_optional_gpu_key(expected_entry.first)) {
                continue;
            }
            if (error_message) {
                *error_message = "Golden artifact missing actual key: " + expected_entry.first;
            }
            return false;
        }
        if (actual_it->second != expected_entry.second) {
            if (error_message) {
                *error_message = "Golden artifact mismatch at key: " + expected_entry.first;
            }
            return false;
        }
    }

    for (const auto & actual_entry : actual) {
        if (expected.find(actual_entry.first) == expected.end()) {
            if (error_message) {
                *error_message = "Golden artifact contains unexpected key: " + actual_entry.first;
            }
            return false;
        }
    }

    return true;
}

int main(int argc, char ** argv)
{
    test_runtime::force_single_threaded_pipeline();
    mlvapp_force_singlethread_init();
    test_runtime::prefer_desktop_opengl_on_windows();
#ifdef Q_OS_WIN
    if (qEnvironmentVariableIsEmpty("QT_QPA_PLATFORM")) {
        qputenv("QT_QPA_PLATFORM", QByteArrayLiteral("windows"));
    }
#else
    if (qEnvironmentVariableIsEmpty("QT_QPA_PLATFORM")) {
        qputenv("QT_QPA_PLATFORM", QByteArrayLiteral("offscreen"));
    }
#endif
    QGuiApplication app(argc, argv);

    std::string hash_output_path;
    std::string stage_csv_path;
    std::string test_filter;
    QString golden_input_path;
    for (int index = 1; index < argc; ++index) {
        const std::string argument = argv[index];
        if (argument == "--hash-output" && (index + 1) < argc) {
            hash_output_path = argv[++index];
        } else if (argument == "--profile-stages-to-csv" && (index + 1) < argc) {
            stage_csv_path = argv[++index];
        } else if (argument.rfind("--profile-stages-to-csv=", 0) == 0) {
            stage_csv_path = argument.substr(std::string("--profile-stages-to-csv=").size());
        } else if (argument == "--check-golden") {
            if ((index + 1) < argc && std::string(argv[index + 1]).rfind("--", 0) != 0) {
                golden_input_path = QString::fromLocal8Bit(argv[++index]);
            } else {
                golden_input_path = repo_file_path(QStringLiteral("tests/fixtures/golden/pipeline_hashes.json"));
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

    if (!stage_csv_path.empty()
        && stage_timing_csv_sink_open(stage_csv_path.c_str()) == 0) {
        std::cerr << "[ERROR] Could not open stage CSV sink: " << stage_csv_path << "\n";
        return 4;
    }

    const int failed = minitest::run_all();
    stage_timing_csv_sink_close();

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
