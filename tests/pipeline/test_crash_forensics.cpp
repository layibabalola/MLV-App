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
#include <QList>
#include <QMap>
#include <QSettings>
#include <QStandardPaths>
#include <QString>
#include <QStringList>
#include <QTemporaryDir>
#include <QVariant>

#include <string>

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

class PerformanceSettingsSnapshot {
public:
    PerformanceSettingsSnapshot()
    {
        QSettings set(QSettings::UserScope,
                      QStringLiteral("magiclantern.MLVApp"),
                      QStringLiteral("MLVApp"));
        set.beginGroup(QStringLiteral("PerformanceProfiling"));
        const QStringList keys = set.allKeys();
        for (const QString & key : keys) {
            m_values.insert(key, set.value(key));
        }
        set.endGroup();
    }

    ~PerformanceSettingsSnapshot()
    {
        QSettings set(QSettings::UserScope,
                      QStringLiteral("magiclantern.MLVApp"),
                      QStringLiteral("MLVApp"));
        set.beginGroup(QStringLiteral("PerformanceProfiling"));
        set.remove(QString());
        for (auto it = m_values.constBegin(); it != m_values.constEnd(); ++it) {
            set.setValue(it.key(), it.value());
        }
        set.endGroup();
        set.sync();
    }

private:
    QMap<QString, QVariant> m_values;
};

class EnvSnapshot {
public:
    explicit EnvSnapshot(const QList<QByteArray> &names)
    {
        for (const QByteArray & name : names) {
            Entry entry;
            entry.name = name;
            entry.wasSet = qEnvironmentVariableIsSet(name.constData());
            entry.value = qgetenv(name.constData());
            m_entries.append(entry);
        }
    }

    ~EnvSnapshot()
    {
        for (const Entry &entry : m_entries) {
            if (entry.wasSet) {
                qputenv(entry.name.constData(), entry.value);
            } else {
                qunsetenv(entry.name.constData());
            }
        }
    }

private:
    struct Entry {
        QByteArray name;
        bool wasSet = false;
        QByteArray value;
    };
    QList<Entry> m_entries;
};

} // namespace

