/*!
 * \file CrashForensics.cpp
 * \brief Implementation of the always-on rotating log sink, the Windows
 *        minidump handler, and the run-metadata helper.
 *
 * See CrashForensics.h for the contract.
 */

#include "CrashForensics.h"

#include "Phase3Breadcrumbs.h"

#include <QByteArray>
#include <QCoreApplication>
#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QList>
#include <QMutex>
#include <QMutexLocker>
#include <QRegularExpression>
#include <QStandardPaths>
#include <QString>
#include <QSysInfo>
#include <QThread>
#include <QtGlobal>

#include <atomic>
#include <cstdio>
#include <cstdlib>

#ifdef Q_OS_WIN
#  ifndef WIN32_LEAN_AND_MEAN
#    define WIN32_LEAN_AND_MEAN
#  endif
#  include <windows.h>
#  include <dbghelp.h>
#endif

/* Build-time git SHA, passed in via `-DMLVAPP_GIT_SHA=...` in the .pro
 * file.  Fall back to "unknown" when the build system did not provide
 * one (e.g. standalone test builds). */
#ifndef MLVAPP_GIT_SHA
#  define MLVAPP_GIT_SHA "unknown"
#endif

namespace {

static QMutex g_logMutex;
static QString g_logFilePath;
static QString g_logsDir;
static QStringList g_commandLine;
static std::atomic<bool> g_installed{false};
static QtMessageHandler g_previousHandler = nullptr;

QString cpuFeatureList()
{
    QStringList features;
#if defined(__GNUC__)
    __builtin_cpu_init();
    if (__builtin_cpu_supports("sse4.2")) features << QStringLiteral("sse4.2");
    if (__builtin_cpu_supports("avx"))    features << QStringLiteral("avx");
    if (__builtin_cpu_supports("avx2"))   features << QStringLiteral("avx2");
    if (__builtin_cpu_supports("avx512f")) features << QStringLiteral("avx512f");
#endif
    return features.join(QLatin1Char(','));
}

QString levelTag(QtMsgType type)
{
    switch (type) {
        case QtDebugMsg:    return QStringLiteral("DEBUG");
        case QtInfoMsg:     return QStringLiteral("INFO");
        case QtWarningMsg:  return QStringLiteral("WARNING");
        case QtCriticalMsg: return QStringLiteral("CRITICAL");
        case QtFatalMsg:    return QStringLiteral("FATAL");
    }
    return QStringLiteral("UNKNOWN");
}

QString todaysLogFileName()
{
    return QStringLiteral("mlvapp-")
        + QDateTime::currentDateTime().toString(QStringLiteral("yyyyMMdd"))
        + QStringLiteral(".log");
}

void pruneOldLogs(const QString & logsDir, int keep)
{
    QDir dir(logsDir);
    if (!dir.exists()) return;
    const QRegularExpression pattern(
        QStringLiteral("^mlvapp-\\d{8}\\.log$"));
    QFileInfoList entries = dir.entryInfoList(
        QStringList() << QStringLiteral("mlvapp-*.log"),
        QDir::Files,
        QDir::Name);
    QFileInfoList matching;
    for (const QFileInfo & info : entries) {
        if (pattern.match(info.fileName()).hasMatch()) {
            matching.append(info);
        }
    }
    if (matching.size() <= keep) return;
    // Sort ascending by filename; older files come first. Delete all but
    // the last `keep`.
    std::sort(matching.begin(), matching.end(),
              [](const QFileInfo & a, const QFileInfo & b) {
                  return a.fileName() < b.fileName();
              });
    const int deleteCount = matching.size() - keep;
    for (int i = 0; i < deleteCount; ++i) {
        QFile::remove(matching.at(i).absoluteFilePath());
    }
}

void writeLogLine(QtMsgType type, const QMessageLogContext & ctx, const QString & message)
{
    if (g_logFilePath.isEmpty()) return;

    const QString timestamp =
        QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs);
    const QString thread = QStringLiteral("0x%1")
        .arg(reinterpret_cast<quintptr>(QThread::currentThreadId()), 0, 16);
    QString location;
    if (ctx.file && *ctx.file) {
        location = QStringLiteral(" (%1:%2)")
            .arg(QString::fromLocal8Bit(ctx.file))
            .arg(ctx.line);
    }

