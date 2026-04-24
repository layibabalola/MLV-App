/*!
 * \file test_crash_forensics.cpp
 * \brief Smoke test for the crash-forensics / logging bundle.
 *
 * Verifies the qInstallMessageHandler file sink creates the expected
 * rotating log under AppDataLocation and that the run-metadata JSON
 * helper exposes the expected keys.  The Windows minidump path is
 * deliberately skipped: triggering it with RaiseException would tear
 * down the test runner.
 */

#include "../common/minitest.h"

#include "../../platform/qt/CrashForensics.h"

#include <QByteArray>
#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QStandardPaths>
#include <QString>
#include <QTemporaryDir>

namespace {

/* Redirect AppDataLocation to a fresh temp directory so the test does
 * not pollute the real user profile.  Qt 6 honours setTestModeEnabled
 * which switches QStandardPaths to a per-user writable test location
 * on Windows/macOS/Linux. */
class AppDataRedirect {
public:
    AppDataRedirect()
    {
        QStandardPaths::setTestModeEnabled(true);
    }
    ~AppDataRedirect()
    {
        QStandardPaths::setTestModeEnabled(false);
    }
};

} // namespace

TEST(CrashForensics, MessageHandlerCreatesLogFile)
{
    AppDataRedirect redirect;

    /* Simulate argv for install(). */
    QByteArray arg0 = QByteArrayLiteral("pipeline_tests");
    QByteArray arg1 = QByteArrayLiteral("--profile-playback");
    char * argv[] = { arg0.data(), arg1.data(), nullptr };
    const int argc = 2;

    const QString logPath = CrashForensics::install(argc, argv);
    if (logPath.isEmpty()) {
        SKIP_TEST("AppDataLocation unavailable in this environment");
    }

    /* install() is idempotent; verify it returns the same path on a
     * second call. */
    const QString logPath2 = CrashForensics::install(argc, argv);
    ASSERT_EQ(logPath.toStdString(), logPath2.toStdString());

    const QString marker = QStringLiteral(
        "CrashForensics-smoke-marker-XYZ123-uniq");
    qInfo("%s", marker.toUtf8().constData());

    /* The handler flushes after every write, so the marker should be
     * visible immediately. */
    QFile file(logPath);
    ASSERT_TRUE(file.exists());
    ASSERT_TRUE(file.open(QIODevice::ReadOnly | QIODevice::Text));
    const QByteArray contents = file.readAll();
    file.close();
    const QString asText = QString::fromUtf8(contents);
    ASSERT_TRUE(asText.contains(marker));
}

TEST(CrashForensics, RunMetadataContainsExpectedFields)
{
    AppDataRedirect redirect;

    QByteArray arg0 = QByteArrayLiteral("pipeline_tests");
    QByteArray arg1 = QByteArrayLiteral("--check-metadata");
    char * argv[] = { arg0.data(), arg1.data(), nullptr };
    const int argc = 2;
    CrashForensics::install(argc, argv);

    const QString json = CrashForensics::runMetadataJson();
    ASSERT_FALSE(json.isEmpty());

    QJsonParseError parseError;
    const QJsonDocument doc = QJsonDocument::fromJson(json.toUtf8(), &parseError);
    ASSERT_EQ(int(QJsonParseError::NoError), int(parseError.error));
    ASSERT_TRUE(doc.isObject());

    const QJsonObject obj = doc.object();
    ASSERT_TRUE(obj.contains(QStringLiteral("build_sha")));
    ASSERT_TRUE(obj.contains(QStringLiteral("app_version")));
    ASSERT_TRUE(obj.contains(QStringLiteral("qt_version")));
    ASSERT_TRUE(obj.contains(QStringLiteral("os")));
    ASSERT_TRUE(obj.contains(QStringLiteral("cpu_features")));
    ASSERT_TRUE(obj.contains(QStringLiteral("command_line")));

    /* os is a nested object with "pretty" set. */
    ASSERT_TRUE(obj.value(QStringLiteral("os")).isObject());
    const QJsonObject osObj = obj.value(QStringLiteral("os")).toObject();
    ASSERT_TRUE(osObj.contains(QStringLiteral("pretty")));
    ASSERT_TRUE(osObj.contains(QStringLiteral("kernel_version")));

    /* cpu_features is an array (may be empty if the compiler doesn't
     * support __builtin_cpu_supports). */
    ASSERT_TRUE(obj.value(QStringLiteral("cpu_features")).isArray());

    /* command_line should contain at least arg0. */
    ASSERT_TRUE(obj.value(QStringLiteral("command_line")).isArray());
    const QJsonArray cmd = obj.value(QStringLiteral("command_line")).toArray();
    ASSERT_TRUE(cmd.size() >= 1);
    ASSERT_EQ(std::string("pipeline_tests"), cmd.at(0).toString().toStdString());

    /* qt_version should look like MAJOR.MINOR.PATCH. */
    const QString qtVersion = obj.value(QStringLiteral("qt_version")).toString();
    ASSERT_FALSE(qtVersion.isEmpty());
    ASSERT_TRUE(qtVersion.contains(QLatin1Char('.')));
}

TEST(CrashForensics, MinidumpHandlerIsInstalledOnWindows)
{
#ifdef Q_OS_WIN
    /* We intentionally do NOT raise an exception here - that would
     * terminate the test runner.  The install() call registered the
     * filter; covering it end-to-end requires an out-of-process
     * harness. */
    SKIP_TEST("Windows minidump path is covered out-of-process; skipping to avoid tearing down the test runner");
#else
    SKIP_TEST("Minidump handler is Windows-only");
#endif
}