TEST(CrashForensics, MessageHandlerCreatesLogFile)
{
    AppDataRedirect redirect;

    QTemporaryDir overrideDir;
    ASSERT_TRUE(overrideDir.isValid());
    overrideDir.setAutoRemove(false);
    qputenv("MLVAPP_CRASH_FORENSICS_LOG_DIR",
            overrideDir.path().toLocal8Bit());

    /* Simulate argv for install(). */
    QByteArray arg0 = QByteArrayLiteral("pipeline_tests");
    QByteArray arg1 = QByteArrayLiteral("--profile-playback");
    char * argv[] = { arg0.data(), arg1.data(), nullptr };
    const int argc = 2;

    const QString logPath = CrashForensics::install(argc, argv);
    if (logPath.isEmpty()) {
        SKIP_TEST("AppDataLocation unavailable in this environment");
    }
    ASSERT_TRUE(logPath.startsWith(overrideDir.path()));

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

TEST(CrashForensics, GuiProfilingPresetsManageOnlyOwnedEnvironment)
{
    PerformanceSettingsSnapshot settingsSnapshot;
    EnvSnapshot envSnapshot(QList<QByteArray>()
        << QByteArrayLiteral("MLVAPP_EXPERIMENTAL_GL_VIEWPORT")
        << QByteArrayLiteral("MLVAPP_EXPERIMENTAL_GL_VIEWPORT_GUI_MANAGED")
        << QByteArrayLiteral("MLVAPP_EXPERIMENTAL_GPU_PROCESSING")
        << QByteArrayLiteral("MLVAPP_EXPERIMENTAL_GPU_PROCESSING_GUI_MANAGED")
        << QByteArrayLiteral("MLVAPP_GPU_PLAYBACK_RECON")
        << QByteArrayLiteral("MLVAPP_GPU_PLAYBACK_RECON_GUI_MANAGED")
        << QByteArrayLiteral("MLVAPP_EXPERIMENTAL_GPU_PLAYBACK_RECON_TEXTURE_PRESENT")
        << QByteArrayLiteral("MLVAPP_EXPERIMENTAL_GPU_PLAYBACK_RECON_TEXTURE_PRESENT_GUI_MANAGED")
        << QByteArrayLiteral("MLVAPP_PLAYBACK_PHASE3_UNATTENDED")
        << QByteArrayLiteral("MLVAPP_PLAYBACK_PHASE3_UNATTENDED_GUI_MANAGED")
        << QByteArrayLiteral("MLVAPP_PLAYBACK_QUALITY_MODE")
        << QByteArrayLiteral("MLVAPP_PLAYBACK_QUALITY_MODE_GUI_MANAGED")
        << QByteArrayLiteral("MLVAPP_PLAYBACK_SCALE_FACTOR")
        << QByteArrayLiteral("MLVAPP_PLAYBACK_SCALE_FACTOR_GUI_MANAGED")
        << QByteArrayLiteral("MLVAPP_CDNG_EXPORT_ASYNC_WRITER")
        << QByteArrayLiteral("MLVAPP_CDNG_EXPORT_ASYNC_WRITER_GUI_MANAGED")
        << QByteArrayLiteral("MLVAPP_CDNG_EXPORT_ASYNC_WRITER_COMPRESS")
        << QByteArrayLiteral("MLVAPP_CDNG_EXPORT_ASYNC_WRITER_COMPRESS_GUI_MANAGED")
        << QByteArrayLiteral("MLVAPP_CDNG_EXPORT_ASYNC_WRITER_QUEUE_DEPTH")
        << QByteArrayLiteral("MLVAPP_CDNG_EXPORT_ASYNC_WRITER_QUEUE_DEPTH_GUI_MANAGED")
        << QByteArrayLiteral("MLVAPP_CDNG_EXPORT_ASYNC_WRITER_THREADS")
        << QByteArrayLiteral("MLVAPP_CDNG_EXPORT_ASYNC_WRITER_THREADS_GUI_MANAGED")
        << QByteArrayLiteral("MLVAPP_EXPORT_STAGE_PROFILER")
        << QByteArrayLiteral("MLVAPP_EXPORT_STAGE_PROFILER_GUI_MANAGED")
        << QByteArrayLiteral("MLVAPP_EXPORT_STAGE_PROFILE_FILE")
        << QByteArrayLiteral("MLVAPP_EXPORT_STAGE_PROFILE_FILE_GUI_MANAGED")
        << QByteArrayLiteral("MLVAPP_PERF_FIELD_LOG"));

    QSettings set(QSettings::UserScope,
                  QStringLiteral("magiclantern.MLVApp"),
                  QStringLiteral("MLVApp"));
    set.beginGroup(QStringLiteral("PerformanceProfiling"));
    set.remove(QString());
    set.endGroup();
    set.sync();

    CrashForensics::setCudaPlaybackProfilingSettingsEnabled(true);
    ASSERT_TRUE(CrashForensics::cudaPlaybackProfilingSettingsEnabled());
    CrashForensics::applyCudaPlaybackProfilingEnvironment(true);
    ASSERT_EQ(std::string("1"), qgetenv("MLVAPP_EXPERIMENTAL_GL_VIEWPORT").toStdString());
    ASSERT_EQ(std::string("1"), qgetenv("MLVAPP_EXPERIMENTAL_GPU_PROCESSING").toStdString());
    ASSERT_EQ(std::string("1"), qgetenv("MLVAPP_GPU_PLAYBACK_RECON").toStdString());
    ASSERT_EQ(std::string("1"), qgetenv("MLVAPP_EXPERIMENTAL_GPU_PLAYBACK_RECON_TEXTURE_PRESENT").toStdString());
    ASSERT_EQ(std::string("1"), qgetenv("MLVAPP_PLAYBACK_PHASE3_UNATTENDED").toStdString());
    ASSERT_EQ(std::string("phase3_hq"), qgetenv("MLVAPP_PLAYBACK_QUALITY_MODE").toStdString());
    ASSERT_EQ(std::string("1"), qgetenv("MLVAPP_PLAYBACK_SCALE_FACTOR").toStdString());
    ASSERT_EQ(std::string("1"), qgetenv("MLVAPP_GPU_PLAYBACK_RECON_GUI_MANAGED").toStdString());

    CrashForensics::applyCudaPlaybackProfilingEnvironment(false);
    ASSERT_FALSE(qEnvironmentVariableIsSet("MLVAPP_GPU_PLAYBACK_RECON"));
    ASSERT_FALSE(qEnvironmentVariableIsSet("MLVAPP_PLAYBACK_QUALITY_MODE"));
    ASSERT_FALSE(qEnvironmentVariableIsSet("MLVAPP_PLAYBACK_SCALE_FACTOR"));

    qputenv("MLVAPP_GPU_PLAYBACK_RECON", QByteArrayLiteral("1"));
    qunsetenv("MLVAPP_GPU_PLAYBACK_RECON_GUI_MANAGED");
    CrashForensics::applyCudaPlaybackProfilingEnvironment(false);
    ASSERT_EQ(std::string("1"), qgetenv("MLVAPP_GPU_PLAYBACK_RECON").toStdString());
    qunsetenv("MLVAPP_GPU_PLAYBACK_RECON");

    CrashForensics::setDngAsyncCompressionProfilingSettings(true, 99, -4);
    ASSERT_TRUE(CrashForensics::dngAsyncCompressionProfilingSettingsEnabled());
    ASSERT_EQ(8, CrashForensics::dngAsyncCompressionQueueDepthSettingsValue());
    ASSERT_EQ(1, CrashForensics::dngAsyncCompressionThreadCountSettingsValue());
    CrashForensics::applyDngAsyncCompressionProfilingEnvironment(true, 8, 1);
    ASSERT_EQ(std::string("1"), qgetenv("MLVAPP_CDNG_EXPORT_ASYNC_WRITER").toStdString());
    ASSERT_EQ(std::string("1"), qgetenv("MLVAPP_CDNG_EXPORT_ASYNC_WRITER_COMPRESS").toStdString());
    ASSERT_EQ(std::string("8"), qgetenv("MLVAPP_CDNG_EXPORT_ASYNC_WRITER_QUEUE_DEPTH").toStdString());
    ASSERT_EQ(std::string("1"), qgetenv("MLVAPP_CDNG_EXPORT_ASYNC_WRITER_THREADS").toStdString());
    ASSERT_EQ(std::string("1"), qgetenv("MLVAPP_EXPORT_STAGE_PROFILER").toStdString());

    CrashForensics::setDngAsyncCompressionProfilingSettings(false, 2, 2);
    CrashForensics::applyDngAsyncCompressionProfilingEnvironment(false, 2, 2);
    ASSERT_FALSE(qEnvironmentVariableIsSet("MLVAPP_CDNG_EXPORT_ASYNC_WRITER"));
    ASSERT_FALSE(qEnvironmentVariableIsSet("MLVAPP_CDNG_EXPORT_ASYNC_WRITER_COMPRESS"));
    ASSERT_FALSE(qEnvironmentVariableIsSet("MLVAPP_CDNG_EXPORT_ASYNC_WRITER_QUEUE_DEPTH"));
    ASSERT_FALSE(qEnvironmentVariableIsSet("MLVAPP_CDNG_EXPORT_ASYNC_WRITER_THREADS"));
    ASSERT_EQ(std::string("0"), qgetenv("MLVAPP_EXPORT_STAGE_PROFILER").toStdString());
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