    // Sanitise message (strip stray CR/LF within a line so each record
    // stays on a single line).
    QString sanitized = message;
    sanitized.replace(QLatin1Char('\r'), QLatin1Char(' '));
    sanitized.replace(QLatin1Char('\n'), QStringLiteral(" | "));

    const QString line = QStringLiteral("[%1] [%2] [%3] %4%5\n")
        .arg(timestamp)
        .arg(levelTag(type))
        .arg(thread)
        .arg(sanitized)
        .arg(location);

    QMutexLocker locker(&g_logMutex);
    QFile file(g_logFilePath);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Append | QIODevice::Text)) {
        return;
    }
    const QByteArray bytes = line.toUtf8();
    file.write(bytes);
    file.flush();
    file.close();
}

void messageHandler(QtMsgType type,
                    const QMessageLogContext & context,
                    const QString & message)
{
    writeLogLine(type, context, message);

    // Chain to the previous handler (if any) so stderr/stdout output is
    // preserved for developers running the app from a terminal.
    if (g_previousHandler) {
        g_previousHandler(type, context, message);
    }

    if (type == QtFatalMsg) {
        // Mirror Qt's default behaviour: abort after writing.
        std::abort();
    }
}

#ifdef Q_OS_WIN
/* Emergency crash-time log writer.  We CANNOT use qInfo/QFile here
 * because the heap may be corrupt; use plain Win32 calls instead. */
void emergencyAppendLine(const wchar_t * logPath, const char * line)
{
    HANDLE h = CreateFileW(logPath,
                           FILE_APPEND_DATA,
                           FILE_SHARE_READ,
                           nullptr,
                           OPEN_ALWAYS,
                           FILE_ATTRIBUTE_NORMAL,
                           nullptr);
    if (h == INVALID_HANDLE_VALUE) return;
    DWORD written = 0;
    WriteFile(h, line, static_cast<DWORD>(strlen(line)), &written, nullptr);
    FlushFileBuffers(h);
    CloseHandle(h);
}

LONG WINAPI crashExceptionFilter(EXCEPTION_POINTERS * exceptionInfo)
{
    // Build dump path: <logsDir>/mlvapp-YYYYMMDD-HHMMSS.dmp
    const QString dumpName = QStringLiteral("mlvapp-")
        + QDateTime::currentDateTime().toString(QStringLiteral("yyyyMMdd-HHmmss"))
        + QStringLiteral(".dmp");
    const QString dumpPath = QDir(g_logsDir).absoluteFilePath(dumpName);
    const std::wstring dumpPathW =
        QDir::toNativeSeparators(dumpPath).toStdWString();
    const std::wstring logPathW =
        QDir::toNativeSeparators(g_logFilePath).toStdWString();

    HANDLE file = CreateFileW(dumpPathW.c_str(),
                              GENERIC_WRITE,
                              0,
                              nullptr,
                              CREATE_ALWAYS,
                              FILE_ATTRIBUTE_NORMAL,
                              nullptr);
    bool wroteDump = false;
    DWORD exceptionCode = 0;
    if (exceptionInfo && exceptionInfo->ExceptionRecord) {
        exceptionCode = exceptionInfo->ExceptionRecord->ExceptionCode;
    }
    if (file != INVALID_HANDLE_VALUE) {
        MINIDUMP_EXCEPTION_INFORMATION mei;
        mei.ThreadId = GetCurrentThreadId();
        mei.ExceptionPointers = exceptionInfo;
        mei.ClientPointers = FALSE;
        const MINIDUMP_TYPE dumpType = static_cast<MINIDUMP_TYPE>(
            MiniDumpWithDataSegs | MiniDumpWithHandleData | MiniDumpWithThreadInfo);
        wroteDump = MiniDumpWriteDump(GetCurrentProcess(),
                                      GetCurrentProcessId(),
                                      file,
                                      dumpType,
                                      exceptionInfo ? &mei : nullptr,
                                      nullptr,
                                      nullptr) != FALSE;
        CloseHandle(file);
    }

    // Append a "CRASH:" line to the rotating log directly, because the
    // normal message handler may not be safe at this point.
    char emergencyLine[1024];
    const QByteArray dumpPathUtf8 =
        QDir::toNativeSeparators(dumpPath).toUtf8();
    const QByteArray nowUtf8 =
        QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs).toUtf8();
    std::snprintf(emergencyLine, sizeof(emergencyLine),
                  "[%s] [FATAL] [crash] CRASH: minidump at %s, exception code 0x%08lX, dump_written=%d\n",
                  nowUtf8.constData(),
                  dumpPathUtf8.constData(),
                  static_cast<unsigned long>(exceptionCode),
                  wroteDump ? 1 : 0);
    emergencyAppendLine(logPathW.c_str(), emergencyLine);
    Phase3Breadcrumbs::dumpToWindowsLogFile(logPathW.c_str());

    return EXCEPTION_EXECUTE_HANDLER;
}
#endif // Q_OS_WIN

} // namespace

