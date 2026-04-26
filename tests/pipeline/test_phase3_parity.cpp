/*!
 * \file test_phase3_parity.cpp
 * \brief Phase 3 parity harness for the serial baseline fixtures.
 */

#include "../common/minitest.h"
#include "../common/frame_compare.h"
#include "../common/hash_helpers.h"
#include "../common/repo_paths.h"
#include "../common/test_artifacts.h"

#include "mlv_pipeline_fixture.h"

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

void openTinyDualIsoWithReceipt(MlvPipelineFixture * fixture, const QString & receipt)
{
    QString error;
    ASSERT_TRUE(fixture->openTinyDualIso(&error));
    ASSERT_TRUE(fixture->loadReceipt(receipt, &error));
    ASSERT_TRUE(fixture->applyReceipt(&error));
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
    ASSERT_TRUE(!manifest.value(QStringLiteral("clips")).toArray().isEmpty());

    const QJsonArray clips = manifest.value(QStringLiteral("clips")).toArray();
    for( const QJsonValue &clipValue : clips )
    {
        ASSERT_TRUE(clipValue.isObject());
        const QJsonObject clip = clipValue.toObject();
        ASSERT_TRUE(clip.value(QStringLiteral("fixture")).toString() == QStringLiteral("tiny_dual_iso"));
        ASSERT_TRUE(clip.value(QStringLiteral("receipt")).toString().endsWith(QStringLiteral(".marxml")));
        ASSERT_TRUE(clip.value(QStringLiteral("frames")).isArray());
        ASSERT_TRUE(!clip.value(QStringLiteral("frames")).toArray().isEmpty());
    }
}

TEST(Phase3_PAR, SerialPathByteIdentity)
{
    QJsonObject manifest;
    if (!loadManifest(&manifest))
    {
        SKIP_TEST("Phase 3 PAR byte-identity fixtures absent; harness skeleton recorded the gap.");
    }

    const QJsonArray clips = manifest.value(QStringLiteral("clips")).toArray();
    for( const QJsonValue &clipValue : clips )
    {
        const QJsonObject clip = clipValue.toObject();
        MlvPipelineFixture fixture;
        openTinyDualIsoWithReceipt(&fixture, clip.value(QStringLiteral("receipt")).toString());

        const int threads = clip.value(QStringLiteral("threads")).toInt(1);
        const QJsonArray frames = clip.value(QStringLiteral("frames")).toArray();
        for( const QJsonValue &frameValue : frames )
        {
            const QJsonObject frame = frameValue.toObject();
            ASSERT_TRUE(frame.value(QStringLiteral("format")).toString() == QStringLiteral("rgb16"));
            const int frameIndex = frame.value(QStringLiteral("frame")).toInt(-1);
            ASSERT_TRUE(frameIndex >= 0);
            const std::vector<uint16_t> rendered =
                fixture.renderFrame16(static_cast<uint64_t>(frameIndex), threads);
            const std::string actual =
                sha256_bytes(rendered.data(), rendered.size() * sizeof(uint16_t));
            const std::string expected =
                frame.value(QStringLiteral("sha256")).toString().toStdString();
            ASSERT_EQ(expected, actual);
        }
    }
}

TEST(Phase3_PAR, PSNRWithinTolerance)
{
    QJsonObject manifest;
    if (!loadManifest(&manifest))
    {
        SKIP_TEST("Phase 3 PAR PSNR fixtures absent; harness skeleton recorded the gap.");
    }

    ASSERT_TRUE(manifest.value(QStringLiteral("psnr_checks")).isArray());
    const QJsonArray checks = manifest.value(QStringLiteral("psnr_checks")).toArray();
    ASSERT_TRUE(!checks.isEmpty());
    for( const QJsonValue &checkValue : checks )
    {
        const QJsonObject check = checkValue.toObject();
        MlvPipelineFixture fullFixture;
        openTinyDualIsoWithReceipt(
            &fullFixture,
            check.value(QStringLiteral("full_receipt")).toString());
        const int frameIndex = check.value(QStringLiteral("frame")).toInt(-1);
        ASSERT_TRUE(frameIndex >= 0);
        const std::vector<uint16_t> full =
            fullFixture.renderFrame16(static_cast<uint64_t>(frameIndex), 1);

        MlvPipelineFixture previewFixture;
        openTinyDualIsoWithReceipt(
            &previewFixture,
            check.value(QStringLiteral("full_receipt")).toString());
        previewFixture.receipt().setDualIso(
            check.value(QStringLiteral("preview_dual_iso_mode")).toInt(2));
        previewFixture.receipt().setDualIsoInterpolation(
            check.value(QStringLiteral("preview_interpolation")).toInt(1));
        previewFixture.receipt().setDualIsoAliasMap(
            check.value(QStringLiteral("preview_alias_map")).toInt(0));
        previewFixture.receipt().setDualIsoFrBlending(
            check.value(QStringLiteral("preview_fr_blending")).toInt(0));
        QString error;
        ASSERT_TRUE(previewFixture.applyReceipt(&error));
        const std::vector<uint16_t> preview =
            previewFixture.renderFrame16(static_cast<uint64_t>(frameIndex), 1);

        const frame_compare_result_t compare =
            compare_frames_u16(full.data(),
                               preview.data(),
                               previewFixture.width(),
                               previewFixture.height(),
                               3,
                               2);
        const double minimum = check.value(QStringLiteral("min_psnr_db")).toDouble();
        ASSERT_TRUE(compare.psnr_db >= minimum);
    }
}
