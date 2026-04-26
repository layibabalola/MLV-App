/*!
 * \file test_phase3_parity.cpp
 * \brief Phase 3 parity harness skeleton. The initial Phase B contract is
 *        that missing fixtures are explicit, recorded, and skipped rather
 *        than silently absent.
 */

#include "../common/minitest.h"
#include "../common/repo_paths.h"
#include "../common/test_artifacts.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QString>
#include <QTextStream>

namespace {

QString phase3ManifestPath()
{
    return repo_file_path(QStringLiteral("tests/fixtures/phase3_baselines/golden.json"));
}

void recordMissingManifest()
{
    test_artifacts::record("phase3.par.missing_fixture.manifest",
                           "tests/fixtures/phase3_baselines/golden.json is not present yet; Phase B validates the harness skeleton only.");

    QString outDir =
        repo_file_path(QStringLiteral(".claude-state/profiling/phase3-parity-missing"));
    if (outDir.isEmpty())
    {
        outDir = QDir::current().filePath(
            QStringLiteral(".claude-state/profiling/phase3-parity-missing"));
    }
    QDir().mkpath(outDir);
    QFile file(QDir(outDir).filePath(QStringLiteral("README.txt")));
    if (file.open(QIODevice::WriteOnly | QIODevice::Text | QIODevice::Truncate))
    {
        QTextStream out(&file);
        out << "Phase 3 PAR manifest is missing by design in the Phase B "
               "harness-only slice.\n";
        out << "Expected future path: tests/fixtures/phase3_baselines/golden.json\n";
    }
}

bool loadManifest(QJsonObject * object)
{
    const QString path = phase3ManifestPath();
    if (!QFileInfo::exists(path))
    {
        recordMissingManifest();
        return false;
    }

    QFile file(path);
    ASSERT_TRUE(file.open(QIODevice::ReadOnly | QIODevice::Text));
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(file.readAll(), &parseError);
    ASSERT_EQ(static_cast<int>(QJsonParseError::NoError), static_cast<int>(parseError.error));
    ASSERT_TRUE(document.isObject());
    *object = document.object();
    return true;
}

} // namespace

TEST(Phase3_PAR, ManifestSchemaIsValid)
{
    QJsonObject manifest;
    if (!loadManifest(&manifest))
    {
        SKIP_TEST("Phase 3 PAR manifest absent; missing-fixture artifact recorded.");
    }

    ASSERT_TRUE(manifest.contains(QStringLiteral("schema_version")));
    ASSERT_TRUE(manifest.contains(QStringLiteral("clips")));
    ASSERT_TRUE(manifest.value(QStringLiteral("clips")).isArray());
}

TEST(Phase3_PAR, SerialPathByteIdentity)
{
    QJsonObject manifest;
    if (!loadManifest(&manifest))
    {
        SKIP_TEST("Phase 3 PAR byte-identity fixtures absent; harness skeleton recorded the gap.");
    }

    test_artifacts::record("phase3.par.serial_path_harness", "manifest-present");
    ASSERT_TRUE(manifest.value(QStringLiteral("clips")).isArray());
}

TEST(Phase3_PAR, PSNRWithinTolerance)
{
    QJsonObject manifest;
    if (!loadManifest(&manifest))
    {
        SKIP_TEST("Phase 3 PAR PSNR fixtures absent; harness skeleton recorded the gap.");
    }

    test_artifacts::record("phase3.par.psnr_harness", "manifest-present");
    ASSERT_TRUE(manifest.value(QStringLiteral("clips")).isArray());
}