namespace CrashForensics {

QString install(int argc, char * argv[])
{
    bool expected = false;
    if (!g_installed.compare_exchange_strong(expected, true)) {
        return g_logFilePath;
    }

    g_commandLine.clear();
    for (int i = 0; i < argc; ++i) {
        g_commandLine.append(QString::fromLocal8Bit(argv[i]));
    }

    const QString base =
        QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    if (base.isEmpty()) {
        g_installed.store(false);
        return QString();
    }
    g_logsDir = QDir(base).absoluteFilePath(QStringLiteral("logs"));
    QDir().mkpath(g_logsDir);
    pruneOldLogs(g_logsDir, 5);

    g_logFilePath = QDir(g_logsDir).absoluteFilePath(todaysLogFileName());

    g_previousHandler = qInstallMessageHandler(&messageHandler);

#ifdef Q_OS_WIN
    SetUnhandledExceptionFilter(&crashExceptionFilter);
#endif

    return g_logFilePath;
}

QString currentLogFilePath()
{
    return g_logFilePath;
}

QString logsDirectoryPath()
{
    return g_logsDir;
}

QString runMetadataJson()
{
    QJsonObject root;
    root.insert(QStringLiteral("build_sha"),
                QString::fromLatin1(MLVAPP_GIT_SHA));
    QString appVersion = QCoreApplication::applicationVersion();
    if (appVersion.isEmpty()) {
#ifdef VERSION_MAJOR
#  define MLVAPP_STRINGIFY_INNER(X) #X
#  define MLVAPP_STRINGIFY(X) MLVAPP_STRINGIFY_INNER(X)
        appVersion = QStringLiteral("%1.%2.%3.%4")
            .arg(QString::fromLatin1(MLVAPP_STRINGIFY(VERSION_MAJOR)))
            .arg(QString::fromLatin1(MLVAPP_STRINGIFY(VERSION_MINOR)))
            .arg(QString::fromLatin1(MLVAPP_STRINGIFY(VERSION_PATCH)))
            .arg(QString::fromLatin1(MLVAPP_STRINGIFY(VERSION_BUILD)));
#  undef MLVAPP_STRINGIFY
#  undef MLVAPP_STRINGIFY_INNER
#else
        appVersion = QStringLiteral("unknown");
#endif
    }
    root.insert(QStringLiteral("app_version"), appVersion);
    root.insert(QStringLiteral("qt_version"),
                QString::fromLatin1(qVersion()));

    QJsonObject os;
    os.insert(QStringLiteral("pretty"), QSysInfo::prettyProductName());
    os.insert(QStringLiteral("kernel_version"), QSysInfo::kernelVersion());
    os.insert(QStringLiteral("kernel_type"), QSysInfo::kernelType());
    os.insert(QStringLiteral("cpu_architecture"), QSysInfo::currentCpuArchitecture());
    root.insert(QStringLiteral("os"), os);

    QJsonArray features;
    const QString featureStr = cpuFeatureList();
    if (!featureStr.isEmpty()) {
        const QStringList split = featureStr.split(QLatin1Char(','), Qt::SkipEmptyParts);
        for (const QString & f : split) features.append(f);
    }
    root.insert(QStringLiteral("cpu_features"), features);

    QJsonArray cmdline;
    for (const QString & arg : g_commandLine) cmdline.append(arg);
    root.insert(QStringLiteral("command_line"), cmdline);

    root.insert(QStringLiteral("log_file"), g_logFilePath);
    root.insert(QStringLiteral("logs_directory"), g_logsDir);

    return QString::fromUtf8(QJsonDocument(root).toJson(QJsonDocument::Compact));
}

void logStartupMetadata()
{
    const QString json = runMetadataJson();
    qInfo().noquote() << QStringLiteral("run_metadata=") + json;
}

} // namespace CrashForensics
