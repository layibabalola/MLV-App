#include "../common/minitest.h"
#include "../common/hash_helpers.h"
#include "../common/repo_paths.h"

#include <QByteArray>
#include <QDir>
#include <QDirIterator>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcess>
#include <QProcessEnvironment>
#include <QTemporaryDir>

#include <iostream>
#include <map>

static QString clip_fixture_path()
{
    return repo_file_path(QStringLiteral("tests/fixtures/clips/tiny_dual_iso.mlv"));
}

static QString clip_receipt_path()
{
    return repo_file_path(QStringLiteral("tests/fixtures/receipts/tiny_dual_iso_hq.marxml"));
}

static QString clip_manifest_path()
{
    return repo_file_path(QStringLiteral("tests/fixtures/golden/tiny_dual_iso_hq_dng_hashes.json"));
}

static QString batch_executable_path()
{
    const QString env_path = qEnvironmentVariable("MLVAPP_BATCH_EXE");
    if (!env_path.isEmpty()) {
        return env_path;
    }
    return QString();
}

static std::map<std::string, std::string> load_expected_hashes(const QString & path)
{
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly | QIODevice::Text)) {
        return {};
    }

    const QJsonDocument document = QJsonDocument::fromJson(file.readAll());
    if (!document.isObject()) {
        return {};
    }

    std::map<std::string, std::string> hashes;
    const QJsonObject object = document.object();
    for (auto it = object.begin(); it != object.end(); ++it) {
        hashes.emplace(it.key().toStdString(), it.value().toString().toStdString());
    }
    return hashes;
}

static std::map<std::string, std::string> collect_dng_hashes(const QString & directory)
{
    std::map<std::string, std::string> hashes;
    QDirIterator it(directory,
                    QStringList() << QStringLiteral("*.dng") << QStringLiteral("*.DNG"),
                    QDir::Files,
                    QDirIterator::Subdirectories);
    const QDir base_dir(directory);
    while (it.hasNext()) {
        const QString absolute_path = it.next();
        const QString relative_path =
            QDir::fromNativeSeparators(base_dir.relativeFilePath(absolute_path));
        hashes.emplace(relative_path.toStdString(), sha256_file(absolute_path));
    }
    return hashes;
}

TEST(ClipGolden, TinyDualIsoBatchExportMatchesGolden)
{
    const QString fixture_path = clip_fixture_path();
    if (!QFileInfo::exists(fixture_path)) {
        SKIP_TEST("Missing fixture clip tests/fixtures/clips/tiny_dual_iso.mlv");
    }

    const QString receipt_path = clip_receipt_path();
    if (!QFileInfo::exists(receipt_path)) {
        SKIP_TEST("Missing fixture receipt tests/fixtures/receipts/tiny_dual_iso_hq.marxml");
    }

    const QString manifest_path = clip_manifest_path();
    if (!QFileInfo::exists(manifest_path)) {
        SKIP_TEST("Missing golden manifest tests/fixtures/golden/tiny_dual_iso_hq_dng_hashes.json");
    }

    const QString batch_exe = batch_executable_path();
    if (batch_exe.isEmpty() || !QFileInfo::exists(batch_exe)) {
        SKIP_TEST("Set MLVAPP_BATCH_EXE to a built MLVApp binary with batch support");
    }

    const QString repo_root = find_repo_root();
    ASSERT_TRUE(!repo_root.isEmpty());

    const std::map<std::string, std::string> expected_hashes = load_expected_hashes(manifest_path);
    if (expected_hashes.empty()) {
        SKIP_TEST("Golden manifest is empty or invalid");
    }

    QTemporaryDir temp_dir;
    ASSERT_TRUE(temp_dir.isValid());
    const QString output_dir = temp_dir.filePath(QStringLiteral("out"));
    ASSERT_TRUE(QDir().mkpath(output_dir));

    QProcess process;
    QProcessEnvironment environment = QProcessEnvironment::systemEnvironment();
    environment.insert(QStringLiteral("MLVAPP_FORCE_THREADS"), QStringLiteral("1"));
    environment.insert(QStringLiteral("OMP_NUM_THREADS"), QStringLiteral("1"));
    environment.insert(QStringLiteral("OMP_DYNAMIC"), QStringLiteral("FALSE"));
    process.setProcessEnvironment(environment);
    process.setWorkingDirectory(repo_root);
    process.setProgram(batch_exe);
    process.setArguments(QStringList()
                         << QStringLiteral("--batch")
                         << QStringLiteral("--input") << fixture_path
                         << QStringLiteral("--output") << output_dir
                         << QStringLiteral("--receipt") << receipt_path);
    process.start();
    ASSERT_TRUE(process.waitForStarted());
    ASSERT_TRUE(process.waitForFinished(-1));
    ASSERT_EQ(0, process.exitCode());

    const std::map<std::string, std::string> actual_hashes = collect_dng_hashes(output_dir);
    ASSERT_EQ(expected_hashes.size(), actual_hashes.size());
    if (expected_hashes != actual_hashes) {
        std::cerr << "[ClipGolden] Expected hashes:\n";
        for (const auto & entry : expected_hashes) {
            std::cerr << "  " << entry.first << " " << entry.second << "\n";
        }
        std::cerr << "[ClipGolden] Actual hashes:\n";
        for (const auto & entry : actual_hashes) {
            std::cerr << "  " << entry.first << " " << entry.second << "\n";
        }
    }
    ASSERT_TRUE(expected_hashes == actual_hashes);
}
